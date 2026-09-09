# -*- coding: utf-8 -*-
"""阶段 6：基础润色（P0 → P1 分卷并行）。

仅改表达不改情节；校验润色后字数变化 < 20%（防止重写）。
输出 chapters/refined/。

P1 改造：尊重 config system.yaml 的 parallelism.polish（并发卷数），
将待润色章节分卷，每卷独立子会话并发执行，整体时延从 O(N) 降至 O(N/polish)。
- 断点续跑：refined 已存在的章跳过；
- 失败语义：任一卷失败 → 阶段失败（已成功的 refined 保留，可断点重跑）；
- 成本记账：每卷独立记账。
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.verify_chapter import count_cn_words
from utils.template_loader import load_template


def build_polish_task(proj, checked_dir, refined_dir, chapters, vol_index=None):
    checked = Path(checked_dir).resolve()
    listing = "\n".join(f"- 第{n}章: {checked / f'{n:02d}.md'}" for n in chapters)
    report_path = Path("data/outline/polish_report.md").resolve()
    if vol_index is not None:
        report_path = Path(f"data/outline/polish_report_vol{vol_index}.md").resolve()
    _, body = load_template("stage6_polish.md", {
        "listing": listing,
        "path_refined": Path(refined_dir).resolve(),
        "path_report": report_path,
    })
    return body


def _run_one_volume(client, task_dir, vol_index, proj, checked_dir, refined_dir, vol_chapters, run_id, cost):
    """执行单个润色卷，返回 (ok, msg, vol_chapters)。"""
    checked = Path(checked_dir).resolve()
    refined = Path(refined_dir).resolve()
    content = build_polish_task(proj, checked, refined, vol_chapters, vol_index=vol_index)
    task_path = client.write_task(task_dir, f"stage6_polish_vol{vol_index}.md", content)
    result = client.run_task(task_path)
    if cost and run_id:
        cost.charge_cost(run_id, 6, vol_chapters[0] if vol_chapters else 0, result)
    if result["exit_code"] != 0:
        return False, f"stage6 卷{vol_index} 子会话失败", vol_chapters

    missing = [f"{n:02d}.md" for n in vol_chapters
               if not (refined / f"{n:02d}.md").exists()]
    if missing:
        return False, f"stage6 卷{vol_index} 缺润色文件: {missing}", vol_chapters

    for n in vol_chapters:
        src = checked / f"{n:02d}.md"
        out = refined / f"{n:02d}.md"
        before = count_cn_words(read_text(src))
        after = count_cn_words(read_text(out))
        if before and abs(after - before) / before > 0.2:
            return False, f"stage6 第{n}章字数变化超20%（疑似重写）", vol_chapters
    return True, f"stage6 卷{vol_index} 完成（{len(vol_chapters)} 章）", vol_chapters


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or make_client(cfg, "writer")
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
    # 分卷：并发数 = parallelism.polish（默认 3）， ceil 均分
    polish = max(1, int((cfg or {}).get("parallelism", {}).get("polish", 3)))
    vols = [chapters[i::polish] for i in range(polish) if chapters[i::polish]]
    vols = [v for v in vols if v]
    print(f"[stage6] 待润色 {len(chapters)} 章，分 {len(vols)} 卷并发（polish={polish}）")

    results = []
    with ThreadPoolExecutor(max_workers=min(polish, len(vols))) as pool:
        futures = {
            pool.submit(_run_one_volume, client, task_dir, idx, proj,
                        checked_dir, refined_dir, vol, run_id, cost): idx
            for idx, vol in enumerate(vols)
        }
        for fut in as_completed(futures):
            ok, msg, _ = fut.result()
            results.append((ok, msg))
            print(f"[stage6] {msg}")

    failed = [m for ok, m in results if not ok]
    if failed:
        progress.set_stage(6, "failed", error="; ".join(failed))
        return False, f"stage6 失败：{'；'.join(failed)}"

    if db and run_id:
        for n in chapters:
            db.log_chapter(run_id, 6, n, "ok")
    progress.mark_stage_done(6)
    print(f"[stage6] 基础润色完成（{len(chapters)} 章，{len(vols)} 卷）")
    return True, "stage6 完成"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段6：基础润色（P1 分卷并行）")
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
