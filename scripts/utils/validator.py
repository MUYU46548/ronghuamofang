# -*- coding: utf-8 -*-
"""校验器 —— 搬运自明鉴（MingJian）scripts/utils/validator.py。

来源：E:/CODE/CangKu/MingJian/scripts/utils/validator.py（2026-09-14 快照）

NovelForge 目前只用到 parse_llm_json（LLM 输出容错解析）。
明鉴原文件里的决策表 schema 校验（validate_decision_table / loc_exists /
validate_insert_len / build_loc_index）依赖 utils.splitter.para_hash，
本仓库暂无对应实现，**不搬运**，避免搬入坏依赖。

与上游的差异（唯一一处，向后兼容的超集）：
  上游只提取首个 `{...}` 对象；LLM 有时按提示词返回**数组**（如 `[{...}]`）。
  此处 object 优先、array 兜底，二者都失败才返回 None。
  对上游语义是严格的超集：任何上游能解析的输入此处结果相同。
"""
import json
import re

_FENCE_OPEN = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```$")
_FENCE_ANY = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.IGNORECASE)


def _strip_fence(text):
    """剥掉代码围栏：先剥首尾，再从任意位置取围栏内容（LLM 常在围栏前说话）。"""
    t = _FENCE_OPEN.sub("", text)
    t = _FENCE_CLOSE.sub("", t)
    if t.lstrip().startswith(("{", "[")):
        return t
    m = _FENCE_ANY.search(text)
    return m.group(1) if m else t


def parse_llm_json(raw):
    """容错解析 LLM 输出的 JSON：去代码围栏 → 找首个 JSON 块 → json.loads。

    失败返回 None（调用方重试或标记人工接管）。
    """
    if not raw:
        return None
    if isinstance(raw, (dict, list)):      # 调用方已给结构化对象 → 原样返回
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            return None

    text = _strip_fence(str(raw).strip())
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # object 优先，array 兜底（对上游语义的超集，见模块 docstring）
    for opener, closer in (("{", "}"), ("[", "]")):
        s, e = text.find(opener), text.rfind(closer)
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return None
