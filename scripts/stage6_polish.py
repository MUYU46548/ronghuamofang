# -*- coding: utf-8 -*-
"""阶段 6：基础润色（P0 基础版，分卷并行留 P1）。

仅改表达不改情节；校验润色后字数变化 < 20%（防止重写）。
输出 chapters/refined/。
"""
import argparse
from pathlib import Path

from utils.api_client import HermesClient
from utils.file_io import read_text, write_text
from utils.verify_chapter import count_cn_words
from utils.template_loader import load_template


def build_polish_task(proj, checked_dir, refined_dir, chapters):
    checked = Path(checked_dir).resolve()
    listing = "\n".join(f"- 第{n}章: {checked / f'{n:02d}.md'}" for n in chapters)
    _, body = load_template("stage6_polish.md", {
        "listing": listing,
        "path_refined": Path(refined_dir).resolve(),
        "path_report": Path("data/outline/polish_report.md").resolve(),
    })
    return body


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or HermesClient()
    task_dir = task_dir or "data/state/tasks"
    checked_dir = Path("data/chapters/checked")
    refined_dir = Path("data/chapters/refined")
    refined_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(checked_dir.glob("*.md"))
    if not files:
        progress.set_stage(6, "failed", error="无 checked 章节")
        return False, "stage6 失败：无 checked 章节"
    pending = [f for f in files if not (refined_dir / f.name).exists()]
    if not pending:
        progress.mark_stage_done(6)
        return True, "stage6 跳过（refined 已存在）"

    chapters = [int(f.stem) for f in pending]
    task = client.write_task(task_dir, "stage6_polish.md",
                             build_polish_task(proj, checked_dir, refined_dir, chapters))
    result = client.run_task(task)
    if result["exit_code"] != 0:
        progress.set_stage(6, "failed", error="子会话退出码非零")
        return False, "stage6 子会话失败"

    for f in pending:
        out = refined_dir / f.name
        if not out.exists():
            progress.set_stage(6, "failed", error=f"缺润色文件: {f.name}")
            return False, f"stage6 缺润色文件: {f.name}"
        before = count_cn_words(read_text(f))
        after = count_cn_words(read_text(out))
        if before and abs(after - before) / before > 0.2:
            progress.set_stage(6, "failed", error=f"第{f.stem}章字数变化超20%")
            return False, f"stage6 第{f.stem}章字数变化超20%（疑似重写）"

    if db and run_id:
        for f in pending:
            db.log_chapter(run_id, 6, int(f.stem), "ok")
    progress.mark_stage_done(6)
    print(f"[stage6] 基础润色完成（{len(pending)} 章）")
    return True, "stage6 完成"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段6：基础润色（P0 基础版）")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()
    import yaml
    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    from utils.progress_manager import ProgressManager
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(cfg, proj, progress, None, None, task_dir=args.task_dir)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
