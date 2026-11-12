# -*- coding: utf-8 -*-
"""提问通道：Agent 问 → 作者答（2026-10-31）；也是“要作者定的事”的唯一通道（2026-11-12 合并）。

## 为什么要有它

外部 Agent 接进来之后，**没有任何方式向作者提问**：MCP 无 elicitation、无 ask 工具、
后端也没有"待回答问题"的落点。于是它想问只能写在回复正文里（作者得自己回），
或者干脆自己猜 —— 猜错了作者也不知道，因为界面上什么都没发生。

这条通道只做一件事：**提问 → 落在工作区 → 作者在界面上回答 → Agent 读到回答。**

## 2026-11-12 合并（问题总账第 6 条）

原来判据只输出"过 / 不过"，没有"要问什么 / 选项 / 默认"，界面只能显示红绿灯。
那一层**没有另开一套端点**，而是并进这里 —— 同一个文件 `data/state/questions.json`、
同一条链路（提问 → 待答 → 作答 → Agent 读回）：

- 选项可以带 `value/label/note`（也可以仍是字符串数组，老调用不受影响）；
- 可带 `default`（不选也能往下走的那个）、`effect`（答了会做什么，如重写这一章）；
- 判据生成的问题由 `utils/ask.collect_structure_questions()` 写入，阶段 5 自动跑。

## 三条纪律

1. **Agent 只能问，不能答**。`POST /ask/answer` 在 `FORBIDDEN_IN_AGENT_MODE` 里 ——
   能自问自答的通道等于没有通道，Agent 会自己编一个答案继续跑。
2. **问题落盘、不占内存**（`data/state/questions.json`）：应用重启后作者仍看得见。
3. **不催、不阻塞**。没有回答时 Agent 读到的是"还没有回答"，它该停下来等或被作者唤醒，
   而不是拿这个当"作者默认同意"。

## 数据形态

```json
{"items": [{"id": "q_20261031_1620_ab12", "at": "...", "question": "第三章让谁先动手？",
            "context": "两种走向后面十章完全不同",
            "options": ["沈砚", "老周"], "default": "沈砚",
            "effect": "", "source": "manual", "status": "pending",
            "answer": null, "answered_at": null}]}
```
`options` 也可以是 `[{"value": "rewrite", "label": "重写这一章", "note": "…"}]`。
`status` = pending / answered。只增不删（答过的也是记录，最多留 200 条）。
"""
import nf_api as api
from utils import ask

MAX_QUESTION = 500
MAX_ANSWER = 2000
MAX_OPTIONS = 6


def _clean_options(raw):
    """选项清洗：字符串数组与对象数组都收。返回 (清洗后, 错误)。"""
    if raw is None:
        return None, ""
    if not isinstance(raw, list):
        return None, "options 必须是数组（字符串或 {value,label,note}）"
    out = []
    for o in raw[:MAX_OPTIONS]:
        if isinstance(o, dict):
            value = str(o.get("value") or "").strip()
            if not value:
                return None, "每个选项都要有 value"
            out.append({"value": value,
                        "label": str(o.get("label") or value),
                        "note": str(o.get("note") or "")})
        elif isinstance(o, (str, int, float)):
            v = str(o).strip()
            if v:
                out.append(v)
        else:
            return None, "选项只能是字符串或 {value,label,note}"
    return out, ""


def handle_ask(h, body):
    """`POST /ask {question, options?, context?, default?, effect?, id?, stage?, chapter?}`

    Agent 提问（也是判据提问的入口）。**问不等于定** —— 所以这里不设禁用。
    """
    try:
        body = body or {}
        q = str(body.get("question") or "").strip()
        if not q:
            return 400, {"ok": False, "error": "question 不能为空"}
        if len(q) > MAX_QUESTION:
            return 400, {"ok": False,
                         "error": "问题太长（%d 字 > %d）。要问得具体到能一句话回答。"
                                  % (len(q), MAX_QUESTION)}
        opts, err = _clean_options(body.get("options"))
        if err:
            return 400, {"ok": False, "error": err}
        if opts and body.get("default"):
            vals = [o["value"] if isinstance(o, dict) else o for o in opts]
            if str(body["default"]) not in vals:
                return 400, {"ok": False,
                             "error": "default 必须是选项之一：%s" % "、".join(vals)}
        qid, changed = ask.upsert(api.ROOT, {
            "id": body.get("id"),
            "question": q,
            "options": opts,
            "context": str(body.get("context") or "").strip()[:1000],
            "default": body.get("default"),
            "effect": str(body.get("effect") or ""),
            "source": str(body.get("source") or "agent"),
            "stage": body.get("stage"),
            "chapter": body.get("chapter"),
        }, by=str(body.get("by") or "api"))
        pending = len(ask.open_items(api.ROOT))
        return 200, {"ok": True, "id": qid, "pending": pending, "created": bool(changed),
                     "message": ("问题已记下（作者在界面上回答后可读 /ask/answers）"
                                 if changed else "这个问题已经在清单里了，等作者回答")}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_ask_pending(h):
    """`GET /ask/pending` —— 还没回答的问题（界面轮询它）。"""
    try:
        items = ask.open_items(api.ROOT)
        return 200, {"ok": True, "items": items, "count": len(items)}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_ask_answers(h):
    """`GET /ask/answers` —— **已回答**的问题（Agent 读它）。

    只回已回答的：把 pending 也回给 Agent，它很容易把"没说"当成"没说不行"。
    """
    try:
        since = ""
        try:
            since = (h._query().get("since") or [""])[0]
        except Exception:                                    # noqa: BLE001
            since = ""
        items = ask.answered_items(api.ROOT, since=since)
        return 200, {"ok": True, "items": items, "count": len(items),
                     "note": "只回已回答的问题；pending 不在此列（未回答 ≠ 同意）。"}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_ask_answer(h, body):
    """`POST /ask/answer {id, answer}` 或 `{id, value}` —— **作者**回答（GUI 用，Agent 禁用）。

    `value` 给的是选项的 value（界面点按钮走这条）；`answer` 是自由文本（老通道）。
    带 `effect` 的题可以改主意（作者反悔要能撤销），纯提问仍然一次性。
    """
    try:
        body = body or {}
        qid = str(body.get("id") or "").strip()
        ans = str(body.get("answer") or "").strip()
        value = str(body.get("value") or "").strip()
        if not qid:
            return 400, {"ok": False, "error": "id 必填"}
        if not ans and not value:
            return 400, {"ok": False, "error": "answer 不能为空"}
        if len(ans) > MAX_ANSWER:
            return 400, {"ok": False, "error": "回答太长（上限 %d 字）" % MAX_ANSWER}
        ok, msg, item = ask.answer(api.ROOT, qid, answer_text=ans, value=value,
                                   by="gui", note=str(body.get("note") or ""))
        if not ok:
            code = 404 if "没有这条问题" in msg else 400
            return code, {"ok": False, "error": msg}
        return 200, {"ok": True, "item": item, "message": msg,
                     "effect": (item or {}).get("effect") or "",
                     "counts": ask.summary(api.ROOT)}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("POST", "/ask", handle_ask),
    ("GET", "/ask/pending", handle_ask_pending),
    ("GET", "/ask/answers", handle_ask_answers),
    ("POST", "/ask/answer", handle_ask_answer),
)
