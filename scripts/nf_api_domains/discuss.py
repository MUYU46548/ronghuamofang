# -*- coding: utf-8 -*-
"""讨论落地域（2026-10-18 P0-1）：agent 讨论成果的提案/审批/队列端点。

## 端点与守卫分工

| 端点 | 方法 | Agent 可调用 | 说明 |
|------|------|--------------|------|
| `/outline/discussion/brief`   | GET  | ✅ | 打包“讨论第 N 章”的自包含任务书（只读，供复制给外部 Agent） |
| `/outline/discussion/apply`  | POST | ✅ | 提交提案 → 写草稿区 + 登记 pending（草稿类，自动待审） |
| `/outline/discussion/review` | POST | ❌ 进 `FORBIDDEN_IN_AGENT_MODE` | 批准（真正写回章大纲）/ 驳回 —— 审批族，与 /approve 同档 |
| `/outline/discussions`       | GET  | ✅ | 提案队列（GUI 审批页签消费） |
| `/outline/discussion/draft`  | GET  | ✅ | 提案全文预览（看 diff 的料） |

**红线**：agent 可提案、不可自批。讨论发生在哪（Hermes 简报投递 / GUI 单轮精修）
与本域无关 —— 产物都汇到同一队列。

## 为什么不用沙盒 manifest

沙盒是“贴进 vault”中转区，讨论提案是“改我的大纲”，目的地与生命周期都不同。
`utils/discuss` 用独立 `data/state/discuss_manifest.json`，判据仍复用
`sandbox_review` 的状态机实现（一份实现，两处 manifest）。
"""
import nf_api as api


def _bad(msg):
    return 400, {"ok": False, "error": msg}


def handle_discussion_apply(h, body):
    """POST /outline/discussion/apply —— 提交一份讨论提案（自动待审）。

    body: {target_n, title, body, rationale?, candidates?, chosen?, anchor?, rounds?}
    `body` 是要写回章大纲的优化点正文（不含 `## 【优化点N】` 标题行 ——
    编号由写回时按章内现状顺延，避免两个提案撞号）。
    """
    from utils import discuss

    body = body if isinstance(body, dict) else {}
    try:
        target_n = int(body.get("target_n") or 0)
    except (TypeError, ValueError):
        return _bad("target_n 必须是章号整数")
    ok, msg, rel = discuss.submit(
        target_n=target_n,
        title=str(body.get("title") or ""),
        body=str(body.get("body") or ""),
        rationale=str(body.get("rationale") or ""),
        candidates=[str(c) for c in (body.get("candidates") or [])],
        chosen=str(body.get("chosen") or ""),
        anchor=body.get("anchor") or {},
        rounds=body.get("rounds") or 0,
        source=str(body.get("source") or "agent 讨论"),
        root=str(api.ROOT),
    )
    if not ok:
        return _bad(msg if isinstance(msg, str) else str(msg))
    return 200, {"ok": True, "message": msg, "path": rel,
                 "queue": _queue_snapshot()}


def handle_discussion_review(h, body):
    """POST /outline/discussion/review —— 批准/驳回提案。

    body: {path, action: approve|reject, note?}
    approve 会**真正把优化点写回章大纲**（写前备份章文件），这是审批动作，
    所以整个端点在 FORBIDDEN_IN_AGENT_MODE 里。
    """
    from utils import discuss

    body = body if isinstance(body, dict) else {}
    rel = str(body.get("path") or "").replace("\\", "/")
    action = str(body.get("action") or "").strip().lower()
    note = str(body.get("note") or "")

    if not rel:
        return _bad("path 必填（提案相对路径，见 /outline/discussions）")
    if action == "approve":
        ok, msg = discuss.apply_proposal(rel, root=str(api.ROOT), reviewer=note)
        status = "approved" if ok else "pending"
    elif action == "reject":
        ok, msg = discuss.reject(rel, note, root=str(api.ROOT))
        status = "rejected" if ok else None
    else:
        return _bad("action 必须是 approve / reject")
    if not ok:
        return 400 if action == "reject" else 409, {"ok": False, "error": str(msg)}
    return 200, {"ok": True, "message": str(msg), "action": action,
                 "status": status, "queue": _queue_snapshot()}


def handle_discussions(h):
    """GET /outline/discussions?status= —— 提案队列（默认待审）。"""
    from utils import discuss

    q = h._query()
    status = str((q.get("status") or ["pending"])[0]).strip() or "pending"
    items = discuss.queue(status=status if status != "all" else None,
                          root=str(api.ROOT))
    return 200, {"ok": True, "items": items, "status": status,
                 "stats": _queue_stats()}


def handle_discussion_draft(h):
    """GET /outline/discussion/draft?path= —— 提案全文（GUI 预览/看 diff）。"""
    from utils import discuss

    q = h._query()
    rel = str((q.get("path") or [""])[0]).replace("\\", "/")
    text, err = discuss.draft_text(rel, root=str(api.ROOT))
    if err:
        return 400, {"ok": False, "error": err}
    return 200, {"ok": True, "path": rel, "content": text,
                 "chars": len(text)}


def handle_discussion_brief(h):
    """GET /outline/discussion/brief?n=<章号>&note=<可选意见> —— 打包讨论任务书。

    只读：读章大纲 / 设定集 / 上一章末尾 / 初稿 / 滚动摘要，拼成一份自包含任务书
    返回给 GUI 复制。**不写任何文件** —— 所以 Agent 也可调用（它只是在读）。

    `note` 是用户意见，可选；不给就让 Agent 自己先提可讨论的问题。
    """
    from utils import discuss_brief

    q = h._query()
    raw_n = str((q.get("n") or [""])[0]).strip()
    if not raw_n.isdigit():
        return _bad("n 必填且须为章号整数（如 ?n=3）")
    note = str((q.get("note") or [""])[0] or "")
    ok, msg, brief = discuss_brief.build_chapter_brief(
        int(raw_n), note=note, root=str(api.ROOT),
        project_root_label=str(api.ROOT))
    if not ok:
        return _bad(msg)
    return 200, {"ok": True, "message": msg, "n": int(raw_n),
                 "brief": brief, "chars": len(brief)}


def _queue_stats():
    from utils import discuss
    from utils import sandbox_review as srv
    return srv.stats(manifest_path=str(api.ROOT / discuss.MANIFEST))


def _queue_snapshot():
    """写操作后附带的队列快照（GUI 就地刷新计数，不必再补一次 GET）。"""
    from utils import discuss
    return {"items": discuss.queue(root=str(api.ROOT)), "stats": _queue_stats()}


# 域模块负责的端点（供自检与文档）。注意 review 是 POST 且在禁用名单里。
def handle_entry_discuss_list(h):
    """GET /discuss/entry?kind=&name= → 条目级讨论（不带 name 时列全部）。

    2026-11-05（试跑第 16 条）：素材/设定条目上原本没有任何讨论入口。
    """
    try:
        from utils import entry_discuss as ed
        q = h._query()
        kind = (q.get("kind") or [""])[0]
        name = (q.get("name") or [""])[0]
        items = ed.list_threads(api.ROOT, kind or None, name or None)
        return 200, {"ok": True, "items": items,
                     "note": "条目讨论的结论**不会**自动进章节写作提示词；"
                             "定稿后由作者点“写回设定集”（改条目状态）。"}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_entry_discuss_open(h, body):
    """POST /discuss/entry/open {kind, name, topic} → 开一条条目讨论。"""
    try:
        from utils import entry_discuss as ed
        ok, res = ed.open_thread(api.ROOT, body.get("kind"), body.get("name"),
                                 body.get("topic"))
        res["ok"] = bool(ok)
        return (200 if ok else 400), res
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_entry_discuss_say(h, body):
    """POST /discuss/entry/say {kind, name, role, text} → 追加一轮（只追加）。"""
    try:
        from utils import entry_discuss as ed
        ok, res = ed.say(api.ROOT, body.get("kind"), body.get("name"),
                         body.get("role"), body.get("text"))
        res["ok"] = bool(ok)
        return (200 if ok else 400), res
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_entry_discuss_decide(h, body):
    """POST /discuss/entry/decide {kind, name, must, must_not, summary} → 作者定稿。

    ⚠️ 这是**作者拍板**（与审批同族）：进 `FORBIDDEN_IN_AGENT_MODE` —— Agent 可以开题、
    可以发言，但不能替作者定稿。
    """
    try:
        from utils import entry_discuss as ed
        ok, res = ed.decide(api.ROOT, body.get("kind"), body.get("name"),
                            {"must": body.get("must"),
                             "must_not": body.get("must_not"),
                             "summary": body.get("summary")})
        res["ok"] = bool(ok)
        return (200 if ok else 400), res
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/discuss/entry", handle_entry_discuss_list),
    ("POST", "/discuss/entry/open", handle_entry_discuss_open),
    ("POST", "/discuss/entry/say", handle_entry_discuss_say),
    ("POST", "/discuss/entry/decide", handle_entry_discuss_decide),
    ("GET", "/outline/discussion/brief", handle_discussion_brief),
    ("POST", "/outline/discussion/apply", handle_discussion_apply),
    ("POST", "/outline/discussion/review", handle_discussion_review),
    ("GET", "/outline/discussions", handle_discussions),
    ("GET", "/outline/discussion/draft", handle_discussion_draft),
)
