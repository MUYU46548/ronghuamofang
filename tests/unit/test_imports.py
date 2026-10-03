# -*- coding: utf-8 -*-
"""导入冒烟：审稿闭环的两个入口模块必须能 import 且关键函数可调用（零 LLM）。

## 为什么保留这种"看着很弱"的用例

`chapter_review` / `batch_refine` 是审稿闭环的入口。它们**在 import 期**坏掉
（语法错 / 循环依赖 / 模块级常量引用错）时，整条闭环不是"报个错"，而是**静默不可用**
——API 端点 500、GUI 页签空白。这条冒烟只花毫秒级成本就能拦住这类回归。

## 2026-10-03 修：它以前是**纯 print**（没有任何断言）

```python
print('chapter_review.run_review:', callable(chapter_review.run_review))
```
`callable=False` 也照样退 0 → `nfctl test` 记成通过（假绿的一种：
**用例只会"展示"，不会"判定"**）。现按项目惯例改成断言 + 非零退出。

用法：python tests/unit/test_imports.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 62)
    print("  审稿闭环入口导入冒烟")
    print("=" * 62)
    try:
        import chapter_review
        check("chapter_review 可 import", True)
    except Exception as e:                                  # noqa: BLE001
        chapter_review = None
        check("chapter_review 可 import", False, "%s: %s" % (type(e).__name__, e))
    try:
        import batch_refine
        check("batch_refine 可 import", True)
    except Exception as e:                                  # noqa: BLE001
        batch_refine = None
        check("batch_refine 可 import", False, "%s: %s" % (type(e).__name__, e))

    check("chapter_review.run_review 可调用",
          bool(chapter_review) and callable(getattr(chapter_review, "run_review", None)))
    check("batch_refine.run_batch_refine 可调用",
          bool(batch_refine) and callable(getattr(batch_refine, "run_batch_refine", None)))
    # 判据只有一份：JSON 修复统一走 validator.parse_llm_json，chapter_review 只做转发
    check("chapter_review 仍提供 JSON 解析出口（转发 validator）",
          bool(chapter_review) and callable(getattr(chapter_review,
                                                    "_extract_json_from_output", None)))
    check("两个模块都有 CLI 入口（main）",
          bool(chapter_review) and callable(getattr(chapter_review, "main", None))
          and bool(batch_refine) and callable(getattr(batch_refine, "main", None)))

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
