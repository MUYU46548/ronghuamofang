# -*- coding: utf-8 -*-
"""stage5 改稿裁定闸的 API（2026-11-04，N1=B）。

## 端点

| 端点 | 作用 | 谁可调 |
|---|---|---|
| `GET /review/stage5/list` | 待裁定清单（章 / 状态 / 已定项数） | 读侧不拦 |
| `GET /review/stage5/chapter?n=` | 单章裁定项（逐项 old/new/已定） | 读侧不拦 |
| `POST /review/stage5/decide` | 作者逐条勾"采用 / 不采用" | **仅 GUI**（403） |
| `POST /review/stage5/apply` | 合成 `checked/NN.md`（可 `all:true`） | **仅 GUI**（403） |

## 纪律

1. 零 token：本域不构造模型客户端。
2. 路径一律 `api.ROOT / ...`（`--root` 场景不许读错项目）。
3. 裁定与合成都是**作者动作**，已列入 `agent_guard.FORBIDDEN_IN_AGENT_MODE`。
"""
import nf_api as api
import stage5_gate as G


def _query(h):
    qs = (getattr(h, "path", "") or "").split("?", 1)
    return api.parse_qs(qs[1]) if len(qs) > 1 else {}


def _summary(meta):
    items = meta.get("items") or []
    return {
        "chapter": meta.get("chapter"),
        "status": meta.get("status"),
        "items": len(items),
        "decided": sum(1 for it in items if it.get("accepted") is not None),
        "pending": [it["id"] for it in items if it.get("accepted") is None],
    }


def handle_stage5_list(h):
    rows = G.list_all(root=api.ROOT)
    return 200, {"ok": True, "chapters": rows,
                 "pending": [r["chapter"] for r in rows
                             if r["status"] in ("pending", "ready")]}


def handle_stage5_chapter(h):
    try:
        n = int((_query(h).get("n") or ["0"])[0])
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "n 必须是章号"}
    meta = G.load(n, root=api.ROOT)
    if not meta:
        return 404, {"ok": False, "error": "第 %d 章没有待裁定的改稿" % n}
    return 200, {"ok": True, "chapter": n, "status": meta.get("status"),
                 "raw_sha": meta.get("raw_sha"),
                 "items": meta.get("items") or [],
                 "can_apply": all(it.get("accepted") is not None
                                  for it in meta.get("items") or []),
                 "updated_at": meta.get("updated_at")}


def handle_stage5_decide(h, body):
    body = body or {}
    try:
        n = int(body.get("chapter"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "chapter 必填（整数）"}
    raw = body.get("decisions") or {}
    if not isinstance(raw, dict) or not raw:
        return 400, {"ok": False, "error": "decisions 必填：{项号: true/false}"}
    dec = {}
    for k, v in raw.items():
        try:
            dec[int(k)] = bool(v)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "裁定项号必须是整数：%r" % (k,)}
    ok, msg, meta = G.decide(n, dec, root=api.ROOT)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg, "chapter": _summary(meta) if meta else None}


def handle_stage5_apply(h, body):
    body = body or {}
    if body.get("all"):
        n, msgs = G.apply_all(root=api.ROOT)
        return 200, {"ok": True, "applied": n, "messages": msgs}
    try:
        n = int(body.get("chapter"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "chapter 必填（整数），或传 all:true"}
    ok, msg, path = G.apply_chapter(n, root=api.ROOT)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg,
                 "path": str(path.relative_to(api.ROOT)) if path else None}


ROUTES = [
    ("GET", "/review/stage5/list", handle_stage5_list),
    ("GET", "/review/stage5/chapter", handle_stage5_chapter),
    ("POST", "/review/stage5/decide", handle_stage5_decide),
    ("POST", "/review/stage5/apply", handle_stage5_apply),
]
