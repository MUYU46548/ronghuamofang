# -*- coding: utf-8 -*-
"""Agent 提问通道（2026-10-31）。

## 为什么要有它

外部 Agent 接进来之后，**没有任何方式向作者提问**：MCP 无 elicitation、无 ask 工具、
后端也没有"待回答问题"的落点。于是它想问只能写在回复正文里（作者得自己回），
或者干脆自己猜 —— 猜错了作者也不知道，因为界面上什么都没发生。

这条通道只做一件事：**Agent 提问 → 落在工作区 → 作者在界面上回答 → Agent 读到回答。**

## 三条纪律

1. **Agent 只能问，不能答**。`POST /ask/answer` 在 `FORBIDDEN_IN_AGENT_MODE` 里 ——
   能自问自答的通道等于没有通道，Agent 会自己编一个答案继续跑。
2. **问题落盘、不占内存**（`data/state/questions.json`）：应用重启后作者仍看得见。
3. **不催、不阻塞**。没有回答时 Agent 读到的是"还没有回答"，它该停下来等或被作者唤醒，
   而不是拿这个当"作者默认同意"。

## 数据形态

```json
{"items": [{"id": "q_20261031_1620_ab12", "at": "...", "question": "第三章让谁先动手？",
            "options": ["沈砚", "老周"], "context": "两种走向后面十章完全不同",
            "status": "pending", "answer": null, "answered_at": null}]}
```
`status` = pending / answered。只增不删（答过的问题也是记录）。
"""
import json
import time
import uuid

import nf_api as api

STATE_REL = "data/state/questions.json"
MAX_ITEMS = 200              # 上限：这是"待办"不是日志，塞满 200 条说明没人看
MAX_QUESTION = 500
MAX_ANSWER = 2000
MAX_OPTIONS = 6


def _state_file():
    return api.ROOT / STATE_REL


def _load():
    fp = _state_file()
    if not fp.is_file():
        return {"items": []}
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("items"), list):
            return {"items": []}
        return d
    except Exception:                                        # noqa: BLE001
        # 坏文件不抛：问题清单丢了只是不方便，不该让设置页/顶栏 500。
        return {"items": []}


def _save(doc):
    fp = _state_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    items = [x for x in (doc.get("items") or []) if isinstance(x, dict)]
    doc["items"] = items[-MAX_ITEMS:]
    fp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def handle_ask(h, body):
    """`POST /ask {question, options?, context?}` —— Agent 提问。"""
    try:
        body = body or {}
        q = str(body.get("question") or "").strip()
        if not q:
            return 400, {"ok": False, "error": "question 不能为空"}
        if len(q) > MAX_QUESTION:
            return 400, {"ok": False,
                         "error": "问题太长（%d 字 > %d）。要问得具体到能一句话回答。"
                                  % (len(q), MAX_QUESTION)}
        opts = body.get("options")
        if opts is not None:
            if not isinstance(opts, list) or not all(isinstance(x, (str, int, float))
                                                     for x in opts):
                return 400, {"ok": False, "error": "options 必须是字符串数组"}
            opts = [str(x).strip() for x in opts if str(x).strip()][:MAX_OPTIONS]
        doc = _load()
        item = {
            "id": "q_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4],
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "question": q,
            "options": opts or [],
            "context": str(body.get("context") or "").strip()[:1000],
            "status": "pending",
            "answer": None,
            "answered_at": None,
        }
        doc["items"].append(item)
        _save(doc)
        pending = sum(1 for x in doc["items"] if x.get("status") == "pending")
        return 200, {"ok": True, "id": item["id"], "pending": pending,
                     "message": "问题已记下（作者在界面上回答后可读 /ask/answers）"}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_ask_pending(h):
    """`GET /ask/pending` —— 还没回答的问题（界面轮询它）。"""
    try:
        doc = _load()
        items = [x for x in doc["items"] if x.get("status") == "pending"]
        return 200, {"ok": True, "items": items, "count": len(items)}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_ask_answers(h):
    """`GET /ask/answers` —— **已回答**的问题（Agent 读它）。

    只回已回答的：把 pending 也回给 Agent，它很容易把"没说"当成"没说不行"。
    """
    try:
        doc = _load()
        items = [x for x in doc["items"] if x.get("status") == "answered"]
        since = ""
        try:
            since = (h._query().get("since") or [""])[0]
        except Exception:                                    # noqa: BLE001
            since = ""
        if since:
            items = [x for x in items if str(x.get("answered_at") or "") >= since]
        return 200, {"ok": True, "items": items, "count": len(items),
                     "note": "只回已回答的问题；pending 不在此列（未回答 ≠ 同意）。"}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_ask_answer(h, body):
    """`POST /ask/answer {id, answer}` —— **作者**回答（GUI 用，Agent 禁用）。"""
    try:
        body = body or {}
        qid = str(body.get("id") or "").strip()
        ans = str(body.get("answer") or "").strip()
        if not qid:
            return 400, {"ok": False, "error": "id 必填"}
        if not ans:
            return 400, {"ok": False, "error": "answer 不能为空"}
        if len(ans) > MAX_ANSWER:
            return 400, {"ok": False, "error": "回答太长（上限 %d 字）" % MAX_ANSWER}
        doc = _load()
        for it in doc["items"]:
            if it.get("id") == qid:
                if it.get("status") == "answered":
                    return 400, {"ok": False, "error": "这条已经回答过了"}
                it["status"] = "answered"
                it["answer"] = ans
                it["answered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                _save(doc)
                return 200, {"ok": True, "item": it,
                             "message": "已回答（Agent 下次读 /ask/answers 就能看到）"}
        return 404, {"ok": False, "error": "没有这条问题：%s" % qid}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("POST", "/ask", handle_ask),
    ("GET", "/ask/pending", handle_ask_pending),
    ("GET", "/ask/answers", handle_ask_answers),
    ("POST", "/ask/answer", handle_ask_answer),
)
