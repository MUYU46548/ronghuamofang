#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D1 探针：段落身份能不能跨阶段传递（零模型、纯确定性）。

背景
----
11-01 产品基线把主指标定为 BROE（单点干预半径），目标从"章"降到"段"；
同时声明 **D1（段身份能否跨八阶段传递）未确认**。2026-11-04 作者拍板：
先做探针，再决定按段做还是退回章级。

探针要回答的三件事
------------------
1. 现在有没有段身份？——没有，`paragraph_index` 是**当前文本的数组下标**，
   插一段/删一段，后面全部错位。
2. 用"内容指纹"当身份行不行？——本脚本给出量化：改稿后有多少段能被认出来。
3. 认不出来的怎么算？——精确哈希只认"一字未改"；近似匹配（编辑距离）能分出
   "改过"与"新插入"，但阈值必须显式。

用法
----
    python scripts/d1_probe.py 旧稿.md 新稿.md
    python scripts/d1_probe.py 旧稿.md 新稿.md --json
    python scripts/d1_probe.py --chapter 3          # raw/03.md vs checked/03.md

判定口径（写死在代码里，不许各模块自己解释）
--------------------------------------------
- `matched`：规范化后哈希相同 → 同一段的强证据。
- `edited`：哈希不同，但与某个未匹配旧段相似度 ≥ 阈值（默认 0.60）→ 同一段被改过。
- `inserted` / `removed`：既不同、也不像 → 新增 / 删除。
- `carry_rate` = (matched + edited) / 旧段数：**这就是 D1 的可行性指标**。
  低于 0.5 说明"按段接管"在真稿上不成立，BROE 一期应退回章级。

注意：本脚本只读数、不写任何产物；它不调模型。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THRESHOLD = 0.60


def split_paragraphs(text: str) -> list[str]:
    """按空行切段，去掉首尾空白与空段。标题行单独成段。"""
    parts = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    return [p.strip() for p in parts if p.strip()]


def normalize(p: str) -> str:
    """只做无害归一：全角空格→半角、压缩空白。不动标点与字词。"""
    p = p.replace("\u3000", " ")
    p = re.sub(r"[ \t]+", " ", p)
    return p.strip()


def fingerprint(p: str) -> str:
    return hashlib.sha256(normalize(p).encode("utf-8")).hexdigest()[:16]


def compare(old: list[str], new: list[str], threshold: float = THRESHOLD) -> dict:
    """返回匹配报告（不依赖顺序，按内容认段）。"""
    old_fp = [fingerprint(p) for p in old]
    new_fp = [fingerprint(p) for p in new]

    used_new: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for i, fp in enumerate(old_fp):
        for j, fp2 in enumerate(new_fp):
            if j in used_new:
                continue
            if fp == fp2:
                pairs.append((i, j))
                used_new.add(j)
                break

    matched_old = {i for i, _ in pairs}
    edited = []
    for j, np_ in enumerate(new):
        if j in used_new:
            continue
        best, best_ratio = None, 0.0
        for i, op in enumerate(old):
            if i in matched_old:
                continue
            r = SequenceMatcher(None, normalize(op), normalize(np_)).ratio()
            if r > best_ratio:
                best, best_ratio = i, r
        if best is not None and best_ratio >= threshold:
            edited.append((best, j, round(best_ratio, 3)))
            matched_old.add(best)
            used_new.add(j)

    inserted = [j for j in range(len(new)) if j not in used_new]
    removed = [i for i in range(len(old)) if i not in matched_old]
    carried = len(pairs) + len(edited)
    return {
        "old_count": len(old),
        "new_count": len(new),
        "matched": len(pairs),
        "edited": len(edited),
        "inserted": len(inserted),
        "removed": len(removed),
        "carry_rate": round(carried / len(old), 3) if old else 0.0,
        "edited_pairs": edited,
        "inserted_index": inserted,
        "removed_index": removed,
        "threshold": threshold,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D1 探针：段身份跨版本可识别率")
    ap.add_argument("old", nargs="?", help="旧稿 markdown")
    ap.add_argument("new", nargs="?", help="新稿 markdown")
    ap.add_argument("--chapter", type=int, help="用 data/chapters/raw/NN.md vs checked/NN.md")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.chapter is not None:
        old_p = ROOT / "data" / "chapters" / "raw" / ("%02d.md" % args.chapter)
        new_p = ROOT / "data" / "chapters" / "checked" / ("%02d.md" % args.chapter)
    else:
        if not (args.old and args.new):
            ap.print_help()
            return 2
        old_p, new_p = Path(args.old), Path(args.new)

    for p in (old_p, new_p):
        if not p.exists():
            print(f"[d1] 文件不存在：{p}", file=sys.stderr)
            return 2

    old = split_paragraphs(old_p.read_text(encoding="utf-8"))
    new = split_paragraphs(new_p.read_text(encoding="utf-8"))
    rep = compare(old, new, args.threshold)
    rep["old_file"] = str(old_p)
    rep["new_file"] = str(new_p)

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"[d1] {old_p.name} → {new_p.name}")
        print(f"  旧段 {rep['old_count']} · 新段 {rep['new_count']}")
        print(f"  原样保留 {rep['matched']} · 改过 {rep['edited']} · 新增 {rep['inserted']} · 删除 {rep['removed']}")
        print(f"  段身份可识别率 carry_rate = {rep['carry_rate']}（阈值 {rep['threshold']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
