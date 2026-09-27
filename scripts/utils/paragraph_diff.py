# -*- coding: utf-8 -*-
"""段落级 diff 工具（与 console/src/diff.js 的 charDiff 对齐）。

用于段落重写前后的字符级 diff，输出结构化 diff 记录。
"""
import re
from pathlib import Path


def char_diff(a, b):
    """字符级 diff，返回 [{op: '='|'add'|'del', text: str}]。
    与 console/src/diff.js 的 charDiff 逻辑一致。
    """
    s1 = a or ""
    s2 = b or ""
    m, n = len(s1), len(s2)
    if m * n > 4000000:
        return [{"op": "=", "text": s1}, {"op": "add", "text": s2}]
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            dp[i][j] = dp[i - 1][j - 1] + 1 if s1[i - 1] == s2[j - 1] else max(dp[i - 1][j], dp[i][j - 1])
    ops = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and s1[i - 1] == s2[j - 1]:
            ops.insert(0, {"op": "=", "text": s1[i - 1]})
            i -= 1; j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            ops.insert(0, {"op": "add", "text": s2[j - 1]})
            j -= 1
        else:
            ops.insert(0, {"op": "del", "text": s1[i - 1]})
            i -= 1
    # 合并相邻同 op
    merged = []
    for op in ops:
        if merged and merged[-1]["op"] == op["op"]:
            merged[-1]["text"] += op["text"]
        else:
            merged.append(dict(op))
    return merged


def summarize_diff(diff_ops):
    """汇总 diff 统计。"""
    unchanged_chars = sum(len(op["text"]) for op in diff_ops if op["op"] == "=")
    added_chars = sum(len(op["text"]) for op in diff_ops if op["op"] == "add")
    deleted_chars = sum(len(op["text"]) for op in diff_ops if op["op"] == "del")
    total_chars = unchanged_chars + added_chars + deleted_chars
    if total_chars == 0:
        change_ratio = 0.0
    else:
        change_ratio = (added_chars + deleted_chars) / total_chars
    return {
        "unchanged_chars": unchanged_chars,
        "added_chars": added_chars,
        "deleted_chars": deleted_chars,
        "total_chars": total_chars,
        "change_ratio": round(change_ratio, 3),
        "is_major_change": change_ratio > 0.5,
    }


def diff_paragraph_to_record(original, rewritten, context=None):
    """生成完整的段落 diff 记录。"""
    ops = char_diff(original, rewritten)
    summary = summarize_diff(ops)
    record = {
        "ops": ops,
        "summary": summary,
    }
    if context:
        record["context"] = context
    return record


def format_diff_for_display(diff_ops, max_line=80):
    """格式化为终端可读文本。"""
    lines = []
    current_line = ""
    for op in diff_ops:
        text = op["text"]
        if op["op"] == "=":
            current_line += text
        elif op["op"] == "add":
            current_line += f"[+{text}+]"
        elif op["op"] == "del":
            current_line += f"[-{text}-]"
        if len(current_line) >= max_line:
            lines.append(current_line)
            current_line = ""
    if current_line:
        lines.append(current_line)
    return "\n".join(lines)
