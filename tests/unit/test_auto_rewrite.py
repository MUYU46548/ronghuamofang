# -*- coding: utf-8 -*-
"""方向3 质量自评闭环自检（纯 fake，零 LLM、零网络）。

在**临时工作目录**里跑（复制 prompts/），避免污染真实 data/ 产物。
覆盖：
- collect_targets：优先 progress.needs_rewrite、兜底重扫 quality 注释、达标项自动剔除
- dry-run：不改章节内容（quality 不变），仍生成任务文件
- 完整重写：低分章 → quality 提升、needs_rewrite 清理、双份报告产出、±20% 通过
- 幂等：重跑不产生二次重写
- 轮次上限：达 max_rounds 后跳过并转人工
- 预算熔断：cost=pause 立即中止（不烧钱）
- build_feedback：叠加 review_report 的确定性发现
- orchestrator 开关：auto_rewrite 与 auto_refine 同时开启时告警

用法：python tests/test_auto_rewrite.py
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import auto_rewrite as ar                          # noqa: E402
import orchestrator as orch                        # noqa: E402
from utils.fake_client import FakeClient           # noqa: E402
from utils.progress_manager import ProgressManager  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


# ------------------------------------------------------------------ 工作区

def build_workspace(tag="ws"):
    tmp = Path(tempfile.mkdtemp(prefix="auto_rewrite_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts")
    (tmp / "data").mkdir()
    (tmp / "data" / "outline").mkdir(parents=True)
    return tmp


def write_chapter(tmp, n, quality, paras=80):
    d = tmp / "data" / "chapters" / "raw"
    d.mkdir(parents=True, exist_ok=True)
    body = ("## 第%d章 测试章节\n\n" % n + "测试正文。" * paras + "\n\n"
            "<!-- quality: %s/10 -->\n<!-- summary: 测试摘要。 -->\n" % quality)
    (d / ("%02d.md" % n)).write_text(body, encoding="utf-8")


def make_progress(tmp):
    return ProgressManager(str(tmp / "data" / "state" / "progress.json"))


def cfg_of(tmp=None):
    import yaml
    return yaml.safe_load((ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))


def run_auto(tmp, progress, chapters=None, dry_run=False, max_rounds=None,
             threshold=6, cost=None, run_id=None, write_report_files=True):
    cfg = cfg_of()
    proj = {"book": {"name": "测试书", "chapters": 3}}
    return ar.run_auto_rewrite(
        cfg, proj, progress, db=None, cost=cost, run_id=run_id,
        threshold=threshold, max_rounds=max_rounds, dry_run=dry_run,
        client=FakeClient(), task_dir=str(tmp / "data" / "state" / "tasks"),
        chapters=chapters, write_report_files=write_report_files)


class PauseCost:
    """模拟预算熔断。"""

    def status(self, run_id=None):
        return "pause", 999.0

    def charge_cost(self, *a, **k):
        return "pause"


# ------------------------------------------------------------------ 用例

def case_collect_targets():
    print("\n=== 1. collect_targets：优先 / 兜底 / 达标剔除 ===")
    tmp = build_workspace("collect")
    try:
        os.chdir(tmp)
        write_chapter(tmp, 1, 5)
        write_chapter(tmp, 2, 8)
        write_chapter(tmp, 3, 4)
        progress = make_progress(tmp)

        check("纯兜底重扫：无 needs_rewrite 也能找到低分章",
              ar.collect_targets(progress, threshold=6) == [1, 3],
              ar.collect_targets(progress, threshold=6))

        progress.data["stages"]["4"]["needs_rewrite"] = [2, 4]
        progress.save()
        check("needs_rewrite 里已达标(2)/不存在(4)的章被剔除",
              ar.collect_targets(progress, threshold=6) == [1, 3],
              ar.collect_targets(progress, threshold=6))

        check("--chapters 限定生效",
              ar.collect_targets(progress, threshold=6, only=[3]) == [3])
        check("阈值放宽到 9 → 三章全收（含 quality 8）",
              ar.collect_targets(progress, threshold=9) == [1, 2, 3])
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_dry_run():
    print("\n=== 2. dry-run：不改章节内容，仍生成任务 ===")
    tmp = build_workspace("dry")
    try:
        os.chdir(tmp)
        write_chapter(tmp, 1, 5)
        progress = make_progress(tmp)
        before = (tmp / "data/chapters/raw/01.md").read_text(encoding="utf-8")

        ok, msg, stats = run_auto(tmp, progress, chapters=[1], dry_run=True)
        after = (tmp / "data/chapters/raw/01.md").read_text(encoding="utf-8")

        check("dry-run 成功返回", ok, msg)
        check("dry-run 不改章节正文/quality", before == after)
        check("dry-run 章节 quality 仍为 5",
              ar.parse_quality(tmp / "data/chapters/raw/01.md") == 5.0)
        check("dry-run 仍生成精修任务文件",
              (tmp / "data/state/tasks/batch_refine_ch01.md").exists())
        check("dry-run 不写 auto_rewritten（不消耗轮次）",
              "auto_rewritten" not in progress.data["stages"]["4"])
        check("dry-run 结果状态标记 dry_run",
              stats["items"] and stats["items"][0]["status"] == "dry_run",
              stats["items"])
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_full_rewrite():
    print("\n=== 3. 完整重写：提质 + 清标记 + 双份报告 + ±20% 通过 ===")
    tmp = build_workspace("full")
    try:
        os.chdir(tmp)
        write_chapter(tmp, 1, 4)
        write_chapter(tmp, 3, 5)
        progress = make_progress(tmp)
        progress.data["stages"]["4"]["needs_rewrite"] = [1, 3]
        progress.save()

        ok, msg, stats = run_auto(tmp, progress)
        check("重写成功", ok, msg)
        check("两章 quality 均提升至 8",
              ar.parse_quality(tmp / "data/chapters/raw/01.md") == 8.0
              and ar.parse_quality(tmp / "data/chapters/raw/03.md") == 8.0)
        check("needs_rewrite 已清空（达标章移除）",
              progress.data["stages"]["4"].get("needs_rewrite") == [],
              progress.data["stages"]["4"].get("needs_rewrite"))
        check("auto_rewritten 记录轮次 {1:1,3:1}",
              progress.data["stages"]["4"].get("auto_rewritten") == {"1": 1, "3": 1},
              progress.data["stages"]["4"].get("auto_rewritten"))
        check("字数变化在 ±20% 内（fake 保留字数 → 0%）",
              all(abs(r["delta_pct"]) <= 20 for r in stats["items"]), stats["items"])
        check("history 备份已生成（可回退）",
              (tmp / "data/chapters/history/ch01_v1.md").exists()
              and (tmp / "data/chapters/history/ch03_v1.md").exists())
        check("人可读报告已追加",
              (tmp / "data/outline/auto_rewrite_report.md").exists())
        rj = tmp / "data/outline/auto_rewrite_report.json"
        check("机器可读报告已生成", rj.exists())
        if rj.exists():
            doc = json.loads(rj.read_text(encoding="utf-8"))
            check("JSON 含 latest + runs，且 2 条 ok",
                  doc.get("latest", {}).get("items")
                  and len(doc["latest"]["items"]) == 2
                  and all(i["status"] == "ok" for i in doc["latest"]["items"]),
                  doc.get("latest", {}).get("items"))
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_idempotent():
    print("\n=== 4. 幂等：重跑不产生二次重写 ===")
    tmp = build_workspace("idem")
    try:
        os.chdir(tmp)
        write_chapter(tmp, 1, 4)
        progress = make_progress(tmp)
        run_auto(tmp, progress)
        stamp1 = (tmp / "data/chapters/raw/01.md").stat().st_mtime_ns
        rounds1 = dict(progress.data["stages"]["4"]["auto_rewritten"])

        ok, msg, stats = run_auto(tmp, progress)
        stamp2 = (tmp / "data/chapters/raw/01.md").stat().st_mtime_ns
        check("二次运行无待重写章节", "无待重写章节" in msg, msg)
        check("章节文件未被再次改写", stamp1 == stamp2)
        check("auto_rewritten 轮次未叠加",
              progress.data["stages"]["4"]["auto_rewritten"] == rounds1,
              progress.data["stages"]["4"]["auto_rewritten"])
        check("未追加新的 history 备份（只有 v1）",
              sorted(p.name for p in (tmp / "data/chapters/history").glob("ch01_v*.md"))
              == ["ch01_v1.md"])
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_max_rounds():
    print("\n=== 5. 轮次上限：达上限跳过并转人工 ===")
    tmp = build_workspace("rounds")
    try:
        os.chdir(tmp)
        write_chapter(tmp, 1, 5)
        progress = make_progress(tmp)
        progress.data["stages"]["4"]["auto_rewritten"] = {"1": 1}
        progress.data["stages"]["4"]["needs_rewrite"] = [1]
        progress.save()

        ok, msg, stats = run_auto(tmp, progress, max_rounds=1)
        check("达上限不重写（章节 quality 保持 5）",
              ar.parse_quality(tmp / "data/chapters/raw/01.md") == 5.0)
        check("记录为 skipped 且进入 unresolved",
              stats["skipped"] and stats["unresolved"] == [1], stats)
        check("needs_rewrite 保留（转人工）",
              progress.data["stages"]["4"].get("needs_rewrite") == [1])
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_budget_pause():
    print("\n=== 6. 预算熔断：立即中止，不烧钱 ===")
    tmp = build_workspace("budget")
    try:
        os.chdir(tmp)
        write_chapter(tmp, 1, 5)
        write_chapter(tmp, 2, 5)
        progress = make_progress(tmp)

        ok, msg, stats = run_auto(tmp, progress, cost=PauseCost(), run_id=1)
        check("记录熔断原因", bool(stats.get("aborted")), stats.get("aborted"))
        check("熔断时未改写任何章节",
              ar.parse_quality(tmp / "data/chapters/raw/01.md") == 5.0
              and ar.parse_quality(tmp / "data/chapters/raw/02.md") == 5.0)
        check("无 items（第一章前即中止）", stats["items"] == [], stats["items"])
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_build_feedback():
    print("\n=== 7. build_feedback：叠加审稿发现 ===")
    tmp = build_workspace("fb")
    try:
        os.chdir(tmp)
        (tmp / "data/outline/review_report.json").write_text(json.dumps({
            "chapters": [{"n": 1, "findings": [
                {"id": "f001", "type": "outline_gap", "severity": "error",
                 "detail": "核心事件被处理为回忆", "suggested_action": "改为当下发生"},
                {"id": "f002", "type": "other", "severity": "info", "detail": ""},
            ]}]
        }, ensure_ascii=False), encoding="utf-8")
        fb = ar.build_feedback(1, 4, 6)
        check("含阈值与自评分", "低于阈值 6" in fb and "4/10" in fb)
        check("含 ±20% 铁律", "±20%" in fb)
        check("叠加审稿发现 detail + 建议",
              "核心事件被处理为回忆" in fb and "改为当下发生" in fb, fb[:200])
        check("空 detail 的发现被跳过", fb.count("- [") == 1, fb.count("- ["))
        check("无报告章节时仅返回默认模板",
              "另需一并处理" not in ar.build_feedback(2, 5, 6))
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


def case_orchestrator_gate():
    print("\n=== 8. orchestrator 开关与冲突告警 ===")
    cfg = cfg_of()
    check("配置默认关闭 auto_rewrite",
          cfg.get("gates", {}).get("auto_rewrite") is False,
          cfg.get("gates", {}).get("auto_rewrite"))
    check("auto_rewrite_max_rounds 默认 1",
          cfg.get("gates", {}).get("auto_rewrite_max_rounds") == 1)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        orch._warn_rewrite_conflict({"gates": {"auto_rewrite": True, "auto_refine": True}})
    check("两开关同开 → 打印冲突告警", "同时开启" in buf.getvalue(), buf.getvalue()[:120])

    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        orch._warn_rewrite_conflict({"gates": {"auto_rewrite": True, "auto_refine": False}})
    check("仅开 auto_rewrite → 不告警", buf2.getvalue() == "")

    src = (ROOT / "scripts/orchestrator.py").read_text(encoding="utf-8")
    i_ar = src.find('gates", {}) or {}).get("auto_rewrite"')
    i_rev = src.find('review_after_stage4", False')
    check("auto_rewrite 排在审稿分支之前（F5 排队）",
          -1 < i_ar < i_rev, (i_ar, i_rev))


def case_orchestrator_hook():
    print("\n=== 9. orchestrator n==4 钩子：开关真的接线 ===")
    for gate, expect in ((True, 1), (False, 0)):
        tmp = build_workspace("hook%s" % gate)
        try:
            shutil.copytree(ROOT / "config", tmp / "config",
                            ignore=shutil.ignore_patterns("history"))
            cfgp = tmp / "config" / "system.yaml"
            cfgp.write_text(
                cfgp.read_text(encoding="utf-8")
                    .replace("review_after_stage4: true", "review_after_stage4: false")
                    .replace("auto_rewrite: false", "auto_rewrite: true" if gate
                             else "auto_rewrite: false"),
                encoding="utf-8")
            (tmp / "logs").mkdir()
            os.chdir(tmp)

            calls = []
            orig_ar, orig_s4 = ar.run_auto_rewrite, orch.s4.run_stage

            def fake_ar(*a, **k):
                calls.append(1)
                return True, "hooked", {}

            ar.run_auto_rewrite = fake_ar
            orch.s4.run_stage = lambda *a, **k: (True, "stage4 fake ok")
            try:
                rc = orch.run(from_stage=4, only_stage=4, client=object())
            finally:
                ar.run_auto_rewrite, orch.s4.run_stage = orig_ar, orig_s4

            check("gates.auto_rewrite=%s → orchestrator 正常结束" % gate, rc == 0, rc)
            check("gates.auto_rewrite=%s → 钩子调用 %d 次" % (gate, expect),
                  len(calls) == expect, calls)
        finally:
            os.chdir(ROOT)
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 70)
    print("方向3 质量自评闭环自检（fake 模式，临时工作目录）")
    print("=" * 70)
    for fn in (case_collect_targets, case_dry_run, case_full_rewrite, case_idempotent,
               case_max_rounds, case_budget_pause, case_build_feedback,
               case_orchestrator_gate, case_orchestrator_hook):
        try:
            fn()
        except Exception as e:      # noqa: BLE001
            FAIL.append(fn.__name__)
            import traceback
            print("  [ERROR] %s → %s" % (fn.__name__, e))
            traceback.print_exc()
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 70)
    raise SystemExit(1 if FAIL else 0)
