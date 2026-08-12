# -*- coding: utf-8 -*-
"""阶段 2：整体大纲。

读设定集 → 生成自包含任务文件 → 子会话输出 data/outline/global.md
→ 结构校验 → 标记 stage2 完成（approved=False，等待人工确认，v2 4.1）。
"""
import argparse
import re
from pathlib import Path

from utils.api_client import HermesClient
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

REQUIRED_SECTIONS = ["起", "承", "转", "合"]


def check_global_outline(path):
    """校验 global.md：起承转合四节 + 关键节点 + 预计章节数。返回 (ok, errors)。"""
    text = read_text(path)
    errors = []
    for sec in REQUIRED_SECTIONS:
        if not re.search(rf"^##\s*{sec}", text, re.M):
            errors.append(f"缺少章节: ## {sec}")
    if len(re.findall(r"^##\s*关键节点", text, re.M)) == 0:
        errors.append("缺少 ## 关键节点")
    m = re.search(r"^##\s*预计章节数\s*\n\s*(\d+)", text, re.M)
    if not m or int(m.group(1)) <= 0:
        errors.append("缺少有效的 ## 预计章节数")
    return (not errors, errors)


def build_task(cfg, proj):
    book = proj.get("book", {})
    _, body = load_template("stage2_global_outline.md", {
        "book_name": book.get("name", "未命名"),
        "target_words": book.get("target_words", 300000),
        "path_setting": Path("data/setting/setting.json").resolve(),
        "path_manifest": Path("data/setting/materials_manifest.json").resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "path_global_outline": Path("data/outline/global.md").resolve(),
    })
    return body


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or HermesClient()
    task_dir = task_dir or "data/state/tasks"
    task = client.write_task(task_dir, "stage2_global_outline.md", build_task(cfg, proj))

    result = client.run_task(task)
    if result["exit_code"] != 0:
        progress.set_stage(2, "failed", error="子会话退出码非零")
        return False, "stage2 子会话失败"

    ok, errors = check_global_outline("data/outline/global.md")
    if not ok:
        progress.set_stage(2, "failed", error="结构校验失败: " + "; ".join(errors))
        return False, "stage2 大纲校验失败: " + "; ".join(errors)

    progress.mark_stage_done(2)          # done 但未 approved（审批门）
    progress.set_approved(2, False)
    if run_id:
        db.log_chapter(run_id, 2, 0, "ok", quality=None)
    print("[stage2] 整体大纲完成，等待人工确认（data/state/progress.json → is_approved(2)）")
    return True, "stage2 完成（待审批）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段2：整体大纲")
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
