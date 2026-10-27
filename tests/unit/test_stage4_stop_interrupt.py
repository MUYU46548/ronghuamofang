# -*- coding: utf-8 -*-
"""stage4「停止」要真的停下来（2026-10-26，用户第 6 条）。

## 为什么单独一个用例

用户原话：「我受不了那个永恒的 29% 了，直接批准让 AI 试跑阶段三。依旧点停止停不下来，绝了。」

真实原因不是按钮坏了，是**停止语义只存在于章间**：非流式 `run_task` 要等整章
返回（十几分钟）才轮到下一轮循环检查 `_stop_requested()`。这一章写不完，
按多少次停止都没用。

修法：写作改走 `run_task_stream`，每读一小块就查一次 stop_flag；中断时半截
正文**不落盘**（`_apply_ops` 被 stop 跳过）。

## 判据（行为级，不看日志）

A. `_run_task_safe` 在有 `run_task_stream` 的客户端上**确实走流式**
B. 中断章**不落盘**（盘上只有已完成的那一章）
C. 中断 ≠ 失败：阶段状态是 `pending`（可断点续跑），不是 `failed`
D. 已完成章保留、不被回滚
E. 中断消息说清「半截不入稿」，不报成"失败"

纯 fake、零 LLM、零网络。
用法：python tests/unit/test_stage4_stop_interrupt.py
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


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


SETTING = {
    "characters": [{"id": "luxi", "name": "苏芷", "role": "主角",
                    "traits": ["冷静"], "source": "fake"}],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [], "timeline": [],
    "_meta": {"version": 1, "generated_from": ["fake"]},
}


class StreamStopClient:
    """前 N-1 章交给真 FakeClient 流式写完，第 N 章模拟「用户点了停止」。

    为什么要包一层：真实停止是 nf_api 置位 STOP_EVENTS 后 stage4 在流里查
    `stop_flag()`；这里直接把「流被中断」的结果交给 stage4，测的是
    **stage4 拿到 stopped 之后怎么处理**（这条路径以前根本不存在）。
    """

    provider = "direct"

    def __init__(self, stop_at_call=2):
        self.real = FakeClient()
        self.stop_at_call = stop_at_call
        self.calls = 0
        self.stream_calls = 0

    def write_task(self, *a, **kw):
        return self.real.write_task(*a, **kw)

    def run_task_stream(self, task_file, on_piece, stop_flag, **kw):
        self.calls += 1
        self.stream_calls += 1
        if self.calls >= self.stop_at_call:
            # 已经产出一些字 → 然后被停掉（真实实现里 _apply_ops 不会执行）
            for ch in "半截正文不该落盘":
                on_piece(ch)
            return {"exit_code": 0, "stopped": True, "stdout_tail": "",
                    "tokens": 100, "tokens_out": 20, "cost_yuan": 0.0,
                    "estimated": False, "provider": "direct", "model": "fake",
                    "requests": 1, "finish_reasons": []}
        return self.real.run_task_stream(task_file, on_piece, stop_flag, **kw)

    # 非流式入口也保留：若 stage4 退回 run_task，用例要能指出"没走流式"
    def run_task(self, task_file, **kw):
        self.calls += 1
        return self.real.run_task(task_file, **kw)


def build_workspace(total=3, tag="intr"):
    tmp = Path(tempfile.mkdtemp(prefix="stage4_intr_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "中断测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 中断测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n" % n,
            encoding="utf-8")
    return tmp


def main():
    import yaml

    print("【1】流式真的被用上了（停止才可能生效）")
    tmp = build_workspace(total=3)
    origin = os.getcwd()
    try:
        os.chdir(tmp)
        cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
        proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
        progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
        client = StreamStopClient(stop_at_call=2)
        ok, msg = s4.run_stage(cfg, proj, progress, None, None,
                               client=client,
                               task_dir=str(tmp / "data" / "state" / "tasks"))
        check("走了 run_task_stream（不是非流式 run_task）",
              client.stream_calls >= 1, "stream_calls=%d calls=%d"
              % (client.stream_calls, client.calls))
        check("阶段返回 False（本轮没跑完）", ok is False, msg)
        check("消息说清是「用户中断」", "中断" in msg, msg)
        check("消息点明「半截不入稿」", "半截" in msg or "不入稿" in msg, msg)

        raw = tmp / "data" / "chapters" / "raw"
        check("第 1 章已落盘（已完成的保留）", (raw / "01.md").exists(),
              sorted(p.name for p in raw.glob("*.md")))
        check("被中断的第 2 章**不落盘**（半截不入稿）", not (raw / "02.md").exists(),
              sorted(p.name for p in raw.glob("*.md")))
        check("第 3 章没开始", not (raw / "03.md").exists(),
              sorted(p.name for p in raw.glob("*.md")))

        print("\n【2】中断 ≠ 失败（状态语义）")
        check("阶段状态是 pending（可断点续跑）", progress.stage_status(4) == "pending",
              progress.stage_status(4))
        check("**不是 failed**（否则界面显示「阶段 4 失败」，用户以为弄坏了）",
              progress.stage_status(4) != "failed")
        check("已完成章仍在 completed_chapters 里",
              1 in progress.completed_chapters(4), progress.completed_chapters(4))
        check("中断的章没被记成失败",
              2 not in [f["n"] for f in progress.failed_chapters(4)],
              progress.failed_chapters(4))
        st4 = progress.data.get("stages", {}).get("4", {})
        check("current_chapter 已清掉（否则界面停在旧章号）",
              "current_chapter" not in st4, st4)
        check("current_chars 已清掉", "current_chars" not in st4, st4)

        print("\n【3】静态：stage4 里确实先判 stopped 再判 exit_code")
        src = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
        i_stopped = src.find('result.get("stopped")')
        i_exit = src.find('if result["exit_code"] != 0')
        check("两处锚点都在", i_stopped > 0 and i_exit > 0, (i_stopped, i_exit))
        check("stopped 判定在 exit_code 判定之前（顺序即生命线）",
              0 < i_stopped < i_exit, (i_stopped, i_exit))
        check("写作回调传了 on_piece（实时字数要能上来）",
              "on_piece=_piece_progress(n)" in src)
        check("字数回写有节流（不是每个 token 都落盘）",
              "_ch_state[\"last_write\"]" in src and "1.5" in src)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
