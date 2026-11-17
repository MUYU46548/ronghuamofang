# -*- coding: utf-8 -*-
"""R01 预算—篇幅联动校验：**先算装不装得下，装不下就不发请求**。

为什么必须有：`prompts/stage5_check.md` 要求模型“输出修正后的全文”，而它写着
`max_tokens: 8000`；6000 字中文 ≈ 8000–9000 token —— 装不下时模型会**自己删**
（实测 03 章 5967 → 2186），`finish_reason` 还是 `stop`，既有截断防线完全看不见。
所以判定不能只在"截断即失败"，要在**开跑之前**拒绝。

系数与安全系数**必须可配**（AC-01-3）：登记在 `data/judge/caliber.json`（J13/J14），
改值留痕；未标定时按保守值并在结论里标“未标定”。
"""
from __future__ import annotations

import math
import re
from pathlib import Path

PROMPT_BY_STAGE = {4: "prompts/stage4_writing.md", 5: "prompts/stage5_check.md",
                   6: "prompts/stage6_polish.md"}


def prompt_max_tokens(rel, root="."):
    """从**任意**提示词文件的 frontmatter 读输出上限（None = 文件或字段缺失）。

    2026-11-17：段落级精修也要做同一道预算校验，而那张卡不在 `PROMPT_BY_STAGE`
    （它不属于 4/5/6 的整章阶段）。解析逻辑只留一份 —— 两份就会各自漂移，
    漂移的表现是"校验放行了一个注定被截断的请求"。
    """
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


def stage_max_tokens(stage, root="."):
    """从提示词 frontmatter 读该阶段的输出上限（None = 文件或字段缺失）。"""
    return prompt_max_tokens(PROMPT_BY_STAGE.get(int(stage)), root)


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


def check_whole_chapter_capacity(cfg, words, prompt="prompts/stage4_refine_paragraph.md",
                                 root="."):
    """任务要求模型**写回整章**时（段落精修这类），开跑前算装不装得下。

    与 4/5/6 同一套系数（J13/J14），区别只是提示词不按阶段号取 —— 段落精修的
    卡是 `stage4_refine_paragraph.md`，它同样要求"除目标段外逐字不变"地把整章写回，
    所以**装不下时模型会自己删**，`finish_reason` 还可能是 `stop`（2026-10-30 的实证）。
    返回 (ok, message, detail)；ok=False 时调用方**不许发起任何模型请求**。
    """
    words = int(words or 0)
    if not words:
        return False, "这次要写多少字未知 —— 拒绝执行（不猜）", {}
    declared = prompt_max_tokens(prompt, root)
    tl = ((cfg or {}).get("budget") or {}).get("token_limit") or {}
    cap = 0
    if isinstance(tl, dict) and tl.get("enabled"):
        try:
            cap = int(tl.get("per_request_max_tokens") or 0)
        except (TypeError, ValueError):
            cap = 0
    cands = [x for x in (declared, cap) if x]
    limit = min(cands) if cands else 0
    coeff, safety, calibrated = _factors(root)
    need = needed_tokens(words, coeff, safety)
    detail = {"prompt": prompt, "words": words, "declared": declared, "cap": cap,
              "max_tokens": limit, "needed": need, "coeff": coeff, "safety": safety,
              "calibrated": calibrated}
    if not limit:
        # 卡片文件整个缺失（临时工作区/自造项目根）→ 不校验，但要留下"未校验"的痕迹；
        # 卡片在、却没写 max_tokens → 那是配置坏了，拒绝（与 4/5/6 同口径）。
        if not (Path(root) / prompt).exists():
            detail["skipped"] = "读不到 %s（文件不存在）→ 本次没做预算校验" % prompt
            return True, "", detail
        return False, ("%s 没写 max_tokens（配置不全）—— 拒绝执行" % prompt), detail
    if limit < need:
        msg = ("预算装不下：这次要模型写回整章 %d 字（≈%d tokens），本次请求上限 %d "
               "—— 已拒绝，没有发模型调用。解决：把 %s 的 max_tokens 调大。"
               % (words, need, limit, prompt))
        if not calibrated:
            msg += "（系数未实测标定，结论可能偏严）"
        return False, msg, detail
    return True, "", detail


def effective_max_tokens(cfg, stage, root="."):
    """本阶段**实际**的输出上限 = min(提示词声明, config 的单请求上限)。

    为什么必须取小（2026-10-30）：direct 引擎真正设进 `payload.max_tokens` 的是
    config 的 `budget.token_limit.per_request_max_tokens`，提示词 frontmatter 只
    参与容量校验。两个数字各写一份时，提示词写 40000、config 写 20000 → 校验放行
    一个注定被截断的请求（半截正文不落盘、退出码 2，白烧一遍输入）。
    `enabled: false` 时不设上限（老行为），返回提示词声明值。
    """
    declared = stage_max_tokens(stage, root)
    tl = ((cfg or {}).get("budget") or {}).get("token_limit") or {}
    if not isinstance(tl, dict) or not tl.get("enabled"):
        return declared
    try:
        cap = int(tl.get("per_request_max_tokens") or 0)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return declared
    return min(declared, cap) if declared else cap


def guard_capacity(proj, stage, root=".", words=None):
    """阶段入口用的一行版本：装不下就返回 (False, 人话)。"""
    ok, msg, _detail = check_capacity(proj, stage, root, words=words)
    return ok, msg


def words_capacity(max_tokens, root=".", coeff=None, safety=None):
    """`needed_tokens` 的反函数：**max_tokens 装得下多少字**。

    为什么需要（2026-10-30）：批/卷的尺寸本该由“装得下多少”决定，而旧实现是
    写死 `batch_size = 5`（stage5）与“整卷一次发”（stage6），容量校验再拿
    **单章**字数去比 —— 两个数字各说各话，批一多就低估 5 倍、卷一大就低估一个
    数量级。有了这个反函数，“批多大”与“装得下”就只有一处判据。
    """
    c, s, _ = _factors(root)
    if coeff is not None:
        c = float(coeff)
    if safety is not None:
        s = float(safety)
    try:
        limit = int(max_tokens or 0)
    except (TypeError, ValueError):
        return 0
    if limit <= 0 or c <= 0 or s <= 0:
        return 0
    return int(limit / (c * s))


def items_per_request(max_tokens, words_per_item, root=".", maximum=None):
    """一次请求最多装几个“字数为 words_per_item”的条目（章）。

    返回值**恒 ≥ 1**：连一页都装不下时也返回 1，让 `check_capacity` 去拒绝并
    说清“差多少”—— 在这里返回 0 会把调用方变成“一个都不发”且**没有任何
    报错**（静默空转，正是本项目最忌讳的失败形态）。
    """
    cap = words_capacity(max_tokens, root)
    try:
        per = int(words_per_item or 0)
    except (TypeError, ValueError):
        per = 0
    k = 1 if per <= 0 or cap <= 0 else max(1, cap // per)
    if maximum:
        k = min(k, max(1, int(maximum)))
    return k
