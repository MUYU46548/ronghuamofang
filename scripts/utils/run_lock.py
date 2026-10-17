# -*- coding: utf-8 -*-
"""跨进程运行锁（data/state/run.lock）—— 同一本书同时只跑一条流水线。

## 为什么需要（2026-10-17 第14次试跑复盘）

用户问「是否支持跨会话跑同一本书」。答案此前是**不安全**：nf_api 的 409
守卫只在**单进程内**有效（CURRENT 字典），CLI orchestrator、GUI 后台任务、
外部 agent 各起一个进程时互不知情 —— 两条流水线同时写 progress.json /
data/chapters / runs.db 会互相踩踏（第14次试跑就留下多条 status='running'
的脏行）。本锁把「同时只跑一条」从约定变成机制。

## 语义

- 锁文件 `data/state/run.lock` 是一行 JSON：``{pid, started_at, host, cmd}``。
- ``acquire()`` 拿不到锁时返回 ``(False, 人话原因)``，调用方**不要**继续开跑。
- 陈旧锁自愈：持有进程已死（pid 查活失败）或锁文件损坏/超龄（12 小时，
  防 pid 复用误判）→ 告警后接管，不让一次崩溃把书永久锁死。
- 持有者自己重复 acquire（同进程内重入，如 nf_api 串行 job）→ 视为已持有，
  不自杀。
- ``release()`` 只删自己持有的锁（pid 比对），绝不误删别人的。

## Windows 坑（写查活时必读）

**绝不能用 ``os.kill(pid, 0)`` 查活**：Windows 上 os.kill 的非
CTRL_C/CTRL_BREAK 信号会走 TerminateProcess —— 传 0 不是"探测"，是
**把目标进程杀掉（exit code 0）**。查活用 OpenProcess
（PROCESS_QUERY_LIMITED_INFORMATION = 0x1000）：拿得到句柄即活。
"""
import ctypes
import json
import os
import time
from pathlib import Path

LOCK_NAME = "run.lock"
STALE_AFTER_SECONDS = 12 * 3600   # 超龄锁视为陈旧（防 pid 复用误判兜底）


def _pid_alive(pid):
    """查 pid 是否存活（Windows 用 OpenProcess，POSIX 用 kill(pid,0)）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
            k32.OpenProcess.restype = ctypes.c_void_p
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000：只查询不加权，
            # 打不开句柄（含进程不存在 / 拒绝访问）都按"不存活"处理。
            handle = k32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                # 关键第二查（2026-10-17 实测踩坑）：已退出但**残留句柄未销账**的
                # 进程，OpenProcess 仍会成功 —— tasklist 已查无此进程而本函数
                # 报"活"，陈旧锁因此永远接管不了。退出码 == STILL_ACTIVE(259)
                # 才是"真活"的判据。
                code = ctypes.c_uint32()
                if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == 259
                return False
            finally:
                k32.CloseHandle(handle)
        except Exception:                                   # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:      # 存在但无权限发信号
        return True
    except Exception:                                   # noqa: BLE001
        return False


class RunLock:
    """orchestrator 级跨进程互斥。用法::

        lock = RunLock("data/state/run.lock")
        ok, msg = lock.acquire()
        if not ok:
            print(msg); return 1
        try:
            ...
        finally:
            lock.release()
    """

    def __init__(self, path):
        self.path = Path(path)
        self._held = False

    def acquire(self):
        """拿锁。返回 (ok, message)。失败时 message 是给操作者看的处置指引。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):   # 陈旧锁接管后再试一轮
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                info = self._read()
                pid = info.get("pid")
                if pid == os.getpid():
                    # 同进程重入（nf_api 串行 job 场景）：视为已持有，不自杀。
                    self._held = True
                    return True, ""
                age = time.time() - self.path.stat().st_mtime
                if _pid_alive(pid) and age < STALE_AFTER_SECONDS:
                    return False, (f"已有另一条流水线在跑（pid={pid}，"
                                   f"开始于 {info.get('started_at', '?')}，"
                                   f"命令: {info.get('cmd', '?')}）—— 同一本书"
                                   f"不能两条流水线同时写。等它结束，或确认它已死"
                                   f"后删除 {self.path} 再跑。")
                # 陈旧：进程已死 / 超龄 / 文件损坏 → 告警接管
                print(f"[run_lock] 陈旧锁接管：pid={pid} "
                      f"{'已退出' if not _pid_alive(pid) else '超龄 %.1fh' % (age / 3600)}"
                      f"（原文件: {info.get('_raw', '')[:120]}）→ 覆盖")
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(),
                           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "host": os.environ.get("COMPUTERNAME", ""),
                           "cmd": " ".join(os.sys.argv)[:200]},
                          f, ensure_ascii=False)
            self._held = True
            return True, ""
        return False, f"运行锁竞争未收敛（{self.path}）—— 请人工检查该文件"

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a dict")
            data.setdefault("pid", None)
            return data
        except Exception as e:                           # noqa: BLE001
            # 损坏的锁文件按"陈旧"处理，但把原文带出去供告警展示
            try:
                raw = self.path.read_text(encoding="utf-8", errors="replace")
            except Exception:                           # noqa: BLE001
                raw = "<unreadable>"
            return {"pid": None, "_raw": f"{type(e).__name__}: {raw}"}

    def release(self):
        """释放自己持有的锁。pid 比对：绝不误删别人的锁。"""
        if not self._held:
            return
        try:
            info = self._read()
            if info.get("pid") == os.getpid():
                self.path.unlink(missing_ok=True)
        finally:
            self._held = False
