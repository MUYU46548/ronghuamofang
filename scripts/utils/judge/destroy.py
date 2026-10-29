# -*- coding: utf-8 -*-
"""R02 正文销毁检测（P0 形态 = 离线三稿对照，不改 stage5/6 执行路径）。

要回答的问题只有一个：**这一章的 6000 字，是哪一步被削到 2000 的、删了哪些句子。**
所以这里只读三份稿（raw / checked / refined），逐句对齐，输出删除/改写清单与缩减率；
缩减率超口径（默认 5%）即判「销毁」并进缺陷单。

为什么不改 stage5/6：那要真机重跑（花钱），P0 的硬线是零 token、断网可跑。
"""
from __future__ import annotations

import difflib

from . import text as T


def _sentences_with_loc(body):
    out = []
    for pi, si, s in T.iter_sentences(body):
        out.append({"paragraph_index": pi, "sentence_index": si, "sentence": s})
    return out


def words(body):
    return T.count_cn_words(body or "")


def paragraphs(body):
    return T.paragraph_count(body or "")


def compare(raw, checked, refined, cal=None, threshold=None):
    """三稿对照。缺哪一版就明说「该版不存在」，绝不记 0（记 0 会让「删了 4000 字」显示成无变化）。"""
    cal = cal or {}
    limit = float(cal.get("J11", 0.05)) if threshold is None else float(threshold)
    stages = {"raw": raw, "checked": checked, "refined": refined}
    present = {k: bool((v or "").strip()) for k, v in stages.items()}
    if not any(present.values()):
        return {"ok": False, "reason": "不可判定：无正文", "present": present}

    counts = {k: words(stages[k]) if present[k] else None for k in stages}
    paras = {k: paragraphs(stages[k]) if present[k] else None for k in stages}

    def _shrink(a, b):
        wa, wb = counts.get(a), counts.get(b)
        if not wa or wb is None:
            return None
        return round((wa - wb) / float(wa), 4)

    def _diff(a, b):
        """a → b 的逐句对照：删除句 / 改写句（带段号与原句）。"""
        if not (present.get(a) and present.get(b)):
            return {"ok": False, "deleted": [], "rewritten": []}
        la = _sentences_with_loc(stages[a])
        lb = _sentences_with_loc(stages[b])
        sa = [x["sentence"] for x in la]
        sb = [x["sentence"] for x in lb]
        sm = difflib.SequenceMatcher(a=sa, b=sb, autojunk=False)
        deleted, rewritten = [], []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                continue
            if tag == "delete" or (tag == "replace" and (i2 - i1) > (j2 - j1)):
                for k in range(i1, i2):
                    deleted.append(dict(la[k]))
            if tag == "replace":
                for k in range(i1, i2):
                    old = la[k]
                    new = lb[j1 + (k - i1)] if (j1 + (k - i1)) < j2 else None
                    rewritten.append({
                        "paragraph_index": old["paragraph_index"],
                        "sentence": old["sentence"],
                        "changed_to": (new or {}).get("sentence"),
                    })
        return {"ok": True, "deleted": deleted, "rewritten": rewritten}

    chain = {}
    for a, b in (("raw", "checked"), ("checked", "refined"), ("raw", "refined")):
        chain["%s->%s" % (a, b)] = _shrink(a, b)

    primary = _diff("raw", "checked")
    second = _diff("checked", "refined")
    drops = [v for v in chain.values() if v is not None]
    worst = max(drops) if drops else None
    destroyed = bool(worst is not None and worst > limit)
    # 两次压缩（raw→checked 与 checked→refined 都明显缩水）→ 对象链不可比，判定只能落 B
    compressed_twice = bool(
        (chain.get("raw->checked") or 0) > limit
        and (chain.get("checked->refined") or 0) > limit)

    return {
        "ok": True,
        "present": present,
        "words": counts,
        "paragraphs": paras,
        "shrink": chain,
        "threshold": limit,
        "destroyed": destroyed,
        "compressed_twice": compressed_twice,
        "deleted": primary.get("deleted", []) + second.get("deleted", []),
        "rewritten": primary.get("rewritten", []) + second.get("rewritten", []),
        "missing": [k for k, v in present.items() if not v],
        "note": ("raw %s → checked %s，缩减 %.1f%%，疑似整章重生成"
                 % (counts.get("raw"), counts.get("checked"),
                    (chain.get("raw->checked") or 0) * 100)
                 if chain.get("raw->checked") is not None else ""),
    }
