# -*- coding: utf-8 -*-
"""未改动段逐字不变（F11，2026-11-03）：不是"段还在"，是"一个字都没动"。

## 要治的病

段级精修的承诺是"只改那一段"。旧实现用 `"\\n\\n".join(段落)` 重新拼整章，
等于顺手把所有分隔符规范化了：原来 `\\n   \\n`（带空格的空行）、多个空行、
行首缩进，全被压平。字数几乎不变、段还在，但**第 1~4 段的字节已经变了** ——
"未改动区不变"这条保证根本不成立，而且没人看得出来。

按段 id 多重集判定（"改写 1 段、未动 3 段"）也拦不住它：id 相同只说明
"这几段还在"，说明不了"一个字都没动"。

## 判据（每条都能变红）

- U1 `replace_segment` 只换目标段，前缀/后缀字节逐字相同（含 `\\n   \\n` 这种怪分隔）
- U2 `verify_untouched` 对正确的替换判通过
- U3 前缀被改（模拟老写法压平空行）→ 报出来
- U4 后缀被改 → 报出来
- U5 段号越界 → 报出来（不抛）
- U6 接线：写盘前校验，不通过就**不写**（宁可这一段落空，也不许动全章）

用法：python tests/unit/test_untouched_bytes.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from refine_paragraph import replace_segment, verify_untouched    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


# 刻意用怪分隔符：带空格的空行 + 三连空行 —— 老写法 join("\n\n") 会把它压平
SRC = "第一段。\n   \n第二段，要被改。\n\n\n第三段。\n第四段。"


def main():
    print("=" * 66)
    print("未改动段逐字不变（F11）")

    print("\n【1】replace_segment 的字节保证")
    out = replace_segment(SRC, 1, "第二段，改过了。")
    head = SRC[:SRC.index("第二段")]
    tail = SRC[SRC.index("\n\n\n第三段"):]
    check("U1 目标段被换掉", "第二段，改过了。" in out and "第二段，要被改。" not in out, out)
    check("U1 前缀（含怪空行）逐字保留", out.startswith(head), repr(out[:12]))
    check("U1 后缀（含三连空行）逐字保留", out.endswith(tail), repr(out[-14:]))
    check("U1 除目标段外完全相等",
          out.replace("第二段，改过了。", "第二段，要被改。") == SRC, out)

    print("\n【2】verify_untouched 判通过")
    check("U2 正确替换 → 无问题", verify_untouched(SRC, out, 1) == [], verify_untouched(SRC, out, 1))

    print("\n【3】两类破坏都要报出来")
    # 模拟老实现的"顺手规范化"：带空格的空行被压成标准空行
    bad_prefix = SRC.replace("\n   \n", "\n\n")
    check("U3 前置：这种改动确实改变了字节但段数不变",
          bad_prefix != SRC and len(bad_prefix) < len(SRC), (len(SRC), len(bad_prefix)))
    got = verify_untouched(SRC, bad_prefix, 1)
    check("U3 前缀被改 → 报出来", any("之前" in p for p in got), got)

    broken_tail = out[:-1] + "！"
    got = verify_untouched(SRC, broken_tail, 1)
    check("U4 后缀被改 → 报出来", any("之后" in p for p in got), got)

    print("\n【4】边界")
    got = verify_untouched(SRC, out, 9)
    check("U5 段号越界 → 报出来且不抛", got and "越界" in got[0], got)
    check("U5b 空文本不抛", isinstance(verify_untouched("", "", 0), list))

    print("\n【5】接线：不通过就不写")
    src = (ROOT / "scripts" / "refine_paragraph.py").read_text(encoding="utf-8")
    check("U6 写盘前调 verify_untouched", "verify_untouched(full_text, new_full" in src)
    check("U6 不通过时不 write_text（返回 False 在写之前）",
          src.index("没写入：改动波及了其他段") < src.index("write_text(chap_path, new_full)"))

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
