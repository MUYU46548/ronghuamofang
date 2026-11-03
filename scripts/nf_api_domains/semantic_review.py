# -*- coding: utf-8 -*-
"""`/review/semantic`：语义审稿的窄口（④，2026-11-03）。

只做确定性部分：读正文 → 开一个"审稿-<焦点>"讨论 → 写任务书。
**不调模型**（跑它的是调用方/流水线）；产物是讨论稿，不是分数。
"""
from __future__ import annotations


def handle_review_semantic(h, body):
    import nf_api as api
    from utils import review_semantic

    body = body if isinstance(body, dict) else {}
    try:
        chapter = int(body.get("chapter") or 0)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "chapter 必须是章号整数"}
    if chapter < 1:
        return 400, {"ok": False, "error": "chapter 必须是正整数"}
    focus = str(body.get("focus") or "").strip() or None
    if focus and focus not in review_semantic.FOCUS:
        return 400, {"ok": False,
                     "error": "focus 只能是 %s（想讨论别的，用 /discuss/open）"
                              % "、".join(review_semantic.FOCUS)}
    res = review_semantic.build_review(chapter, focus=focus, root=str(api.ROOT))
    if not res.get("ok"):
        return 404, {"ok": False, "error": res.get("error")}
    return 200, res


def handle_review_semantic_list(h):
    import nf_api as api
    from utils import review_semantic

    items = review_semantic.list_pending(str(api.ROOT))
    return 200, {"ok": True, "count": len(items), "pending": items}


ROUTES = (
    ("POST", "/review/semantic", handle_review_semantic),
    ("GET", "/review/semantic/list", handle_review_semantic_list),
)
