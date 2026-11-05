# -*- coding: utf-8 -*-
"""规划四态状态机（丙0）域模块。

语义：规划提案必须经用户确认（select）才能写进正文（written）。
AI 只能提案（propose），不能自选。

外部 Agent（gates.agent_mode=true）可读但不可调用 select/write/drop/revive
（规划决策权归用户）。只读端点（/stats, /list, /get, /audit）不禁用；
propose 允许（AI 只能提案，不能自选 —— 2026-10-16 修正本文档：此前误写
“propose 走 FORBIDDEN_IN_AGENT_MODE”，实码从来只禁四态转换）；
写入端点（/select, /write, /drop, /revive）走 FORBIDDEN_IN_AGENT_MODE。
CLI 侧同名四命令同受 utils/agent_guard.cli_approval_gate 守卫（P2，2026-10-16）。
"""
from pathlib import Path

import nf_api as api
from utils import agent_guard
from utils.plan_state import (
    propose, select, write, drop, revive, transition,
    list_items, get, stats, LABELS, VALID_STATUSES,
    STATUS_IDEA, STATUS_SELECTED, STATUS_WRITTEN, STATUS_DROPPED,
)


def _bad_request(msg):
    return 400, {"error": msg}


def _paths():
    """规划本的读写路径，**一律由 api.ROOT 派生**（2026-11-05）。

    此前各 handler 直接调 `propose(...)` / `list_items()` 不传 state_path，
    而 plan_state 的默认值是 `data/state/plan_state.json`（**cwd 相对**）——
    `--root <书B>` 且 cwd 不是书B 时，界面写进的是 A、读的是 B，且不报错。
    与 AGENTS 里“路径 IO 必须经 ROOT”同一条纪律。
    """
    from utils import plan_state as ps
    return str(api.ROOT / ps.PLAN_STATE), str(api.ROOT / ps.PLAN_AUDIT)


def handle_propose(h, body=None):
    """POST /plan/propose {title, summary, source?, kind?, context?}"""
    body = body if isinstance(body, dict) else {}
    title = str(body.get("title") or "").strip()
    summary = str(body.get("summary") or "")
    source = str(body.get("source") or "agent")
    kind = str(body.get("kind") or "proposal")
    context = body.get("context") or {}
    if not title:
        return _bad_request("title 不能为空")
    sp, ap = _paths()
    try:
        iid, entry = propose(title, summary, source=source, kind=kind,
                              context=context if isinstance(context, dict) else {},
                              state_path=sp, audit_path=ap)
        return 200, {"ok": True, "id": iid, "entry": entry}
    except ValueError as e:
        return _bad_request(str(e))


def handle_select(h, body=None):
    """POST /plan/select {id, reason?} → 用户专属。Agent 模式下禁用。"""
    body = body if isinstance(body, dict) else {}
    iid = str(body.get("id") or "").strip()
    reason = str(body.get("reason") or "")
    if not iid:
        return _bad_request("id 不能为空")
    sp, ap = _paths()
    ok, msg, entry = select(iid, operator="user", reason=reason,
                            state_path=sp, audit_path=ap)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg, "entry": entry}


def handle_write(h, body=None):
    """POST /plan/write {id, ref_path, reason?} → 用户专属。硬校验引用路径。"""
    body = body if isinstance(body, dict) else {}
    iid = str(body.get("id") or "").strip()
    ref_path = str(body.get("ref_path") or "").strip()
    reason = str(body.get("reason") or "")
    if not iid:
        return _bad_request("id 不能为空")
    sp, ap = _paths()
    ok, msg, entry = write(iid, ref_path, operator="user", reason=reason,
                           state_path=sp, audit_path=ap)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg, "entry": entry}


def handle_drop(h, body=None):
    """POST /plan/drop {id, reason?} → 用户专属。Agent 模式下禁用。"""
    body = body if isinstance(body, dict) else {}
    iid = str(body.get("id") or "").strip()
    reason = str(body.get("reason") or "")
    if not iid:
        return _bad_request("id 不能为空")
    sp, ap = _paths()
    ok, msg, entry = drop(iid, operator="user", reason=reason,
                          state_path=sp, audit_path=ap)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg, "entry": entry}


def handle_revive(h, body=None):
    """POST /plan/revive {id, reason?} → 用户专属。仅对 dropped 生效。"""
    body = body if isinstance(body, dict) else {}
    iid = str(body.get("id") or "").strip()
    reason = str(body.get("reason") or "")
    if not iid:
        return _bad_request("id 不能为空")
    sp, ap = _paths()
    ok, msg, entry = revive(iid, operator="user", reason=reason,
                            state_path=sp, audit_path=ap)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg, "entry": entry}


def handle_list(h):
    """GET /plan/list?status=<idea|selected|written|dropped>"""
    status = h._query().get("status", [None])[0]
    if status and status not in VALID_STATUSES:
        return _bad_request("status 必须是 %s 之一" % list(VALID_STATUSES))
    sp, _ap = _paths()
    items = list_items(status=status or None, state_path=sp)
    return 200, {"items": items, "count": len(items), "stats": stats(state_path=sp)}


def handle_get(h):
    """GET /plan/get?id=<idea_id>"""
    iid = h._query().get("id", [""])[0]
    sp, _ap = _paths()
    entry = get(iid, state_path=sp)
    if entry is None:
        return 404, {"error": "未找到: " + iid}
    return 200, entry


def handle_stats(h):
    """GET /plan/stats"""
    sp, _ap = _paths()
    return 200, {"stats": stats(state_path=sp),
                 "labels": {s: LABELS.get(s, s) for s in VALID_STATUSES}}


ROUTES = (
    ("POST", "/plan/propose", handle_propose),
    ("POST", "/plan/select", handle_select),
    ("POST", "/plan/write", handle_write),
    ("POST", "/plan/drop", handle_drop),
    ("POST", "/plan/revive", handle_revive),
    ("GET", "/plan/list", handle_list),
    ("GET", "/plan/get", handle_get),
    ("GET", "/plan/stats", handle_stats),
)
