"""core —— 七件套参考实现的公共件：JSON 抽取、模板填充、围栏剥离。

参考实现不内置任何网络请求：LLM 调用由集成方以 ``call(prompt) -> str`` 形式注入
（见各模块的 ``run(...)``）。
"""

import json
import re
from typing import Any

__all__ = ["JSONExtractionError", "extract_json", "fill", "strip_fences"]

_JSON_STARTS = "{["


class JSONExtractionError(ValueError):
    """模型输出中找不到可解析的 JSON。"""


def extract_json(text: str) -> Any:
    """从模型输出中抽出第一个可解析的 JSON 值（对象或数组）。

    容忍：代码块围栏、前后解释文字、字符串内的大括号。找不到或全部解析失败
    时抛 :class:`JSONExtractionError`。
    """
    if not isinstance(text, str) or not text.strip():
        raise JSONExtractionError("输入为空，无法抽取 JSON")
    s = text.strip()
    # 整段就是 JSON 的快路径
    try:
        return json.loads(s)
    except ValueError:
        pass
    n = len(s)
    i = 0
    while i < n:
        if s[i] not in _JSON_STARTS:
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        end = -1
        j = i
        while j < n:
            ch = s[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch in _JSON_STARTS:
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    end = j
                    break
            j += 1
        if end == -1:  # 括号不配对，跳过这个起点继续找
            i += 1
            continue
        try:
            return json.loads(s[i:end + 1])
        except ValueError:
            i += 1
    raise JSONExtractionError("未在模型输出中找到可解析的 JSON")


def fill(template: str, **slots: Any) -> str:
    """把模板中的 ``[[槽名]]`` 替换为对应值；留有未填充槽则报错。"""
    out = template
    for key, value in slots.items():
        out = out.replace("[[" + key + "]]", str(value))
    leftover = sorted(set(re.findall(r"\[\[([^\]]+)\]\]", out)))
    if leftover:
        raise ValueError("模板存在未填充的槽：%s" % "、".join(leftover))
    return out


def strip_fences(text: str) -> str:
    """剥掉模型输出最外层的 Markdown 代码围栏（若有）。"""
    s = text.strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)
