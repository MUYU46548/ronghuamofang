# -*- coding: utf-8 -*-
"""阶段终态：`retrying` 必须有出口（2026-11-03，真机 run 53）。

## 要治的病

阶段 4 失败 → orchestrator 标 `retrying` → 作者点“停止” → 收尾只写了 `runs` 表
（`status: stopped`），**progress.json 一个字没动**，阶段永远停在 `retrying`：

    阶段4: {"status": "retrying", "retry_count": 2, "retry_started_at": "..."}

界面上既不是失败也不在跑，重试次数还停在 2 —— 作者看到的是“卡在重试循环里”。
`retrying` 能进不能出，没有任何出口。

## 判据（每条都能变红）

- S1 retrying + stopped  → 落 stopped，带人话原因，有 finished_at
- S2 retrying + failed   → 落 failed
- S3 retrying + paused   → 落 paused（熔断暂停也要有落点）
- S4 running 不动（收尾不替正在跑的阶段下结论）
- S5 done 不动（绝不许把完成改回失败）
- S6 非终态 status（如 done）传进来 → 不动
- S7 `_finalize` 真的调了它（源码口径，防止有人把这一步又删掉）

用法：python tests/unit/test_stage_terminal_state.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import orchestrator                                              # noqa: E402
from utils.progress_manager import ProgressManager               # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def fresh(status):
    """造一个 progress.json，阶段 4 处于给定状态。返回 (pm, tmpdir)。"""
    tmp = tempfile.mkdtemp(prefix="nf_settle_")
    pm = ProgressManager(str(Path(tmp) / "progress.json"))
    pm.set_stage(4, status)
    return pm, tmp


def main():
    print("=" * 66)
    print("阶段终态：retrying 必须是能进能出的状态")
    print("=" * 66)

    for i, status in enumerate(("stopped", "failed", "paused"), start=1):
        pm, _ = fresh("retrying")
        pm.set_stage(4, "retrying", retry_count=2)
        changed = orchestrator.settle_retrying_stage(pm, 4, status)
        st = pm.data["stages"]["4"]
        check("S%d retrying → %s" % (i, status),
              changed and st.get("status") == status, st.get("status"))
        check("S%d 带人话原因（不是空串/英文码）" % i,
              bool(st.get("error")) and st["error"] == orchestrator.RETRY_SETTLE_WHY[status],
              st.get("error"))
        check("S%d 写了 finished_at（能说清这一轮什么时候停的）" % i,
              bool(st.get("finished_at")), st.get("finished_at"))

    # S4 / S5：绝不动非 retrying 的阶段
    for status in ("running", "done"):
        pm, _ = fresh(status)
        changed = orchestrator.settle_retrying_stage(pm, 4, "stopped")
        check("S %s：%s 不被收尾改写" % ("4" if status == "running" else "5", status),
              (not changed) and pm.data["stages"]["4"].get("status") == status,
              pm.data["stages"]["4"].get("status"))

    # S6：非终态 status 直接不动
    pm, _ = fresh("retrying")
    changed = orchestrator.settle_retrying_stage(pm, 4, "done")
    check("S6 非终态 status（done）不当落点", (not changed)
          and pm.data["stages"]["4"].get("status") == "retrying",
          pm.data["stages"]["4"].get("status"))

    # S7：源码口径 —— 收尾必须调它
    src = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("S7 _finalize 里真的调了 settle_retrying_stage",
          "settle_retrying_stage(progress, _n, status)" in src)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
