# -*- coding: utf-8 -*-
"""阶段 3：逐章大纲。

读整体大纲 + 设定集 → 分批（batch_outline 章/批）生成逐章大纲
→ 校验每章含 核心事件/涉及角色/功能 → progress stage3 done。
"""
import argparse
import re
from pathlib import Path

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

REQUIRED_FIELDS = ["核心事件", "涉及角色", "功能"]


def check_chapter_outline(path):
    """校验单章大纲字段。返回 (ok, errors)。"""
    text = read_text(path)
    missing = [f for f in REQUIRED_FIELDS if f not in text]
    return (not missing, missing)


def build_batch_task(cfg, proj, chapters, global_outline_path, setting_path):
    listing = "\n".join(f"- 第{n}章 → data/outline/chapters/{n:02d}.md" for n in chapters)
    _, body = load_template("stage3_chapter_outline.md", {
        "first": chapters[0],
        "last": chapters[-1],
        "listing": listing,
        "path_global_outline": Path(global_outline_path).resolve(),
        "path_setting": Path(setting_path).resolve(),
        "path_project": Path("config/project.yaml").resolve(),
    })
    return body


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or make_client(cfg, "outliner")
    task_dir = task_dir or "data/state/tasks"
    total = int(proj.get("book", {}).get("chapters", 10))
    batch = int(cfg.get("chapter", {}).get("batch_outline", 10))
    out_dir = Path("data/outline/chapters")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 断点：已存在的大纲文件跳过
    pending = [n for n in range(1, total + 1) if not (out_dir / f"{n:02d}.md").exists()]
    if not pending:
        progress.mark_stage_done(3)
        print("[stage3] 全部章节大纲已存在，跳过")
        return True, "stage3 跳过（已存在）"

    batches = [pending[i:i + batch] for i in range(0, len(pending), batch)]
    for bi, chapters in enumerate(batches, 1):
        task = client.write_task(task_dir, f"stage3_batch{bi}.md",
                                 build_batch_task(cfg, proj, chapters,
                                                  "data/outline/global.md",
                                                  "data/setting/setting.json"))
        result = client.run_task(task)
        if cost and run_id:
            cost.charge_cost(run_id, 3, chapters[0], result)
        if result["exit_code"] != 0:
            progress.set_stage(3, "failed", error=f"批 {bi} 子会话失败")
            return False, f"stage3 批 {bi} 失败"
        for n in chapters:
            p = out_dir / f"{n:02d}.md"
            if not p.exists():
                progress.set_stage(3, "failed", error=f"第{n}章大纲未生成")
                return False, f"stage3 第{n}章大纲未生成"
            ok, missing = check_chapter_outline(p)
            if not ok:
                progress.set_stage(3, "failed", error=f"第{n}章大纲缺字段: {missing}")
                return False, f"stage3 第{n}章大纲缺字段: {missing}"
        print(f"[stage3] 批 {bi}/{len(batches)} 完成（章节 {chapters[0]}-{chapters[-1]}）")

    progress.mark_stage_done(3)
    print(f"[stage3] 逐章大纲完成（{total} 章）")
    return True, f"stage3 完成（{total} 章）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段3：逐章大纲")
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
