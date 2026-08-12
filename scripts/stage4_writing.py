# -*- coding: utf-8 -*-
"""阶段 4：逐章写作（核心链路）。

每章一次独立子会话，上下文 = 本章大纲 + 设定集（文件引用，子会话按需读取）
+ 滚动摘要 rolling.md + 上一章末尾衔接段（v2 4.4）。

完成后：verify_chapter 校验 → quality<阈值 标记 needs_rewrite（P0 不重写）
→ 提取 summary 注释 append 到 rolling.md → 更新 progress/db/cost。
"""
import argparse
import re
from pathlib import Path

from utils.api_client import HermesClient
from utils.file_io import read_text, write_text
from utils.verify_chapter import check_chapter, count_cn_words
from utils.summary_chain import append_chapter_summary, extract_prev_tail, compress_recent, load_rolling, write_rolling
from utils.progress_manager import ProgressManager
from utils.cost_tracker import CostTracker

SUMMARY_RE = re.compile(r"<!--\s*summary:\s*(.+?)\s*-->", re.IGNORECASE | re.S)


def build_chapter_task(cfg, proj, n, outline_path, setting_path, rolling_path, prev_tail):
    tail_section = "\n\n".join(prev_tail) if prev_tail else "（无上一章，本章为开篇）"
    return f"""# 阶段 4 任务：写作第 {n} 章

你是长篇小说写作 Agent，服务于《{proj.get('book', {}).get('name', '未命名')}》。

## 输入文件（用 read_file 读取）
- 本章大纲: {Path(outline_path).resolve()}
- 设定集: {Path(setting_path).resolve()}（仅参考涉及本章的角色/地点/势力条目，locked 条目不可违逆）
- 滚动摘要: {Path(rolling_path).resolve()}
- 项目配置: {Path('config/project.yaml').resolve()}

## 上一章末尾衔接段（用于语气与节奏衔接）
{tail_section}

## 写作要求
1. 字数 {cfg.get('chapter', {}).get('target_words', [2000, 3000])[0]}-{cfg.get('chapter', {}).get('target_words', [2000, 3000])[1]} 字
2. 严格遵循本章大纲事件推进；可补充细节，但不得新增关键情节
3. 角色言行符合设定集 traits 与 relations
4. 结尾自然衔接下一章开头

## 输出
将正文写入文件: {Path(f'data/chapters/raw/{n:02d}.md').resolve()}

## 输出格式（严格遵循）
## 第{n}章 章节名
（正文，段落间空一行）

文件末尾追加两行注释：
<!-- quality: X/10 -->（X=0-10，依据：与大纲契合度/设定遵循度/文笔流畅度）
<!-- summary: 本章关键事件与角色状态摘要（约100字） -->

## 硬性禁止
- 禁止与设定集 locked 条目冲突；禁止修改时间线
- 禁止使用 Markdown 表格、图片、代码块；确需呈现用文本描述，如 [表格：xxx]
- 禁止 AI 腔套话；禁止占位符（XXX/TODO/待补充）
"""


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or HermesClient()
    task_dir = task_dir or "data/state/tasks"
    total = int(proj.get("book", {}).get("chapters", 10))
    raw_dir = Path("data/chapters/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    rolling_path = Path("data/summaries/rolling.md")
    quality_threshold = int(cfg.get("chapter", {}).get("quality_threshold", 6))
    max_recent = int(cfg.get("chapter", {}).get("summary_every", 5))

    completed = progress.completed_chapters(4)
    failed = progress.failed_chapters(4)
    failed_ns = {f["n"] for f in failed}
    todo = [n for n in range(1, total + 1) if n not in completed or n in failed_ns]

    if not todo:
        print("[stage4] 全部章节已存在，跳过")
        progress.set_stage(4, "done")
        return True, "stage4 跳过（已完成）"

    progress.set_stage(4, "running")
    for n in todo:
        chap_path = raw_dir / f"{n:02d}.md"
        prev_path = raw_dir / f"{n - 1:02d}.md"
        prev_tail = extract_prev_tail(prev_path, 4) if prev_path.exists() else []
        task = client.write_task(task_dir, f"stage4_ch{n:02d}.md",
                                 build_chapter_task(cfg, proj, n,
                                                    f"data/outline/chapters/{n:02d}.md",
                                                    "data/setting/setting.json",
                                                    "data/summaries/rolling.md",
                                                    prev_tail))
        result = client.run_task(task)
        cost_est = cost.estimate_cost_yuan(result["tokens"] or 20000, result["tokens"] // 3 or 6000) if cost else 0
        if result["exit_code"] != 0:
            progress.mark_chapter_failed(n, "子会话退出码非零", 4)
            if db and run_id:
                db.log_chapter(run_id, 4, n, "failed", error="exit非零")
            print(f"[stage4] 第{n}章子会话失败")
            continue

        if not chap_path.exists():
            progress.mark_chapter_failed(n, "章节文件未生成", 4)
            if db and run_id:
                db.log_chapter(run_id, 4, n, "failed", error="未生成文件")
            continue

        check = check_chapter(chap_path,
                              cfg.get("chapter", {}).get("target_words", [2000, 3000])[0],
                              cfg.get("chapter", {}).get("target_words", [2000, 3000])[1])
        if not check.ok:
            progress.mark_chapter_failed(n, "校验失败: " + "; ".join(check.errors), 4)
            if db and run_id:
                db.log_chapter(run_id, 4, n, "failed", error="; ".join(check.errors),
                               quality=check.quality, cost_yuan=cost_est)
            print(f"[stage4] 第{n}章校验失败: {check.errors[:2]}")
            continue

        # 摘要提取 + 滚动维护
        text = read_text(chap_path)
        sm = SUMMARY_RE.search(text)
        summary = sm.group(1).strip() if sm else f"（第{n}章，未附摘要）"
        res = append_chapter_summary(rolling_path, n, summary, max_recent=max_recent)
        if res["need_compress"]:
            data = load_rolling(rolling_path)
            oldest = res["oldest_chapter"]
            merged = f"{data.get('global_summary', '')} 第{oldest}章: {data['chapters'].get(oldest, '')}"
            data = compress_recent(data, oldest, "", merged[:500])
            write_rolling(rolling_path, data)
            print(f"[stage4] 滚动摘要已压缩（第{oldest}章并入全书摘要）")

        # quality 标记（P0 只标记不重写）
        needs_rewrite = check.quality is not None and check.quality < quality_threshold
        status = "needs_rewrite" if needs_rewrite else "ok"
        progress.mark_chapter_done(n, 4)
        if needs_rewrite:
            progress.data["stages"]["4"].setdefault("needs_rewrite", [])
            if n not in progress.data["stages"]["4"]["needs_rewrite"]:
                progress.data["stages"]["4"]["needs_rewrite"].append(n)
            progress.save()
        if db and run_id:
            db.log_chapter(run_id, 4, n, status, quality=check.quality, cost_yuan=cost_est)
        if cost:
            cost.record(run_id, 4, n, result["tokens"] or 20000, result["tokens"] // 3 or 6000)
        print(f"[stage4] 第{n}章完成 {check.summary()}")

    # 收尾
    remaining_failed = progress.failed_chapters(4)
    if remaining_failed:
        progress.set_stage(4, "failed", failed_count=len(remaining_failed))
        return False, f"stage4 有 {len(remaining_failed)} 章失败"
    progress.set_stage(4, "done")
    return True, f"stage4 完成（{total} 章）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段4：逐章写作")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()
    import yaml
    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(cfg, proj, progress, None, None, task_dir=args.task_dir)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
