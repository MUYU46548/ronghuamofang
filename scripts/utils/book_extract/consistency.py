"""模块① 一致性检查（不重读原文：只读②③的卡）。

出门版提示卡见 ``prompts/01_一致性检查.md``；行式输出与结构化契约见
``schemas/conflict_report.schema.json``。

解析器同时接受两种产出：
- 行式（出门版默认）``[类型：人设漂移] B中短语｜A中依据｜高``；
- JSON 数组（结构化契约形态）。
全文无冲突时模型输出"无冲突"，解析为空数组。
"""

import re
from typing import Any, Callable, Dict, List, Union

from . import core
from .normalize import as_str

__all__ = ["PROMPT_TEMPLATE", "CONFLICT_TYPES", "build_prompt", "parse_response", "run"]

PROMPT_TEMPLATE = """你是小说一致性审计员。给你两样东西：A=设定卡与前情摘要，B=新写章节正文。找出 B 与 A 的全部冲突，只报告、不修改：

每条冲突的输出格式：
[类型：人设漂移|时间线矛盾|称呼不一致|设定冲突|事实矛盾] B中位置（引原文短语）｜与A中何处冲突（引依据）｜严重度（高=必须改，低=可解释）

要求：不推测、不脑补；A 里没有的不要报"缺失"，只报实打实的矛盾；全部无冲突就明说"无冲突"。
逐条输出，每条一行，不要输出其他解释。

A 如下：
---
[[A]]
---
B 如下：
---
[[B]]
---"""

CONFLICT_TYPES = ("人设漂移", "时间线矛盾", "称呼不一致", "设定冲突", "事实矛盾")

# 行式：[类型：xxx] rest（冒号兼容全角/半角）
_LINE_RE = re.compile(r"^\s*[-*]?\s*\[类型[::：]\s*([^\]]+?)\]\s*(.+?)\s*$")
_SEVERITY_RE = re.compile(r"(高|低)")


def build_prompt(setting_context: str, new_chapter: str) -> str:
    """``setting_context`` 只放与本章相关的卡（按需检索），``new_chapter`` 放新章正文。"""
    return core.fill(PROMPT_TEMPLATE, A=setting_context, B=new_chapter)


def _parse_lines(text: str) -> List[Dict[str, str]]:
    conflicts: List[Dict[str, str]] = []
    for line in text.splitlines():
        match = _LINE_RE.match(line)
        if not match:
            continue
        ctype = match.group(1).strip()
        parts = [p.strip() for p in re.split(r"[｜|]", match.group(2))]
        severity = ""
        b_at = parts[0] if parts else ""
        a_at = parts[1] if len(parts) > 1 else ""
        if len(parts) > 2:
            severity_match = _SEVERITY_RE.search(parts[2])
            if severity_match:
                severity = severity_match.group(1)
        if not severity:
            severity = "低"
        conflicts.append({"类型": ctype, "B位置": b_at, "A依据": a_at, "严重度": severity})
    return conflicts


def normalize(data: Any) -> List[Dict[str, str]]:
    """把 JSON 形态的冲突清单收敛为 schema 形状。"""
    if not isinstance(data, list):
        return []
    conflicts: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        conflicts.append({
            "类型": as_str(item.get("类型")),
            "B位置": as_str(item.get("B位置")),
            "A依据": as_str(item.get("A依据")),
            "严重度": as_str(item.get("严重度")) or "低",
        })
    return conflicts


def parse_response(text: str) -> List[Dict[str, str]]:
    """解析模型输出为冲突清单；两种形态都认，全无冲突返回 []。"""
    stripped = core.strip_fences(text).strip()
    try:
        data = core.extract_json(stripped)
    except core.JSONExtractionError:
        pass
    else:
        conflicts = normalize(data)
        if conflicts:
            return conflicts
    return _parse_lines(stripped)


def run(setting_context: str, new_chapter: str, call: Callable[[str], str]) -> List[Dict[str, str]]:
    """build → call → parse 一步到位。"""
    if call is None:
        raise TypeError("run() 需要注入 call(prompt)->str 的 LLM 调用函数；参考实现不内置网络请求")
    return parse_response(call(build_prompt(setting_context, new_chapter)))
