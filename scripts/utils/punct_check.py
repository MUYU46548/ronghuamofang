# -*- coding: utf-8 -*-
"""标点规范校验（第一波防御工程 · 任务一：字数与标点硬校验）。

## 与 utils/text_norm 的分工（别重复造轮子）

- text_norm = **确定性修复**：半角→全角、直角引号→弯引号、重复标点折叠、
  省略号/破折号归一……挂在 7 处落盘点，**能修的它都修了**。
- 本模块 = **校验**：修复之后**仍然**不规范、或根本不是归一化能修的形态
  → 报出来，由 `verify_chapter.check_chapter` 拦截（打回重生成 / 待人工）。

## 判据分级（与 verify_chapter「宁可漏判不可误伤」纪律一致）

打回重生成要烧一整章的钱，误伤一个好章就是白烧 —— 所以：

**errors（硬拦）**：

1. 成对标号不成对 —— 只列**跨段引用极罕见**的符号：单引号 ‘’、书名号 《》〈〉、
   圆括号 （）、六角括号 〔〕、方头括号 【】、直角引号 「」『』。
   ⚠️ 双引号 “” **刻意不做硬拦**：中文排版规范允许引语跨段
   （每段段首用前引号、末段才收尾），全文计数不配对是**合法形态** ——
   硬拦的误伤代价是白烧一整章重写。它降级为 warning（人眼复核）。
2. 连续重复标点（同字符 ≥2）：。，；：！？、
   —— 省略号 …、破折号 —、间隔号 · 刻意排除（……/—— 本来就是成对字符）。
   text_norm 的归一化已把这类折叠成单个，**残留下来的都是没过归一化的产物**
   （直接改文件绕过挂载点 / 归一化失败），拦下正合适。

**warnings（只留痕不拦）**：

3. 双引号计数差（疑似未配对或跨段引语）。
4. 段落句末无标点的段数（小说里偶有以动作/留白收尾的段，不值得拦）。
5. 汉字语境半角标点残留（归一化漏网兜底）。

## 保护区（与 text_norm 一致）

`<!-- -->` 注释块与 ``` 代码围栏**整块跳过**：quality/summary 注释里全是 ASCII，
不剥会全数误报（这是 text_norm 踩过的坑，判据侧同样要守）。
"""
import re
from pathlib import Path

from utils.file_io import read_text

CJK = "一-鿿㐀-䶿"
CJK_RE = re.compile("[" + CJK + "]")

# ---- 硬拦 1：成对标号配对（只列跨段引用极罕见的符号；双引号刻意不在其中）----
# (open, close, 名称)
PAIR_SYMBOLS = [
    ("‘", "’", "单引号 ‘’"),
    ("《", "》", "书名号 《》"),
    ("〈", "〉", "单书名号 〈〉"),
    ("（", "）", "圆括号 （）"),
    ("〔", "〕", "六角括号 〔〕"),
    ("【", "】", "方头括号 【】"),
    ("「", "」", "直角引号 「」"),
    ("『", "』", "双直角引号 『』"),
]

# ---- 硬拦 2：连续重复标点（同字符 ≥2；…—· 是成对/特殊字符，排除）----
REPEAT_PUNCT = re.compile("([。，；：！？、])\\1+")

# ---- 警告：双引号计数差 ----
DBL_QUOTE = ("“", "”")

# ---- 警告：句末合法收尾字符 ----
END_OK = set("。，！？；：…”’」』）》…—.!?")


def _strip_protected(text):
    """剥去保护区（注释块 / 代码围栏），返回可校验的正文。"""
    parts = re.split("(^```.*?^```)", text, flags=re.S | re.M)
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            continue                                   # 围栏块整块跳过
        out.append(re.sub("<!--.*?-->", "", seg, flags=re.S))
    return "\n".join(out)


def _body_paras(text):
    """正文段落列表：非空、非标题行。"""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def check_punctuation(text):
    """标点规范校验，返回 `(errors, warnings, metrics)`。

    - errors：必须拦下（不成对配对 / 连续重复标点）。
    - warnings：只留痕（双引号计数差 / 句末无标点 / 半角残留）。
    - metrics：给留痕用的计数明细。
    """
    errors, warnings, metrics = [], [], {}
    body = _strip_protected(text or "")
    if not body.strip():
        return errors, warnings, metrics

    # ---- 1 成对标号配对 ----
    for op, cl, name in PAIR_SYMBOLS:
        n_open, n_close = body.count(op), body.count(cl)
        if n_open != n_close:
            errors.append("标点不成对：%s 开 %d 个 / 合 %d 个" % (name, n_open, n_close))

    # ---- 2 连续重复标点 ----
    repeats = REPEAT_PUNCT.findall(body)
    if repeats:
        detail = "、".join("%sx%d" % (c, repeats.count(c)) for c in sorted(set(repeats)))
        errors.append("连续重复标点：%s（同字符连写 ≥2）" % detail)

    # ---- 3 双引号计数差（warning：跨段引语合法，不硬拦）----
    n_open, n_close = body.count(DBL_QUOTE[0]), body.count(DBL_QUOTE[1])
    metrics["quote_open"] = n_open
    metrics["quote_close"] = n_close
    if n_open != n_close:
        warnings.append("双引号开 %d / 合 %d 不等（若非跨段引语则为未配对，请复核）"
                        % (n_open, n_close))

    # ---- 4 段落句末无标点（warning）----
    paras = _body_paras(body)
    no_end = [p for p in paras if p[-1] not in END_OK]
    metrics["paragraphs"] = len(paras)
    metrics["no_end_punct"] = len(no_end)
    if no_end and len(no_end) <= max(2, len(paras) // 3):
        warnings.append("%d/%d 个段落句末无标点（如 %r）"
                        % (len(no_end), len(paras), no_end[0][:30]))

    # ---- 5 汉字语境半角标点残留（warning）----
    half = re.findall("(?<=[%s])[,;:!?]|(?<=[,;:!?])[%s]" % (CJK, CJK), body)
    metrics["halfwidth残留"] = len(half)
    if half:
        warnings.append("汉字语境半角标点残留 %d 处（归一化漏网，建议核查落盘挂载点）"
                        % len(half))

    return errors, warnings, metrics


def check_punctuation_file(path):
    """按文件路径校验（读不到文件按空文本处理，不抛异常）。"""
    p = Path(path)
    try:
        text = read_text(p) if p.exists() else ""
    except Exception:                                     # noqa: BLE001
        text = ""
    return check_punctuation(text)
