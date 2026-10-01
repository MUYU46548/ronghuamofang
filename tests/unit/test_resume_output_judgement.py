# -*- coding: utf-8 -*-
"""断点续跑「产物有效性」判据自检（`utils.verify_chapter.is_usable_output`）。

## 为什么需要这套用例

续跑逻辑如果只判 `exists()`，一次失败留下的**空壳文件**会被永久跳过 ——
重跑也救不回来，成品里静默留着那几章残稿。本项目真的发生过：stage5 曾因输入构造
错误产出空壳（章节根本没进 prompt），stage6 的润色同理（LLM 没回协议块）。

本用例守两件事：
1. 判据本身的正反例（空壳必须判不可用；正常产物不能被误杀）；
2. **stage5 / stage6 用的是这个判据**，而不是退回裸 `exists()` —— 后者是典型的
   "修好了又漂回去"，光测函数测不到。

全程离线、零 LLM。
用法：python tests/unit/test_resume_output_judgement.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.verify_chapter import is_usable_output   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def _w(p, text):
    p.write_text(text, encoding="utf-8")
    return p


def case_judgement():
    print("\n【1】判据正反例")
    d = Path(tempfile.mkdtemp(prefix="nf_resume_"))
    src = _w(d / "src.md", "正文内容" * 200)          # 约 800 字（去空白后计）
    check("目标文件不存在 → 不可用", is_usable_output(src, d / "nope.md") is False)
    check("目标为空文件 → 不可用",
          is_usable_output(src, _w(d / "empty.md", "")) is False)
    check("目标只剩空白 → 不可用",
          is_usable_output(src, _w(d / "blank.md", "   \n\n  ")) is False)
    # 空壳：只有原稿的一小截（模拟 LLM 只吐了开头就断）
    check("目标是空壳（远短于原稿）→ 不可用",
          is_usable_output(src, _w(d / "shell.md", "正文内容")) is False)
    # 正常：字数相当
    check("目标与原稿字数相当 → 可用",
          is_usable_output(src, _w(d / "ok.md", "正文内容" * 190)) is True)
    # 润色后变长也算正常
    check("目标比原稿长（润色扩写）→ 可用",
          is_usable_output(src, _w(d / "long.md", "正文内容" * 260)) is True)
    # 阈值边界：刚好 30%
    check("恰在 30% 边界 → 可用（阈值是「< 30% 才判不可用」）",
          is_usable_output(src, _w(d / "edge.md", "正文内容" * 60)) is True)
    # 源不存在时退化为「目标非空即可用」
    check("原稿缺失但目标非空 → 可用（不因缺源而误杀）",
          is_usable_output(d / "no_src.md", _w(d / "t2.md", "正文内容" * 80)) is True)
    print(f"  （临时目录 {d.name}）")


def case_callsites_use_it():
    print("\n【2】调用点真的用它（防「修好了又漂回 exists()」）")
    s5 = (ROOT / "scripts" / "stage5_check.py").read_text(encoding="utf-8")
    s6 = (ROOT / "scripts" / "stage6_polish.py").read_text(encoding="utf-8")
    check("stage5 的续跑判据用 is_usable_output",
          "is_usable_output(raw_dir" in s5, s5[:0] or "未找到调用")
    check("stage5 不再用裸 exists() 判 checked 跳过",
          "not (checked_dir / f.name).exists()" not in s5)
    check("stage6 的续跑判据用 is_usable_output",
          "is_usable_output(checked_dir" in s6)
    check("stage6 不再用裸 exists() 判 refined 跳过",
          "not (refined_dir / f.name).exists()" not in s6)
    vc = (ROOT / "scripts" / "utils" / "verify_chapter.py").read_text(encoding="utf-8")
    check("判据只有一份实现（stage5/6 不各自写一遍）",
          s5.count("def is_usable_output") == 0
          and s6.count("def is_usable_output") == 0
          and vc.count("def is_usable_output") == 1)


def main():
    print("=" * 62)
    print("  断点续跑产物有效性判据自检（离线）")
    print("=" * 62)
    case_judgement()
    case_callsites_use_it()
    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
