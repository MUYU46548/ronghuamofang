# -*- coding: utf-8 -*-
"""真人作品拆解：导入 → 拆章 → 结构画像（P2 内核，零 token）。

## 它做什么、不做什么

**做**：把一部你有权使用的作品切成章，统计**结构特征**——章长分布、对话占比、
段长/句长、章末钩子比例——产出 `materials/style_profile.json`，供 stage4 当**数值约束**。

**不做**：不复制、不摘要、不保存任何原文。输出里只有统计数字与文件名哈希。
为什么这么硬：拆解别人的作品去模仿文风，一旦把原文存进项目，它就会跟着快照、归档、
甚至打包进安装包 —— 那不是"参考"，那是复制。片段引用是**另一个显式动作**（few-shot 勾选），
默认关闭、逐条可见可删。

## 为什么不用 LLM

同 P1：结构特征是确定性可测的东西（字数、引号占比、句长），让模型来"读一遍说像不像"
既贵又不可回归。这里一个正则加几个计数器就够，且结果可复现。

## 与已有模块的分工

- `style_analyzer`：**单篇**文本的风格特征（给范文注入用）。本模块复用它的分句与特征，
  只在"多章 + 全书分布 + 钩子率"这层做新增。
- `book_split`：把书拆成文本块（已有，服务节奏面板）。本模块不重复造拆章器？
  —— 见 `_split_chapters`：那边按"章"切分但目标是给章节节奏用，容错取向不同
  （它宁可少切，这里宁可错切也要给出分布）。两者判据不同，故并存放并写明理由。
"""
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from utils.file_io import read_text, write_text                        # noqa: E402
from utils.style_analyzer import (                                     # noqa: E402
    extract_style_features,
    prose_char_count,
)

# ---- 拆章 ----------------------------------------------------------------

_TITLE_PATTERNS = (
    # 第一章 / 第 12 回 / 第三节 / 第四卷（行首，且整行短）
    re.compile(r"^\s*第\s*[0-9０-９一二三四五六七八九十百千零〇]{1,6}\s*[章回节卷部]\s*[^\n]{0,30}$"),
    re.compile(r"^\s*Chapter\s+\d{1,4}\s*[^\n]{0,40}$", re.I),
    # 1、标题 / 12. 标题（行首数字 + 分隔符 + 短标题）
    re.compile(r"^\s*\d{1,4}\s*[、.．]\s*[^\n]{1,30}$"),
)

MIN_GAP_CHARS = 300      # 两个"标题"之间至少隔这么多字，否则当成正文里的提及
MAX_TITLE_LEN = 40


def _is_title(line):
    s = line.strip()
    if not s or len(s) > MAX_TITLE_LEN:
        return False
    return any(p.match(s) for p in _TITLE_PATTERNS)


def split_chapters(text):
    """按章标题切分。返回 (preface, [(title, body), ...])。

    防误拆：正文里"他翻到第三章"这种提及不独占一行、或与上一个标题间隔过近，
    都不算章标题 —— 错误的切分会把画像算歪，而画像要拿去约束写作。
    """
    lines = text.splitlines()
    marks = []                                  # (line_index, title, char_offset)
    offset = 0
    for i, ln in enumerate(lines):
        if _is_title(ln):
            marks.append((i, ln.strip(), offset))
        offset += len(ln) + 1

    kept = []
    last_off = -10 ** 9
    for i, title, off in marks:
        if off - last_off < MIN_GAP_CHARS:
            continue                            # 间隔太近 → 判定为正文提及
        kept.append((i, title, off))
        last_off = off

    if not kept:
        body = text.strip()
        return "", ([("全文", body)] if body else [])

    preface = "\n".join(lines[:kept[0][0]]).strip()
    chapters = []
    for idx, (line_i, title, _off) in enumerate(kept):
        end = kept[idx + 1][0] if idx + 1 < len(kept) else len(lines)
        body = "\n".join(lines[line_i + 1:end]).strip()
        chapters.append((title, body))
    return preface, chapters


# ---- 画像 ----------------------------------------------------------------

HOOK_MARKS = ("？", "?", "……", "突然", "却", "但是", "然而", "没想", "直到",
              "只见", "就在这时", "不料", "没想到")
OPEN_QUOTE_MARKS = ("“", "\"")


def _chapter_metrics(title, body):
    chars = prose_char_count(body)
    f = extract_style_features(body) or {}
    tail = body[-max(120, int(len(body) * 0.15)):]
    head = body[:220]
    hook_hits = [m for m in HOOK_MARKS if m in tail]
    return {
        "index": 0,                             # 由调用方填
        "title_chars": len(title),
        "chars": chars,
        "paragraphs": int(f.get("paragraph_count") or 0),
        "avg_para_len": float(f.get("avg_paragraph_len") or 0),
        "avg_sent_len": float(f.get("avg_sentence_len") or 0),
        "sent_cv": float(f.get("sentence_len_cv") or 0),
        "dialogue_ratio": float(f.get("dialogue_ratio") or 0),
        "hook": bool(hook_hits),
        "hook_marks": hook_hits[:3],
        "opens_with_dialogue": any(m in head for m in OPEN_QUOTE_MARKS),
        "sufficient": bool(f),                 # 太短的章 extract_style_features 会返回 {}
    }


def _dist(values, nd=1):
    """分布统计。`nd` 是小数位：章长用 1 位就够，**比率必须 3 位** ——
    首版对 dialogue_ratio 用 1 位，0.04 被四舍五入成 0.0，画像对外报"没有对话"。
    """
    vals = sorted(v for v in values if v is not None and v > 0)
    if not vals:
        return {}
    n = len(vals)
    mean = sum(vals) / n
    median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
    var = sum((x - mean) ** 2 for x in vals) / n
    std = var ** 0.5
    return {
        "mean": round(mean, nd),
        "median": round(median, nd),
        "min": round(vals[0], nd),
        "max": round(vals[-1], nd),
        "cv": round(std / mean, 3) if mean else 0,
    }


def build_profile(chapters, source_name="", source_text=""):
    """chapters: [(title, body)] → 画像 dict（**不含任何原文**）。"""
    rows = []
    for i, (title, body) in enumerate(chapters, 1):
        if not body.strip():
            continue
        m = _chapter_metrics(title, body)
        m["index"] = i
        rows.append(m)
    usable = [r for r in rows if r["sufficient"]]

    summary = {
        "chapter_count": len(rows),
        "usable_chapters": len(usable),
        "total_chars": sum(r["chars"] for r in rows),
        "chapter_len": _dist([r["chars"] for r in rows], nd=1),
        "dialogue_ratio": _dist([r["dialogue_ratio"] for r in usable], nd=3),
        "avg_sent_len": _dist([r["avg_sent_len"] for r in usable], nd=2),
        "sent_cv": _dist([r["sent_cv"] for r in usable], nd=3),
        "paragraphs": _dist([r["paragraphs"] for r in usable], nd=1),
        "hook_rate": round(sum(1 for r in usable if r["hook"]) / len(usable), 2)
        if usable else 0,
        "dialogue_open_rate": round(
            sum(1 for r in usable if r["opens_with_dialogue"]) / len(usable), 2)
        if usable else 0,
    }

    return {
        "version": 1,
        "source": {
            "name": source_name,
            "sha256_8": hashlib.sha256(source_text.encode("utf-8")).hexdigest()[:8]
            if source_text else "",
            "chars": prose_char_count(source_text) if source_text else 0,
            "imported_at": datetime.now().isoformat(timespec="seconds"),
        },
        "chapters": rows,
        "summary": summary,
        "guidance": _guidance(summary),
        "notice": "本文件只含统计结果，不含原文；片段引用需用户显式勾选。",
    }


def _guidance(s):
    """把统计翻成写作时能用的数值约束（stage4 直接读人话）。"""
    out = []
    cl = s.get("chapter_len") or {}
    if cl.get("median"):
        lo = int(cl["median"] * 0.8 // 100 * 100)
        hi = int(cl["median"] * 1.2 // 100 * 100)
        out.append("章长目标 %d-%d 字（参考中位 %d，最长 %d）"
                   % (lo, hi, int(cl["median"]), int(cl.get("max") or 0)))
    d = s.get("dialogue_ratio") or {}
    if d.get("mean"):
        out.append("对话占比约 %d%%，区间 %d%%-%d%%"
                   % (round(d["mean"] * 100), round(d.get("min", 0) * 100),
                      round(d.get("max", 0) * 100)))
    sl = s.get("avg_sent_len") or {}
    cv = s.get("sent_cv") or {}
    if sl.get("mean"):
        out.append("平均句长 %.1f 字，长短交替（CV %.2f）"
                   % (sl["mean"], cv.get("mean", 0)))
    if s.get("hook_rate"):
        out.append("章末留钩子的比例 %d%%（参考作品的实际做法）"
                   % round(s["hook_rate"] * 100))
    if s.get("paragraphs", {}).get("mean"):
        out.append("每章约 %d 段，平均段长 %d 字"
                   % (round(s["paragraphs"]["mean"]),
                      round((s.get("chapter_len") or {}).get("median", 0)
                            / max(s["paragraphs"]["mean"], 1))))
    return out


def decompose(path, out_path=None, root="."):
    """读文件 → 拆章 → 画像 → 落盘。返回 (ok, msg, profile)。"""
    p = Path(path)
    if not p.is_file():
        return False, "文件不存在: %s" % p, None
    try:
        text = read_text(p)
    except Exception as e:                                     # noqa: BLE001
        return False, "读取失败: %s: %s" % (type(e).__name__, e), None
    if prose_char_count(text) < 1000:
        return False, "有效正文不足 1000 字，拆解没有意义（也容易算出误导性画像）", None

    preface, chapters = split_chapters(text)
    if not chapters:
        return False, "没有识别出任何章节，也没有正文", None

    prof = build_profile(chapters, source_name=p.name, source_text=text)
    prof["source"]["preface_chars"] = prose_char_count(preface) if preface else 0

    target = Path(out_path) if out_path else Path(root) / "materials" / "style_profile.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, json.dumps(prof, ensure_ascii=False, indent=2))
    return True, "拆解 %d 章，画像已写入 %s" % (len(chapters), target), prof


def load_profile(root="."):
    p = Path(root) / "materials" / "style_profile.json"
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:                                          # noqa: BLE001
        return None


# ---- 片段勾选（few-shot）：候选只在内存，用户勾一条才落一条 ---------------

SNIPPETS_REL = "materials/style_snippets.json"
CONSENT_REL = "data/state/decompose_consent.json"
MAX_PICKS = 6
SNIPPET_MIN = 80
SNIPPET_MAX = 400

ACTION_WORDS = ("走", "站", "坐", "推", "拉", "拿", "放", "转", "抬", "低",
                "伸", "收", "握", "扔", "捡", "递", "跨", "退", "停")
SENSORY_WORDS = ("光", "暗", "亮", "影", "声", "响", "静", "冷", "热",
                 "味", "香", "腥", "湿", "硬", "软")


def classify_paragraph(p):
    """给段落分类：dialogue / action / scene / narrative。

    只用来给候选**贴标签**，不参与任何质量判断 —— 用户看标签只是为了
    快速找到"想要的那种段落"，判断权在他手上。
    """
    n = max(len(p), 1)
    quoted = sum(len(x) for x in re.findall(r"[“\"]([^”\"]{1,300})[”\"]", p))
    # 0.22 而不是 0.35：真实段落常是"动作 + 两句对话"的混合，
    # 引号占比 25% 上下很常见；阈值定太高会把对话段全判成 action（实测过）。
    if quoted / n > 0.22:
        return "dialogue"
    if sum(1 for w in ACTION_WORDS if w in p) >= 3:
        return "action"
    if sum(1 for w in SENSORY_WORDS if w in p) >= 3:
        return "scene"
    return "narrative"


def extract_snippets(text, per_kind=2):
    """从正文抽候选片段（**不落盘**，只在调用方内存/响应里）。

    每类最多 `per_kind` 条，长度 SNIPPET_MIN~SNIPPET_MAX。去重用"前 20 字"，
    避免同一段在候选里出现两次。
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    buckets, seen = {}, set()
    for i, p in enumerate(paras):
        n = len(p)
        if n < SNIPPET_MIN or n > SNIPPET_MAX:
            continue
        key = p[:20]
        if key in seen:
            continue
        kind = classify_paragraph(p)
        if len(buckets.get(kind, [])) >= per_kind:
            continue
        seen.add(key)
        buckets.setdefault(kind, []).append({
            "id": "%s-%d" % (kind, len(buckets.get(kind, [])) + 1),
            "kind": kind,
            "text": p,
            "chars": n,
            "para_index": i,
        })
    out = []
    for kind in ("dialogue", "action", "scene", "narrative"):
        out.extend(buckets.get(kind, []))
    return out


def consent_state(root="."):
    p = Path(root) / CONSENT_REL
    if not p.exists():
        return {"agreed": False, "at": None}
    try:
        d = json.loads(read_text(p))
        return {"agreed": bool(d.get("agreed")), "at": d.get("at")}
    except Exception:                                          # noqa: BLE001
        return {"agreed": False, "at": None}


def set_consent(root=".", agreed=False):
    p = Path(root) / CONSENT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps({
        "agreed": bool(agreed),
        "at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2))
    return consent_state(root)


def save_snippets(picks, root=".", source=None):
    """保存用户显式勾选的片段。返回 (ok, msg)。

    **先过声明门**：没同意就一条都不写。这不是形式主义 ——
    片段会随写作任务发给模型服务商，用户必须先知道这件事。
    """
    if not consent_state(root).get("agreed"):
        return False, "尚未同意拆解功能的版权与免责声明，不能保存片段"
    if not isinstance(picks, list) or not picks:
        return False, "没有勾选任何片段"
    if len(picks) > MAX_PICKS:
        return False, "最多勾选 %d 条（现在是 %d 条）" % (MAX_PICKS, len(picks))

    clean = []
    for p in picks:
        if not isinstance(p, dict):
            continue
        t = str(p.get("text") or "").strip()
        if not (SNIPPET_MIN <= len(t) <= SNIPPET_MAX):
            return False, "片段长度须在 %d-%d 字之间（有一条是 %d 字）" % (
                SNIPPET_MIN, SNIPPET_MAX, len(t))
        clean.append({
            "kind": str(p.get("kind") or "narrative"),
            "text": t,
            "chars": len(t),
        })
    if not clean:
        return False, "勾选内容不合法（需要 {kind, text}）"

    payload = {
        "version": 1,
        "picked_at": datetime.now().isoformat(timespec="seconds"),
        "source": source or {},
        "snippets": clean,
        "notice": "这些片段是用户显式勾选的引用，会随写作任务发给模型服务商。",
    }
    target = Path(root) / SNIPPETS_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    write_text(target, json.dumps(payload, ensure_ascii=False, indent=2))
    return True, "已保存 %d 条片段到 %s" % (len(clean), target)


def load_snippets(root="."):
    p = Path(root) / SNIPPETS_REL
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:                                          # noqa: BLE001
        return None


def clear_snippets(root="."):
    p = Path(root) / SNIPPETS_REL
    if p.exists():
        p.unlink()
        return True
    return False


def load_snippets_block(root="."):
    """给 stage4 的注入块；没有片段返回 ""（模板里独占一行，空就是空行）。"""
    data = load_snippets(root)
    if not data or not data.get("snippets"):
        return ""
    lines = ["## 参考片段（作者本人提供并勾选，只学语感与节奏）", "",
             "**不要复制这些句子，也不要改写它们**；只借鉴叙述的密度、句子长短与对话节奏。", ""]
    for i, s in enumerate(data["snippets"], 1):
        lines.append("### 片段 %d（%s）" % (i, s.get("kind") or "narrative"))
        lines.append(s.get("text") or "")
        lines.append("")
    return "\n".join(lines).strip()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="拆解真人作品的结构画像（零 token，不存原文）")
    ap.add_argument("file", help="要拆解的文件（.txt / .md）")
    ap.add_argument("--out", default="", help="画像输出路径（默认 materials/style_profile.json）")
    ap.add_argument("--json", action="store_true", help="把画像打到 stdout")
    args = ap.parse_args(argv)

    ok, msg, prof = decompose(args.file, args.out or None)
    if not ok:
        print("[style_profile] %s" % msg)
        return 1
    if args.json:
        print(json.dumps(prof, ensure_ascii=False, indent=2))
    else:
        print("[style_profile] %s" % msg)
        s = prof["summary"]
        for t in prof["guidance"]:
            print("  · %s" % t)
        print("  · 章数 %d（可用 %d），总字数 %d，钩子率 %d%%"
              % (s["chapter_count"], s["usable_chapters"],
                 s["total_chars"], round(s["hook_rate"] * 100)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
