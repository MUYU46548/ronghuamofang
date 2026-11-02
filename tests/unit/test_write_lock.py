# -*- coding: utf-8 -*-
"""施工权（不可用清单第 10 条 / P5 · L-1~L-5）。

## 要治的病

\u300c两个 agent 同时用必炸\u300d：锁只装在总闸上（`orchestrator.py:311` 唯一调用点），
段落重写、保存正文、改设定、审批、打回、plan 落盘**全部裸写**。

## 判据（每条都能变红）

- W1 没有锁 → 放行
- W2 AI 持锁（活进程）→ 人 409 但可接管；外部 Agent 409 且不可接管
- W3 人持锁 → 人放行；外部 Agent 仍 409（单一施工权）
- W4 持有进程已死 → 放行（陈旧锁不许把全书锁死）
- W5 **旧锁文件没有 owner 字段 → 按 AI 持有处理**（当成"没人持锁"= 开后门）
- W6 `/stop`、`/stream/*`、`/lock/*` 在 AI 持锁时仍放行（那是人的通道本身）
- W7 流水线检查点：人接管后必须说"停手"，AI 持锁时说"继续"
- W8 takeover 把 owner 改成 human 并记下原持有者；release(force) 清锁

用法：python tests/unit/test_write_lock.py
"""
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import lock_guard                                        # noqa: E402
from utils.run_lock import RunLock, read_holder                     # noqa: E402
from utils import agent_guard                                       # noqa: E402

PASS, FAIL = [], []
DEAD_PID = 999999        # 查活必失败（Windows OpenProcess 打不开句柄）


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def _write_lock(path, pid, owner=None):
    data = {"pid": pid, "started_at": "2026-11-02T10:00:00",
            "host": "TEST", "cmd": "python scripts/orchestrator.py"}
    if owner is not None:
        data["owner"] = owner
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main():
    print("=" * 66)
    print("施工权（锁在谁手里 + 谁能写）")
    print("=" * 66)
    with tempfile.TemporaryDirectory() as tmp:
        lock = str(Path(tmp) / "data" / "state" / "run.lock")
        W = lambda path="/chapters/save", gui=True: lock_guard.check_write(lock, path, gui)  # noqa: E731

        # ---- W1 无锁 ----
        check("W1 没有锁 → 人可写", W(gui=True) is None)
        check("W1 没有锁 → Agent 可写", W(gui=False) is None)

        # ---- W2 AI 持锁 ----
        _write_lock(lock, os.getpid(), owner="pipeline")
        r = W(gui=True)
        check("W2 AI 持锁 → 人 409", r is not None, r and r.get("code"))
        check("W2 人的 409 可接管", r and r.get("can_takeover") is True, r and r.get("can_takeover"))
        check("W2 拒绝里说清是谁在跑", r and "AI 流水线" in r.get("error", ""), r and r.get("error"))
        r2 = W(gui=False)
        check("W2 Agent 409 且不给接管", r2 and r2.get("can_takeover") is False, r2)

        # ---- W6 人的通道永不拦 ----
        for path in ("/stop", "/stream/pause", "/stream/resume",
                     "/lock/takeover", "/lock/release"):
            check("W6 AI 持锁时 %s 仍放行" % path, W(path) is None)

        # ---- W3 人持锁 ----
        _write_lock(lock, os.getpid(), owner="human")
        check("W3 人持锁 → 人可写", W(gui=True) is None)
        r3 = W(gui=False)
        check("W3 人持锁 → Agent 仍 409", r3 is not None, r3 and r3.get("error"))

        # ---- W5 旧锁没有 owner ----
        _write_lock(lock, os.getpid(), owner=None)
        r5 = W(gui=False)
        check("W5 旧锁缺 owner → 按流水线处理（不放开）", r5 is not None, r5)
        print("     读数检查：", read_holder(lock))

        # ---- W4 持有进程已死 ----
        _write_lock(lock, DEAD_PID, owner="pipeline")
        check("W4 进程已死 → 放行（陈旧锁不锁死全书）", W(gui=False) is None, read_holder(lock))

        # ---- W7 流水线检查点 ----
        _write_lock(lock, os.getpid(), owner="human")
        why = lock_guard.check_pipeline_should_stop(lock)
        check("W7 人接管 → 流水线必须停手", bool(why) and "接管" in why, why)
        _write_lock(lock, os.getpid(), owner="pipeline")
        check("W7 AI 持锁 → 流水线继续", lock_guard.check_pipeline_should_stop(lock) is None)

        # ---- W8 接管 / 释放 ----
        _write_lock(lock, os.getpid(), owner="pipeline")
        lk = RunLock(lock)
        ok, _ = lk.takeover("human", by="测试")
        h = read_holder(lock)
        check("W8 接管成功", ok and h["owner"] == "human", h)
        check("W8 记下原持有者", bool(json.loads(Path(lock).read_text(encoding="utf-8"))
                                       .get("preempted")), "锁里应有 preempted 字段")
        RunLock(lock).release(force=True)
        check("W8 人释放后锁没了", not Path(lock).exists())

        # ---- W9 界面状态文案（L-5）----
        st = lock_guard.lock_status(lock)
        check("W9 无锁时说人话", st.get("held") is False and "没" in st.get("text", ""), st)
        _write_lock(lock, os.getpid(), owner="human")
        st = lock_guard.lock_status(lock)
        check("W9 人持锁时说在你手里", "你" in st.get("text", ""), st)
        _write_lock(lock, os.getpid(), owner="pipeline")
        st = lock_guard.lock_status(lock)
        check("W9 AI 持锁时说出是谁", "AI 流水线" in st.get("text", ""), st)

    # ---- W10 外部 Agent 不得夺锁 ----
    check("W10 /lock/takeover 在 Agent 禁止名单里",
          agent_guard.is_forbidden_in_agent_mode("/lock/takeover"))
    check("W10 /lock/release 在 Agent 禁止名单里",
          agent_guard.is_forbidden_in_agent_mode("/lock/release"))
    check("W10 只读的 GET /lock 不在名单里（Agent 该知道锁在谁手里）",
          not agent_guard.is_forbidden_in_agent_mode("/lock"))

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
