# -*- coding: utf-8 -*-
"""stage4 长跑止损自检（2026-10-01 新增，纯 fake，零 LLM、零网络）。

## 为什么需要这套用例

`data/chapters/raw/` 是那种"铺满了才发现不对"的地方：模型挂了或提示词被改坏时，
stage4 原来会**一路把 100 章全跑完**，只在最后报一句"有 100 章失败"——
废稿已经全在盘上了。这正是"几十万字烂摊子"的成因。

本次加两道闸，本用例守它们真的会闸：

1. **连续失败止损**：连续 N 章失败 → 判定系统性故障 → 提前停（默认 N=3，可配，0=关）
2. **章间预算熔断**：orchestrator 只在**阶段之间**查预算，而 stage4 一章就能烧掉几元
   —— 阶段内不查的话，限额对"一次跑 100 章"这种最该保护的情形恰好失效

判据都是**行为级**的：看"到底跑了几章"，而不是看日志里有没有打印。

用法：python tests/unit/test_stage4_stoploss.py
"""
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.fake_client import FakeClient                # noqa: E402
from utils.progress_manager import ProgressManager      # noqa: E402
import stage4_writing as s4                             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


SETTING = {
    "characters": [{"id": "luxi", "name": "苏芷", "role": "主角",
                    "traits": ["冷静"], "source": "fake"}],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [], "timeline": [],
    "_meta": {"version": 1, "generated_from": ["fake"]},
}


class AlwaysFailClient:
    """永远失败的 client：用来制造"系统性故障"场景。"""

    def __init__(self):
        self.calls = 0

    def write_task(self, task_dir, name, content):
        Path(task_dir).mkdir(parents=True, exist_ok=True)
        p = Path(task_dir) / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def run_task(self, task_path, **kwargs):   # **kwargs：收下 session_id 等新参数（签名同构）
        self.calls += 1
        return {"exit_code": 1, "tokens": 10, "tokens_out": 5, "text": "", "model": "fake"}


class StubCost:
    """可控熔断：第 pause_after 次 charge 起返回 'pause'。"""

    def __init__(self, pause_after=1):
        self.calls = 0
        self.pause_after = pause_after

    def estimate_cost_yuan(self, *a, **kw):
        return 0.0

    def charge_cost(self, run_id, stage, chapter, result):
        self.calls += 1
        return "pause" if self.calls >= self.pause_after else "ok"

    def spent(self, run_id=None):
        return 999.0


def build_workspace(total=10, tag="ws"):
    tmp = Path(tempfile.mkdtemp(prefix="stage4_stop_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "止损测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 止损测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n" % n,
            encoding="utf-8")
    return tmp


def run_stage4(tmp, client, cost=None, gates_override=None):
    """在 tmp 为 cwd 的前提下跑一次 stage4。返回 (ok, msg, progress, client)。"""
    import yaml
    os.chdir(tmp)
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    if gates_override:
        cfg.setdefault("gates", {}).update(gates_override)
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s4.run_stage(cfg, proj, progress, None, cost,
                           client=client,
                           task_dir=str(tmp / "data" / "state" / "tasks"))
    return ok, msg, progress, client


def case_consecutive_failures_stop():
    print("\n【1】连续失败止损：坏掉了不许一路跑完")
    tmp = build_workspace(total=10, tag="stop")
    origin = os.getcwd()
    try:
        c = AlwaysFailClient()
        ok, msg, progress, _ = run_stage4(tmp, c)
        check("连续失败 → 阶段判失败", ok is False, msg)
        check("消息点明「提前止损」而非只报一句失败数",
              "提前止损" in msg, msg)
        check("**只跑了 3 章就停**（默认阈值 3，不是 10）",
              c.calls == 3, "实际调用 %d 次" % c.calls)
        check("已失败的章节被记录（供断点续跑重试）",
              len(progress.failed_chapters(4)) == 3,
              progress.failed_chapters(4))
        check("阶段状态为 failed", progress.stage_status(4) == "failed",
              progress.stage_status(4))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_threshold_configurable():
    print("\n【2】阈值可配：0 = 关闭保护（跑完全部）")
    tmp = build_workspace(total=4, tag="off")
    origin = os.getcwd()
    try:
        c = AlwaysFailClient()
        ok, msg, _, _ = run_stage4(tmp, c, gates_override={
            "stage4_max_consecutive_failures": 0})
        check("阈值 0 → 不提前止损（跑完 4 章）", c.calls == 4,
              "实际调用 %d 次" % c.calls)
        check("跑完后仍因有失败章而返回 False（不是假成功）", ok is False, msg)
        check("消息里**不含**「提前止损」", "提前止损" not in msg, msg)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_budget_circuit_breaker_inside_stage():
    print("\n【3】章间预算熔断：限额对「一次跑几十章」也要生效")
    tmp = build_workspace(total=5, tag="budget")
    origin = os.getcwd()
    try:
        c = FakeClient()
        cost = StubCost(pause_after=1)      # 第 1 章成功后就熔断
        ok, msg, progress, _ = run_stage4(tmp, c, cost=cost)
        check("熔断后阶段返回 False", ok is False, msg)
        check("消息点明「预算熔断」", "预算熔断" in msg, msg)
        check("**熔断在阶段内就生效**（没有把 5 章跑完）",
              cost.calls == 1, "charge 调用 %d 次" % cost.calls)
        check("progress 标记预算暂停（orchestrator 下次启动即刻熔断）",
              bool(progress.data.get("budget", {}).get("paused")),
              progress.data.get("budget"))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_success_path_unaffected():
    print("\n【4】反证：正常路径不受影响（保护不能变成 Always-Break）")
    tmp = build_workspace(total=3, tag="ok")
    origin = os.getcwd()
    try:
        c = FakeClient()
        ok, msg, progress, _ = run_stage4(tmp, c)
        check("全成功 → 阶段 done", ok is True, msg)
        check("消息为完成态（不含止损/熔断字样）",
              "提前止损" not in msg and "熔断" not in msg, msg)
        check("3 章都落盘", len(list((tmp / "data" / "chapters" / "raw").glob("*.md"))) == 3,
              sorted(p.name for p in (tmp / "data" / "chapters" / "raw").glob("*.md")))
        check("stage4 状态为 done", progress.stage_status(4) == "done",
              progress.stage_status(4))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("  stage4 长跑止损自检（离线）")
    print("=" * 62)
    case_consecutive_failures_stop()
    case_threshold_configurable()
    case_budget_circuit_breaker_inside_stage()
    case_success_path_unaffected()
    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
