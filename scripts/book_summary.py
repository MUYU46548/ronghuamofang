# -*- coding: utf-8 -*-
"""完书后生成全书摘要（P0.7 #9'）。

收集各章 refined/checked/raw 的 <!-- summary --> 注释 → 子会话聚合为
全书摘要（600-900 字：世界观/主线/角色弧光/结局），输出到 output/。
用户后期填充 ROSA 设定库时可直接粘贴。

用法：
  python scripts/book_summary.py                    # 输出 output/{书名}_全书摘要.md
  python scripts/book_summary.py --out <path> --dry-run
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

from utils.api_client import HermesClient
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

SUMMARY_RE = re.compile(r"<!--\s*summary:\s*(.+?)\s*-->", re.IGNORECASE | re.S)


def collect_chapter_summaries():
    """从 refined > checked > raw 收集各章 summary 注释。返回 [(章号, 摘要)]。"""
    seen = {}
    for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
        for p in sorted(Path(d).glob("*.md")):
            try:
                n = int(p.stem)
            except ValueError:
                continue
            if n in seen:
                continue
            m = SUMMARY_RE.search(read_text(p))
            if m:
                seen[n] = m.group(1).strip()
    return sorted(seen.items())


def build_task(proj, summaries, out_path):
    listing = "\n".join(f"- 第{n}章: {s}" for n, s in summaries)
    _, body = load_template("book_summary.md", {
        "book_name": proj.get("book", {}).get("name", "未命名"),
        "chapter_summaries": listing,
        "path_output": Path(out_path).resolve(),
    })
    return body


def run(proj, out_path, client=None, task_dir="data/state/tasks", dry_run=False):
    summaries = collect_chapter_summaries()
    if not summaries:
        return False, "未收集到任何章节摘要（章节文件缺 <!-- summary --> 注释）"
    print(f"[book_summary] 收集 {len(summaries)} 章摘要（第 {summaries[0][0]}-{summaries[-1][0]} 章）")

    client = client or HermesClient(model=(
        yaml.safe_load(read_text("config/system.yaml")).get("model", {}) or {}
    ).get("default"))
    task = client.write_task(task_dir, "book_summary.md", build_task(proj, summaries, out_path))
    print(f"[book_summary] 任务文件: {task}")
    if dry_run:
        print("[book_summary] --dry-run：未调用子会话")
        return True, "dry-run（仅任务文件）"

    result = client.run_task(task)
    if result["exit_code"] != 0:
        return False, "摘要子会话失败"
    if not Path(out_path).exists():
        return False, f"摘要未生成: {out_path}"
    print(f"[book_summary] 全书摘要 → {out_path}")
    return True, "全书摘要完成"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 全书摘要（完书后）")
    parser.add_argument("--out", default=None, help="输出路径（默认 output/{书名}_全书摘要.md）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    proj = yaml.safe_load(read_text("config/project.yaml"))
    book = proj.get("book", {})
    out = args.out or f"output/{book.get('name', '未命名')}_全书摘要.md"
    ok, msg = run(proj, out, dry_run=args.dry_run)
    print(f"[book_summary] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
