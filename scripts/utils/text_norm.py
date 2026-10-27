# -*- coding: utf-8 -*-
"""中文标点归一化（确定性、幂等、零 LLM 成本）。

## 为什么必须是代码，不是提示词（2026-10-18 取证）

用户工作区《雾港核心》3 章成品实测：`refined/01.md` 含 **76 个半角 `"`、0 个 `“”`**，
`02.md` 含 **39 对「」**，成品 docx 里 `“` 出现 **0 次**、`「` **67 次**。
而 stage6 提示词里早就写着「所有标点使用全角中文标点」—— 提示词写了，产物照旧。
**这类形态错误的正确治理方式是确定性替换**：可单测、可幂等、可回归、可事后验收，
提示词对它的命中率既不稳定也无法验证。

规范来源（用户自己的校对咒语 `文本校对咒语.md`）：
「确保正确使用全角中文标点，注意中文引号为双引号和单引号，绝非「」」。

## 设计约束

- **幂等**：`f(f(x)) == f(x)`，任何规则重复应用都不改变结果（挂载点有 4 处，重复跑是常态）。
- **保守**：只改「形态可判定」的东西。判断不了的（行尾标点、`. 的` 半角句点、
  英文缩写里的撇号、小数点）一律不动 —— 误改正文比留着脏标点更糟。
- **保护段**：`<!-- ... -->` 注释块（quality / summary 注释，内含 ASCII）
  与 ``` 代码围栏整块跳过，不参与归一化。
"""
import re
from pathlib import Path

from utils.file_io import read_text, write_text

CJK = "\u4e00-\u9fff\u3400-\u4dbf"
CJK_RE = re.compile("[" + CJK + "]")

_ZERO_WIDTH = re.compile("[\u200b-\u200f\ufeff\u00ad\u2060]")
# 破折号家族：先归一到 —，再把单个 — 补成 ——（中文破折号是两个 em dash）
_DASH_FAMILY = re.compile("[\u2014\u2015\u2012\u2013\u2500\u2501-]+")
_ELLIPSIS_DOTS = re.compile(r"\.{3,}")
_ELLIPSIS_DOTS_CN = re.compile("。{2,}")
_ELLIPSIS_SPARSE = re.compile("(?<!…)…(?!…)")
# 汉字/中文收尾标点语境下的半角标点
_HALF_PUNCT = re.compile("(?<=[%s。！？…”】》」』）])[,;:!?]" % CJK)
# 括号内含汉字 → 全角括号（英文缩写 / 公式不碰：内部无汉字）
_PAREN_CJK = re.compile(r"\(([^()\n]*[" + CJK + r"][^()\n]*)\)")
_REPEAT_PUNCT = re.compile("([，。；：、！？!?])\\1+")
_CJK_SPACE = re.compile("(?<=[%s])[ \t]+(?=[%s])" % (CJK, CJK))

_QUOTE_TRANS = str.maketrans({
    "\u300c": "\u201c", "\u300d": "\u201d",     # 「」 → “”
    "\u300e": "\u2018", "\u300f": "\u2019",     # 『』 → ‘’（内层单引号）
    "\u201c": "\u201c", "\u201d": "\u201d",
})

# 开引号判定：前一个字符属于这些（含行首）→ 认为是「开」
_QUOTE_OPEN_PREV = set("，。；：！？、…—\u3000 \t（〔【《〈『「“‘\"'([{,;:!?")
# 结尾语境：这些字符后面跟的 " 通常是闭引号（前面没有开引号的孤儿引号）
_QUOTE_CLOSE_PREV = set("”’】》」』）…—")


def _pair_ascii_double(line):
    """半角 `" ` 按开合配对转 `“”`。

    ## 判据（2026-10-18 修正：改为「行内交替」为主）

    第一版用「前一字符是否为开符号」判定，结果漏掉了**句中引号**：
    `程砧说的"想通"，就发生在夜里` —— 前一字符是汉字「的」，被判成"不动"，
    于是真实产物《雾港核心》01/03 章残留 6 个半角引号（验收脚本当场抓出）。

    现在以**行内计数**为准：偶数个 → 严格开合交替（句中、句首都正确）；
    奇数个 → 前 n-1 个交替，最后一个**保持原样**（孤儿引号，宁可留脏，
    不可凭空造出一个配对）。中文小说一行通常就是一句，行内配对足够稳。
    """
    pos = [i for i, ch in enumerate(line) if ch == '"']
    if not pos:
        return line
    chars = list(line)
    even = len(pos) % 2 == 0
    limit = len(pos) if even else len(pos) - 1
    for k in range(limit):
        chars[pos[k]] = "\u201c" if k % 2 == 0 else "\u201d"
    return "".join(chars)


def _pair_ascii_single(line):
    """半角 `' ` → `‘’`。**只在前后都是汉字/中文标点时才动**（避开英文缩写 don't）。

    同双引号一样按「候选位成对处理」：候选不足 2 个 → 一个都不动
    （半开的 `‘嗯' 比不改更糟）。
    """
    cand = []
    for i, ch in enumerate(line):
        if ch != "'":
            continue
        prev = line[i - 1] if i else ""
        nxt = line[i + 1] if i + 1 < len(line) else ""
        prev_ok = bool(CJK_RE.match(prev)) or prev in _QUOTE_OPEN_PREV or prev == ""
        # 闭引号后面允许跟空白/中文标点（`'嗯' don't` 这种后面接英文的形态）
        nxt_ok = (bool(CJK_RE.match(nxt)) or nxt in _QUOTE_CLOSE_PREV
                  or nxt in " \t，。；：！？、…—\n")
        if prev_ok and nxt_ok:
            cand.append(i)
    if len(cand) < 2:
        return line
    chars = list(line)
    even = len(cand) % 2 == 0
    limit = len(cand) if even else len(cand) - 1
    for k in range(limit):
        chars[cand[k]] = "\u2018" if k % 2 == 0 else "\u2019"
    return "".join(chars)


def _normalize_prose(text):
    """不含保护区的正文归一化。"""
    counts = {}

    def bump(key, n=1):
        counts[key] = counts.get(key, 0) + n

    if _ZERO_WIDTH.search(text):
        bump("zero_width", len(_ZERO_WIDTH.findall(text)))
        text = _ZERO_WIDTH.sub("", text)

    if "\u300c" in text or "\u300e" in text:
        bump("cjk_bracket_quote", text.count("\u300c") + text.count("\u300e"))
        text = text.translate(_QUOTE_TRANS)

    if '"' in text:
        before = text
        text = "\n".join(_pair_ascii_double(ln) for ln in text.split("\n"))
        bump("ascii_double_quote", before.count('"') - text.count('"'))

    if "'" in text:
        before = text
        text = "\n".join(_pair_ascii_single(ln) for ln in text.split("\n"))
        bump("ascii_single_quote", before.count("'") - text.count("'"))

    if _DASH_FAMILY.search(text):
        def _dash(m):
            run = m.group(0)
            i, j = m.start(), m.end()
            prev = text[i - 1] if i else ""
            nxt = text[j] if j < len(text) else ""

            def _cn(ch):
                return bool(ch) and (CJK_RE.match(ch) is not None
                                     or ch in "，。；：！？、…—“”‘’（）【】《》〈〉\n")

            # 英文语境一律不动：`--flag`、`3-5`、`a-b` 这些不是破折号
            if not (_cn(prev) or _cn(nxt)):
                return run
            return "\u2014\u2014"
        before = text
        text = _DASH_FAMILY.sub(_dash, text)
        bump("dash", 0 if before == text else 1)

    if _ELLIPSIS_DOTS.search(text) or _ELLIPSIS_DOTS_CN.search(text):
        n = len(_ELLIPSIS_DOTS.findall(text)) + len(_ELLIPSIS_DOTS_CN.findall(text))
        text = _ELLIPSIS_DOTS.sub("\u2026\u2026", text)
        text = _ELLIPSIS_DOTS_CN.sub("\u2026\u2026", text)
        bump("ellipsis_dots", n)
    # 单个 … 补成 ……（中文省略号固定两格）
    if _ELLIPSIS_SPARSE.search(text):
        n = len(_ELLIPSIS_SPARSE.findall(text))
        text = _ELLIPSIS_SPARSE.sub("\u2026\u2026", text)
        bump("ellipsis_single", n)

    if _HALF_PUNCT.search(text):
        n = len(_HALF_PUNCT.findall(text))
        text = _HALF_PUNCT.sub(lambda m: {",": "，", ";": "；", ":": "：",
                                          "!": "！", "?": "？"}[m.group(0)], text)
        bump("halfwidth_punct", n)

    if _PAREN_CJK.search(text):
        n = len(_PAREN_CJK.findall(text))
        text = _PAREN_CJK.sub("（\\1）", text)
        bump("paren_cjk", n)

    if _REPEAT_PUNCT.search(text):
        n = len(_REPEAT_PUNCT.findall(text))
        text = _REPEAT_PUNCT.sub("\\1", text)
        bump("repeat_punct", n)

    if _CJK_SPACE.search(text):
        n = len(_CJK_SPACE.findall(text))
        text = _CJK_SPACE.sub("", text)
        bump("cjk_space", n)

    return text, counts


COMMENT_BLOCK = re.compile("(<!--.*?-->)", re.S)
FENCE_BLOCK = re.compile("(^```.*?^```)", re.S | re.M)


# ---------------------------------------------------------------- 章标题格式（2026-10-26）
# 用户第 12 条：「章节名可能有问题，用户期望 `第X章 XXX`，实际上是 `第10章潜七十二`，
# 中间没有空格，是完全粘连的。做个章节名格式自定义不难吧？」
#
# 为什么不能靠提示词：stage4 模板里写的是 `## 第{{n}}章 章节名`（带空格），
# 模型照样会输出 `## 第10章潜七十二` —— 而且 verify_chapter 的 TITLE_RE
# (`^##?\s*第\s*(\d+)\s*章.*$`) 对「章」后的任何东西都宽容，所以这类粘连
# **一路绿灯进成品**。形态问题只能靠确定性归一化治理，与标点同一条纪律。
TITLE_LINE_RE = re.compile(r"^(#{1,6})[ \t]*第[ \t]*(\d+)[ \t]*章[ \t]*(.*)$", re.M)
DEFAULT_TITLE_FORMAT = "第{n}章 {title}"


def load_chapter_title_format():
    """读 `chapter.title_format`（缺省 = `第{n}章 {title}`）。

    只认含 `{n}` 的字符串 —— 不含章号的格式没法保证章号不丢，宁可回退默认。
    配置坏了不抛异常：标题格式是显示问题，不该让整条流水线停下。
    """
    try:
        from utils.config_io import load_config_yaml
        cfg = load_config_yaml("config/system.yaml") or {}
        cand = ((cfg.get("chapter") or {}).get("title_format") or "").strip()
        if cand and "{n}" in cand:
            return cand
    except Exception:                                        # noqa: BLE001
        pass
    return DEFAULT_TITLE_FORMAT


def _render_title_line(fmt, n, title):
    title = (title or "").strip()
    if not title:
        # 没有章名：把 {title} 连同它前面的分隔符一起去掉，不留尾随空格
        s = fmt.replace("{title}", "").replace("{n}", str(n))
        return s.rstrip(" \t·—-—:：")
    return fmt.replace("{n}", str(n)).replace("{title}", title)


def normalize_chapter_title(text, fmt=None):
    """把章标题行统一成 `fmt`（默认 `第N章 章名`，中间有空格）。

    只动**标题行**（`#`~`######` 开头、含 `第N章`），正文里的「第10章」不动。
    幂等：已经是目标格式的标题原样返回。返回 `(新文本, 改动处数)`。
    """
    if not text:
        return text, 0
    fmt = fmt or load_chapter_title_format()
    changed = [0]

    def repl(m):
        hashes, n, rest = m.group(1), m.group(2), m.group(3)
        new = hashes + " " + _render_title_line(fmt, n, rest)
        if new != m.group(0):
            changed[0] += 1
        return new

    return TITLE_LINE_RE.sub(repl, text), changed[0]


def normalize_chinese_punctuation(text):
    """归一化整篇正文：注释块与代码围栏整块跳过，其余按规则处理。

    返回归一化后的文本。幂等。
    """
    if not text:
        return text
    # 先按围栏切，再在非围栏段里按注释切 —— 两层保护，顺序不敏感
    parts = FENCE_BLOCK.split(text)
    out = []
    for i, seg in enumerate(parts):
        if i % 2 == 1:                        # 围栏块原样
            out.append(seg)
            continue
        sub = COMMENT_BLOCK.split(seg)
        for j, s in enumerate(sub):
            out.append(s if j % 2 == 1 else _normalize_prose(s)[0])
    return "".join(out)


def normalize_chapter_file(path):
    """就地归一化一个章节文件。

    返回 `(changed, counts)`；未变化时**不写盘**（避免无谓地刷新 mtime 影响断点续跑）。
    """
    p = Path(path)
    if not p.exists():
        return False, {}
    try:
        text = read_text(p)
    except Exception:                                        # noqa: BLE001
        return False, {}
    # 章标题先归一（`第10章潜七十二` → `第10章 潜七十二`），再洗标点 ——
    # 标题行里的标点同样需要洗，顺序不影响结果。
    text, title_changed = normalize_chapter_title(text)
    new, counts = normalize_chinese_punctuation_with_counts(text)
    if title_changed:
        counts["title"] = counts.get("title", 0) + title_changed
    if new == text and not title_changed:
        return False, counts
    write_text(p, new)
    return True, counts


def normalize_chinese_punctuation_with_counts(text):
    """与 `normalize_chinese_punctuation` 同逻辑，额外返回各类替换计数（供日志）。"""
    if not text:
        return text, {}
    parts = FENCE_BLOCK.split(text)
    out = []
    total = {}
    for i, seg in enumerate(parts):
        if i % 2 == 1:
            out.append(seg)
            continue
        sub = COMMENT_BLOCK.split(seg)
        for j, s in enumerate(sub):
            if j % 2 == 1:
                out.append(s)
            else:
                s2, c = _normalize_prose(s)
                out.append(s2)
                for k, v in c.items():
                    total[k] = total.get(k, 0) + v
    return "".join(out), total
