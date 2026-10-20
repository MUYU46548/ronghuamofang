# -*- coding: utf-8 -*-
"""七维评审账本 —— 把 stage5 的自由文本审稿换成**可回归的结构化判定**。

## 为什么要有它

旧 stage5 只产出一份 `check_report.md`：模型写什么、写几段、有没有真读过原稿，
全都无从判断。同一本书重跑两次，"检查通过"的含义可能完全不同 —— 这种产出
**不可回归**（无法 diff、无法断言、无法知道模型是不是只是在复述任务要求）。

台账（docs/收口台账-20261019.md）二·「七维评审 + 原文举证」定的判据是：
**每一维都必须引用原文句子，否则不算评分**。本模块就是那条判据的唯一实现。

## 判据（全部确定性、零额外 token）

七维与 `prompts/stage5_check.md` 的七项检查**一一对应**（不另起一套名词）：
情节连贯 / 时序一致 / 空间合理 / 指代清晰 / 关系自洽 / 人设统一 / 常识科学。

1. **齐全**：七维缺一不可、不可重复。缺就是缺，不补默认分（补 0 分等于伪造结论）。
2. **区间**：分数必须是 0-10 的整数，越界该维作废。
3. **举证**：`quote` 必须是**该章正文里真实存在的句子** —— 用归一化后的子串匹配；
   命中不了 = 模型在编，该维 `verified=false`、`score=None`（**不算评分**）。
   引用太短（< 8 个有效字符）同样不算 —— 短到"他点了点头"的句子定位不到任何一处，
   举证等于没有。
4. **完整**：该章七维全部 verified 才算 `complete=True`。任何一维未举证 → 整章不完整。
5. **落盘**：每章一份 `data/review/NN.json`（可 diff、可回归）+ 汇总 `summary.json`
   + 人读版 `ledger.md`。

第 3 条的宽松面（有意为之，写在注释里免得后人"收紧"）：模型引用长句时常插省略号，
所以含 `…`/`...` 的 quote 按省略号切成片段，**要求每一段都命中** —— 既容纳省略，
又不放水（编造的那一段照样命中不了）。

## 不做什么

不做语义判断：分数高低由模型给，本模块只判「有没有资格算分」。
"""
import json
import re
from pathlib import Path

from utils.validator import parse_llm_json
from utils.file_io import write_text

# ---------------------------------------------------------------------------
# 七维定义（单一事实源）：顺序即提示词里的编号，GUI/报告都按这个顺序排
# ---------------------------------------------------------------------------
DIMENSIONS = [
    {"code": "plot",        "label": "情节连贯性",   "hint": "主线支线是否咬合、转折是否有铺垫"},
    {"code": "timeline",    "label": "时序一致性",   "hint": "时间线前后是否矛盾"},
    {"code": "space",       "label": "空间合理性",   "hint": "地理与场景转换是否说得通"},
    {"code": "reference",   "label": "名词指代清晰", "hint": "指代是否产生歧义（他/她/它、绰号）"},
    {"code": "relation",    "label": "人物关系自洽", "hint": "互动是否符合既有关系"},
    {"code": "persona",     "label": "人设统一",     "hint": "行为与背景设定是否一致"},
    {"code": "commonsense", "label": "常识与科学",   "hint": "是否违背常识或作品内设定"},
]
DIM_CODES = [d["code"] for d in DIMENSIONS]
DIM_LABEL = {d["code"]: d["label"] for d in DIMENSIONS}

MIN_QUOTE_CHARS = 8      # 举证下限（有效字符，标点空白不计）
GOOD_SCORE = 8           # >= 视为无问题
WARN_SCORE = 6           # >= 视为可接受；低于此进 findings
MAX_QUOTE_CHARS = 400    # 单条举证上限（防止模型整段粘贴冒充"引用"）

# 匹配时**忽略**的字符：空白 + 中英文标点/引号。
# 为什么连标点一起忽略：模型转述原文时经常改引号形态（「」/“”/"）、把逗号写成半角，
# 这种差异不该判成"编造"；但字符顺序编不了，所以子串匹配依然能抓住真正的虚构。
_IGNORE_RE = re.compile(
    r"[\s\u3000“”‘’「」『』\"'`，。！？；：、,.!?;:—…·\-（）()【】\[\]《》<>~～|/\\*#]+"
)
_ELLIPSIS_RE = re.compile(r"…+|\.{3,}")


class LedgerError(Exception):
    """账本结构不可用（JSON 缺失/不是对象/没有 chapters）。"""


def _squash(text):
    """归一化：删掉空白与标点，只留字符序列。用于子串匹配。"""
    return _IGNORE_RE.sub("", str(text or ""))


def quote_ok(quote, chapter_text):
    """该 quote 是否真的出自 chapter_text（返回 (bool, 原因)）。"""
    q = str(quote or "").strip()
    if len(_squash(q)) < MIN_QUOTE_CHARS:
        return False, f"举证太短（<{MIN_QUOTE_CHARS} 个有效字符）"
    if len(q) > MAX_QUOTE_CHARS:
        return False, f"举证过长（>{MAX_QUOTE_CHARS} 字，应引用句子而不是整段）"
    hay = _squash(chapter_text)
    if not hay:
        return False, "章节正文为空，无法核对举证"
    parts = [p for p in _ELLIPSIS_RE.split(q) if _squash(p)]
    if not parts:
        return False, "举证为空"
    for p in parts:
        if _squash(p) not in hay:
            return False, "举证未在本章原文中找到"
    return True, ""


def verdict_of(score):
    """分数 → 结论（由分数推导，不让模型自选结论，免得 8 分 + bad 这种自相矛盾）。"""
    if score is None:
        return "unverified"
    if score >= GOOD_SCORE:
        return "ok"
    if score >= WARN_SCORE:
        return "warn"
    return "bad"


def _as_int_score(v):
    """分数只收 0-10 整数；`7.5`/`"7"` 容错，其它一律 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 0 <= v <= 10 else None
    if isinstance(v, float):
        return int(round(v)) if 0 <= v <= 10 else None
    if isinstance(v, str) and v.strip():
        try:
            f = float(v.strip())
        except ValueError:
            return None
        return int(round(f)) if 0 <= f <= 10 else None
    return None


def _pick_dims(entry):
    """把一条章节记录里的维度摊成 {code: raw_dict}（兼容 dims 列表/字典/平铺三种写法）。

    LLM 三种都写过，与其让它重跑，不如都认 —— 但**认的是形态，不是内容**：
    分数区间、举证真伪一样要过判据。
    """
    out = {}
    dims = entry.get("dims")
    if isinstance(dims, dict):
        for k, v in dims.items():
            if isinstance(v, dict):
                out[str(k)] = v
            elif _as_int_score(v) is not None:
                out[str(k)] = {"score": v}
    elif isinstance(dims, list):
        for d in dims:
            if not isinstance(d, dict):
                continue
            code = str(d.get("dim") or d.get("code") or d.get("key") or "").strip()
            if code:
                out[code] = d
    # 平铺写法：{"plot": {...}, "timeline": {...}} 直接挂在章上
    for code in DIM_CODES:
        if code not in out and isinstance(entry.get(code), dict):
            out[code] = entry[code]
    return out


def check_chapter(entry, chapter_text, chapter_no=None):
    """校验一章的七维记录，返回规范化结果（纯 JSON 可序列化）。"""
    entry = entry if isinstance(entry, dict) else {}
    no = chapter_no
    if no is None:
        raw_no = entry.get("chapter", entry.get("n"))
        try:
            no = int(raw_no)
        except (TypeError, ValueError):
            no = 0
    raw_dims = _pick_dims(entry)
    dims, missing, unverified, findings = {}, [], [], []

    for code in DIM_CODES:
        d = raw_dims.get(code)
        if not isinstance(d, dict):
            missing.append(code)
            dims[code] = {
                "label": DIM_LABEL[code], "score": None, "quote": "",
                "note": "", "verified": False, "verdict": "unverified",
                "reason": "该维缺失",
            }
            continue
        score = _as_int_score(d.get("score", d.get("mark")))
        quote = str(d.get("quote") or d.get("evidence") or d.get("原文") or "").strip()
        ok, why = quote_ok(quote, chapter_text)
        if score is None:
            why = why if not ok else ""
            reason = "分数缺失或不在 0-10 区间" + (f"；{why}" if why else "")
            ok = False
        else:
            reason = why
        verified = bool(ok)
        if not verified:
            unverified.append(code)
        rec = {
            "label": DIM_LABEL[code],
            "score": score if verified else None,
            "raw_score": score,
            "quote": quote,
            "note": str(d.get("note") or d.get("why") or "").strip(),
            "verified": verified,
            "verdict": verdict_of(score if verified else None),
            "reason": reason,
        }
        dims[code] = rec
        if verified and score < WARN_SCORE:
            findings.append({
                "chapter": no, "dim": code, "label": DIM_LABEL[code],
                "score": score, "quote": quote, "note": rec["note"],
            })

    verified_scores = [r["score"] for r in dims.values() if r["verified"]]
    mean = round(sum(verified_scores) / len(verified_scores), 2) if verified_scores else None
    return {
        "chapter": no,
        "dims": dims,
        "missing": missing,
        "unverified": unverified,
        "complete": not missing and not unverified,
        "verified_count": len(verified_scores),
        "mean": mean,
        "findings": findings,
        "summary": str(entry.get("summary") or "").strip(),
    }


def load_payload(text):
    """从 LLM 原文里取账本 JSON（允许围栏/前后废话）。失败抛 LedgerError。"""
    data = parse_llm_json(text)
    if data is None:
        raise LedgerError("未找到可解析的 JSON")
    if isinstance(data, list):
        data = {"chapters": data}
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), list):
        raise LedgerError("缺少 chapters 列表")
    return data


def validate_payload(payload, chapter_texts):
    """批量校验。chapter_texts: {章号: 正文}。

    返回 (results: list[dict], unknown: list) —— unknown 是原文里不存在的章号
    （模型编了第 99 章），不参与统计但要报出来。
    """
    results, unknown = [], []
    seen = set()
    for entry in payload.get("chapters") or []:
        if not isinstance(entry, dict):
            continue
        raw_no = entry.get("chapter", entry.get("n"))
        try:
            no = int(raw_no)
        except (TypeError, ValueError):
            unknown.append(raw_no)
            continue
        if no not in chapter_texts:
            unknown.append(no)
            continue
        if no in seen:                     # 同章重复 → 只认第一条（不合并，不猜）
            continue
        seen.add(no)
        results.append(check_chapter(entry, chapter_texts[no], no))
    results.sort(key=lambda r: r["chapter"])
    return results, unknown


def missing_chapters(results, chapter_texts):
    """本批里**没有**任何评审记录的章号（不是"不完整"，是压根没提）。"""
    got = {r["chapter"] for r in results}
    return sorted(set(chapter_texts) - got)


def summarize(results):
    """汇总：每维均值/未举证数、全章完整率、重点问题清单。"""
    per_dim = {}
    for code in DIM_CODES:
        scores = [r["dims"][code]["score"] for r in results
                  if r["dims"][code]["verified"]]
        bad = [r["chapter"] for r in results
               if r["dims"][code]["verified"] and r["dims"][code]["score"] < WARN_SCORE]
        per_dim[code] = {
            "label": DIM_LABEL[code],
            "mean": round(sum(scores) / len(scores), 2) if scores else None,
            "verified": len(scores),
            "unverified": len(results) - len(scores),
            "low": bad,
        }
    complete = [r for r in results if r["complete"]]
    means = [r["mean"] for r in results if r["mean"] is not None]
    return {
        "chapters": len(results),
        "complete": len(complete),
        "incomplete": [r["chapter"] for r in results if not r["complete"]],
        "mean": round(sum(means) / len(means), 2) if means else None,
        "dims": per_dim,
        "findings": [f for r in results for f in r["findings"]],
    }


def write_ledger(base_dir, result):
    """单章落盘：data/review/NN.json（两位章号，与 chapters/ 命名一致）。"""
    d = Path(base_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%02d.json" % int(result.get("chapter") or 0))
    write_text(p, json.dumps(result, ensure_ascii=False, indent=2))
    return p


def write_summary(base_dir, results, extra=None):
    """汇总落盘：summary.json（机器读）+ ledger.md（人读）。"""
    d = Path(base_dir)
    d.mkdir(parents=True, exist_ok=True)
    s = summarize(results)
    if extra:
        s.update(extra)
    write_text(d / "summary.json", json.dumps(s, ensure_ascii=False, indent=2))
    write_text(d / "ledger.md", render_markdown(results, s))
    return s


def render_markdown(results, summary=None):
    """人读版：一章一行，七维分数 + 举证状态。未举证的维度写「未举证」。"""
    s = summary or summarize(results)
    lines = ["# 七维评审账本", ""]
    lines.append(f"- 章节：{s['chapters']} 章（完整 {s['complete']} 章）")
    lines.append(f"- 均分：{s['mean'] if s['mean'] is not None else '—'}")
    if s.get("incomplete"):
        lines.append(f"- 不完整：{'、'.join('第%d章' % n for n in s['incomplete'])}")
    lines += ["", "| 章 | " + " | ".join(d["label"] for d in DIMENSIONS) + " | 均分 |",
              "|---|" + "---|" * (len(DIMENSIONS) + 1)]
    for r in results:
        cells = []
        for code in DIM_CODES:
            d = r["dims"][code]
            cells.append(str(d["score"]) if d["verified"] else "未举证")
        mean = r["mean"] if r["mean"] is not None else "—"
        lines.append(f"| {r['chapter']} | " + " | ".join(cells) + f" | {mean} |")
    if s.get("findings"):
        lines += ["", "## 低分项（< %d）" % WARN_SCORE]
        for f in s["findings"]:
            lines.append(f"- 第{f['chapter']}章 · {f['label']} {f['score']}：{f['note'] or '（无说明）'}")
            if f.get("quote"):
                lines.append(f"  > {f['quote']}")
    bad = [(r["chapter"], c) for r in results for c in r["unverified"]]
    if bad:
        lines += ["", "## 未举证（不计分）"]
        for no, code in bad:
            lines.append(f"- 第{no}章 · {DIM_LABEL[code]}：{results[[x['chapter'] for x in results].index(no)]['dims'][code]['reason']}")
    return "\n".join(lines) + "\n"
