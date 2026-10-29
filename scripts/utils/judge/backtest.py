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
    }


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

    results = [judge_chapter(root, n, cal_doc, cal_values, recs) for n in nums]
    chapters = []
    total_defects = 0
    for r in results:
        v = r["verdict"]
        defects = r["analysis"]["defects"]
        total_defects += len(defects) + (1 if r["analysis"]["outline"]["density"].get("flagged") else 0)
        top = [
            {"paragraph_index": d["paragraph_index"], "sentence": T.snippet(d["sentence"], 20),
             "rule_id": d["rule_id"], "rule_name": d["rule_name"], "count": d["count"]}
            for d in defects if int(d.get("paragraph_index") or 0) > 0
        ][:3]
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
            "author": r["author"],
            "destroy": {"compressed_twice": r["destroy"].get("compressed_twice"),
                        "shrink": r["destroy"].get("shrink")},
        })

    summary = {
        "total": len(chapters),
        "ok": sum(1 for c in chapters if c["verdict"] == V.VERDICT_OK),
        "spot": sum(1 for c in chapters if c["verdict"] == V.VERDICT_SPOT),
        "rewrite": sum(1 for c in chapters if c["verdict"] == V.VERDICT_REWRITE),
        "undecided": sum(1 for c in chapters if c["verdict"] is None),
        "defects": total_defects,
    }
    report = {
        "ok": True,
        "kind": "backtest",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
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
        "zero_token": True,
        "evidence": {
            "capability": [],
            "demo": [],
        },
    }
    run_rec = {
        "id": "run_%s" % datetime.now().strftime("%Y%m%d_%H%M%S"),
        "at": report["generated_at"],
        "caliber_version": report["caliber"]["version"],
        "caliber_fingerprint": report["caliber"]["fingerprint"],
        "summary": summary,
        "chapters": [
            {"chapter": c["chapter"], "verdict": c["verdict"],
             "status": c["status"], "reason_code": c["reason_code"],
             "defect_count": c["defect_count"],
             "ratio": c["defect_paragraph_ratio"],
             "object": c["object"]}
            for c in chapters
        ],
    }
    R.append_run(root, run_rec)
    report["run_id"] = run_rec["id"]
    return report


def export_markdown(report, root="."):
    """导出缺陷单（I1 底部「导出这份清单」）—— 显式动作，不属于回测本身。"""
    lines = ["# 判定报告 · %s" % (report.get("book") or ""), ""]
    lines.append("> %s" % report["notices"][0])
    lines.append("> %s" % report["notices"][1])
    lines.append("")
    lines.append("口径：v%s（%s） · 生成：%s"
                 % (report["caliber"]["version"], report["caliber"]["fingerprint"],
                    report["generated_at"]))
    s = report["summary"]
    lines.append("")
    lines.append("%d 章：%d 可以开始逐字改 · %d 要定点重写 · %d 要重写本章；全书 %d 处要改"
                 % (s["total"], s["ok"], s["spot"], s["rewrite"], s["defects"]))
    lines.append("")
    for c in report["chapters"]:
        lines.append("## 第 %d 章 · %s" % (c["chapter"], c["status"] or "未判"))
        if c["reasons"]:
            for r in c["reasons"]:
                lines.append("- %s" % r.get("text"))
        for d in c["top_defects"]:
            lines.append("- 第 %d 段「%s」（%s）" % (d["paragraph_index"], d["sentence"],
                                                 d["rule_name"]))
        lines.append("")
    out = Path(root) / "output" / "judge_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
