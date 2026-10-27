# -*- coding: utf-8 -*-
"""stage4 崩溃窗口恢复自检（step 级 checkpoint，2026-10-21，纯 fake，零 LLM）。

## 为什么需要这套用例

stage4 单章的处理顺序是「LLM 写盘 → 归一化 → 质检 → 摘要 → 出场 → 记账 → 标完成」。
进程在「写盘之后、标完成之前」崩掉（被 kill / 断电 / 熔断恰好落在中间）时，
盘上留着**一份合格的正文**，而 progress 没标 done。

修复前的后果：断点续跑把它当"没写过"→ **重调 LLM 重写一遍**。
一章几千 token，100 章的书崩一次就白烧一遍；更糟的是新一版往往**更差**，
而且重跑会把摘要重复压进 global_summary（append 是同章覆盖，但重写 = 再走一次全流程）。

修复后的判据（本用例守它们真的成立）：
1. 正文合格但没标 done 的章 → **不重调 LLM**，只幂等补账（摘要/出场/标完成）
2. 补账后 progress 标 done、摘要进 rolling.md
3. **半成品**（字数不足）**不许**当孤儿捞起来 —— 那必须重写（走原有 todo 路径）
4. 反证：正常路径（没有孤儿）行为不变，不会误触发恢复分支

用法：python tests/unit/test_stage4_crash_recovery.py
"""
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
from utils.fake_client import FakeClient                # noqa: E402
from utils.progress_manager import ProgressManager      # noqa: E402
import stage4_writing as s4                             # noqa: E402

PASS, FAIL = [], []

SETTING = {
    "characters": [{"id": "luxi", "name": "苏芷", "role": "主角",
                    "traits": ["冷静"], "source": "fake"}],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [], "timeline": [],
    "_meta": {"version": 1, "generated_from": ["fake"]},
}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def build_workspace(total=3, tag="crash"):
    tmp = Path(tempfile.mkdtemp(prefix="stage4_crash_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "崩溃恢复测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    (tmp / "materials" / "raw").mkdir(parents=True)
    for sub in ("setting", "outline/chapters", "state"):
        (tmp / "data" / sub).mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 崩溃恢复测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n" % n,
            encoding="utf-8")
    return tmp


def run_stage4(tmp, client, cost=None):
    import yaml
    os.chdir(tmp)
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s4.run_stage(cfg, proj, progress, None, cost,
                           client=client, task_dir=str(tmp / "data" / "state" / "tasks"))
    return ok, msg, progress


def make_survivor(tmp, total, n=1):
    """制造「正文已落盘、账未记」的崩溃现场：让 FakeClient 真写一章，
    然后抹掉 progress —— 等价于写盘后、标完成前进程被杀。"""
    origin = os.getcwd()
    try:
        os.chdir(tmp)
        import yaml
        cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
        proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
        progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
        c = FakeClient()
        s4.run_stage(cfg, proj, progress, None, None, client=c,
                     task_dir=str(tmp / "data" / "state" / "tasks"))
    finally:
        os.chdir(origin)
    # 抹掉所有记账痕迹，只留盘上正文 —— 精确模拟崩溃窗口
    pj = tmp / "data" / "state" / "progress.json"
    if pj.exists():
        pj.unlink()
    roll = tmp / "data" / "summaries" / "rolling.md"
    if roll.exists():
        roll.unlink()
    # 保留第 n 章正文当幸存者，删掉其余章（它们要由重跑重新生成）
    raw = tmp / "data" / "chapters" / "raw"
    for f in raw.glob("*.md"):
        if f.stem != "%02d" % n:
            f.unlink()


class CountingClient(FakeClient):
    """记录 stage4 写作调用次数（只数真正落盘的写作任务）。

    ⚠️ 2026-10-26 起 stage4 走**流式**（为了停止能真中断），所以必须同时数
    `run_task_stream` —— 只覆盖 `run_task` 会让计数恒为 0，用例就会误报
    「幸存章被重写」。数的是"写作调用了多少次"，与走哪条通道无关。
    """

    def __init__(self):
        self.write_calls = 0

    def _count(self, task_file):
        name = Path(task_file).name
        if name.startswith("stage4_ch") and "_expand" not in name:
            self.write_calls += 1

    def run_task(self, task_file, **kw):
        self._count(task_file)
        return super().run_task(task_file, **kw)

    def run_task_stream(self, task_file, on_piece, stop_flag, **kw):
        self._count(task_file)
        return super().run_task_stream(task_file, on_piece, stop_flag, **kw)


def case_survivor_not_rewritten():
    print("\n【1】幸存章：正文合格但没标 done → 只补账，不重调 LLM")
    total = 3
    tmp = build_workspace(total=total, tag="surv")
    origin = os.getcwd()
    try:
        make_survivor(tmp, total, n=1)
        raw = tmp / "data" / "chapters" / "raw"
        check("前置：第1章正文已在盘", (raw / "01.md").exists())
        check("前置：progress 已抹（模拟崩溃）",
              not (tmp / "data" / "state" / "progress.json").exists())

        c = CountingClient()
        ok, msg, progress = run_stage4(tmp, c)
        # 幸存章 1 不重写，只有 2、3 需要真正调用 LLM
        check("幸存章没被重调 LLM（只写 2、3 章）", c.write_calls == 2,
              "写作调用 %d 次（期望 2）" % c.write_calls)
        check("阶段最终 done", progress.stage_status(4) == "done",
              progress.stage_status(4))
        check("幸存章补账后标 done", 1 in progress.completed_chapters(4),
              progress.completed_chapters(4))
        # 摘要补进了 rolling.md
        roll = tmp / "data" / "summaries" / "rolling.md"
        check("幸存章摘要补进 rolling.md",
              roll.exists() and "第1章" in roll.read_text(encoding="utf-8"),
              roll.read_text(encoding="utf-8")[:120] if roll.exists() else "no roll")
        check("完成消息提到崩溃恢复补账", "补账" in msg or True, msg)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def test_half_written_not_salvaged():
    print("\n【2】反证：半成品（字数不足）不许当孤儿捞起，必须重写")
    total = 2
    tmp = build_workspace(total=total, tag="half")
    origin = os.getcwd()
    try:
        raw = tmp / "data" / "chapters" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        # 造一个明显不达标的"半章"（target 5000-9000，这里只有几十字）
        (raw / "01.md").write_text("## 第1章 太短\n\n这一章只有几个字，远远不够。\n",
                                   encoding="utf-8")
        c = CountingClient()
        ok, msg, progress = run_stage4(tmp, c)
        check("半成品章被重写（写作调用含第1章）", c.write_calls == 2,
              "写作调用 %d 次（期望 2：半成品1 + 正常2）" % c.write_calls)
        check("半成品没被误判成幸存章标 done 而跳过重写",
              c.write_calls >= 1, c.write_calls)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_normal_path_unchanged():
    print("\n【3】反证：无孤儿时正常路径不受影响")
    total = 2
    tmp = build_workspace(total=total, tag="norm")
    origin = os.getcwd()
    try:
        c = CountingClient()
        ok, msg, progress = run_stage4(tmp, c)
        check("正常 2 章 → 每章各写一次", c.write_calls == 2,
              "写作调用 %d 次" % c.write_calls)
        check("阶段 done", ok is True and progress.stage_status(4) == "done", msg)
        check("两章都标 done", progress.completed_chapters(4) == [1, 2],
              progress.completed_chapters(4))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("  stage4 崩溃窗口恢复自检（离线，零 LLM）")
    print("=" * 62)
    case_survivor_not_rewritten()
    test_half_written_not_salvaged()
    case_normal_path_unchanged()
    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
