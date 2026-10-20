# -*- coding: utf-8 -*-
"""阶段 2：整体大纲。

读设定集 → 生成自包含任务文件 → 子会话输出 data/outline/global.md
→ 结构校验 → 标记 stage2 完成（approved=False，等待人工确认，v2 4.1）。
"""
import argparse
import re
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.llm_client import make_client
from utils.file_io import read_text
from utils.template_loader import load_template
from utils.config_io import load_config_yaml

REQUIRED_SECTIONS = ["起", "承", "转", "合"]

# 件3：产出侧空泛（THIN）条目上限。绿标准为「大纲 THIN ≤1/11」，故容差取 1。
THIN_TOLERANCE = 1


def check_global_outline(path):
    """校验 global.md：起承转合四节 + 关键节点 + 预计章节数。返回 (ok, errors)。"""
    text = read_text(path)
    errors = []
    for sec in REQUIRED_SECTIONS:
        if not re.search(rf"^##\s*{sec}", text, re.M):
            errors.append(f"缺少章节: ## {sec}")
    if len(re.findall(r"^##\s*关键节点", text, re.M)) == 0:
        errors.append("缺少 ## 关键节点")
    m = re.search(r"^##\s*预计章节数\s*\n\s*(\d+)", text, re.M)
    if not m or int(m.group(1)) <= 0:
        errors.append("缺少有效的 ## 预计章节数")
    return (not errors, errors)


def check_outline_anchors(path, setting_path="data/setting/setting.json"):
    """产出侧锚点校验（件3）：THIN 条目数超上限即判不合格。返回 (ok, errors)。

    与 `prompts/stage2_global_outline.md` 里的「每节点锚点数下限」硬约束配套：
    提示词负责**引导**，这里负责**拦**。

    判据**复用** `outline_review.review`（确定性、零 token），不在此重造打分逻辑——
    否则「提示词要求的锚点」与「校验器认的锚点」会各写一套、迟早分叉。

    只用于**流水线产出**（run_stage）。结构校验 `check_global_outline` 保持纯净：
    GUI 的「保存大纲 / 精修大纲」也走它，不应因空泛而被拦（那是审阅环节的事）。
    """
    errors = []
    try:
        import outline_review as orv
        res = orv.review(str(path), setting_path) or {}
        summary = res.get("summary", {}) or {}
        thin = int(summary.get("thin", 0))
        total = int(summary.get("total", 0))
        if thin > THIN_TOLERANCE:
            entries = (res.get("nodes", []) or []) + (res.get("plan", []) or [])
            names = [e.get("title", "?") for e in entries if e.get("level") == "THIN"]
            errors.append(
                f"空泛（THIN）条目 {thin}/{total} 条，超过上限 {THIN_TOLERANCE}"
                + ("；例：" + "、".join(names[:3]) if names else "")
                + "。请在关键节点/章节规划里补足具体事件、场景地点与角色行动后重跑")
    except Exception as e:                                      # noqa: BLE001
        print(f"[stage2] 锚点校验跳过（不影响结构校验）: {type(e).__name__}: {e}")
    return (not errors, errors)


def build_task(cfg, proj):
    # A3：大纲阶段**只读 canon 快照**（approved 后冻结），不读活稿 ——
    # 否则改素材会悄悄扰动已经定稿的大纲。没有快照时退回 setting.json。
    from utils.material_state import canon_or_setting
    # 开局方案（2026-10-19）：用户在大纲之前选定的走向，优先级高于 user_outline。
    # 没选就留一句明确的"未选定"，而不是空字符串 —— 空节会让模型以为
    # "这一节被裁掉了"，进而自己发挥。
    from utils.open_variants import load_chosen
    book = proj.get("book", {})
    user_outline = (book.get("user_outline") or "").strip()
    # 设定集摘要内联（2026-10-20，台账三·设定库闭环）：此前只给一个文件路径，
    # 模型读多少不受控 —— 实测 69 KB 设定集里 model 通常只翻到前十几个角色，
    # **伏笔与时间线从未进过大纲**。这里压成有预算的摘要直接内联。
    from utils import setting_brief
    canon_path = canon_or_setting()
    _brief = setting_brief.build(canon_path, _vault_path_if_configured())
    if _brief["error"]:
        print("[stage2] 设定集摘要不可用：" + _brief["error"])
    else:
        print("[stage2] 设定集摘要内联：角色 %d / 地点势力 %d / 伏笔 %d / 时间线 %d / vault 补充 %d（%d 字）"
              % (_brief["stats"]["characters"], _brief["stats"]["world"],
                 _brief["stats"]["fragments"], _brief["stats"]["timeline"],
                 _brief["stats"]["vault_extra"], _brief["stats"]["chars"]))
    _, body = load_template("stage2_global_outline.md", {
        "book_name": book.get("name", "未命名"),
        "target_words": book.get("target_words", 300000),
        "user_outline": user_outline or "（未提供，请基于设定集与素材清单自拟整体大纲）",
        "variant_chosen": (load_chosen()
                           or "（未选定开局方案 —— 按上面的「用户提供的大纲/构思」走）"),
        "setting_brief": (_brief["block"]
                          or "（设定集摘要不可用：%s。请从素材清单与用户构思出发）"
                          % (_brief["error"] or "未生成")),
        "path_setting": Path(canon_path).resolve(),
        "path_manifest": Path("data/setting/materials_manifest.json").resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "path_global_outline": Path("data/outline/global.md").resolve(),
    })
    return body


def _vault_path_if_configured():
    """vault 路径（未配置返回 None）—— 摘要里可以带上"尚未归并的词条"。"""
    try:
        from obsidian_bridge import is_vault_configured, _get_vault_path
        if not is_vault_configured():
            return None
        p = _get_vault_path()
        return str(p) if p else None
    except Exception:                                          # noqa: BLE001
        return None


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or make_client(cfg, "outliner")
    task_dir = task_dir or "data/state/tasks"
    task = client.write_task(task_dir, "stage2_global_outline.md", build_task(cfg, proj))

    result = client.run_task(task)
    if cost and run_id:
        cost.charge_cost(run_id, 2, 0, result)
    if result["exit_code"] != 0:
        progress.set_stage(2, "failed", error="子会话退出码非零")
        return False, "stage2 子会话失败"

    ok, errors = check_global_outline("data/outline/global.md")
    if not ok:
        progress.set_stage(2, "failed", error="结构校验失败: " + "; ".join(errors))
        return False, "stage2 大纲校验失败: " + "; ".join(errors)

    # 件3：产出侧锚点校验（THIN 治理）——结构过关不等于信息密度过关。
    ok_a, errors_a = check_outline_anchors("data/outline/global.md")
    if not ok_a:
        progress.set_stage(2, "failed", error="锚点校验失败: " + "; ".join(errors_a))
        return False, "stage2 大纲锚点校验失败: " + "; ".join(errors_a)

    progress.mark_stage_done(2)          # done 但未 approved（审批门）
    progress.set_approved(2, False)
    if run_id:
        db.log_chapter(run_id, 2, 0, "ok", quality=None)
    print("[stage2] 整体大纲完成，等待人工确认（data/state/progress.json → is_approved(2)）")
    return True, "stage2 完成（待审批）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段2：整体大纲")
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
