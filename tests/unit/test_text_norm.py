# -*- coding: utf-8 -*-
"""中文标点归一化（utils/text_norm）的回归锁。

背景（2026-10-18 取证）：用户工作区《雾港核心》成品 `refined/01.md` 含 76 个半角 `"、
0 个 `“”`；`02.md` 含 39 对 `「」`；成品 docx 里 `“` 出现 0 次、`「` 67 次。
stage6 提示词早就写着「全角标点」，产物照旧 —— 所以这里锁的是**确定性替换**行为。

锁五件事：

1. **引号**：半角 `" `按开合配对转 `“”`；`「」`→`“”`、`『』`→`‘’`；孤儿引号保持原样。
2. **破折号/省略号**：单 `—`、`--`、`―` → `——`；`...` / `。。。` / 单 `…` → `……`。
3. **保护区**：`<!-- quality: … -->` 注释与 ``` 代码围栏一字不动（注释里全是 ASCII，
   归一化它会把质量注释改坏，下游 QUALITY_RE 就再也匹配不上）。
4. **幂等**：`f(f(x)) == f(x)` —— 挂载点有 4 处（stage4/5/6 落盘后 + stage7 合并前），
   重复跑是常态；不幂等就会出现「跑两遍结果不同」的灵异问题。
5. **不误伤英文**：`don't`、`--flag`、`(a+b)` 这类不含汉字的英文形态保持原样。

用法：python tests/unit/test_text_norm.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.text_norm import (                          # noqa: E402
    normalize_chinese_punctuation,
    normalize_chinese_punctuation_with_counts,
)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


def eq(name, got, want):
    check(name, got == want, "got=%r want=%r" % (got, want))


# ---------------- 1 引号 ----------------

def test_quotes():
    eq("半角双引号配对转全角",
       normalize_chinese_punctuation('"沈砚？"他说。'), "“沈砚？”他说。")
    eq("行首孤儿开引号",
       normalize_chinese_punctuation('"门上没有第二块匾。"'), "“门上没有第二块匾。”")
    eq("跨段各自配对（不跨段配对）",
       normalize_chinese_punctuation('"嗯。"\n\n"走吧。"'), "“嗯。”\n\n“走吧。”")
    eq("「」→“”",
       normalize_chinese_punctuation("「二十八个。」程砧数完，「栈道封死了。」"),
       "“二十八个。”程砧数完，“栈道封死了。”")
    got = normalize_chinese_punctuation("外『内』外")
    eq("『』成对转单引号", got, "外‘内’外")
    # 奇数个半角引号：最后一个保持原样，不硬转
    eq("孤儿引号保持原样", normalize_chinese_punctuation('"说了半句'), '"说了半句')
    # 单引号：只有前后都是汉字/中文标点才转
    eq("中文语境单引号转全角", normalize_chinese_punctuation("他说'嗯'就走"),
       "他说‘嗯’就走")
    eq("英文缩写撇号不动", normalize_chinese_punctuation("don't touch"),
       "don't touch")


# ---------------- 2 破折号 / 省略号 ----------------

def test_dash_ellipsis():
    eq("单个 em dash 补成双", normalize_chinese_punctuation("力道恰到好处—不是蛮力"),
       "力道恰到好处——不是蛮力")
    eq("已经是双破折号不动", normalize_chinese_punctuation("力道恰到好处——不是蛮力"),
       "力道恰到好处——不是蛮力")
    eq("两个 ASCII 连字符转双破折号", normalize_chinese_punctuation("他说--然后走了"),
       "他说——然后走了")
    eq("三个以上点转中文省略号", normalize_chinese_punctuation("如果...那么"),
       "如果……那么")
    eq("连续中文句号转省略号", normalize_chinese_punctuation("这样。。。"),
       "这样……")
    eq("单个省略号补成两格", normalize_chinese_punctuation("他…没有说"),
       "他……没有说")
    eq("已是两格省略号不动", normalize_chinese_punctuation("他……没有说"),
       "他……没有说")


# ---------------- 3 其它半角标点 ----------------

def test_halfwidth_and_punct():
    eq("汉字间半角逗号", normalize_chinese_punctuation("他来了,然后坐下"),
       "他来了，然后坐下")
    eq("汉字间半角问号", normalize_chinese_punctuation("你听见了吗?"),
       "你听见了吗？")
    eq("汉字间半角冒号/分号", normalize_chinese_punctuation("他说:好;行"),
       "他说：好；行")
    eq("括号内含汉字转全角", normalize_chinese_punctuation("这是(测试)内容"),
       "这是（测试）内容")
    eq("纯英文括号不动", normalize_chinese_punctuation("(a + b) * c"),
       "(a + b) * c")
    eq("连续重复标点合并", normalize_chinese_punctuation("好！！真的好"),
       "好！真的好")
    eq("汉字间空格清除", normalize_chinese_punctuation("沈 砚 放 下 卡钳"),
       "沈砚放下卡钳")
    eq("零宽字符删除", normalize_chinese_punctuation("他\u200b走了"),
       "他走了")


# ---------------- 4 保护区 ----------------

def test_protected_blocks():
    src = '<!-- quality: 8/10 -->\n"嗯。"\n<!-- summary: 第1章, 事件: A/B -->\n'
    out = normalize_chinese_punctuation(src)
    check("注释块原样保留", "<!-- quality: 8/10 -->" in out
          and "<!-- summary: 第1章, A/B -->" not in out.replace(
              "<!-- summary: 第1章, 事件: A/B -->", "<!-- summary: 第1章, 事件: A/B -->"))
    check("注释里的半角标点不被改", "quality: 8/10" in out and "summary: 第1章" in out)
    check("注释外的正文照常归一", "“嗯。”" in out)

    code = "```\nuse --flag (a,b) \"quoted\"\n```\n\"正文。\"\n"
    out2 = normalize_chinese_punctuation(code)
    check("代码围栏整块原样", 'use --flag (a,b) "quoted"' in out2)
    check("围栏外正文归一", "“正文。”" in out2)


# ---------------- 5 幂等 + 计数 ----------------

def test_idempotent_and_counts():
    samples = [
        '"沈砚？"他说。「嗯。」这是(测试)力道—不是,蛮力...真的',
        "『内』外『内』外 他说'嗯' don't (a+b) --flag",
        '<!-- quality: 7/10 -->\n"第一段。"\n\n第二段--继续\u2026\n',
        "「二十八个。」程砧数完，「栈道封死了。」\r\n「嗯。」",
    ]
    for i, s in enumerate(samples, 1):
        once = normalize_chinese_punctuation(s)
        twice = normalize_chinese_punctuation(once)
        eq("样本%d 幂等" % i, twice, once)

    text, counts = normalize_chinese_punctuation_with_counts('"嗯。"「走。」力道—不是')
    check("计数非空且为 dict", isinstance(counts, dict) and sum(counts.values()) > 0,
          counts)
    check("计数含引号项", counts.get("ascii_double_quote", 0) > 0
          or counts.get("cjk_bracket_quote", 0) > 0, counts)

    # 空输入不炸
    eq("空串原样", normalize_chinese_punctuation(""), "")


# ---------------- 6 真实产物片段（取证样本） ----------------

def test_real_samples():
    # 取自 refined/01.md 原文（半角引号 + 单破折号）
    line = '"沈砚？"'
    eq("真实片段：对话行", normalize_chinese_punctuation(line), "“沈砚？”")
    line2 = "他视线掠过屋内—铜齿轮、未完成的机关臂—最后回到沈砚脸上。"
    eq("真实片段：叙述行破折号", normalize_chinese_punctuation(line2),
       "他视线掠过屋内——铜齿轮、未完成的机关臂——最后回到沈砚脸上。")
    # 取自 refined/02.md（直角引号）
    line3 = "「我走门，你走水。」他摸出一面旧铁牌"
    eq("真实片段：直角引号", normalize_chinese_punctuation(line3),
       "“我走门，你走水。”他摸出一面旧铁牌")
    # 真实产物残留（首次验收当场抓出的漏网形态）：**句中**引号，前后都是汉字。
    # 第一版「前一字符是否为开符号」的启发式漏掉了它 —— 这条断言锁住回归。
    line4 = "他以为这就是程砧说的\"想通\"，就发生在夜里子时前后。"
    eq("真实片段：句中引号（曾漏判）", normalize_chinese_punctuation(line4),
       "他以为这就是程砧说的“想通”，就发生在夜里子时前后。")
    line5 = "第七锁落定时说出\"老师，第七锁我替你落\"；核心重新封印。"
    eq("真实片段：句中长引号", normalize_chinese_punctuation(line5),
       "第七锁落定时说出“老师，第七锁我替你落”；核心重新封印。")
    # 奇数个引号（孤儿）：只转能配对的，最后一个保持原样，不硬造配对
    line6 = '他说"只有一半'
    eq("真实片段：孤儿引号保持原样", normalize_chinese_punctuation(line6),
       '他说"只有一半')


def main():
    test_quotes()
    test_dash_ellipsis()
    test_halfwidth_and_punct()
    test_protected_blocks()
    test_idempotent_and_counts()
    test_real_samples()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
