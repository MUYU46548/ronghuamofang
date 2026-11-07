# -*- coding: utf-8 -*-
"""候选正文打分（Best-of-N 的“择优”那一半）：确定性、零 token、每条扣分都说得出依据。

## 为什么单独一层

大纲侧早有 `utils/outline_panel.rank_drafts()`（结构化统计 + “这版在哪些项上排第一”），
但那套只认大纲的 OK/WARN/THIN 计数，正文用不上。正文要判的是另外几件事：
字数够不够、病灶词密不密、段落是不是被切成一句一段、有没有复读、字数预算偏没偏。

## 铁律：只给依据，不给“文学分”

打分只用来**在几个候选之间排序**，不是“这章写得好不好”的结论 —— 那是判定引擎与作者的活。
所以：

- 每一条都必须是**可复算的事实**（字数、密度、比率），并且带人话理由；
- 排序相同则按（分高、字数更接近目标中值、id 升序）稳定输出，同输入同输出；
- 任何一项拿不到数据（没词表、没有 findings）就**明说没有**，不默认满分也不默认零分。

## 五个维度（权重合计 100）

| 维度 | 权重 | 满分条件 | 怎么扣 |
|---|---|---|---|
| 字数达标 | 30 | 在 `[low, high]` 区间内 | 每偏离区间 1% 扣 1 分，扣完为止 |
| 病灶词密度 | 25 | 0 次/千字 | 线性扣到 20 次/千字为 0 分 |
| 段落结构 | 20 | 平均段长 80–250 汉字，且“一句一段”占比 ≤ 15% | 越界按比例扣 |
| 复读 | 15 | 12-gram 重复覆盖率 ≤ 5% | 线性扣到 35%（退化门上限）为 0 分 |
| 字数预算 | 10 | 与目标中值偏离 ≤ 10% | 线性扣到偏离 50% 为 0 分 |

用法：
    from utils.candidate_score import score_candidates
    rows = score_candidates([{"id": "cand1", "text": "..."}], target_words=[4000, 7200])
    rows[0]["score"], rows[0]["reasons"]
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

__all__ = ["score_text", "score_candidates", "DIM_WEIGHTS", "CALIBER"]

DIM_WEIGHTS = {
    "words": 30,          # 字数是否在区间内
    "cliche": 25,         # 病灶词密度
    "paragraphs": 20,     # 段落结构（一句一段 / 段落过短过长）
    "repeat": 15,         # 复读
    "budget": 10,         # 与目标字数的贴合度
}

CALIBER = {
    "cliche_zero_per_k": 0.0,
    "cliche_floor_per_k": 20.0,      # 每千字 20 次以下按比例给分，20 次及以上 0 分
    "repeat_full_at": 0.05,          # 12-gram 重复覆盖率 ≤5% 满分
    "repeat_zero_at": 0.35,          # = 退化门上限，到这儿 0 分
    "short_para_chars": 30,          # 少于这个汉字数的自然段算“一句一段”
    "short_para_max_ratio": 0.15,    # 一句一段占比上限
    "avg_para_low": 80,              # 平均段长（汉字）舒适区下沿
    "avg_para_high": 250,            # 上沿
    "budget_full_pct": 10.0,         # 与目标中值偏离 ≤10% 满分
    "budget_zero_pct": 50.0,
}


def _cn(text: str) -> int:
    """汉字数（与判定/校验同一口径：只数汉字）。"""
    from utils.word_count import count_cn_words
    return count_cn_words(text or "")


def _paragraphs(text: str) -> List[str]:
    out = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue                                  # 标题行不算自然段
        out.append(s)
    return out


def score_text(text: str, target_words: Optional[Iterable[int]] = None,
               cliche_terms: Optional[List[Any]] = None,
               findings: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """给一段候选正文打分。返回 `{score, words, dims, reasons, facts}`。

    `cliche_terms` 为 None 表示“没配词表”——该项按“未评”处理（给中性分并说明），
    既不假装干净，也不凭空扣分。
    """
    from utils.homogeneity import shingle_repeat

    text = text or ""
    words = _cn(text)
    paras = _paragraphs(text)
    dims: Dict[str, float] = {}
    reasons: List[str] = []
    facts: Dict[str, Any] = {"words": words, "paragraphs": len(paras)}

    # 1) 字数是否在区间内
    lo = hi = 0
    if target_words:
        tw = list(target_words)[:2]
        if len(tw) == 2:
            try:
                lo, hi = int(tw[0]), int(tw[1])
            except (TypeError, ValueError):
                lo = hi = 0
    if lo and hi:
        if lo <= words <= hi:
            dims["words"] = float(DIM_WEIGHTS["words"])
            reasons.append("字数在区间内（%s 汉字，区间 %s–%s）"
                           % (format(words, ","), format(lo, ","), format(hi, ",")))
        else:
            gap = lo - words if words < lo else words - hi
            pct = gap / float(lo or hi) * 100.0
            dims["words"] = max(0.0, DIM_WEIGHTS["words"] - pct)
            reasons.append("字数%s区间 %s 汉字（%s 汉字，差 %.1f%%）"
                           % ("低于" if words < lo else "超出", format(gap, ","),
                              format(words, ","), pct))
    else:
        dims["words"] = DIM_WEIGHTS["words"] * 0.5
        reasons.append("没有字数区间可对照，这一项按中性计（不算达标）")

    # 2) 病灶词密度
    if cliche_terms is None:
        dims["cliche"] = DIM_WEIGHTS["cliche"] * 0.5
        reasons.append("没配病灶词表，这一项未评（按中性计）")
        facts["cliche_per_k"] = None
    else:
        hits = 0
        for term in cliche_terms:
            try:
                hits += len(term.pattern.findall(text))
            except Exception:                             # noqa: BLE001
                continue
        per_k = (hits * 1000.0 / words) if words else 0.0
        ratio = min(1.0, per_k / CALIBER["cliche_floor_per_k"])
        dims["cliche"] = round(DIM_WEIGHTS["cliche"] * (1.0 - ratio), 2)
        facts["cliche_hits"] = hits
        facts["cliche_per_k"] = round(per_k, 2)
        if hits:
            reasons.append("病灶词 %d 次（%.1f 次/千字）" % (hits, per_k))
        else:
            reasons.append("没命中病灶词")

    # 3) 段落结构
    if paras:
        short = sum(1 for p in paras if _cn(p) < CALIBER["short_para_chars"])
        short_ratio = short / float(len(paras))
        avg = words / float(len(paras))
        facts.update({"avg_para_words": round(avg, 1),
                      "short_para_ratio": round(short_ratio, 3)})
        pen = 0.0
        if short_ratio > CALIBER["short_para_max_ratio"]:
            over = (short_ratio - CALIBER["short_para_max_ratio"]) / (1 - CALIBER["short_para_max_ratio"])
            pen += over * (DIM_WEIGHTS["paragraphs"] * 0.6)
            reasons.append("%d 段是一句一段（占 %.0f%%，上限 %.0f%%）"
                           % (short, short_ratio * 100,
                              CALIBER["short_para_max_ratio"] * 100))
        if avg < CALIBER["avg_para_low"]:
            r = (CALIBER["avg_para_low"] - avg) / float(CALIBER["avg_para_low"])
            pen += r * (DIM_WEIGHTS["paragraphs"] * 0.4)
            reasons.append("平均段长偏短（%.0f 汉字/段，舒适区 %d–%d）"
                           % (avg, CALIBER["avg_para_low"], CALIBER["avg_para_high"]))
        elif avg > CALIBER["avg_para_high"]:
            r = min(1.0, (avg - CALIBER["avg_para_high"]) / float(CALIBER["avg_para_high"]))
            pen += r * (DIM_WEIGHTS["paragraphs"] * 0.4)
            reasons.append("平均段长偏长（%.0f 汉字/段，舒适区 %d–%d）"
                           % (avg, CALIBER["avg_para_low"], CALIBER["avg_para_high"]))
        dims["paragraphs"] = round(max(0.0, DIM_WEIGHTS["paragraphs"] - pen), 2)
    else:
        dims["paragraphs"] = 0.0
        reasons.append("没有可读的自然段（只有标题或空文）")

    # 4) 复读（12-gram；与退化门同一把尺子）
    rate, _grams = shingle_repeat(text, n=12)
    facts["repeat_12"] = rate
    span = CALIBER["repeat_zero_at"] - CALIBER["repeat_full_at"]
    if rate <= CALIBER["repeat_full_at"]:
        dims["repeat"] = float(DIM_WEIGHTS["repeat"])
    else:
        r = min(1.0, (rate - CALIBER["repeat_full_at"]) / span)
        dims["repeat"] = round(DIM_WEIGHTS["repeat"] * (1.0 - r), 2)
    if rate > CALIBER["repeat_full_at"]:
        reasons.append("复读偏高（12-gram 重复覆盖 %.1f%%，上限 %.0f%%）"
                       % (rate * 100, CALIBER["repeat_zero_at"] * 100))

    # 5) 与目标字数的贴合度（预算）
    if lo and hi:
        mid = (lo + hi) / 2.0
        dev = abs(words - mid) / mid * 100.0
        if dev <= CALIBER["budget_full_pct"]:
            dims["budget"] = float(DIM_WEIGHTS["budget"])
        else:
            r = min(1.0, (dev - CALIBER["budget_full_pct"])
                    / (CALIBER["budget_zero_pct"] - CALIBER["budget_full_pct"]))
            dims["budget"] = round(DIM_WEIGHTS["budget"] * (1.0 - r), 2)
            reasons.append("离目标中值（%s 汉字）%.0f%%" % (format(int(mid), ","), dev))
    else:
        dims["budget"] = DIM_WEIGHTS["budget"] * 0.5

    # 质检发现（有就计入理由，不单独占权重：那条链自己的判据在 quality_checklist 里）
    if findings:
        reasons.append("质检发现 %d 条（%s）"
                       % (len(findings),
                          "、".join(sorted({str(f.get("type") or "?") for f in findings})[:4])))

    score = round(sum(dims.values()), 1)
    return {"score": score, "words": words, "dims": dims, "reasons": reasons, "facts": facts}


def score_candidates(cands: List[Dict[str, Any]],
                     target_words: Optional[Iterable[int]] = None,
                     cliche_terms: Optional[List[Any]] = None,
                     findings_by_id: Optional[Dict[str, List[Dict[str, Any]]]] = None
                     ) -> List[Dict[str, Any]]:
    """给 N 个候选打分并排序（score 降 → 字数更贴中值 → id 升）。

    返回每项：`{id, score, words, dims, reasons, facts}`。**同输入同输出**。
    """
    findings_by_id = findings_by_id or {}
    tw = list(target_words)[:2] if target_words else []
    mid = ((int(tw[0]) + int(tw[1])) / 2.0) if len(tw) == 2 else 0.0
    rows = []
    for c in cands or []:
        cid = str(c.get("id"))
        row = score_text(c.get("text") or "", target_words=tw or None,
                         cliche_terms=cliche_terms,
                         findings=findings_by_id.get(cid))
        row["id"] = cid
        rows.append(row)
    rows.sort(key=lambda r: (-r["score"],
                             abs(r["words"] - mid) if mid else 0, r["id"]))
    return rows
