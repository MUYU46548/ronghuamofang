# -*- coding: utf-8 -*-
"""`/lookup/*` 与 `/notes/*`：作品资料检索（① 资料工具，2026-11-03）。

| 方法 | 路径 | 干什么 |
|---|---|---|
| GET  | `/lookup?q=&kind=&limit=` | 按关键词查设定/素材/笔记（名字 + 出处） |
| POST | `/lookup/rebuild`        | 重建索引（设定集 / 素材 / 笔记变了之后跑一次） |
| GET  | `/notes/list`            | 案头笔记清单 |
| POST | `/notes/add`             | 写一条案头笔记（`data/notes/`，不进正典） |

纪律：路径一律经 `api.ROOT`；**写只落 `data/notes/` 与 `data/index/`**，不碰正典、
不碰任何产物。
"""
from __future__ import annotations


def _bad(msg):
    return 400, {"ok": False, "error": str(msg)}


def handle_lookup(h):
    """`GET /lookup?q=...` —— 查资料。索引不存在时**自动建一次**（只写索引文件）。"""
    import nf_api as api
    from utils import lookup

    q = h._query()
    query = (q.get("q") or q.get("query") or [""])[0].strip()
    kind = (q.get("kind") or [""])[0].strip() or None
    try:
        limit = int((q.get("limit") or ["8"])[0])
    except (TypeError, ValueError):
        limit = 8
    if not query:
        return _bad("q 必填（要查什么）")
    if lookup.load_index(str(api.ROOT)) is None:
        lookup.build_index(str(api.ROOT))
    hits = lookup.search(query, kind=kind, limit=limit, root=str(api.ROOT))
    return 200, {"ok": True, "q": query, "kind": kind, "count": len(hits), "hits": hits,
                 "block": lookup.render(query, kind=kind, limit=limit, root=str(api.ROOT))}


def handle_lookup_rebuild(h, body):
    """`POST /lookup/rebuild` —— 重建索引，返回条目数与耗时来源。"""
    import nf_api as api
    from utils import lookup

    idx = lookup.build_index(str(api.ROOT))
    kinds = {}
    for e in idx.get("entries") or []:
        kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
    return 200, {"ok": True, "count": idx.get("count"), "built_at": idx.get("built_at"),
                 "kinds": kinds, "path": str(api.ROOT / lookup.INDEX_PATH)}


def handle_notes_list(h):
    import nf_api as api
    from utils import lookup

    items = lookup.list_notes(str(api.ROOT))
    return 200, {"ok": True, "count": len(items), "notes": items}


def handle_note_add(h, body):
    """`POST /notes/add {title, text}` —— 写案头笔记（不碰正典）。"""
    import nf_api as api
    from utils import lookup

    body = body if isinstance(body, dict) else {}
    title = str(body.get("title") or "").strip()
    text = str(body.get("text") or "").strip()
    if not title:
        return _bad("title 必填")
    if not text:
        return _bad("text 不能为空")
    p = lookup.add_note(title, text, root=str(api.ROOT))
    return 200, {"ok": True, "path": str(p),
                 "message": "已写入案头笔记（不是正典；要进设定请走正式通道）"}


ROUTES = (
    ("GET", "/lookup", handle_lookup),
    ("POST", "/lookup/rebuild", handle_lookup_rebuild),
    ("GET", "/notes/list", handle_notes_list),
    ("POST", "/notes/add", handle_note_add),
)
