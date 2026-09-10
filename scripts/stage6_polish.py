# -*- coding: utf-8 -*-
"""阶段 6：基础润色（P0 → P1 分卷并行）。

仅改表达不改情节；校验润色后字数变化 < 20%（防止重写）。
输出 chapters/refined/。

风格反馈校验：若配置有 style_reference，润色完成后逐章对比范文与润色稿的
文风特征，把偏差小节追加进本卷 polish_report（阈值 30%，仅提示不阻断流程）。

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
from utils.file_io import read_text, write_text, append_text
from utils.verify_chapter import count_cn_words
from utils.template_loader import load_template
from utils.style_analyzer import (
    build_style_notes_section,
    extract_style_features,
    compute_style_drift,
    format_drift_report,
    MIN_TEXT_LEN,
)

# 范文特征缓存：style_reference 路径 -> 特征字典（一次提取，逐章复用）
_REF_FEATURE_CACHE = {}


def _report_path(vol_index=None):
    """润色报告路径（分卷时带卷号），偏差报告追加到同一文件。"""
    if vol_index is not None:
        return Path(f"data/outline/polish_report_vol{vol_index}.md").resolve()
    return Path("data/outline/polish_report.md").resolve()


def _load_reference_features(style_ref):
    """加载范文风格特征并缓存；未配置 / 读取或提取失败均返回 {}（跳过偏差分析）。"""
    if not style_ref:
        return {}
    key = str(style_ref)
    if key in _REF_FEATURE_CACHE:
        return _REF_FEATURE_CACHE[key]
    feats = {}
    try:
        feats = extract_style_features(read_text(style_ref)) or {}
    except Exception as e:  # 范文不可读不应阻塞润色
        print(f"[stage6] 范文特征提取失败，跳过风格偏差分析：{e}")
        feats = {}
    if not feats:
        print(f"[stage6] 范文特征不足（{style_ref}），跳过风格偏差分析")
    _REF_FEATURE_CACHE[key] = feats
    return feats


def build_polish_task(proj, checked_dir, refined_dir, chapters, vol_index=None):
    checked = Path(checked_dir).resolve()
    listing = "\n".join(f"- 第{n}章: {checked / f'{n:02d}.md'}" for n in chapters)
    report_path = _report_path(vol_index)

    # 加载文风参考：量化风格指令 + 范文原文片段（few-shot）+ 用户手写的风格笔记
    book = proj.get("book", {})
    style_instruction = ""
    style_samples = ""
    style_ref = book.get("style_reference", "")
    if style_ref:
        from utils.style_analyzer import load_style_reference, extract_style_samples
        style_instruction = load_style_reference(style_ref)
        # 以本卷待润色正文作为"当前内容"，避开与之高度相似的范文段落
        current_text = _collect_current_text([checked / f"{n:02d}.md" for n in chapters])
        style_samples = extract_style_samples(style_ref, current_text=current_text)
        # 预计算范文特征并缓存，供 _run_one_volume 的偏差分析复用（避免每章重复提取）
        _load_reference_features(style_ref)
    style_notes = build_style_notes_section(book.get("style_notes", ""))

    _, body = load_template("stage6_polish.md", {
        "listing": listing,
        "path_refined": Path(refined_dir).resolve(),
        "path_report": report_path,
        "style_instruction": style_instruction,
        "style_samples": style_samples,
        "style_notes": style_notes,
    })
    return body


def _collect_current_text(paths, budget=20000):
    """汇总若干文件正文，作为片段筛选的"当前内容"参照（限量，防任务膨胀）。"""
    chunks = []
    total = 0
    for p in paths:
        try:
            t = read_text(p)
        except Exception:
            continue
        if not t:
            continue
        chunks.append(t)
        total += len(t)
        if total >= budget:
            break
    return "\n".join(chunks)[:budget]


def _drift_section(chapter, ref_features, output_text, threshold=0.3):
    """生成单章风格偏差小节；范文特征缺失 / 输出过短 / 提取失败均返回 ""。"""
    if not ref_features or len(output_text or "") < MIN_TEXT_LEN:
        return ""
    try:
        out_features = extract_style_features(output_text) or {}
    except Exception as e:  # 分析异常不应阻塞润色
        print(f"[stage6] 第{chapter}章特征提取失败，跳过偏差分析：{e}")
        return ""
    if not out_features:
        return ""
    result = compute_style_drift(ref_features, out_features, threshold=threshold)
    return format_drift_report(result, title=f"第{chapter}章")


def _append_drift_report(report_path, sections):
    """把偏差小节追加到润色报告末尾；写失败只告警，不影响润色结果。"""
    blocks = [s for s in sections if s and s.strip()]
    if not blocks:
        return 0
    try:
        append_text(report_path, "\n" + "\n".join(b.strip() for b in blocks) + "\n")
    except Exception as e:
        print(f"[stage6] 风格偏差报告追加失败（不阻塞润色）：{e}")
        return 0
    return len(blocks)


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

    # 风格偏差分析：范文特征在 build_polish_task 中已缓存，此处直接复用
    ref_features = _load_reference_features((proj.get("book") or {}).get("style_reference", ""))
    drift_sections = []
    for n in vol_chapters:
        src = checked / f"{n:02d}.md"
        out = refined / f"{n:02d}.md"
        before = count_cn_words(read_text(src))
        out_text = read_text(out)
        after = count_cn_words(out_text)
        if before and abs(after - before) / before > 0.2:
            return False, f"stage6 第{n}章字数变化超20%（疑似重写）", vol_chapters
        if ref_features:
            drift_sections.append(_drift_section(n, ref_features, out_text))

    if ref_features and drift_sections:
        written = _append_drift_report(_report_path(vol_index), drift_sections)
        if written:
            print(f"[stage6] 卷{vol_index} 风格偏差报告已追加 {written} 章 → {_report_path(vol_index).name}")
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
