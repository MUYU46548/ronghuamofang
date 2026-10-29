# -*- coding: utf-8 -*-
"""判定引擎的文本层：分段 / 分句 / 定位 / 计数（确定性，零模型）。

为什么单独一层：所有规则都要回答「第几段、哪一句」，如果每支规则各写一套切分，
同一句话在不同规则里会得到不同段号，缺陷单就会出现"同句两个位置"的自相矛盾。
段号从 1 开始，与界面「第 3 段」一致。
"""
from __future__ import annotations

import re

# 句末（含中文省略号与分号：判定按"一句话"切，不按语法句切）
SENT_END = "。！？…；!?;"
# 句末之后可能还有收尾引号，不另起一句
CLOSERS = "”」』）)]】"

MD_COMMENT = re.compile(r"<!--[\s\S]*?-->")


def strip_markup(text: str) -> str:
    """去掉 HTML 注释与标题行标记，保留正文（判定只看正文）。"""
    t = MD_COMMENT.sub("", text or "")
    return t


def paragraphs(text: str) -> list:
    """正文段落（空行分隔；标题行不参与判定）。

    为什么保留段落内部换行：真实稿件常见"一句一行"，按行切会把一段拆成多段，
    段号就与界面对不上。
    """
    t = strip_markup(text)
    out = []
    for block in re.split(r"\n\s*\n", t):
        kept = [ln.strip() for ln in block.splitlines() if ln.strip()]
        kept = [ln for ln in kept if not ln.startswith("#")]
        if kept:
            out.append("\n".join(kept))
    return out


def split_sentences(para: str) -> list:
    """把一段切成句子（保留句末标点；收尾引号跟在上一句）。"""
    out, buf, pending = [], [], False
    for ch in para or "":
        buf.append(ch)
        if ch in SENT_END:
            pending = True
            continue
        if pending:
            if ch in CLOSERS:
                continue
            out.append("".join(buf[:-1]))
            buf = [ch]
            pending = False
    if buf:
        out.append("".join(buf))
    return [s.strip() for s in out if s and s.strip()]


def count_cn_words(text: str) -> int:
    """字数口径与全仓一致（`verify_chapter.count_cn_words`：去空白与 Markdown 符号后的字符数）。"""
    stripped = re.sub(r"[#*`>_|\[\]()!\-]", "", text or "")
    stripped = re.sub(r"\s+", "", stripped)
    return len(stripped)


def iter_sentences(text: str):
    """逐句遍历：产出 (段号, 段内序号, 句子)。段号 1 起。"""
    for pi, para in enumerate(paragraphs(text), 1):
        for si, sent in enumerate(split_sentences(para), 1):
            yield pi, si, sent


def locate(text: str, sentence: str):
    """在正文里定位一句话：返回 (段号, 段内序号) 或 None。

    **引文校验的底座**（AC-14-1 / AC-09-3）：定位不到就不许进缺陷单 ——
    宁可少报，也不许给出对不上原文的段号。
    """
    target = (sentence or "").strip()
    if not target:
        return None
    for pi, si, sent in iter_sentences(text):
        if sent == target:
            return pi, si
    # 允许"包含"（模型/规则给出的句子可能带多余空白或引号外层）
    for pi, si, sent in iter_sentences(text):
        if target and target in sent:
            return pi, si
    return None


def quote_ok(text: str, sentence: str) -> bool:
    """原句是否真的存在于正文（缺陷单第一道门）。"""
    return locate(text, sentence) is not None


def snippet(sentence: str, limit: int = 20) -> str:
    s = (sentence or "").strip()
    return s if len(s) <= limit else s[:limit] + "…"


def dialogue_count(text: str) -> int:
    """对话句数（含「」“”『』或对话标签「……说」）。"""
    n = 0
    for _pi, _si, sent in iter_sentences(text):
        if re.search(r"[「『]|[“”]", sent) or re.search(r"(说|问|答|道)[：:「“]", sent):
            n += 1
    return n


def paragraph_count(text: str) -> int:
    return len(paragraphs(text))
