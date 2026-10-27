# -*- coding: utf-8 -*-
"""吃书管线（移植自 SP-1024 `chishu/`，2026-10-27 集成进绒花墨坊）。

## 这是什么

把**一本已经写完的书**（旧稿 / 参考作品 / 自己的前作）吃成一套结构化正典：
章节摘要 ② → 角色卡 ③ / 伏笔台账 ⑥ / 时间线 ⑧ / 世界观设定 ⑨ → 一致性检查 ①，
外加纯本地的病灶词扫描 ⑦。产物是 `data/eatbook/<书名>/` 下的 JSON，
`eat_book.py merge` 可按「只增不改」并进 `data/setting/setting.json`。

## 与沙盒原版的差异（为什么不照搬）

原地在 `SP-1024` 是**零网络**的参考实现：LLM 调用由调用方注入 `call(prompt) -> str`，
配套 `campaign` 用「出提示词 → 人工贴到外部平台 → 回收文件」跑批。本仓有
`make_client()` 与 `_post_chat()`，所以这里：

1. 保留全部 `build_prompt / parse_response / run(call=...)` 三件套（**提示词逐字未改**，
   与 `SP-1024/prompts/*.md` 仍一一对应）；
2. 去掉文件搬运（emit/ingest），改由 `utils.eatbook.runner` 在进程内分批调用；
3. **保留断点语义**：每章摘要落盘后即算完成，重跑自动跳过（`--force` 才重做）。

## 红线

`chishu` 原版不含任何网络代码，这里同样不含 —— 真正的 HTTP 只在
`utils/eatbook/llm.py` 的适配器里，且只在调用方显式构造客户端时发生。
"""

__version__ = "1.0.0+nf"

from . import core, wordlist
from .core import JSONExtractionError, extract_json, fill, strip_fences
from .normalize import as_str, as_rows, as_str_list
from .wordlist import Term, load_wordlist
from . import (summarizer, character_cards, consistency, foreshadow,
               cliche_scanner, timeline, worldview)

__all__ = [
    "__version__",
    "core", "wordlist",
    "JSONExtractionError", "extract_json", "fill", "strip_fences",
    "as_str", "as_rows", "as_str_list",
    "Term", "load_wordlist",
    "summarizer", "character_cards", "consistency", "foreshadow",
    "cliche_scanner", "timeline", "worldview",
]
