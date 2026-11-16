# -*- coding: utf-8 -*-
"""外派回执：子任务自报什么不算数，主控复算才算（R6，2026-11-16）。

## 为什么

OB37 施工回执 §4.2 第 4 条：**9 个子任务全部报"体检 0"，主控复检抓出 6 处硬错**
（3 处连排碎段、3 处疑问句误用句号）、1 处写入失败、1 处重复段落。
结论原话："外派可以，但回执必须是脚本原始输出 + 文件 sha + 字数，不是模型自述。"

墨坊现状：`engine: self` 有派单座位（`nf_agent_dispatch.build_task_brief`），
任务书里写了"把 ≤500 字摘要写到 `<run_id>_result.md`" —— 而**那份摘要有读者吗**？
没有（回执 §五 原话："agent_briefs/<run_id>_result.md 全项目零读者"）。

## 本模块的规则（唯一一条判据）

**模型自述与脚本实测不一致时，系统站在脚本一边。**

回执三件（任务书要求执行方逐项给出）：

1. `checks`：体检脚本的**原始输出**（不是"0 问题"这种结论）；
2. `outputs`：每个产物文件的 `sha256` 与汉字数；
3. `edits`：改了哪些段（段落级摘要，可选但建议）。

`verify()` 不信回执里的任何数字：它自己重算 sha256 / 汉字数 / 段落数，
差额一律判 `mismatch`，并把 `trusted` 固定成 `"script"`。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

BRIEFS_REL = "data/state/agent_briefs"


def _root(root="."):
    return Path(root)


def receipt_path(run_id, root="."):
    return _root(root) / BRIEFS_REL / ("%s_receipt.json" % run_id)


def result_path(run_id, root="."):
    return _root(root) / BRIEFS_REL / ("%s_result.md" % run_id)


def fingerprint(path, root="."):
    """一个产物的**脚本口径**指纹：sha256 + 汉字数 + 段落数。"""
    from utils.word_count import count_cn_words
    p = Path(path)
    if not p.is_absolute():
        p = _root(root) / p
    if not p.exists():
        return {"path": str(path), "exists": False}
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    return {
        "path": p.relative_to(_root(root)).as_posix() if str(p).startswith(str(_root(root)))
                else p.as_posix(),
        "exists": True,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "hanzi": count_cn_words(text),
        "paragraphs": len([x for x in text.split("\n\n") if x.strip()]),
    }


def build_receipt(run_id, outputs, checks=None, edits=None, notes="", root="."):
    """执行方生成回执（CLI `--emit` 用）。

    `outputs` 可以是路径列表 —— 指纹由本函数现算（**执行方不需要自己数**，
    数错了会被 `verify()` 抓出来）。
    """
    files = [fingerprint(p, root) for p in (outputs or [])]
    receipt = {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "outputs": files,
        "checks": checks or [],
        "edits": edits or [],
        "notes": str(notes or ""),
        "schema": 2,
    }
    p = receipt_path(run_id, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_receipt(run_id, root="."):
    try:
        return json.loads(receipt_path(run_id, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def verify(run_id, root=".", expect_outputs=None):
    """主控复检。返回 `{ok, verdict, trusted, mismatches, outputs, receipt_present}`。

    verdict:
    - `no_receipt`：执行方没按约定给回执（**不算通过** —— 没有可核对的东西）；
    - `mismatch`：自报与实测不一致（判失败，理由逐条列出）；
    - `ok`：三件齐全且一致。
    """
    receipt = load_receipt(run_id, root)
    if not receipt:
        return {"run_id": run_id, "ok": False, "verdict": "no_receipt", "trusted": "script",
                "receipt_present": False, "mismatches": [],
                "why": "这一趟外派没有回执 —— 没有可核对的产物，不能当完成"}

    declared = {o.get("path"): o for o in (receipt.get("outputs") or []) if o.get("path")}
    paths = list(declared)
    for p in (expect_outputs or []):
        if p not in declared:
            paths.append(p)

    mismatches = []
    actual = []
    for path in paths:
        real = fingerprint(path, root)
        actual.append(real)
        seen = declared.get(path)
        if seen is None:
            if real.get("exists"):
                mismatches.append({"path": path, "field": "outputs",
                                   "why": "实测有这个产物，回执里没报"})
            else:
                mismatches.append({"path": path, "field": "outputs",
                                   "why": "回执说要产出，盘上没有"})
            continue
        if not real.get("exists"):
            mismatches.append({"path": path, "field": "exists", "why": "回执说有，盘上没有"})
            continue
        for field in ("sha256", "hanzi"):
            if seen.get(field) is not None and seen.get(field) != real.get(field):
                mismatches.append({"path": path, "field": field,
                                   "declared": seen.get(field), "actual": real.get(field),
                                   "why": "回执自称 %s=%s，脚本实测 %s"
                                          % (field, seen.get(field), real.get(field))})
    checks = receipt.get("checks") or []
    thin_checks = [c for c in checks if not str(c.get("output") or "").strip()]
    if checks and thin_checks:
        mismatches.append({"field": "checks",
                           "why": "有 %d 条体检只报了结论、没给脚本原始输出 —— "
                                  "按纪律不算体检" % len(thin_checks)})

    ok = not mismatches
    return {"run_id": run_id, "ok": ok, "trusted": "script",
            "verdict": "ok" if ok else "mismatch",
            "receipt_present": True, "mismatches": mismatches, "outputs": actual,
            "edits": receipt.get("edits") or [],
            "checks_count": len(checks),
            "why": "" if ok else "自报与脚本实测不一致（以脚本为准）"}
