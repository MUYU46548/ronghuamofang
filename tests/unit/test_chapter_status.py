# -*- coding: utf-8 -*-
"""章节状态：确定性判据优先，自评分只作参考（2026-11-02，清单第 5 条）。

## 要治的病

实测：润色稿 10 章全部 2000 上下（`chapter.target_words` 下限 5000），
`chapter_log` 全部 `status=ok, quality=8~9`，界面显示「完成」。

代码原因是两行：

    needs_rewrite = check.quality is not None and check.quality < quality_threshold
    status = "needs_rewrite" if needs_rewrite else "ok"

字数区间明明写在 `check.errors` 里、`check.ok` 已经是 False，status 却**没看它**。
于是自评分成了唯一判据（P5 的 H2 明确说它不该单独决定任何转移）。

## 判据（每条都能变红）

- C1 字数不达标（ok=False）+ 自评 9 分 → **不许是 ok**（应 invalid）
- C2 确定性过了、自评低于阈值 → needs_rewrite
- C3 两者都过 → ok
- C4 没有自评注释（quality=None）不算低分（缺注释是 warning，不是判据）
- C5 stage4 真的用这个函数算 status（AST 接线，防"写了函数没人调"）

用法：python tests/unit/test_chapter_status.py
"""
import ast
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.verify_chapter import ChapterCheck                     # noqa: E402
from stage4_writing import _chapter_status                        # noqa: E402

PASS, FAIL = [], []
TH = 6


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("章节状态：确定性判据优先")
    print("=" * 66)

    # ---- C1 短章不许判 ok（这就是事故现场：2163 字 + 自评 9）----
    short = ChapterCheck(path="x", ok=False, word_count=2163, quality=9,
                         errors=["字数 2163 不在 [5000, 9000] 区间"])
    st = _chapter_status(short, TH)
    check("C1 短章 + 自评 9 → 不许 ok", st != "ok", st)
    check("C1 归为 invalid", st == "invalid", st)

    # ---- C2 自评低 ----
    low = ChapterCheck(path="x", ok=True, word_count=6000, quality=3, errors=[])
    check("C2 自评低 → needs_rewrite", _chapter_status(low, TH) == "needs_rewrite")

    # ---- C3 都过 ----
    good = ChapterCheck(path="x", ok=True, word_count=6000, quality=9, errors=[])
    check("C3 都过 → ok", _chapter_status(good, TH) == "ok")

    # ---- C4 缺自评 ----
    none_q = ChapterCheck(path="x", ok=True, word_count=6000, quality=None, errors=[])
    check("C4 缺自评 → 不算低分", _chapter_status(none_q, TH) == "ok")

    # ---- C5 接线：stage4 的 status 必须来自这个函数 ----
    src = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    wired = False
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") == "status"
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") == "_chapter_status"):
            wired = True
    check("C5 stage4 的 status 真的用它算", wired,
          "应有 status = _chapter_status(...)")

    old = 'needs_rewrite = check.quality is not None and check.quality < quality_threshold'
    check("C5 旧的两行判据已移除", old not in src, "不应再出现旧的两行判据")

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
