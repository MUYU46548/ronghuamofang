# -*- coding: utf-8 -*-
"""截断类失败**不得自动重试**自检（2026-10-03）。零 LLM、零网络。

## 为什么

`finish_reason == "length"` 时我们不落盘、把残缺文本隔离到 `data/state/truncated/`
—— 但原先留下的只有给人看的 txt，**机器无从判断"这次失败是截断类"**，
于是 `auto_retry`（默认开、默认 2 轮）会照样重试：同一个 `max_tokens` 上限再跑一次，
**只会再截断一次**，白烧一倍输入（几万~几十万 token）。这正是本轮"止烧"要治的浪费。

现：截断时同时写机器可读标记 `data/state/truncated/last.json`；
orchestrator 重试前用它判定（`should_auto_retry`），截断 → 不重试 + 明确告知该调哪个键。

判据只有一份：`utils/truncation`（写 `mark` / 读 `after`）。

用法：python tests/unit/test_truncation_no_retry.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import truncation  # noqa: E402
import orchestrator  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def case_mark_and_query():
    print("\n【1】截断标记：写得出、读得回、按时间可比（不靠「文件存在」）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_trunc_"))
    origin = os.getcwd()
    real_mark = REPO / "data" / "state" / "truncated" / "last.json"
    real_before = (real_mark.stat().st_mtime if real_mark.exists() else None)
    try:
        os.chdir(tmp)
        check("初始没有标记时 last() 返回 None", truncation.last() is None)
        check("没有标记时 after(任意时间) 一律 False",
              truncation.after("2020-01-01T00:00:00") is False)

        info = truncation.mark("data/state/tasks/stage4_ch01.md", ["正文" * 10, "第二段"])
        check("mark() 返回诊断信息（任务/时间/字符数）",
              info.get("task") == "stage4_ch01" and info.get("chars") == 23
              and info.get("at"), info)
        marker = truncation.TRUNC_DIR / truncation.MARK_NAME
        check("标记文件真的落盘", marker.exists(), marker)
        check("标记内容可解析且不含正文（只留诊断项）",
              "正文" not in json.dumps(truncation.last(), ensure_ascii=False),
              truncation.last())
        check("隔离目录已建（供 artifacts.py 管理）", truncation.TRUNC_DIR.is_dir())

        check("after(更早的时间) → True（这次失败确实是截断类）",
              truncation.after("2000-01-01T00:00:00") is True)
        check("after(未来时间) → False（旧标记不会误判新失败）",
              truncation.after("2999-01-01T00:00:00") is False)
        check("after(None) → False（无参照时间不许乱判）", truncation.after(None) is False)
        check("after(垃圾字符串) → False，不抛", truncation.after("not-a-time") is False)

        marker.write_text("这不是 JSON", encoding="utf-8")
        check("标记坏掉 → last() 返 None（诊断信息绝不抛）", truncation.last() is None)
        check("标记坏掉 → after() 返 False（宁可重试也不乱判）",
              truncation.after("2000-01-01T00:00:00") is False)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)

    real_after = (real_mark.stat().st_mtime if real_mark.exists() else None)
    check("**真实工作区的截断标记未被本用例碰到**", real_before == real_after,
          (real_before, real_after))


def case_retry_decision():
    print("\n【2】重试判据（should_auto_retry）：截断一票否决 + 只说该说的事")
    ok, why = orchestrator.should_auto_retry({"auto_retry": True}, 0, False)
    check("正常失败 + 开关开 + 还有轮次 → 重试", ok is True and why == "", (ok, why))

    ok, why = orchestrator.should_auto_retry({}, 0, False)
    check("缺省（未配置 auto_retry）→ 保持既有行为（重试）", ok is True, why)

    ok, why = orchestrator.should_auto_retry({"auto_retry": False}, 0, False)
    check("auto_retry=false → 不重试", ok is False and "auto_retry" in why, why)

    ok, why = orchestrator.should_auto_retry({"auto_retry_max_rounds": 2}, 2, False)
    check("已达轮次上限 → 不重试", ok is False and "上限" in why, why)

    # 反证：**截断必须一票否决**（哪怕开关开着、轮次还没到）
    ok, why = orchestrator.should_auto_retry({"auto_retry": True, "auto_retry_max_rounds": 5},
                                             0, True)
    check("**截断类失败 → 绝不重试**（开关开着也不重试）", ok is False, (ok, why))
    check("拒绝理由点明根因（max_tokens 截断）", "截断" in why and "max_tokens" in why, why)
    check("拒绝理由给出可执行的调法（指出该调哪个键）",
          "per_request_max_tokens" in why and "budget.token_limit" in why, why)
    check("拒绝理由说明为什么（同一上限只会再截断一次）",
          "再截断" in why or "白烧" in why, why)


def case_wiring():
    print("\n【3】接线：orchestrator 真的查了、llm_client 真的标了（判据一份）")
    orch = (REPO / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    llm = (REPO / "scripts" / "utils" / "llm_client.py").read_text(encoding="utf-8")
    check("orchestrator 的重试循环调用 should_auto_retry",
          "should_auto_retry(gates, retry_count, _truncated)" in orch)
    check("orchestrator 用 truncation.after(阶段开始时刻) 判定",
          "truncation.after(stage_started)" in orch
          and "_stage_started_iso = datetime.now()" in orch)
    check("阶段开始时刻在 run_stage **之前**记录",
          orch.index("_stage_started_iso = datetime.now()")
          < orch.index("STAGES[n].run_stage(cfg, proj, progress, db, cost"))
    check("不重试时打印原因（不能静默跳过）",
          "不自动重试：%s" % "%s" in orch or "不自动重试：" in orch)
    check("旧的 `while auto_retry and retry_count < max_retry` 已不存在（判据不再两处）",
          "while auto_retry and retry_count < max_retry" not in orch)
    check("llm_client 的截断隔离走 truncation.mark（写标记的唯一入口）",
          "truncation.mark(" in llm and "from utils import truncation" in llm)
    check("llm_client 仍打印隔离路径（人要看得到残缺稿在哪）",
          "已隔离存放" in llm)


def main():
    print("=" * 62)
    print("  截断类失败不得自动重试 · 自检")
    print("=" * 62)
    case_mark_and_query()
    case_retry_decision()
    case_wiring()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    _ = time
    sys.exit(main())
