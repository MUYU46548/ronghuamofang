# -*- coding: utf-8 -*-
"""章节级精修 + 版本回退 CLI（P0.7 #4 / 任务4）。

复用大纲精修模式（refine_outline.py）到章节层：用户对单章提意见 →
备份当前章（data/chapters/history/chNN_vM.md）→ 子会话增量修订 →
校验（存在/字数±20%/quality 注释）。不改变任何 stage 状态。

版本回退（跨章节 diff 的配套）：每次改稿前自动备份，随时可回退到任一历史版本。
备份按 **chNN_vM.md** 命名（NN=章号两位，M=版本号递增），
即 `ch03_v1.md / ch03_v2.md …`；恢复前会把当前稿再存一版，故回退本身也可回退。

用法：
  python scripts/refine_chapter.py 3 "露汐决战前夜加一段内心独白，保持白描风格"
  python scripts/refine_chapter.py 3 "..." --dry-run   # 只备份+生成任务，不跑子会话
  python scripts/refine_chapter.py 3 --list            # 列出该章所有历史版本
  python scripts/refine_chapter.py 3 --restore 2       # 回退到 v2
"""
import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

HISTORY_DIR = Path("data/chapters/history")
QUALITY_RE = re.compile(r"<!--\s*quality:\s*(\d+(?:\.\d+)?)")
VERSION_RE = re.compile(r"^ch(\d{2})_v(\d+)\.md$")


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


# ---------------------------------------------------------------- 版本回退

def list_versions(chapter):
    """列出某章的历史版本（新 → 旧）。

    返回 [{"version", "file", "size", "words", "mtime", "quality"}]。
    只认 chNN_vM.md，且只认属于本章的（避免读到 ch1 的 v12 误配 ch12）。
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in HISTORY_DIR.glob(f"ch{chapter:02d}_v*.md"):
        m = VERSION_RE.match(p.name)
        if not m or int(m.group(1)) != chapter:
            continue
        try:
            text = read_text(p)
            words = _count_cn(text)
        except Exception:                                    # noqa: BLE001
            text, words = "", 0
        q = QUALITY_RE.search(text)
        out.append({
            "version": int(m.group(2)),
            "file": p.name,
            "size": p.stat().st_size,
            "words": words,
            "quality": q.group(1) if q else None,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    out.sort(key=lambda x: x["version"], reverse=True)
    return out


def restore_version(chapter, version):
    """把第 chapter 章回退到历史版本 version。

    安全纪律：
    - 目标稿在覆盖前**先存一版**（回退本身可回退）；
    - 版本不存在 → 报错不动作；
    - 当前稿不存在（raw/checked/refined 皆无）→ 报错不动作。
    返回 (ok, message)。
    """
    history = HISTORY_DIR / f"ch{chapter:02d}_v{int(version)}.md"
    if not history.exists():
        avail = [v["version"] for v in list_versions(chapter)]
        return False, ("第 %d 章无 v%s 版本（现有：%s）"
                       % (chapter, version,
                          "、".join("v%d" % v for v in avail) or "无任何备份"))

    target = _pick_chapter_path(chapter)
    if target is None:
        # 当前无章节文件：恢复到最高优先级目录（refined 优先）
        for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
            if Path(d).exists():
                target = Path(d) / f"{chapter:02d}.md"
                break
    if target is None:
        return False, ("第 %d 章当前不存在（raw/checked/refined 均无），"
                       "无法确定恢复目标目录" % chapter)

    saved_note = ""
    if target.exists():
        keep = _next_version(chapter)
        keep_path = HISTORY_DIR / f"ch{chapter:02d}_v{keep}.md"
        shutil.copy2(target, keep_path)
        saved_note = "；回退前当前稿已存为 v%d" % keep

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(history, target)
    restored = _count_cn(read_text(target))
    print(f"[refine_ch] 已回退 第{chapter}章 → v{int(version)}（{target}，{restored} 字）")
    return True, ("第 %d 章已回退到 v%d（%s）%s"
                  % (chapter, int(version), target, saved_note))


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
    parser.add_argument("--list", action="store_true", help="列出该章历史版本")
    parser.add_argument("--restore", type=int, default=None, metavar="V",
                        help="回退到历史版本 V（回退前会再存一版当前稿）")
    args = parser.parse_args()

    if args.list:
        vs = list_versions(args.chapter)
        if not vs:
            print("[refine_ch] 第 %d 章暂无历史版本（data/chapters/history/）" % args.chapter)
            return 0
        print("[refine_ch] 第 %d 章历史版本（新 → 旧）：" % args.chapter)
        for v in vs:
            print("  v%-3d %s  %d 字  quality=%s  %s"
                  % (v["version"], v["mtime"], v["words"], v["quality"] or "-", v["file"]))
        return 0

    if args.restore is not None:
        ok, msg = restore_version(args.chapter, args.restore)
        print(("[OK] " if ok else "[FAIL] ") + msg)
        return 0 if ok else 1

    feedback = args.fb2 or args.feedback
    if not feedback:
        print('用法: python scripts/refine_chapter.py 3 "修订意见" [--dry-run]')
        print("      python scripts/refine_chapter.py 3 --list         # 看历史版本")
        print("      python scripts/refine_chapter.py 3 --restore 2    # 回退到 v2")
        return 1
    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    ok, msg = run_refine(cfg, proj, args.chapter, feedback,
                         task_dir=args.task_dir, dry_run=args.dry_run)
    print(f"[refine_ch] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
