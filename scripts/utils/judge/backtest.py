# -*- coding: utf-8 -*-
"""F14 离线回测台：拿盘上现有的章跑一遍全套判定，零模型调用、断网可跑。

两条硬规矩：
1. **只对 `raw` 回测**（AC-17-2）。拿 checked/refined 回测等于在已经被削平的稿上
   验证判定能力 —— 基线自毁。
2. **不写第二份文件**（F13）：跑一次只落 `records.json`；报告是**算出来的**，
   导出（md/docx）是另一个显式动作。

零 token 不是口号：本模块不 import 任何 llm_client / make_client，单测里也会
断言"没有模型调用记录"。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from . import caliber as C
from . import destroy as D
from . import records as R
from . import rules as RU
from . import verdict as V
from . import text as T

RAW_DIR = "data/chapters/raw"
CHECKED_DIR = "data/chapters/checked"
REFINED_DIR = "data/chapters/refined"
OUTLINE_DIR = "data/outline/chapters"
# R19 基准集（作者自选的"好稿"样本）：存在才算"不误报"有依据
BENCHMARK_REL = "data/judge/benchmark"

NOTICE_BACKTEST = "存量回测判定，非新跑拦截"
NOTICE_NO_ARTIFACT = "本报告不改变现有成品文件"

# 判据矩阵的行（AC-17-3：每格数值或显式"不适用（原因）"，不许空格）
MATRIX_ROWS = ["J01", "J02", "J03", "J04", "J05", "J06", "J07", "J08", "J09",
               "J10", "J11", "J12", "J13", "J14"]


def _read(p):
    try:
        return p.read_text(encoding="utf-8")
    except Exception:                                          # noqa: BLE001
        return ""


def _fp(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def discover(root="."):
    """章号清单（以 raw 为准；只有 checked/refined 的章不算可回测章）。"""
    raw = Path(root) / RAW_DIR
    nums = []
    if raw.is_dir():
        for p in raw.glob("*.md"):
            stem = p.stem
            if stem.isdigit():
                nums.append(int(stem))
    return sorted(set(nums))


def judge_chapter(root, n, cal_doc=None, cal_values=None, records_doc=None):
    root = Path(root)
    body = _read(root / RAW_DIR / ("%02d.md" % n))
    outline = _read(root / OUTLINE_DIR / ("%02d.md" % n))
    checked = _read(root / CHECKED_DIR / ("%02d.md" % n))
    refined = _read(root / REFINED_DIR / ("%02d.md" % n))
    checked = checked or None
    refined = refined or None

    analysis = RU.analyze_chapter(body, outline_text=outline, chapter=n, cal=cal_values)
    d = D.compare(body, checked, refined, cal=cal_values)
    author = ((records_doc or {}).get("author") or {}).get("events", {}).get(str(n))
    v = V.decide(analysis, d, author, cal=cal_values)

    obj = {
        "raw_fp": _fp(body), "raw_words": T.count_cn_words(body),
        "checked_words": (T.count_cn_words(checked) if checked else None),
        "refined_words": (T.count_cn_words(refined) if refined else None),
    }
    return {
        "chapter": n,
        "verdict": v,
        "analysis": analysis,
        "destroy": d,
        "object": obj,
        "author": author,
        # 原文只为**引文校验**（R05 AC-05-2：卡上的引文必须真在正文里）。
        # 不进 report / records —— 正文已经在 paragraphs 里有一份了。
        "body": body,
    }


def _rule_refs(res):
    """R05 口径解释卡的**引文**：每条判据取第一条命中的原句。

    AC-05-2“卡内正例/反例引文必须在原文中存在，对不上则卡作废并计数”——
    所以这里做两道：
      ① 引文只从 `analysis.defects` 里取（规则从正文切句得来，不是人编的）；
      ② 仍然验一次 `sentence in body`：规则将来改了切句方式，引文可能与正文脱节，
         那种卡必须作废并**计数**（`invalid_refs`），不能静默留着。

    返回 `({Jxx: {sentence, paragraph_index, rule_name}}, invalid_count)`。
    """
    body = res.get("body") or ""
    out, invalid = {}, 0
    for d in (res.get("analysis") or {}).get("defects") or []:
        rid = d.get("rule_id")
        sent = (d.get("sentence") or "").strip()
        if not rid or not sent or rid in out:
            continue
        if body and sent not in body:
            invalid += 1
            continue
        out[rid] = {"sentence": T.snippet(sent, 40),
                    "paragraph_index": d.get("paragraph_index"),
                    "rule_name": d.get("rule_name") or ""}
    return out, invalid


def _detector_alerts(prev_chapters, cur_chapters, words_min):
    """R1 判据失效告警（评审意见里标"高风险"的那条，2026-10-31）。

    ## 它防的是什么

    判据只在**旧的 AI 腔**上标定过。0.7.20 拆锁（放开密度与风格）之后，
    腔调形态必然改变 —— 于是可能出现“某章 F01+F03 命中数从 ≥5 掉到 0，
    而字数完全达标”。这有两种解释：稿子真的改好了，或者**判据已经对新腔调失效**。

    系统**不分不清**这两者（要分清得有基准集，R19 目前为空）。所以这里只做
    fail-loud：出声，不判档、不改结论。宁可让作者多抽检一章，不能静默显示"没查出问题"。

    可比性前提：上一次 run 里该章的 `counts` 存在（本字段 2026-10-31 才有，
    更早的历史 run 没有 → 跳过并说明，不假报）。
    """
    prev = {int(x.get("chapter") or 0): x for x in (prev_chapters or [])}
    out = []
    for c in cur_chapters or []:
        n = int(c.get("chapter") or 0)
        p = prev.get(n)
        if not p or not isinstance(p.get("counts"), dict):
            continue
        pc = p["counts"]
        before = int(pc.get("F01") or 0) + int(pc.get("F03") or 0)
        cc = c.get("counts") or {}
        after = int(cc.get("F01") or 0) + int(cc.get("F03") or 0)
        words = c.get("words")
        if before >= 5 and after == 0 and isinstance(words, int) and words >= words_min:
            out.append({
                "chapter": n, "before": before, "after": after, "words": words,
                "note": ("第 %d 章：上一次判定命中 %d 处（F01 字数/句长 + F03 可替换骨架），"
                         "这次 0 处，而字数 %d 达标。可能是稿子真改好了，"
                         "也可能是判据对新腔调失效 —— 建议人工抽检这一章，"
                         "确认不是“判据睡着了”。" % (n, before, words)),
            })
    return out


def _matrix_row(res):
    a = res["analysis"]
    m = a["metrics"]
    vals = {
        "J01": m["words"],
        "J02": "合格" if not (m["below_min_words"] or m["above_max_words"]) else
               ("偏少" if m["below_min_words"] else "偏多"),
        "J03": len([x for x in a["defects"] if x["rule_id"] == "J03"]),
        "J04": len([x for x in a["defects"] if x["rule_id"] == "J04"]),
        "J05": len([x for x in a["defects"] if x["rule_id"] == "J05"]),
        "J06": len(a["fake_concrete"]),
        "J07": len(a["skeletons"]),
        "J08": a["outline"]["density"].get("count") or 0
               if a["outline"]["density"].get("flagged") else 0,
        "J09": a["outline"]["landing"].get("rate")
               if a["outline"]["landing"].get("rate") is not None else "不适用（无章纲）",
        "J10": res["verdict"].get("defect_paragraph_ratio")
               if res["verdict"].get("defect_paragraph_ratio") is not None else "不适用（无正文）",
        "J11": (res["destroy"].get("shrink", {}).get("raw->checked")
                if res["destroy"].get("ok")
                and res["destroy"].get("shrink", {}).get("raw->checked") is not None
                else "不适用（缺稿）"),
        "J12": (res["author"] or {}).get("events", "未勾选"),
        "J13": "不适用（预算校验不在回测范围）",
        "J14": "不适用（预算校验不在回测范围）",
    }
    return {"chapter": res["chapter"], "cells": vals}


def judge_all(root=".", cal_values=None, records_doc=None):
    """对全部可回测章跑一遍判定（不写盘）。`preview` 靠它算影响面。"""
    vals = cal_values if cal_values is not None else C.values(C.load(root))
    recs = records_doc if records_doc is not None else R.load(root)
    return [judge_chapter(root, n, None, vals, recs) for n in discover(root)]


def preview(root=".", changes=None):
    """R06 改口径的影响面预览（**一个字都不写**）。

    返回三行分列：受影响章数 / 预计转不合格章数 / 不可重算判据需作者重录条数。
    """
    root = Path(root)
    cal_doc = C.load(root)
    ok, why = C.require_ready(cal_doc)
    if not ok:
        return {"ok": False, "error": why}
    changes = changes or {}
    cur_vals = C.values(cal_doc)
    # 界面传来的是字符串（"0.30"）——先按原判据类型归一，否则规则里 int() 直接炸
    proposed = {}
    crit = {c["id"]: c for c in cal_doc.get("criteria") or []}
    for k, v in changes.items():
        if k not in crit:
            continue
        try:
            proposed[k] = C.coerce_value(crit[k].get("value"), v)
        except (TypeError, ValueError):
            continue
    new_vals = dict(cur_vals)
    new_vals.update(proposed)
    cur = {r["chapter"]: r["verdict"] for r in judge_all(root, cur_vals)}
    new = {r["chapter"]: r["verdict"] for r in judge_all(root, new_vals)}
    if not cur:
        return {"ok": False, "error": "没有可回测的稿"}

    affected, to_fail = [], []
    for n in sorted(cur):
        c, x = cur[n], new[n]
        changed = (c.get("verdict") != x.get("verdict")
                   or c.get("status") != x.get("status"))
        if changed:
            affected.append({"chapter": n, "before": c.get("verdict") or c.get("status"),
                             "after": x.get("verdict") or x.get("status")})
        if (x.get("verdict") in (V.VERDICT_SPOT, V.VERDICT_REWRITE)
                and c.get("verdict") not in (V.VERDICT_SPOT, V.VERDICT_REWRITE)):
            to_fail.append(n)
    manual = [{"id": c["id"], "name": c["name"]}
              for c in cal_doc.get("criteria") or []
              if c["id"] in proposed and c.get("kind") == "不可自动判定"]
    return {
        "ok": True, "changed": bool(changes),
        "affected_count": len(affected), "affected": affected,
        "to_fail_count": len(to_fail), "to_fail": to_fail,
        "manual_count": len(manual), "manual": manual,
        "caliber": {"current_version": cal_doc.get("version"),
                    "current_fingerprint": cal_doc.get("fingerprint"),
                    "next_fingerprint": C.fingerprint(
                        [dict(c, value=new_vals.get(c["id"], c["value"]))
                         for c in cal_doc.get("criteria") or []])},
        "message": ("受影响 %d 章：%d 章将转为不合格；%d 条不可重算判据需你重录"
                    % (len(affected), len(to_fail), len(manual))),
    }


def benchmark_count(root="."):
    """基准集条目数（正例稿由作者从界面导入，见 utils/benchmark.py）。"""
    try:
        from utils import benchmark as _bench
        return _bench.count(root)
    except Exception:                                          # noqa: BLE001
        return 0


def run(root=".", book_name="", by="作者"):
    """跑一次全量回测：判定 + 汇总 + 落记录（唯一写点）。返回报告 payload。"""
    root = Path(root)
    cal_doc = C.load(root)
    ok, why = C.require_ready(cal_doc)
    if not ok:
        return {"ok": False, "error": why, "caliber": cal_doc}
    cal_values = C.values(cal_doc)
    recs = R.load(root)
    nums = discover(root)
    if not nums:
        return {"ok": False, "error": "没有可回测的稿：%s 下没有 NN.md" % RAW_DIR,
                "caliber": {"version": cal_doc.get("version")}}

    prev_run = R.latest_run(recs)
    prev_fp = {}
    for x in (prev_run or {}).get("chapters") or []:
        prev_fp[int(x.get("chapter") or 0)] = ((x.get("object") or {}).get("raw_fp"))

    results = [judge_chapter(root, n, cal_doc, cal_values, recs) for n in nums]
    chapters = []
    total_defects = 0
    object_changed = []
    refs_all = {}          # {Jxx: {sentence, paragraph_index, chapter}} 全书取第一条
    invalid_refs = 0
    for r in results:
        v = r["verdict"]
        defects = r["analysis"]["defects"]
        total_defects += len(defects) + (1 if r["analysis"]["outline"]["density"].get("flagged") else 0)
        top = [
            {"paragraph_index": d["paragraph_index"], "sentence": T.snippet(d["sentence"], 20),
             "rule_id": d["rule_id"], "rule_name": d["rule_name"], "count": d["count"]}
            for d in defects if int(d.get("paragraph_index") or 0) > 0
        ][:3]
        # R08：对象指纹变了 → 这一章上一次的结论作废（只标出来，不删除历史）
        changed = bool(prev_fp.get(r["chapter"])
                       and prev_fp[r["chapter"]] != r["object"]["raw_fp"])
        if changed:
            object_changed.append(r["chapter"])
        refs, bad = _rule_refs(r)
        invalid_refs += bad
        for rid, ref in refs.items():
            if rid not in refs_all:
                refs_all[rid] = dict(ref, chapter=r["chapter"])
        chapters.append({
            "chapter": r["chapter"],
            "verdict": v.get("verdict"),
            "status": v.get("status"),
            "symbol": V.SYMBOL.get(v.get("verdict"), ""),
            "reason_code": v.get("reason_code"),
            "reasons": v.get("reasons"),
            "defect_count": v.get("defect_count"),
            "defect_paragraph_ratio": v.get("defect_paragraph_ratio"),
            "paragraph_set": v.get("paragraph_set"),
            "top_defects": top,
            "words": r["analysis"]["metrics"]["words"],
            "object": r["object"],
            "object_changed": changed,
            "author": r["author"],
            "destroy": {"compressed_twice": r["destroy"].get("compressed_twice"),
                        "shrink": r["destroy"].get("shrink")},
            # 分规则命中数：R1 判据失效告警要跨 run 对比它（2026-10-31 起才有）
            "counts": r["analysis"].get("counts") or {},
            "refs": refs,
        })

    summary = {
        "total": len(chapters),
        "ok": sum(1 for c in chapters if c["verdict"] == V.VERDICT_OK),
        "spot": sum(1 for c in chapters if c["verdict"] == V.VERDICT_SPOT),
        "rewrite": sum(1 for c in chapters if c["verdict"] == V.VERDICT_REWRITE),
        "undecided": sum(1 for c in chapters if c["verdict"] is None),
        "defects": total_defects,
    }
    words_min = int((cal_values or {}).get("J01", 5000))
    alerts = _detector_alerts((prev_run or {}).get("chapters"), chapters, words_min)
    # 基准集（2026-11-04）：判据是"清单里有没有条目"，不是"目录在不在"——
    # 空目录曾让 verified 误报为 true。条目由作者从界面导入（utils/benchmark.py）。
    _bench_n = benchmark_count(root)
    has_bench = _bench_n > 0
    report = {
        "ok": True,
        "kind": "backtest",        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "book": book_name,
        "caliber": {
            "version": cal_doc.get("version"),
            "fingerprint": cal_doc.get("fingerprint"),
            "updated_at": cal_doc.get("updated_at"),
            "calibrated": cal_doc.get("calibrated", False),
        },
        "notices": [NOTICE_BACKTEST, NOTICE_NO_ARTIFACT],
        "summary": summary,
        "chapters": chapters,
        "matrix": [_matrix_row(r) for r in results],
        # R05 口径解释卡：每条判据的**引文**（取自正文切句，不是人编的）
        "refs": refs_all,
        "invalid_refs": invalid_refs,
        # R1 判据失效告警：只出声，不判档（详见 _detector_alerts）
        "alerts": alerts,
        "alerts_note": ("" if alerts else
                        "本轮没有“命中骤降”的章。注意：更早的历史 run 没有分规则计数，"
                        "首轮之后才开始可比。"),
        "zero_token": True,
        "object_changed": object_changed,
        "object_changed_note": (
            ("第 %s 章的稿在两次判定之间被改过 —— 上一版结论作废，以本次为准"
             % "、".join(str(x) for x in object_changed)) if object_changed else ""),
        # R18 证据分级：**能力证据**要基准集；**demo 证据**只是流程演示。
        # 两区必须分开显示，勾 1 章不得出现在能力区（AC-18-2）。
        "evidence": {
            "capability": {
                "verified": has_bench,
                "note": ("基准集已配置（%d 份）：不误报以基准集为准" % _bench_n if has_bench
                         else "不误报未验证：没有基准集（R19），能力证据区为空"),
                "items": [],
            },
            "demo": {
                "note": "流程验证，不构成能力证明",
                "items": [c["chapter"] for c in chapters
                          if (c.get("author") or {}).get("checked")]
                or "还没有勾选任何章（demo 区为空）",
            },
        },
    }
    run_rec = {
        "id": "run_%s" % datetime.now().strftime("%Y%m%d_%H%M%S"),
        "at": report["generated_at"],
        "caliber_version": report["caliber"]["version"],
        "caliber_fingerprint": report["caliber"]["fingerprint"],
        "summary": summary,
        "evidence": report["evidence"],
        "object_changed": object_changed,
        # R05 / R1：**持久化**的 run 记录也必须带这三样 ——
        # `/judge/report` 读的是 records 里的 run_rec，不是内存里的 report。
        # 第一版只往 report 里塞了 refs，结果界面永远显示“没命中”（白查半天）。
        "refs": refs_all,
        "invalid_refs": invalid_refs,
        "alerts": alerts,
        "chapters": [
            {"chapter": c["chapter"], "verdict": c["verdict"],
             "status": c["status"], "reason_code": c["reason_code"],
             "defect_count": c["defect_count"],
             "ratio": c["defect_paragraph_ratio"],
             "words": c.get("words"),
             # 分规则命中数：R1 下一轮要和这一份对比（没有它永远不可比）
             "counts": c.get("counts") or {},
             "refs": c.get("refs") or {},
             "object": c["object"]}
            for c in chapters
        ],
    }
    R.append_run(root, run_rec)
    report["run_id"] = run_rec["id"]
    return report


def export_markdown(report, root="."):
    """导出缺陷单（I1 底部“导出这份清单”）—— 显式动作，不属于回测本身。"""
    lines = ["# 判定报告 · %s" % (report.get("book") or ""), ""]
    lines.append("> %s" % report["notices"][0])
    lines.append("> %s" % report["notices"][1])
    lines.append("")
    lines.append("口径：v%s（%s） · 生成：%s"
                 % (report["caliber"]["version"], report["caliber"]["fingerprint"],
                    report["generated_at"]))
    # R11 数据合规（评审意见低风险但必须有）：这份清单含**正文原句**。
    # 作者把它发给编辑/朋友，等于外发了一部分稿件 —— 必须在报告正文里说清，
    # 而不是藏在设置页的某段说明里（评审原话：导出时在报告头保留一句）。
    lines.append("")
    lines.append("> ⚠️ 这份清单**含正文原句**（每处缺陷都引了原文）。转给他人前请确认："
                 "那不是只发了一份报告，而是外发了一部分稿件。")
    s = report["summary"]
    lines.append("")
    lines.append("%d 章：%d 可以开始逐字改 · %d 要定点重写 · %d 要重写本章；全书 %d 处要改"
                 % (s["total"], s["ok"], s["spot"], s["rewrite"], s["defects"]))
    lines.append("")
    for a in (report.get("alerts") or []):
        lines.append("> 🔔 %s" % a.get("note"))
    if report.get("alerts"):
        lines.append("")
    for c in report["chapters"]:
        lines.append("## 第 %d 章 · %s" % (c["chapter"], c["status"] or "未判"))
        if c["reasons"]:
            for r in c["reasons"]:
                lines.append("- %s" % r.get("text"))
        for d in c["top_defects"]:
            lines.append("- 第 %d 段“%s”（%s）" % (d["paragraph_index"], d["sentence"],
                                                 d["rule_name"]))
        lines.append("")
    refs = report.get("refs") or {}
    if refs:
        lines.append("")
        lines.append("## 判据引文（每条判据在本书里的一个真实例子）")
        lines.append("")
        lines.append("| 判据 | 命中原句 | 位置 |")
        lines.append("|---|---|---|")
        for rid in sorted(refs):
            r = refs[rid] or {}
            lines.append("| %s %s | %s | 第 %s 章第 %s 段 |"
                         % (rid, r.get("rule_name") or "", r.get("sentence") or "",
                            r.get("chapter"), r.get("paragraph_index")))
        if report.get("invalid_refs"):
            lines.append("")
            lines.append("（另有 %d 条引文与正文对不上，已按 AC-05-2 作废）"
                         % report["invalid_refs"])
    out = Path(root) / "output" / "judge_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
