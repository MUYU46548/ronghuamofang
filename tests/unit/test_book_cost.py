# -*- coding: utf-8 -*-
"""本书成本账（2026-11-05）：机器侧自动 + Agent 侧自报。

## 为什么要这条账

`engine: self/hermes` 下钱是外部 Agent 花的，墨坊的 cost_log 恒 0 —— 读不到。
所以账本必须**分成两半**，并且**不猜**：
- 机器侧（章数 / 汉字数 / 墨坊 token 与费用）自动刷新；
- Agent 侧（元）由执行方 `record --yuan` 补；没补就显示“未上报”。

判据：
1. auto 只写机器侧，不覆盖已填的 agent_yuan；
2. record 写入后，再 auto 一次不能把 Agent 花费抹掉；
3. 汉字数走唯一口径（标点不算）；
4. 换书（progress.project 变）各记各的。

用法：python tests/unit/test_book_cost.py
"""
import io
import json
import sys
import tempfile
import shutil
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import book_cost  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def make_root(tmp: Path, book: str, chapters):
    (tmp / "data" / "chapters" / "refined").mkdir(parents=True, exist_ok=True)
    for old in (tmp / "data" / "chapters" / "refined").glob("*.md"):
        old.unlink()
    for i, text in enumerate(chapters, 1):
        (tmp / "data" / "chapters" / "refined" / ("%02d.md" % i)).write_text(
            text, encoding="utf-8")
    (tmp / "data" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "state" / "progress.json").write_text(
        json.dumps({"project": book, "stages": {"1": {"status": "done",
                                                      "finished_at": "2026-11-05T10:00:00"},
                                                "4": {"status": "done",
                                                      "finished_at": "2026-11-05T12:00:00"}}},
                   ensure_ascii=False), encoding="utf-8")


def main():
    print("=" * 62)
    print("本书成本账")
    print("=" * 62)
    tmp = Path(tempfile.mkdtemp(prefix="nf_cost_"))
    try:
        # 两章：汉字 10 + 标点若干；第二章 4 个汉字
        texts = ["第一段有十个汉字在这里。", "“四个汉字”"]
        expect = sum(len([c for c in x if "\u4e00" <= c <= "\u9fff"]) for x in texts)
        make_root(tmp, "测试账本", texts)
        rec = book_cost.auto_record(tmp)
        check("auto 记下书名", rec["book"] == "测试账本", rec["book"])
        check("章数 = 2", rec["chapters"] == 2, rec["chapters"])
        check("汉字数只计汉字（标点不算）", rec["han_words"] == expect, (rec["han_words"], expect))
        check("Agent 侧未上报时是 None（不猜成 0）", rec["agent_yuan"] is None,
              rec["agent_yuan"])
        check("阶段时间进了账", rec["first_stage_at"] and rec["last_stage_at"],
              (rec["first_stage_at"], rec["last_stage_at"]))
        check("摘要如实说未上报", "未上报" in book_cost.summary_line(rec))

        rec2 = book_cost.upsert(tmp, agent_yuan=45.0, note="Hermes，12 章", source="manual")
        check("Agent 花费写入", rec2["agent_yuan"] == 45.0, rec2["agent_yuan"])
        check("备注保留", rec2["agent_note"].startswith("Hermes"))

        rec3 = book_cost.auto_record(tmp)
        check("再 auto 一次不抹掉 Agent 花费", rec3["agent_yuan"] == 45.0,
              rec3["agent_yuan"])
        check("合计 = 墨坊 + Agent",
              abs(book_cost.total_yuan(rec3) - (rec3["mofang_yuan"] + 45.0)) < 1e-6)

        # 换一本书：各记各的
        make_root(tmp, "另一本", ["九个汉字都在这一段里面"])
        rec4 = book_cost.auto_record(tmp)
        data = book_cost.load(tmp)
        check("两本书分别记账", set(data["books"]) == {"测试账本", "另一本"}, list(data["books"]))
        check("新书 Agent 侧仍是未上报", rec4["agent_yuan"] is None)
        check("旧书记录没被覆盖", data["books"]["测试账本"]["agent_yuan"] == 45.0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
