# -*- coding: utf-8 -*-
"""R15 三档结论：把规则命中映射成「能不能开始改」，且**不给分数**。

映射表（冻结 v1，改它要走口径版本，不许改代码里的隐含阈值）：
- 不可判定（A）：输入/口径缺失（无正文、无口径、无章纲对账……）
- 不可判定（B）：对象链被压缩/版本不可比（三稿缩减超阈值且是两次压缩）
- 需重写本章：有 ≥1 条章级依据（章纲密度虚高且作者认定事件 <2；或作者认定"整章只有 1 件事"）
- 需定点重写：有带段号缺陷，且**缺陷段占比 ≥ 口径阈值（20%）**
- 可开始逐字改：无章级依据、段级缺陷不超阈值、作者认定事件 ≥2、无未消解冲突
- 结论字段为 null：作者还没勾事件（F03b）—— 不猜、不替作者决定

铁律：任何"判不了"都不得显示"合格"；输出不含 total/quality/score（AC-15-5）。
"""
from __future__ import annotations

VERDICT_OK = "可开始逐字改"
VERDICT_SPOT = "需定点重写"
VERDICT_REWRITE = "需重写本章"
VERDICT_UNDECIDED = "不可判定"

SYMBOL = {VERDICT_OK: "✓", VERDICT_SPOT: "⚠", VERDICT_REWRITE: "✕", VERDICT_UNDECIDED: "？"}


def _reason(rule_id, text, **kw):
    d = {"rule_id": rule_id, "text": text}
    d.update(kw)
    return d


def defect_paragraphs(defects):
    """缺陷段集合（段号 0 表示章级条目，不计入段占比）。"""
    return sorted({int(d.get("paragraph_index") or 0)
                   for d in defects or [] if int(d.get("paragraph_index") or 0) > 0})


def decide(analysis, destroy_info=None, author=None, cal=None, conflicts=None):
    """返回 {verdict, status, reason_code, reasons, ratio, paragraph_set, ...}。"""
    cal = cal or {}
    ratio_limit = float(cal.get("J10", 0.20))
    events_min = int(cal.get("J12", 2))
    defects = list((analysis or {}).get("defects") or [])
    metrics = (analysis or {}).get("metrics") or {}
    paras = int(metrics.get("paragraphs") or 0)
    pset = defect_paragraphs(defects)
    ratio = round(len(pset) / float(paras), 4) if paras else None

    base = {
        "chapter": (analysis or {}).get("chapter"),
        "defect_count": len(defects),
        "paragraph_set": pset,
        "defect_paragraph_ratio": ratio,
        "threshold": ratio_limit,
    }

    # 1) 输入缺失 → A（分得越细，用户越知道补什么）
    if not (analysis or {}).get("metrics", {}).get("words"):
        return dict(base, verdict=VERDICT_UNDECIDED, status="不可判定 A：无正文",
                    reason_code="A", reasons=[_reason("input", "这一章没有可判的正文")])
    if (analysis or {}).get("outline", {}).get("events") == []:
        return dict(base, verdict=VERDICT_UNDECIDED, status="不可判定 A：无章纲",
                    reason_code="A",
                    reasons=[_reason("outline", "没有章纲，病因①②无法对账")])

    # 2) 对象链被压缩两次 → B（结论不可信，直接不给档位）
    if destroy_info and destroy_info.get("compressed_twice"):
        return dict(base, verdict=VERDICT_UNDECIDED,
                    status="不可判定（B）：三稿被压缩两次，判定不可信",
                    reason_code="B",
                    reasons=[_reason(
                        "R02", "raw %s → checked %s → refined %s；成品取自被两次压缩后的版本"
                        % (destroy_info.get("words", {}).get("raw"),
                           destroy_info.get("words", {}).get("checked"),
                           destroy_info.get("words", {}).get("refined")),
                        evidence=destroy_info.get("shrink"))])

    # 3) 作者还没勾事件：结论为 null（F03b 硬约束，不猜）
    chosen = (author or {}).get("chosen")
    n_events = (author or {}).get("events")
    if not chosen:
        return dict(base, verdict=None, status="待作者勾选",
                    reason_code="A",
                    reasons=[_reason("author.events",
                                     "这一章发生了几件事由你勾（四选一），系统不替你数")])

    # 4) 章级依据 → 需重写本章（依据不得是字数，AC-15-3）
    chapter_level = []
    density = (analysis or {}).get("outline", {}).get("density") or {}
    if density.get("flagged") and (n_events is None or int(n_events) < events_min):
        chapter_level.append(_reason(
            "F04", density.get("reason") or "章纲密度虚高",
            paragraph=None, event_count=n_events, threshold=events_min))
    if n_events is not None and int(n_events) <= 1:
        chapter_level.append(_reason(
            "author.events", "作者认定整章只有 %s 件事 —— 篇幅再长也是一件事" % n_events,
            event_count=n_events))
    if chapter_level:
        return dict(base, verdict=VERDICT_REWRITE, status=VERDICT_REWRITE,
                    reason_code=None, reasons=chapter_level)

    # 5) 段级缺陷超阈值 → 需定点重写（给段号集合）
    if ratio is not None and ratio >= ratio_limit and pset:
        return dict(base, verdict=VERDICT_SPOT,
                    status="需定点重写（第 %s 段）" % "、".join(str(x) for x in pset[:8]),
                    reason_code=None,
                    reasons=[_reason("J10", "缺陷段 %d / 全章 %d 段 = %.0f%%，达到阈值 %.0f%%"
                                     % (len(pset), paras, ratio * 100, ratio_limit * 100),
                                     paragraphs=pset)])

    # 6) 冲突未消解 → 至少落到需定点重写
    if conflicts:
        return dict(base, verdict=VERDICT_SPOT, status=VERDICT_SPOT, reason_code=None,
                    reasons=[_reason("conflict", "有未消解的一致性冲突：%s" % conflicts[:3])])

    if n_events is not None and int(n_events) < events_min:
        return dict(base, verdict=VERDICT_REWRITE, status=VERDICT_REWRITE, reason_code=None,
                    reasons=[_reason("author.events",
                                     "作者认定只有 %s 件事（口径要求 ≥%d）" % (n_events, events_min))])

    return dict(base, verdict=VERDICT_OK, status=VERDICT_OK, reason_code=None,
                reasons=[_reason("none", "无章级依据；缺陷段 %d/%d 未超阈值"
                                 % (len(pset), paras))])
