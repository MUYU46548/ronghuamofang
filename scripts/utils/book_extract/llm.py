# -*- coding: utf-8 -*-
"""LLM 适配器：把绒花墨坊的客户端包成成书提炼管线要的 ``call(prompt) -> str``。

## 为什么需要这一层

沙盒原版（SP-1024）刻意**不含网络代码**，LLM 调用由集成方注入。本仓的客户端有
两条路径：`run_task(任务文件)` 与更底层的 `_post_chat(messages)`。前者按
「===FILE:」协议落盘、只回 `stdout_tail`（截断到 2000 字）—— 成书提炼要的是**整段
JSON 正文**，所以必须走 `_post_chat`。本仓已有三处同样用法
（`branch_samples` / `field_refine` / `open_variants`），不是新开的口子。

## 三条纪律

- **截断即失败**：`finish_reason == "length"` 时正文不完整，JSON 必然解析失败；
  这里提前抛 `LLMCallError`，让上层按「本章未完成」处理（可重跑），
  绝不把半截 JSON 当产出落盘 —— 与全仓的截断纪律同源。
- **成本必须记**：`stats` 累加 tokens / 估算金额，落盘到 `cost.json`。
  沙盒版零额度；本仓是真花钱，不记账等于让用户闭眼烧。
- **不在本模块决定模型**：模型来自 `config/system.yaml`，由 `make_client()` 取。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

SYSTEM_PROMPT = (
    "你是长篇小说项目里的文本分析员。用户会给你一份严格的 JSON 契约。"
    "只输出契约要求的 JSON 本身，不要解释、不要代码块标记、不要复述原文。"
)


class LLMCallError(RuntimeError):
    """成书提炼过程中的模型调用失败（截断 / 客户端不支持 / 空输出）。"""


def new_stats() -> Dict[str, Any]:
    """成本累计器（tokens 与调用次数）。"""
    return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_yuan": 0.0,
            "model": "", "truncated": 0}


def make_call(client: Any, *, max_tokens: Optional[int] = None,
              temperature: Optional[float] = None,
              stats: Optional[Dict[str, Any]] = None,
              system: str = SYSTEM_PROMPT) -> Callable[[str], str]:
    """把客户端包成 ``call(prompt) -> text``。

    `client` 必须提供 `_post_chat(messages, temperature=, max_tokens=)`
    （直连引擎的 `OpenAICompatClient` 有；Hermes 子会话没有 —— 那种引擎下
    请改用 `book_extract.py --dry-run` 出提示词，或先切直连引擎）。
    """
    if not hasattr(client, "_post_chat"):
        raise LLMCallError(
            "当前引擎不支持这种「一次把整段书稿交给模型」的用法。"
            "请到「设置 → 引擎与模型」里改用直连模型后再试；"
            "命令行也可以先用 --dry-run 预演（不花 token）。")

    def call(prompt: str) -> str:
        text, usage, model, finish = client._post_chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": prompt}],
            temperature=temperature, max_tokens=max_tokens)
        tin = int((usage or {}).get("prompt_tokens") or 0)
        tout = int((usage or {}).get("completion_tokens") or 0)
        if stats is not None:
            stats["calls"] += 1
            stats["tokens_in"] += tin
            stats["tokens_out"] += tout
            stats["model"] = model or stats.get("model") or ""
            try:
                from utils import cost_tracker
                stats["cost_yuan"] = round(
                    stats["cost_yuan"] + cost_tracker.estimate_cost_yuan(tin, tout, model),
                    6)
            except Exception:                                  # noqa: BLE001
                pass
        if finish == "length":
            if stats is not None:
                stats["truncated"] += 1
            raise LLMCallError(
                "输出被 max_tokens 截断（finish_reason=length）—— 半截 JSON 不可用，"
                "提高该请求的 max_tokens 或换模型后重跑本章")
        if not (text or "").strip():
            raise LLMCallError("模型返回空文本（思考模式把正文吃进 reasoning？）")
        return text

    return call
