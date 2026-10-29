"""模块② 章节摘要器（七件套地基）。

出门版提示卡见 ``prompts/02_章节摘要器.md``；结构契约见
``schemas/chapter_summary.schema.json``。

用法（集成方注入 LLM 调用）::

    from chishu import summarizer

    summary = summarizer.run(chapter_text, call=my_llm)          # dict，见 schema
    prompt  = summarizer.build_prompt(chapter_text)              # 或只拿提示词出门跑
    summary = summarizer.parse_response(model_output)            # 或只解析产出
"""

from typing import Any, Callable, Dict

from . import core
from .normalize import as_str, as_str_list, as_rows

__all__ = ["PROMPT_TEMPLATE", "build_prompt", "parse_response", "run"]

PROMPT_TEMPLATE = """你是小说工程的摘要员。阅读下面的章节文本，输出结构化摘要 JSON，不要输出原文复述：

{
  "章节": "<章节号或标题>",
  "人物": [{"名字": "", "本章动作": "", "状态变化": ""}],
  "地点": [],
  "事件": ["按发生顺序，每条一句"],
  "时间线": "",
  "伏笔动作": [{"类型": "埋设|回收|推进", "内容": ""}],
  "专有名词": ["本章确立或使用的称呼、专名、设定"]
}

要求：只记录文本中明确出现的内容，不推测；某字段为空就留空数组。
只输出 JSON 本身，不要输出任何解释文字或代码块标记。

章节文本如下：
---
[[章节文本]]
---"""

_FORESHADOW_KEYS = ["类型", "内容"]
_FORESHADOW_DEFAULTS = {"类型": "", "内容": ""}
_PERSON_KEYS = ["名字", "本章动作", "状态变化"]
_PERSON_DEFAULTS = {"名字": "", "本章动作": "", "状态变化": ""}

FORESHADOW_TYPES = ("埋设", "回收", "推进")


def build_prompt(chapter_text: str) -> str:
    """用整章原文填出出门版提示词。"""
    return core.fill(PROMPT_TEMPLATE, 章节文本=chapter_text)


def normalize(data: Any) -> Dict[str, Any]:
    """把模型产出收敛为 schema 形状（缺字段补空，未知键丢弃）。"""
    if not isinstance(data, dict):
        raise core.JSONExtractionError("模块② 期望一个 JSON 对象，实际不是")
    people = as_rows(data.get("人物"), _PERSON_KEYS, _PERSON_DEFAULTS)
    foreshadow = []
    for row in as_rows(data.get("伏笔动作"), _FORESHADOW_KEYS, _FORESHADOW_DEFAULTS):
        if row["类型"] not in FORESHADOW_TYPES:
            row["类型"] = ""
        foreshadow.append(row)
    return {
        "章节": as_str(data.get("章节")),
        "人物": people,
        "地点": as_str_list(data.get("地点")),
        "事件": as_str_list(data.get("事件")),
        "时间线": as_str(data.get("时间线")),
        "伏笔动作": foreshadow,
        "专有名词": as_str_list(data.get("专有名词")),
    }


def parse_response(text: str) -> Dict[str, Any]:
    """解析模型输出为章节摘要 dict。"""
    return normalize(core.extract_json(core.strip_fences(text)))


def run(chapter_text: str, call: Callable[[str], str]) -> Dict[str, Any]:
    """build → call → parse 一步到位；``call(prompt) -> 模型输出``。"""
    if call is None:
        raise TypeError("run() 需要注入 call(prompt)->str 的 LLM 调用函数；参考实现不内置网络请求")
    return parse_response(call(build_prompt(chapter_text)))
