# -*- coding: utf-8 -*-
"""运行态真相：`running` 是"在跑"还是"上辈子留下的"（F02，2026-11-03）。

进程被杀 / 断电 / 崩溃后，`progress.json` 里的阶段还写着 `running`。界面只能靠
"GUI 内存里有没有 job" 猜，命令行起的 run 一概猜不出 → 用户盯着一个永远不动的
"运行中"。这里换成确定性判据：谁在跑就写自己的 pid，读的时候问"这个 pid 还活着吗"。

零 IO 之外只读 `progress.data["runner"]`，因此可单测（见 `tests/unit/test_liveness.py`）。
"""
from __future__ import annotations

import os
import time
from typing import Any

_MARK = "runner"


def pid_alive(pid: Any) -> bool:
    """这个 pid 现在是活进程吗。垃圾值一律当"已死"（不许抛）。

    用 `os.kill(pid, 0)` 而不是 psutil：后者是可选依赖，而且只需要"存在性"这一个
    信息就够 —— 不需要判断它是不是僵尸、属于谁。
    """
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    if n == os.getpid():
        return True
    try:
        if os.name == "nt":
            # Windows 上 os.kill(pid, 0) 会真的发 CTRL_C_EVENT/杀进程（历史坑），
            # 所以走 OpenProcess 查询句柄（不获取权限、不改变进程状态）。
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, n)
            if not handle:
                return False
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return False
                # STILL_ACTIVE = 259
                return code.value == 259
            finally:
                kernel32.CloseHandle(handle)
        os.kill(n, 0)
        return True
    except OSError:
        return False
    except Exception:                                        # noqa: BLE001
        return False


def mark_run_start(progress, stage=None, note: str = "") -> dict:
    """当前进程开始跑流水线：把 pid 写进 progress（收尾必须 clear_run_marker）。"""
    info = {
        "pid": os.getpid(),
        "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        if stage is not None:
            info["stage"] = int(stage)
    except (TypeError, ValueError):
        pass
    if note:
        info["note"] = str(note)
    try:
        progress.data[_MARK] = info
        progress.save()
    except Exception:                                        # noqa: BLE001
        pass
    return info


def clear_run_marker(progress) -> None:
    """这一轮结束了：撤掉标记（不撤的话下次启动会被误判成失联）。"""
    try:
        if _MARK in progress.data:
            progress.data.pop(_MARK, None)
            progress.save()
    except Exception:                                        # noqa: BLE001
        pass


def run_status(progress) -> dict:
    """`{"alive", "stale", "pid", "since", "stage", "why"}` —— 界面与 /state 共用。

    · 没有记录 → alive=False, stale=False（从没跑过，不是残留）
    · pid 活着 → alive=True
    · pid 死了 → alive=False, stale=True，why 里带 pid 与起始时间
    """
    try:
        info = (getattr(progress, "data", {}) or {}).get(_MARK) or {}
    except Exception:                                        # noqa: BLE001
        info = {}
    if not isinstance(info, dict) or not info:
        return {"alive": False, "stale": False, "pid": None, "why": "没有记录中的 run"}
    pid = info.get("pid")
    since = info.get("since") or ""
    stage = info.get("stage")
    out = {"pid": pid, "since": since, "stage": stage}
    if pid_alive(pid):
        out.update(alive=True, stale=False, why="正在跑（pid %s）" % pid)
        return out
    out.update(
        alive=False, stale=True,
        why=("**失联**：记录里的 pid %s 已经不在了（开始于 %s）—— 那是上辈子留下的 "
             "`running`，不是现在在跑。点“运行”即可断点续跑（已完成章节不会重写）"
             % (pid, since or "未知时间")),
    )
    return out
