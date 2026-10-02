# -*- coding: utf-8 -*-
"""校验器 —— 搬运自明鉴（MingJian）scripts/utils/validator.py。

来源：明鉴（MingJian）项目 scripts/utils/validator.py（2026-09-14 快照）

NovelForge 目前只用到 parse_llm_json（LLM 输出容错解析）。
明鉴原文件里的决策表 schema 校验（validate_decision_table / loc_exists /
validate_insert_len / build_loc_index）依赖 utils.splitter.para_hash，
本仓库暂无对应实现，**不搬运**，避免搬入坏依赖。

与上游的差异（全部是对上游语义的**超集**，任何上游能解析的输入此处结果相同）：
  1) 上游只提取首个 `{...}` 对象；LLM 有时按提示词返回**数组**（如 `[{...}]`）。
     此处 object 优先、array 兜底，二者都失败才返回 None。
  2) 2026-09-29（件2）新增三道容错：
     a. **剥离 `<think>` / `<thinking>` 内嵌块** —— R1 系网关会把思考直接内嵌在
        content 里（全库原先 grep 零剥离），不剥离会把思考残片当正文/数据落进产物。
     b. **断裂 JSON 兜底** —— 输出被 max_tokens 截断、或模型中途收尾时，用括号配对
        扫描补全未闭合的 `}`/`]`（并去掉尾部悬空的 `,`/`:`），把「只差收尾括号」的
        报告救回来（审稿 JSON 断裂是 09-28 冒烟的实测事故）。
     c. **失败不抛裸异常，并回报原文偏移** —— 解析彻底失败时把原因与偏移写进
        `LAST_ERROR` 并打印一行 WARN，调用方据此人工接管（恶意样本也不得抛栈）。
"""
import json
import re

_FENCE_OPEN = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```$")
_FENCE_ANY = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.IGNORECASE)

# <think> / <thinking> 内嵌思考块（成对）；以及只有开标签的截断形态（其后全是思考）
_THINK_BLOCK = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.S | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think(?:ing)?>.*\Z", re.S | re.IGNORECASE)

# 最近一次解析失败诊断（含原文偏移）。仅供调用方/自检读取，不改变返回值语义。
LAST_ERROR = None


def _strip_fence(text):
    """剥掉代码围栏：先剥首尾，再从任意位置取围栏内容（LLM 常在围栏前说话）。"""
    t = _FENCE_OPEN.sub("", text)
    t = _FENCE_CLOSE.sub("", t)
    if t.lstrip().startswith(("{", "[")):
        return t
    m = _FENCE_ANY.search(text)
    return m.group(1) if m else t


def strip_think(text):
    """剥离 `<think>` / `<thinking>` 内嵌思考块。

    只留开标签（被 max_tokens 截断）时，丢弃开标签之后的全部内容 —— 那一段是思考，
    绝不能让它的残片冒充正文或 JSON。
    """
    if not text:
        return text
    t = _THINK_BLOCK.sub("", text)
    t = _THINK_OPEN.sub("", t)
    return t


def _balanced_blocks(text):
    """扫描文本，按出现顺序返回「括号配对的候选 JSON 块」。

    未闭合的候选（被截断）取到文本末尾返回，交给 `_repair_truncated` 兜底。
    扫描时区分字符串内外，字符串里的 `{}` 不计入配对。
    """
    blocks, n, i = [], len(text), 0
    while i < n:
        if text[i] in "{[":
            depth, in_str, esc, j = 0, False, False, i
            while j < n:
                ch = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                elif ch == '"':
                    in_str = True
                elif ch in "{[(":
                    depth += 1
                elif ch in "}])":
                    depth -= 1
                    if depth == 0:
                        blocks.append(text[i:j + 1])
                        i = j
                        break
                j += 1
            else:                       # 走到末尾仍未闭合 → 截断候选
                blocks.append(text[i:])
                break
        i += 1
    return blocks


def _repair_truncated(s):
    """断裂 JSON 兜底：补全未闭合的括号，并去掉尾部悬空的 `,` / `:`。

    仅在括号配对扫描发现未闭合时才动手；括号已配对则**原样返回**（不动正常文本）。
    """
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[(":
            stack.append(ch)
        elif ch in "}])":
            if stack:
                stack.pop()
    if not stack and not in_str:
        return s
    fixed = s
    if in_str:                                   # 字符串被截断 → 先补引号
        fixed += '"'
    fixed = re.sub(r"[,:]\s*$", "", fixed)       # 去掉悬空的逗号/冒号
    closer = {"{": "}", "[": "]", "(": ")"}
    for ch in reversed(stack):
        fixed += closer[ch]
    return fixed


def _first_json_offset(text):
    """首个 `{` / `[` 的位置；没有则 -1（用于失败时回报原文偏移）。"""
    for i, ch in enumerate(text):
        if ch in "{[":
            return i
    return -1


def extract_json(raw):
    """从任意文本里提取首个**可解析**的 JSON（对象或数组）。

    返回 `(value, offset)`：value 为解析结果或 None；offset 为原文中该 JSON 的起点偏移
    （取值失败时为 -1）。解析成功但靠断裂兜底时，会打印一行 WARN（**不静默**）。
    """
    if not raw:
        return None, -1
    text = strip_think(_strip_fence(str(raw).strip()))
    if not text:
        return None, -1
    try:
        return json.loads(text), 0
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    for cand in _balanced_blocks(text):
        try:
            return json.loads(cand), text.find(cand)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        fixed = _repair_truncated(cand)
        if fixed != cand:
            try:
                value = json.loads(fixed)
                print("[validator] WARN LLM 输出 JSON 断裂，已兜底修复（补全未闭合括号）")
                return value, text.find(cand)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    return None, _first_json_offset(text)


def parse_llm_json(raw):
    """容错解析 LLM 输出的 JSON。

    流程：去代码围栏 → 剥 `<think>` → 直接 json.loads → 逐候选块解析（object 优先、
    array 兜底）→ 断裂兜底修复再试。

    失败返回 None（并在 module 级 `LAST_ERROR` 记下原因与原文偏移，同时打印一行 WARN），
    调用方据此重试或标记人工接管。**任何输入都不抛裸异常。**
    """
    global LAST_ERROR
    LAST_ERROR = None
    if not raw:
        return None
    if isinstance(raw, (dict, list)):            # 调用方已给结构化对象 → 原样返回
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8", "replace")
        except Exception:                        # noqa: BLE001
            LAST_ERROR = {"reason": "bytes 解码失败", "offset": 0}
            return None

    text = strip_think(_strip_fence(str(raw).strip()))
    if not text:
        LAST_ERROR = {"reason": "剥离思考块/围栏后为空", "offset": 0}
        return None

    value, offset = extract_json(text)
    if value is not None:
        return value

    reason = "未找到可解析的 JSON 对象或数组"
    LAST_ERROR = {"reason": reason, "offset": offset}
    print("[validator] WARN LLM 输出解析失败（偏移 " + str(offset) + "）: " + reason
          + "；调用方应重试或标记人工接管")
    return None
