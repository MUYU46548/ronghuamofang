# -*- coding: utf-8 -*-
"""阶段 3：逐章大纲。

读整体大纲 + 设定集 → 分批（batch_outline 章/批）生成逐章大纲
→ 校验每章含 核心事件/涉及角色/功能 → progress stage3 done。
"""
import argparse
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template
from utils.config_io import load_config_yaml
from utils import volume_arc as va

REQUIRED_FIELDS = ["核心事件", "涉及角色", "功能"]


def check_chapter_outline(path):
    """校验单章大纲字段。返回 (ok, errors)。"""
    text = read_text(path)
    missing = [f for f in REQUIRED_FIELDS if f not in text]
    return (not missing, missing)


def outline_ok(path):
    """断点判据：大纲存在**且通过字段校验**（核心事件/涉及角色/功能）。

    ⚠️ 只判 `exists()` 不够：`reject.py --stage 3` 现在会清空本目录，但若目录里
    残留了半成品/占位大纲，会被无条件信任并喂给 stage4（写作阶段照着烂大纲写）。
    直接复用 `check_chapter_outline` —— 判据只有一份。
    """
    p = Path(path)
    if not p.exists():
        return False
    try:
        ok, _errs = check_chapter_outline(p)
        return bool(ok)
    except Exception:                                         # noqa: BLE001
        return False


def written_chapters(raw_dir="data/chapters/raw"):
    """已写到第几章（raw 目录里最大章号）—— 滚动规划的"当前位置"。"""
    p = Path(raw_dir)
    if not p.exists():
        return 0
    nos = []
    for f in p.glob("*.md"):
        try:
            nos.append(int(f.stem))
        except (TypeError, ValueError):
            continue
    return max(nos) if nos else 0


def build_batch_task(cfg, proj, chapters, global_outline_path, setting_path):
    listing = "\n".join(f"- 第{n}章 → data/outline/chapters/{n:02d}.md" for n in chapters)
    total = int((proj.get("book") or {}).get("chapters", 0) or max(chapters))
    # 卷弧（2026-10-20）：本批**首章所属卷**的卷纲。一批不跨卷（见 generate_chapters）。
    volume_context = va.render_context(chapters[0], total, cfg, proj)
    _, body = load_template("stage3_chapter_outline.md", {
        "first": chapters[0],
        "last": chapters[-1],
        "listing": listing,
        "path_global_outline": Path(global_outline_path).resolve(),
        "path_setting": Path(setting_path).resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "volume_context": volume_context,
    })
    return body


def generate_chapters(cfg, proj, chapters, client, task_dir="data/state/tasks",
                      cost=None, run_id=None):
    """生成指定章节的逐章大纲。返回 (ok, msg)。

    **为什么要抽成函数**：滚动规划下 stage3 只铺到"当前卷 + 下一卷"，
    stage4 写到窗口外时必须能**按需补齐单章大纲**（否则第 41 章会因缺大纲直接失败）。
    两条路径共用这一份实现 —— 判据写两遍必然漂移。
    """
    total = int((proj.get("book") or {}).get("chapters", 10))
    batch = int(cfg.get("chapter", {}).get("batch_outline", 10))
    out_dir = Path("data/outline/chapters")
    out_dir.mkdir(parents=True, exist_ok=True)
    # A3：与 stage2 同源 —— 只读 canon 快照（无快照则退回活稿）
    from utils.material_state import canon_or_setting

    # 批不跨卷：卷纲是按卷给的，跨卷的批会让后半批拿到错的卷弧
    size = va.volume_size(cfg, proj)
    groups, cur = [], []
    for n in chapters:
        if cur and va.volume_of(n, size) != va.volume_of(cur[0], size):
            groups.append(cur)
            cur = []
        cur.append(n)
    if cur:
        groups.append(cur)

    for bi, chs in enumerate(groups, 1):
        task = client.write_task(task_dir, f"stage3_batch{bi}.md",
                                 build_batch_task(cfg, proj, chs,
                                                  "data/outline/global.md",
                                                  canon_or_setting()))
        result = client.run_task(task)
        if cost and run_id:
            cost.charge_cost(run_id, 3, chs[0], result)
        if result["exit_code"] != 0:
            return False, f"stage3 批 {bi} 子会话失败（章节 {chs[0]}-{chs[-1]}）"
        for n in chs:
            p = out_dir / f"{n:02d}.md"
            if not p.exists():
                return False, f"stage3 第{n}章大纲未生成"
            ok, missing = check_chapter_outline(p)
            if not ok:
                return False, f"stage3 第{n}章大纲缺字段: {missing}"
        print(f"[stage3] 批 {bi}/{len(groups)} 完成（章节 {chs[0]}-{chs[-1]}）")
    return True, "生成完成（%d 章）" % len(chapters)


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or make_client(cfg, "outliner")
    task_dir = task_dir or "data/state/tasks"
    total = int(proj.get("book", {}).get("chapters", 10))
    out_dir = Path("data/outline/chapters")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 滚动规划（2026-10-20，台账三）：只铺到"当前卷 + 后续 rolling_volumes-1 卷"。
    # rolling_volumes=0 时 limit=total，行为与改动前逐字一致（老配置不受影响）。
    size = va.volume_size(cfg, proj)
    roll = va.rolling_volumes(cfg)
    written = written_chapters()
    limit = va.horizon(total, written, size, roll)
    if limit < total:
        print("[stage3] 滚动规划：本轮只生成到第 %d 章（全书 %d 章）；"
              "更远的章大纲等写到再定（避免越远越空泛）" % (limit, total))

    # 断点：已存在且**合格**的大纲文件跳过
    pending = [n for n in range(1, limit + 1) if not outline_ok(out_dir / f"{n:02d}.md")]
    if not pending:
        if limit < total:
            progress.set_stage(3, "done",
                               warning="滚动规划：只覆盖到第 %d 章（全书 %d 章）" % (limit, total))
            print("[stage3] 窗口内章节大纲已存在且合格，跳过")
            return True, "stage3 跳过（窗口内已存在且合格）"
        progress.mark_stage_done(3)
        print("[stage3] 全部章节大纲已存在且合格，跳过")
        return True, "stage3 跳过（已存在且合格）"

    ok, msg = generate_chapters(cfg, proj, pending, client, task_dir, cost, run_id)
    if not ok:
        progress.set_stage(3, "failed", error=msg)
        return False, msg

    if limit < total:
        progress.set_stage(3, "done",
                           warning="滚动规划：只覆盖到第 %d 章（全书 %d 章）" % (limit, total))
        print(f"[stage3] 逐章大纲完成（本轮 {len(pending)} 章，窗口上限第 {limit} 章）")
        return True, f"stage3 完成（窗口内 {len(pending)} 章，全书 {total} 章）"

    progress.mark_stage_done(3)
    print(f"[stage3] 逐章大纲完成（{total} 章）")
    return True, f"stage3 完成（{total} 章）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段3：逐章大纲")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()
    cfg = load_config_yaml("config/system.yaml")
    proj = load_config_yaml("config/project.yaml")
    from utils.progress_manager import ProgressManager
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(cfg, proj, progress, None, None, task_dir=args.task_dir)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
