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


def set_events(root, chapter, checked, classes=None, evidence="", evidences=None,
               rejected=None, total=None):
    """F03b：作者勾选“这一章发生了几件事”。**唯一硬门**，系统不代勾。

    ## 三个计数，别混用（R24）

    - `events` = **勾了几条**（作者认定"是事件"）；
    - `counted_events` = **能计入下限的几条** = 勾了 + 分了类 + 有证据句。
      R24 的 AC-24-2：类别有、证据句空 → 可提交，落“无举证”，**计入条数少 1**；
    - `judged` = **已表态几条** = 勾了 + 明确标为"不算一件事"。
      R24 的 AC-24-5 要求“4 条全部有归属（勾+分类，或明确不勾）”才叫勾完 ——
      光有勾选时，"勾完了"只等于"你勾了你认为是的那些"，剩下的到底是不算还是没看，
      无法区分。`rejected`（明确不勾）就是补这个缺口。

    `rejected` 与 `checked` **互斥**（同一条不能既算又不算）；两者都有时以 `checked` 为准
    并把它从 rejected 里剔掉 —— 界面不该产生这种状态，这里只是防御。

    `total` 是章纲事件总数（界面传进来算进度）；不传就只记 `judged` 的绝对值。

    `evidence`（全章一句）保留兼容：早期界面只有一个输入框。逐事件证据在
    `evidences`（`{事件序号: 句子}`）。
    """
    doc = load(root)
    checked = [int(x) for x in (checked or [])]
    rejected = [int(x) for x in (rejected or []) if int(x) not in checked]
    classes = {str(k): v for k, v in (classes or {}).items()
               if v in EVENT_CLASSES}
    evidences = {str(k): str(v).strip() for k, v in (evidences or {}).items()
                 if str(v).strip()}
    counted = [i for i in checked if classes.get(str(i)) and evidences.get(str(i))]
    judged = len(set(checked) | set(rejected))
    doc["author"]["events"][str(int(chapter))] = {
        "chapter": int(chapter),
        "checked": checked,
        "rejected": rejected,
        "classes": classes,
        "evidences": evidences,
        "events": len(checked),
        "counted_events": len(counted),
        "judged": judged,
        "total": (int(total) if total is not None else None),
        "unproven": [i for i in checked if not evidences.get(str(i))],
        "chosen": (classes.get(str(checked[0])) if checked else None),
        "evidence_sentence": str(evidence or ""),
        "at": _now(),
    }
    save(root, doc)
    return doc["author"]["events"][str(int(chapter))]


def set_book_reject(root, reason):
    """B2：作者“整本否掉”（业务界定 A3 / S07，2026-10-31 补上）。

    ## 为什么只落记录，不自动动作

    否掉整本之后该做什么（清产物？退回阶段 2？归档另起？）是**产品决策**，
    不是这里该替作者定的。本函数的职责只有一件：把"作者判定整本不行"这件事
    连同理由、时间一起落盘，让报告与以后的自己都看得见。

    破坏性动作仍走既有路径（`reject.py` 打回、归档、换书），每一条都带确认。
    """
    reason = str(reason or "").strip()
    if len(reason) < 8:
        return False, {"error": "理由太短（至少 8 个字）—— 否掉整本是要留档的决定，"
                                "半年后你得看得懂当时为什么"}
    doc = load(root)
    doc["author"]["book_reject"] = {
        "at": _now(),
        "reason": reason,
        "by": "author",
    }
    save(root, doc)
    return True, doc["author"]["book_reject"]


def get_book_reject(root):
    return (load(root).get("author") or {}).get("book_reject")


def clear_book_reject(root):
    """撤销否掉（作者改主意）。留一条 history，别让"曾经否过"凭空消失。"""
    doc = load(root)
    author = doc.get("author") or {}
    prev = author.get("book_reject")
    if not prev:
        return False, {"error": "当前没有否掉记录"}
    author.setdefault("book_reject_history", []).append(
        dict(prev, cleared_at=_now()))
    author["book_reject"] = None
    doc["author"] = author
    save(root, doc)
    return True, prev


def get_events(root, chapter):
    return (load(root).get("author") or {}).get("events", {}).get(str(int(chapter)))


def set_sentence(root, chapter, paragraph_index, sentence, feeling, reason="",
                 source="author", from_proposal=None):
    """F12：逐句裁定。二值 + 一句理由（`拿不准` 也要求理由），原句必须能在正文里定位。

    `from_proposal`（M4，2026-11-04）：这条作者裁定是从**模型提案**终裁来的
    （提案 id 见 `proposals.feelings`）。留这个字段才答得出“最终结论里有多少条
    是模型先出的”—— 否则 M4=B 落地之后，报告上分不清哪条是作者自己写的感受。
    """
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
    if from_proposal:
        item["from_proposal"] = str(from_proposal)
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
