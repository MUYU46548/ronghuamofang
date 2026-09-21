# -*- coding: utf-8 -*-
"""设定集补全 CLI（P1.5-2）+ **auto-thin 闭环**（P1.5-3）。

stage2 审批门之前的补全通道：用户对 setting.json 的薄弱项（体检 THIN/WARN）
提出定向补全意见 → 备份当前设定（data/setting/history/setting_vN.json）→
子会话按"仅从素材推断 + llm_inferred 标记"增量补全 → 结构校验 →
体检复评 → 差异摘要。补全不改变审批状态。

## auto-thin 闭环（--auto-thin）

无人工意见时，直接从体检报告取 THIN 清单，**逐轮**补全：
生成定向 feedback（含具体角色名 + 各自缺失维度）→ 补全 → 复评 THIN 数 →
若 THIN 数下降则再来一轮，直到**达标即停 / 无进展即停 / 触达轮次上限**。

设计约束（暮雨原话：「轮次上限 1-2、达标即停、默认关」）：

- **默认关**：`gates.setting_refine_auto = False`。现有流水线行为零变化 ——
  自动补全会改设定集，属"改变用户产物"的动作，必须用户显式开启。
- **轮次上限**：`gates.setting_refine_max_rounds`（默认 1）。上限 2 已足够：
  THIN 的根因多半是**素材本身没写**，再跑也不会有新信息。
- **达标即停**：THIN 数为 0 → 立即停，不做无谓调用。
- **无进展即停**：本轮 THIN 数未下降 → 停（继续跑纯浪费 token，
  且会反复改同一批条目、放大编造风险）。
- **每轮独立备份**：`setting_v{N}.json` 递增，任一轮出问题都能单独回溯。

用法：
  python scripts/setting_refine.py "补充露汐与罗霄的关系往事"
  python scripts/setting_refine.py --feedback "..." --dry-run   # 只备份+生成任务
  python scripts/setting_refine.py --auto-thin                    # 对全部 THIN 批量补全
  python scripts/setting_refine.py --auto-thin --max-rounds 2     # 闭环至多两轮
  python scripts/setting_refine.py --auto-thin --dry-run          # 只预演计划，不跑子会话
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

import material_review as mr
import stage1_consolidate as s1

SETTING = "data/setting/setting.json"
HISTORY_DIR = "data/setting/history"
NORMALIZED = "data/setting/normalized"

# auto-thin 闭环默认轮次上限。1 = 只跑一轮（最保守，避免反复改同一批条目）。
DEFAULT_MAX_ROUNDS = 1


def next_version(history_dir):
    """返回下一个版本号（history/setting_v{N}.json 中最大 N + 1）。"""
    hist = Path(history_dir)
    hist.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in hist.glob("setting_v*.json"):
        m = re.match(r"setting_v(\d+)\.json", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def backup_current(setting_path, history_dir, version):
    """复制当前设定集到 history 目录。返回备份路径。"""
    hist = Path(history_dir)
    hist.mkdir(parents=True, exist_ok=True)
    dest = hist / f"setting_v{version}.json"
    shutil.copy2(setting_path, dest)
    return dest


def build_task(cfg, proj, feedback, version, review_thin):
    """生成补全任务文件内容（自包含：嵌入当前设定集全文 + 体检 THIN 清单）。"""
    current = read_text(SETTING)
    meta, body = load_template("stage1_refine.md", {
        "version": version,
        "feedback": feedback,
        "review_thin": review_thin,
        "path_setting": Path(SETTING).resolve(),
        "path_normalized": Path(NORMALIZED).resolve(),
    })
    # current_setting 最后替换，避免设定内容中的 {{...}} 被后续替换误伤
    body = body.replace("{{current_setting}}", current)
    return body


def diff_summary(before, after):
    """对比补全前后 setting.json，输出人类可读差异摘要。"""
    lines = []
    b_chars = {c.get("name"): c for c in before.get("characters", []) if isinstance(c, dict)}
    a_chars = {c.get("name"): c for c in after.get("characters", []) if isinstance(c, dict)}
    for name, ac in a_chars.items():
        if name not in b_chars:
            lines.append(f"+ 新增角色条目: {name}")
            continue
        bc = b_chars[name]
        inferred = []
        for key in ac:
            if key == "llm_inferred":
                continue
            if isinstance(ac.get(key), bool) and key not in bc:
                continue
            if key not in bc:
                inferred.append(key)
            elif ac[key] != bc[key]:
                inferred.append(key + "（修改）")
        if inferred:
            lines.append(f"~ {name}: 补强字段 {('、'.join(inferred))}")
    # 全局限定
    for key in ("world", "plot_fragments", "timeline"):
        if key in after and key in before and after[key] != before[key]:
            lines.append(f"~ {key}: 内容有变化")
    return lines


def run_refine(cfg, proj, feedback, client=None, task_dir=None, dry_run=False):
    if not Path(SETTING).exists():
        return False, f"设定集不存在: {SETTING}（先跑 stage1）"

    version = next_version(HISTORY_DIR)
    backup = backup_current(SETTING, HISTORY_DIR, version)
    print(f"[setting_refine] 已备份当前设定 → {backup}（v{version}）")

    # 体检复评（补全前基线）
    try:
        char_results, world_items, thin_names, warn_names = mr.review(SETTING, NORMALIZED)
        review_thin = "、".join(thin_names) if thin_names else "（无 THIN，见用户意见）"
    except Exception as e:
        review_thin = f"（体检失败: {e}）"

    client = client or make_client(cfg, "default")
    task_dir = task_dir or "data/state/tasks"
    task = client.write_task(task_dir, "stage1_refine.md",
                             build_task(cfg, proj, feedback, version, review_thin))
    print(f"[setting_refine] 任务文件: {task}")

    if dry_run:
        print("[setting_refine] --dry-run：未调用子会话，补全未执行")
        return True, "dry-run（仅备份 + 任务文件）"

    result = client.run_task(task)
    if result["exit_code"] != 0:
        return False, "补全子会话失败"

    ok, errors = s1.validate_setting(SETTING)
    if not ok:
        return False, "补全后设定集校验失败: " + "; ".join(errors)

    # 差异摘要 + 体检复评
    before = json.loads(read_text(str(backup)))
    after = json.loads(read_text(SETTING))
    diffs = diff_summary(before, after)
    print("[setting_refine] 差异摘要：")
    for d in diffs:
        print(f"  {d}")

    try:
        char_results, world_items, thin_names, warn_names = mr.review(SETTING, NORMALIZED)
        mr.print_summary(char_results, world_items, thin_names, warn_names)
    except Exception as e:
        print(f"[setting_refine] 复评失败（不影响补全结果）: {e}")

    print("[setting_refine] 补全完成。注意：审批状态未变，"
          "满意后请执行 approve.py --stage 2 确认")
    return True, f"补全完成（v{version}）"


# ---------------------------------------------------------------- auto-thin 闭环

def thin_report(setting_path=SETTING, normalized_dir=NORMALIZED):
    """跑体检，返回 (thin_names, thin_detail)。异常时返回 (None, 错误信息)。

    `thin_detail` 是「角色名 → 缺失维度列表」的映射，用于**生成定向 feedback**：
    只说「补全 THIN 角色」模型会泛泛地加形容词；点名「露汐 缺 ability、
    relations」才能让它去素材里找对应线索。
    """
    try:
        char_results, _, thin_names, _ = mr.review(setting_path, normalized_dir)
    except Exception as e:                       # noqa: BLE001
        return None, f"体检失败: {e}"
    detail = {r["name"]: r.get("thin_dims", []) for r in char_results
              if r["level"] == mr.THIN}
    return thin_names, detail


def build_auto_feedback(thin_names, thin_detail, round_no, max_rounds):
    """把 THIN 清单渲染成**定向**补全意见（取代原来的硬编码占位串）。

    ⚠️ 旧实现是 `"请对以下碎片角色进行设定补全（见体检报告）"` —— 既没有角色名，
    也没说明缺什么维度，模型只能猜。实测这种 feedback 的补全命中率极低。
    """
    lines = [
        f"（自动补全第 {round_no}/{max_rounds} 轮）",
        "以下角色经体检判定为**碎片角色**（6 维中仅满足 ≤2 维）。",
        "请逐个补全其缺失维度，**只从素材推断**，新增/补强字段一律标 `llm_inferred: true`，",
        "`source` 指向素材文件名。素材中找不到出处的维度**留空不写**，禁止编造。",
        "",
        "待补全清单（角色名 → 缺失维度）：",
    ]
    for name in thin_names:
        dims = thin_detail.get(name) or []
        dim_txt = "、".join(dims) if dims else "（维度未知，请通读素材补齐）"
        lines.append(f"  - {name} → 缺 {dim_txt}")
    lines.append("")
    lines.append("完成后自查：仍是合法 JSON、四顶层键齐全、未删除既有条目、"
                 "未触碰 locked 字段。")
    return "\n".join(lines)


def run_auto_thin(cfg, proj, client=None, task_dir=None,
                  max_rounds=DEFAULT_MAX_ROUNDS, dry_run=False):
    """auto-thin 闭环：体检 → 定向补全 → 复评 → 直到达标/无进展/触顶。

    返回 (ok, msg, stats)。stats 供 orchestrator 与测试断言使用：
      {rounds, thin_before, thin_after, reason, versions}
    """
    stats = {"rounds": 0, "thin_before": None, "thin_after": None,
             "reason": "", "versions": [], "dry_run": bool(dry_run)}

    if not Path(SETTING).exists():
        stats["reason"] = "no_setting"
        return False, f"设定集不存在: {SETTING}（先跑 stage1）", stats

    thin_names, detail = thin_report()
    if thin_names is None:
        stats["reason"] = "review_failed"
        return False, str(detail), stats
    stats["thin_before"] = len(thin_names)
    stats["thin_after"] = len(thin_names)

    # 达标即停：没有碎片角色就不必开跑（零调用短路）
    if not thin_names:
        stats["reason"] = "already_ok"
        print("[setting_refine] auto-thin：体检无 THIN 条目，无需补全（零调用）")
        return True, "无 THIN 条目，未执行补全", stats

    max_rounds = max(1, int(max_rounds))
    client = client or make_client(cfg, "default")
    task_dir = task_dir or "data/state/tasks"

    for rnd in range(1, max_rounds + 1):
        current_thin, current_detail = thin_report()
        if current_thin is None:
            stats["reason"] = "review_failed"
            return False, str(current_detail), stats
        if not current_thin:                       # 中途达标
            stats["reason"] = "reached_ok"
            break

        feedback = build_auto_feedback(current_thin, current_detail, rnd, max_rounds)
        print(f"\n[setting_refine] ==== auto-thin 第 {rnd}/{max_rounds} 轮 "
              f"（THIN {len(current_thin)} 个：{'、'.join(current_thin[:8])}"
              f"{'…' if len(current_thin) > 8 else ''}）====")

        before_n = len(current_thin)
        ok, msg = run_refine(cfg, proj, feedback, client=client,
                             task_dir=task_dir, dry_run=dry_run)
        stats["rounds"] = rnd

        if not ok:
            stats["reason"] = "refine_failed"
            stats["thin_after"] = before_n
            return False, f"第 {rnd} 轮补全失败: {msg}", stats

        if dry_run:
            stats["reason"] = "dry_run"
            stats["thin_after"] = before_n
            return True, f"dry-run：计划 {max_rounds} 轮，已备份+生成第 1 轮任务", stats

        # 记录本轮备份版本（每轮独立可回溯）
        try:
            stats["versions"].append(next_version(HISTORY_DIR) - 1)
        except Exception:                          # noqa: BLE001
            pass

        after_thin, _ = thin_report()
        if after_thin is None:
            stats["reason"] = "review_failed"
            return False, "补全后体检失败", stats
        stats["thin_after"] = len(after_thin)
        print(f"[setting_refine] 第 {rnd} 轮结果：THIN {before_n} → {len(after_thin)}")

        if not after_thin:
            stats["reason"] = "reached_ok"
            break
        if len(after_thin) >= before_n:
            # 无进展即停：再跑大概率还是改同一批条目、放大编造风险
            stats["reason"] = "no_progress"
            print(f"[setting_refine] 第 {rnd} 轮无进展（THIN 未减少），停止闭环")
            break
    else:
        # for-else：跑满 max_rounds 仍未达标
        stats["reason"] = "max_rounds"

    if not stats["reason"]:
        stats["reason"] = "max_rounds"

    summary = (f"auto-thin 结束（{stats['rounds']} 轮，"
               f"THIN {stats['thin_before']} → {stats['thin_after']}，"
               f"原因：{stats['reason']}）")
    return True, summary, stats


def main():
    parser = argparse.ArgumentParser(description="NovelForge 设定集补全（增量，不重跑 stage1）")
    parser.add_argument("feedback", nargs="?", default=None,
                        help="补全意见（自然语言）")
    parser.add_argument("--feedback", dest="feedback_opt", default=None,
                        help="补全意见（与位置参数二选一）")
    parser.add_argument("--auto-thin", action="store_true",
                        help="auto-thin 闭环：按体检 THIN 清单分轮补全，达标/无进展即停")
    parser.add_argument("--max-rounds", type=int, default=None,
                        help=f"auto-thin 轮次上限（默认取 gates.setting_refine_max_rounds，"
                             f"再兜底 {DEFAULT_MAX_ROUNDS}）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只备份 + 生成任务文件，不调用子会话")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()

    feedback = args.feedback_opt or args.feedback
    if not feedback and not args.auto_thin:
        print("用法: python scripts/setting_refine.py \"补全意见\" [--dry-run] 或 --auto-thin")
        return 1

    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))

    if args.auto_thin:
        # 轮次上限优先级：CLI 显式指定 > gates 配置 > 代码默认
        gates = (cfg.get("gates", {}) or {})
        max_rounds = args.max_rounds
        if max_rounds is None:
            max_rounds = int(gates.get("setting_refine_max_rounds",
                                       DEFAULT_MAX_ROUNDS))
        print(f"[setting_refine] auto-thin 模式（轮次上限 {max_rounds}）")
        ok, msg, stats = run_auto_thin(cfg, proj, task_dir=args.task_dir,
                                       max_rounds=max_rounds,
                                       dry_run=args.dry_run)
        print(f"[setting_refine] {msg}")
        if stats.get("reason") == "no_setting":
            return 1
        print("[setting_refine] 注意：审批状态未变，"
              "满意后请执行 approve.py --stage 2 确认")
        return 0 if ok else 1

    ok, msg = run_refine(cfg, proj, feedback, task_dir=args.task_dir,
                         dry_run=args.dry_run)
    print(f"[setting_refine] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
