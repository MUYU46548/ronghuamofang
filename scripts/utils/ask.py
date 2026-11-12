# -*- coding: utf-8 -*-
"""要作者定的事（question）：判据只给结论，这里给选项。

## 为什么要有这一层

用户 2026-11-11 的判词："判据输出只有结论，无'要问作者什么/选项/默认'。
界面因此只能显示通过或不通过。"

决定 ②（界面决定、对话干活）要落地，界面就得是**作答位**：一句话问清、几个选项能点、
默认值写明白。红绿灯不是作答位。

## 唯一落点

`data/state/questions.json` —— **与 2026-10-31 的 Agent 提问通道同一个文件、同一个形状**
（`{"items": [...]}`）。刻意不另开一份：两个写点写同一个文件会互相覆盖，而"两条通道"
最后一定是老的没人答、新的没人看。

一条问题就是一个可读的字典：

```
{id, at, question, context, options[字符串 或 {value,label,note}], default,
 effect, source, stage, chapter, status(pending/answered), answer, answered_at/by}
```

两条纪律（与产物闸同源）：

- **id 稳定**：判据反复生成同一条问题是 upsert，不刷屏；没有 id 的（agent 顺手一问）
  按时间戳生成，与旧通道一致。
- **答案绑内容**：题干 / 选项 / 默认值 / 效果变了 → 旧答案作废并重开。拿旧答案套新问题，
  和拿旧批准套新稿是同一类错。

## 答了会怎样（`effect`）

- `rewrite_chapter` + 选到 `rewrite` → 该章进 `data/state/rewrite_queue.json`，
  下次阶段 4 把它当"待写"重出任务书；新版本被批准合成后自动出队
  （见 `stage4_writing._promote_chapter_candidates`）。选到 `keep` 则出队。
- 其余情况只记录（v1 边界，见 `docs/问题总账.md` 第 6 条）。
"""
import hashlib
import json
import time
import uuid
from pathlib import Path

STATE_REL = "data/state/questions.json"
QUEUE_REL = "data/state/rewrite_queue.json"

#: 上限：这是"待办"不是日志，塞满 200 条说明没人看。
MAX_ITEMS = 200

#: 一条问题的默认选项（给"只问一句"的调用方用，免得每个调用方自己编）。
DEFAULT_OPTIONS = [
    {"value": "yes", "label": "行", "note": ""},
    {"value": "no", "label": "不行", "note": ""},
]


def state_path(root):
    return Path(root) / STATE_REL


def queue_path(root):
    return Path(root) / QUEUE_REL


def _read_json(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load(root):
    """读整份清单。坏文件当空清单 —— 问题丢了只是不方便，不该让界面 500。"""
    doc = _read_json(state_path(root), {"items": []})
    items = [x for x in (doc.get("items") or []) if isinstance(x, dict)]
    return {"items": items}


def _save(root, doc):
    items = [x for x in (doc.get("items") or []) if isinstance(x, dict)]
    _write_json(state_path(root), {"items": items[-MAX_ITEMS:]})


def _sig(item):
    payload = json.dumps([item.get("question") or "",
                          item.get("context") or "",
                          item.get("options") or [],
                          item.get("default") or "",
                          item.get("effect") or ""],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _new_id():
    return "q_%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:4])


def _option_values(item):
    """该题可选的 value（选项既可能是字符串，也可能是 {value,label,note}）。"""
    out = []
    for o in (item.get("options") or []):
        v = str((o.get("value") if isinstance(o, dict) else o) or "").strip()
        if v:
            out.append(v)
    return out


def upsert(root, item, by="system"):
    """登记一条问题（幂等）。返回 `(id, changed)`。

    已答过且内容没变 → 原样保留；内容变了 → 重开（答案清空）。
    """
    item = dict(item or {})
    qid = str(item.get("id") or "").strip() or _new_id()
    question = str(item.get("question") or "").strip()
    if not question:
        return qid, False
    options = item.get("options") if item.get("options") else list(DEFAULT_OPTIONS)
    if not isinstance(options, list):
        return qid, False
    payload = {
        "id": qid,
        "question": question,
        "context": str(item.get("context") or ""),
        "options": options,
        "default": str(item.get("default") or "").strip(),
        "effect": str(item.get("effect") or ""),
        "source": str(item.get("source") or "manual"),
        "stage": item.get("stage"),
        "chapter": item.get("chapter"),
    }
    if not payload["default"]:
        vals = _option_values(payload)
        payload["default"] = vals[0] if vals else ""
    sig = _sig(payload)
    doc = load(root)
    prev = next((x for x in doc["items"] if x.get("id") == qid), None)
    if prev and prev.get("sig") == sig and prev.get("status") in ("pending", "answered"):
        return qid, False          # 同一件事、没变过：不重开、不刷屏
    base = dict(prev or {})
    if base.get("status") == "answered":
        # 内容变了：旧答案作废（不然就是"拿旧答案套新问题"）
        base = {"id": qid}
    base.update(payload)
    base.update({
        "sig": sig,
        "status": "pending",
        "answer": None,
        "answered_at": None,
        "answered_by": None,
        "at": base.get("at") or _now(),
        "asked_by": base.get("asked_by") or str(by),
    })
    if prev:
        doc["items"] = [base if x.get("id") == qid else x for x in doc["items"]]
    else:
        doc["items"].append(base)
    _save(root, doc)
    return qid, True


def open_items(root):
    return [x for x in load(root)["items"] if x.get("status") == "pending"]


def answered_items(root, since=""):
    items = [x for x in load(root)["items"] if x.get("status") == "answered"]
    if since:
        items = [x for x in items if str(x.get("answered_at") or "") >= since]
    return items


def find(root, qid):
    return next((x for x in load(root)["items"] if x.get("id") == qid), None)


def answer(root, qid, answer_text=None, value=None, by="gui", note=""):
    """作者作答。返回 `(ok, msg, item)`。

    - `value`：选了哪个选项（给了选项的题用这个，避免"答了一句话但不知道算哪个"）。
    - `answer_text`：自由回答（老通道的用法，保留）。

    **可以改主意**，但只有带 `effect` 的题（决定型）允许改；纯提问（agent 问一句）
    仍是一次性的 —— 否则 agent 读到的"回答"可能已经被改过三遍。
    """
    qid = str(qid or "").strip()
    value = str(value or "").strip()
    text = str(answer_text or "").strip()
    if not qid:
        return False, "id 必填（要答哪一条）", None
    item = find(root, qid)
    if not item:
        return False, "没有这条问题：%s" % qid, None
    if item.get("status") == "answered" and not item.get("effect"):
        return False, "这条已经回答过了", item
    opts = _option_values(item)
    if value:
        if opts and value not in opts:
            return False, "这个选项不在本题里：%s（可选：%s）" % (value, "、".join(opts)), item
        text = value
    if not text:
        return False, "answer 不能为空", item
    doc = load(root)
    for it in doc["items"]:
        if it.get("id") != qid:
            continue
        it["status"] = "answered"
        it["answer"] = text
        it["answered_at"] = _now()
        it["answered_by"] = str(by)
        if note:
            it["note"] = str(note)
        break
    _save(root, doc)
    item = find(root, qid) or item
    _apply_effect(root, item, text)
    return True, "已回答（Agent 下次读 /ask/answers 就能看到）", item


def _apply_effect(root, item, chosen):
    """把答案变成动作（目前只有"重写这一章"这一种）。"""
    if item.get("effect") != "rewrite_chapter":
        return
    try:
        chapter = int(item.get("chapter") or 0)
    except (TypeError, ValueError):
        return
    if chapter <= 0:
        return
    chosen = str(chosen or "").strip()
    if chosen == "rewrite" or chosen.startswith("重写"):
        add_rewrite(root, chapter, reason=item.get("question") or "")
    elif chosen in ("keep", "later") or chosen.startswith("保留") or chosen.startswith("先放"):
        clear_rewrites(root, [chapter])


def summary(root):
    items = load(root)["items"]
    open_n = len([x for x in items if x.get("status") == "pending"])
    return {"open": open_n, "answered": len(items) - open_n, "rewrites": queue_rewrites(root)}


# ---------------------------------------------------------------- 重写队列

def queue_rewrites(root):
    data = _read_json(queue_path(root), {"chapters": []})
    out = []
    for n in data.get("chapters") or []:
        try:
            v = int(n)
        except (TypeError, ValueError):
            continue
        if v > 0 and v not in out:
            out.append(v)
    return sorted(out)


def add_rewrite(root, chapter, reason=""):
    chapter = int(chapter)
    data = _read_json(queue_path(root), {"chapters": []})
    chs = [int(n) for n in (data.get("chapters") or []) if str(n).strip().isdigit()]
    if chapter not in chs:
        chs.append(chapter)
    data["chapters"] = sorted(set(chs))
    data["updated_at"] = _now()
    if reason:
        reasons = data.get("reasons") or {}
        reasons[str(chapter)] = str(reason)[:200]
        data["reasons"] = reasons
    _write_json(queue_path(root), data)


def clear_rewrites(root, chapters):
    """出队（合成新版本后调用）。队列空了就删文件，别留一个空壳让读方猜。"""
    p = queue_path(root)
    if not p.exists():
        return
    data = _read_json(p, {"chapters": []})
    drop = {int(n) for n in chapters}
    keep = [int(n) for n in (data.get("chapters") or [])
            if str(n).strip().isdigit() and int(n) not in drop]
    if not keep:
        try:
            p.unlink()
        except OSError:
            pass
        return
    data["chapters"] = sorted(set(keep))
    reasons = data.get("reasons") or {}
    for n in drop:
        reasons.pop(str(n), None)
    data["reasons"] = reasons
    data["updated_at"] = _now()
    _write_json(p, data)


# ---------------------------------------------------------------- 判据 → 问题

def _fmt(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return ("%.3f" % f).rstrip("0").rstrip(".")


def collect_structure_questions(root, result=None):
    """把"结构指标越线"变成具体问题（零 token）。

    `result` 可传 `utils.judge.structure.collect()` 的返回值；不传就读
    `data/review/structure.json`（阶段 5 落盘的那份）。都没有 → 什么都不生成。

    为什么问"这一章怎么办"而不是给个分数：结构指标只提示要不要人工抽样，
    它**判不了**这一章写得好不好 —— 那是作者的事。给选项，不给结论。
    """
    if result is None:
        p = Path(root) / "data" / "review" / "structure.json"
        if not p.is_file():
            return 0, 0
        try:
            result = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return 0, 0
        if not isinstance(result, dict) or not result.get("ok"):
            return 0, 0
    added = skipped = 0
    for ch in (result.get("chapters") or []):
        if not isinstance(ch, dict):
            continue
        try:
            no = int(ch.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        trig = ch.get("triggered") or []
        over = bool(ch.get("over_dialogue_max"))
        if not trig and not over:
            continue
        bits = []
        for t in trig:
            if not isinstance(t, dict):
                continue
            bits.append("%s %s（阈值 %s）" % (t.get("label") or t.get("code"),
                                              _fmt(t.get("value")), _fmt(t.get("limit"))))
        if over:
            bits.append("对话段占比 %s 超过上限 %s"
                        % (_fmt((ch.get("metrics") or {}).get("dialogue_cover")),
                           _fmt(ch.get("dialogue_max"))))
        qid, changed = upsert(root, {
            "id": "structure:ch%02d" % no,
            "stage": 4,
            "chapter": no,
            "question": "第 %d 章有 %d 项结构指标越线 —— 这一章怎么办？" % (no, len(bits)),
            "context": "；".join(bits) + "。阈值来自口径表 J16–J21：这些只提示人工抽样，"
                                        "不判文学质量。",
            "options": [
                {"value": "rewrite", "label": "重写这一章",
                 "note": "加入待重写清单，下次写作阶段重出这一章的任务书"},
                {"value": "keep", "label": "保留不改",
                 "note": "记下，不动稿子"},
                {"value": "later", "label": "先放着",
                 "note": "留在面板里，之后再说"},
            ],
            "default": "keep",
            "effect": "rewrite_chapter",
            "source": "structure",
        }, by="system")
        if changed:
            added += 1
        else:
            skipped += 1
    return added, skipped
