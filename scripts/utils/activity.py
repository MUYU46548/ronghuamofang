# -*- coding: utf-8 -*-
"""执行方在做哪一步 —— 给“过程黑盒”开一扇窗。

## 为什么需要它

作者原话：“踢一脚跑一下——AI 干活时你看不见它走到哪步、卡在哪。”
`progress.json` 只说阶段状态（pending / running / done），没有“**现在正在做什么**”，
更没有“多久没动静了”。执行方是 agent 时，界面就完全瞎了。

这里补一层**心跳**：执行方每做一步写一条，界面读它显示“当前动作 + 停了多久”。

## 三条纪律

1. **文件即状态**（`data/state/activity.json`），不引入内存态 —— 进程重启、换执行方都不丢。
2. **只留最近 50 条**：长跑一整天，文件也不能无限长。
3. **“卡住没卡住”由读方算**（`idle_seconds`），不信写入方自述 ——
   与其让执行方写“我还在跑”，不如让界面看“上次心跳是多少秒前”。

## 它是提示，不是闸门

心跳超时只表示“可能卡住了”，不阻断任何流程、不改阶段状态。
把“没心跳”当成失败会让离线跑批的人无辜被拦 —— 那比看不见更糟。
"""
import json
from datetime import datetime
from pathlib import Path

REL = "data/state/activity.json"

#: 超过这个秒数没有心跳 → 界面标黄（“多久没动静”）。只提示，不阻断。
IDLE_WARN_SECONDS = 180

#: 最近动作保留条数（够看“刚才在忙什么”，又不会把文件撑大）
RECENT_LIMIT = 50

#: 动作类型 → 人话。**唯一一份**：界面不再自己翻译（否则两处说法会漂）。
KIND_LABEL = {
    "read": "读资料",
    "write": "写产物",
    "check": "自检",
    "run": "跑阶段",
    "review": "审视",
    "wait": "等人",
    "done": "收尾",
}


def path(root):
    return Path(root) / REL


def load(root):
    p = path(root)
    if not p.exists():
        return {"current": None, "recent": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"current": None, "recent": []}
    if not isinstance(data, dict):
        return {"current": None, "recent": []}
    data.setdefault("current", None)
    data.setdefault("recent", [])
    return data


def beat(root, kind, target="", note="", at=None):
    """记一次心跳。返回写入的那条记录。

    `kind` 取 `KIND_LABEL` 的键；给别的值也不会报错（未知就原样显示），
    因为“执行方想多说一句”不该被一张词表卡住。
    """
    now = at or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = {
        "kind": str(kind or "").strip() or "run",
        "target": str(target or "").strip(),
        "note": str(note or "").strip(),
        "at": now,
    }
    data = load(root)
    data["current"] = entry
    recent = list(data.get("recent") or [])
    recent.append(entry)
    data["recent"] = recent[-RECENT_LIMIT:]
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry


def _idle_seconds(at_text, now=None):
    try:
        then = datetime.strptime(str(at_text), "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    now = now or datetime.now()
    return max(0, int((now - then).total_seconds()))


def status(root, now=None):
    """界面读的形态：当前动作 + 停了多久 + 是否该标黄。只读，不改文件。"""
    data = load(root)
    cur = data.get("current") or None
    if not cur:
        return {"ok": True, "current": None, "idle_seconds": None, "stale": False,
                "kind_label": "", "summary": "还没有执行方报过进度", "recent": []}
    idle = _idle_seconds(cur.get("at"), now=now)
    stale = idle is not None and idle >= IDLE_WARN_SECONDS
    kind = str(cur.get("kind") or "")
    label = KIND_LABEL.get(kind, kind)
    target = str(cur.get("target") or "")
    summary = (label + (" · " + target if target else "")).strip(" ·")
    return {
        "ok": True,
        "current": cur,
        "idle_seconds": idle,
        "stale": stale,
        "warn_seconds": IDLE_WARN_SECONDS,
        "kind_label": label,
        "summary": summary,
        "recent": (data.get("recent") or [])[-10:],
    }
