"""模块⑨ 世界观设定卡抽取器（读②，同③架构）——正典库"设定表"。

出门版提示卡见 ``prompts/09_世界观设定卡.md``；结构契约见
``schemas/worldview.schema.json``。

用法::

    from chishu import worldview

    cards = worldview.run(list_of_summaries, call=my_llm)   # List[dict]
"""

import json
from typing import Any, Callable, Dict, List, Union

from . import core
from .normalize import as_str, as_str_list

__all__ = ["PROMPT_TEMPLATE", "CATEGORIES", "build_prompt", "parse_response",
           "merge_cards", "run"]

PROMPT_TEMPLATE = """以下是同一部小说若干章节的结构化摘要（JSON）。请聚合出世界观设定卡，输出 JSON 数组，每张卡格式：

{
  "设定名": "",
  "类别": "地理|势力|种族|物品|规则|其他",
  "内容": "",
  "确立章节": [],
  "补充章节": [],
  "矛盾点": []
}

要求：
- 同名设定跨章节合并为一张卡；首次出现的章节填"确立章节"，后续有增补的章节填"补充章节"；
- "内容"以最新章节的表述为准，演变过程一句话带过；
- 发现前后矛盾的（早期说 A 后期说 B）单独列在"矛盾点"里，不要自行取舍；
- 只记录摘要中明确出现的内容，不推测。
只输出 JSON 数组本身，不要输出任何解释文字或代码块标记。

材料如下：
---
[[材料]]
---"""

CATEGORIES = ("地理", "势力", "种族", "物品", "规则", "其他")
_CARD_KEYS = ["设定名", "类别", "内容", "确立章节", "补充章节", "矛盾点"]
_CARD_DEFAULTS = {"设定名": "", "类别": "其他", "内容": "",
                  "确立章节": [], "补充章节": [], "矛盾点": []}


def build_prompt(summaries: Union[str, List[Any], Dict[str, Any]]) -> str:
    """``summaries`` 可直接给粘贴文本，或给 ② 产出的 dict/数组（自动序列化）。"""
    if isinstance(summaries, str):
        material = summaries
    else:
        material = json.dumps(summaries, ensure_ascii=False, indent=2)
    return core.fill(PROMPT_TEMPLATE, 材料=material)


def normalize(data: Any) -> List[Dict[str, Any]]:
    """把模型产出收敛为设定卡数组（容忍 ``{"设定卡": [...]}`` 包裹形态）。"""
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = max(lists, key=len) if lists else []
    if not isinstance(data, list):
        raise core.JSONExtractionError("模块⑨ 期望一个 JSON 数组，实际不是")
    cards: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        card = dict(_CARD_DEFAULTS)
        for key in _CARD_KEYS:
            if key not in item:
                continue
            if key in ("确立章节", "补充章节", "矛盾点"):
                card[key] = as_str_list(item[key])
            else:
                card[key] = as_str(item[key])
        if card["类别"] not in CATEGORIES:
            card["类别"] = "其他"
        cards.append(card)
    return cards


def merge_cards(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把新一批设定卡按"设定名"并入卡库：非空字段覆盖，章节与矛盾点取并集。"""
    by_name = {}
    ordered: List[Dict[str, Any]] = []
    for card in existing:
        key = card.get("设定名", "").strip()
        if key and key not in by_name:
            by_name[key] = card
            ordered.append(card)
    for card in incoming:
        key = card.get("设定名", "").strip()
        if not key:
            continue
        current = by_name.get(key)
        if current is None:
            clone = dict(card)
            by_name[key] = clone
            ordered.append(clone)
            continue
        for field in ("类别", "内容"):
            if card.get(field):
                current[field] = card[field]
        for field in ("确立章节", "补充章节", "矛盾点"):
            for value in card.get(field, []):
                if value and value not in current[field]:
                    current[field].append(value)
    return ordered


def parse_response(text: str) -> List[Dict[str, Any]]:
    """解析模型输出为设定卡数组。"""
    return normalize(core.extract_json(core.strip_fences(text)))


def run(summaries: Union[str, List[Any], Dict[str, Any]], call: Callable[[str], str]) -> List[Dict[str, Any]]:
    """build → call → parse 一步到位。"""
    if call is None:
        raise TypeError("run() 需要注入 call(prompt)->str 的 LLM 调用函数；参考实现不内置网络请求")
    return parse_response(call(build_prompt(summaries)))
