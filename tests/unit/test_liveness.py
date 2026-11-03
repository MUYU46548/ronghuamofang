# -*- coding: utf-8 -*-
"""运行态真相（F02）：`running` 到底是"在跑"还是"上辈子留下的"（2026-11-03）。

## 要治的病

进程被杀 / 断电 / 崩溃后，`progress.json` 里阶段的 `status` 仍然是 `running`。
界面只能靠"GUI 内存里有没有 job"来猜，命令行跑的、别的进程跑的一概猜不出 ——
用户看到的是一个永远不动的"运行中"（真机反馈过不止一次）。

判据必须是**确定性**的：谁在跑，就写自己的 pid；读的时候问一句"这个 pid 还活着吗"。

## 判据（每条都能变红）

- L1 没有 run 记录 → `alive=False, stale=False`（从没跑过，不是残留）
- L2 记录里的 pid 是活进程 → `alive=True`
- L3 记录里的 pid 已死 → `alive=False, stale=True`，并且说清"是哪个 pid、什么时候开始的"
- L4 记录里的 pid 是垃圾值（字符串/None）→ 当作已死（不许抛异常）
- L5 撤标记后回到 L1 的状态
- L6 接线：orchestrator 拿到运行锁就写标记、收尾就撤；/state 把 stale 交给界面

用法：python tests/unit/test_liveness.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import liveness                                    # noqa: E402
from utils.progress_manager import ProgressManager            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    print("运行态真相（liveness）")

    tmp = Path(tempfile.mkdtemp())
    pm = ProgressManager(str(tmp / "data" / "state" / "progress.json"))

    print("\n【1】没有记录 = 不是残留")
    st = liveness.run_status(pm)
    check("L1 alive/stale 都是 False", st["alive"] is False and st["stale"] is False, st)
    check("L1 说得清为什么", bool(st.get("why")), st)

    print("\n【2】活 pid = 在跑")
    liveness.mark_run_start(pm, stage=4)
    st = liveness.run_status(pm)
    check("L2 认出自己在跑", st["alive"] is True and st["pid"] == os.getpid(), st)
    check("L2 记下了起始时间", bool(st.get("since")), st)
    check("L2 记下了阶段", st.get("stage") == 4, st)

    print("\n【3】死 pid = 失联（这一条才是本功能）")
    pm.data["runner"]["pid"] = 999999
    st = liveness.run_status(pm)
    check("L3 判为失联", st["alive"] is False and st["stale"] is True, st)
    check("L3 说清是哪个 pid", "999999" in (st.get("why") or ""), st)

    print("\n【4】垃圾值不许抛")
    for bad in ("abc", None, [], -1):
        pm.data["runner"]["pid"] = bad
        try:
            st = liveness.run_status(pm)
            ok = st["alive"] is False
        except Exception as e:                                # noqa: BLE001
            ok = False
            st = repr(e)
        check("L4 pid=%r 当作已死且不抛" % (bad,), ok, st)

    print("\n【5】撤标记")
    liveness.mark_run_start(pm, stage=4)
    liveness.clear_run_marker(pm)
    st = liveness.run_status(pm)
    check("L5 撤掉后回到“没有记录”", st["alive"] is False and st["stale"] is False, st)
    check("L5 runner 键被清掉", "runner" not in pm.data, pm.data.get("runner"))

    print("\n【7】跨进程读取不丢 runner（2026-11-03 真机发现）")
    liveness.mark_run_start(pm, stage=2)
    path = str(tmp / "data" / "state" / "progress.json")
    pm2 = ProgressManager(path)          # 模拟 GUI/另一个进程从盘上读
    st = liveness.run_status(pm2)
    check("L7 新进程读得到 runner", st.get("pid") == os.getpid(), st)
    check("L7 不误判失联", st["alive"] is True and st["stale"] is False, st)
    pm2.save()                            # 再存盘不许把标记抹掉（旧的过滤会抹）
    pm3 = ProgressManager(path)
    check("L7 再存盘后标记仍在",
          (pm3.data.get("runner") or {}).get("pid") == os.getpid(),
          pm3.data.get("runner"))
    liveness.clear_run_marker(pm3)

    print("\n【6】接线")
    orch = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("L6 拿到锁后写标记", "mark_run_start(progress" in orch)
    check("L6 收尾撤标记", "clear_run_marker(progress" in orch)
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("L6 /state 用 liveness 判失联", "run_status(" in api)
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("L6 界面把失联说成人话", "stale" in app)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
