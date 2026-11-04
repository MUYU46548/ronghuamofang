# -*- coding: utf-8 -*-
"""/discuss/*：讨论件（2026-11-03，作者判词"架构里没有这个构件"）。

五个端点，全部只动 `data/discuss/`：

| 方法 | 路径 | 干什么 |
|---|---|---|
| GET  | `/discuss/list`   | 列出讨论（可按章）：状态 / 轮次 / 定稿时间 |
| GET  | `/discuss/read`   | 读一篇讨论全文 + 机读状态 |
| POST | `/discuss/open`   | 开一个讨论（章号 + 主题 + 待解决问题） |
| POST | `/discuss/say`    | 追加一轮发言（角色：author / ai） |
| POST | `/discuss/decide` | **作者拍板**：把讨论收敛成约束（写进写作提示词） |

为什么定稿要单独一个端点：约束是写作阶段的**输入**，不是一条聊天记录。
`decide` 之后 `stage4` 会把它注入任务文件；定稿晚于正文时该章自动判待重写。

纪律：路径一律经 `api.ROOT`（`--root` 场景下不许读错项目）；写只落 `data/discuss/`。
"""
from __future__ import annotations


def _bad(msg, code=400):
    return code, {"ok": False, "error": str(msg)}


def _int(body, key, default=None):
    try:
        return int(body.get(key) if body.get(key) is not None else default)
    except (TypeError, ValueError):
        return default


def handle_discuss_list(h):
    """`GET /discuss/list?chapter=3` —— 讨论清单（零 token）。"""
    import nf_api as api
    from utils import discussion

    try:
        chapter = int((h._query().get("chapter") or ["0"])[0]) or None
    except (TypeError, ValueError):
        chapter = None
    items = discussion.list_threads(chapter, root=str(api.ROOT))
    return 200, {"ok": True, "count": len(items), "threads": items}


def handle_discuss_read(h):
    """`GET /discuss/read?chapter=3&topic=xxx` —— 一篇讨论的全文 + 状态。"""
    import nf_api as api
    from utils import discussion

    q = h._query()
    try:
        chapter = int((q.get("chapter") or ["0"])[0])
    except (TypeError, ValueError):
        return _bad("chapter 必须是章号整数")
    topic = (q.get("topic") or [""])[0]
    if not topic:
        return _bad("topic 必填（讨论主题）")
    md, js = discussion.thread_paths(chapter, topic, str(api.ROOT))
    if not md.exists():
        return 404, {"ok": False, "error": "没有这个讨论：%s" % md.as_posix()}
    from utils.file_io import read_text
    return 200, {"ok": True, "chapter": chapter, "topic": topic,
                 "path": md.as_posix(),
                 "state": discussion._read_json(js),
                 "text": read_text(md)}


def handle_discuss_open(h, body):
    """`POST /discuss/open {chapter, topic, question?}` —— 开一个讨论。"""
    import nf_api as api
    from utils import discussion

    body = body if isinstance(body, dict) else {}
    chapter = _int(body, "chapter")
    topic = str(body.get("topic") or "").strip()
    if not chapter or chapter < 1:
        return _bad("chapter 必须是正整数")
    if not topic:
        return _bad("topic 必填（这个讨论在讨论什么）")
    md, js = discussion.open_thread(chapter, topic,
                                    str(body.get("question") or ""), root=str(api.ROOT))
    return 200, {"ok": True, "chapter": chapter, "topic": topic,
                 "path": md.as_posix(),
                 "state": discussion._read_json(js),
                 "message": "讨论已开：%s" % md.as_posix()}


def handle_discuss_say(h, body):
    """`POST /discuss/say {chapter, topic, role, text}` —— 追加一轮（只追加）。"""
    import nf_api as api
    from utils import discussion

    body = body if isinstance(body, dict) else {}
    chapter = _int(body, "chapter")
    topic = str(body.get("topic") or "").strip()
    text = str(body.get("text") or "").strip()
    if not chapter or not topic:
        return _bad("chapter / topic 必填")
    if not text:
        return _bad("text 不能为空（说了一句空话不算一轮）")
    role = str(body.get("role") or "ai").strip().lower()
    if role not in ("author", "ai", "editor"):
        return _bad("role 只能是 author / ai / editor")
    n = discussion.append_round(chapter, topic, role, text, root=str(api.ROOT))
    return 200, {"ok": True, "chapter": chapter, "topic": topic, "round": n}


def handle_discuss_decide(h, body):
    """`POST /discuss/decide {...}` —— **作者拍板**，把讨论收敛成约束。

    与审批同族（决定"这一章按什么写"），因此 agent_mode 下按 GUI 来源闸门判定。
    """
    import nf_api as api
    from utils import agent_guard, discussion

    _code, _why = agent_guard.gui_refusal(h.headers)
    if _code is not None:
        # 三种情形分开说（2026-11-04）：桌面端没拿到令牌 / 令牌过期 / 真不是 GUI。
        # 原先一律用一句通用拒文 —— 作者本人点了也读它。
        return 403, {"ok": False, "code": _code, "error": "定稿（拍板）被拒：" + _why}

    body = body if isinstance(body, dict) else {}
    chapter = _int(body, "chapter")
    topic = str(body.get("topic") or "").strip()
    if not chapter or not topic:
        return _bad("chapter / topic 必填")

    def _list(key):
        v = body.get(key) or []
        if isinstance(v, str):
            v = [x for x in v.splitlines() if x.strip()]
        return [str(x) for x in v if str(x).strip()]

    ok, msg = discussion.decide(
        chapter, topic,
        must=_list("must"), must_not=_list("must_not"), voice=_list("voice"),
        foreshadow_plant=_list("foreshadow_plant"),
        foreshadow_payoff=_list("foreshadow_payoff"),
        summary=str(body.get("summary") or ""),
        root=str(api.ROOT), by="author")
    if not ok:
        return _bad(msg)
    return 200, {"ok": True, "message": msg,
                 "constraints": discussion.load_constraints(chapter, root=str(api.ROOT)),
                 "block_preview": discussion.render_for_prompt(chapter, root=str(api.ROOT))}


ROUTES = (
    ("GET", "/discuss/list", handle_discuss_list),
    ("GET", "/discuss/read", handle_discuss_read),
    ("POST", "/discuss/open", handle_discuss_open),
    ("POST", "/discuss/say", handle_discuss_say),
    ("POST", "/discuss/decide", handle_discuss_decide),
)
