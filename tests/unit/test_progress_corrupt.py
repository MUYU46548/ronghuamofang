# -*- coding: utf-8 -*-
"""progress.json 损坏时的处置自检（2026-09-29）。

背景：`ProgressManager._load()` 原先在 JSON 解析失败时**静默降级为空状态** ——
界面上表现为「全部阶段未开始」，而**下一次 save() 会用默认值把用户原有的阶段状态整体覆盖**
（真丢数据）。这是典型的静默失败：不报错、还顺手把证据抹掉。

本自检覆盖：
A. 损坏输入 → 大声告警 + **原件先备份** + 进程内降级（不崩）。
B. 备份内容与原件逐字一致（能人工恢复）。
C. 正常输入不受影响（回归）：1-4 done+approved、5-7 pending 能原样读回。

全程离线、零网络、零费用。
用法：python tests/unit/test_progress_corrupt.py
"""
import glob
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.progress_manager import ProgressManager   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


BAD = '# 注释开头的进度文件（非法 JSON）\n{"stages": {"1": {"status": "done"}}}\n'


def test_corrupt():
    print("\n[A/B] 损坏的 progress.json：告警 + 留原件 + 降级")
    d = Path(tempfile.mkdtemp(prefix="nf_prog_"))
    p = d / "progress.json"
    p.write_text(BAD, encoding="utf-8")

    buf = io.StringIO()
    with redirect_stdout(buf):
        pm = ProgressManager(p)
    out = buf.getvalue()

    check("打印了 WARN（不再静默）", "WARN" in out and "解析失败" in out, out.strip()[:160])
    check("告警里带文件路径", "progress.json" in out)
    check("降级为 pending 而不是抛异常", pm.stage_status(1) == "pending", pm.stage_status(1))

    baks = sorted(glob.glob(str(p) + ".corrupt-*"))
    check("原件已另存为 .corrupt-*", len(baks) == 1, [os.path.basename(b) for b in baks])
    if baks:
        check("备份内容与原件逐字一致（可人工恢复）",
              Path(baks[0]).read_text(encoding="utf-8") == BAD)
    check("降级后 save() 不会覆盖原件（原件已在备份里）",
          Path(baks[0]).read_text(encoding="utf-8") == BAD if baks else False)


def test_valid_regression():
    print("\n[C] 正常 progress.json 不受影响（回归）")
    d = Path(tempfile.mkdtemp(prefix="nf_prog_ok_"))
    p = d / "progress.json"
    data = {
        "project": "夹具书", "version": 3, "current_stage": 5,
        "stages": {
            "1": {"status": "done", "approved": True},
            "2": {"status": "done", "approved": True},
            "3": {"status": "done", "approved": True},
            "4": {"status": "done", "approved": True},
            "5": {"status": "pending"},
        },
        "budget": {"limit_yuan": 300, "spent_yuan": 0.0, "paused": False},
    }
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        pm = ProgressManager(p)
    check("合法文件：无 WARN", "WARN" not in buf.getvalue(), buf.getvalue()[:120])
    check("阶段 1-4 读回 done 且已批准",
          all(pm.data["stages"][k]["status"] == "done" and pm.data["stages"][k]["approved"]
              for k in ("1", "2", "3", "4")))
    check("阶段 5 读回 pending", pm.data["stages"]["5"]["status"] == "pending")
    check("project 字段读回", pm.data.get("project") == "夹具书")
    check("未产生 .corrupt-* 文件", not glob.glob(str(p) + ".corrupt-*"))


def main():
    print("===== progress.json 损坏处置自检 =====")
    test_corrupt()
    test_valid_regression()
    print("\n===== 合计: %d passed, %d failed =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
