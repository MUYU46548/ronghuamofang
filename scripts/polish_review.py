# -*- coding: utf-8 -*-
"""润色后体检（P0.7 #7，确定性，不调 LLM）。

stage6 完成后运行：汇总 refined 章节的字数、quality 自评分、与 checked
的字数变化，输出 data/outline/polish_review.md + 终端摘要。
用于交付 Word 前快速确认润色质量，无需逐章人工审。

用法：
  python scripts/polish_review.py [--report data/outline/polish_review.md]
"""
import argparse
import re
import sys
from pathlib import Path

from utils.file_io import read_text, write_text

QUALITY_RE = re.compile(r"<!--\s*quality:\s*(\d+(?:\.\d+)?)")
CN_RE = re.compile(r"[\u4e00-\u9fff]")
THRESHOLD = 6  # 低质量阈值（与 system.yaml quality_threshold 一致）


def count_cn(text):
    return len(CN_RE.findall(text))


def review(refined_dir="data/chapters/refined",
           checked_dir="data/chapters/checked"):
    refined = Path(refined_dir)
    checked = Path(checked_dir)
    rows = []
    if not refined.exists():
        return {"rows": [], "summary": {"total": 0, "verdict": "NO_REFINED"}}
    for p in sorted(refined.glob("*.md")):
        try:
            n = int(p.stem)
        except ValueError:
            continue
        rt = read_text(p)
        rq = QUALITY_RE.search(rt)
        r_words = count_cn(rt)
        c_path = checked / p.name
        c_words = count_cn(read_text(c_path)) if c_path.exists() else None
        delta = (r_words - c_words) / c_words if c_words else None
        rows.append({
            "n": n, "words": r_words, "quality": float(rq.group(1)) if rq else None,
            "checked_words": c_words, "delta": delta,
        })
    return {"rows": rows, "summary": _summarize(rows)}


def _summarize(rows):
    if not rows:
        return {"total": 0, "verdict": "EMPTY"}
    words = [r["words"] for r in rows]
    quals = [r["quality"] for r in rows if r["quality"] is not None]
    deltas = [r["delta"] for r in rows if r["delta"] is not None]
    low = [r["n"] for r in rows if r["quality"] is not None and r["quality"] < THRESHOLD]
    over = [r["n"] for r in rows if r["delta"] is not None and abs(r["delta"]) > 0.2]
    issues = []
    if low:
        issues.append(f"低质量章(<{THRESHOLD}): {low}")
    if over:
        issues.append(f"字数变化超20%章: {over}")
    missing_q = [r["n"] for r in rows if r["quality"] is None]
    if missing_q:
        issues.append(f"缺 quality 注释章: {missing_q}")
    verdict = "WARN" if issues else "PASS"
    return {
        "total": len(rows),
        "avg_words": sum(words) / len(words),
        "min_words": min(words), "max_words": max(words),
        "avg_quality": sum(quals) / len(quals) if quals else None,
        "avg_delta": sum(deltas) / len(deltas) if deltas else None,
        "low": low, "over": over, "missing_quality": missing_q,
        "issues": issues, "verdict": verdict,
    }


def render_markdown(result):
    s = result["summary"]
    lines = [
        "# 润色后体检报告",
        "",
        f"- 章节数: {s['total']}",
        f"- 平均字数: {s['avg_words']:.0f}（min {s['min_words']} / max {s['max_words']}）"
        if s["total"] else "- 无章节",
        f"- 平均 quality: {s['avg_quality']:.1f}" if s["avg_quality"] is not None else "- quality: 无",
        f"- 平均字数变化(vs checked): {s['avg_delta']*100:+.1f}%"
        if s["avg_delta"] is not None else "",
        "",
        "| 章 | 字数 | quality | vs checked |",
        "|----|------|---------|-----------|",
    ]
    for r in result["rows"]:
        d = f"{r['delta']*100:+.1f}%" if r["delta"] is not None else "-"
        q = f"{r['quality']:.1f}" if r["quality"] is not None else "-"
        lines.append(f"| {r['n']} | {r['words']} | {q} | {d} |")
    lines += ["", "## 检查"]
    if s.get("issues"):
        lines += [f"- ⚠ {i}" for i in s["issues"]]
    else:
        lines.append("- 无异常")
    lines += ["", f"## 判定: **{s['verdict']}**",
              "", "- WARN 时建议先用 refine_chapter.py 精修对应章再转 Word"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 润色后体检（确定性）")
    parser.add_argument("--report", default="data/outline/polish_review.md")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    result = review()
    s = result["summary"]
    if s["verdict"] in ("NO_REFINED", "EMPTY"):
        print("[polish_review] refined/ 无章节（先跑 stage6）")
        return 1
    if not args.quiet:
        write_text(args.report, render_markdown(result))
        print(f"[polish_review] 报告: {args.report}")
    print(f"[polish_review] {s['total']} 章 | 平均 {s['avg_words']:.0f} 字 | "
          f"quality {s['avg_quality']:.1f} | 判定 {s['verdict']}")
    for i in s.get("issues", []):
        print(f"  [!] {i}")
    return 0 if s["verdict"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
