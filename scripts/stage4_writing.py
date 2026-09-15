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

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.verify_chapter import check_chapter, count_cn_words, is_chapter_complete
from utils.summary_chain import append_chapter_summary, extract_prev_tail, compress_recent, load_rolling, write_rolling
from utils.template_loader import load_template
from utils.style_analyzer import extract_style_samples, build_style_notes_section
from utils.progress_manager import ProgressManager
from utils.cost_tracker import CostTracker

SUMMARY_RE = re.compile(r"<!--\s*summary:\s*(.+?)\s*-->", re.IGNORECASE | re.S)


def _safe_read(path, budget=20000):
    try:
        return read_text(path)[:budget]
    except Exception:
        return ""


def sync_appearances_after_chapter(chapter_text, chapter_no, total_chapters=None):
    """章节完成后同步出场记录（确定性，零 LLM）。

    两路都做，任何异常只告警不阻断流水线（出场记录属辅助产物）：
    1) rosa_integrate.sync_chapter_appearances → setting.json 的 appearances 字段（幂等）
    2) appearances.refresh_appearances → data/state/appearances.json（全量重算，逐章精确）
    返回 True 表示 appearances.json 已刷新。
    """
    try:
        from rosa_integrate import sync_chapter_appearances
        sync_chapter_appearances(chapter_text, chapter_no)
    except Exception as e:      # noqa: BLE001
        print("[stage4] [warn] 出场记录同步失败（setting.json，不影响流程）: "
              + type(e).__name__ + ": " + str(e)[:160])
    try:
        from appearances import refresh_appearances
        path, stats, total, _new = refresh_appearances(chapter_count=total_chapters)
        if path:
            print("[stage4] 出场记录已更新: " + str(path)
                  + "（" + str(len(stats)) + " 个角色 / " + str(total) + " 章）")
            return True
        print("[stage4] [warn] 出场记录暂无可统计内容（缺设定集角色或章节），已跳过")
    except Exception as e:      # noqa: BLE001
        print("[stage4] [warn] appearances.json 刷新失败（不影响流程）: "
              + type(e).__name__ + ": " + str(e)[:160])
    return False


def _extract_character_cards_for_chapter(setting_path, outline_path, budget=3000):
    """从 setting.json 提取当前章节涉及的角色卡片段。

    匹配逻辑：从大纲中提取「涉及角色」字段，从 setting.json 的 characters
    中找出对应角色，返回格式化的角色卡文本（供任务文件注入）。
    setting.json 角色 schema: name, path, tags, type, locked, relations, snippet, source
    """
    try:
        setting_text = read_text(setting_path)
        import json
        setting = json.loads(setting_text)
        characters = setting.get("characters", [])
        if not characters:
            return ""

        # 从大纲提取涉及角色
        outline_text = _safe_read(outline_path, budget=8000)
        rm = re.search(r"涉及角色[：:]\s*(.+)", outline_text)
        if not rm:
            return ""
        involved = [c.strip() for c in re.split(r"[,，、/；;]", rm.group(1)) if c.strip()]

        cards_parts = ["### 本章涉及角色卡（写作时严格遵循，locked 条目不可违逆）"]
        for name in involved:
            for char in characters:
                if isinstance(char, dict) and char.get("name") == name:
                    parts = [f"**{char['name']}**"]
                    if char.get("type"):
                        parts.append(f"类型：{char['type']}")
                    if char.get("tags"):
                        tags = char["tags"] if isinstance(char["tags"], list) else [char["tags"]]
                        parts.append(f"标签：{'、'.join(str(t) for t in tags)}")
                    if char.get("relations"):
                        rels = char["relations"]
                        if isinstance(rels, list) and rels:
                            parts.append(f"关系：{'、'.join(str(r) for r in rels)}")
                        elif isinstance(rels, str) and rels:
                            parts.append(f"关系：{rels}")
                    if char.get("snippet"):
                        snippet = char["snippet"][:200].replace('\n', ' ')
                        parts.append(f"简介：{snippet}…")
                    if char.get("locked"):
                        parts.append("⚠ locked 条目，绝对不可违逆")
                    cards_parts.append("\n".join(parts))
                    break
        return "\n\n".join(cards_parts) if len(cards_parts) > 1 else ""
    except Exception:
        return ""


def build_chapter_task(cfg, proj, n, outline_path, setting_path, rolling_path, prev_tail):
    target = cfg.get("chapter", {}).get("target_words", [2000, 3000])
    tail_section = "\n\n".join(prev_tail) if prev_tail else "（无上一章，本章为开篇）"
    book = proj.get("book", {})

    # 风格注入
    style_ref = (book.get("style_reference") or "").strip()
    style_text = read_text(style_ref).strip()[:6000] if style_ref and Path(style_ref).exists() else "（未提供，请严格按下方文风要求写作）"
    style_samples = ""
    if style_ref:
        current_text = "\n".join([tail_section, _safe_read(outline_path)])
        style_samples = extract_style_samples(style_ref, current_text=current_text)
    style_notes = build_style_notes_section(book.get("style_notes", ""))

    # P2.4 知识库上下文注入（纯角色名，避免整段描述干扰检索）
    kb_context = ""
    try:
        from utils import kb_index
        idx = kb_index.load_index()
        if idx:
            outline_text = _safe_read(outline_path, budget=8000)
            rm = re.search(r"涉及角色[：:]\s*(.+)", outline_text)
            query = rm.group(1)[:100] if rm else outline_text[:100]
            results = kb_index.search(query, index=idx, top_k=5, vault_path="E:/图书馆/ROSA")
            if results:
                parts = ["### 相关正典词条（写作时参考，locked 条目不可违逆）"]
                for path, name, snippet, score in results:
                    parts.append(f"**{name}**（相关度 {score:.1f}）\n{snippet}\n")
                kb_context = "\n".join(parts)
    except Exception:
        pass

    # 角色卡锁定：从 setting.json 提取当前章节涉及的角色
    character_cards = _extract_character_cards_for_chapter(setting_path, outline_path)

    _, body = load_template("stage4_writing.md", {
        "n": n,
        "book_name": book.get("name", "未命名"),
        "path_outline": Path(outline_path).resolve(),
        "path_setting": Path(setting_path).resolve(),
        "path_rolling": Path(rolling_path).resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "prev_tail": tail_section,
        "style_reference": style_text,
        "style_samples": style_samples,
        "style_notes": style_notes,
        "kb_context": kb_context,
        "character_cards": character_cards,
        "min_words": target[0],
        "max_words": target[1],
        "path_output": Path(f"data/chapters/raw/{n:02d}.md").resolve(),
    })
    return body


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or make_client(cfg, "writer")
    task_dir = task_dir or "data/state/tasks"
    total = int(proj.get("book", {}).get("chapters", 10))
    raw_dir = Path("data/chapters/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    rolling_path = Path("data/summaries/rolling.md")
    quality_threshold = int(cfg.get("chapter", {}).get("quality_threshold", 6))
    max_recent = int(cfg.get("chapter", {}).get("summary_every", 5))

    completed = progress.completed_chapters(4)
    # 断点续跑：只保留文件真正完成的章节（防止半成品被跳过）
    verified = [n for n in completed if is_chapter_complete(raw_dir / f"{n:02d}.md", *cfg.get("chapter", {}).get("target_words", [2000, 3000]))]
    skipped = set(completed) - set(verified)
    if skipped:
        print(f"[stage4] 半成品章节重跑（文件存在但校验未通过）: {sorted(skipped)}")

    failed = progress.failed_chapters(4)
    failed_ns = {f["n"] for f in failed}
    todo = [n for n in range(1, total + 1) if n not in verified or n in failed_ns or n in skipped]

    if not todo:
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
        cost_est = (cost.estimate_cost_yuan(result["tokens"], result["tokens_out"],
                                            model=result.get("model"),
                                            provider=result.get("provider"),
                                            role=result.get("model_key"),
                                            cache_read=result.get("cache_read", 0))
                    if cost else 0)
        if result["exit_code"] != 0:
            progress.mark_chapter_failed(n, "子会话退出码非零", 4)
            print(f"[stage4] 第{n}章子会话失败")
            continue

        if not chap_path.exists():
            progress.mark_chapter_failed(n, "章节文件未生成", 4)
            continue

        check = check_chapter(chap_path,
                              cfg.get("chapter", {}).get("target_words", [2000, 3000])[0],
                              cfg.get("chapter", {}).get("target_words", [2000, 3000])[1])
        if not check.ok:
            progress.mark_chapter_failed(n, "校验失败: " + "; ".join(check.errors), 4)
            print(f"[stage4] 第{n}章校验失败: {check.errors[:2]}")
            continue

        # 摘要提取 + 滚动维护
        text = read_text(chap_path)
        sm = SUMMARY_RE.search(text)
        summary = sm.group(1).strip() if sm else f"（第{n}章，未附摘要）"
        res = append_chapter_summary(rolling_path, n, summary, max_recent=max_recent)

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
            cost.charge_cost(run_id, 4, n, result)

        # 出场记录同步（P1.6：章节完成即更新 data/state/appearances.json；
        # 失败只告警，绝不阻断写作主线）
        sync_appearances_after_chapter(text, n, total)
        print(f"[stage4] 第{n}章完成 {check.summary()}")

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
