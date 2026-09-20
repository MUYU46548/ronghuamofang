# -*- coding: utf-8 -*-
"""S1 回归：orchestrator 自动重试路径的崩溃与脏状态。

## 本用例守护什么

2026-09-19 审查发现 `orchestrator.py:282` 在重试循环里引用 `datetime`
但全文件未导入 → `auto_retry` 默认 `true`，任一阶段失败即 `NameError`，
且 `db.finish_run()` 永不执行 → `runs` 表残留 `status='running'` 脏行。

**这条用例的价值在于：它必须能在修复前失败。** 因此断言的是「行为」而非「代码长相」：
  1. 阶段失败 + auto_retry 开启时，`run()` 不得抛异常（应返回退出码 1）
  2. `runs` 表不得残留 `status='running'`（必须有收尾状态）
  3. 通过 `finish_run_if_running` 的幂等补偿路径也要被覆盖

## 隔离策略

在**临时项目根**跑 fake 全链（与项目既有测试隔离策略一致），
真实 `data/`、`logs/`、`config/` 零污染。

用法：python tests/unit/test_orchestrator_retry.py
"""
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def build_sandbox():
    """搭一个最小可跑的临时项目根。"""
    root = Path(tempfile.mkdtemp(prefix="nf_retry_"))
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    for sub in ("data/state", "data/outline/chapters", "data/setting",
                "data/chapters/raw", "data/chapters/checked",
                "data/chapters/refined", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    shutil.copy(REPO / "scripts" / "orchestrator.py", root / "scripts" / "orchestrator.py")
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # 各 stage 模块在此用例中会被 mock 掉，但仍需存在以通过 import
    needed = ("stage1_consolidate.py", "stage2_outline.py", "stage3_chapter_outline.py",
              "stage4_writing.py", "stage5_check.py", "stage6_polish.py",
              "stage7_convert.py", "stage8_markdown_export.py",
              "snapshot.py", "material_review.py", "auto_rewrite.py",
              "chapter_review.py", "proofread.py")
    for name in needed:
        src = REPO / "scripts" / name
        if src.exists():
            shutil.copy(src, root / "scripts" / name)

    (root / "config" / "system.yaml").write_text(
        "engine: direct\n"
        "providers:\n  tokenhub:\n    available_models:\n    - glm-5\n"
        "model:\n  default:\n    provider: tokenhub\n    id: glm-5\n"
        "budget:\n  limit_yuan: 300\n  warn_ratio: 0.7\n"
        "gates:\n"
        "  auto_retry: true\n"
        "  auto_retry_max_rounds: 2\n"
        "  auto_retry_delay_seconds: 0\n"
        "  auto_retry_backoff: 1\n"
        "  pause_on_failure: true\n"
        "  require_approval: []\n", encoding="utf-8")
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: 回归测试书\n  genre: 测试\n  chapters: 1\n", encoding="utf-8")
    return root


def run_case(label, stage_side_effect, expect_code):
    """在沙箱里跑一次 orchestrator.run()，返回 (exit_code, runs 表行, 异常)。"""
    root = build_sandbox()
    code = f'''
import sys, json, sqlite3
sys.path.insert(0, r"{root / 'scripts'}")
sys.path.insert(0, r"{root}")
import os
os.chdir(r"{root}")

import orchestrator as O

CALLS = {{"n": 0}}

class FakeStage:
    @staticmethod
    def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
        CALLS["n"] += 1
        return {stage_side_effect}

O.STAGES = {{n: FakeStage for n in range(1, 10)}}
O.load_config = lambda: ({{"gates": {{"auto_retry": True, "auto_retry_max_rounds": 2,
                                     "auto_retry_delay_seconds": 0, "auto_retry_backoff": 1,
                                     "pause_on_failure": True, "require_approval": []}},
                          "budget": {{"limit_yuan": 300, "warn_ratio": 0.7}}}},
                         {{"book": {{"name": "回归测试书"}}}})
O.snap = type("S", (), {{"snapshot": staticmethod(lambda lbl: None)}})()

err = None
try:
    rc = O.run(from_stage=1, only_stage=1)
except Exception as e:
    rc = None
    err = type(e).__name__ + ": " + str(e)

con = sqlite3.connect(r"{root / 'logs' / 'runs.db'}")
rows = con.execute("SELECT id, status FROM runs").fetchall()
con.close()
print("__RESULT__" + json.dumps({{"rc": rc, "err": err, "calls": CALLS["n"],
                                  "rows": rows}}))
'''
    import subprocess
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    out = proc.stdout or ""
    marker = "__RESULT__"
    if marker not in out:
        print(f"  子进程无结果。stdout={out[-800:]}\n  stderr={(proc.stderr or '')[-800:]}")
        return None
    payload = json.loads(out.split(marker, 1)[1].splitlines()[0])
    print(f"\n--- {label} ---")
    print(f"  退出码={payload['rc']} 异常={payload['err']} "
          f"重试次数={payload['calls']} runs={payload['rows']}")
    return payload


def main():
    print("=" * 70)
    print("S1 回归：orchestrator 自动重试路径（datetime / 脏状态）")
    print("=" * 70)

    # 用例 1：阶段恒失败 → 必须收敛为 exit=1，不得抛 NameError，不得留 running
    p1 = run_case("阶段恒失败（auto_retry 走满 2 轮）",
                  'False, "模拟阶段失败"', 1)
    if p1:
        check(p1["err"] is None, "run() 未抛异常（修复前会 NameError: datetime）",
              f"实际异常={p1['err']}")
        check(p1["rc"] == 1, "返回退出码 1（阶段失败暂停）", f"实际={p1['rc']}")
        check(p1["calls"] >= 1, "重试循环确实被执行（非零次）",
              f"calls={p1['calls']}")
        statuses = [r[1] for r in p1["rows"]]
        check("running" not in statuses,
              "runs 表无残留 status='running' 脏行", f"实际={statuses}")
        check(statuses and statuses[-1] == "failed",
              "runs 末行状态为 failed", f"实际={statuses}")

    # 用例 2：首次失败、重试成功 → exit=0
    root = build_sandbox()
    code = f'''
import sys, json, sqlite3, os
sys.path.insert(0, r"{root / 'scripts'}")
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")
import orchestrator as O
CALLS = {{"n": 0}}

class FakeStage:
    @staticmethod
    def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
        CALLS["n"] += 1
        return (False, "首次失败") if CALLS["n"] == 1 else (True, "重试成功")

O.STAGES = {{n: FakeStage for n in range(1, 10)}}
O.load_config = lambda: ({{"gates": {{"auto_retry": True, "auto_retry_max_rounds": 2,
                                     "auto_retry_delay_seconds": 0, "auto_retry_backoff": 1,
                                     "pause_on_failure": True, "require_approval": []}},
                          "budget": {{"limit_yuan": 300, "warn_ratio": 0.7}}}},
                         {{"book": {{"name": "回归测试书"}}}})
O.snap = type("S", (), {{"snapshot": staticmethod(lambda lbl: None)}})()
err = None
try:
    rc = O.run(from_stage=1, only_stage=1)
except Exception as e:
    rc = None; err = type(e).__name__ + ": " + str(e)
con = sqlite3.connect(r"{root / 'logs' / 'runs.db'}")
rows = con.execute("SELECT id, status FROM runs").fetchall()
con.close()
print("__RESULT__" + json.dumps({{"rc": rc, "err": err, "calls": CALLS["n"], "rows": rows}}))
'''
    import subprocess
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)
    print("\n--- 用例 2：首次失败、重试成功 ---")
    if "__RESULT__" in (proc.stdout or ""):
        p2 = json.loads(proc.stdout.split("__RESULT__", 1)[1].splitlines()[0])
        print(f"  退出码={p2['rc']} 异常={p2['err']} 调用={p2['calls']} runs={p2['rows']}")
        check(p2["err"] is None, "重试成功路径无异常", f"{p2['err']}")
        check(p2["calls"] == 2, "重试被真实触发（首次失败+1 次重试）", f"calls={p2['calls']}")
        check([r[1] for r in p2["rows"]][-1] == "done", "runs 末行状态为 done")
    else:
        check(False, "用例 2 子进程产出结果",
              (proc.stderr or "")[-500:])

    # 用例 3：finish_run_if_running 幂等性（单元级）
    print("\n--- 用例 3：finish_run_if_running 幂等补偿 ---")
    sys.path.insert(0, str(REPO / "scripts"))
    from utils.db import RunDB
    tmpdb = Path(tempfile.mkdtemp(prefix="nf_db_")) / "r.db"
    db = RunDB(tmpdb)
    rid = db.start_run()
    check(db.finish_run_if_running(rid, "crashed") is True,
          "首次补偿：running → crashed 命中 1 行")
    check(db.finish_run_if_running(rid, "crashed") is False,
          "二次补偿：非 running 行不重复更新（幂等）")
    rid2 = db.start_run()
    db.finish_run(rid2, "done")
    check(db.finish_run_if_running(rid2, "crashed") is False,
          "已正常收尾的 run 不被兜底改写（done 保持不变）")
    row = db.conn.execute("SELECT status FROM runs WHERE id=?", (rid2,)).fetchone()
    check(row[0] == "done", "done 状态未被覆盖为 crashed", f"实际={row[0]}")
    check(db.finish_run_if_running(None, "crashed") is False,
          "run_id=None 安全返回 False（不抛异常）")
    db.close()

    print("\n" + "=" * 70)
    print(f"结果：PASS={PASS}  FAIL={FAIL}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
