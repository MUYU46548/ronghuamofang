"""失败契约：把"为什么失败"从底层一路带到 `progress.error`。

## 为什么有这份文件（2026-11-03）

`llm_client._finish` 已经把子会话的 `stderr_tail`（上游 HTTP 报文尾部）抓下来了，
但各 stage 的失败分支统一只写一句"子会话退出码非零" —— 于是 `progress.error`
和界面上**一个字都没有**可诊断的信息。上次排障 20 分钟，全耗在猜"到底报了什么"。

这里只有**纯函数**（零 IO、零 token、不 import 项目内其它模块），做两件事：

1. `describe(result)` —— 把一次子会话结果压成一句能贴进 `progress.error` 的原因，
   尽量带上上游 HTTP 码与报文里的那句话；
2. `retryable(text)` —— 这句话值不值得**原样再赌一次**。判据是错误类型：
   400/401/403/404、模型不可用、请求体被拒 —— 同一输入重跑只会同样失败，
   白烧一倍输入（实测上次有个 run 把 2/3 的时间烧在无意义的重试上）。

判据只有这一份，`orchestrator.should_auto_retry` 与各 stage 共用。
"""

from __future__ import annotations

import re

# 一句话的上限：progress.error 会进 GUI 提示条，太长会被截断成没用的噪声。
MAX_REASON = 220

_CODE_RE = re.compile(r"(?:Error code:\s*|HTTP\s+|status(?:_code)?\s*[:=]\s*)(\d{3})")

# 上游 OpenAI 兼容网关的报文形如  {'code': 'X', 'message': '真正的原因'}
# 光看整行会把 `{'code': ..., ` 这类前缀挤进前 160 字，真正的原因被截掉一半。
_MSG_RE = re.compile(r"""['"]message['"]\s*:\s*(['"])((?:\\.|(?!\1).)*)\1""", re.S)

# 报文里"这句话是重点"的特征（小写比较）。按命中优先级从高到低。
_HINTS = (
    "error code", "upstream_error", "model_not_available", "model not",
    "duplicate field", "deserialize", "invalid_request", "invalid api key",
    "unauthorized", "permission", "not found", "no such model",
    "rate limit", "timeout", "timed out", "connection", "connecterror",
    "模型不可用", "超时", "连接", "拒绝",
)

# 非可重试：同一输入再跑一次结果完全相同。
_NON_RETRYABLE = {
    "request": ("请求被上游拒绝（400/422 一类）→ 不重试：同一输入重跑只会同样失败。"
                "处理：按 progress.error 里的报文改提示词 / 换模型 / 修配置"),
    "auth": ("鉴权失败（401/403）→ 不重试：重试不会让 key 生效。"
             "处理：检查 config/system.yaml 的 providers 与该 provider 的 API key"),
    "not_found": ("资源/模型不存在（404 或 MODEL_NOT_AVAILABLE）→ 不重试。"
                  "处理：按 progress.error 里的模型名核对 providers 与模型白名单"),
}


def _clean(line: str) -> str:
    """压掉多余的空白与 JSON 引号，只留人能读的一句话。"""
    s = " ".join((line or "").split())
    return s.strip().strip("'\"")


def _pick_line(text: str) -> str:
    """从长报文里挑最该看的那一行：优先带错误码/关键词，其次是最长的一行。"""
    lines = [_clean(x) for x in (text or "").splitlines() if _clean(x)]
    if not lines:
        return ""
    for hint in _HINTS:
        for line in reversed(lines):
            if hint in line.lower():
                return line
    # 没有关键词：取最长的一行（通常是唯一的报文），避免挑到 "Traceback" 这类噪声
    return max(lines, key=len)


def status_code(text: str) -> int | None:
    """从报文里取上游 HTTP 码；取不到返回 None。"""
    m = _CODE_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def classify(text: str) -> str:
    """错误类型：truncated / transport / auth / not_found / request / unknown。"""
    low = (text or "").lower()
    if "finish_reason=length" in low or "截断" in low:
        return "truncated"
    code = status_code(text)
    if code in (401, 403):
        return "auth"
    if code == 404 or "model_not_available" in low or "模型不可用" in low \
            or "no such model" in low:
        return "not_found"
    if code in (400, 422) or "duplicate field" in low or "deserialize" in low \
            or "invalid_request" in low:
        return "request"
    if code in (408, 429, 500, 502, 503, 504) \
            or "transport_error" in low or "timeout" in low or "timed out" in low \
            or "connecterror" in low or "connection" in low or "连接" in low \
            or "超时" in low:
        return "transport"
    return "unknown"


def retryable(text: str) -> tuple[bool, str]:
    """这句话值不值得原样重试。返回 (bool, 不重试的理由)。"""
    kind = classify(text)
    if kind in _NON_RETRYABLE:
        return False, _NON_RETRYABLE[kind]
    if kind == "truncated":
        return False, ("上一次失败是**输出被 max_tokens 截断**（finish_reason=length）"
                       "→ 不重试：同一上限只会再截断一次，白烧一倍输入。处理：调大 "
                       "config/system.yaml 的 budget.token_limit.per_request_max_tokens，"
                       "或减小单次任务量 / 改提示词")
    # transport / unknown：偶发面居多，按原有策略重试（unknown 保持旧行为，不擅自收紧）
    return True, ""


def upstream_message(text: str) -> str:
    """从上游报文里取出 `message` 字段的值（没有就空串）。"""
    m = _MSG_RE.search(text or "")
    return _clean(m.group(2)) if m else ""


def describe(result: dict, limit: int = MAX_REASON) -> str:
    """把一次子会话结果（llm_client.run_task 的返回）压成一句失败原因。

    `stderr_tail` 优先（上游 HTTP 报文在里面），其次 `stdout_tail`（模型/CLI 的
    最后一句话），再次 `error` 字段。一句话都没有时退回退出码 —— 但**不再**只写
    "子会话退出码非零"：那样等于把诊断信息丢掉。

    报文里有 `message` 字段时**只取那个值**：真正的原因在那儿，整行会把
    `{'code': 'UPSTREAM_ERROR', ` 这类前缀挤到前面，把原因截掉一半。
    """
    result = result or {}
    if result.get("transport_error"):
        base = "网络/传输异常（内部重试已耗尽）"
    else:
        base = ""
    for key in ("stderr_tail", "stdout_tail", "error"):
        raw = str(result.get(key) or "")
        line = _pick_line(raw)
        if not line:
            continue
        code = status_code(line) or status_code(raw)
        tag = f"（HTTP {code}）" if code else ""
        why = upstream_message(raw) or line
        out = f"{base or '子会话失败'}{tag}：{why}"
        return out[:limit]
    if base:
        return base
    rc = result.get("exit_code")
    return f"子会话退出码非零（exit={rc}），但上游没有留下任何报文" if rc \
        else "子会话失败（未取到原因）"


def stage_error_text(progress, stage: int) -> str:
    """把某一阶段的失败原因汇成一段文本，供 `retryable()` 判定。

    两处都有价值：阶段级 `error`（校验/预算一类）与逐章 `failed_chapters[].error`
    （子会话失败一类）。断点续跑时阶段级 error 可能被清掉，逐章的还在。
    """
    parts: list[str] = []
    try:
        st = (getattr(progress, "data", {}) or {}).get("stages", {}).get(str(stage)) or {}
        if st.get("error"):
            parts.append(str(st["error"]))
        for item in (st.get("failed_chapters") or [])[:3]:
            if isinstance(item, dict) and item.get("error"):
                parts.append(str(item["error"]))
    except Exception:                                          # noqa: BLE001
        pass
    return "\n".join(parts)


def first_failure(progress, stage: int) -> str:
    """逐章失败里第一条原因（给"阶段 failed"补一句人话），没有就空串。"""
    try:
        st = (getattr(progress, "data", {}) or {}).get("stages", {}).get(str(stage)) or {}
        for item in st.get("failed_chapters") or []:
            if isinstance(item, dict) and item.get("error"):
                return str(item["error"])[:MAX_REASON]
    except Exception:                                          # noqa: BLE001
        pass
    return ""
