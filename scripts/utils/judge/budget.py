# -*- coding: utf-8 -*-
"""R01 预算—篇幅联动校验：**先算装不装得下，装不下就不发请求**。

为什么必须有：`prompts/stage5_check.md` 要求模型「输出修正后的全文」，而它写着
`max_tokens: 8000`；6000 字中文 ≈ 8000–9000 token —— 装不下时模型会**自己删**
（实测 03 章 5967 → 2186），`finish_reason` 还是 `stop`，既有截断防线完全看不见。
所以判定不能只在"截断即失败"，要在**开跑之前**拒绝。

系数与安全系数**必须可配**（AC-01-3）：登记在 `data/judge/caliber.json`（J13/J14），
改值留痕；未标定时按保守值并在结论里标「未标定」。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

PROMPT_BY_STAGE = {4: "prompts/stage4_writing.md", 5: "prompts/stage5_check.md",
                   6: "prompts/stage6_polish.md"}


def stage_max_tokens(stage, root="."):
    """从提示词 frontmatter 读该阶段的输出上限（None = 文件或字段缺失）。"""
    rel = PROMPT_BY_STAGE.get(int(stage))
    if not rel:
        return None
    p = Path(root) / rel
    if not p.exists():
        return None
    try:
        head = p.read_text(encoding="utf-8").split("---", 2)
        block = head[1] if len(head) >= 3 else ""
    except Exception:                                          # noqa: BLE001
        return None
    m = re.search(r"^\s*max_tokens\s*:\s*(\d+)\s*$", block, re.M)
    return int(m.group(1)) if m else None


def target_words(proj):
    """目标字数：优先 chapter.target_words 的上限（章节口径），否则 book.target_words。"""
    chapter = (proj or {}).get("chapter") or {}
    tw = chapter.get("target_words")
    if isinstance(tw, (list, tuple)) and tw:
        try:
            return int(tw[-1])
        except (TypeError, ValueError):
            pass
    book = (proj or {}).get("book") or {}
    try:
        return int(book.get("target_words") or 0) or None
    except (TypeError, ValueError):
        return None


def _factors(root):
    try:
        from utils.judge import caliber as C
        doc = C.load(root)
        vals = C.values(doc) if doc else {}
        return float(vals.get("J13", 1.6)), float(vals.get("J14", 1.2)), \
            bool((doc or {}).get("calibrated"))
    except Exception:                                          # noqa: BLE001
        return 1.6, 1.2, False


def needed_tokens(words, coeff=1.6, safety=1.2):
    """装 N 字需要多少输出预算（向上取整）。"""
    return int(math.ceil(float(words) * float(coeff) * float(safety)))


def check_capacity(proj, stage, root=".", max_tokens=None, words=None):
    """返回 (ok, message, detail)。ok=False 时**调用方必须不发起任何模型请求**。

    `words` 优先用**本阶段实际要处理的字数**（待检查章的最大字数）—— 判据的原始
    实证就是 5967 / 2186 这类真实字数，拿"目标字数上限"会偏严到误伤合法章节。
    """
    words = int(words) if words else target_words(proj)
    if not words:
        return False, "目标字数未知 —— 拒绝执行（不猜默认 2000，也不降级为分段回抄）", {}
    coeff, safety, calibrated = _factors(root)
    need = needed_tokens(words, coeff, safety)
    limit = stage_max_tokens(stage, root) if max_tokens is None else int(max_tokens)
    detail = {"stage": int(stage), "target_words": words, "max_tokens": limit,
              "needed": need, "coeff": coeff, "safety": safety,
              "calibrated": calibrated}
    if not limit:
        return False, "读不到第 %d 阶段的 max_tokens —— 拒绝执行" % int(stage), detail
    if limit < need:
        msg = ("第 %d 阶段只够装 %d 字（max_tokens=%d × 系数 %.1f × 安全 %.1f），"
               "目标 %d 字 —— 拒绝执行，不发起模型调用。"
               % (int(stage), int(limit / (coeff * safety)), limit, coeff, safety, words))
        if not calibrated:
            msg += "（系数未实测标定，结论可能偏严）"
        return False, msg, detail
    return True, "", detail


def guard_capacity(proj, stage, root=".", words=None):
    """阶段入口用的一行版本：装不下就返回 (False, 人话)。"""
    ok, msg, _detail = check_capacity(proj, stage, root, words=words)
    return ok, msg
