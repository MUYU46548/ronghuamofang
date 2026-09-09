# -*- coding: utf-8 -*-
"""章节级精修 CLI（P0.7 #4）。

复用大纲精修模式（refine_outline.py）到章节层：用户对单章提意见 →
备份当前章（data/chapters/history/chNN_vM.md）→ 子会话增量修订 →
校验（存在/字数±20%/quality 注释）。不改变任何 stage 状态。

用法：
  python scripts/refine_chapter.py 3 "露汐决战前夜加一段内心独白，保持白描风格"
  python scripts/refine_chapter.py 3 "..." --dry-run   # 只备份+生成任务，不跑子会话
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

import yaml

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

HISTORY_DIR = Path("data/chapters/history")
QUALITY_RE = re.compile(r"<!--\s*quality:\s*(\d+(?:\.\d+)?)")


def _pick_chapter_path(n):
    """按 refined > checked > raw 优先级定位章节文件。"""
    for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
        p = Path(d) / f"{n:02d}.md"
        if p.exists():
            return p
    return None


def _next_version(chapter):
    """该章已有备份数 + 1。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in HISTORY_DIR.glob(f"ch{chapter:02d}_v*.md")
            if (m := re.match(rf"ch{chapter:02d}_v(\d+)\.md", p.name))]
    return (max(nums) + 1) if nums else 1


def _count_cn(text):
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def build_task(cfg, proj, chapter, feedback, version, chap_path, outline_path):
    """生成精修任务文件内容（自包含：嵌入当前章全文 + 意见）。"""
    current = read_text(chap_path)
    meta, body = load_template("stage4_refine.md", {
        "chapter": chapter,
        "version": version,
        "feedback": feedback,
        "path_outline": Path(outline_path).resolve(),
        "path_setting": Path("data/setting/setting.json").resolve(),
        "path_chapter": Path(chap_path).resolve(),
    })
    # current_chapter 最后替换，防 {{...}} 误伤
    body = body.replace("{{current_chapter}}", current)
    return body


def run_refine(cfg, proj, chapter, feedback, client=None, task_dir=None,
               dry_run=False, outline_path="data/outline/chapters"):
    client = client or make_client(cfg, "default")
    chap_path = _pick_chapter_path(chapter)
    if chap_path is None:
        return False, f"第 {chapter} 章不存在（raw/checked/refined 均无）"

    version = _next_version(chapter)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    backup = HISTORY_DIR / f"ch{chapter:02d}_v{version}.md"
    shutil.copy2(chap_path, backup)
    print(f"[refine_ch] 已备份 → {backup}（v{version}）")

    outline = Path(outline_path) / f"{chapter:02d}.md"
    if not outline.exists():
        outline = Path("data/outline/global.md")  # 回退整体大纲
    task = client.write_task(task_dir, f"stage4_refine_ch{chapter:02d}.md",
                             build_task(cfg, proj, chapter, feedback, version,
                                        chap_path, outline))
    print(f"[refine_ch] 任务文件: {task}")
    if dry_run:
        print("[refine_ch] --dry-run：未调用子会话")
        return True, "dry-run（仅备份 + 任务文件）"

    result = client.run_task(task)
    if result["exit_code"] != 0:
        return False, "精修子会话失败"
    if not chap_path.exists():
        return False, "精修后章节文件缺失"

    # 校验：字数 ±20% + quality 注释
    before = _count_cn(read_text(backup))
    after = _count_cn(read_text(chap_path))
    delta = abs(after - before) / before if before else 0
    q = QUALITY_RE.search(read_text(chap_path))
    if delta > 0.2:
        return False, (f"字数变化 {delta*100:.0f}% 超 20% 铁律"
                       f"（{before}→{after}），疑似重写，已保留备份 {backup}")
    print(f"[refine_ch] 第 {chapter} 章修订完成：{before}→{after} 字 "
          f"({delta*100:+.1f}%)；quality: {q.group(1) if q else '缺失'}")
    if q is None:
        print("[refine_ch] 警告：修订后缺 <!-- quality --> 注释")
    return True, f"第 {chapter} 章精修完成（v{version}）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 章节级精修")
    parser.add_argument("chapter", type=int, help="章节号")
    parser.add_argument("feedback", nargs="?", default=None, help="修订意见")
    parser.add_argument("--feedback", dest="fb2", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()

    feedback = args.fb2 or args.feedback
    if not feedback:
        print('用法: python scripts/refine_chapter.py 3 "修订意见" [--dry-run]')
        return 1
    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    ok, msg = run_refine(cfg, proj, args.chapter, feedback,
                         task_dir=args.task_dir, dry_run=args.dry_run)
    print(f"[refine_ch] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
