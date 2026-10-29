# -*- coding: utf-8 -*-
"""F13 判定记录单一出口：所有判定结果只写这一份文件。

为什么必须"单一出口"：现状是评审写 checked、评分对象却是自己改过的稿 ——
结论散在多处，谁都能改一笔，于是"报告说 A、用户拿到 B"。这里把
**稿件版本 × 口径版本 × 判定结果 + 作者裁定 + 事件勾选**全部收进一个 JSON，
并且只留 `save()` 一个写点（单测会枚举写入目录做反证）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

RECORDS_REL = "data/judge/records.json"
SCHEMA = 1

EVENT_CLASSES = ["人物处境", "信息占有", "关系", "目标"]


def _now():
    return datetime.now().isoformat(timespec="seconds")


def empty():
    return {
        "schema": SCHEMA,
        "created_at": _now(),
        "runs": [],
        "author": {"events": {}, "sentences": []},
        "caliber_history_seen": [],
    }


def load(root="."):
    p = Path(root) / RECORDS_REL
    if not p.exists():
        return empty()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return empty()
    d.setdefault("runs", [])
    d.setdefault("author", {"events": {}, "sentences": []})
    d["author"].setdefault("events", {})
    d["author"].setdefault("sentences", [])
    return d


def save(root, doc):
    """**唯一写入口**（F13）。改记录只许走这里。"""
    p = Path(root) / RECORDS_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def latest_run(doc):
    runs = (doc or {}).get("runs") or []
    return runs[-1] if runs else None


def append_run(root, run):
    doc = load(root)
    doc["runs"].append(run)
    doc["runs"] = doc["runs"][-50:]          # 只留最近 50 次（历史结论仍在各自 run 里）
    save(root, doc)
    return run


def set_events(root, chapter, checked, classes=None, evidence=""):
    """F03b：作者勾选「这一章发生了几件事」。**唯一硬门**，系统不代勾。"""
    doc = load(root)
    checked = [int(x) for x in (checked or [])]
    classes = {str(k): v for k, v in (classes or {}).items()
               if v in EVENT_CLASSES}
    doc["author"]["events"][str(int(chapter))] = {
        "chapter": int(chapter),
        "checked": checked,
        "classes": classes,
        "events": len(checked),
        "chosen": (classes.get(str(checked[0])) if checked else None),
        "evidence_sentence": str(evidence or ""),
        "at": _now(),
    }
    save(root, doc)
    return doc["author"]["events"][str(int(chapter))]


def get_events(root, chapter):
    return (load(root).get("author") or {}).get("events", {}).get(str(int(chapter)))


def set_sentence(root, chapter, paragraph_index, sentence, feeling, reason="",
                 source="author"):
    """F12：逐句裁定。二值 + 一句理由（`拿不准` 也要求理由），原句必须能在正文里定位。"""
    if feeling not in ("打中了", "没打中", "拿不准"):
        return False, {"error": "感受类型只能是 打中了 / 没打中 / 拿不准"}
    if not str(sentence or "").strip():
        return False, {"error": "原句不能为空（裁定必须带位置）"}
    if not str(reason or "").strip():
        return False, {"error": "理由必填 —— 无理由的裁定等于随手点击"}
    doc = load(root)
    item = {
        "chapter": int(chapter),
        "paragraph_index": int(paragraph_index),
        "sentence": str(sentence),
        "feeling": feeling,
        "reason": str(reason).strip(),
        "source": source,
        "at": _now(),
    }
    doc["author"]["sentences"].append(item)
    doc["author"]["sentences"] = doc["author"]["sentences"][-2000:]
    save(root, doc)
    return True, item


def sentence_verdicts(root, chapter=None):
    items = (load(root).get("author") or {}).get("sentences") or []
    if chapter is None:
        return items
    return [x for x in items if int(x.get("chapter") or 0) == int(chapter)]


def prune_author(doc, valid_chapters):
    """删除已不存在章节的裁定（换书后旧裁定不该污染新书）。"""
    keep = set(str(int(x)) for x in valid_chapters)
    a = doc.get("author") or {}
    a["events"] = {k: v for k, v in (a.get("events") or {}).items() if k in keep}
    a["sentences"] = [x for x in (a.get("sentences") or [])
                      if str(x.get("chapter")) in keep]
    return doc
