#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问卷收卷器：把卷面上的勾选与附言抽成结构化答案，并可落进 decision-log。

定位
----
问卷链路的第二步（出卷 → **答** → 落盘 → 下游读到）。只做确定性解析，零模型调用：

- 勾选：`- [X] **B. …**` → 第 2 个选项的 key（字母序 = options 顺序，出卷时已约定）
- 附言：选项列表之后、yaml 围栏之前的非列表文字，原话照录
- 未勾选但有附言：`value: 自由作答`（这种题必须人工把附言归成一句可执行的 value）

用法
----
    python scripts/questionnaire_collect.py docs/questionnaires/*-r2.md
    python scripts/questionnaire_collect.py docs/questionnaires/*-r2.md --json
    python scripts/questionnaire_collect.py docs/questionnaires/*-r2.md --apply
    python scripts/questionnaire_collect.py --list          # 读 decision-log 的当前决定

`--apply` 只追加，不覆盖；同 key 同 value 同日期视为已收过，跳过。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("[collect] 需要 PyYAML：用项目 .venv 的 python 跑", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
DECISION_LOG = ROOT / "docs" / "questionnaires" / "decision-log.md"
FENCE = re.compile(r"```yaml\s+question\s*\n(.*?)```", re.S)
CHECKED = re.compile(r"^\s*-\s*\[[xX]\]\s*\*\*([A-Za-z])[.、]?\s*(.*?)\*\*", re.M)
OPTION_LINE = re.compile(r"^\s*-\s*\[[ xX]\]")
SKIP_NOTE = ("```", "|", ">", "##", "---", "#")


def parse_file(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    out: list[dict] = []
    prev_end = 0
    for m in FENCE.finditer(text):
        human = text[prev_end:m.start()]
        prev_end = m.end()
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            print(f"[collect] {path.name}: YAML 解析失败 {e}", file=sys.stderr)
            continue
        if not isinstance(meta, dict):
            continue

        # 选项字母 → key（出卷约定：A/B/C/D 与 options 顺序一一对应）
        opts = meta.get("options") or []
        picked = None
        letters = []
        for cm in CHECKED.finditer(human):
            letter = cm.group(1).upper()
            idx = ord(letter) - ord("A")
            letters.append(letter)
            if 0 <= idx < len(opts):
                picked = opts[idx]

        # 附言：最后一条选项之后的非列表文字
        lines = human.splitlines()
        last_opt = -1
        for i, ln in enumerate(lines):
            if OPTION_LINE.match(ln):
                last_opt = i
        note = ""
        if last_opt >= 0:
            buf = []
            for ln in lines[last_opt + 1:]:
                s = ln.strip()
                if not s:
                    continue
                if s.startswith(SKIP_NOTE):
                    continue
                buf.append(s)
            note = "\n".join(buf).strip()

        out.append({
            "file": path.name,
            "id": str(meta.get("id") or ""),
            "question": str(meta.get("question") or ""),
            "letters": letters,
            "option_key": (picked or {}).get("key") if picked else None,
            "option_label": (picked or {}).get("label") if picked else None,
            "note": note,
            "status": ("checked" if picked else ("free" if note else "unanswered")),
        })
    return out


def existing(log: Path) -> list[dict]:
    if not log.exists():
        return []
    text = log.read_text(encoding="utf-8")
    rows = []
    for m in re.finditer(r"```yaml\s+decision\s*\n(.*?)```", text, re.S):
        try:
            d = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(d, dict) and not d.get("draft"):
            rows.append(d)
    return rows


def entry(ans: dict, today: str) -> str:
    value = ans["option_key"] or "自由作答"
    source = "卷面勾选" if ans["option_key"] else "卷面附言"
    note = (ans["note"] or "").replace("\r", "")
    body = [
        "```yaml decision",
        f"key: {ans['id']}",
        f"date: {today}",
        f"value: {value}",
        "kind: decision",
        "scope: 墨坊",
        f"source: {source}（{ans['file']}）",
        'supersedes: ""',
    ]
    if note:
        body.append("note: |-")
        for ln in note.splitlines():
            body.append("  " + ln)
    body.append("```")
    return "\n".join(body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="问卷收卷器（勾选 + 附言 → decision-log）")
    ap.add_argument("files", nargs="*", help="问卷 markdown")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true", help="追加进 decision-log")
    ap.add_argument("--list", action="store_true", help="只列 decision-log 当前决定")
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args(argv)

    if args.list:
        rows = existing(DECISION_LOG)
        print(f"[collect] decision-log：{len(rows)} 条")
        for d in rows:
            print(f"  {d.get('key'):<38} {str(d.get('date')):<12} {str(d.get('value'))[:44]}")
        return 0

    if not args.files:
        ap.print_help()
        return 2

    answers = []
    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"[collect] 文件不存在：{f}", file=sys.stderr)
            return 2
        answers.extend(parse_file(p))

    checked = [a for a in answers if a["status"] == "checked"]
    free = [a for a in answers if a["status"] == "free"]
    none = [a for a in answers if a["status"] == "unanswered"]

    if args.json:
        print(json.dumps({"answers": answers, "checked": len(checked),
                          "free": len(free), "unanswered": len(none)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[collect] {len(answers)} 题：勾选 {len(checked)} · 只有附言 {len(free)} · 未答 {len(none)}")
        for a in answers:
            if a["status"] == "checked":
                print(f"  ✓ {a['id']:<36} → {a['option_key']}")
            elif a["status"] == "free":
                print(f"  ✎ {a['id']:<36} → 自由作答（需人工归一）")
            else:
                print(f"  ? {a['id']:<36} → 未答")

    if args.apply:
        have = existing(DECISION_LOG)
        seen = {(str(d.get("key")), str(d.get("value")), str(d.get("date"))) for d in have}
        today = args.date
        added, skipped = 0, 0
        block = []
        for a in answers:
            if a["status"] == "unanswered":
                continue
            key = (a["id"], a["option_key"] or "自由作答", today)
            if key in seen:
                skipped += 1
                continue
            block.append(entry(a, today))
            added += 1
        if block:
            with DECISION_LOG.open("a", encoding="utf-8") as fh:
                fh.write("\n---\n\n## 收卷 " + today + "（自动追加）\n\n")
                fh.write("\n\n".join(block) + "\n")
        print(f"[collect] 追加 {added} 条，跳过 {skipped} 条 → {DECISION_LOG.relative_to(ROOT)}")
        if free:
            print(f"[collect] 注意：{len(free)} 条是自由作答，value 暂记“自由作答”，需人工归一："
                  + "、".join(a["id"] for a in free))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
