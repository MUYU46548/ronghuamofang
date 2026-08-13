# -*- coding: utf-8 -*-
"""大纲精修 CLI（P0.5-B）。

阶段 2 审批门前的迭代通道：用户对 global.md 提出定向意见 →
备份当前版（data/outline/history/global_vN.md）→ 子会话按意见
增量修订 → 结构校验 → 输出体检摘要。精修不改变审批状态
（approve 仍需用户显式执行）。

用法：
  python scripts/refine_outline.py "第3章侧重露汐，结尾留悬念"
  python scripts/refine_outline.py --feedback "..." --dry-run   # 只备份+生成任务，不跑子会话
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

from utils.api_client import HermesClient
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

import stage2_outline as s2
import outline_review as ov

GLOBAL = "data/outline/global.md"
HISTORY_DIR = "data/outline/history"


def next_version(history_dir):
    """返回下一个版本号（history/global_v{N}.md 中最大 N + 1）。"""
    hist = Path(history_dir)
    hist.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in hist.glob("global_v*.md"):
        m = re.match(r"global_v(\d+)\.md", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def backup_current(global_path, history_dir, version):
    """复制当前大纲到 history 目录（保留原始换行）。返回备份路径。"""
    hist = Path(history_dir)
    hist.mkdir(parents=True, exist_ok=True)
    dest = hist / f"global_v{version}.md"
    shutil.copy2(global_path, dest)
    return dest


def build_task(cfg, proj, feedback, version):
    """生成精修任务文件内容（自包含：嵌入当前大纲全文 + 用户意见）。"""
    book = proj.get("book", {})
    current = read_text(GLOBAL)
    meta, body = load_template("stage2_refine.md", {
        "version": version,
        "feedback": feedback,
        "path_setting": Path("data/setting/setting.json").resolve(),
        "path_manifest": Path("data/setting/materials_manifest.json").resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "path_global_outline": Path(GLOBAL).resolve(),
        "target_words": book.get("target_words", 300000),
    })
    # current_outline 最后替换，避免大纲内容中的 {{...}} 被后续占位符替换误伤
    body = body.replace("{{current_outline}}", current)
    return body


def run_refine(cfg, proj, feedback, client=None, task_dir=None, dry_run=False):
    if not Path(GLOBAL).exists():
        return False, f"整体大纲不存在: {GLOBAL}（先跑 stage2）"

    version = next_version(HISTORY_DIR)
    backup = backup_current(GLOBAL, HISTORY_DIR, version)
    print(f"[refine] 已备份当前大纲 → {backup}（v{version}）")

    client = client or HermesClient(model=(cfg or {}).get("model", {}).get("default"))
    task_dir = task_dir or "data/state/tasks"
    task = client.write_task(task_dir, "stage2_refine.md",
                             build_task(cfg, proj, feedback, version))
    print(f"[refine] 任务文件: {task}")

    if dry_run:
        print("[refine] --dry-run：未调用子会话，精修未执行")
        return True, "dry-run（仅备份 + 任务文件）"

    result = client.run_task(task)
    if result["exit_code"] != 0:
        return False, "精修子会话失败"

    ok, errors = s2.check_global_outline(GLOBAL)
    if not ok:
        return False, "精修后大纲校验失败: " + "; ".join(errors)

    # 精修后体检摘要，供用户判断是否满意
    try:
        review = ov.review(GLOBAL, "data/setting/setting.json")
        ov.print_summary(review)
    except Exception as e:
        print(f"[refine] 体检摘要生成失败（不影响精修结果）: {e}")

    print("[refine] 精修完成。注意：审批状态未变，"
          "满意后请执行 approve.py --stage 2 确认")
    return True, f"精修完成（v{version}）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 大纲精修（增量修订，不重跑 stage2）")
    parser.add_argument("feedback", nargs="?", default=None,
                        help="用户修订意见（自然语言）")
    parser.add_argument("--feedback", dest="feedback_opt", default=None,
                        help="修订意见（与位置参数二选一）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只备份 + 生成任务文件，不调用子会话")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()

    feedback = args.feedback_opt or args.feedback
    if not feedback:
        print("用法: python scripts/refine_outline.py \"修订意见\" [--dry-run]")
        return 1

    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    ok, msg = run_refine(cfg, proj, feedback, task_dir=args.task_dir,
                         dry_run=args.dry_run)
    print(f"[refine] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
