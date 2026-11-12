# -*- coding: utf-8 -*-
"""执行方进度（“过程可见”）的 API。

## 端点

| 端点 | 作用 | 谁可调 |
|---|---|---|
| `GET /activity` | 当前动作 + 停了多久 + 最近 10 条 | 读侧不拦 |
| `POST /activity/beat` | 执行方报一步：`{kind, target, note}` | **不拦**（这是记录，不是审批） |

## 为什么顺手也要给 agent 写

`FORBIDDEN_IN_AGENT_MODE` 是“作者动作”的名单（审批、裁定、签字）。报进度不在其中 ——
执行方（agent）看得见自己在干哪一步，正是作者要的“过程可见”。把它也拦掉，
界面就永远只有“已连接 / 离线”两个状态。
"""
import nf_api as api
from utils import activity as A


def handle_activity_get(h):
    return 200, A.status(api.ROOT)


def handle_activity_beat(h, body):
    body = body or {}
    kind = str(body.get("kind") or "").strip() or "run"
    entry = A.beat(api.ROOT, kind,
                   target=body.get("target") or "",
                   note=body.get("note") or "")
    return 200, {"ok": True, "entry": entry}


ROUTES = [
    ("GET", "/activity", handle_activity_get),
    ("POST", "/activity/beat", handle_activity_beat),
]
