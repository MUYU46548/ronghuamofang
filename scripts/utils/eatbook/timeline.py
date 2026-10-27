"""模块⑧ 时间线抽取器（吃②，同③架构）——正典库"时间线表"。

出门版提示卡见 ``prompts/08_时间线抽取器.md``；结构契约见
``schemas/timeline.schema.json``。

用法::

    from chishu import timeline

    rows = timeline.run(list_of_summaries, call=my_llm)   # List[dict]
"""

import json
from typing import Any, Callable, Dict, List, Union

from . import core
from .normalize import as_str, as_rows

__all__ = ["PROMPT_TEMPLATE", "build_prompt", "parse_response", "run"]

PROMPT_TEMPLATE = """以下是同一部小说若干章节的结构化摘要（JSON）。请聚合出全书时间线，输出 JSON 数组，每行格式：

{
  "顺序": 1,
  "时间": "",
  "事件": "",
  "依据章节": "",
  "支线": ""
}

要求：
- 按叙事先后排序，"顺序"从 1 连续编号；同一时间点的多个事件分行记录；
- "时间"保留原文表述（如"贞观十三年""三日后""次日"），不换算成绝对年代、不推测；
- "依据章节"填该事件发生的章节号或标题；
- "支线"填主线/支线名，属主线可留空串；
- 只记录摘要中明确出现的内容，不推测。
只输出 JSON 数组本身，不要输出任何解释文字或代码块标记。

材料如下：
---
[[材料]]
---"""

_ROW_KEYS = ["顺序", "时间", "事件", "依据章节", "支线"]
_ROW_DEFAULTS = {"顺序": 0, "时间": "", "事件": "", "依据章节": "", "支线": ""}


def build_prompt(summaries: Union[str, List[Any], Dict[str, Any]]) -> str:
    """``summaries`` 可直接给粘贴文本，或给 ② 产出的 dict/数组（自动序列化）。"""
    if isinstance(summaries, str):
        material = summaries
    else:
        material = json.dumps(summaries, ensure_ascii=False, indent=2)
    return core.fill(PROMPT_TEMPLATE, 材料=material)


def normalize(data: Any) -> List[Dict[str, Any]]:
    """把模型产出收敛为时间线行数组（容忍 ``{"时间线": [...]}`` 包裹形态）。"""
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = max(lists, key=len) if lists else []
    if not isinstance(data, list):
        raise core.JSONExtractionError("模块⑧ 期望一个 JSON 数组，实际不是")
    rows: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        row = dict(_ROW_DEFAULTS)
        for key in _ROW_KEYS:
            if key in item:
                row[key] = as_str(item[key])
        try:
            row["顺序"] = int(row["顺序"])
        except (TypeError, ValueError):
            row["顺序"] = 0
        rows.append(row)
    return rows


def parse_response(text: str) -> List[Dict[str, Any]]:
    """解析模型输出为时间线行数组。"""
    return normalize(core.extract_json(core.strip_fences(text)))


def run(summaries: Union[str, List[Any], Dict[str, Any]], call: Callable[[str], str]) -> List[Dict[str, Any]]:
    """build → call → parse 一步到位。"""
    if call is None:
        raise TypeError("run() 需要注入 call(prompt)->str 的 LLM 调用函数；参考实现不内置网络请求")
    return parse_response(call(build_prompt(summaries)))
