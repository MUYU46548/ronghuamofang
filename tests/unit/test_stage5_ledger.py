# -*- coding: utf-8 -*-
"""阶段 5 的完成判据必须验账本（2026-11-04，试跑 P1-5）。

## 要治的病

`stage5_check.SELF_CRITERIA` 与阶段 5 任务书里都写着“缺账本 = 视为没检查”，
但 `self_done()` 只问 `checked/NN.md` 可不可用 —— 一份**根本没写账本**的
“检查”照样判完成。试跑实测：4 批全部 complete=True、七维给到 8.14 分，
而 `data/review/batchN.json` 里一条记录都没有，分数是人手工抽句填的。
**判据不验账本 = 这条闸门不存在**，绿得毫无意义。

## 判据（每条都能变红）

- L1 checked 稿齐 + 账本齐全 → 完成
- L2 checked 稿齐、**没有** data/review → 未完成，且说的是“缺账本”
- L3 账本不是可用 JSON → 未完成
- L4 账本漏了一章 → 未完成，且点名那一章
- L5 有记录但七维举证不完整（引句编的）→ 未完成
- L6 接线：`self_done` 真的调了账本判据（不是只写了函数没人用）

用法：python tests/unit/test_stage5_ledger.py
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

import stage5_check as s5                                         # noqa: E402
from utils import review_ledger as R                              # noqa: E402
from utils.config_io import load_config_yaml                      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (detail,)))


CHAPTER = (
    "## 第1章 锈锁\n\n"
    "他推开门的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。\n\n"
    "“你迟到了。”老者没有抬头，手指在桌沿上慢慢摩挲。\n\n"
    "院子里的老狗趴在墙根，尾巴动了一下，又沉下去。\n\n"
    "灯芯爆了一下，屋里暗了半格。\n\n"
)
QUOTE = "他推开门的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。"
PROJ = {"book": {"form": "narrative"}}


def _w(rel, text):
    p = Path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _entry(no, quote=QUOTE):
    return {"chapter": no, "dims": [
        {"dim": c, "score": 8, "quote": quote, "note": "（测试）"}
        for c in R.DIM_CODES]}


def main():
    print("=" * 66)
    print("阶段 5 完成判据必须验账本（P1-5）")
    cfg = load_config_yaml(ROOT / "config" / "system.yaml") or {}
    tmp = Path(tempfile.mkdtemp())
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        # 两章都写齐 raw + checked（往返拆分足以让 is_usable_output 认）
        for n in (1, 2):
            body = CHAPTER if n == 1 else CHAPTER.replace("锈锁", "空屋")
            _w("data/chapters/raw/%02d.md" % n, body)
            _w("data/chapters/checked/%02d.md" % n, body)

        print("\n【L2】checked 齐但一条账本都没有 → 未完成")
        ok, why = s5.self_done(cfg, PROJ)
        check("L2 判未完成", ok is False, why)
        check("L2 说清是缺账本（不是含糊的‘不合格’）",
              "账本" in str(why), why)

        print("\n【L3】账本不是可用 JSON → 未完成")
        _w("data/review/batch1.json", "这不是 JSON，模型随手吐的一段话")
        ok, why = s5.self_done(cfg, PROJ)
        check("L3 判未完成", ok is False, why)
        check("L3 问题里点了不可用的账本",
              "batch1.json" in str(why) or "账本不可用" in str(why), why)

        print("\n【L4】账本只写了第 1 章、漏了第 2 章 → 未完成")
        _w("data/review/batch1.json",
           json.dumps({"chapters": [_entry(1)]}, ensure_ascii=False))
        ok, why = s5.self_done(cfg, PROJ)
        check("L4 判未完成", ok is False, why)
        check("L4 点名漏掉的第 2 章", "第2章" in str(why), why)

        print("\n【L5】有记录但举证是编的（七维不完整）→ 未完成")
        _w("data/review/batch1.json",
           json.dumps({"chapters": [_entry(1, "他掏出怀表看了一眼，转身走进雨里。"),
                                    _entry(2, "他掏出怀表看了一眼，转身走进雨里。")]},
                      ensure_ascii=False))
        ok, why = s5.self_done(cfg, PROJ)
        check("L5 判未完成", ok is False, why)
        check("L5 问题指向‘举证不完整’", "举证" in str(why), why)

        print("\n【L1】账本齐 + 举证命中原文 → 完成")
        _w("data/review/batch1.json",
           json.dumps({"chapters": [_entry(1), _entry(2)]}, ensure_ascii=False))
        ok, why = s5.self_done(cfg, PROJ)
        check("L1 判完成", ok is True, why)
        check("L1 结论写明账本已覆盖", "账本" in str(why), why)

        print("\n【L6】接线：完成判据真的用了账本校验")
        src = (ROOT / "scripts" / "stage5_check.py").read_text(encoding="utf-8")
        check("L6 self_done 调 _ledger_gaps", "_ledger_gaps(proj)" in src)
        check("L6 账本判据读 data/review 的 batch*.json",
              'REVIEW_DIR.glob("batch*.json")' in src)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
