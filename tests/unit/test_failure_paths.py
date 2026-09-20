# -*- coding: utf-8 -*-
"""F1~F8 失败路径回归：堵住「FakeClient 恒 exit_code=0」的最大盲区。

## 背景（2026-09-19 审查报告 H5）

全仓 22 个 `result["exit_code"] != 0` 分支在测试里**一次都没走到过**。
`FakeClient` 永远成功，于是「子会话失败 → 标记章节失败 → 阶段失败 →
自动重试 → 预算熔断 / 用户停止」这条**兜底链路**完全是纸面承诺。

比覆盖率低更危险的是「声称覆盖 > 实际覆盖」——它制造虚假安全感。
本用例是该结论的正面回应：**主动把系统打坏，看它是不是按承诺的方式坏。**

## 覆盖清单

| 编号 | 目标 | 断言的是行为，不是代码长相 |
|------|------|--------------------------|
| F1 | 阶段失败 → 重试 | `retry_count` 真实递增；重试次数符合 `auto_retry_max_rounds` |
| F2 | 重试异常不外泄 | `run()` 返回而非抛；`runs` 无 `running` 脏行 |
| F3 | 重试-预算熔断 | 超预算 → exit=2，且 `progress.budget.paused=True` |
| F4 | 重试-用户停止 | stop 置位 → exit=4，状态 `stopped` |
| F5 | stage4 逐章失败隔离 | 单章失败被 `mark_chapter_failed` 记录，**不拖垮整阶段** |
| F6 | 未定义名静态检测 | 等价 `ruff F821` 的 pyflakes 门禁零 error |
| F7 | seedWorkspace 升级 | 旧 `.seeded` workspace 升级后 scripts/prompts 被刷新 |
| F8 | approve/reject 审批门 | 打回后下游产物被清、状态被重置、标记被撤销 |

## 隔离策略

所有 Python 侧用例在**临时项目根**跑（与项目既有策略一致），
真实 `data/`、`logs/`、`config/` 零污染。F7 在临时 AppData 目录跑。

用法：python tests/unit/test_failure_paths.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASS = 0
FAIL = 0

# 沙箱需要的脚本（缺一即 import 失败）。与 test_orchestrator_retry.py 同源。
NEEDED_SCRIPTS = (
    "orchestrator.py", "stage1_consolidate.py", "stage2_outline.py",
    "stage3_chapter_outline.py", "stage4_writing.py", "stage5_check.py",
    "stage6_polish.py", "stage7_convert.py", "stage8_markdown_export.py",
    "snapshot.py", "material_review.py", "auto_rewrite.py",
    "chapter_review.py", "proofread.py", "approve.py", "reject.py",
)


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def build_sandbox(prefix="nf_fail_", gates_extra="", budget_limit=300):
    """搭一个最小可跑的临时项目根。"""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    for sub in ("data/state", "data/outline/chapters", "data/setting",
                "data/chapters/raw", "data/chapters/checked",
                "data/chapters/refined", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    # 提示词必须真实复制：stage4 等阶段会 load_template("stageN_*.md")，
    # 缺文件会 FileNotFoundError —— 那是**测试环境问题**，不是被测缺陷。
    if (REPO / "prompts").is_dir():
        shutil.copytree(REPO / "prompts", root / "prompts",
                        ignore=shutil.ignore_patterns("__pycache__", "history"))

    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name in NEEDED_SCRIPTS:
        src = REPO / "scripts" / name
        if src.exists():
            shutil.copy(src, root / "scripts" / name)

    (root / "config" / "system.yaml").write_text(
        "engine: direct\n"
        "providers:\n  tokenhub:\n    available_models:\n    - glm-5\n"
        "model:\n  default:\n    provider: tokenhub\n    id: glm-5\n"
        f"budget:\n  limit_yuan: {budget_limit}\n  warn_ratio: 0.7\n"
        "gates:\n"
        "  auto_retry: true\n"
        "  auto_retry_max_rounds: 2\n"
        "  auto_retry_delay_seconds: 0\n"
        "  auto_retry_backoff: 1\n"
        "  pause_on_failure: true\n"
        "  require_approval: []\n"
        + gates_extra, encoding="utf-8")
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: 失败路径测试书\n  genre: 测试\n  chapters: 2\n",
        encoding="utf-8")
    return root


def run_py(root, code, timeout=180):
    """在沙箱里跑一段 python，返回 (payload, stdout, stderr)。"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)
    out = proc.stdout or ""
    if "__RESULT__" not in out:
        return None, out, proc.stderr or ""
    try:
        payload = json.loads(out.split("__RESULT__", 1)[1].splitlines()[0])
    except (ValueError, IndexError):
        return None, out, proc.stderr or ""
    return payload, out, proc.stderr or ""


# ====================================================================== F1 + F2
def case_f1_f2():
    """F1 重试计数递增；F2 异常不外泄、无 running 脏行。"""
    print("\n【F1/F2】阶段恒失败 → 重试计数 + 异常收敛")
    root = build_sandbox("nf_f1_")
    code = f'''
import sys, json, sqlite3, os
sys.path.insert(0, r"{root / 'scripts'}")
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")
import orchestrator as O
from utils.progress_manager import ProgressManager

CALLS = {{"n": 0}}

class AlwaysFailStage:
    @staticmethod
    def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
        CALLS["n"] += 1
        return False, "模拟阶段失败"

O.STAGES = {{n: AlwaysFailStage for n in range(1, 10)}}
O.load_config = lambda: ({{"gates": {{"auto_retry": True, "auto_retry_max_rounds": 2,
                                     "auto_retry_delay_seconds": 0, "auto_retry_backoff": 1,
                                     "pause_on_failure": True, "require_approval": []}},
                          "budget": {{"limit_yuan": 300, "warn_ratio": 0.7}}}},
                         {{"book": {{"name": "失败路径测试书"}}}})
O.snap = type("S", (), {{"snapshot": staticmethod(lambda lbl: None)}})()
O._stop_requested = lambda: False

err = None
try:
    rc = O.run(from_stage=1, only_stage=1)
except Exception as e:
    rc = None; err = type(e).__name__ + ": " + str(e)

con = sqlite3.connect(r"{root / 'logs' / 'runs.db'}")
rows = con.execute("SELECT id, status FROM runs").fetchall()
con.close()
pm = ProgressManager(r"{root / 'data' / 'state' / 'progress.json'}")
st = pm.data["stages"]["1"]
print("__RESULT__" + json.dumps({{
    "rc": rc, "err": err, "calls": CALLS["n"], "rows": rows,
    "retry_count": st.get("retry_count"),
    "stage_status": st.get("status"),
}}))
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "F1/F2 子进程产出结果", f"stdout={out[-500:]} stderr={errout[-500:]}")
        return
    print(f"  退出码={p['rc']} 异常={p['err']} 阶段调用={p['calls']} "
          f"retry_count={p['retry_count']} runs={p['rows']}")
    check(p["err"] is None, "F2 run() 未抛异常（失败被收敛为退出码）", f"实际={p['err']}")
    check(p["rc"] == 1, "F2 退出码 1（阶段失败暂停）", f"实际={p['rc']}")
    statuses = [r[1] for r in p["rows"]]
    check("running" not in statuses, "F2 runs 表无残留 running 脏行", f"实际={statuses}")
    check(statuses and statuses[-1] == "failed",
          "F2 runs 末行状态为 failed", f"实际={statuses}")
    # 首次执行 + 2 轮重试 = 3
    check(p["calls"] == 3, "F1 阶段被调用 3 次（首次 + 2 轮重试）",
          f"实际={p['calls']}")
    check(p["retry_count"] == 2, "F1 progress.retry_count 递增到 2（走满上限）",
          f"实际={p['retry_count']}")


# ====================================================================== F3
def case_f3():
    """F3 重试成功但烧钱 → 阶段末尾的预算检查熔断 → exit=2 + budget.paused。

    采用「重试后成功」而非「恒失败」：恒失败会在**重试循环内部**先因预算
    返回 paused（那条路径由 F3b 覆盖）。本用例针对阶段循环**末尾**的
    常规熔断检查（orchestrator.py 的 `state, spent = cost.status(run_id)` 段），
    两条路径语义不同，都要验。
    """
    print("\n【F3】阶段完成但超预算 → 熔断 exit=2")
    root = build_sandbox("nf_f3_", budget_limit=1)
    code = f'''
import sys, json, sqlite3, os
sys.path.insert(0, r"{root / 'scripts'}")
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")
import orchestrator as O
from utils.progress_manager import ProgressManager

CALLS = {{"n": 0}}

class OkStage:
    @staticmethod
    def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
        CALLS["n"] += 1
        # 关键：阶段"成功"但**真的花了钱**（999 元，远超 1 元限额）。
        # 若不在这里记账，末尾的 cost.status() 永远是 ok，测不到熔断。
        cost.db.log_cost(run_id, 1, 0, "glm-5", 1000, 1000, 999.0)
        return True, "模拟阶段成功"

O.STAGES = {{n: OkStage for n in range(1, 10)}}
O.load_config = lambda: ({{"gates": {{"auto_retry": True, "auto_retry_max_rounds": 2,
                                     "auto_retry_delay_seconds": 0, "auto_retry_backoff": 1,
                                     "pause_on_failure": True, "require_approval": []}},
                          "budget": {{"limit_yuan": 1, "warn_ratio": 0.7}}}},
                         {{"book": {{"name": "失败路径测试书"}}}})
O.snap = type("S", (), {{"snapshot": staticmethod(lambda lbl: None)}})()
O._stop_requested = lambda: False

err = None
try:
    # 注意用 from_stage=1 且跑多阶段：末尾预算检查在 `_finalize("done", 0)` **之前**，
    # 熔断窗口发生在**下一阶段启动前**。只在 order 还有下一阶段时才可观测。
    rc = O.run(from_stage=1, only_stage=None)
except Exception as e:
    rc = None; err = type(e).__name__ + ": " + str(e)

con = sqlite3.connect(r"{root / 'logs' / 'runs.db'}")
rows = con.execute("SELECT id, status FROM runs").fetchall()
con.close()
pm = ProgressManager(r"{root / 'data' / 'state' / 'progress.json'}")
print("__RESULT__" + json.dumps({{
    "rc": rc, "err": err, "rows": rows, "calls": CALLS["n"],
    "paused": bool((pm.data.get("budget") or {{}}).get("paused")),
}}))
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "F3 子进程产出结果", f"stdout={out[-500:]} stderr={errout[-500:]}")
        return
    print(f"  退出码={p['rc']} 异常={p['err']} 阶段调用={p['calls']} "
          f"budget.paused={p['paused']} runs={p['rows']}")
    check(p["err"] is None, "F3 熔断路径无异常", f"实际={p['err']}")
    check(p["rc"] == 2, "F3 退出码 2（预算熔断）", f"实际={p['rc']}")
    check(p["paused"] is True, "F3 progress.budget.paused 被置位（GUI 可见）",
          f"实际={p['paused']}")
    check(p["calls"] == 1, "F3 熔断后不再启动后续阶段（只跑了第 1 个）",
          f"实际={p['calls']}")
    statuses = [r[1] for r in p["rows"]]
    check("running" not in statuses, "F3 runs 无残留 running", f"实际={statuses}")
    check(statuses and statuses[-1] == "paused", "F3 runs 末行状态为 paused",
          f"实际={statuses}")

    # --- F3c：**最后一个阶段**超预算 → 阶段末尾检查必须熔断（S10）
    #
    # 这是本组最关键的一条，也是最难构造的一条：
    #   · 「失败 → 重试前检查」由 F3b 覆盖；
    #   · 「非末阶段成功后 → 下一阶段启动前检查」由上面的 F3 覆盖
    #     （阶段1 成功后进入阶段2 前被拦）。
    #   · 唯独「**最后一个阶段**成功且烧钱」没有下一阶段可拦 ——
    #     只能靠阶段循环**末尾**那次检查。而它此前只 print 不停机（S10），
    #     于是 run() 走到 `_finalize("done", 0)` 宣告"全部完成"，GUI 显示成功，
    #     实际已超预算 → 静默失败。
    #
    # 构造：only_stage=8（最后一站），阶段 8 自己烧掉超预算的钱并返回成功。
    # 期望：exit=2 + runs.status='paused'，而不是 exit=0 + 'done'。
    print("\n【F3c】最后一站（阶段8）超预算 → 末尾检查必须熔断（S10 回归）")
    root_c = build_sandbox("nf_f3c_", budget_limit=1)
    code_c = f'''
import sys, json, sqlite3, os
sys.path.insert(0, r"{root_c / 'scripts'}")
sys.path.insert(0, r"{root_c}")
os.chdir(r"{root_c}")
import orchestrator as O
from utils.progress_manager import ProgressManager

class LastStage:
    @staticmethod
    def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
        # 阶段 8 成功，但花掉 999 元（限额 1 元）
        cost.db.log_cost(run_id, 8, 0, "glm-5", 1000, 1000, 999.0)
        return True, "模拟末阶段成功"

O.STAGES = {{n: LastStage for n in range(1, 10)}}
O.load_config = lambda: ({{"gates": {{"auto_retry": True, "auto_retry_max_rounds": 2,
                                     "auto_retry_delay_seconds": 0, "auto_retry_backoff": 1,
                                     "pause_on_failure": True, "require_approval": []}},
                          "budget": {{"limit_yuan": 1, "warn_ratio": 0.7}}}},
                         {{"book": {{"name": "失败路径测试书"}}}})
O.snap = type("S", (), {{"snapshot": staticmethod(lambda lbl: None)}})()
O._stop_requested = lambda: False

err = None
try:
    rc = O.run(from_stage=8, only_stage=8)
except Exception as e:
    rc = None; err = type(e).__name__ + ": " + str(e)
con = sqlite3.connect(r"{root_c / 'logs' / 'runs.db'}")
rows = con.execute("SELECT id, status FROM runs").fetchall()
con.close()
pm = ProgressManager(r"{root_c / 'data' / 'state' / 'progress.json'}")
print("__RESULT__" + json.dumps({{
    "rc": rc, "err": err, "rows": rows,
    "paused": bool((pm.data.get("budget") or {{}}).get("paused")),
    "st8": pm.stage_status(8),
}}))
'''
    pc, outc, errc = run_py(root_c, code_c)
    if pc is None:
        check(False, "F3c 子进程产出结果",
              f"stdout={outc[-500:]} stderr={errc[-500:]}")
    else:
        print(f"  退出码={pc['rc']} 异常={pc['err']} runs={pc['rows']} "
              f"paused={pc['paused']} st8={pc['st8']}")
        check(pc["err"] is None, "F3c 末阶段熔断路径无异常", f"实际={pc['err']}")
        check(pc["rc"] == 2,
              "F3c 最后一站超预算 → 退出码 2（不是 0「全部完成」）",
              f"实际={pc['rc']}（0 = 静默宣告成功，S10 缺陷）")
        check([r[1] for r in pc["rows"]][-1] == "paused",
              "F3c runs 末行状态为 paused（不是 done）", f"实际={pc['rows']}")
        check(pc["paused"] is True, "F3c budget.paused 置位（GUI 可提示）",
              f"实际={pc['paused']}")

    # F3b：恒失败 + 超预算 → 走**重试循环内部**的熔断分支（另一条路径）
    print("\n【F3b】重试循环内部预算熔断")
    root_b = build_sandbox("nf_f3b_", budget_limit=1)
    code_b = f'''
import sys, json, sqlite3, os
sys.path.insert(0, r"{root_b / 'scripts'}")
sys.path.insert(0, r"{root_b}")
os.chdir(r"{root_b}")
import orchestrator as O
from utils.progress_manager import ProgressManager

class FailStage:
    @staticmethod
    def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
        # 阶段自己"烧掉"超预算的钱，然后失败 → 重试循环内的熔断检查命中
        cost.db.log_cost(run_id, 1, 0, "glm-5", 1000, 1000, 999.0)
        return False, "模拟阶段失败"

O.STAGES = {{n: FailStage for n in range(1, 10)}}
O.load_config = lambda: ({{"gates": {{"auto_retry": True, "auto_retry_max_rounds": 2,
                                     "auto_retry_delay_seconds": 0, "auto_retry_backoff": 1,
                                     "pause_on_failure": True, "require_approval": []}},
                          "budget": {{"limit_yuan": 1, "warn_ratio": 0.7}}}},
                         {{"book": {{"name": "失败路径测试书"}}}})
O.snap = type("S", (), {{"snapshot": staticmethod(lambda lbl: None)}})()
O._stop_requested = lambda: False

err = None
try:
    rc = O.run(from_stage=1, only_stage=1)
except Exception as e:
    rc = None; err = type(e).__name__ + ": " + str(e)
con = sqlite3.connect(r"{root_b / 'logs' / 'runs.db'}")
rows = con.execute("SELECT id, status FROM runs").fetchall()
con.close()
pm = ProgressManager(r"{root_b / 'data' / 'state' / 'progress.json'}")
print("__RESULT__" + json.dumps({{
    "rc": rc, "err": err, "rows": rows,
    "paused": bool((pm.data.get("budget") or {{}}).get("paused")),
}}))
'''
    pb, outb, errb = run_py(root_b, code_b)
    if pb is None:
        check(False, "F3b 子进程产出结果", f"stdout={outb[-500:]} stderr={errb[-500:]}")
    else:
        print(f"  退出码={pb['rc']} 异常={pb['err']} runs={pb['rows']}")
        check(pb["err"] is None, "F3b 熔断路径无异常", f"实际={pb['err']}")
        check(pb["rc"] == 2, "F3b 退出码 2（重试循环内熔断）", f"实际={pb['rc']}")
        check(pb["paused"] is True, "F3b budget.paused 置位", f"实际={pb['paused']}")
        check([r[1] for r in pb["rows"]][-1] == "paused",
              "F3b runs 末行状态为 paused", f"实际={pb['rows']}")


# ====================================================================== F4
def case_f4():
    """F4 重试前用户停止 → exit=4 + stopped。"""
    print("\n【F4】重试前用户停止 → exit=4")
    root = build_sandbox("nf_f4_")
    code = f'''
import sys, json, sqlite3, os
sys.path.insert(0, r"{root / 'scripts'}")
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")
import orchestrator as O

CALLS = {{"n": 0}}
class AlwaysFailStage:
    @staticmethod
    def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
        CALLS["n"] += 1
        return False, "模拟阶段失败"

O.STAGES = {{n: AlwaysFailStage for n in range(1, 10)}}
O.load_config = lambda: ({{"gates": {{"auto_retry": True, "auto_retry_max_rounds": 2,
                                     "auto_retry_delay_seconds": 0, "auto_retry_backoff": 1,
                                     "pause_on_failure": True, "require_approval": []}},
                          "budget": {{"limit_yuan": 300, "warn_ratio": 0.7}}}},
                         {{"book": {{"name": "失败路径测试书"}}}})
O.snap = type("S", (), {{"snapshot": staticmethod(lambda lbl: None)}})()
# 首次失败后、进入重试循环前的停止检查即返回 True
O._stop_requested = lambda: True

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
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "F4 子进程产出结果", f"stdout={out[-500:]} stderr={errout[-500:]}")
        return
    print(f"  退出码={p['rc']} 异常={p['err']} 阶段调用={p['calls']} runs={p['rows']}")
    check(p["err"] is None, "F4 停止路径无异常", f"实际={p['err']}")
    check(p["rc"] == 4, "F4 退出码 4（用户停止）", f"实际={p['rc']}")
    statuses = [r[1] for r in p["rows"]]
    check(statuses and statuses[-1] == "stopped", "F4 runs 末行状态为 stopped",
          f"实际={statuses}")
    check("running" not in statuses, "F4 runs 无残留 running", f"实际={statuses}")
    # 初始阶段开始前检查 _stop_requested() 即返回 True → 阶段一次都没启动。
    # 这比「跑完再停」更强：停止生效在最前，不产生任何额外花费。
    check(p["calls"] == 0, "F4 停止请求在阶段启动前即生效（零额外调用）",
          f"实际={p['calls']}")


# ====================================================================== F5
def case_f5():
    """F5 stage4 逐章失败隔离：一章失败不拖垮整阶段；重试后成功则阶段完成。"""
    print("\n【F5】stage4 逐章失败隔离 + 重试后成功")
    root = build_sandbox("nf_f5_")
    code = f'''
import sys, json, os, shutil
sys.path.insert(0, r"{root / 'scripts'}")
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")
import yaml
from utils.file_io import read_text, write_text
from utils.progress_manager import ProgressManager
from utils.failing_client import FailingClient
import stage4_writing as s4

cfg = yaml.safe_load(read_text("config/system.yaml"))
proj = yaml.safe_load(read_text("config/project.yaml"))
proj["book"]["chapters"] = 2

# 造两章大纲，让 stage4 有活可干
for n in (1, 2):
    write_text("data/outline/chapters/%02d.md" % n,
               "# 第%d章 大纲（fake）\\n\\n- 核心事件：推进\\n- 涉及角色：测试角色\\n- 功能：承接\\n" % n)
write_text("data/setting/setting.json", '{{"characters":[],"world":{{}},"plot_fragments":[],"timeline":[]}}')

pm = ProgressManager("data/state/progress.json")

# ① 第 1 章失败：只让 stage4 的第 1 次调用失败 → 第1章 failed、第2章成功
c1 = FailingClient(fail_on=["stage4"], fail_times=1)
ok1, msg1 = s4.run_stage(cfg, proj, pm, None, None, client=c1, task_dir="data/state/tasks")
failed1 = pm.failed_chapters(4)
print("[case5-1] ok=%s msg=%s failed=%s" % (ok1, msg1, failed1))

# ② 重跑：客户端现在正常，失败的章节应被补写
c2 = FailingClient()   # 不启用失败
c2.fail_on = []        # 全部放行（等价 FakeClient）
ok2, msg2 = s4.run_stage(cfg, proj, pm, None, None, client=c2, task_dir="data/state/tasks")
failed2 = pm.failed_chapters(4)
raw = sorted(p.name for p in __import__("pathlib").Path("data/chapters/raw").glob("*.md"))
print("__RESULT__" + json.dumps({{
    "ok1": ok1, "msg1": msg1, "failed1": failed1,
    "ok2": ok2, "msg2": msg2, "failed2": failed2, "raw": raw,
}}))
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "F5 子进程产出结果", f"stdout={out[-800:]} stderr={errout[-800:]}")
        return
    print(f"  ① ok={p['ok1']} msg={p['msg1']} failed={p['failed1']}")
    print(f"  ② ok={p['ok2']} msg={p['msg2']} failed={p['failed2']} raw={p['raw']}")
    check(p["ok1"] is False, "F5 有章节失败时阶段返回 False（不谎报成功）")
    # failed_chapters 返回 dict 列表（n/error/attempts），断言章号集合而非对象相等
    check([f["n"] for f in p["failed1"]] == [1],
          "F5 仅第 1 章被标记失败（失败被精确隔离）",
          f"实际={p['failed1']}")
    check(p["failed1"] and "退出码" in (p["failed1"][0].get("error") or ""),
          "F5 失败原因被记录为「子会话退出码非零」（可诊断）",
          f"实际={p['failed1']}")
    check(p["failed1"][0].get("attempts") == 1, "F5 失败尝试次数被记录",
          f"实际={p['failed1']}")
    check("01.md" in p["raw"], "F5 第 2 章仍被正常写出（单章失败不阻断其余章节）",
          f"实际={p['raw']}")
    check(p["ok2"] is True, "F5 重试后全部成功 → 阶段返回 True")
    check(p["failed2"] == [], "F5 失败清单被清空", f"实际={p['failed2']}")
    check(p["raw"] == ["01.md", "02.md"], "F5 两章产物齐备", f"实际={p['raw']}")


# ====================================================================== F6
def case_f6():
    """F6 静态门禁：等价 ruff --select F821，零 undefined name。"""
    print("\n【F6】未定义名静态检测（ruff F821 等价门禁）")
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "quality_gate.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO))
    out = (proc.stdout or "") + (proc.stderr or "")
    print("  " + out.strip().replace("\n", "\n  ")[-600:])
    check(proc.returncode == 0, "F6 质量门禁退出码 0（BLOCK 为 0）",
          f"实际={proc.returncode}")
    check("BLOCK（未定义名）: 0" in out, "F6 报告「未定义名: 0」",
          out[-300:])
    # 反向验证：把缺陷塞进一个真文件，门禁必须变红
    probe = REPO / "scripts" / "_gate_probe.py"
    try:
        probe.write_text("# -*- coding: utf-8 -*-\n"
                         "def f():\n    return undefined_symbol_xyz\n",
                         encoding="utf-8")
        proc2 = subprocess.run(
            [sys.executable, "-m", "pyflakes", str(probe)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("undefined name" in (proc2.stdout or ""),
              "F6 反向验证：注入未定义名后门禁能检出（不是空门禁）",
              f"实际输出={proc2.stdout!r}")
    finally:
        probe.unlink(missing_ok=True)


# ====================================================================== F7
def case_f7():
    """F7 seedWorkspace 升级：旧工作区升级后 scripts/prompts 被刷新。

    这是**Node 侧**逻辑（console/main/index.js），用 node 子进程 +
    临时 AppData 目录验证，不启动 Electron GUI。
    """
    print("\n【F7】seedWorkspace 升级刷新（旧 .seeded 工作区）")
    src = (REPO / "console" / "main" / "index.js").read_text(encoding="utf-8")

    # 静态检查：源码层面必须同时具备「版本标记」与「刷新」能力
    has_version_marker = "SEED_VERSION" in src and ".seed-version" in src
    has_unconditional_return = 'if (fs.existsSync(path.join(ws, ".seeded"))) return;' in src
    check(has_version_marker,
          "F7 存在种子版本标记（可判断是否需要刷新）",
          "未找到 SEED_VERSION / .seed-version")
    check(not has_unconditional_return,
          "F7 seedWorkspace 不再在 .seeded 存在时无条件提前返回（老工作区可升级）",
          "仍存在旧的 early-return 写法")
    check("data" not in re.search(r"SEED_CODE_DIRS\s*=\s*\[[^\]]*\]", src or "").group(0)
          if re.search(r"SEED_CODE_DIRS\s*=\s*\[[^\]]*\]", src) else False,
          "F7 刷新目录白名单**不含** data/（用户产物永不被覆盖）",
          "SEED_CODE_DIRS 里出现了 data")

    # 行为验证：抽取真实函数体到 node 里跑（不启动 Electron）
    node = shutil.which("node")
    if not node:
        check(True, "F7 跳过 node 行为验证（环境无 node）")
        return

    fn_start = src.find("function seedWorkspace()")
    fn_end = src.find("\n}", fn_start)
    check(fn_start > 0 and fn_end > fn_start,
          "F7 能从 index.js 抽取 seedWorkspace 函数体")
    if fn_start < 0 or fn_end < fn_start:
        return
    fn_body = src[fn_start:fn_end + 2]

    probe = Path(tempfile.mkdtemp(prefix="nf_seed_"))
    try:
        # payload：新版代码 + 新版提示词 + config
        (probe / "scripts").mkdir(parents=True, exist_ok=True)
        (probe / "scripts" / "orchestrator.py").write_text("# NEW-CODE\n", encoding="utf-8")
        (probe / "prompts").mkdir(exist_ok=True)
        (probe / "prompts" / "stage4.md").write_text("NEW-PROMPT\n", encoding="utf-8")
        (probe / "config").mkdir(exist_ok=True)
        (probe / "config" / "system.yaml").write_text("engine: new\n", encoding="utf-8")
        (probe / "config" / "new_feature.yaml").write_text("added: true\n",
                                                          encoding="utf-8")

        ws = probe / "workspace"
        # 模拟老工作区：旧代码 + 用户改过的 config + 用户 data + 旧版本标记
        (ws / "scripts").mkdir(parents=True, exist_ok=True)
        (ws / "scripts" / "orchestrator.py").write_text("# OLD-CODE\n", encoding="utf-8")
        (ws / "config").mkdir(parents=True, exist_ok=True)
        (ws / "config" / "project.yaml").write_text("book:\n  name: 我的书\n",
                                                    encoding="utf-8")
        (ws / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
        (ws / "data" / "chapters" / "raw" / "01.md").write_text("用户章节\n",
                                                              encoding="utf-8")
        (ws / ".seeded").write_text("1", encoding="utf-8")
        (ws / ".seed-version").write_text("1", encoding="utf-8")   # 落后于当前版本

        script = probe / "run.cjs"
        script.write_text(
            "const fs = require('fs');\n"
            "const path = require('path');\n"
            f"const ROOT = {json.dumps(str(probe)).replace(chr(92), '/')};\n"
            f"const WORKSPACE = {json.dumps(str(ws)).replace(chr(92), '/')};\n"
            "const isPackaged = false;\n"
            "const app = { getPath: () => WORKSPACE };\n"
            "process.resourcesPath = ROOT;\n"
            "function getWorkspaceDir() { return WORKSPACE; }\n"
            + re.search(r"const SEED_VERSION = \d+;", src).group(0) + "\n"
            + re.search(r"const SEED_CODE_DIRS = \[[^\]]*\];", src).group(0) + "\n"
            + re.search(r"const SEED_CONFIG_DIR = \"[^\"]*\";", src).group(0) + "\n"
            + fn_body + "\n"
            "seedWorkspace();\n"
            "const rd = (p) => { try { return fs.readFileSync(p, 'utf8').trim(); }"
            " catch (e) { return '<缺失>'; } };\n"
            "console.log('__RESULT__' + JSON.stringify({\n"
            "  orch: rd(path.join(WORKSPACE, 'scripts', 'orchestrator.py')),\n"
            "  prompt: rd(path.join(WORKSPACE, 'prompts', 'stage4.md')),\n"
            "  userConfig: rd(path.join(WORKSPACE, 'config', 'project.yaml')),\n"
            "  newConfig: rd(path.join(WORKSPACE, 'config', 'new_feature.yaml')),\n"
            "  userChapter: rd(path.join(WORKSPACE, 'data', 'chapters', 'raw', '01.md')),\n"
            "  seedVersion: rd(path.join(WORKSPACE, '.seed-version')),\n"
            "}));\n",
            encoding="utf-8")
        proc = subprocess.run([node, str(script)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
        out = proc.stdout or ""
        if "__RESULT__" not in out:
            check(False, "F7 node 探测产出结果",
                  f"stdout={out[-400:]} stderr={(proc.stderr or '')[-400:]}")
            return
        payload = json.loads(out.split("__RESULT__", 1)[1].splitlines()[0])
        print(f"  代码={payload['orch']!r} 提示词={payload['prompt']!r} "
              f"用户config={payload['userConfig']!r}")
        print(f"  新增config={payload['newConfig']!r} 用户章节={payload['userChapter']!r} "
              f"版本={payload['seedVersion']!r}")
        check(payload["orch"] == "# NEW-CODE",
              "F7 升级后 scripts 被刷新为新版（老用户拿到新代码）",
              f"实际={payload['orch']!r}")
        check(payload["prompt"] == "NEW-PROMPT", "F7 升级后 prompts 被刷新",
              f"实际={payload['prompt']!r}")
        check(payload["userConfig"].startswith("book:"),
              "F7 用户改过的 config/project.yaml **不被覆盖**（书名不丢）",
              f"实际={payload['userConfig']!r}")
        check(payload["newConfig"] == "added: true",
              "F7 版本新增的配置文件被补充到位",
              f"实际={payload['newConfig']!r}")
        check(payload["userChapter"] == "用户章节",
              "F7 升级刷新绝不动用户 data/（不丢稿）",
              f"实际={payload['userChapter']!r}")
        check(payload["seedVersion"] == "2",
              "F7 版本标记被写为当前种子版本（下次启动不再重复刷新）",
              f"实际={payload['seedVersion']!r}")

        # 幂等性：再跑一次，用户若是自己改过代码则不应被二次刷新
        (ws / "scripts" / "orchestrator.py").write_text("# USER-PATCHED\n",
                                                       encoding="utf-8")
        proc2 = subprocess.run([node, str(script)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
        out2 = proc2.stdout or ""
        if "__RESULT__" in out2:
            payload2 = json.loads(out2.split("__RESULT__", 1)[1].splitlines()[0])
            check(payload2["orch"] == "# USER-PATCHED",
                  "F7 版本已是最新时不再覆盖（幂等：不反复回滚用户改动）",
                  f"实际={payload2['orch']!r}")
    finally:
        shutil.rmtree(probe, ignore_errors=True)


# ====================================================================== F8
def case_f8():
    """F8 approve/reject 审批门：打回后下游产物被清、状态重置。"""
    print("\n【F8】approve/reject 审批门（打回清下游）")
    root = build_sandbox("nf_f8_")
    code = f'''
import sys, json, os
sys.path.insert(0, r"{root / 'scripts'}")
sys.path.insert(0, r"{root}")
os.chdir(r"{root}")
from pathlib import Path
from utils.file_io import write_text
from utils.progress_manager import ProgressManager
import approve as A
import reject as R

# 造 stage5 完成态 + 下游产物
for d in ("data/chapters/checked", "data/chapters/refined", "output"):
    Path(d).mkdir(parents=True, exist_ok=True)
    write_text(d + "/01.md", "产物\\n")
write_text("output/书.docx", "docx")

pm = ProgressManager("data/state/progress.json")
for n in (5, 6, 7):
    pm.data["stages"][str(n)]["status"] = "done"
pm.data["stages"]["5"]["approved"] = True
pm.data["stages"]["5"]["skipped"] = True          # 人工跳过标记也应在打回时清掉
pm.save()

before = {{
    "checked": Path("data/chapters/checked").exists(),
    "refined": Path("data/chapters/refined").exists(),
    "output": Path("output").exists(),
    "st5_approved": pm.data["stages"]["5"].get("approved"),
}}
# 打回 stage5（真实执行，非 dry-run）
ok, msgs = R.reject_stage(pm, 5, "测试打回", dry_run=False)
pm2 = ProgressManager("data/state/progress.json")
print("__RESULT__" + json.dumps({{
    "ok": ok, "msgs": msgs[-4:],
    "before": before,
    "after": {{
        "checked": Path("data/chapters/checked").exists(),
        "refined": Path("data/chapters/refined").exists(),
        "output": Path("output").exists(),
        "st5_status": pm2.data["stages"]["5"].get("status"),
        "st5_approved": pm2.data["stages"]["5"].get("approved"),
        "st5_skipped": pm2.data["stages"]["5"].get("skipped"),
        "st5_rejected": bool(pm2.data["stages"]["5"].get("rejected")),
        "st6_status": pm2.data["stages"]["6"].get("status"),
        "st7_status": pm2.data["stages"]["7"].get("status"),
    }},
}}, ensure_ascii=False))
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "F8 子进程产出结果", f"stdout={out[-800:]} stderr={errout[-800:]}")
        return
    b, a = p["before"], p["after"]
    print(f"  打回前: {b}")
    print(f"  打回后: {a}")
    check(p["ok"] is True, "F8 reject_stage 返回成功")
    check(b["checked"] and b["refined"] and b["output"], "F8 前置：下游产物确实存在")
    check(not a["refined"], "F8 stage5 打回 → refined/ 被清（下游产物清理）")
    check(not a["output"], "F8 stage5 打回 → output/ 被清")
    # 注意：reject(5) 清掉了 checked —— 这是**符合设计**的。
    # reject 的语义是「重跑 stage5」，若 checked 保留，stage5 的断点逻辑
    # （`pending = [f for f in raw_files if not (checked/f.name).exists()]`）
    # 会把全部章节判为"已检查"而直接跳过 → 打回变成空操作。清掉才能真重跑。
    check(not a["checked"],
          "F8 stage5 打回 → checked/ 一并清除（否则断点逻辑会让重跑变空操作）",
          f"实际={a['checked']}")
    check(a["st5_status"] == "rejected", "F8 stage5 状态置为 rejected",
          f"实际={a['st5_status']}")
    check(a["st5_rejected"] is True, "F8 打回原因已记录（GUI 可显示）")
    check(a["st5_approved"] is None, "F8 stage5 审批标记被撤销（防误放行）",
          f"实际={a['st5_approved']}")
    check(a["st5_skipped"] is None, "F8 人工跳过标记被清（避免「已跳过+已打回」双徽标）",
          f"实际={a['st5_skipped']}")
    check(a["st6_status"] == "pending" and a["st7_status"] == "pending",
          "F8 下游阶段 6/7 状态被重置为 pending",
          f"实际=st6:{a['st6_status']} st7:{a['st7_status']}")

    # approve 侧：走**真实 CLI**（approve.py 无公开函数，只有 main()），
    # 比调私有接口更可信 —— 测的就是用户实际敲的那条命令。
    root2 = build_sandbox("nf_f8b_")
    sys.path.insert(0, str(REPO / "scripts"))
    from utils.progress_manager import ProgressManager
    proj_pm = ProgressManager(str(root2 / "data" / "state" / "progress.json"))
    proj_pm.data["stages"]["2"]["status"] = "done"
    proj_pm.save()

    def _approve_cmd(*extra):
        return subprocess.run(
            [sys.executable, str(root2 / "scripts" / "approve.py"), "--stage", "2", *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(root2), timeout=60)

    r_ap = _approve_cmd()
    pm_a = ProgressManager(str(root2 / "data" / "state" / "progress.json"))
    check(r_ap.returncode == 0, "F8 approve.py --stage 2 退出码 0",
          f"实际={r_ap.returncode} {(r_ap.stderr or '')[-200:]}")
    check(pm_a.is_approved(2) is True, "F8 审批后 is_approved(2) 为真")
    check(pm_a.stage_status(2) == "done",
          "F8 审批不改动阶段状态（只置审批位）", f"实际={pm_a.stage_status(2)}")

    r_rv = _approve_cmd("--revoke")
    pm_b = ProgressManager(str(root2 / "data" / "state" / "progress.json"))
    check(r_rv.returncode == 0, "F8 approve.py --revoke 退出码 0",
          f"实际={r_rv.returncode}")
    check(pm_b.is_approved(2) is False, "F8 撤销后审批位被清")


# ====================================================================== S9
def case_s9():
    """S9（本轮 F3 用例暴露的新缺陷）：阶段键集与编排遍历范围失同步。

    `STAGE_KEYS` 原为 1~7，而 `orchestrator` 默认遍历 `range(from_stage, 9)`
    → 阶段 7 跑完后进入阶段 8，`orchestrator.py:213` 的
    `progress.stage_status(8)` 抛 `KeyError: '8'`，异常穿透 `run()`，
    `runs` 被兜底标为 `crashed`。**默认全流程的最后一站必崩。**

    断言的是「阶段键集必须覆盖编排遍历范围」+「越界阶段不崩」两条行为。
    """
    print("\n【S9】阶段键集与编排遍历范围对齐（KeyError: '8' 回归）")
    sys.path.insert(0, str(REPO / "scripts"))
    from utils.progress_manager import STAGE_KEYS, ProgressManager

    # 编排侧的遍历上界（硬编码 9 = 阶段 8 是最后一站）。用文本抽取而非 import，
    # 避免拉起 orchestrator 的完整依赖链。
    orch_src = (REPO / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    m = re.search(r"range\(from_stage,\s*(\d+)\)", orch_src)
    check(m is not None, "S9 能从 orchestrator 抽到阶段遍历上界",
          "未匹配 range(from_stage, N)")
    if not m:
        return
    upper = int(m.group(1)) - 1          # range 右开 → 实际最大阶段号
    declared = max(int(k) for k in STAGE_KEYS)
    print(f"  orchestrator 最大阶段号 = {upper}；STAGE_KEYS 最大 = {declared}")
    check(declared >= upper,
          f"S9 STAGE_KEYS 覆盖编排最大阶段号（{declared} >= {upper}）",
          f"缺口：阶段 {declared + 1}..{upper} 不在键集内 → 跑到处必 KeyError")

    # 行为验证 1：原崩溃点不再抛异常
    tmp = Path(tempfile.mkdtemp(prefix="nf_s9_"))
    try:
        pm = ProgressManager(str(tmp / "progress.json"))
        crashed = None
        try:
            pm.stage_status(upper)
            pm.set_stage(upper, "done")
        except Exception as e:                              # noqa: BLE001
            crashed = f"{type(e).__name__}: {e}"
        check(crashed is None,
              f"S9 stage_status({upper}) / set_stage({upper}) 不再抛异常",
              f"实际={crashed}")
        check(pm.stage_status(upper) == "done",
              "S9 阶段 8 状态可正常读写", f"实际={pm.stage_status(upper)}")

        # 行为验证 2：纵深防御 —— 完全越界的阶段号自动登记而非崩溃
        out_of_range = None
        try:
            pm.stage_status(99)
        except Exception as e:                              # noqa: BLE001
            out_of_range = f"{type(e).__name__}: {e}"
        check(out_of_range is None,
              "S9 纵深防御：未知阶段号自动登记而非 KeyError（防未来再次失同步）",
              f"实际={out_of_range}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 行为验证 3：reject 下游重置范围必须覆盖到最大阶段号
    rej_src = (REPO / "scripts" / "reject.py").read_text(encoding="utf-8")
    m2 = re.search(r"for n in range\(stage \+ 1,\s*(\d+)\)", rej_src)
    check(m2 is not None, "S9 能从 reject.py 抽到下游重置上界")
    if m2:
        rej_upper = int(m2.group(1)) - 1
        print(f"  reject 下游重置最大阶段号 = {rej_upper}")
        check(rej_upper >= upper,
              f"S9 reject 下游重置覆盖全部阶段（{rej_upper} >= {upper}）",
              f"缺口：阶段 {rej_upper + 1}..{upper} 打回后不会被重置")


def main():
    print("=" * 72)
    print("F1~F8 失败路径回归（FakeClient 恒成功盲区）")
    print("=" * 72)
    for fn in (case_f1_f2, case_f3, case_f4, case_f5, case_f6, case_f7,
               case_f8, case_s9):
        try:
            fn()
        except Exception:
            print(f"\n  !! {fn.__name__} 执行异常：")
            traceback.print_exc()
            check(False, f"{fn.__name__} 不抛异常")
    print("\n" + "=" * 72)
    print(f"结果：PASS={PASS}  FAIL={FAIL}")
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
