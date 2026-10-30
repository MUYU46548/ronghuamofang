# -*- coding: utf-8 -*-
"""子会话输出的 **usage 解析 / token 估算**工具（不再执行任何子会话）。

## 历史（2026-10-30 收敛）

本模块原有一个独立的 `HermesClient` 实现（自己拼 `hermes chat -q`、自己透传 `-m`）。
它和 `utils.llm_client.HermesClient` 是**两份会各自漂移的同一件事**：
两者命令行参数不同（一个带 `--format stream-json` + `-t file` + `--max-turns`，
一个都不带）、停止语义不同、usage 解析也不同。全仓无一处 import 它，纯死代码 ——
谁照着旧文档用上，就会得到一套没有超时结构化失败、没有工具白名单、
没有 stream-json 的「另一个引擎」。

现在执行子会话**只有一个实现**：`utils.llm_client.HermesClient`。
本模块只保留两件纯函数（无进程、无 IO 副作用）：
- `parse_usage(stdout)`：从自由文本里正则捞 token（历史兜底口径）；
- `estimate_tokens(task_path)`：解析不到 usage 时按任务文件大小保守估算。

⚠️ 不要在这里重新加执行逻辑 —— 引擎选择只有一个入口：`make_client()`。
"""
import re
from pathlib import Path

TOKEN_IN_RE = re.compile(
    r"(?:tokens_in|prompt_tokens|input_tokens|input)[\s:=]+([\d,]+)", re.IGNORECASE)
TOKEN_OUT_RE = re.compile(
    r"(?:tokens_out|completion_tokens|output_tokens|output)[\s:=]+([\d,]+)", re.IGNORECASE)
TOKEN_TOTAL_RE = re.compile(
    r"(?:total_tokens|tokens)[\s:=]+([\d,]+)", re.IGNORECASE)
COST_RE = re.compile(r"cost[\s:=]+([\d.]+)", re.IGNORECASE)


def _to_int(s):
    try:
        return int(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0


def parse_usage(stdout):
    """从子会话 stdout 解析 (tokens_in, tokens_out, cost_yuan, estimated)。

    解析不到 token 时按任务文件无法获知——调用方需提供估算（见 estimate_tokens）。
    返回 estimated=False 表示来自真实解析。
    """
    m_in = TOKEN_IN_RE.search(stdout)
    m_out = TOKEN_OUT_RE.search(stdout)
    m_total = TOKEN_TOTAL_RE.search(stdout)
    m_cost = COST_RE.search(stdout)

    tokens_in = _to_int(m_in.group(1)) if m_in else 0
    tokens_out = _to_int(m_out.group(1)) if m_out else 0
    if tokens_in == 0 and tokens_out == 0 and m_total:
        total = _to_int(m_total.group(1))
        tokens_in = total * 3 // 4
        tokens_out = total - tokens_in
    cost_yuan = float(m_cost.group(1)) if m_cost else 0.0
    estimated = not (m_in or m_out or m_total) or cost_yuan == 0.0
    return tokens_in, tokens_out, cost_yuan, estimated


def estimate_tokens(task_path):
    """解析失败时的保守估算：任务文件字符量 → token。

    中文约 1 token/字（UTF-8 3 字节/字）。输入按文件大小×0.4，输出按输入 1/3。
    返回 (tokens_in, tokens_out)。仅用于记账兜底，标注 estimated=True。
    """
    try:
        size = Path(task_path).stat().st_size
    except OSError:
        size = 0
    tokens_in = max(1000, int(size * 0.4))
    tokens_out = max(500, tokens_in // 3)
    return tokens_in, tokens_out
