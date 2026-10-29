"""模块③ 角色状态卡抽取（读②）。

出门版提示卡见 ``prompts/03_角色状态卡抽取.md``；结构契约见
``schemas/character_card.schema.json``。

用法::

    from chishu import character_cards

    cards = character_cards.run(list_of_summaries, call=my_llm)  # List[dict]
"""

import json
from typing import Any, Callable, Dict, List, Union

from . import core
from .normalize import as_str, as_str_list, as_rows

__all__ = ["PROMPT_TEMPLATE", "build_prompt", "parse_response", "run"]

PROMPT_TEMPLATE = """以下是同一部小说若干章节的结构化摘要（JSON）。请聚合出每个角色的状态卡：

{
  "名字": "",
  "身份": "",
  "性格特质": [],
  "当前关系": [{"对象": "", "关系": "", "依据章节": ""}],
  "当前状态": "",
  "出场章节": [],
  "未决线": "",
  "矛盾清单": []
}

要求：以最新出现的章节状态为准；发现前后矛盾的（早期说 A 后期说 B）单独列在"矛盾清单"里，不要自行取舍。
只输出 JSON 数组本身，不要输出任何解释文字或代码块标记。

材料如下：
---
[[材料]]
---"""

_RELATION_KEYS = ["对象", "关系", "依据章节"]
_RELATION_DEFAULTS = {"对象": "", "关系": "", "依据章节": ""}
_CARD_KEYS = ["名字", "身份", "性格特质", "当前关系", "当前状态", "出场章节", "未决线", "矛盾清单"]
_CARD_DEFAULTS = {
    "名字": "",
    "身份": "",
    "性格特质": [],
    "当前关系": [],
    "当前状态": "",
    "出场章节": [],
    "未决线": "",
    "矛盾清单": [],
}


def build_prompt(summaries: Union[str, List[Any], Dict[str, Any]]) -> str:
    """``summaries`` 可直接给粘贴文本，或给 ② 产出的 dict/数组（自动序列化）。"""
    if isinstance(summaries, str):
        material = summaries
    else:
        material = json.dumps(summaries, ensure_ascii=False, indent=2)
    return core.fill(PROMPT_TEMPLATE, 材料=material)


def normalize(data: Any) -> List[Dict[str, Any]]:
    """把模型产出收敛为角色卡数组（容忍 ``{"角色": [...]}`` 包裹形态）。"""
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        if lists:
            data = max(lists, key=len)
        else:
            data = [data]
    if not isinstance(data, list):
        raise core.JSONExtractionError("模块③ 期望一个 JSON 数组，实际不是")
    cards: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        card = dict(_CARD_DEFAULTS)
        for key in _CARD_KEYS:
            if key not in item:
                continue
            if key in ("性格特质", "出场章节", "矛盾清单"):
                card[key] = as_str_list(item[key])
            elif key == "当前关系":
                card[key] = as_rows(item[key], _RELATION_KEYS, _RELATION_DEFAULTS)
            else:
                card[key] = as_str(item[key])
        cards.append(card)
    return cards


def parse_response(text: str) -> List[Dict[str, Any]]:
    """解析模型输出为角色卡数组。"""
    return normalize(core.extract_json(core.strip_fences(text)))


def run(summaries: Union[str, List[Any], Dict[str, Any]], call: Callable[[str], str]) -> List[Dict[str, Any]]:
    """build → call → parse 一步到位。"""
    if call is None:
        raise TypeError("run() 需要注入 call(prompt)->str 的 LLM 调用函数；参考实现不内置网络请求")
    return parse_response(call(build_prompt(summaries)))
