# -*- coding: utf-8 -*-
"""批量精修脚本（P0 审稿→修稿闭环，Phase 2）。

读取审查报告 + 用户决策，按章节调用精修逻辑：
- 用户决策：接受/忽略/补充每一条审查发现
- 按章节合并意见 → 复用 refine_chapter 核心逻辑
- 每章精修前自动快照（snapshot.py），防崩溃
- 精修后校验（字数 ±20% + quality 注释）

用法：
  python scripts/batch_refine.py --report data/outline/review_report.json
  python scripts/batch_refine.py --report data/outline/review_report.json --decisions user_decisions.json
  python scripts/batch_refine.py --report data/outline/review_report.json --auto  # 接受全部
  python scripts/batch_refine.py --report data/outline/review_report.json --dry-run  # 只备份+生成任务
  python scripts/batch_refine.py --report data/outline/review_report.json --interactive  # 逐条确认
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.file_io import read_text, write_text
from utils.verify_chapter import count_cn_words
from utils.progress_manager import ProgressManager
from utils.llm_client import make_client

HISTORY_DIR = Path("data/chapters/history")
OUTLINE_DIR = Path("data/outline/chapters")
GLOBAL_OUTLINE_PATH = Path("data/outline/global.md")
SETTING_PATH = Path("data/setting/setting.json")


def load_report(report_path):
    """加载审查报告 JSON。"""
    try:
        data = json.loads(read_text(report_path))
        return data
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[batch_refine] 报告加载失败: {e}")
        return None


def pick_chapter_path(n, scope="checked"):
    """按 refined > checked > raw 优先级定位章节文件。"""
    for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
        p = Path(d) / f"{n:02d}.md"
        if p.exists():
            return p
    return None


def next_version(chapter):
    """该章已有备份数 + 1。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in HISTORY_DIR.glob(f"ch{chapter:02d}_v*.md")
            if (m := re.match(rf"ch{chapter:02d}_v(\d+)\.md", p.name))]
    return (max(nums) + 1) if nums else 1


def build_refine_task(cfg, proj, chapter, feedback, version, chap_path, outline_path):
    """生成精修任务文件内容（自包含）。"""
    from utils.template_loader import load_template
    current = read_text(chap_path)
    meta, body = load_template("stage4_refine.md", {
        "chapter": chapter,
        "version": version,
        "feedback": feedback,
        "path_outline": Path(outline_path).resolve(),
        "path_setting": Path("data/setting/setting.json").resolve(),
        "path_chapter": Path(chap_path).resolve(),
    })
    body = body.replace("{{current_chapter}}", current)
    return body


def run_single_refine(cfg, proj, chapter, feedback, client=None, task_dir=None, dry_run=False):
    """执行单章精修，返回 (ok, msg, backup_path)。"""
    task_dir = task_dir or "data/state/tasks"
    client = client or make_client(cfg, "writer")

    chap_path = pick_chapter_path(chapter)
    if chap_path is None:
        return False, f"第 {chapter} 章不存在（raw/checked/refined 均无）", None

    version = next_version(chapter)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    backup = HISTORY_DIR / f"ch{chapter:02d}_v{version}.md"
    shutil.copy2(chap_path, backup)
    print(f"  [backup] → {backup}（v{version}）")

    outline = OUTLINE_DIR / f"{chapter:02d}.md"
    if not outline.exists():
        outline = GLOBAL_OUTLINE_PATH

    task = client.write_task(task_dir, f"batch_refine_ch{chapter:02d}.md",
                             build_refine_task(cfg, proj, chapter, feedback,
                                                version, chap_path, outline))
    print(f"  [task] {task}")

    if dry_run:
        print("  [dry-run] 未调用子会话")
        return True, "dry-run", backup

    result = client.run_task(task)
    if result["exit_code"] != 0:
        return False, f"精修子会话失败: {result.get('stdout_tail', '')[:200]}", backup

    if not chap_path.exists():
        return False, "精修后章节文件缺失", backup

    # 校验：字数 ±20% + quality 注释
    before = count_cn_words(read_text(backup))
    after = count_cn_words(read_text(chap_path))
    delta = abs(after - before) / before if before else 0
    q = re.search(r"<!--\s*quality:\s*(\d+(?:\.\d+)?)", read_text(chap_path))

    if delta > 0.2:
        return False, (f"字数变化 {delta*100:.0f}% 超 20% 铁律"
                       f"（{before}→{after}），已保留备份 {backup}"), backup

    print(f"  [done] 第 {chapter} 章修订：{before}→{after} 字 "
          f"({delta*100:+.1f}%)；quality: {q.group(1) if q else '缺失'}")
    if q is None:
        print("  [warn] 修订后缺 <!-- quality --> 注释")

    return True, f"第 {chapter} 章精修完成（v{version}）", backup


def interactive_decisions(report_data):
    """交互式收集用户决策，返回 decisions 列表。"""
    decisions = []
    chapters = report_data.get("chapters", [])
    for chap in chapters:
        n = chap.get("n")
        findings = chap.get("findings", [])
        if not findings:
            continue
        print(f"\n{'='*50}")
        print(f"第 {n} 章（{len(findings)} 条发现）")
        print(f"{'='*50}")
        for f in findings:
            fid = f.get("id", "?")
            ftype = f.get("type", "other")
            severity = f.get("severity", "info")
            detail = f.get("detail", "")
            suggestion = f.get("suggested_action", "")
            icon = {"error": "🔴", "warn": "🟡", "info": "🔵"}.get(severity, "⚪")
            print(f"\n  {icon} [{fid}] {ftype} ({severity})")
            print(f"  问题: {detail}")
            if suggestion:
                print(f"  建议: {suggestion}")

            while True:
                choice = input("\n  决策: [a]接受并精修 / [i]忽略 / [c]自定义意见 / [s]跳过整章 > ").strip().lower()
                if choice in ("a", "i", "c", "s"):
                    break
                print("  请输入 a / i / c / s")

            if choice == "i":
                decisions.append({"finding_id": fid, "chapter": n, "action": "ignore"})
            elif choice == "a":
                decisions.append({"finding_id": fid, "chapter": n, "action": "accept", "feedback": ""})
            elif choice == "c":
                custom = input("  自定义意见: ").strip()
                decisions.append({"finding_id": fid, "chapter": n, "action": "accept", "feedback": custom})
            elif choice == "s":
                # 跳过整章：将该章所有发现标为 ignore
                for f2 in findings:
                    decisions.append({"finding_id": f2.get("id"), "chapter": n, "action": "ignore"})
                break

    return decisions


def load_decisions_file(path):
    """从文件加载用户决策。支持两种格式：
    1. { "decisions": [{"finding_id", "chapter", "action", "feedback"}] }
    2. { "finding_id": {"action", "feedback"} }  （GUI 直传格式）
    """
    try:
        data = json.loads(read_text(path))
        # 格式 1：数组
        if "decisions" in data:
            return data["decisions"]
        # 格式 2：对象（GUI 直传）
        decisions = []
        for fid, d in data.items():
            if fid == "decisions":
                continue
            if isinstance(d, dict) and "action" in d:
                decisions.append({
                    "finding_id": fid,
                    "chapter": d.get("chapter"),
                    "action": d["action"],
                    "feedback": d.get("feedback", ""),
                })
        return decisions
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[batch_refine] 决策文件加载失败: {e}")
        return []


def build_chapter_feedback(chapter_n, findings, decisions):
    """按章节合并决策为一条修订意见。"""
    accepted = []
    for d in decisions:
        # 兼容两种格式：有 chapter 字段（CLI 生成）或无（GUI 按 finding_id 找）
        if d.get("chapter") and int(d["chapter"]) != chapter_n:
            continue
        if d.get("action") == "ignore":
            continue
        # accept
        fid = d.get("finding_id")
        finding = next((f for f in findings if f.get("id") == fid), None)
        if finding is None:
            continue
        custom = d.get("feedback", "").strip()
        if custom:
            accepted.append(f"- {finding['detail']}（用户补充意见：{custom}）")
        else:
            accepted.append(f"- {finding['detail']}")

    if not accepted:
        return None

    return f"第 {chapter_n} 章修订意见（共 {len(accepted)} 条）：\n" + "\n".join(accepted)


def run_batch_refine(report_path, decisions_mode, auto_accept, dry_run, client=None):
    """批量精修主流程。"""
    report_data = load_report(report_path)
    if report_data is None:
        return False, "报告加载失败"

    # 收集决策
    if auto_accept:
        decisions = []
        for chap in report_data.get("chapters", []):
            for f in chap.get("findings", []):
                decisions.append({"finding_id": f.get("id"), "chapter": chap["n"], "action": "accept", "feedback": ""})
        print(f"[batch_refine] 自动接受模式：{len(decisions)} 条")
    elif decisions_mode == "interactive":
        decisions = interactive_decisions(report_data)
        if not decisions:
            print("[batch_refine] 无决策，退出")
            return True, "无决策"
        # 保存决策文件供后续查阅
        decisions_path = Path(report_path).with_suffix(".decisions.json")
        write_text(decisions_path, json.dumps({"decisions": decisions}, ensure_ascii=False, indent=2))
        print(f"[batch_refine] 决策已保存: {decisions_path}")
    elif decisions_mode == "file":
        decisions_path = Path(report_path).with_suffix(".decisions.json")
        decisions = load_decisions_file(decisions_path)
        if not decisions:
            return False, "无可用决策（先用 --interactive 生成或手动创建 .decisions.json）"
        print(f"[batch_refine] 从文件加载 {len(decisions)} 条决策")
    else:
        return False, f"未知决策模式: {decisions_mode}"

    # 按章节分组
    chapters_to_refine = {}
    for chap in report_data.get("chapters", []):
        n = chap["n"]
        feedback = build_chapter_feedback(n, chap.get("findings", []), decisions)
        if feedback:
            chapters_to_refine[n] = feedback

    if not chapters_to_refine:
        print("[batch_refine] 无需要精修的章节")
        return True, "无需要精修的章节"

    print(f"\n[batch_refine] 待精修 {len(chapters_to_refine)} 章: {sorted(chapters_to_refine.keys())}")

    # 加载配置
    import yaml
    cfg = yaml.safe_load(read_text("config/system.yaml")) or {}
    proj = yaml.safe_load(read_text("config/project.yaml")) or {}

    # 创建进度管理器
    progress = ProgressManager("data/state/progress.json")

    # 逐章精修
    results = []
    task_dir = Path("data/state/tasks")
    total_chapters = len(chapters_to_refine)
    progress_file = Path("data/state/batch_refine_progress.json")

    for idx, n in enumerate(sorted(chapters_to_refine.keys()), 1):
        feedback = chapters_to_refine[n]
        print(f"\n{'─'*40}")
        print(f"[batch_refine] 第 {n} 章精修（{idx}/{total_chapters}）")
        print(f"{'─'*40}")
        print(f"  意见: {feedback[:200]}{'...' if len(feedback) > 200 else ''}")

        # 写入实时进度
        progress_file.write_text(json.dumps({
            "current": n,
            "total": total_chapters,
            "index": idx,
            "status": "running",
            "message": f"正在精修第 {n} 章（{idx}/{total_chapters}）",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        # 快照（每章精修前）
        try:
            import snapshot as snap
            snap.snapshot(f"before_refine_ch{n:02d}")
        except Exception as e:
            print(f"  [warn] 快照失败（不影响流程）: {e}")

        ok, msg, backup = run_single_refine(
            cfg, proj, n, feedback,
            client=client, task_dir=task_dir, dry_run=dry_run
        )
        results.append((n, ok, msg))

        if ok:
            progress.set_review_status(n, "revised")
        else:
            progress.set_review_status(n, "failed")
            print(f"  [FAIL] {msg}")
            if backup:
                print(f"  [restore] 可从备份恢复: {backup}")

    # 汇总
    print(f"\n{'═'*40}")
    print("[batch_refine] 精修汇总")
    print(f"{'═'*40}")
    success = sum(1 for _, ok, _ in results if ok)
    fail = sum(1 for _, ok, _ in results if not ok)
    for n, ok, msg in results:
        status = "✓" if ok else "✗"
        print(f"  第 {n} 章 {status}: {msg}")
    print(f"\n合计: {success}/{len(results)} 成功，{fail} 失败")

    # 写入最终进度
    progress_file.write_text(json.dumps({
        "current": None,
        "total": total_chapters,
        "index": total_chapters,
        "status": "done" if fail == 0 else "failed",
        "message": f"批量精修完成：成功 {success} / 失败 {fail}",
        "results": [{"chapter": n, "ok": ok, "msg": msg} for n, ok, msg in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # 更新报告状态
    progress.set_review_report(str(Path(report_path).with_suffix(".json")))

    return fail == 0, f"成功 {success} / 失败 {fail}"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 批量精修（P0 闭环 Phase 2）")
    parser.add_argument("--report", default="data/outline/review_report.json",
                        help="审查报告路径")
    parser.add_argument("--decisions", choices=["interactive", "file"], default="interactive",
                        help="决策模式：interactive（逐条确认）/ file（从 .decisions.json 读取）")
    parser.add_argument("--auto", action="store_true",
                        help="自动接受全部审查发现（跳过确认）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅备份+生成任务文件，不调用 LLM")
    args = parser.parse_args()

    ok, msg = run_batch_refine(
        args.report,
        args.decisions,
        args.auto,
        args.dry_run,
    )
    print(f"\n[batch_refine] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
