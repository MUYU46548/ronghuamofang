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


def _act_ai_turn(cfg, chapter, topic, hint, stream_q, stop_ev, preview_job):
    """讨论 AI 轮的 job 体（跑在 `start_job` 的线程里）。

    三条与全局一致的行为，别在这里破例：
    - 流式推 token 到 SSE 队列：作者要看得见 AI 正在写什么（否则"闭嘴"按钮
      出现的时候，屏幕上什么也没有）；
    - `stop_flag` 直连 `stop_ev`：/stop 置位后逐块中断，延迟上限 ≈ 一个块；
    - 失败一律**不写讨论稿**：半截话进 `data/state/truncated/`
      （见 `discuss_ai.run_turn`）。
    """
    import queue as _queue

    def _fn():
        import nf_api as api
        from utils import discuss_ai

        client = api._client_for_env(cfg, "default")
        real = api.CURRENT.get("id", preview_job)
        with api.STREAMERS_LOCK:
            api.STREAMERS.pop(preview_job, None)
            api.STREAMERS[real] = {"queue": stream_q, "stop": stop_ev, "text": []}

        def on_chunk(piece):
            try:
                stream_q.put_nowait({"type": "token", "text": piece})
            except _queue.Full:
                pass

        def _mark(exit_code, stopped, model=""):
            with api.STREAMERS_LOCK:
                st = api.STREAMERS.get(real)
                if st is not None:
                    st["last_result"] = {"stopped": bool(stopped), "model": model,
                                         "cost_yuan": 0, "exit_code": exit_code}

        try:
            res = discuss_ai.run_turn(client, chapter, topic, hint,
                                      root=str(api.ROOT), cfg=cfg,
                                      on_chunk=on_chunk, stop_flag=stop_ev.is_set)
        except discuss_ai.DiscussAIError as e:
            stopped = e.kind == "stopped"
            _mark(4 if stopped else 1, stopped)
            # 被停下来要报 **ok**：`_notify_stream_done` 的判据是
            # "stopped and state==ok → done.status='stopped'"。若返回 False，
            # state 会变 failed，前端看到的是"失败"——把作者自己按的停止
            # 说成系统故障，是另一种撒谎。
            return (stopped, str(e) if stopped else "[%s] %s" % (e.kind, e))
        except Exception as e:                                 # noqa: BLE001
            _mark(1, False)
            return False, "[model_error] %s: %s" % (type(e).__name__, str(e)[:200])

        _mark(0, False, res.get("model") or "")
        v = res.get("verify") or {}
        tail = "" if res.get("streaming") else "（当前引擎整块返回，中途停不下来）"
        return True, ("AI 说了第 %d 轮（引文 %d 条，命中 %d）%s"
                      % (res.get("round") or 0, v.get("quotes") or 0,
                         v.get("verified") or 0, tail))

    return _fn


def handle_discuss_ai_turn(h, body):
    """`POST /discuss/ai_turn {chapter, topic, hint?}` —— **AI 说一轮**（2026-11-16）。

    返回 202 + job_id，前端接 `/stream/{job_id}` 看它边写边说、按 `/stop` 叫停。
    为什么必须异步：一次模型调用 30 秒到几分钟，同步返回会把界面卡死，
    而且中止按钮就没有落点了。

    谁可以发起：作者与外部 Agent 都可以（作者 2026-11-16 拍板：要让 AI 主动把
    讨论往某个方向带）。**停下来只有人能按** —— `/stop` 不受 agent_guard 拦。
    """
    import threading
    import time

    import nf_api as api
    from utils import discussion
    from utils.engine import resolve_engine

    body = body if isinstance(body, dict) else {}
    chapter = _int(body, "chapter")
    topic = str(body.get("topic") or "").strip()
    if not chapter or chapter < 1:
        return _bad("chapter 必须是正整数")
    if not topic:
        return _bad("topic 必填（这个讨论在讨论什么）")
    md, _js = discussion.thread_paths(chapter, topic, str(api.ROOT))
    if not md.exists():
        return 404, {"ok": False, "error": "没有这个讨论：%s" % md.as_posix()}

    cfg, _ = api.load_all()
    if resolve_engine(cfg) == "self":
        # D4：self 引擎下**绝不**发请求（make_client 会直接抛错）。这里给人话，
        # 而不是把 500 抛到界面上 —— 任务书那一步在 C13（第 2 批）。
        return 400, {"ok": False, "error_kind": "self_engine",
                     "error": "当前引擎是 self（只出任务书，不调用模型）——"
                              "讨论轮需要模型，请先切到 direct 或 hermes 引擎。"}

    hint = str(body.get("hint") or "")
    preview = time.strftime("%Y%m%d_%H%M%S_") + "pending"
    stream_q = api.queue.Queue(maxsize=10000)
    stop_ev = threading.Event()
    with api.STREAMERS_LOCK:
        api.STREAMERS[preview] = {"queue": stream_q, "stop": stop_ev, "text": []}

    jid, err = api.start_job(
        "discuss_ai",
        _act_ai_turn(cfg, chapter, topic, hint, stream_q, stop_ev, preview))
    if err:
        with api.STREAMERS_LOCK:
            api.STREAMERS.pop(preview, None)
        return 409, {"ok": False, "error": err}
    with api.STREAMERS_LOCK:
        if jid not in api.STREAMERS and preview in api.STREAMERS:
            api.STREAMERS[jid] = api.STREAMERS.pop(preview)
    return 202, {"ok": True, "job_id": jid, "chapter": chapter, "topic": topic,
                 "stream": True,
                 # hermes（agent）引擎的单轮调用是整块返回的 —— 没有流式接口，
                 # 按"让 AI 闭嘴"只能等它说完。这里如实告诉界面，别让它假装能停。
                 "stoppable": resolve_engine(cfg) != "hermes"}


ROUTES = (
    ("GET", "/discuss/list", handle_discuss_list),
    ("GET", "/discuss/read", handle_discuss_read),
    ("POST", "/discuss/open", handle_discuss_open),
    ("POST", "/discuss/say", handle_discuss_say),
    ("POST", "/discuss/ai_turn", handle_discuss_ai_turn),
    ("POST", "/discuss/decide", handle_discuss_decide),
)
