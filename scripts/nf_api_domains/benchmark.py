# -*- coding: utf-8 -*-
"""判定基准集（正例稿）的 API（2026-11-04，`mofang.judge.benchmark`）。

| 端点 | 作用 | 谁可调 |
|---|---|---|
| `GET /judge/benchmark` | 清单 + 免责声明 + 已移除项 | 读侧不拦 |
| `POST /judge/benchmark/import` | 导入一份正例（path 或 text） | **仅 GUI** |
| `POST /judge/benchmark/remove` | 移出基准集（进 `_removed/`，可恢复） | **仅 GUI** |
| `POST /judge/benchmark/restore` | 从 `_removed/` 恢复 | **仅 GUI** |

为什么写侧全 GUI-only：这份稿子是**作者的审美样本**。外部 Agent 若能自己塞一份，
"不误报"就变成"Agent 自己选标准自己判"——和审批被代签是同一类问题。
"""
import nf_api as api
from utils import benchmark as B


def _query(h):
    qs = (getattr(h, "path", "") or "").split("?", 1)
    return api.parse_qs(qs[1]) if len(qs) > 1 else {}


def handle_benchmark_list(h):
    include = (_query(h).get("removed") or ["0"])[0] in ("1", "true", "yes")
    return 200, B.list_entries(root=api.ROOT, include_removed=include)


def handle_benchmark_import(h, body):
    body = body or {}
    accept = body.get("accept_license") is True
    name = body.get("name") or ""
    if body.get("text"):
        ok, msg, ent = B.import_text(name or "粘贴正例", body.get("text"),
                                     accept, source="粘贴（界面）", root=api.ROOT)
    else:
        ok, msg, ent = B.import_path(body.get("path"), name, accept, root=api.ROOT)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg,
                 "entry": B._summary(ent) if ent else None,
                 "count": B.count(api.ROOT)}


def handle_benchmark_remove(h, body):
    eid = str((body or {}).get("id") or "").strip()
    if not eid:
        return 400, {"ok": False, "error": "id 必填"}
    ok, msg, ent = B.remove(eid, root=api.ROOT)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg, "count": B.count(api.ROOT)}


def handle_benchmark_restore(h, body):
    eid = str((body or {}).get("id") or "").strip()
    if not eid:
        return 400, {"ok": False, "error": "id 必填"}
    ok, msg, ent = B.restore(eid, root=api.ROOT)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg, "count": B.count(api.ROOT)}


ROUTES = [
    ("GET", "/judge/benchmark", handle_benchmark_list),
    ("POST", "/judge/benchmark/import", handle_benchmark_import),
    ("POST", "/judge/benchmark/remove", handle_benchmark_remove),
    ("POST", "/judge/benchmark/restore", handle_benchmark_restore),
]
