# -*- coding: utf-8 -*-
"""段落级精修轴自检（零 LLM，纯确定性）。

覆盖：
- paragraph_diff: char_diff / summarize_diff
- style_drift: extract_style_features / split_sentences
- refine_paragraph: split_paragraphs / list_paragraph_history / restore_paragraph_version
"""
import sys
import os
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from utils import paragraph_diff, style_drift
import refine_paragraph

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def test_char_diff():
    print("\n=== paragraph_diff ===")
    # 相同文本
    d = paragraph_diff.char_diff("abc", "abc")
    check("相同文本 → 全 =", all(op["op"] == "=" for op in d))
    # 纯新增
    d = paragraph_diff.char_diff("abc", "abcd")
    check("纯新增 → 末尾 add", d[-1]["op"] == "add" and d[-1]["text"] == "d")
    # 纯删除
    d = paragraph_diff.char_diff("abcd", "abc")
    check("纯删除 → 末尾 del", d[-1]["op"] == "del" and d[-1]["text"] == "d")
    # 替换
    d = paragraph_diff.char_diff("abc", "axc")
    ops = [op["op"] for op in d]
    check("替换 → del+add", "del" in ops and "add" in ops)
    # 空文本
    d = paragraph_diff.char_diff("", "abc")
    check("空→有 → 全 add", all(op["op"] == "add" for op in d))
    d = paragraph_diff.char_diff("abc", "")
    check("有→空 → 全 del", all(op["op"] == "del" for op in d))
    # 大文本降级
    d = paragraph_diff.char_diff("a" * 10000, "b" * 10000)
    check("大文本降级 → 2 ops", len(d) == 2)


def test_summarize_diff():
    print("\n=== summarize_diff ===")
    d = paragraph_diff.char_diff("abc", "axc")
    s = paragraph_diff.summarize_diff(d)
    check("统计含 unchanged/added/deleted", 
          all(k in s for k in ("unchanged_chars", "added_chars", "deleted_chars")))
    check("total = sum", 
          s["total_chars"] == s["unchanged_chars"] + s["added_chars"] + s["deleted_chars"])


def test_style_features():
    print("\n=== style_drift ===")
    # 空文本
    f = style_drift.extract_style_features("")
    check("空文本 → 空 dict", f == {})
    # 正常文本
    text = "他走进房间。她看着窗外，心中思绪万千。\"你来了？\"他问道。"
    f = style_drift.extract_style_features(text)
    check("提取句长", f.get("avg_sentence_length", 0) > 0)
    check("提取词汇丰富度", 0 < f.get("vocab_richness", 0) <= 1)
    check("提取标点密度", f.get("punct_density_per_100", 0) > 0)
    check("提取对话比例", f.get("dialogue_ratio", 0) > 0)
    # 对话比例：含引号的文本
    text2 = '"你好。"他说。"你好。"她回答。'
    f2 = style_drift.extract_style_features(text2)
    check("对话比例 > 0", f2.get("dialogue_ratio", 0) > 0)
    # 无对话文本
    text3 = "他走进房间。她看着窗外。"
    f3 = style_drift.extract_style_features(text3)
    check("无对话 → 对话比例 = 0", f3.get("dialogue_ratio", 1) == 0)


def test_split_paragraphs():
    print("\n=== refine_paragraph.split_paragraphs ===")
    # 基本切分
    text = "第一段。\n\n第二段。\n\n第三段。"
    paras = refine_paragraph.split_paragraphs(text)
    check("基本切分 → 3 段", len(paras) == 3)
    # 单换行 fallback：空行切不出多段时按行切
    text2 = "第一行\n第二行"
    paras2 = refine_paragraph.split_paragraphs(text2)
    check("单换行 fallback → 2 段", len(paras2) == 2)
    # 多空行
    text3 = "A\n\n\n\nB"
    paras3 = refine_paragraph.split_paragraphs(text3)
    check("多空行 → 2 段", len(paras3) == 2)
    # 空文本
    paras4 = refine_paragraph.split_paragraphs("")
    check("空文本 → 0 段", len(paras4) == 0)


def test_paragraph_history():
    print("\n=== refine_paragraph.list_paragraph_history ===")
    # 无历史 → 空列表
    records = refine_paragraph.list_paragraph_history(999, 0)
    check("无历史 → 空列表", records == [])


def test_restore_invalid():
    print("\n=== refine_paragraph.restore_paragraph_version ===")
    # 无效章节号
    ok, msg = refine_paragraph.restore_paragraph_version(0, 0, 1)
    check("无效章节号 → False", not ok)
    # 无效段落号
    ok, msg = refine_paragraph.restore_paragraph_version(1, -1, 1)
    check("无效段落号 → False", not ok)
    # 无效历史 ID
    ok, msg = refine_paragraph.restore_paragraph_version(1, 0, 0)
    check("无效历史 ID → False", not ok)


def main():
    print("=" * 70)
    print("段落级精修轴自检（零 LLM）")
    print("=" * 70)
    for fn in (test_char_diff, test_summarize_diff, test_style_features,
               test_split_paragraphs, test_paragraph_history, test_restore_invalid):
        try:
            fn()
        except Exception as e:
            import traceback
            print("  [ERROR] %s → %s" % (fn.__name__, e))
            traceback.print_exc()
            FAIL.append(fn.__name__)
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
