# -*- coding: utf-8 -*-
"""阶段 5：逻辑检查（P0 基础版，单路）。

P0：单子会话全量检查（角色一致性/时间线/伏笔/世界观合并一路），
修正问题句输出到 chapters/checked/ + 问题报告。
P1 升级：四路并行（角色/时间线/伏笔/世界观）分批 delegate + 归并（v2 4.5）。
"""
import argparse
from pathlib import Path

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.cost_tracker import pause_detail as _pause_detail
from utils.template_loader import load_template
from utils.verify_chapter import is_usable_output


def build_check_task(proj, raw_dir, setting_path, checked_dir, batch_files, batch_index, total_batches):
    _, body = load_template("stage5_check.md", {
        "batch_files": batch_files,
        "path_setting": Path(setting_path).resolve(),
        "path_report": Path("data/outline/check_report.md").resolve(),
        "path_checked": Path(checked_dir).resolve(),
        "batch_index": batch_index,
        "total_batches": total_batches,
    })
    return body


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or make_client(cfg, "checker")
    task_dir = task_dir or "data/state/tasks"
    raw_dir = Path("data/chapters/raw")
    checked_dir = Path("data/chapters/checked")
    checked_dir.mkdir(parents=True, exist_ok=True)

    raw_files = sorted(raw_dir.glob("*.md"))
    if not raw_files:
        progress.set_stage(5, "failed", error="无 raw 章节")
        return False, "stage5 失败：无 raw 章节"
    # 断点：checked 中已存在**且内容有效**的章节跳过（空壳要重跑，见 is_usable_output）
    pending = [f for f in raw_files
               if not is_usable_output(raw_dir / f.name, checked_dir / f.name)]
    stale = [f.name for f in raw_files
             if (checked_dir / f.name).exists()
             and not is_usable_output(raw_dir / f.name, checked_dir / f.name)]
    if stale:
        print(f"[stage5] {len(stale)} 章 checked 文件是空壳/残稿，将重跑: {stale[:8]}"
              + ("…" if len(stale) > 8 else ""))
    if not pending:
        progress.mark_stage_done(5)
        return True, "stage5 跳过（checked 已存在且内容有效）"

    # 分批：每批最多 5 章
    batch_size = 5
    batches = [pending[i:i + batch_size] for i in range(0, len(pending), batch_size)]
    total_batches = len(batches)
    print(f"[stage5] 待检查 {len(pending)} 章，分 {total_batches} 批")

    def _bad_checked(batch):
        """本批中「修正文件缺失**或为空壳**」的章节名。

        ⚠️ 光判 `exists()` 不够：LLM 可能产出空壳（章节压根没进 prompt，只吐了个
        开头就收工）。那种文件"存在但不可用"，会被 `exists()` 判成已完成 →
        **空壳直接过关**，再被 stage6 当合格稿润色进成品。2026-09-29 件7 的空壳
        事故正是这样一路穿过去的。
        """
        return [f.name for f in batch
                if not is_usable_output(raw_dir / f.name, checked_dir / f.name)]

    copied_all = []
    for bi, batch in enumerate(batches, 1):
        # 件7（2026-09-29）：原先只写**裸文件名**（`- 01.md`），llm_client 的
        # `inline_inputs` 按 CWD 找不到 → 判 missing → **章节从未进入 prompt**，
        # 模型收到缺失警告后如实报告，于是 stage5 产出空壳（冒烟实测输入仅 2,048 token，
        # 真内联应 1 万+）。这里必须给**可解析的路径**（绝对值）。
        batch_files = "\n".join(f"- {f.resolve()}" for f in batch)
        task = client.write_task(task_dir, f"stage5_check_batch{bi}.md",
                                 build_check_task(proj, raw_dir, "data/setting/setting.json",
                                                  checked_dir, batch_files, bi, total_batches))
        result = client.run_task(task)
        if cost and run_id:
            # 批间熔断（2026-10-01）：orchestrator 只在**阶段之间**查预算，
            # 分批阶段内部不查的话，一轮 20 批可以一路烧过限额。
            state = cost.charge_cost(run_id, 5, 0, result)
            if state == "pause":
                # 熔断口径见 CostTracker.status_detail（金额 or token —— hermes 下只有 token）
                _detail = _pause_detail(cost, run_id)
                progress.set_stage(5, "failed",
                                   error=f"熔断暂停（{_detail['reason']}）：{_detail['message']}")
                return False, (f"stage5 熔断停止（{_detail['message']}；累计 token="
                               f"{_detail['tokens']}）—— 调大 budget.token_limit.max_total_tokens "
                               "后重跑可续上；已完成的批次保留")
        if result["exit_code"] != 0:
            progress.set_stage(5, "failed", error=f"批 {bi}/{total_batches} 子会话退出码非零")
            return False, f"stage5 批 {bi}/{total_batches} 子会话失败"

        missing = _bad_checked(batch)
        if missing:
            # 兜底：LLM 未产出**有效**修正文件（缺失或空壳）→ 从 raw 直接复制
            copied = []
            for name in missing:
                src = raw_dir / name
                dst = checked_dir / name
                if src.exists():
                    import shutil
                    shutil.copy2(src, dst)
                    copied.append(name)
            if copied:
                # ⚠️ 兜底复制 = **这批实际上没有被检查**。旧实现只打一行普通日志，
                # 用户看到「阶段 5 完成」就以为检查过了。2026-09-28 那本正是如此：
                # checked/03.md 与 raw/03.md 逐字节相同，check_report.md 从未落盘，
                # 而「检查完成」照打（→ 报告的根因是协议结束标记不匹配，已修）。
                copied_all.extend(copied)
                # ⚠️ 提示文字不用 emoji：本行会在「被捕获的 stdout」（GUI/后台任务）下
                # 以 cp936 输出，emoji 直接 UnicodeEncodeError —— 恰好在这一条
                # 「这几章未经检查」的告警上崩，等于把最该被看见的话弄丢。
                print(f"[stage5] 兜底：本批 LLM 未产出有效修正 → 复制 raw → checked "
                      f"({', '.join(copied)})；**这几章未经检查**")
                missing = _bad_checked(batch)
        if missing:
            progress.set_stage(5, "failed", error=f"批 {bi} 缺修正文件: {missing[:3]}")
            return False, f"stage5 批 {bi} 缺修正文件: {missing[:3]}"
        print(f"[stage5] 批 {bi}/{total_batches} 完成（章节 {batch[0].stem}-{batch[-1].stem}）")

    if db and run_id:
        for f in pending:
            db.log_chapter(run_id, 5, int(f.stem), "ok")

    # 阶段收尾自检（2026-10-02）：**把「没检查」这件事显式化**。
    # 旧实现只要文件存在就 `mark_stage_done` + 打印「报告: check_report.md」——
    # 而报告可能压根不存在（实测如此）。空壳检查器比没有检查器更危险。
    warnings = []
    if copied_all:
        names = sorted(set(copied_all))
        warnings.append(f"{len(names)} 章未经检查（兜底复制 raw→checked）："
                        + "、".join(names[:10]) + ("…" if len(names) > 10 else ""))
    report_path = Path("data/outline/check_report.md")
    try:
        report_text = read_text(report_path) if report_path.exists() else ""
    except Exception:                                         # noqa: BLE001
        report_text = ""
    if len(report_text.strip()) < 20:
        warnings.append("检查报告 data/outline/check_report.md 缺失或为空 —— "
                        "本轮没有留下检查记录（章节产物仍可用，但别当「检查通过」）")

    if warnings:
        print("[stage5] " + "=" * 58)
        print("[stage5] 本阶段**带警告完成**（≠ 检查通过）：")   # 不用 emoji，见上
        for w in warnings:
            print("[stage5]   · " + w)
        print("[stage5] " + "=" * 58)
        progress.set_stage(5, "done", warning="；".join(warnings))
    else:
        progress.mark_stage_done(5)

    print(f"[stage5] 逻辑检查完成（{len(raw_files)} 章，{total_batches} 批）")
    if report_text.strip():
        print("[stage5] 报告: data/outline/check_report.md")
    msg = f"stage5 完成（{len(raw_files)} 章，{total_batches} 批）"
    if warnings:
        msg += "｜⚠️ " + "；".join(warnings)
    return True, msg


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
