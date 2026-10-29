# -*- coding: utf-8 -*-
"""R07 成品与源稿一致性检测（P0 形态：比对 + 标「已过期」，**不做自动重出**）。

为什么 P0 只做检测：重出必须重跑评审→润色→成书（花钱、要模型），
而"能看见成品已过期"是纯离线比对 —— 正好落在这期"零 token、断网可跑"的线上。

失效判定必须给**两项凭据**（字数 + 时间），只给一个"已过期"徽标 = 未实现：
- 字数：现在稿子里第 3 章 5967 字，成品里只有 2186 字
- 时间：第 3 章 10-27 改过，成品是 10-25 16:00 出的
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import text as T

DOCX_DIR = "output"
CHAP_RE = re.compile(r"第\s*(\d+)\s*章")
WORD_TOLERANCE = 0.01        # 字数差 1% 以内算一致（文档格式会带来少量误差）


def _docx_text(path):
    try:
        import docx                                            # python-docx
        doc = docx.Document(str(path))
        parts = [p.text for p in doc.paragraphs]
        for tb in doc.tables:
            for row in tb.rows:
                for cell in row.cells:
                    parts.append(cell.text)
        return "\n".join(parts)
    except Exception:                                          # noqa: BLE001
        return None


def newest_docx(root="."):
    out = Path(root) / DOCX_DIR
    if not out.is_dir():
        return None
    files = [p for p in out.glob("*.docx") if not p.name.startswith("~$")]
    if not files:
        return None
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def split_chapters(text):
    """按「第N章」切分成品正文（拿不到编号的段落归到最近一章）。"""
    text = text or ""
    marks = list(CHAP_RE.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        n = int(m.group(1))
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[n] = text[m.start():end]
    return out


def _fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def status(root="."):
    """成品 vs 源稿：返回逐章字数/时间对比 + 是否过期 + 两项凭据。"""
    root = Path(root)
    p = newest_docx(root)
    if not p:
        return {"ok": True, "exists": False, "status": "未生成",
                "note": "还没有生成过 Word 成品 —— 这不是「已过期」，是「还没出」",
                "chapters": [], "stale": False, "evidence": []}
    text = _docx_text(p)
    if text is None:
        return {"ok": False, "error": "成品读不出来（缺 python-docx 或文件损坏）：%s" % p.name,
                "exists": True, "path": str(p.relative_to(root)).replace("\\", "/"),
                "stale": None}
    per = split_chapters(text)
    doc_mtime = p.stat().st_mtime
    rows, evidence, stale_chapters = [], [], []
    raw_dir = root / "data" / "chapters" / "raw"
    refined_dir = root / "data" / "chapters" / "refined"
    for src in sorted(raw_dir.glob("*.md")):
        if not src.stem.isdigit():
            continue
        n = int(src.stem)
        refined = refined_dir / src.name
        source = refined if refined.exists() else src
        src_text = source.read_text(encoding="utf-8")
        src_words = T.count_cn_words(src_text)
        got = per.get(n)
        docx_words = T.count_cn_words(got) if got else 0
        src_mtime = source.stat().st_mtime
        word_gap = abs(docx_words - src_words)
        words_differ = got is None or word_gap > max(30, src_words * WORD_TOLERANCE)
        time_later = src_mtime > doc_mtime + 60
        row = {
            "chapter": n,
            "docx_words": docx_words,
            "src_words": src_words,
            "src_layer": "refined" if refined.exists() else "raw",
            "src_modified": _fmt_ts(src_mtime),
            "stale": bool(words_differ or time_later),
            "reasons": [],
        }
        if words_differ:
            row["reasons"].append("字数对不上：成品 %d 字 / 现在稿子 %d 字"
                                  % (docx_words, src_words))
        if time_later:
            row["reasons"].append("时间对不上：稿子 %s 改过，成品 %s 出的"
                                  % (_fmt_ts(src_mtime), _fmt_ts(doc_mtime)))
        if row["stale"]:
            stale_chapters.append(n)
            evidence.append("第 %d 章：%s" % (n, "；".join(row["reasons"])))
        rows.append(row)
    return {
        "ok": True, "exists": True,
        "path": str(p.relative_to(root)).replace("\\", "/"),
        "name": p.name,
        "built_at": _fmt_ts(doc_mtime),
        "stale": bool(stale_chapters),
        "stale_chapters": stale_chapters,
        "chapters": rows,
        "evidence": evidence[:5],
        "note": ("成品已过期：第 %s 章与现在稿子对不上" % "、".join(str(x) for x in stale_chapters)
                 if stale_chapters else "成品与源稿一致"),
    }
