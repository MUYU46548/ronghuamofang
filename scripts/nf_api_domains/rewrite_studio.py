# -*- coding: utf-8 -*-
"""`/rewrite/studio`：改写工作室（C 组三个对标活，2026-11-03）。

只做确定性部分：读正文/摘录 → 开讨论 → 出任务书。**不调模型**。
"""
from __future__ import annotations


def handle_rewrite_studio(h, body):
    import nf_api as api
    from utils import rewrite_studio

    body = body if isinstance(body, dict) else {}
    try:
        chapter = int(body.get("chapter") or 0)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "chapter 必须是章号整数"}
    if chapter < 1:
        return 400, {"ok": False, "error": "chapter 必须是正整数"}
    res = rewrite_studio.build(str(body.get("mode") or ""), chapter,
                               excerpt=str(body.get("excerpt") or ""), root=str(api.ROOT))
    if not res.get("ok"):
        return 400, {"ok": False, "error": res.get("error")}
    return 200, res


def handle_rewrite_studio_list(h):
    import nf_api as api
    from utils import rewrite_studio

    items = rewrite_studio.list_pending(str(api.ROOT))
    return 200, {"ok": True, "count": len(items), "pending": items,
                 "modes": {k: v["label"] for k, v in rewrite_studio.MODES.items()}}


ROUTES = (
    ("POST", "/rewrite/studio", handle_rewrite_studio),
    ("GET", "/rewrite/studio/list", handle_rewrite_studio_list),
)
