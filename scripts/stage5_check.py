# -*- coding: utf-8 -*-
"""阶段 5：逻辑检查（P0 基础版，单路）。

P0：单子会话全量检查（角色一致性/时间线/伏笔/世界观合并一路），
修正问题句输出到 chapters/checked/ + 问题报告。
P1 升级：四路并行（角色/时间线/伏笔/世界观）分批 delegate + 归并（v2 4.5）。
"""
import argparse
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.cost_tracker import pause_detail as _pause_detail
from utils.template_loader import load_template
from utils.verify_chapter import is_usable_output
from utils.config_io import load_config_yaml
from utils.text_norm import normalize_chapter_file
from utils import review_ledger as rl


REVIEW_DIR = Path("data/review")


def _norm(path):
    """就地归一化章节文件的中文标点。幂等、零 token；失败只打印不阻断。

    与 stage4/stage6 的 `_norm` 同一实现（utils/text_norm）；这几个脚本都是
    可独立运行的入口，各自薄包一层，避免互相 import 业务函数。
    """
    try:
        changed, counts = normalize_chapter_file(path)
    except Exception as e:                                    # noqa: BLE001
        print(f"[text_norm] 标点归一化失败（不影响主流程）: {type(e).__name__}: {e}")
        return
    if changed:
        detail = "、".join(f"{k}={v}" for k, v in counts.items() if v)
        print(f"[stage5] {Path(path).name} 标点归一化（{detail}）")


def build_check_task(proj, raw_dir, setting_path, checked_dir, batch_files, batch_index, total_batches):
    # 评审账本（2026-10-20）：每批一份，stage5 读回后按章拆成 data/review/NN.json。
    # 路径必须是**绝对路径**——模板里的相对路径在 GUI/后台任务下按 CWD 解析，
    # 而 CWD 不一定是仓库根（--root 打包态尤其如此）。
    path_review = (REVIEW_DIR / f"batch{batch_index}.json").resolve()

    # 按文体裁剪评审范围（2026-10-20）：散文项目原先也被要求逐条举证七维 +
    # 给"对话区分度"作证 —— 校验侧标 N/A 不计分，但 token 已经花掉，账本里
    # 还会留下模型为凑数硬编的证据。提示词本身要裁。
    from utils import writing_form as _wf
    _scope = _wf.review_scope(_wf.applicability(proj), str(path_review))

    _, body = load_template("stage5_check.md", {
        "batch_files": batch_files,
        "path_setting": Path(setting_path).resolve(),
        "path_report": Path("data/outline/check_report.md").resolve(),
        "path_checked": Path(checked_dir).resolve(),
        "path_review": path_review,
        "review_scope": _scope,
        "batch_index": batch_index,
        "total_batches": total_batches,
    })
    return body


def _chapter_no(path):
    """从文件名取章号（`03.md` → 3）；取不到返回 None。"""
    try:
        return int(Path(path).stem)
    except (TypeError, ValueError):
        return None


def _verify_ledger(batch, bi, app=None):
    """读回本批七维评审 JSON 并校验。返回 (results, problems)。

    **为什么必须是「读回文件」而不是「解析 stdout」**：stdout 尾部只留最后几百字，
    而且模型随手说的 JSON 片段与真正落盘的产物不是一回事。只有落盘文件才是
    下游能复用的那份账本。
    """
    p = REVIEW_DIR / f"batch{bi}.json"
    if not p.exists():
        return [], [f"批 {bi} 未产出七维评审（缺 {p.as_posix()}）"]
    try:
        text = read_text(p)
    except Exception as e:                                    # noqa: BLE001
        return [], [f"批 {bi} 七维评审读取失败：{type(e).__name__}"]
    try:
        payload = rl.load_payload(text)
    except rl.LedgerError as e:
        return [], [f"批 {bi} 七维评审不是可用 JSON（{e}）"]

    texts = {}
    for f in batch:
        no = _chapter_no(f)
        if no is None:
            continue
        try:
            texts[no] = read_text(f)
        except Exception:                                     # noqa: BLE001
            texts[no] = ""
    # 文体（2026-10-20）：不适用的维度标 N/A —— 不计分、不算缺失、不进均值。
    # 散文只有"常识"一维适用，硬按七维要求它永远不完整。
    _dims = (app or {}).get("review_dims")
    _qs = (app or {}).get("aesthetic")
    results, unknown = rl.validate_payload(payload, texts, dims=_dims, questions=_qs)
    problems = []
    miss = rl.missing_chapters(results, texts)
    if miss:
        problems.append(f"批 {bi} 有 {len(miss)} 章没有评审记录（{miss[:6]}）")
    if unknown:
        problems.append(f"批 {bi} 评审里出现本批不存在的章号 {unknown[:6]}（已忽略）")
    return results, problems


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
    from utils import writing_form as _wf5
    _para_ok = _wf5.applicability(proj).get("para_structure", True)
    pending = [f for f in raw_files
               if not is_usable_output(raw_dir / f.name, checked_dir / f.name,
                                       para_structure=_para_ok)]
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

    # 文体（2026-10-20）：项目声明了 book.form 就按它的判据适用范围走
    from utils import writing_form as _wf
    _app = _wf.applicability(proj)
    if _app.get("warning"):
        print("[stage5] " + _app["warning"])
    print("[stage5] " + _wf.describe(_app))
    if not _app.get("style_prompt_wired"):
        print("[stage5] 说明：本文件体的**写法指导**尚未接入 stage4（判据口径已按文体生效）")

    copied_all = []
    ledger_all = []
    ledger_problems = []
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
        # 中文标点归一化（2026-10-18）：检查阶段的模型也会写出半角引号 / 直角引号。
        # 兜底复制（上面 shutil.copy2）搬来的是 raw 的内容，raw 已在 stage4 归一化过，
        # 再跑一次幂等、零成本；LLM 产出的 checked 则必须在这里清洗。
        for f in batch:
            _norm(checked_dir / f.name)
        # 七维评审账本（2026-10-20）：与修正稿同批判定。**校验的是 raw 原文**——
        # 模型读到的就是 raw，拿 checked 去核对等于让它给自己改过的句子作证。
        batch_results, batch_problems = _verify_ledger(batch, bi, _app)
        for r in batch_results:
            rl.write_ledger(REVIEW_DIR, r)
        ledger_all.extend(batch_results)
        ledger_problems.extend(batch_problems)
        if batch_results:
            done = sum(1 for r in batch_results if r["complete"])
            print(f"[stage5] 七维评审：本批 {len(batch_results)} 章，完整举证 {done} 章")
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

    # 七维评审汇总（2026-10-20）：`data/review/` 是**可回归**的那份（JSON + 表格），
    # check_report.md 仍是给人看的自由文本报告 —— 两者都留，不互相替代。
    if ledger_all:
        ledger_summary = rl.write_summary(REVIEW_DIR, ledger_all)
        mean = ledger_summary["mean"]
        print(f"[stage5] 七维评审账本：{ledger_summary['chapters']} 章，"
              f"完整举证 {ledger_summary['complete']} 章，均分 "
              f"{mean if mean is not None else '—'}")
        print("[stage5] 账本: data/review/ledger.md（逐章 JSON 在 data/review/NN.json）")
        # 审美五问（2026-10-20）：只打印不加入 warnings —— 它是"写得好看吗"，
        # 不是"有没有写错"。审美缺失不该让 stage5 判失败（老账本也都没有这块）。
        _aes = ledger_summary.get("aesthetic") or {}
        if _aes.get("chapters"):
            print("[stage5] 审美五问：%d 章已评，五问齐全 %d 章，均分 %s"
                  % (_aes["chapters"], _aes["complete"],
                     _aes["mean"] if _aes["mean"] is not None else "—"))
        else:
            print("[stage5] 审美五问：本轮未产出（不影响七维判定；"
                  "需要时在 stage5 提示词里要求 aesthetic 字段）")
        incomplete = [r["chapter"] for r in ledger_all if not r["complete"]]
        if incomplete:
            warnings.append(f"{len(incomplete)} 章七维评审不完整（缺维或举证对不上原文）："
                            + "、".join("第%d章" % n for n in incomplete[:10])
                            + ("…" if len(incomplete) > 10 else ""))
    else:
        warnings.append("本轮没有可回归的七维评审（data/review/ 无账本）"
                        "—— 检查结论仍只有自由文本，无法回归")
    if ledger_problems:
        warnings.extend(ledger_problems)

    # 伏笔联动（2026-10-21）：悬空钩子（埋了没推进/没回收）并进"带警告完成"。
    # 零 token、确定性；只报不拦 —— 悬空不等于写错，留给作者在台账里拍板。
    try:
        from utils import foreshadow as _fs
        _fw = _fs.review_warnings()
        _dang = _fw.get("stale") or []
        if _dang:
            warnings.append(
                f"{len(_dang)} 条伏笔悬空（埋设后一直没推进/回收）："
                + "、".join("`%s`(第%d章)" % (r["id"], r["planted"]) for r in _dang[:6])
                + ("…" if len(_dang) > 6 else "")
                + " —— 详见章节页签「伏笔台账」")
    except Exception as _e:                                   # noqa: BLE001
        # 告警是锦上添花，坏了不该拖垮检查主链路；但要出声，不静默。
        print(f"[stage5] 伏笔联动告警组装失败（不影响检查结果）: {type(_e).__name__}: {_e}")

    # gates.review_ledger（默认 warn）：只有显式写 block 才让账本问题判阶段失败。
    # 为什么默认 warn：真实模型的 JSON 合规率还没实测过，先报不拦；等稳态再切 block。
    _ledger_mode = str(((cfg or {}).get("gates") or {}).get("review_ledger", "warn")).lower()
    if _ledger_mode == "block" and (not ledger_all or any(not r["complete"] for r in ledger_all)):
        detail = "；".join(warnings) or "无账本"
        progress.set_stage(5, "failed",
                           error="七维评审未通过（gates.review_ledger=block）：" + detail)
        return False, "stage5 失败：七维评审未通过（gates.review_ledger=block）"

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
    cfg = load_config_yaml("config/system.yaml")
    proj = load_config_yaml("config/project.yaml")
    from utils.progress_manager import ProgressManager
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(cfg, proj, progress, None, None, task_dir=args.task_dir)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
