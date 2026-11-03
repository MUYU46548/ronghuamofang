# -*- coding: utf-8 -*-
"""“写完这一章就停”标志（2026-11-02，一期 F04 真暂停）。

## 要治的病

界面上原来只有一个“中断”：它**立刻**掐断正在生成的那一章。想"把手上的活干完再停"
只能掐时间点，或者干脆不停。用户原话的痛点就是"停一次的代价不可控"。

## 与“中断”的区别（这是两个动作，不是一个）

| | 什么时候停 | 正在写的这一章 |
|---|---|---|
| 中断（`/stop`） | 立刻 | 半截存成残稿 |
| **写完这章就停**（本标志） | 本章写完、入稿、标记完成之后 | **完整保留** |

所以它不是"慢一点的停止"，而是"在断点上下车"——下次点继续，从下一章接着写。

## 为什么用文件而不是内存

`/stop` 用进程内 `should_stop()`，只在 GUI 起 job 时有效；命令行直跑 orchestrator
时根本收不到。落盘标志两个场景都认。代价是要人工清（`consume()` 用完即删）。
"""
import json
import time
from pathlib import Path

FLAG_REL = Path("data") / "state" / "finish_chapter.flag"


def path(root=None):
    return (Path(root) / FLAG_REL) if root else FLAG_REL


def request(root=None, note=""):
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                             "note": str(note or "")}, ensure_ascii=False),
                 encoding="utf-8")
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": str(note or "")}


def is_requested(root=None):
    try:
        return path(root).is_file()
    except OSError:
        return False


def clear(root=None):
    try:
        path(root).unlink(missing_ok=True)
    except OSError:
        pass


def consume(root=None):
    """读一次并清掉（谁停的谁负责清，避免下一轮又被立刻停住）。"""
    data = None
    try:
        p = path(root)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            p.unlink(missing_ok=True)
    except Exception:                                        # noqa: BLE001
        data = {}
    return data
