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

# ---------------------------------------------------------------------------
# 审美五问（2026-10-20，抄 ainovel-cli 的审美细分）
#
# **为什么不叫「第八维」**：七维是既有契约（prompt / 测试 / GUI / 账本四处都按"七维齐全"
# 判完整）。往里加一维会让**所有历史账本一夜之间变成"不完整"**，那是拿数据迁移换一句
# 好听的话。所以另开一块独立字段 `aesthetic`，人话叫「审美五问」：
#
#   这章写得好看吗 —— 描写、手法、对话、用词、打动人心，各问一句，各引一句原文。
#
# 七维管"没写错"，五问管"写得好不好"。两者互不影响：
#   complete            仍只看七维；
#   aesthetic_complete  只看五问。
# ---------------------------------------------------------------------------
AESTHETIC = [
    {"code": "prose",     "label": "描写质感",   "hint": "有没有具体的感官细节，而不是形容词堆砌"},
    {"code": "technique", "label": "叙事手法",   "hint": "视角、节奏、信息给出的方式有没有讲究"},
    {"code": "dialogue",  "label": "对话区分度", "hint": "不同角色说话，能不能听出是谁"},
    {"code": "diction",   "label": "用词质量",   "hint": "有没有用词重复、口头禅式表达、AI 腔"},
    {"code": "impact",    "label": "情感打动力", "hint": "读完这一段，读者会不会被推动一下"},
]
AESTHETIC_CODES = [q["code"] for q in AESTHETIC]
AESTHETIC_LABEL = {q["code"]: q["label"] for q in AESTHETIC}

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


def _pick_aesthetic(entry):
    """把审美记录摊成 {code: raw_dict}（兼容 list / dict 两种写法）。"""
    raw = entry.get("aesthetic") if isinstance(entry, dict) else None
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                out[str(k)] = v
            elif _as_int_score(v) is not None:
                out[str(k)] = {"score": v}
    elif isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            code = str(it.get("q") or it.get("code") or it.get("key") or "").strip()
            if code:
                out[code] = it
    return out


def check_aesthetic(entry, chapter_text, chapter_no=0, reserved=None, questions=None):
    """校验「审美五问」。返回 {items, missing, unverified, complete, mean, findings}。

    ## 两条硬判据

    1. 五问缺一即整块作废（不算"部分完成"—— 只问了三问的评审等于没评审美）。
    2. **一问一句，同一句原文只算第一问**。模型最爱偷懒：拿同一句"她哭了"给五个问题
       各作一次证据。去重按 squash 后的字符串比对，第 2 次起判未举证并写明与哪一问撞了。
       `reserved` 传「七维已经用过的句子」→ 同一句既给七维又给审美也算复用
       （提示词里已经这么要求，这里必须真的判 —— 判据写两遍必然漂移）。

    分数仍走 0-10；低于 `WARN_SCORE` 进 findings（给作者看"哪里不好看"），
    不阻断 —— 审美是建议，不是闸门。
    """
    entry = entry if isinstance(entry, dict) else {}
    raw = _pick_aesthetic(entry)
    items, missing, unverified, findings = {}, [], [], []
    used = dict(reserved or {})   # squash(quote) -> 已经用过它的地方（问 code / "七维·标签"）
    # 适用问题（文体）：不在列表里的标 N/A，不计分、不算缺失（散文没有"对话区分度"）
    applicable_q = list(AESTHETIC_CODES) if questions is None else \
        [c for c in AESTHETIC_CODES if c in questions]

    for q in AESTHETIC:
        code = q["code"]
        if code not in applicable_q:
            items[code] = {
                "label": q["label"], "score": None, "quote": "", "note": "",
                "verified": False, "verdict": "n/a", "applicable": False,
                "reason": "本文体不适用",
            }
            continue
        d = raw.get(code)
        if not isinstance(d, dict):
            missing.append(code)
            items[code] = {
                "label": q["label"], "score": None, "quote": "", "note": "",
                "verified": False, "verdict": "unverified", "reason": "该问缺失",
            }
            continue
        score = _as_int_score(d.get("score", d.get("mark")))
        quote = str(d.get("quote") or d.get("evidence") or "").strip()
        ok, why = quote_ok(quote, chapter_text)
        key = _squash(quote)
        if ok and key in used:
            ok = False
            why = ("与「%s」用了同一句举证（一问一句，同一句只算第一处）"
                   % AESTHETIC_LABEL.get(used[key], used[key]))
        if score is None:
            reason = "分数缺失或不在 0-10 区间" + ("；" + why if why else "")
            ok = False
        else:
            reason = why
        if ok:
            used[key] = code
        else:
            unverified.append(code)
        rec = {
            "label": q["label"],
            "score": score if ok else None,
            "raw_score": score,
            "quote": quote,
            "note": str(d.get("note") or d.get("why") or "").strip(),
            "verified": bool(ok),
            "verdict": verdict_of(score if ok else None),
            "reason": reason,
            "applicable": True,
        }
        items[code] = rec
        if ok and score < WARN_SCORE:
            findings.append({
                "chapter": chapter_no, "kind": "aesthetic", "dim": code,
                "label": q["label"], "score": score, "quote": quote, "note": rec["note"],
            })

    scores = [items[c]["score"] for c in applicable_q if items[c]["verified"]]
    return {
        "items": items,
        "missing": missing,
        "unverified": unverified,
        # 完整性只看**适用**问题：散文没有"对话区分度"，那一条不该让它永远不完整
        "complete": bool(applicable_q) and not missing and not unverified,
        "applicable_questions": applicable_q,
        "verified_count": len(scores),
        "mean": round(sum(scores) / len(scores), 2) if scores else None,
        "findings": findings,
    }


def check_chapter(entry, chapter_text, chapter_no=None, dims=None, questions=None):
    """校验一章的七维记录，返回规范化结果（纯 JSON 可序列化）。"""
    entry = entry if isinstance(entry, dict) else {}
    no = chapter_no
    if no is None:
        raw_no = entry.get("chapter", entry.get("n"))
        try:
            no = int(raw_no)
        except (TypeError, ValueError):
            no = 0
    # ⚠️ 参数 dims 与输出字典同名：先存一份再覆盖，否则适用列表恒为空
    # （2026-10-20 实测：所有维度都被判 N/A，complete 恒 False）。
    _dim_arg, _q_arg = dims, questions
    applicable = (list(DIM_CODES) if _dim_arg is None
                  else [c for c in DIM_CODES if c in _dim_arg])
    applicable_q = (list(AESTHETIC_CODES) if _q_arg is None
                    else [c for c in AESTHETIC_CODES if c in _q_arg])
    raw_dims = _pick_dims(entry)
    dims, missing, unverified, findings = {}, [], [], []

    for code in DIM_CODES:
        if code not in applicable:
            dims[code] = {
                "label": DIM_LABEL[code], "score": None, "quote": "", "note": "",
                "verified": False, "verdict": "n/a", "applicable": False,
                "reason": "本文体不适用",
            }
            continue
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
            "applicable": True,
        }
        dims[code] = rec
        if verified and score < WARN_SCORE:
            findings.append({
                "chapter": no, "dim": code, "label": DIM_LABEL[code],
                "score": score, "quote": quote, "note": rec["note"],
            })

    verified_scores = [dims[c]["score"] for c in applicable if dims[c]["verified"]]
    mean = round(sum(verified_scores) / len(verified_scores), 2) if verified_scores else None
    # 审美五问（独立字段，不影响 complete —— 见 AESTHETIC 的说明）
    # 七维已经引过的句子不允许再审美的五问里复用（否则"引用原文"就成了套模板）
    reserved = {_squash(r["quote"]): "七维·" + r["label"]
                for r in dims.values() if r["verified"] and r["quote"]}
    aes = check_aesthetic(entry, chapter_text, no, reserved, questions=applicable_q)
    return {
        "chapter": no,
        "dims": dims,
        "missing": missing,
        "unverified": unverified,
        # 完整性只看**适用**维度：散文只有一维适用，那一维举证了就算完整
        "complete": bool(applicable) and not missing and not unverified,
        "applicable_dims": applicable,
        "not_applicable_dims": [c for c in DIM_CODES if c not in applicable],
        "verified_count": len(verified_scores),
        "mean": mean,
        "aesthetic": aes,
        "aesthetic_complete": aes["complete"],
        "findings": findings + aes["findings"],
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


def validate_payload(payload, chapter_texts, dims=None, questions=None):
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
        results.append(check_chapter(entry, chapter_texts[no], no,
                                     dims=dims, questions=questions))
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
        # 只统计**适用**该维的章（applicable 缺省 True，老账本没有这个键）
        appl = [r for r in results if r["dims"][code].get("applicable", True)]
        scores = [r["dims"][code]["score"] for r in appl if r["dims"][code]["verified"]]
        bad = [r["chapter"] for r in appl
               if r["dims"][code]["verified"] and r["dims"][code]["score"] < WARN_SCORE]
        per_dim[code] = {
            "label": DIM_LABEL[code],
            "mean": round(sum(scores) / len(scores), 2) if scores else None,
            "verified": len(scores),
            "unverified": len(appl) - len(scores),
            "applicable": len(appl),
            "not_applicable": len(results) - len(appl),
            "low": bad,
        }
    # 审美五问：独立统计（老账本没有这个字段 → 全是 None，不报错、不算作废）
    aes_items = {}
    for code in AESTHETIC_CODES:
        vals = [r["aesthetic"]["items"][code]["score"] for r in results
                if (r.get("aesthetic") or {}).get("items", {}).get(code, {}).get("verified")]
        _appl = [r for r in results
                 if (r.get("aesthetic") or {}).get("items", {}).get(code, {}).get("applicable", True)]
        bad = [r["chapter"] for r in results
               if (r.get("aesthetic") or {}).get("items", {}).get(code, {}).get("verified")
               and r["aesthetic"]["items"][code]["score"] < WARN_SCORE]
        aes_items[code] = {
            "label": AESTHETIC_LABEL[code],
            "mean": round(sum(vals) / len(vals), 2) if vals else None,
            "verified": len(vals),
            "unverified": len(_appl) - len(vals),
            "applicable": len(_appl),
            "low": bad,
        }
    aes_means = [r["aesthetic"]["mean"] for r in results
                 if (r.get("aesthetic") or {}).get("mean") is not None]
    aes = {
        "chapters": len(aes_means),
        "complete": sum(1 for r in results if (r.get("aesthetic") or {}).get("complete")),
        "mean": round(sum(aes_means) / len(aes_means), 2) if aes_means else None,
        "items": aes_items,
    }
    complete = [r for r in results if r["complete"]]
    means = [r["mean"] for r in results if r["mean"] is not None]
    return {
        "chapters": len(results),
        "complete": len(complete),
        "incomplete": [r["chapter"] for r in results if not r["complete"]],
        "mean": round(sum(means) / len(means), 2) if means else None,
        "dims": per_dim,
        "aesthetic": aes,
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
    lines += ["", "| 章 | " + " | ".join(d["label"] for d in DIMENSIONS) + " | 逻辑均分 | 审美 |",
              "|---|" + "---|" * (len(DIMENSIONS) + 2)]
    for r in results:
        cells = []
        for code in DIM_CODES:
            d = r["dims"][code]
            if not d.get("applicable", True):
                cells.append("—")          # 本文体不适用
            else:
                cells.append(str(d["score"]) if d["verified"] else "未举证")
        mean = r["mean"] if r["mean"] is not None else "—"
        aes_mean = (r.get("aesthetic") or {}).get("mean")
        aes_cell = str(aes_mean) if aes_mean is not None else "未评"
        lines.append(f"| {r['chapter']} | " + " | ".join(cells) + f" | {mean} | {aes_cell} |")
    aes_sum = s.get("aesthetic") or {}
    if aes_sum.get("chapters"):
        lines += ["", "## 审美五问（另开一块，不影响逻辑七维的完整性）",
                  "- 已评 %d 章，五问齐全 %d 章，均分 %s"
                  % (aes_sum["chapters"], aes_sum["complete"],
                     aes_sum["mean"] if aes_sum["mean"] is not None else "—")]
        for code in AESTHETIC_CODES:
            it = aes_sum["items"][code]
            if not it.get("applicable"):
                lines.append("- %s：不适用（本文体没有这一项）" % it["label"])
                continue
            line = "- %s：%s" % (it["label"],
                                 it["mean"] if it["mean"] is not None else "未举证")
            if it["unverified"]:
                line += "（%d 章未举证）" % it["unverified"]
            lines.append(line)
    if s.get("findings"):
        lines += ["", "## 低分项（< %d）" % WARN_SCORE]
        for f in s["findings"]:
            lines.append(f"- 第{f['chapter']}章 · {f['label']} {f['score']}：{f['note'] or '（无说明）'}")
            if f.get("quote"):
                lines.append(f"  > {f['quote']}")
    bad = [(r["chapter"], c) for r in results for c in r["unverified"]
           if r["dims"][c].get("applicable", True)]
    if bad:
        lines += ["", "## 未举证（不计分）"]
        for no, code in bad:
            lines.append(f"- 第{no}章 · {DIM_LABEL[code]}：{results[[x['chapter'] for x in results].index(no)]['dims'][code]['reason']}")
    return "\n".join(lines) + "\n"
