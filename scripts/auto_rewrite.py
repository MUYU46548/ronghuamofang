# -*- coding: utf-8 -*-
"""方向3：质量自评闭环 —— quality < 阈值的章节自动轻量重写。

设计原则：**薄封装**。复用 batch_refine.run_single_refine()，不重复实现
「备份 history/chNN_vM.md → 建 stage4_refine 任务 → 跑模型 → ±20% 铁律校验」。
本模块只负责：收集目标章 → 生成修订意见 → 逐章调度 → 记账 → 出报告。

与既有 n==4 钩子的关系：在审稿分支（review_after_stage4）**之前**运行，
使审查看到的已是重写后的章节。开关 gates.auto_rewrite 默认 false，
未获用户确认不自动改稿。

幂等：progress.stages["4"]["auto_rewritten"] = {章号(str): 已用轮次}；
        轮次用尽后不再自动重试，保留 needs_rewrite 标记转人工。

用法：
  python scripts/auto_rewrite.py                        # 按配置阈值，默认轮次 1
  python scripts/auto_rewrite.py --threshold 6 --chapters 3,7
  python scripts/auto_rewrite.py --dry-run              # 只建任务，不改章节内容
  python scripts/auto_rewrite.py --max-rounds 1
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from utils.file_io import read_text, write_text
from utils.verify_chapter import count_cn_words
from utils.llm_client import make_client

import batch_refine as br

# 章节文件质量自评注释（与 batch_refine 的宽松解析一致，不强制 /10）
QUALITY_RE = re.compile(r"<!--\s*quality:\s*(\d+(?:\.\d+)?)")

REVIEW_REPORT_PATH = Path("data/outline/review_report.json")
REPORT_MD = Path("data/outline/auto_rewrite_report.md")
REPORT_JSON = Path("data/outline/auto_rewrite_report.json")

MD_HEADER = (
    "# 自动重写报告（质量自评闭环）\n\n"
    "> 由 `scripts/auto_rewrite.py` 自动追加，每次运行一节。\n"
    "> 触发条件：章节 `<!-- quality: X/10 -->` 低于 `chapter.quality_threshold`。\n"
    "> 修订意见默认仅要求文风/细节层面的收敛与补强，不改情节与结构（±20% 铁律）。\n"
)

DEFAULT_FEEDBACK_TMPL = (
    "本章 quality 自评 {q}/10，低于阈值 {thr}。请在**不改变情节与结构**的前提下重写："
    "① 收敛冗余形容与比喻连缀；② 补充具体动作与感官细节；③ 视角统一为客观叙事、"
    "删除解释性插入；④ 与大纲及设定 locked 条目保持一致；⑤ 保留文末 "
    "`<!-- summary: ... -->` 与 `<!-- quality: X/10 -->` 注释（quality 按重写后实际自评）。"
    "字数必须落在原字数 ±20% 内。"
)


# ---------------------------------------------------------------- 目标收集

def parse_quality(chapter_path):
    """读取章节文件的质量自评；无注释或文件缺失返回 None。"""
    if chapter_path is None:
        return None
    try:
        m = QUALITY_RE.search(read_text(chapter_path))
    except (FileNotFoundError, OSError):
        return None
    return float(m.group(1)) if m else None


def chapter_quality(n):
    """返回 (章节文件路径 | None, quality | None)。定位规则同 batch_refine。"""
    path = br.pick_chapter_path(n)
    return path, parse_quality(path)


def _existing_chapters():
    """枚举所有已存在章节号（refined/checked/raw 去重）。"""
    nums = set()
    for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
        dd = Path(d)
        if not dd.exists():
            continue
        for f in dd.glob("*.md"):
            if f.stem.isdigit():
                nums.add(int(f.stem))
    return sorted(nums)


def scan_low_quality(threshold):
    """兜底路径：重扫章节文件，返回 quality < threshold 的章号。

    needs_rewrite 只在 stage4 当次运行写入，历史 run 可能没留 → 必须能自愈重扫。
    """
    return [n for n in _existing_chapters()
            if (q := chapter_quality(n)[1]) is not None and q < threshold]


def collect_targets(progress, threshold=6, only=None):
    """收集待重写章节号（升序）。

    优先取 progress.stages["4"].needs_rewrite，兜底重扫 quality 注释；
    最后按**当前文件实际 quality** 过滤（已被修好的自动剔除，保证幂等）。
    """
    st4 = progress.data.get("stages", {}).get("4", {})
    flagged = set()
    for x in st4.get("needs_rewrite", []) or []:
        if str(x).isdigit():
            flagged.add(int(x))
    scanned = set(scan_low_quality(threshold))
    candidates = flagged | scanned

    only_set = set(int(x) for x in only) if only else None
    targets = []
    for n in sorted(candidates):
        if only_set is not None and n not in only_set:
            continue
        _, q = chapter_quality(n)
        if q is None or q >= threshold:   # 无自评或已达标 → 不需要自动重写
            continue
        targets.append(n)
    return targets


def _rounds_used(progress):
    st4 = progress.data.setdefault("stages", {}).setdefault("4", {})
    return st4.setdefault("auto_rewritten", {})


# ---------------------------------------------------------------- 修订意见

def load_review_findings(chapter, report_path=REVIEW_REPORT_PATH):
    """读取审稿报告中该章的 findings（无报告/无该章返回 []）。"""
    try:
        data = json.loads(read_text(report_path))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    for c in data.get("chapters", []):
        try:
            if int(c.get("n", -1)) == int(chapter):
                return c.get("findings", []) or []
        except (TypeError, ValueError):
            continue
    return []


def build_feedback(chapter, quality, threshold, review_findings=None):
    """生成给 run_single_refine 的自然语言修订意见，可叠加确定性审查结论。"""
    q_txt = ("%g" % quality) if quality is not None else "?"
    lines = [DEFAULT_FEEDBACK_TMPL.format(q=q_txt, thr=threshold)]
    findings = (review_findings if review_findings is not None
                else load_review_findings(chapter))
    extra = []
    for f in findings:
        detail = (f.get("detail") or "").strip()
        if not detail:
            continue
        sev = f.get("severity", "info")
        act = (f.get("suggested_action") or "").strip()
        extra.append(f"- [{sev}] {detail}" + ("（建议：" + act + "）" if act else ""))
    if extra:
        lines.append("\n另需一并处理审稿报告中的以下发现（确定性检查结论）：\n"
                     + "\n".join(extra))
    return "\n".join(lines)


# ---------------------------------------------------------------- 成本记录代理

class _RecordingClient:
    """透传客户端，记录每次 run_task 的返回，供逐章成本归集。"""

    def __init__(self, client, sink):
        self._client = client
        self._sink = sink

    def write_task(self, *a, **k):
        return self._client.write_task(*a, **k)

    def run_task(self, *a, **k):
        result = self._client.run_task(*a, **k)
        self._sink.append(result)
        return result

    def __getattr__(self, name):
        return getattr(self._client, name)


# ---------------------------------------------------------------- 主流程

def _delta_pct(before, after):
    if not before:
        return 0.0
    return (after - before) / before * 100.0


def run_auto_rewrite(cfg, proj, progress, db=None, cost=None, run_id=None,
                     threshold=None, max_rounds=None, dry_run=False,
                     client=None, task_dir=None, chapters=None,
                     write_report_files=True):
    """自动重写主流程。返回 (ok, msg, stats)。失败不阻断（单章失败继续）。"""
    gates = cfg.get("gates", {}) or {}
    if threshold is None:
        threshold = int(cfg.get("chapter", {}).get("quality_threshold", 6))
    if max_rounds is None:
        max_rounds = int(gates.get("auto_rewrite_max_rounds", 1) or 1)
    task_dir = task_dir or "data/state/tasks"
    client = client or make_client(cfg, "writer")

    targets = collect_targets(progress, threshold=threshold, only=chapters)
    st4 = progress.data.setdefault("stages", {}).setdefault("4", {})
    # dry-run 只读已有轮次，不创建/不消耗（避免副作用）
    done = (st4.get("auto_rewritten", {}) or {} if dry_run
            else st4.setdefault("auto_rewritten", {}))

    stats = {"threshold": threshold, "max_rounds": max_rounds, "dry_run": dry_run,
             "items": [], "unresolved": [], "skipped": [], "aborted": None,
             "targets": targets}

    if not targets:
        print(f"[auto_rewrite] 无 quality<{threshold} 的章节，跳过")
        if write_report_files:
            write_report(stats)
        return True, "无待重写章节", stats

    print(f"[orchestrator] 检测到 {len(targets)} 章 quality<{threshold}，"
          f"自动重写中（阈值 {threshold}，轮次 {max_rounds}）…")

    for n in targets:
        used = int(done.get(str(n), 0) or 0)
        if used >= max_rounds:
            stats["skipped"].append({"chapter": n, "reason": f"已达上限轮次 {max_rounds}"})
            stats["unresolved"].append(n)
            print(f"[auto_rewrite] 第{n}章 已重写 {used} 轮，达上限，转人工")
            continue

        # 预算保护：熔断立即中止
        if cost is not None and run_id is not None:
            state, spent = cost.status(run_id)
            if state == "pause":
                stats["aborted"] = f"预算熔断（已用 {spent:.2f} 元）"
                print(f"[auto_rewrite] 预算熔断，中止自动重写（已用 {spent:.2f} 元）")
                break

        path_before, q_before = chapter_quality(n)
        if path_before is None:
            stats["items"].append({"chapter": n, "status": "missing",
                                   "quality_before": q_before, "quality_after": None,
                                   "words_before": 0, "words_after": 0, "delta_pct": 0.0,
                                   "backup": None, "msg": "章节文件缺失"})
            continue
        words_before = count_cn_words(read_text(path_before))
        feedback = build_feedback(n, q_before, threshold)

        sink = []
        proxy = _RecordingClient(client, sink)
        try:
            ok, msg, backup = br.run_single_refine(
                cfg, proj, n, feedback, client=proxy, task_dir=task_dir, dry_run=dry_run)
        except Exception as e:   # 单章异常不阻断整体
            ok, msg, backup = False, f"{type(e).__name__}: {str(e)[:150]}", None
            print(f"[auto_rewrite] 第{n}章 异常：{msg}")

        # 成本归集（复用 run_id，使 cost_report --by-chapter 可见）
        if cost is not None and run_id is not None:
            for r in sink:
                try:
                    cost.charge_cost(run_id, 4, n, r)
                except Exception as e:
                    print(f"[auto_rewrite] 成本记账失败（不影响流程）: {e}")

        # 重写后复评
        q_after, words_after = None, words_before
        if not dry_run and ok:
            path_after, q_after = chapter_quality(n)
            if path_after is not None:
                words_after = count_cn_words(read_text(path_after))

        status, note = _classify(ok, dry_run, q_after, threshold)
        row = {
            "chapter": n,
            "status": status,
            "quality_before": q_before,
            "quality_after": q_after,
            "words_before": words_before,
            "words_after": words_after,
            "delta_pct": round(_delta_pct(words_before, words_after), 2),
            "backup": Path(backup).name if backup else None,
            "msg": (note or msg),
        }
        stats["items"].append(row)

        if not dry_run:
            done[str(n)] = used + 1
            if status == "ok":
                _clear_needs_rewrite(progress, n)
            else:
                stats["unresolved"].append(n)
            progress.save()

        print(f"[auto_rewrite] 第{n}章 {status}：quality "
              f"{_fmt_q(q_before)}→{_fmt_q(q_after)}，字数 "
              f"{words_before}→{words_after}（{row['delta_pct']:+.1f}%）")

    _dedupe(stats["unresolved"])
    ok_all = all(r["status"] != "failed" for r in stats["items"])
    if write_report_files and not dry_run:
        write_report(stats)
    summary = (f"重写 {len([r for r in stats['items'] if r['status'] in ('ok', 'low')])} 章，"
               f"未通过 {len(stats['unresolved'])} 章"
               + (f"，{stats['aborted']}" if stats.get("aborted") else ""))
    return ok_all, summary, stats


def _classify(ok, dry_run, q_after, threshold):
    if not ok:
        return "failed", None
    if dry_run:
        return "dry_run", "dry-run（未改章节）"
    if q_after is None:
        return "low", "重写后 quality 缺失，转人工复核"
    if q_after >= threshold:
        return "ok", None
    return "low", f"仍低于阈值（{_fmt_q(q_after)}<{threshold}），转人工"


def _clear_needs_rewrite(progress, n):
    st4 = progress.data["stages"]["4"]
    if "needs_rewrite" in st4:
        st4["needs_rewrite"] = [x for x in st4["needs_rewrite"]
                                if str(x) != str(n) and x != n]


def _dedupe(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    seq[:] = out


def _fmt_q(q):
    return "?" if q is None else ("%g" % q)


# ---------------------------------------------------------------- 报告

_STATUS_LABEL = {
    "ok": "✅",
    "low": "⚠️ 仍低于阈值，转人工",
    "failed": "❌ 失败",
    "dry_run": "⏭ dry-run",
    "missing": "❌ 文件缺失",
}


def write_report(stats, out_md=REPORT_MD, out_json=REPORT_JSON):
    """追加人可读报告 + 写机器可读 JSON。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    ts_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    thr, mr = stats["threshold"], stats["max_rounds"]

    # ---- 人可读（追加）----
    try:
        existing = read_text(out_md)
    except (FileNotFoundError, OSError):
        existing = MD_HEADER
    if not existing.strip():
        existing = MD_HEADER

    lines = [f"\n## {ts} 自动重写（阈值 {thr}，轮次 {mr}"
             + ("，dry-run" if stats.get("dry_run") else "") + "）\n"]
    if stats["items"]:
        lines.append("| 章 | 重写前 quality | 重写后 quality | 字数变化 | 备份 | 结果 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in stats["items"]:
            lines.append(
                f"| {r['chapter']:02d} | {_fmt_q(r['quality_before'])} | "
                f"{_fmt_q(r['quality_after'])} | {r['delta_pct']:+.1f}% | "
                f"{r['backup'] or '-'} | {_STATUS_LABEL.get(r['status'], r['status'])} |")
        lines.append("")
    else:
        lines.append("（无待重写章节）\n")

    if stats.get("skipped"):
        lines.append("达上限轮次跳过：" +
                     "、".join(str(s["chapter"]) for s in stats["skipped"]) + "\n")
    if stats.get("aborted"):
        lines.append(f"⚠️ 中止：{stats['aborted']}\n")
    if stats["unresolved"]:
        lines.append("**未通过章号（建议人工精修）**：" +
                     "、".join(f"{n:02d}" for n in stats["unresolved"]))
        lines.append("→ `python scripts/batch_refine.py --report data/outline/review_report.json`\n")

    Path(out_md).parent.mkdir(parents=True, exist_ok=True)
    write_text(out_md, existing.rstrip() + "\n" + "\n".join(lines) + "\n")

    # ---- 机器可读（latest + runs 追加，保留最近 50 次）----
    payload = {"generated_at": ts_iso, "threshold": thr, "max_rounds": mr,
               "dry_run": bool(stats.get("dry_run")), "targets": stats.get("targets", []),
               "items": stats["items"], "unresolved": stats["unresolved"],
               "skipped": stats.get("skipped", []), "aborted": stats.get("aborted")}
    try:
        doc = json.loads(read_text(out_json))
        if not isinstance(doc, dict):
            doc = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        doc = {}
    runs = doc.get("runs", [])
    if not isinstance(runs, list):
        runs = []
    runs.append(payload)
    write_text(out_json, json.dumps({"latest": payload, "runs": runs[-50:]},
                                    ensure_ascii=False, indent=2))
    return str(out_md), str(out_json)


# ---------------------------------------------------------------- CLI

def main():
    parser = argparse.ArgumentParser(description="NovelForge 质量自评闭环：低分章节自动重写")
    parser.add_argument("--threshold", type=int, default=None,
                        help="quality 阈值（默认取 config chapter.quality_threshold）")
    parser.add_argument("--chapters", default=None,
                        help="限定章号，逗号分隔（如 3,7）")
    parser.add_argument("--max-rounds", type=int, default=None,
                        help="同一章最多自动重写轮次（默认取 gates.auto_rewrite_max_rounds）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只建任务文件，不调用模型、不改章节内容")
    args = parser.parse_args()

    import yaml
    from utils.progress_manager import ProgressManager
    from utils.db import RunDB
    from utils.cost_tracker import CostTracker

    cfg = yaml.safe_load(read_text("config/system.yaml")) or {}
    proj = yaml.safe_load(read_text("config/project.yaml")) or {}
    progress = ProgressManager("data/state/progress.json")

    chapters = None
    if args.chapters:
        chapters = [int(x) for x in args.chapters.split(",") if x.strip().isdigit()]

    db, cost, run_id = None, None, None
    if not args.dry_run:
        import os
        db = RunDB("logs/runs.db")
        budget = cfg.get("budget", {})
        limit = float(os.environ.get("BUDGET_LIMIT_YUAN") or budget.get("limit_yuan", 300))
        cost = CostTracker(db, limit_yuan=limit, warn_ratio=budget.get("warn_ratio", 0.7))
        run_id = db.start_run(plan_json="auto_rewrite_cli")

    try:
        ok, msg, _ = run_auto_rewrite(
            cfg, proj, progress, db=db, cost=cost, run_id=run_id,
            threshold=args.threshold, max_rounds=args.max_rounds,
            dry_run=args.dry_run, chapters=chapters)
    finally:
        if db is not None:
            db.finish_run(run_id, "done" if ok else "failed")
            db.close()

    print(f"\n[auto_rewrite] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
