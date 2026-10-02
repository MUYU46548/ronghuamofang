# -*- coding: utf-8 -*-
"""夹具产出信封回归：FakeClient 的正文必须在**任意合法配置**下都不触发退化门。

## 为什么需要它

`utils/fake_client._words_para` 按任务里的**字数下限**生成正文，而它原来靠一个
约 1147 字的静态句库循环复用。`config/system.yaml` 的 `chapter.target_words`
是**用户可改的合法旋钮** —— 2026-10-02 有人把它抬到 [8000,12000]，句库立刻开始
循环，12-gram 重复率飙到 58%，一连串用 FakeClient 的端到端用例**整片假红**
（看起来像产品坏了，其实是夹具模拟不出长章）。

**假红比没有测试更糟**：它会训练人忽略红灯。所以这里把「夹具与配置的耦合」
钉死 —— 无论 target_words 怎么调，夹具都必须产出合法样本。

边界（已在 `_words_para` docstring 记录）：已验证 400–12000 字无退化项；
20000 字起打散句的取模序号回绕，复读率回升到 36%。

用法：python tests/unit/test_fake_client_envelope.py（零 LLM、零网络）
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def main():
    from utils.fake_client import _words_para
    from utils.verify_chapter import check_degenerate, MAX_NGRAM_REPEAT_RATIO

    print("=" * 70)
    print("FakeClient 正文信封 —— 夹具不得因合法配置变化而假红")
    print("=" * 70)
    print(f"  复读率上限 = {MAX_NGRAM_REPEAT_RATIO}")

    # 覆盖：默认区间两端、常见放大档、以及句库容量附近/之上
    sizes = (400, 1500, 2000, 2294, 3000, 5500, 8000, 12000)
    bad_any = []
    for need in sizes:
        body = "## 第1章 测试章节\n\n" + _words_para(need) + "\n"
        items = check_degenerate(body)
        items = items[0] if isinstance(items, tuple) and isinstance(items[0], list) else items
        print(f"  [..] {need:>6} 字 → {items if items else '无退化'}")

    for need in sizes:
        body = "## 第1章 测试章节\n\n" + _words_para(need) + "\n"
        r = check_degenerate(body)
        items = r[0] if isinstance(r, tuple) and isinstance(r[0], list) else []
        if items:
            bad_any.append(f"{need} 字 → {items}")
    check("P1 400–12000 字全部不触发退化门（含 target_words 上下限）",
          not bad_any, "；".join(bad_any[:3]))

    # 字数达标：生成量不得少于请求的下限
    short = [n for n in sizes if len(_words_para(n)) < n]
    check("P2 生成字数不低于请求下限（否则 stage4 会判字数不达标）",
          not short, f"→ {short}")

    # 句库容量之上必须走打散路径，而不是悄悄循环复用
    big = _words_para(8000)
    check("P3 超出句库容量后仍为多段结构（不是一坨）",
          big.count("\n\n") >= 5, f"→ 段数 {big.count(chr(10) + chr(10)) + 1}")

    print("\n" + "=" * 70)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
