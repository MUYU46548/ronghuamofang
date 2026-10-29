"""wordlist —— 模块⑦ 的病灶词表：默认词表与自定义词表加载。

词表文件格式（UTF-8，一行一条）：
- 普通行 = 字面词，按原文精确匹配；
- ``regex:`` 前缀 = 正则（如 ``regex:仿佛[^，。！？\\n]{0,12}一般``）；
- ``#`` 开头 = 注释；空行忽略。
"""

import re
from typing import List, NamedTuple, Optional

__all__ = ["Term", "DEFAULT_WORDLIST", "make_term", "load_wordlist"]


class Term(NamedTuple):
    """一条病灶词：``name`` 为显示名（词表原文），``pattern`` 为编译后的正则。"""

    name: str
    pattern: re.Pattern


# 需求卡给出的公开 AI 高频词，共 20 条；"仿佛…一般"为通配模式，按正则实现。
DEFAULT_WORDLIST = [
    "眼底闪过一丝",
    "勾唇",
    "眸光",
    "薄唇轻启",
    "邪魅一笑",
    "似有若无",
    "莫名的",
    "一丝",
    "仿佛…一般",
    "如墨的长发",
    "语气不禁",
    "心中一动",
    "面色一变",
    "淡淡地道",
    "微微一笑",
    "深吸一口气",
    "目光深邃",
    "嘴角勾起",
    "萦绕",
    "氤氲",
]


def make_term(entry: str) -> Optional[Term]:
    """把一条词表行编译为 :class:`Term`；注释/空行返回 None。"""
    entry = entry.strip()
    if not entry or entry.startswith("#"):
        return None
    if entry.startswith("regex:"):
        raw = entry[len("regex:"):].strip()
        return Term(name=raw, pattern=re.compile(raw))
    return Term(name=entry, pattern=re.compile(re.escape(entry)))


def default_wordlist() -> List[Term]:
    """默认 20 条词表；同名重复条目去重，保留首次出现。"""
    terms: List[Term] = []
    seen = set()
    for entry in DEFAULT_WORDLIST:
        if entry == "仿佛…一般":
            term = Term(name=entry, pattern=re.compile(r"仿佛[^，。！？\n]{0,12}一般"))
        else:
            term = make_term(entry)
        if term is not None and term.name not in seen:
            seen.add(term.name)
            terms.append(term)
    return terms


def load_wordlist(path: Optional[str] = None) -> List[Term]:
    """加载词表；``path`` 为空则返回默认词表。"""
    if path is None:
        return default_wordlist()
    terms: List[Term] = []
    seen = set()
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            term = make_term(line)
            if term is not None and term.name not in seen:
                seen.add(term.name)
                terms.append(term)
    if not terms:
        raise ValueError("词表文件为空：%s" % path)
    return terms
