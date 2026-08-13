# -*- coding: utf-8 -*-
"""阶段 5：逻辑检查（P0 基础版，单路）。

P0：单子会话全量检查（角色一致性/时间线/伏笔/世界观合并一路），
修正问题句输出到 chapters/checked/ + 问题报告。
P1 升级：四路并行（角色/时间线/伏笔/世界观）分批 delegate + 归并（v2 4.5）。
"""
import argparse
from pathlib import Path

from utils.api_client import HermesClient
from utils.file_io import read_text, write_text
from utils.template_loader import load_template
from utils.verify_chapter import check_chapter


def build_check_task(proj, raw_dir, setting_path, checked_dir):
    _, body = load_template("stage5_check.md", {
        "path_raw": Path(raw_dir).resolve(),
        "path_setting": Path(setting_path).resolve(),
        "path_report": Path("data/outline/check_report.md").resolve(),
        "path_checked": Path(checked_dir).resolve(),
    })
    return body


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or HermesClient(model=(cfg or {}).get("model", {}).get("checker"))
    task_dir = task_dir or "data/state/tasks"
    raw_dir = Path("data/chapters/raw")
    checked_dir = Path("data/chapters/checked")
    checked_dir.mkdir(parents=True, exist_ok=True)

    raw_files = sorted(raw_dir.glob("*.md"))
    if not raw_files:
        progress.set_stage(5, "failed", error="无 raw 章节")
        return False, "stage5 失败：无 raw 章节"
    # 断点：checked 中已存在的章节跳过
    pending = [f for f in raw_files if not (checked_dir / f.name).exists()]
    if not pending:
        progress.mark_stage_done(5)
        return True, "stage5 跳过（checked 已存在）"

    task = client.write_task(task_dir, "stage5_check.md",
                             build_check_task(proj, raw_dir, "data/setting/setting.json", checked_dir))
    result = client.run_task(task)
    if cost and run_id:
        cost.record(run_id, 5, 0, result["tokens"], result["tokens_out"],
                    estimated=result.get("estimated", False))
    if result["exit_code"] != 0:
        progress.set_stage(5, "failed", error="子会话退出码非零")
        return False, "stage5 子会话失败"

    missing = [f.name for f in pending if not (checked_dir / f.name).exists()]
    if missing:
        progress.set_stage(5, "failed", error=f"缺修正文件: {missing[:3]}")
        return False, f"stage5 缺修正文件: {missing[:3]}"

    if db and run_id:
        for f in pending:
            db.log_chapter(run_id, 5, int(f.stem), "ok")
    progress.mark_stage_done(5)
    print(f"[stage5] 逻辑检查完成（{len(raw_files)} 章），报告: data/outline/check_report.md")
    return True, "stage5 完成"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段5：逻辑检查（P0 基础版）")
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
