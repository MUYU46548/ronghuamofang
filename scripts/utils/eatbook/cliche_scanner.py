"""模块⑦ 病灶词表扫描器 —— 纯本地实现，零额度，只检测、不改写。

出门版提示卡见 ``prompts/07_病灶词表扫描.md``（供对话内顺带检查用；
本模块功能等价，不需要任何模型）。结构契约见
``schemas/cliche_report.schema.json``。

用法::

    from chishu import cliche_scanner

    report = cliche_scanner.scan(text)                       # 默认词表
    report = cliche_scanner.scan(text, wordlist=my_terms)    # 自定义词表
    print(cliche_scanner.format_report(report))

计数口径：字面子串计数（词表词之间不做长词优先去重，"眼底闪过一丝"与
"一丝"会各自命中——这是词表扫描的既定行为，看排行时留意即可）。
"""

from typing import Dict, List, Optional

from .wordlist import Term, load_wordlist

__all__ = ["scan", "scan_file", "format_report"]

_CONTEXT_PAD = 12  # 位置示例前后各取的字符数


def _excerpt(text: str, start: int, end: int) -> str:
    left = max(0, start - _CONTEXT_PAD)
    right = min(len(text), end + _CONTEXT_PAD)
    snippet = text[left:right].replace("\r", " ").replace("\n", " ")
    rel_start = start - left
    rel_end = rel_start + (end - start)
    return snippet[:rel_start] + "【" + snippet[rel_start:rel_end] + "】" + snippet[rel_end:]


def scan(text: str, wordlist: Optional[List[Term]] = None) -> Dict[str, object]:
    """扫描正文，返回命中清单与密度排行（结构见 schema）。

    密度 = 次数 / 去空白后总字数 × 1000，保留两位小数。
    """
    terms = wordlist if wordlist is not None else load_wordlist()
    total_chars = len("".join(text.split()))
    hits: List[Dict[str, object]] = []
    total_count = 0
    for term in terms:
        matches = list(term.pattern.finditer(text))
        count = len(matches)
        if count == 0:
            continue
        first = matches[0]
        density = round(count * 1000.0 / total_chars, 2) if total_chars else 0.0
        hits.append({
            "词": term.name,
            "次数": count,
            "位置示例": _excerpt(text, first.start(), first.end()),
            "密度每千字": density,
        })
        total_count += count
    hits.sort(key=lambda h: (-h["次数"], str(h["词"])))  # type: ignore[arg-type, return-value]
    average = round(total_count * 1000.0 / total_chars, 2) if total_chars else 0.0
    return {
        "总字数": total_chars,
        "词表条目数": len(terms),
        "总命中次数": total_count,
        "平均密度每千字": average,
        "命中": hits,
        "排行": [[h["词"], h["次数"]] for h in hits],  # type: ignore[list-item]
    }


def scan_file(path: str, wordlist: Optional[List[Term]] = None) -> Dict[str, object]:
    """扫描 UTF-8 文本文件。"""
    with open(path, "r", encoding="utf-8") as fh:
        return scan(fh.read(), wordlist=wordlist)


def format_report(report: Dict[str, object]) -> str:
    """把扫描结果排成出门版同款的三段式报告（不含改写建议）。"""
    lines = [
        "病灶词表扫描报告（只检测、不改写）",
        "正文总字数：%s ｜ 词表 %s 条 ｜ 总命中 %s 次 ｜ 平均密度 %s/千字" % (
            report["总字数"], report["词表条目数"], report["总命中次数"], report["平均密度每千字"]),
        "",
        "一、命中明细",
    ]
    hits = report["命中"]  # type: ignore[assignment]
    if hits:
        for hit in hits:
            lines.append("- %s｜%s 次｜密度 %s/千字｜示例：%s" % (
                hit["词"], hit["次数"], hit["密度每千字"], hit["位置示例"]))
    else:
        lines.append("（无命中）")
    lines.append("")
    lines.append("二、密度排行（词频从高到低）")
    ranking = report["排行"]  # type: ignore[assignment]
    if ranking:
        for i, (word, count) in enumerate(ranking, 1):
            lines.append("%d. %s（%s 次）" % (i, word, count))
    else:
        lines.append("（无命中）")
    lines.append("")
    lines.append("（本报告不含改写建议，不评价情节文笔）")
    return "\n".join(lines)
