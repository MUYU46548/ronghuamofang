# -*- coding: utf-8 -*-
"""条目级讨论：素材 / 设定条目上的“先商量，再改”（零 token，写作前不注入）。

## 要治的病（试跑第 16 条）

作者原话：“想改素材，依然找不到任何讨论入口。我直接在 agent 里问了。
好在它懂得把裁决写进设定集（希望真的写了）。”

章节侧早有 `DiscussThreadPanel`（`data/discuss/` + `/discuss/*`），但**条目侧没有入口** ——
素材卡与设定条目只能“看一眼然后手改”，商量过程留在聊天里，等于没留。

## 口径（与章节讨论的区别，别混）

| | 章节讨论 | 条目讨论（本模块） |
|---|---|---|
| key | 章号 + 主题 | 类别 + 条目名（`characters:岑砚`） |
| 结论去向 | 该章写作提示词（`state.constraints`） | **不进章提示词**；定稿后由作者点“写回设定集”，走 `/setting/status` 改条目状态 |
| 状态文件 | `data/discuss/*.md` + 机读状态 | `data/state/entry_discuss.json` |

**为什么不进提示词**：一条设定该不该改，是创作决策；把它塞进每章提示词会让模型
擅自“按讨论结论改设定”。这里只保存商量过程与结论，写回由作者点。

## 判据

- key 必须是 `类别:条目名`，两类都非空；同名不同类别互不干扰
- 定稿至少要有一条 must / must_not / summary，否则不算结论（与章节讨论同口径）
- 每次写盘前备份旧版（`data/state/entry_discuss_history/`，留 10 份）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PATH_REL = Path("data") / "state" / "entry_discuss.json"
HISTORY_REL = Path("data") / "state" / "entry_discuss_history"
MAX_HISTORY = 10
KINDS = ("characters", "world", "concepts", "timeline", "materials")
KIND_LABEL = {"characters": "角色", "world": "世界观", "concepts": "概念",
              "timeline": "时间线", "materials": "素材"}


def _p(root, rel):
    return (Path(root) if root else Path(".")) / rel


def _key(kind, name):
    return "%s:%s" % (str(kind or "").strip(), str(name or "").strip())


def _load(root):
    p = _p(root, PATH_REL)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(root, data):
    p = _p(root, PATH_REL)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        h = _p(root, HISTORY_REL)
        h.mkdir(parents=True, exist_ok=True)
        try:
            (h / (time.strftime("%Y%m%d_%H%M%S") + ".json")).write_text(
                p.read_text(encoding="utf-8"), encoding="utf-8")
            for old in sorted(h.glob("*.json"))[:-MAX_HISTORY]:
                old.unlink(missing_ok=True)
        except OSError:
            pass
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _validate(kind, name):
    if not str(kind or "").strip():
        return "kind 必填（characters / world / concepts / timeline / materials）"
    if not str(name or "").strip():
        return "name 必填（条目名）"
    return ""


def get(root, kind, name):
    return _load(root).get(_key(kind, name))


def list_threads(root, kind=None, name=None):
    """返回 [thread...]（新的在前）。可按 kind / name 过滤。"""
    data = _load(root)
    out = []
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        if kind and str(v.get("kind")) != str(kind):
            continue
        if name and str(v.get("name")) != str(name):
            continue
        item = dict(v)
        item["key"] = k
        item["kind_label"] = KIND_LABEL.get(v.get("kind"), v.get("kind"))
        out.append(item)
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return out


def open_thread(root, kind, name, topic):
    err = _validate(kind, name)
    if err:
        return False, {"error": err}
    topic = str(topic or "").strip()
    if not topic:
        return False, {"error": "topic 必填（这条讨论要解决什么）"}
    data = _load(root)
    k = _key(kind, name)
    if k in data:
        return False, {"error": "这条已经开过讨论（换一个条目，或在那条里继续发言）",
                       "thread": data[k]}
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    th = {"kind": kind, "name": name, "topic": topic, "status": "open",
          "rounds": [], "decision": {}, "created": now, "updated": now}
    data[k] = th
    _save(root, data)
    return True, {"key": k, "thread": th}


def say(root, kind, name, role, text):
    err = _validate(kind, name)
    if err:
        return False, {"error": err}
    text = str(text or "").strip()
    if not text:
        return False, {"error": "text 必填"}
    if role not in ("ai", "author"):
        return False, {"error": "role 只能是 ai 或 author"}
    data = _load(root)
    k = _key(kind, name)
    th = data.get(k)
    if not th:
        return False, {"error": "这条还没有讨论（先 open）"}
    th["rounds"] = (th.get("rounds") or []) + [{
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "text": text}]
    th["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _save(root, data)
    return True, {"key": k, "rounds": len(th["rounds"])}


def decide(root, kind, name, decision):
    """作者定稿。至少一条 must / must_not / summary —— 空结论不算结论。"""
    err = _validate(kind, name)
    if err:
        return False, {"error": err}
    d = decision if isinstance(decision, dict) else {}
    clean = {}
    for key in ("must", "must_not", "summary"):
        v = str(d.get(key) or "").strip()
        if v:
            clean[key] = v
    if not clean:
        return False, {"error": "定稿至少写一条（must / must_not / summary）——"
                                "一条都没有不算结论"}
    data = _load(root)
    k = _key(kind, name)
    th = data.get(k)
    if not th:
        return False, {"error": "这条还没有讨论（先 open）"}
    th["decision"] = clean
    th["status"] = "decided"
    th["decided_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    th["updated"] = th["decided_at"]
    _save(root, data)
    return True, {"key": k, "decision": clean}


def summary_for(root, kind, name):
    """界面上挂一行“有讨论结论”用的短摘要（没有就 None）。"""
    th = get(root, kind, name)
    if not th:
        return None
    dec = th.get("decision") or {}
    return {"status": th.get("status"), "topic": th.get("topic"),
            "rounds": len(th.get("rounds") or []),
            "decision": dec,
            "text": (dec.get("summary") or dec.get("must") or dec.get("must_not") or "")[:120]}
