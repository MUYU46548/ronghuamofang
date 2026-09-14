# -*- coding: utf-8 -*-
"""拆书 / 章节节奏分析（确定性，零 LLM 调用）。

用途：
1. **拆书**——把一本已有小说（自己写的旧稿、想参考的作品片段）按章节标题
   切分，快速看清它的结构。
2. **节奏可视化**——每章字数、段落数、平均段长、对话占比、句长波动，
   并在全书中标出字数离群章，供 GUI 画柱状/折线图。

产物：
  data/state/book_pacing.json         机器可读（GUI 直接用）
  data/state/book_split/<书名>/NN.md  切分后的章节正文（可选，--emit）

只有引用来源是文本文件时才会写盘；**输入文件只读**，本模块从不修改它。

用法：
  python scripts/book_split.py --input D:/稿子/旧稿.txt
  python scripts/book_split.py --input old.md --emit          # 同时导出切分章节
  python scripts/book_split.py --input old.md --json
  python scripts/book_split.py --input old.md --list-patterns # 看各标题正则命中数
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.file_io import read_text, write_text          # noqa: E402
from utils.verify_chapter import count_cn_words          # noqa: E402

OUT_JSON = "data/state/book_pacing.json"
OUT_DIR = "data/state/book_split"
MAX_INPUT_CHARS = 8_000_000      # 单次读取上限，防误选巨型文件把内存吃满

# 章节标题候选模式（按"中文回目 > Chapter > 编号 > Markdown 标题"的顺序尝试，
# 取命中数最多的那个；命中数 < 2 视为该模式不适用）
PATTERNS = [
    ("中文回目", re.compile(r"^[ \t]*第\s*[0-9一二三四五六七八九十百千零两]+\s*[章回节]\s*[^\n]{0,40}$",
                        re.MULTILINE)),
    ("Chapter", re.compile(r"^[ \t]*Chapter\s+\d+\b[^\n]{0,60}$", re.IGNORECASE | re.MULTILINE)),
    ("编号行", re.compile(r"^[ \t]*\d{1,3}\s*[.、]\s*[^\n]{1,40}$", re.MULTILINE)),
    ("Markdown 标题", re.compile(r"^#{1,3}[ \t]+[^\n#]{1,60}$", re.MULTILINE)),
]


def detect_pattern(text):
    """选出最合适的章节标题模式。返回 (name, regex, matches) 或 (None, None, [])。"""
    best = (None, None, [])
    for name, rx in PATTERNS:
        ms = list(rx.finditer(text))
        if len(ms) >= 2 and len(ms) > len(best[2]):
            best = (name, rx, ms)
    return best


def split_chapters(text):
    """按标题切章。返回 (pattern_name, [{title, body, start}])。"""
    name, rx, ms = detect_pattern(text)
    if not ms:
        return None, [{"title": "（未识别章节标题，按全文一章处理）",
                       "body": text, "start": 0}]
    chapters = []
    # 标题前的引子（版权页/序言等）单独处理：非空就当作"卷首"
    head = text[:ms[0].start()].strip()
    if len(head) > 50:
        chapters.append({"title": "（卷首）", "body": head, "start": 0})
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        chapters.append({
            "title": m.group(0).strip()[:60],
            "body": text[m.start():end],
            "start": m.start(),
        })
    return name, chapters


# ---------------------------------------------------------------- 指标

def chapter_metrics(body):
    """单章节奏指标（与 proofread.chapter_rhythm 同口径，供两处图表一致）。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body)
             if p.strip() and not p.strip().startswith("#")]
    quoted = re.findall(r"[“「]([^”」]*)[”」]", body)
    sentences = [s for s in re.split(r"(?<=[。！？…])", body) if s.strip()]
    para_lens = [count_cn_words(p) for p in paras] or [0]
    sent_lens = [count_cn_words(s) for s in sentences] or [0]
    words = count_cn_words(body)
    dialogue_chars = sum(count_cn_words(q) for q in quoted)
    mean_p = sum(para_lens) / len(para_lens)
    std_p = (sum((x - mean_p) ** 2 for x in para_lens) / len(para_lens)) ** 0.5
    mean_s = sum(sent_lens) / len(sent_lens)
    return {
        "word_count": words,
        "paragraphs": len(paras),
        "sentences": len(sentences),
        "avg_para_len": round(mean_p, 1),
        "para_len_std": round(std_p, 1),
        "avg_sentence_len": round(mean_s, 1),
        "sentence_len_std": round(
            (sum((x - mean_s) ** 2 for x in sent_lens) / len(sent_lens)) ** 0.5, 1),
        "dialogue_ratio": round(dialogue_chars / words, 4) if words else 0.0,
    }


def summarize(rows):
    """全书汇总：均值/极差/离群章。"""
    counts = [r["word_count"] for r in rows] or [0]
    mean = sum(counts) / len(counts)
    std = (sum((x - mean) ** 2 for x in counts) / len(counts)) ** 0.5
    ratios = [r["dialogue_ratio"] for r in rows] or [0]
    outliers = []
    for r in rows:
        if mean > 0 and abs(r["word_count"] - mean) / mean > 0.40:
            outliers.append({
                "index": r["index"], "title": r["title"], "word_count": r["word_count"],
                "delta_pct": round((r["word_count"] - mean) / mean * 100, 1),
            })
    # 折线趋势：把章序分成 3 段算平均字数，判断"越写越长 / 越写越短"
    trend = "平稳"
    if len(counts) >= 6:
        k = len(counts) // 3
        a, b, c = (sum(counts[:k]) / k, sum(counts[k:2 * k]) / k,
                   sum(counts[2 * k:]) / max(1, len(counts) - 2 * k))
        if b > a * 1.15 and c > b * 1.05:
            trend = "递增"
        elif b < a * 0.85 and c < b * 0.95:
            trend = "递减"
        elif max(a, b, c) / max(1e-9, min(a, b, c)) > 1.4:
            trend = "波动"
    return {
        "chapters": len(rows),
        "total_words": sum(counts),
        "word_count": {"mean": round(mean, 1), "std": round(std, 1),
                       "min": min(counts), "max": max(counts),
                       "cv": round(std / mean, 3) if mean else 0.0},
        "dialogue_ratio": {"mean": round(sum(ratios) / len(ratios), 4),
                           "min": round(min(ratios), 4), "max": round(max(ratios), 4)},
        "outliers": outliers,
        "trend": trend,
    }


# ---------------------------------------------------------------- 主流程

def analyze_text(text, source="（直接输入）", emit_dir=None):
    """切章 + 算指标。返回可直接 JSON 化的 dict。"""
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
        truncated = True
    else:
        truncated = False
    pattern, chapters = split_chapters(text)
    rows = []
    for i, ch in enumerate(chapters, 1):
        m = chapter_metrics(ch["body"])
        m.update({"index": i, "title": ch["title"]})
        rows.append(m)
        if emit_dir:
            safe = re.sub(r"[\\/:*?\"<>|\s]+", "_", ch["title"]).strip("_")[:40] or "chapter"
            write_text(Path(emit_dir) / ("%02d_%s.md" % (i, safe)), ch["body"])
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": str(source),
        "pattern": pattern,
        "truncated": truncated,
        "summary": summarize(rows),
        "chapters": rows,
    }


def analyze_project_chapters(scope=None):
    """本书节奏：直接读取 data/chapters/<scope>/*.md（不落盘、不写文件）。

    与 analyze_file 输出同一 schema，GUI 用同一张图渲染"参考书 vs 自己的书"。
    """
    cands = [scope] if scope in ("raw", "checked", "refined") else ["refined", "checked", "raw"]
    scope_dir = None
    for sc in cands:
        d = Path("data/chapters") / sc
        if d.exists() and list(d.glob("*.md")):
            scope_dir = d
            break
    if scope_dir is None:
        return {"ok": False,
                "error": "尚无章节产物（先跑 stage4/5/6）",
                "chapters": [], "summary": summarize([])}

    rows = []
    for f in sorted(p for p in scope_dir.glob("*.md") if p.stem.isdigit()):
        try:
            text = read_text(f)
        except Exception:                                    # noqa: BLE001
            continue
        m = chapter_metrics(text)
        title_m = re.search(r"^#{1,3}\s*([^\n]{1,60})$", text, re.MULTILINE)
        m.update({"index": int(f.stem),
                  "title": (title_m.group(1).strip() if title_m
                            else "第%d章" % int(f.stem))})
        rows.append(m)
    return {
        "ok": True,
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "项目章节（data/chapters/%s）" % scope_dir.name,
        "scope": scope_dir.name,
        "pattern": "项目章节文件",
        "truncated": False,
        "summary": summarize(rows),
        "chapters": rows,
    }


def analyze_file(path, emit=False, out_json=OUT_JSON):
    """读取文件 → 拆书 → 落盘 data/state/book_pacing.json。"""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return False, "文件不存在: %s" % path, None
    try:
        text = read_text(p)
    except Exception as e:                                   # noqa: BLE001
        return False, "读取失败: " + type(e).__name__ + ": " + str(e)[:160], None
    if not text.strip():
        return False, "文件为空: %s" % path, None

    emit_dir = None
    if emit:
        emit_dir = Path(OUT_DIR) / re.sub(r"[\\/:*?\"<>|\s]+", "_", p.stem)[:40]
        emit_dir.mkdir(parents=True, exist_ok=True)

    result = analyze_text(text, source=str(p), emit_dir=emit_dir)
    out = Path(out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text(out, json.dumps(result, ensure_ascii=False, indent=2))
    if emit_dir:
        result["emit_dir"] = str(emit_dir)
    s = result["summary"]
    msg = ("拆书完成：《%s》→ %d 章 / %d 万字（标题模式：%s，节奏趋势：%s，"
           "字数离群 %d 章）→ %s"
           % (result["source"], s["chapters"], round(s["total_words"] / 10000, 1),
              result["pattern"] or "未识别（按一章处理）", s["trend"],
              len(s["outliers"]), out))
    if emit_dir:
        msg += "；切分正文已导出到 " + str(emit_dir)
    return True, msg, result


def format_text(result):
    s = result["summary"]
    L = ["拆书结果：%s" % result["source"],
         "标题模式：%s ｜ 章数：%d ｜ 总字数：%d"
         % (result["pattern"] or "未识别", s["chapters"], s["total_words"]),
         "字数：均值 %.0f（σ=%.0f, CV=%.2f，%d~%d）"
         % (s["word_count"]["mean"], s["word_count"]["std"], s["word_count"]["cv"],
            s["word_count"]["min"], s["word_count"]["max"]),
         "对话占比：%.1f%% ~ %.1f%%（均 %.1f%%）"
         % (s["dialogue_ratio"]["min"] * 100, s["dialogue_ratio"]["max"] * 100,
            s["dialogue_ratio"]["mean"] * 100),
         "节奏趋势：%s" % s["trend"],
         "-" * 60,
         "%-4s %-28s %8s %8s %8s %8s" % ("序", "标题", "字数", "段数", "均段", "对话%")]
    for r in result["chapters"][:60]:
        L.append("%-4d %-28s %8d %8d %8.1f %7.1f%%"
                 % (r["index"], r["title"][:28], r["word_count"], r["paragraphs"],
                    r["avg_para_len"], r["dialogue_ratio"] * 100))
    if len(result["chapters"]) > 60:
        L.append("…（共 %d 章，仅显示前 60）" % len(result["chapters"]))
    if s["outliers"]:
        L.append("")
        L.append("字数离群章：" + "、".join(
            "%s(%+d%%)" % (o["title"][:16] or ("#%d" % o["index"]), round(o["delta_pct"]))
            for o in s["outliers"][:8]))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="拆书 / 章节节奏分析（确定性）")
    ap.add_argument("--input", required=True, help="待拆分的文本文件（.txt/.md）")
    ap.add_argument("--emit", action="store_true",
                    help="同时把切分后的章节导出到 " + OUT_DIR)
    ap.add_argument("--out", default=OUT_JSON, help="节奏 JSON 输出路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--list-patterns", action="store_true", help="只打印各标题正则命中数")
    args = ap.parse_args()

    p = Path(args.input)
    if args.list_patterns:
        if not p.exists():
            print("文件不存在: %s" % p)
            return 1
        text = read_text(p)
        for name, rx in PATTERNS:
            print("%-14s %d 处命中" % (name, len(list(rx.finditer(text)))))
        return 0

    ok, msg, result = analyze_file(p, emit=args.emit, out_json=args.out)
    if not ok:
        print("[FAIL] " + msg)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_text(result))
        print("\n[OK] " + msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
