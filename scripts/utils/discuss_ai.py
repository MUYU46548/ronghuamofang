# -*- coding: utf-8 -*-
"""讨论 AI 轮：让 AI 真的说一轮（C1/C2/C18，2026-11-16）。

## 为什么要有这个模块

`DiscussThreadPanel.vue` 上那个"以 AI 身份说"按钮，点下去是**作者自己替 AI 打字**：
`POST /discuss/say {role:"ai"}` 只是把一个字符串追加进 md。于是"讨论"退化成
作者对着一个文本框自己跟自己讲两遍。这个模块补的就是中间那一步。

## 三条设计约定（别自己改）

1. **一次触发 = 一次单发调用。** 不做常驻会话、不做自动连发 —— 与"用户踢一脚，
   AI 走一下"的全局约定一致（作者 2026-11-16 拍板）。多轮靠会话体续，
   不靠进程活着。
2. **流式 + 可中断。** 走 `client._post_chat_stream(..., stop_flag=)`：作者随时
   能按"让 AI 闭嘴"。停止延迟上限 ≈ 一个流块的到达间隔，已生成的 token 已经计费
   （界面上不许写"已取消，未扣费"）。
3. **半截不落盘。** 被停 / `finish_reason == "length"` / JSON 解析失败 → 都**不算一轮**，
   残片进 `data/state/truncated/`。讨论稿是会被当记忆喂后续所有轮次的，
   半句话留在里面等于让模型记住自己没说完的话（同 `AGENTS.md` 的产出契约）。

## 引文校验

`evidence` 里的 quote 必须**逐字**命中本章正文（去空白后比较；整句没命中就退一步
用前 12 字比对，容尾部截断）。一条都命中不了 → 整轮拒绝（`missing_quote`），
不许"看着像"的判断混进讨论稿。非 quote 类（setting/judge/outline）不比对原文，
但必须写清是哪一条。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from utils import discussion

SYSTEM_TEMPLATE = "discuss_ai.md"
MAX_OUTPUT_TOKENS = 1600          # 一轮发言的上限：再长就不是发言是正文了
BODY_CAP = 9000                   # 本章正文进背景的字数上限
QUOTE_FALLBACK_HEAD = 12          # 整句没命中时，用前 N 字再比一次
TRUNCATED_DIR = Path("data") / "state" / "truncated"
SELF_TASK_DIR = Path("data") / "state" / "tasks" / "self" / "discuss"

STANCES = ("push", "question", "concede", "summarize")
ACTION_KINDS = ("none", "suggest_edit", "rewrite_chapter", "refine_paragraph")
EVIDENCE_KINDS = ("quote", "setting", "judge", "outline")

#: 失败类型。**前端要按它说人话**，别拿异常堆栈给作者看。
ERROR_KINDS = ("no_chapter", "model_error", "stopped", "truncated",
               "bad_json", "bad_payload", "missing_quote", "no_evidence")


class DiscussAIError(Exception):
    """带 kind 的失败：kind 进 job 结果，前端按它给处置建议。"""

    def __init__(self, kind, msg, **extra):
        super().__init__(msg)
        self.kind = str(kind or "model_error")
        self.extra = extra


# ---------------------------------------------------------------- 提示词装配
def _strip_frontmatter(text: str) -> str:
    try:
        from utils.template_loader import FRONTMATTER_RE
        m = FRONTMATTER_RE.match(text or "")
        if m:
            return (text or "")[m.end():]
    except Exception:                                          # noqa: BLE001
        pass
    m = re.match(r"^---\s*\n.*?\n---\s*\n", text or "", re.S)
    return (text or "")[m.end():] if m else (text or "")


def system_prompt(root=".") -> str:
    """讨论轮的固定 system 段（逐字不变 —— 改了等于让前缀缓存失效）。"""
    for p in (Path(root) / "prompts" / SYSTEM_TEMPLATE, Path("prompts") / SYSTEM_TEMPLATE):
        if p.exists():
            try:
                return _strip_frontmatter(p.read_text(encoding="utf-8")).strip()
            except OSError:
                continue
    raise DiscussAIError("model_error", "提示词模板缺失：prompts/" + SYSTEM_TEMPLATE)


def chapter_body(chapter, root=".") -> tuple:
    """取这一章最接近成品的一层正文。返回 (text, layer)；没有正文返回 ("", "")。"""
    root = Path(root)
    for layer in ("refined", "checked", "raw"):
        p = root / "data" / "chapters" / layer / ("%02d.md" % int(chapter))
        if p.exists():
            try:
                return p.read_text(encoding="utf-8"), layer
            except OSError:
                continue
    return "", ""


def chapter_outline(chapter, root=".") -> str:
    p = Path(root) / "data" / "outline" / "chapters" / ("%02d.md" % int(chapter))
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        return ""


def _norm(s: str) -> str:
    """比较用归一：去空白（模型常把换行/空格改掉，那不算改原文）。"""
    return re.sub(r"\s+", "", str(s or ""))


def session_keep(cfg=None) -> int:
    v = ((cfg or {}).get("discuss") or {}).get("session_keep")
    try:
        return max(0, int(v)) if v is not None else discussion.SESSION_KEEP_DEFAULT
    except (TypeError, ValueError):
        return discussion.SESSION_KEEP_DEFAULT


def build_messages(chapter, topic, hint="", root=".", cfg=None):
    """装配一轮请求。返回 (messages, meta)。

    形状：固定 system → 本讨论背景 system → 历史（user/assistant）→ 本轮指令 user。
    背景用**第二条 system** 而不是 user：这样历史里第一条即使是作者发言，
    也不会出现"两条 user 连着"的歧义（哪句才是作者的话）。
    前缀缓存：前两条 system 在开题后逐字不变，历史只往后追加。
    """
    chapter = int(chapter)
    msgs = [{"role": "system", "content": system_prompt(root)}]

    bg = ["**当前讨论**：第 %d 章 · %s" % (chapter, topic or "未命名")]
    md, js = discussion.thread_paths(chapter, topic, root)
    state = discussion._read_json(js)
    if state.get("summary"):
        bg.append("**已有结论**：%s" % state.get("summary"))
    cons = discussion.render_for_prompt(chapter, root)
    if cons:
        bg.append(cons.replace("## 本章讨论结论（作者已拍板，必须落实）",
                               "**这一章已经拍板的约束**"))

    outline = chapter_outline(chapter, root)
    if outline.strip():
        bg.append("**本章大纲**：\n" + outline.strip()[:3000])

    body, layer = chapter_body(chapter, root)
    if body.strip():
        cut = body.strip()
        note = ""
        if len(cut) > BODY_CAP:
            cut = cut[:BODY_CAP]
            note = "\n（正文过长，只给了前 %d 字；后面的内容你引用不到就不要引）" % BODY_CAP
        bg.append("**本章正文（%s 层，引用必须逐字来自这里）**：\n"
                  "<<<本章正文开始>>>\n%s\n<<<本章正文结束>>>%s" % (layer, cut, note))
    else:
        bg.append("**本章还没有正文**。这一轮只能谈大纲与设定："
                  "不要写 `kind: quote` 的证据（没有原文可引），用 outline/setting/judge。")

    msgs.append({"role": "system", "content": "\n\n".join(bg)})

    hist, omitted = discussion.session_messages(chapter, topic, root,
                                               keep=session_keep(cfg))
    if omitted:
        msgs.append({"role": "system",
                     "content": "（更早的第 %s 轮没有带上；需要时看讨论稿原文，别猜。）"
                                % "、".join(str(x) for x in omitted[:20])})
    msgs.extend(hist)

    ask = "轮到你说这一轮。"
    if str(hint or "").strip():
        ask += "\n\n作者给你的提示：%s" % str(hint).strip()
    else:
        ask += "\n\n作者没有给提示 —— 你按上面讨论停下的地方接着往下说。"
    ask += "\n\n只输出 JSON 对象（字段见你的系统提示），不要解释文字。"
    msgs.append({"role": "user", "content": ask})
    return msgs, {"chapter": chapter, "topic": topic, "body_layer": layer,
                  "body_chars": len(body), "history": len(hist),
                  "omitted_rounds": omitted}


# ---------------------------------------------------------------- 解析与校验
def parse_turn(raw):
    """把模型输出解析成结构化的一轮。失败抛 DiscussAIError(bad_json/bad_payload)。"""
    text = str(raw or "").strip()
    if not text:
        raise DiscussAIError("bad_json", "模型返回了空内容")
    # 容忍 ```json 围栏与前后废话：取第一个 { 到最后一个 }
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise DiscussAIError("bad_json", "模型输出里没有 JSON 对象：%s" % text[:120])
        text = text[i:j + 1]
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as e:
        raise DiscussAIError("bad_json", "JSON 解析失败：%s" % str(e)[:120])
    if not isinstance(payload, dict):
        raise DiscussAIError("bad_payload", "JSON 顶层不是对象")

    body = str(payload.get("text") or "").strip()
    if not body:
        raise DiscussAIError("bad_payload", "缺少 text（这一轮没有要说的话）")
    stance = str(payload.get("stance") or "").strip().lower()
    if stance not in STANCES:
        raise DiscussAIError("bad_payload",
                            "stance 只能是 %s，收到 %r" % ("/".join(STANCES), stance))
    ev = payload.get("evidence")
    if not isinstance(ev, list) or not ev:
        raise DiscussAIError("no_evidence",
                             "evidence 为空 —— 没有引文/依据的判断不算一轮")
    clean_ev = []
    for i, item in enumerate(ev):
        if not isinstance(item, dict):
            raise DiscussAIError("bad_payload", "evidence[%d] 不是对象" % i)
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in EVIDENCE_KINDS:
            raise DiscussAIError("bad_payload",
                                 "evidence[%d].kind 只能是 %s，收到 %r"
                                 % (i, "/".join(EVIDENCE_KINDS), kind))
        quote = str(item.get("quote") or "").strip()
        if kind == "quote" and not quote:
            raise DiscussAIError("bad_payload", "evidence[%d] 是 quote 但没有引文" % i)
        clean_ev.append({"kind": kind, "quote": quote,
                         "why": str(item.get("why") or "").strip(),
                         "name": str(item.get("name") or "").strip()})
    action = payload.get("action") or {}
    if not isinstance(action, dict):
        raise DiscussAIError("bad_payload", "action 不是对象")
    kind = str(action.get("kind") or "none").strip().lower()
    if kind not in ACTION_KINDS:
        raise DiscussAIError("bad_payload",
                             "action.kind 只能是 %s，收到 %r" % ("/".join(ACTION_KINDS), kind))
    target = action.get("target") if isinstance(action.get("target"), dict) else {}
    return {"text": body, "stance": stance, "evidence": clean_ev,
            "decision": str(payload.get("decision") or "").strip(),
            "action": {"kind": kind, "target": target,
                       "why": str(action.get("why") or "").strip()},
            "needs_author": bool(payload.get("needs_author", True))}


def verify_evidence(payload, chapter, root="."):
    """校验引文能否命中原文。返回 (payload, report)。

    规则（与判定引擎"引句必须能命中原文"同源）：
    - quote 类：整句命中 → verified；否则用前 12 字命中 → verified（尾部截断容错）；
      都不命中 → 标 verified=False；
    - 有 quote 却**一条都没命中** → 抛 missing_quote（整轮拒绝）；
    - 本章没有正文时不比对，但此时不该出现 quote（解析阶段不拦，这里给 report 提示）。
    """
    body, layer = chapter_body(chapter, root)
    nb = _norm(body)
    total = 0
    hit = 0
    items = []
    for ev in payload.get("evidence") or []:
        if ev.get("kind") != "quote":
            items.append(dict(ev, verified=None))
            continue
        total += 1
        q = _norm(ev.get("quote"))
        ok = bool(nb) and (q in nb if q else False)
        if not ok and nb and len(q) >= QUOTE_FALLBACK_HEAD:
            ok = q[:QUOTE_FALLBACK_HEAD] in nb
        hit += 1 if ok else 0
        items.append(dict(ev, verified=bool(ok)))
    if total and hit == 0:
        raise DiscussAIError(
            "missing_quote",
            "这一轮引的 %d 条原文一条都没在本章正文里找到（正文层：%s）。"
            "引文必须逐字来自原文 —— 已丢弃这一轮，没有记进讨论。"
            % (total, layer or "无正文"),
            quotes=[ev.get("quote") for ev in items if ev.get("kind") == "quote"])
    payload = dict(payload, evidence=items)
    return payload, {"quotes": total, "verified": hit, "body_layer": layer}


# ---------------------------------------------------------------- 执行
def _render_messages_for_task(messages) -> str:
    """把 messages 摊成任务书里的可读段落（自任务模式给外部执行方看）。"""
    label = {"system": "【系统规则】", "user": "【作者 / 本轮指令】",
             "assistant": "【AI 上一轮】"}
    parts = []
    for m in messages or []:
        parts.append("%s\n\n%s" % (label.get(m.get("role"), m.get("role")),
                                   str(m.get("content") or "")))
    return "\n\n---\n\n".join(parts)


def emit_self_task(chapter, topic, hint="", root=".", cfg=None):
    """`engine: self` 下出讨论轮的任务书（零 token）。返回任务书路径。

    为什么要有（D4）：self 引擎的承诺是"程序不调用任何模型"。讨论轮要模型，
    这条路径就必须退化成**任务书**，而不是抛 500 或者偷偷按量跑。
    执行方（作者或外部 agent）把 JSON 写到 `<主题>.ai.json`，再点一次
    "让 AI 说这一轮"，`settle_self` 会把它收编成正式的一轮。
    """
    chapter = int(chapter)
    messages, meta = build_messages(chapter, topic, hint, root, cfg)
    out = discussion.ai_payload_path(chapter, topic, root)
    slug = re.sub(r"[\\/:*?\"<>|\s]+", "-", str(topic or "讨论")).strip("-.")[:40] or "讨论"
    p = Path(root) / SELF_TASK_DIR / ("discuss_%02d_%s.md" % (chapter, slug))
    body = [
        "# 自任务：第 %d 章讨论“%s”的 AI 发言" % (chapter, topic),
        "",
        "> 这是 `engine: self` 下生成的任务书 —— 墨坊**没有**调用任何模型。",
        "> 你把下面的请求交给任意模型执行，把它的 JSON 输出写到第 3 节指定的路径，",
        "> 再回到界面点一次“让 AI 说这一轮”，它就会被收编成正式的一轮。",
        "",
        "## 一、请求（原样交给模型）",
        "",
        _render_messages_for_task(messages),
        "",
        "## 二、输出要求",
        "",
        "只输出**一个 JSON 对象**（不要解释文字、不要 markdown 代码块），字段：",
        "",
        "```",
        '{',
        '  "text": "这一轮要说的话（作者会原样读到）",',
        '  "stance": "push | question | concede | summarize",',
        '  "evidence": [{"kind": "quote", "quote": "正文里逐字存在的句子", "why": "..."}],',
        '  "decision": "想让作者定的事（可空串）",',
        '  "action": {"kind": "none | suggest_edit | rewrite_chapter | refine_paragraph",',
        '             "target": {}, "why": ""},',
        '  "needs_author": true',
        '}',
        "```",
        "",
        "硬要求：`evidence` 至少一条；`kind` 为 quote 时，`quote` 必须**逐字**出现在",
        "上面的本章正文里；不要写 `round` 字段（服务端回填）。",
        "",
        "## 三、写到哪",
        "",
        "```",
        out.as_posix(),
        "```",
        "",
        "## 四、写完怎么放行",
        "",
        "回到“章节 → 讨论”，选中这个讨论，再点一次“让 AI 说这一轮”。",
        "墨坊会校验引文能不能命中原文 —— 一条都对不上就不会收编（并说清原因）。",
        "",
        "## 五、上下文备注",
        "",
        "- 正文层：%s（%d 字）" % (meta.get("body_layer") or "无正文", meta.get("body_chars") or 0),
        "- 带上的历史轮数：%s" % meta.get("history"),
        "- 省略的轮次：%s" % (meta.get("omitted_rounds") or "无"),
        "",
    ]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(body), encoding="utf-8")
    return p


def settle_self(chapter, topic, root=".", cfg=None):
    """把自任务模式下的 `<主题>.ai.json` 收编成正式一轮。

    返回结果 dict；没有待收编的产物返回 None；产物不合格抛 DiscussAIError
    （**校验口径与真跑同一份**：preview 与真跑不能各判各的）。
    """
    chapter = int(chapter)
    p = discussion.ai_payload_path(chapter, topic, root)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise DiscussAIError("bad_json", "任务书里写的结果不是合法 JSON（%s）：%s"
                             % (p.as_posix(), str(e)[:120]))
    if not isinstance(data, dict):
        raise DiscussAIError("bad_payload", "任务书里写的结果不是一个 JSON 对象：%s"
                             % p.as_posix())
    if data.get("settled"):
        return None
    payload = parse_turn(json.dumps(data, ensure_ascii=False))
    payload, report = verify_evidence(payload, chapter, root)
    n = discussion.append_round(chapter, topic, "ai", payload["text"], root=root)
    data.update({"settled": True, "round": n, "verify": report,
                 "settled_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    try:
        discussion._write_json(p, data)
    except Exception:                                          # noqa: BLE001
        print("[discuss_ai] ⚠ 收编后回写产物失败（讨论稿已落）：%s" % p.as_posix())
    return {"round": n, "text": payload["text"], "verify": report,
            "mode": "self", "settled": True}


# ---------------------------------------------------------------- 执行
def _truncated_path(chapter, topic, root=".") -> Path:
    stem = re.sub(r"[\\/:*?\"<>|\s]+", "-", str(topic or "讨论")).strip("-.")[:40] or "讨论"
    return Path(root) / TRUNCATED_DIR / ("discuss_%02d_%s_%s.txt"
                                         % (int(chapter), stem, time.strftime("%Y%m%d_%H%M%S")))


def save_truncated(chapter, topic, text, reason, root=".") -> Path:
    """残片隔离落盘：不算一轮，但绝不能静默丢掉（作者要能看见 AI 说到哪被打断）。"""
    p = _truncated_path(chapter, topic, root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# 讨论 AI 轮残片（%s）\n# 第 %d 章 · %s\n# %s\n\n%s\n"
                     % (reason, int(chapter), topic or "", time.strftime("%Y-%m-%dT%H:%M:%S"),
                        str(text or "")),
                     encoding="utf-8")
    except OSError:
        return p
    return p


def call_model(client, messages, on_chunk=None, stop_flag=None, max_tokens=None):
    """单发一次。返回 (text, usage, model, finish, streaming)。

    优先流式（可中断）；没有流式接口就整块回放（**不可中断**，如实告警）。
    `streaming=False` 会一路带到界面上 —— 那种引擎下按"让 AI 闭嘴"是按不停的，
    不说清楚就是骗人。
    """
    mt = int(max_tokens or MAX_OUTPUT_TOKENS)
    stream = getattr(client, "_post_chat_stream", None)
    if callable(stream):
        text, usage, model, finish = stream(messages, max_tokens=mt,
                                           on_chunk=on_chunk, stop_flag=stop_flag)
        return text, usage, model, finish, True
    print("[discuss_ai] WARN 该客户端没有流式接口 → 这一轮中途停不下来（会等它整块返回）")
    text, usage, model, finish = client._post_chat(messages, max_tokens=mt)
    if on_chunk and text:
        on_chunk(text)
    return text, usage, model, finish, False


def run_turn(client, chapter, topic, hint="", root=".", cfg=None,
             on_chunk=None, stop_flag=None):
    """跑一轮 AI 发言：装配 → 调用 → 解析 → 校验 → 落盘。返回结果 dict。

    失败一律抛 DiscussAIError（带 kind）；**任何失败都不写讨论稿**。
    """
    chapter = int(chapter)
    topic = str(topic or "").strip()
    if not topic:
        raise DiscussAIError("bad_payload", "topic 必填")
    md, _js = discussion.thread_paths(chapter, topic, root)
    if not md.exists():
        raise DiscussAIError("bad_payload", "没有这个讨论：%s" % md.as_posix())

    messages, meta = build_messages(chapter, topic, hint, root, cfg)
    if stop_flag and stop_flag():
        raise DiscussAIError("stopped", "还没开始就被停掉了")

    try:
        text, usage, model, finish, streaming = call_model(
            client, messages, on_chunk=on_chunk, stop_flag=stop_flag)
    except DiscussAIError:
        raise
    except Exception as e:                                     # noqa: BLE001
        raise DiscussAIError("model_error", "%s: %s" % (type(e).__name__, str(e)[:200]))

    if stop_flag and stop_flag():
        p = save_truncated(chapter, topic, text, "作者停下", root=root)
        raise DiscussAIError("stopped",
                             "这一轮被作者停下（已生成 %d 字，没有记进讨论；残片 %s）"
                             % (len(str(text or "")), p.as_posix()),
                             truncated_path=p.as_posix())
    if str(finish or "") == "length":
        p = save_truncated(chapter, topic, text, "被 max_tokens 截断", root=root)
        raise DiscussAIError("truncated",
                             "模型输出被 token 上限截断，这一轮没说完（残片 %s）"
                             % p.as_posix(), truncated_path=p.as_posix())

    payload = parse_turn(text)
    payload, report = verify_evidence(payload, chapter, root)

    n = discussion.append_round(chapter, topic, "ai", payload["text"], root=root)
    payload.update({"round": n, "model": model or "", "usage": usage or {},
                    "finish_reason": finish or "", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "verify": report})
    try:
        discussion._write_json(discussion.ai_payload_path(chapter, topic, root), payload)
    except Exception:                                          # noqa: BLE001
        # 结构化产物是"给程序消费"的，写不进去不该让这一轮失败（md 已落）
        print("[discuss_ai] ⚠ AI 轮结构化产物写入失败：%s"
              % discussion.ai_payload_path(chapter, topic, root))
    return {"round": n, "text": payload["text"], "stance": payload["stance"],
            "evidence": payload["evidence"], "action": payload["action"],
            "decision": payload["decision"], "model": payload["model"],
            "usage": payload["usage"], "verify": report, "meta": meta,
            "streaming": bool(streaming)}
