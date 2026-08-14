# -*- coding: utf-8 -*-
"""设定集补全 CLI（P1.5-2）。

stage2 审批门之前的补全通道：用户对 setting.json 的薄弱项（体检 THIN/WARN）
提出定向补全意见 → 备份当前设定（data/setting/history/setting_vN.json）→
子会话按"仅从素材推断 + llm_inferred 标记"增量补全 → 结构校验 →
体检复评 → 差异摘要。补全不改变审批状态。

用法：
  python scripts/setting_refine.py "补充露汐与罗霄的关系往事"
  python scripts/setting_refine.py --feedback "..." --dry-run   # 只备份+生成任务
  python scripts/setting_refine.py --auto-thin                   # 对全部 THIN 条目批量补全
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from utils.api_client import HermesClient
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

import material_review as mr
import stage1_consolidate as s1

SETTING = "data/setting/setting.json"
HISTORY_DIR = "data/setting/history"
NORMALIZED = "data/setting/normalized"


def next_version(history_dir):
    """返回下一个版本号（history/setting_v{N}.json 中最大 N + 1）。"""
    hist = Path(history_dir)
    hist.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in hist.glob("setting_v*.json"):
        m = re.match(r"setting_v(\d+)\.json", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def backup_current(setting_path, history_dir, version):
    """复制当前设定集到 history 目录。返回备份路径。"""
    hist = Path(history_dir)
    hist.mkdir(parents=True, exist_ok=True)
    dest = hist / f"setting_v{version}.json"
    shutil.copy2(setting_path, dest)
    return dest


def build_task(cfg, proj, feedback, version, review_thin):
    """生成补全任务文件内容（自包含：嵌入当前设定集全文 + 体检 THIN 清单）。"""
    current = read_text(SETTING)
    meta, body = load_template("stage1_refine.md", {
        "version": version,
        "feedback": feedback,
        "review_thin": review_thin,
        "path_setting": Path(SETTING).resolve(),
        "path_normalized": Path(NORMALIZED).resolve(),
    })
    # current_setting 最后替换，避免设定内容中的 {{...}} 被后续替换误伤
    body = body.replace("{{current_setting}}", current)
    return body


def diff_summary(before, after):
    """对比补全前后 setting.json，输出人类可读差异摘要。"""
    lines = []
    b_chars = {c.get("name"): c for c in before.get("characters", []) if isinstance(c, dict)}
    a_chars = {c.get("name"): c for c in after.get("characters", []) if isinstance(c, dict)}
    for name, ac in a_chars.items():
        if name not in b_chars:
            lines.append(f"+ 新增角色条目: {name}")
            continue
        bc = b_chars[name]
        inferred = []
        for key in ac:
            if key == "llm_inferred":
                continue
            if isinstance(ac.get(key), bool) and key not in bc:
                continue
            if key not in bc:
                inferred.append(key)
            elif ac[key] != bc[key]:
                inferred.append(key + "（修改）")
        if inferred:
            lines.append(f"~ {name}: 补强字段 {('、'.join(inferred))}")
    # 全局限定
    for key in ("world", "plot_fragments", "timeline"):
        if key in after and key in before and after[key] != before[key]:
            lines.append(f"~ {key}: 内容有变化")
    return lines


def run_refine(cfg, proj, feedback, client=None, task_dir=None, dry_run=False):
    if not Path(SETTING).exists():
        return False, f"设定集不存在: {SETTING}（先跑 stage1）"

    version = next_version(HISTORY_DIR)
    backup = backup_current(SETTING, HISTORY_DIR, version)
    print(f"[setting_refine] 已备份当前设定 → {backup}（v{version}）")

    # 体检复评（补全前基线）
    try:
        char_results, world_items, thin_names, warn_names = mr.review(SETTING, NORMALIZED)
        review_thin = "、".join(thin_names) if thin_names else "（无 THIN，见用户意见）"
    except Exception as e:
        review_thin = f"（体检失败: {e}）"

    client = client or HermesClient(model=(cfg or {}).get("model", {}).get("default"))
    task_dir = task_dir or "data/state/tasks"
    task = client.write_task(task_dir, "stage1_refine.md",
                             build_task(cfg, proj, feedback, version, review_thin))
    print(f"[setting_refine] 任务文件: {task}")

    if dry_run:
        print("[setting_refine] --dry-run：未调用子会话，补全未执行")
        return True, "dry-run（仅备份 + 任务文件）"

    result = client.run_task(task)
    if result["exit_code"] != 0:
        return False, "补全子会话失败"

    ok, errors = s1.validate_setting(SETTING)
    if not ok:
        return False, "补全后设定集校验失败: " + "; ".join(errors)

    # 差异摘要 + 体检复评
    before = json.loads(read_text(str(backup)))
    after = json.loads(read_text(SETTING))
    diffs = diff_summary(before, after)
    print("[setting_refine] 差异摘要：")
    for d in diffs:
        print(f"  {d}")

    try:
        char_results, world_items, thin_names, warn_names = mr.review(SETTING, NORMALIZED)
        mr.print_summary(char_results, world_items, thin_names, warn_names)
    except Exception as e:
        print(f"[setting_refine] 复评失败（不影响补全结果）: {e}")

    print("[setting_refine] 补全完成。注意：审批状态未变，"
          "满意后请执行 approve.py --stage 2 确认")
    return True, f"补全完成（v{version}）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 设定集补全（增量，不重跑 stage1）")
    parser.add_argument("feedback", nargs="?", default=None,
                        help="补全意见（自然语言）")
    parser.add_argument("--feedback", dest="feedback_opt", default=None,
                        help="补全意见（与位置参数二选一）")
    parser.add_argument("--auto-thin", action="store_true",
                        help="对体检 THIN 条目批量补全（无需意见）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只备份 + 生成任务文件，不调用子会话")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()

    feedback = args.feedback_opt or args.feedback
    if not feedback and not args.auto_thin:
        print("用法: python scripts/setting_refine.py \"补全意见\" [--dry-run] 或 --auto-thin")
        return 1

    if args.auto_thin:
        # 从体检报告取 THIN 清单作为自动意见
        feedback = "请对以下碎片角色进行设定补全（仅从素材推断，llm_inferred 标记）：（见体检报告）"

    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    ok, msg = run_refine(cfg, proj, feedback, task_dir=args.task_dir,
                         dry_run=args.dry_run)
    print(f"[setting_refine] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
