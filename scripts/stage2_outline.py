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
    return f"""# 阶段 2 任务：生成全书整体大纲

你是资深长篇小说编辑兼世界观架构师。请严格按以下指示执行。

## 输入文件（用 read_file 读取）
- 设定集: {Path('data/setting/setting.json').resolve()}
- 素材清单: {Path('data/setting/materials_manifest.json').resolve()}
- 项目配置: {Path('config/project.yaml').resolve()}

## 任务
阅读全部输入，为《{proj.get('book', {}).get('name', '未命名')}》生成全书整体大纲，
写入文件: {Path('data/outline/global.md').resolve()}

## 输出格式（global.md，必须严格遵循）
# 《书名》整体大纲
## 起
（故事开端：背景、主角登场、核心矛盾引入）
## 承
（发展：冲突升级、角色成长、关键转折铺陈）
## 转
（高潮：最大危机、决定性冲突）
## 合
（收束：矛盾解决、结局、余韵）
## 关键节点
- 节点1：章节区间 + 事件概述
（至少 5 个节点，覆盖全书）
## 预计章节数
（一个正整数）
## 章节规划
（简述各章节区间的主要功能：铺垫/推进/高潮/收束等）

## 要求
- 逻辑自洽，无显著漏洞；与设定集硬约束（locked 条目）不冲突
- 全书目标字数约 {proj.get('book', {}).get('target_words', 300000)} 字
- 不要写正文，只写大纲
"""


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
