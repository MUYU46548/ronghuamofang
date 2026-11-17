# -*- coding: utf-8 -*-
"""`/discuss/*`：讨论件（2026-11-03，作者判词"架构里没有这个构件"）。

端点（**数量勿写死**，以本文件 `ROUTES` 为唯一事实来源），全部只动 `data/discuss/`：

| 方法 | 路径 | 干什么 |
|---|---|---|
| GET  | `/discuss/list`   | 列出讨论（可按章）：状态 / 轮次 / 定稿时间 |
| GET  | `/discuss/read`   | 读一篇讨论全文 + 机读状态 |
| POST | `/discuss/open`   | 开一个讨论（章号 + 主题 + 待解决问题） |
| POST | `/discuss/say`    | 追加一轮发言（角色：author / ai） |
| POST | `/discuss/decide` | **作者拍板**：把讨论收敛成约束（写进写作提示词） |
| POST | `/discuss/ai_turn` | AI 说一轮（异步 + 流式 + 可中断，2026-11-16） |
| GET  | `/discuss/actions` | AI 说的"该改哪里"：建议清单 + 锚点体检（C5/C14） |
| POST | `/discuss/actions/apply` | 按建议**生成候选**（不直接改正文） |
| POST | `/discuss/actions/annotate` | 按建议**落成批注**（不调模型、不改正文；定位失败就拒） |
| POST | `/discuss/actions/dismiss` | 放弃一条建议（讨论稿与正文都不动） |
| POST | `/discuss/compress` | 把老轮次压成摘要（只动 session.json，C11） |

为什么定稿要单独一个端点：约束是写作阶段的**输入**，不是一条聊天记录。
`decide` 之后 `stage4` 会把它注入任务文件；定稿晚于正文时该章自动判待重写。

纪律：路径一律经 `api.ROOT`（`--root` 场景下不许读错项目）；写只落 `data/discuss/`。
"""
from __future__ import annotations

import time


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
    # C12（2026-11-16）：`role=author` 只有界面能写。
    # 为什么优先级高：讨论稿会被当**记忆**喂给之后所有轮次（会话体），
    # 一句外部伪造的"作者原话"会污染后续全部判断，而且不可追溯 ——
    # 作者本人也不会记得自己没说过那句话。
    # Agent 仍然可以 `role=ai`（作者 2026-11-16 拍板：要让 AI 主动带方向），
    # 只是不能冒充作者。
    if role == "author":
        from utils import agent_guard
        _code, _why = agent_guard.gui_refusal(h.headers)
        if _code is not None:
            return 403, {"ok": False, "code": _code,
                         "error": "以作者身份发言被拒：" + _why}
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
    hint = str(body.get("hint") or "")
    if resolve_engine(cfg) == "self":
        # D4 / C13：self 的承诺是"程序不调用任何模型"。讨论轮要模型，
        # 这条路径就退化成**任务书**（零 token）：
        #   第一次点 → 出任务书（202 + task 路径）
        #   执行方把 JSON 写到任务书第 3 节指定的位置
        #   第二次点 → 收编成正式一轮（200 + round）
        # 收编的校验与真跑**同一份**（parse_turn + verify_evidence），
        # 不许 preview 一套、真跑一套。
        from utils import discuss_ai
        try:
            settled = discuss_ai.settle_self(chapter, topic,
                                            root=str(api.ROOT), cfg=cfg)
        except discuss_ai.DiscussAIError as e:
            return 400, {"ok": False, "mode": "self", "error_kind": e.kind,
                         "error": str(e)}
        if settled:
            v = settled.get("verify") or {}
            return 200, {"ok": True, "mode": "self", "settled": True,
                         "round": settled.get("round"),
                         "message": "已把自任务写的那一轮收编成第 %d 轮（引文 %d 条，命中 %d）。"
                                    % (settled.get("round") or 0,
                                       v.get("quotes") or 0, v.get("verified") or 0)}
        try:
            task = discuss_ai.emit_self_task(chapter, topic, hint,
                                            root=str(api.ROOT), cfg=cfg)
        except discuss_ai.DiscussAIError as e:
            return 400, {"ok": False, "mode": "self", "error_kind": e.kind,
                         "error": str(e)}
        return 202, {"ok": True, "mode": "self", "task": task.as_posix(),
                     "message": "自任务模式：已出讨论任务书（没有调用模型）。",
                     "hint": "把任务书交给任意模型，产物写到它第 3 节指定的路径；"
                             "再点一次“让 AI 说这一轮”，墨坊会校验引文并收编成正式一轮。"}
    # C19（2026-11-16）：AI 主动发言开关（config 的 discuss.ai_proactive，默认开）。
    # 关掉的意思是"AI 不许自己找话题"——作者必须先写一句要它谈什么。
    # 为什么有这个开关：主动发言会把讨论往某个方向带（作者要的），
    # 但也可能在作者只想看现状时替他定了调；手滑点一下就是一次花钱的调用。
    proactive = True
    try:
        proactive = bool(((cfg or {}).get("discuss") or {}).get("ai_proactive", True))
    except Exception:                                          # noqa: BLE001
        proactive = True
    if not proactive and not hint.strip():
        return 400, {"ok": False, "error_kind": "ai_proactive_off",
                     "error": "AI 主动发言已关闭 —— 先写一句要它谈什么，"
                              "再让它说这一轮（设置 → 讨论）。"}
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
                 # 两条通道（direct / hermes）现在都有流式接口，都能中途掐断
                 # （hermes 走 `_post_chat_stream` → 杀子进程树）。字段保留是给
                 # 将来可能出现的"只能整块返回"的客户端用的：那时置 false，
                 # 界面就会明说"停不下来"，而不是给一个按不停的按钮。
                 "stoppable": True}


def handle_discuss_actions(h):
    """`GET /discuss/actions?chapter=3&topic=…&status=pending` —— AI 说的"该改哪里"。

    每条建议都带一次**锚点体检**（`anchor` 字段）：正文改过之后段号会漂，
    界面必须在作者点之前就知道"这一段还在不在原位"，而不是点了才报错。
    """
    import nf_api as api
    from utils import discuss_actions

    q = h._query()
    try:
        chapter = int((q.get("chapter") or ["0"])[0]) or None
    except (TypeError, ValueError):
        chapter = None
    topic = (q.get("topic") or [""])[0] or None
    status = (q.get("status") or ["pending"])[0] or None
    try:
        items = discuss_actions.list_suggestions(chapter, root=str(api.ROOT),
                                                status=status, topic=topic)
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    out = []
    for it in items:
        row = dict(it)
        try:
            row["anchor"] = discuss_actions.resolve_anchor(
                int(it.get("chapter") or chapter or 0), it.get("target"), str(api.ROOT))
        except Exception:                                      # noqa: BLE001
            row["anchor"] = {"ok": False, "why": "读不到这一章的正文，没法定位"}
        out.append(row)
    return 200, {"ok": True, "count": len(out), "items": out,
                 "note": "建议不会自己动手：点“按这条生成候选”才进候选区，正文要你点采用才换。"}


def handle_discuss_action_apply(h, body):
    """`POST /discuss/actions/apply {chapter, topic, id, confirm?}` —— 按建议生成候选。

    两条路，都不是"直接改正文"：

    - `refine_paragraph` / `suggest_edit` → 转给 `/chapters/paragraph/preview` 同一份实现：
      候选进 `data/state/para_pending.json`，正文一个字不动，作者再点"采用"。
    - `rewrite_chapter` → 转给 `/chapters/rewrite`（旧稿先备份到
      `data/chapters/rewrite_backup/`），必须显式 `confirm: true` —— 整章重写的代价
      与段落改一句不在一个量级。

    为什么这里不做 GUI 闸：外部 Agent 本来就能直接调 `/chapters/rewrite`，
    只在这一条路上加闸是自我安慰。真正的闸是"候选区要作者点采用"和
    `rewrite` 的 `confirm`，两处都保留。
    """
    import nf_api as api
    from utils import discuss_actions

    body = body if isinstance(body, dict) else {}
    chapter = _int(body, "chapter")
    topic = str(body.get("topic") or "").strip()
    sid = str(body.get("id") or "").strip()
    if not chapter or not topic or not sid:
        return _bad("chapter / topic / id 必填")
    sug = discuss_actions.get_suggestion(chapter, topic, sid, str(api.ROOT))
    if not sug:
        return 404, {"ok": False, "error": "找不到这条建议（可能已被处理）：%s" % sid}
    if str(sug.get("status")) == "dismissed":
        return 409, {"ok": False, "error": "这条建议已经被你放弃了"}
    anchor = discuss_actions.resolve_anchor(chapter, sug.get("target"), str(api.ROOT))
    if not anchor.get("ok"):
        # 不猜段号：宁可让作者重开一条讨论，也不把修改落到错的段落上。
        return 409, {"ok": False, "error": anchor.get("why") or "这一段已经不在原位",
                     "anchor": anchor, "suggestion": sug}

    kind = str(sug.get("kind") or "")
    feedback = str(sug.get("why") or "").strip()
    if not feedback:
        quote = str((sug.get("target") or {}).get("quote") or "").strip()
        feedback = ("按第 %s 轮讨论的判断改这一段%s"
                    % (sug.get("round") or "?", ("（讨论里引的是：“%s”）" % quote[:60]) if quote else ""))
    payload = dict(sug, anchor=anchor, feedback=feedback)

    try:
        if kind == "rewrite_chapter":
            if body.get("confirm") is not True:
                return 400, {"ok": False, "need_confirm": True,
                             "error": "整章重写会把旧稿换掉（先备份到 "
                                      "data/chapters/rewrite_backup/）。确认要重写第 %d 章，"
                                      "请带 confirm: true。" % chapter,
                             "suggestion": payload}
            from nf_api_domains import chapters as dom_chapters
            status, out = dom_chapters.handle_chapters_rewrite(h, {"n": chapter})
            if status in (200, 202):
                discuss_actions.set_status(chapter, topic, sid, "started",
                                           root=str(api.ROOT),
                                           job_id=(out or {}).get("job_id") or "",
                                           applied_at=time.strftime(
                                               "%Y-%m-%dT%H:%M:%S"))
            return status, dict(out or {}, suggestion=payload)
        # 段落级：走候选区（同一份实现，不在这里另写一套改写逻辑）
        from nf_api_domains import refine as dom_refine
        status, out = dom_refine.handle_paragraph_preview(h, {
            "chapter": chapter,
            "paragraph_index": int(anchor.get("index") or 0),
            "feedback": feedback,
        })
        if status in (200, 202):
            discuss_actions.set_status(chapter, topic, sid, "started",
                                       root=str(api.ROOT),
                                       job_id=(out or {}).get("job_id") or "",
                                       applied_at=time.strftime(
                                           "%Y-%m-%dT%H:%M:%S"),
                                       anchor_index=anchor.get("index"))
        return status, dict(out or {}, suggestion=payload)
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_discuss_action_annotate(h, body):
    """`POST /discuss/actions/annotate {chapter, topic, id}` —— 建议落成段落批注（正文不动）。

    讨论 → 批注 → 读改链（2026-11-17，讨论 P0）：以前讨论说完“这里该改”，
    只能当场花一次模型调用生成候选；作者想“先记下来，等读完这一章再改”没有出口。
    现在可以落成批注：带引用原文与段号（定位失败就拒，绝不猜），
    批注列表里点“改这一处”走同一条预览/采用链。
    """
    import nf_api as api
    from utils import discuss_actions

    body = body if isinstance(body, dict) else {}
    chapter = _int(body, "chapter")
    topic = str(body.get("topic") or "").strip()
    sid = str(body.get("id") or "").strip()
    if not chapter or not topic or not sid:
        return _bad("chapter / topic / id 必填")
    ok, msg, item, anchor = discuss_actions.annotate_suggestion(
        chapter, topic, sid, str(api.ROOT))
    if not ok:
        code = 409
        if msg.startswith("找不到"):
            code = 404
        return code, {"ok": False, "error": msg, "anchor": anchor}
    return 200, {"ok": True, "message": msg, "annotation": item, "anchor": anchor}


def handle_discuss_action_dismiss(h, body):
    """`POST /discuss/actions/dismiss {chapter, topic, id}` —— 放弃一条建议（正文不动）。"""
    import nf_api as api
    from utils import discuss_actions

    body = body if isinstance(body, dict) else {}
    chapter = _int(body, "chapter")
    topic = str(body.get("topic") or "").strip()
    sid = str(body.get("id") or "").strip()
    if not chapter or not topic or not sid:
        return _bad("chapter / topic / id 必填")
    hit = discuss_actions.set_status(chapter, topic, sid, "dismissed",
                                     root=str(api.ROOT),
                                     dismissed_at=time.strftime(
                                         "%Y-%m-%dT%H:%M:%S"))
    if not hit:
        return 404, {"ok": False, "error": "找不到这条建议：%s" % sid}
    return 200, {"ok": True, "message": "已放弃这条建议（讨论稿与正文都没动）",
                 "suggestion": hit}


def handle_discuss_compress(h, body):
    """`POST /discuss/compress {chapter, topic, keep?, force?}` —— 把老轮次压成摘要。

    只动 `session.json`：讨论稿 `md` 一个字不改。默认按配置阈值判断"够不够长"，
    `force: true` 是作者手动要求（仍然只会压到保留最近 `keep` 轮为止）。
    """
    import nf_api as api
    from utils import discussion

    body = body if isinstance(body, dict) else {}
    chapter = _int(body, "chapter")
    topic = str(body.get("topic") or "").strip()
    if not chapter or not topic:
        return _bad("chapter / topic 必填")
    md, _js = discussion.thread_paths(chapter, topic, str(api.ROOT))
    if not md.exists():
        return 404, {"ok": False, "error": "没有这个讨论：%s" % md.as_posix()}
    try:
        from utils import discuss_ai
        cfg, _ = api.load_all()
        keep = body.get("keep")
        res = discussion.compress_session(
            chapter, topic, root=str(api.ROOT),
            keep=int(keep) if keep is not None else discuss_ai.session_keep(cfg),
            threshold=discuss_ai.compress_after(cfg),
            force=bool(body.get("force")))
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    if not res:
        sess = discussion.load_session(chapter, topic, str(api.ROOT))
        return 200, {"ok": True, "compressed": False,
                     "message": "还没有到需要压缩的长度（现在 %d 轮）"
                                % len(sess.get("messages") or []),
                     "rounds": len(sess.get("messages") or [])}
    return 200, {"ok": True, "compressed": True, **res,
                 "message": "已把第 %s 轮压成摘要（讨论稿原文没动）"
                            % "、".join(str(x) for x in res.get("archived") or [])}


ROUTES = (
    ("GET", "/discuss/list", handle_discuss_list),
    ("GET", "/discuss/read", handle_discuss_read),
    ("POST", "/discuss/open", handle_discuss_open),
    ("POST", "/discuss/say", handle_discuss_say),
    ("POST", "/discuss/ai_turn", handle_discuss_ai_turn),
    ("GET", "/discuss/actions", handle_discuss_actions),
    ("POST", "/discuss/actions/apply", handle_discuss_action_apply),
    ("POST", "/discuss/actions/annotate", handle_discuss_action_annotate),
    ("POST", "/discuss/actions/dismiss", handle_discuss_action_dismiss),
    ("POST", "/discuss/compress", handle_discuss_compress),
    ("POST", "/discuss/decide", handle_discuss_decide),
)
