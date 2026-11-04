#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问卷校验器（方寸问卷系统的第一版判据，零 token、断网可跑）。

为什么要有它
------------
2026-11-04 那批问卷的三类事故都能被机器拦住：
  1. 出处指向不存在的文件 / 引文与原文对不上  → 本轮 8 条出处 5 红 3 黄
  2. 问的是已经落地或已经拍板的事            → 预算 / 判定引擎 / J10 / 四态 / 快照
  3. 选项、验收线不成形                      → 无代价、无判据、非互斥

它只做**确定性检查**，不判断题目好坏。题目好坏仍归作者。

用法
----
    python scripts/questionnaire_lint.py docs/questionnaires/*.md
    python scripts/questionnaire_lint.py docs/questionnaires/*.md --json
    python scripts/questionnaire_lint.py docs/questionnaires/*.md --no-git   # 跳过 HEAD 比对

退出码：0 = 无错误（警告允许）；1 = 有错误；2 = 用法/解析失败。

题目写法（写在同一个 .md 里，人看正文，机器读围栏块）
------------------------------------------------------
    ```yaml question
    id: mofang.p0.scope
    question: 一期范围
    baseline: {repo: NovelForge, commit: "982f149", generated_at: "2026-11-04T10:20+08:00"}
    current:
      state: partial            # done | partial | pending | defaulted | unknown
      summary: 预算与判定引擎已落地；拆锁只拆了 writing；回测未跑
      evidence:
        - prompts/stage5_check.md:4
        - data/judge/caliber.json#J10
    citations:
      - path: docs/产品定义基线-20261028.md
        line: 153
        quote: "### 3.3 P0 二十项功能"
    options:
      - key: backtest_only
        label: 只跑回测
        cost: {token: 0, days: 0.5}
        reversible: true
    requires: []
    conflicts: []
    acceptance:
      check: judge.py run 后 records.json 覆盖 10 章
      artifact: data/judge/records.json
    reconfirm: false
    ```

决策日志写法（decision-log.md）

    ```yaml decision
    key: mofang.judge.defect_ratio
    date: 2026-10-29
    value: 0.20
    kind: decision        # decision | landed
    scope: 全项目
    source: 作者拍板
    ```
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("[lint] 需要 PyYAML：用项目 .venv 的 python 跑（.venv/Scripts/python.exe）", file=sys.stderr)
    raise SystemExit(2)

STATES = {"done", "partial", "pending", "defaulted", "unknown"}
REQUIRED_TOP = ("id", "question", "baseline", "current", "options", "acceptance")
FENCE = re.compile(r"```yaml\s+(question|decision)\s*\n(.*?)```", re.S)
DONE_HINTS = ("confirm", "keep", "维持", "确认")


# ---------------------------------------------------------------- 解析

def parse_blocks(text: str, tag: str) -> list[dict]:
    out = []
    for m in FENCE.finditer(text):
        if m.group(1) != tag:
            continue
        try:
            doc = yaml.safe_load(m.group(2))
        except yaml.YAMLError as e:
            out.append({"__parse_error__": str(e)})
            continue
        if isinstance(doc, dict):
            out.append(doc)
        else:
            out.append({"__parse_error__": "围栏块不是 YAML 映射"})
    return out


def split_ref(ref: str) -> tuple[str, int | None, str | None]:
    """'a/b.md:12#J10' -> ('a/b.md', 12, 'J10')"""
    frag = None
    if "#" in ref:
        ref, frag = ref.split("#", 1)
    line = None
    m = re.match(r"^(.*?):(\d+)$", ref.strip())
    if m:
        ref, line = m.group(1), int(m.group(2))
    return ref.strip(), line, (frag.strip() if frag else None)


class Checker:
    def __init__(self, root: Path, use_git: bool = True):
        self.root = root
        self.errors: list[tuple[str, str]] = []   # (qid, msg)
        self.warns: list[tuple[str, str]] = []
        self.infos: list[tuple[str, str]] = []
        self.head = self._head() if use_git else None
        self.cited_with_line: set[str] = set()

    def _head(self) -> str | None:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(self.root),
                stderr=subprocess.DEVNULL, text=True).strip()
        except Exception:
            return None

    def err(self, qid: str, msg: str) -> None:
        self.errors.append((qid, msg))

    def warn(self, qid: str, msg: str) -> None:
        self.warns.append((qid, msg))

    # ------------------------------------------------------------ 单点检查

    def check_file_ref(self, qid: str, ref: str, label: str) -> None:
        ref = str(ref).strip()
        # 前缀 "!" = 断言"这东西**不存在**"（用于"回测没跑过 / 样本为空"这类现状）
        neg = ref.startswith("!")
        if neg:
            ref = ref[1:].strip()
        path, line, frag = split_ref(ref)
        p = (self.root / path) if not Path(path).is_absolute() else Path(path)
        if neg:
            if not p.exists():
                return
            if p.is_dir() and not any(p.iterdir()):
                return
            self.err(qid, f"{label} 断言不存在，但实际在：{ref}")
            return
        if not p.exists():
            self.err(qid, f"{label} 指向不存在的文件：{ref}")
            return
        if line is not None:
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as e:
                self.err(qid, f"{label} 读不出：{ref}（{e}）")
                return
            if line < 1 or line > len(lines):
                self.err(qid, f"{label} 行号越界：{ref}（文件共 {len(lines)} 行）")
                return
            self.cited_with_line.add(path)
            if frag and frag not in lines[line - 1]:
                self.err(qid, f"{label} 第 {line} 行没有该内容：{frag}")
            return
        if frag:
            text = p.read_text(encoding="utf-8", errors="replace")
            if frag not in text:
                self.err(qid, f"{label} 文件里找不到：{frag}（{ref}）")

    def check_citation(self, qid: str, c: dict) -> None:
        if not isinstance(c, dict) or "path" not in c:
            self.err(qid, "citations 条目缺 path")
            return
        path = str(c["path"])
        p = (self.root / path) if not Path(path).is_absolute() else Path(path)
        if not p.exists():
            self.err(qid, f"出处指向不存在的文件：{path}")
            return
        quote = (c.get("quote") or "").strip()
        line = c.get("line")
        text = p.read_text(encoding="utf-8", errors="replace")
        if line is not None:
            try:
                line = int(line)
            except (TypeError, ValueError):
                self.err(qid, f"出处 line 不是整数：{c}")
                return
            lines = text.splitlines()
            if line < 1 or line > len(lines):
                self.err(qid, f"出处行号越界：{path}:{line}（共 {len(lines)} 行）")
                return
            if quote and quote not in lines[line - 1]:
                self.err(qid, f"引文与原文对不上：{path}:{line} 没有“{quote}”")
        elif quote and quote not in text:
            self.err(qid, f"引文在文件里找不到：{path} 没有“{quote}”")

    def check_cited_drift(self) -> None:
        """带行号的引用若指向**有未提交改动**的文件，行号随时会漂。

        2026-11-04 实测：给 nf_api.py 加了一个 GET 分支（+3 行），
        裁决层 M9 的 `scripts/nf_api.py:3743` 立刻指到别处 —— 同一 commit 内的
        改动不会触发"基线已变"警告，所以必须单独查一次工作区。
        """
        if not self.head or not self.cited_with_line:
            return
        try:
            out = subprocess.check_output(
                ["git", "status", "--porcelain", "--", *sorted(self.cited_with_line)],
                cwd=str(self.root), stderr=subprocess.DEVNULL, text=True)
        except Exception:
            return
        dirty = [ln[3:].strip() for ln in out.splitlines() if ln.strip()]
        for f in dirty:
            self.warn("(baseline)", f"引文文件有未提交改动：{f} —— 行号可能已漂移，改完代码重跑 lint")

    # ------------------------------------------------------------ 单题检查

    def check_question(self, q: dict, idx: int, known_ids: set[str]) -> str:
        if "__parse_error__" in q:
            self.err(f"#{idx}", f"YAML 解析失败：{q['__parse_error__']}")
            return f"#{idx}"
        qid = str(q.get("id") or f"#{idx}")
        for k in REQUIRED_TOP:
            if not q.get(k):
                self.err(qid, f"缺必填字段：{k}")

        cur = q.get("current") or {}
        state = str(cur.get("state") or "").strip()
        if state not in STATES:
            self.err(qid, f"current.state 非法：{state!r}（可选 {sorted(STATES)}）")
        if not str(cur.get("summary") or "").strip():
            self.err(qid, "current.summary 为空（现状必须用人话写清）")
        ev = cur.get("evidence") or []
        if not ev:
            self.err(qid, "current.evidence 为空（没有证据的现状等于回忆）")
        for ref in ev:
            self.check_file_ref(qid, str(ref), "evidence")

        cits = q.get("citations") or []
        if not cits and not q.get("open_ended"):
            self.err(qid, "没有 citations（出处必须可核；开放式题请显式写 open_ended: true）")
        for c in cits:
            self.check_citation(qid, c)

        opts = q.get("options") or []
        if len(opts) < 2 and not q.get("open_ended"):
            self.err(qid, f"选项少于 2 个（现在 {len(opts)} 个）")
        keys = set()
        for o in opts:
            if not isinstance(o, dict):
                self.err(qid, "options 条目不是映射")
                continue
            k = str(o.get("key") or "")
            if not k:
                self.err(qid, "选项缺 key")
                continue
            if k in keys:
                self.err(qid, f"选项 key 重复：{k}")
            keys.add(k)
            if not str(o.get("label") or "").strip():
                self.err(qid, f"选项 {k} 缺 label")
            cost = o.get("cost")
            if not isinstance(cost, dict) or "token" not in cost or "days" not in cost:
                self.err(qid, f"选项 {k} 缺 cost（token / days）")
            if not isinstance(o.get("reversible"), bool):
                self.err(qid, f"选项 {k} 缺 reversible（true/false）")

        acc = q.get("acceptance") or {}
        if not str(acc.get("check") or "").strip():
            self.err(qid, "acceptance.check 为空（验收线必须可判定）")

        for rid in (q.get("requires") or []) + (q.get("conflicts") or []):
            if str(rid) not in known_ids:
                self.err(qid, f"依赖里的 id 不存在：{rid}")

        base = q.get("baseline") or {}
        commit = str(base.get("commit") or "")
        if self.head and commit and not self.head.startswith(commit[:7]):
            self.warn(qid, f"基线已变：卷面 commit={commit}，当前 HEAD={self.head} —— 现状与证据需重算")

        if state in ("done", "defaulted") and not q.get("reconfirm"):
            if not any(any(h in k for h in DONE_HINTS) for k in keys):
                self.warn(qid, "现状已是 done/defaulted，但选项里没有“确认/维持”项 —— 这题可能是补签")

        if str(q.get("id")) in self.decided and not q.get("reconfirm"):
            d = self.decided[str(q.get("id"))]
            self.warn(qid, f"已答过（decision-log {d.get('date')} · {d.get('kind')}）：{str(d.get('value'))[:40]}")
        return qid


# ---------------------------------------------------------------- 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="问卷校验器：出处/现状/选项/验收线")
    ap.add_argument("files", nargs="+", help="问卷 markdown 文件")
    ap.add_argument("--root", default=None, help="仓库根（默认本脚本上一级）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    ap.add_argument("--no-git", action="store_true", help="跳过 HEAD 比对")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    ck = Checker(root, use_git=not args.no_git)

    # 先把全部问卷的 id 与 decision-log 收齐，才能校验 requires/conflicts
    docs: list[tuple[Path, list[dict]]] = []
    known_ids: set[str] = set()
    for f in args.files:
        p = Path(f)
        if not p.exists():
            print(f"[lint] 文件不存在：{f}", file=sys.stderr)
            return 2
        qs = parse_blocks(p.read_text(encoding="utf-8"), "question")
        docs.append((p, qs))
        for q in qs:
            if isinstance(q, dict) and q.get("id"):
                known_ids.add(str(q["id"]))

    ck.decided = {}
    log = root / "docs" / "questionnaires" / "decision-log.md"
    if log.exists():
        for d in parse_blocks(log.read_text(encoding="utf-8"), "decision"):
            if isinstance(d, dict) and d.get("key") and not d.get("draft"):
                ck.decided[str(d["key"])] = d

    report = []
    for p, qs in docs:
        if not qs:
            ck.err(p.name, "没有找到任何 ```yaml question 块")
            continue
        before_e = len(ck.errors)
        for i, q in enumerate(qs, 1):
            ck.check_question(q, i, known_ids)
        report.append({
            "file": str(p),
            "questions": len(qs),
            "errors": len(ck.errors) - before_e,
        })
    ck.check_cited_drift()

    if args.json:
        print(json.dumps({
            "ok": not ck.errors,
            "files": report,
            "errors": [{"qid": q, "msg": m} for q, m in ck.errors],
            "warnings": [{"qid": q, "msg": m} for q, m in ck.warns],
            "head": ck.head,
        }, ensure_ascii=False, indent=2))
    else:
        total_q = sum(r["questions"] for r in report)
        print(f"[lint] 基线 HEAD={ck.head or '-'} · {len(report)} 份 · {total_q} 题 · "
              f"{len(ck.errors)} 错误 · {len(ck.warns)} 警告")
        for q, m in ck.errors:
            print(f"  E {q:<32} {m}")
        for q, m in ck.warns:
            print(f"  W {q:<32} {m}")
        for r in report:
            print(f"  · {Path(r['file']).name}: {r['questions']} 题, {r['errors']} 错误")
        print("[lint] 通过" if not ck.errors else "[lint] 未通过：先修 E 项再发卷")

    return 0 if not ck.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
