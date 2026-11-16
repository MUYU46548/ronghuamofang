# -*- coding: utf-8 -*-
"""节级产物：一章写几节、每节多长、哪一节空了（R1，2026-11-16）。

## 为什么要有这一层

OB37 施工回执（710 规划目录下的《OB37 施工回执》）
的第一条结构性缺口：**墨坊的粒度是"章"，而 1.6 万字一章不可能一次写到位。**

- 生成侧 `stage4_writing.py` 对"节"零命中；
- 转换侧 `stage7_convert.py` 早就认 `### 节` 标题（Heading 2）—— **格式预留了，数据没建**；
- 于是"这一章该写多长、哪一节写空了"只能靠人肉数：OB37 的书均从 5,000 涨到 16,465，
  全靠自建节拍表 + 逐节字数脚本。

本模块补三件事（**判据与数据同源，界面/脚本都读同一份**）：

1. **节列表从章大纲来**：`data/outline/chapters/NN.md` 里写“节列表”，
   每节带名称/目标字数（可选地点/在场/节拍/落点）。两种写法都认（见 `parse_outline_sections`）；
2. **按节产出**：stage4 把节列表注入任务（`render_prompt_block`），要求正文用
   `### 节 N｜名` 承载；写完以后按标题切段，**逐节数汉字并记偏移** → `data/chapters/NN/sections.json`；
3. **速记体判据**（回执 §七 判据 2）：任一节 < 目标 70%，或节均 < 1,000 汉字 → 判 `thin`，
   **不许标 stage 完成**。这一条是防"字数够了但每节都是速记"的那种假完成。

## 边界（刻意不做）

- **不自动生成节列表**：一章切几节是创作决定（回执 §八 的作者判定点），
  大纲没写节就按整章走，行为与 2026-11-16 之前逐字一致（零回归）。
- **不自动改写正文**：本模块只量、只报、只落数据。改哪一节由作者点。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from utils.word_count import count_cn_words

#: 正文里的节标题（stage7 会把 `###` 转成 Heading 2）。
SECTION_HEAD_RE = re.compile(r"^#{2,3}\s*节\s*(\d+)\s*(?:[｜|:：\-—]\s*(.*))?$")
#: 大纲里的节标题（`### 节 1｜名` 或 `## 节 1｜名`）
OUTLINE_HEAD_RE = re.compile(r"^#{2,4}\s*节\s*(\d+)\s*(?:[｜|:：\-—]\s*(.*))?$")
#: 大纲里的单行节条目：`- 节 1｜名｜目标 2100｜地点 门厅｜…`
OUTLINE_LINE_RE = re.compile(r"^\s*[-*]\s*节\s*(\d+)\s*[｜|]\s*(.+)$")

_FIELD_ALIASES = {
    "target": ("目标字数", "目标", "字数"),
    "place": ("地点", "场景"),
    "present": ("在场", "人物", "角色"),
    "beat": ("节拍", "事件", "动作"),
    "landing": ("落点", "结果", "收束"),
}

DEFAULT_MIN_RATIO = 0.70      # 单节低于目标的 70% → 速记
DEFAULT_MIN_HANZI = 1000      # 节均低于这个数 → 速记（回执 §七 判据 2）


# ---------------------------------------------------------------- 解析：大纲
def _split_inline(rest: str):
    """把 `名｜目标 2100｜地点 门厅` 拆成 (name, {field: value})。"""
    parts = [p.strip() for p in re.split(r"[｜|]", rest) if p.strip()]
    if not parts:
        return "", {}
    name, fields = parts[0], {}
    for chunk in parts[1:]:
        key, sep, val = chunk.partition("：")
        if not sep:
            key, sep, val = chunk.partition(":")
        if not sep:
            # 单行写法常用空格分隔：`目标 600`、`地点 门厅`（OB37 的节拍表就长这样）
            key, sep, val = chunk.partition(" ")
        key, val = key.strip(), val.strip()
        matched = False
        for canon, aliases in _FIELD_ALIASES.items():
            if key in aliases:
                fields[canon] = val
                matched = True
                break
        if not matched:
            # 没有字段名的一律当"节拍"补充（`｜敲门→没人应`）
            fields["beat"] = (fields.get("beat", "") + "｜" + chunk).strip("｜")
    return name.strip(), fields


def _target_of(fields, default=None):
    raw = str(fields.get("target") or "").strip()
    m = re.search(r"\d+", raw)
    if m:
        try:
            return int(m.group(0))
        except ValueError:
            return default
    return default


def parse_outline_sections(outline_text, default_target=None):
    """从章大纲里解析节列表。返回 `[{n, name, target, place, present, beat, landing}]`。

    两种写法都认（模板写哪一种都行）：

        ### 节 1｜雨夜入门
        - 目标字数：2100
        - 地点：门厅

        - 节 1｜雨夜入门｜目标 2100｜地点 门厅｜在场 他、她｜节拍 敲门→没人应

    没有节列表 → 返回 `[]`（**不是错误**：老工作区的大纲都长这样，按整章走）。
    """
    text = str(outline_text or "")
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        mh = OUTLINE_HEAD_RE.match(line.strip())
        if mh:
            n = int(mh.group(1))
            name, fields = _split_inline(mh.group(2) or "")
            i += 1
            # 吃这条标题下面的 `- 字段：值` 行
            while i < len(lines):
                sub = lines[i].strip()
                if not sub:
                    i += 1
                    continue
                if sub.startswith(("#", "##", "###", "####", "- 节", "* 节")):
                    break
                if not sub.startswith(("-", "*")):
                    break
                body = sub.lstrip("-* ").strip()
                key, sep, val = body.partition("：")
                if not sep:
                    key, sep, val = body.partition(":")
                if sep:
                    key = key.strip()
                    for canon, aliases in _FIELD_ALIASES.items():
                        if key in aliases:
                            fields[canon] = val.strip()
                            break
                    else:
                        fields["beat"] = (fields.get("beat", "") + "｜" + body).strip("｜")
                i += 1
            out[n] = {"n": n, "name": name, "target": _target_of(fields, default_target),
                      "place": fields.get("place", ""), "present": fields.get("present", ""),
                      "beat": fields.get("beat", ""), "landing": fields.get("landing", "")}
            continue
        ml = OUTLINE_LINE_RE.match(line)
        if ml:
            n = int(ml.group(1))
            name, fields = _split_inline(ml.group(2))
            out[n] = {"n": n, "name": name, "target": _target_of(fields, default_target),
                      "place": fields.get("place", ""), "present": fields.get("present", ""),
                      "beat": fields.get("beat", ""), "landing": fields.get("landing", "")}
        i += 1
    return [out[k] for k in sorted(out)]


def fill_targets(sections, chapter_min=4000, chapter_max=7200):
    """给没写目标字数的节补一个目标（章目标中值 / 节数）。

    为什么要补而不是报错：节拍表里常常只写"这一节干什么"，不写字数；
    没有目标就量不出"空了"，那一节就永远没人管。**补的值会写在报告里**
    （`target_source: "derived"`），作者能看见这是推出来的，不是他写的。
    """
    secs = [dict(s) for s in (sections or [])]
    if not secs:
        return secs
    avg = int(round(((int(chapter_min) + int(chapter_max)) / 2.0) / len(secs)))
    for s in secs:
        if not s.get("target"):
            s["target"] = avg
            s["target_source"] = "derived"
        else:
            s["target_source"] = "outline"
    return secs


# ---------------------------------------------------------------- 解析：正文
def split_body_sections(body):
    """按 `### 节 N｜名` 切正文。返回 `[{n, name, hanzi, offset, text}]`。

    没有节标题 → 返回 `[]`（老稿件、或这一章根本没切节）。
    """
    text = str(body or "")
    hits = []
    pos = 0
    for line in text.splitlines(keepends=True):
        m = SECTION_HEAD_RE.match(line.strip())
        if m:
            hits.append((int(m.group(1)), (m.group(2) or "").strip(), pos))
        pos += len(line)
    if not hits:
        return []
    out = []
    for idx, (n, name, start) in enumerate(hits):
        end = hits[idx + 1][2] if idx + 1 < len(hits) else len(text)
        chunk = text[start:end]
        # 标题行本身不算正文
        first_nl = chunk.find("\n")
        chunk_body = chunk[first_nl + 1:] if first_nl >= 0 else ""
        out.append({"n": n, "name": name, "hanzi": count_cn_words(chunk_body),
                    "offset": start, "text": chunk_body})
    return out


# ---------------------------------------------------------------- 量/判
def _cfg_num(cfg, key, default):
    try:
        v = (cfg or {}).get("chapter", {}).get(key)
        return type(default)(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def report_path(chapter, root="."):
    return Path(root) / "data" / "chapters" / ("%02d" % int(chapter)) / "sections.json"


def read_report(chapter, root="."):
    p = report_path(chapter, root)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_report(report, root="."):
    p = report_path(report.get("chapter"), root)
    p.parent.mkdir(parents=True, exist_ok=True)
    from utils.file_io import write_text
    write_text(p, json.dumps(report, ensure_ascii=False, indent=2))
    return p


def measure(chapter, body, outline_text, cfg=None):
    """量一章的节级数据（零 token）。返回报告 dict，**不变更任何文件**。"""
    cfg = cfg or {}
    tw = (cfg.get("chapter") or {}).get("target_words") or [4000, 7200]
    declared = parse_outline_sections(outline_text)
    declared = fill_targets(declared, tw[0], tw[1])
    written = split_body_sections(body)

    min_ratio = _cfg_num(cfg, "section_min_ratio", DEFAULT_MIN_RATIO)
    min_hanzi = _cfg_num(cfg, "section_min_hanzi", DEFAULT_MIN_HANZI)

    rows = []
    by_n = {s["n"]: s for s in written}
    for d in declared:
        w = by_n.get(d["n"]) or {}
        hanzi = int(w.get("hanzi") or 0)
        target = int(d.get("target") or 0)
        ratio = round(hanzi / target, 2) if target else None
        rows.append({"n": d["n"], "name": d.get("name") or w.get("name") or "",
                     "target": target, "target_source": d.get("target_source"),
                     "hanzi": hanzi, "ratio": ratio,
                     "offset": w.get("offset"),
                     "ok": bool(ratio is not None and ratio >= min_ratio)})
    # 正文里有、大纲里没声明的节，也列出来（多写的节不该被静默忽略）
    for n, w in sorted(by_n.items()):
        if n not in {d["n"] for d in declared}:
            rows.append({"n": n, "name": w["name"], "target": None, "target_source": None,
                         "hanzi": w["hanzi"], "ratio": None, "offset": w["offset"],
                         "ok": True, "undeclared": True})

    total = count_cn_words(body)
    report = {"chapter": int(chapter), "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "declared": bool(declared), "hanzi": total,
              "section_count": len([r for r in rows if not r.get("undeclared")]),
              "min_ratio": min_ratio, "min_hanzi": min_hanzi,
              "sections": rows}

    if not declared:
        report.update({"ok": True, "verdict": "no_sections",
                       "why": "这一章的大纲没有节列表 —— 按整章走（不是失败）"})
        return report
    if not written:
        report.update({"ok": False, "verdict": "missing_headings",
                       "why": "大纲声明了 %d 节，正文里一个 `### 节 N｜名` 都没有"
                              % len(declared)})
        return report
    missing = [d["n"] for d in declared if d["n"] not in by_n]
    short = [r["n"] for r in rows if not r.get("undeclared") and not r["ok"]]
    avg = int(round(sum(r["hanzi"] for r in rows if not r.get("undeclared"))
                    / max(1, len(declared))))
    thin_avg = avg < min(min_hanzi, int(min(d["target"] for d in declared) * min_ratio))
    if missing or short or thin_avg:
        reasons = []
        if missing:
            reasons.append("缺节：%s" % "、".join(str(x) for x in missing))
        if short:
            reasons.append("节太短（< 目标 %d%%）：%s"
                           % (int(min_ratio * 100), "、".join(str(x) for x in short)))
        if thin_avg:
            reasons.append("节均 %d 汉字，低于 %d —— 速记体" % (avg, min_hanzi))
        report.update({"ok": False, "verdict": "thin", "why": "；".join(reasons),
                       "missing": missing, "short": short, "avg_hanzi": avg})
    else:
        report.update({"ok": True, "verdict": "ok", "avg_hanzi": avg,
                       "why": "%d 节全部达标（节均 %d 汉字）" % (len(declared), avg)})
    return report


def measure_and_save(chapter, body, outline_text, cfg=None, root="."):
    """量完落盘（`data/chapters/NN/sections.json`）。写盘失败只留痕，不炸写作。"""
    report = measure(chapter, body, outline_text, cfg)
    try:
        write_report(report, root)
    except Exception as e:                                     # noqa: BLE001
        print("[sections] ⚠ 节级数据写盘失败（章节本身不受影响）：%s" % e)
    return report


def complete(chapter, chapter_path, outline_path, cfg=None, root="."):
    """断点续跑用：这一章在**节级**上算不算完成。没有声明节 → 恒 True。"""
    try:
        outline_text = Path(outline_path).read_text(encoding="utf-8")
    except OSError:
        return True
    if not parse_outline_sections(outline_text):
        return True
    try:
        body = Path(chapter_path).read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(measure(chapter, body, outline_text, cfg).get("ok"))


# ---------------------------------------------------------------- 提示词
def render_prompt_block(sections, chapter_target=None):
    """把节列表渲染成任务文件里的“按节写作”硬要求。

    为什么要把目标字数写到提示词里：模型对"这一节写 2200 个汉字"这种硬数字
    的服从度，明显高于"写够一万六"——后者会被它自己均摊成十段速记。
    """
    secs = [s for s in (sections or [])]
    if not secs:
        return ""
    lines = ["## 本章按节写（**硬要求**）", "",
             "这一章分 %d 节，正文必须用下面的标题承载，一节都不许省：" % len(secs), ""]
    for s in secs:
        bits = ["### 节 %d｜%s" % (s["n"], s.get("name") or "未命名"),
                "  - 目标：**%d 个汉字**（只数汉字，不含标点）" % int(s.get("target") or 0)]
        for key, label in (("place", "地点"), ("present", "在场"),
                           ("beat", "节拍"), ("landing", "落点")):
            if s.get(key):
                bits.append("  - %s：%s" % (label, s[key]))
        lines += bits
        lines.append("")
    total = sum(int(s.get("target") or 0) for s in secs)
    lines += [
        "三条硬约束：",
        "",
        "1. `### 节 N｜名` 这一行**必须原样出现**（Word 转换按它切标题，运营方按它数每节字数）；",
        "2. 每一节写够目标汉字数的九成以上 —— 某一节明显短，这一章会被判“速记体”，**不算完成**；",
        "3. 节与节之间承接要连贯，不要写成 %d 篇互不相干的短文。" % len(secs),
        "",
        "（目标合计 %d 个汉字；章的字数区间仍按提示词上方的 min/max 判。）" % total,
        "",
    ]
    return "\n".join(lines)
