# -*- coding: utf-8 -*-
"""开工方向建议（2026-09-21）。

## 回答什么

「我迭代了 N 轮，现在到底该继续改、还是该开工？如果继续，改哪儿？」

`outline_review --trend` 已经给出**收敛判定**（done/stalled/improving/mixed），
但那只说「有没有进展」。本模块把它变成**可选择的行动方案**：每个方案含
依据、目标条目、缺失维度、**可直接执行的下一步**、代价与取舍。

## 为什么确定性生成，不用 LLM

1. 依据全部来自 `outline_review` 的可验证判据（issues / thin_dims / flags），
   LLM 复述只会引入不可验证的措辞；
2. 零 token —— 「该不该开工」这个问题本身不该花钱；
3. 可测试 —— 方案与判据的映射可以被反向验证。

## 方案池（按优先级排序，不必全选）

| id | 触发条件 | 含义 |
|---|---|---|
| `fix_structure` | `issues` 非空 | 结构性缺陷（如章节规划条数 ≠ 预计章节数）——**必须先修**，否则开工必亏 |
| `rollback` | 本轮退化 | 回退到上一版 |
| `densify` | 有 THIN 或 WARN 条目 | 逐条目补缺失维度（给出每个条目的具体补法）。**仅 WARN 时标为「可选优化」** |
| `change_approach` | 停滞 | 同方向迭代已无收益 → 换角度 / 补素材（给出素材方向） |
| `start_writing` | 始终可选项 | 开工前的人工确认清单 |

**推荐规则按严重度短路**（不是固定顺序）：结构问题 > 本轮退化 > **有 THIN 条目** >
停滞 > 开工。所以「只有 WARN、收敛判定已是 done」时推荐会落到**开工** ——
否则会把「可以开工」这个最重要的结论压掉。

`recommended` 给出推荐方案 id，但**决定权在用户**。
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import outline_review as ov                          # noqa: E402
from utils.file_io import read_text                  # noqa: E402

GLOBAL = "data/outline/global.md"
HISTORY_DIR = "data/outline/history"
SETTING = "data/setting/setting.json"

# 缺失维度 → 补法提示 + 补素材方向（确定性映射，不猜内容）
DIM_HINT = {
    "身份/定位": ("补一句这个角色/势力在本章承担什么功能", "角色设定"),
    "性格特征": ("补 2~3 条具体行为倾向（每条 6 字以上）", "角色设定"),
    "能力/特殊设定": ("补一条可被情节用上的具体能力或规则", "角色设定 / 世界观"),
    "关系网": ("点明与另一名已登场角色的关系", "角色设定"),
    "素材覆盖": ("该条目在素材里出现太少 —— 属**素材缺口**，写正文前需补素材", "原始素材"),
    "无场景锚点": ("补一个具体地点（已登记的或新地点）", "地点设定"),
    "无具体事件动词": ("把概述改成「谁 对 谁 做了什么」", "—"),
    "无冲突/张力词": ("点明这一章的阻力/代价是什么", "—"),
    # 2026-09-21 补：此前没有这条，导致「只被判篇幅过短」的条目拿不到任何提示，
    # 只能落到兜底文案「按体检缺失维度补全信息」——太笼统、不可执行。
    "篇幅过短": ("把概述写具体：加时间/地点/关键动作，至少 60 字", "—"),
}

# 素材缺口的聚合归类（用于 stalled 时的「补素材」建议）
MATERIAL_DIM = "素材覆盖"

# 结构性问题 → 可执行改法。逐条 pattern 匹配，匹配不到就原样呈现（不编造改法）。
_ISSUE_FIX = (
    (re.compile(r"关键节点\s*(\d+)\s*条\s*<\s*预计章节数\s*(\d+)"),
     lambda m: (f"补齐 {int(m.group(2)) - int(m.group(1))} 个节点的驱动事件"
                f"（或把预计章节数下调为 {m.group(1)}）")),
    (re.compile(r"章节规划\s*(\d+)\s*条\s*≠\s*预计章节数\s*(\d+)"),
     lambda m: (f"把章节规划调整到 {m.group(2)} 条"
                f"（或把预计章节数改为 {m.group(1)}）")),
    (re.compile(r"缺少有效的\s*##\s*预计章节数"),
     lambda m: "补一行 `## 预计章节数` 加一个正整数"),
)


def _issue_action(issue):
    """把一条结构性问题变成「问题 + 可执行改法」。

    ⚠️ 2026-09-21：首版把 issue 原文同时塞进 `target` 和 `suggested_feedback`，
    输出成「问题：问题」的重复行。现 `target` 是精简问题，`feedback` 是**改法**。
    """
    for rx, fix in _ISSUE_FIX:
        m = rx.search(issue)
        if m:
            return {"target": issue.split("：", 1)[0][:48], "dims": [], "hints": [],
                    "suggested_feedback": fix(m),
                    "next_step": "整篇精修（refine_outline）"}
    return {"target": issue[:48], "dims": [], "hints": [],
            "suggested_feedback": "（该问题无自动改法，请在 GUI 大纲页签手动处理）",
            "next_step": "手动编辑或整篇精修（refine_outline）"}


def _entry_actions(nodes, plan, struct=None):
    """把体检里的空泛条目变成逐条可执行动作。

    ⚠️ 2026-09-21 修：`outline_review.review()` 的条目**没有 `id`**
    （只有 `title`，如「节点1」）；`id` 是 `utils.outline_struct.parse_global()`
    才产出的（`node1` / `ch1` 这类）。首版直接读 `e.get("id")` 得到 None，
    拼出的命令是 `{"node_id":"None"}` —— **不可执行**。
    现改为用 `outline_struct` 的解析结果**按出现顺序对齐**补上 id
    （与 `outline_panel._decorate` 同一假设：两处解析的条目顺序一致）。
    """
    node_ids = [e.get("id") for e in (struct or {}).get("nodes", [])]
    plan_ids = [e.get("id") for e in (struct or {}).get("plan", [])]

    acts = []
    for kind, entries, ids in (("node", nodes, node_ids), ("plan", plan, plan_ids)):
        for i, e in enumerate(entries):
            if e.get("level") not in ("THIN", "WARN"):
                continue
            eid = ids[i] if i < len(ids) else None
            dims = list(e.get("reasons") or [])
            hints = []
            for d in dims:
                for k, (hint, mat) in DIM_HINT.items():
                    if k in d:
                        hints.append({"dim": d, "hint": hint, "material": mat})
                        break
            fb = "；".join(h["hint"] for h in hints) or "按体检缺失维度补全信息"
            if eid:
                nxt = (f'POST /refine/outline/node '
                       f'{{"node_id":"{eid}","feedback":"{fb[:40]}…"}}')
            else:
                # 对不上 id 时**如实说明**，不给一条跑不通的命令
                nxt = ("无法定位条目 id（outline_struct 解析结果与体检条目数不一致）"
                       "—— 请在 GUI 大纲页签里选中该条目手动精修")
            acts.append({
                "kind": kind,
                "target": e.get("title") or eid,
                "entry_id": eid,
                "level": e.get("level"),
                "dims": dims,
                "hints": hints,
                "suggested_feedback": fb,
                "next_step": nxt,
            })
    return acts


def advise(global_path=GLOBAL, setting_path=SETTING, history_dir=HISTORY_DIR,
           window=3):
    """生成开工方向建议。返回 dict（可 JSON 化）。

    返回 {
      verdict, note,             # 收敛判定（来自 outline_review）
      recommended,               # 推荐方案 id
      options: [...],            # 按优先级排序的方案
      metrics: {...},            # 当前体检指针
      has_backup: bool,
    }
    """
    if not Path(global_path).exists():
        return {"error": f"整体大纲不存在: {global_path}（先跑 stage2）"}

    review = ov.review(global_path, setting_path)
    series = ov.review_series(global_path, setting_path, history_dir=history_dir)
    verdict, note = ov.convergence_verdict(series, window=window)
    cmp = ov.compare_with_previous(global_path, setting_path, history_dir=history_dir)
    s = review["summary"]
    issues = review.get("issues") or []

    nodes = [e for e in review["nodes"]]
    plan = [e for e in review["plan"]]
    # 取条目 id（review 的条目没有 id，见 `_entry_actions` 的说明）
    struct = None
    try:
        from utils import outline_struct as osr
        struct = osr.parse_global(read_text(global_path))
    except Exception:                          # noqa: BLE001
        struct = None
    actions = _entry_actions(nodes, plan, struct)
    # 只有 WARN 条目的 densify 属「可选优化」；有 THIN 才是「该修」
    thin_actions = [a for a in actions if a.get("level") == "THIN"]

    options = []

    # ---- 1) 结构性缺陷：必须先修 ----
    if issues:
        options.append({
            "id": "fix_structure",
            "title": f"先修结构：{len(issues)} 项结构性问题",
            "why": "这些问题会让大纲无法自洽（例如章节规划条数 ≠ 预计章节数），"
                   "带着它们开工必然在写作阶段返工。",
            "actions": [_issue_action(i) for i in issues],
            "llm_calls": 1,
            "tradeoff": "整篇精修会重写全文，代价高但能一次修掉全部结构问题。",
        })

    # ---- 2) 本轮退化 → 回退 ----
    if cmp and cmp["verdict"] == "regressed":
        v = cmp["from_version"]
        options.append({
            "id": "rollback",
            "title": f"回退到 v{v}（本轮变差了）",
            "why": f"问题数/碎片数从 {cmp['prev']['key']} 升到 {cmp['cur']['key']}，"
                   f"本轮修改没有收益。",
            "actions": [{"target": f"v{v}", "dims": [], "hints": [],
                         "suggested_feedback": "",
                         "next_step": f"POST /outline/restore {{\"version\":{v}}}"}],
            "llm_calls": 0,
            "tradeoff": "回退会丢掉本轮所有改动（含其中可能有用的部分）——"
                        "可在回退前先看一眼对比稿。",
        })

    # ---- 3) 补密度：逐条目 ----
    if actions:
        dim_counter = {}
        for a in actions:
            for h in a["hints"]:
                dim_counter[h["dim"]] = dim_counter.get(h["dim"], 0) + 1
        material_gap = [a for a in actions
                        if any(MATERIAL_DIM in d for d in a["dims"])]
        why = (f"体检有 {len(actions)} 个条目信息密度不足"
               f"（{s['thin']} 个 THIN / {s['warn']} 个 WARN），"
               f"这是当前唯一拉低结论的因素。")
        if material_gap:
            why += (f" 其中 {len(material_gap)} 个是**素材缺口**（素材里出现太少），"
                    f"精修治不了，需要补素材。")
        options.append({
            "id": "densify",
            "title": (f"补密度：逐条目精修 {len(actions)} 处"
                      if thin_actions else
                      f"可选优化：{len(actions)} 个 WARN 条目（非必需）"),
            "why": why,
            "actions": actions,
            "llm_calls": len([a for a in actions if a["entry_id"]]),
            "tradeoff": "逐条目精修比整篇精修便宜、可控、可逐条回退，"
                        "但要跑多次调用；适合「大部分条目已经不错」的情况。"
                        + ("" if thin_actions else
                           " 注意：本轮只有 WARN 条目（无 THIN），"
                           "体检已达标 —— 这属于锦上添花，不是开工前提。"),
        })

    # ---- 4) 停滞 → 换策略 ----
    if verdict == "stalled":
        mat_dims = [d for d in (dim_counter if actions else {}) if MATERIAL_DIM in d]
        material_hint = ("素材缺口集中在「素材覆盖」维度 —— "
                         "建议往 materials/raw/ 补这几条对应的原始材料，"
                         "而不是继续让模型复述已有信息。"
                         if mat_dims else
                         "若继续同方向提意见只会反复重写措辞，"
                         "建议换角度（调整母题/角色动机/结局取向）或先补素材。")
        options.append({
            "id": "change_approach",
            "title": "换策略：同方向迭代已无收益",
            "why": note,
            "actions": [{"target": "迭代策略", "dims": [], "hints": [],
                         "suggested_feedback": material_hint,
                         "next_step": "改角度提意见，或补素材后重跑 stage1"}],
            "llm_calls": 1,
            "tradeoff": "换角度可能推翻已确认的部分结构；补素材则要重跑 stage1"
                        "（会重建设定集，注意已自动备份）。",
        })

    # ---- 5) 开工（始终可选）----
    ready = (not issues) and s["thin"] == 0
    options.append({
        "id": "start_writing",
        "title": "直接开工" + ("（体检已达标）" if ready else ""),
        "why": ("体检无结构性问题、无空泛条目 —— 可以进入逐章大纲。"
                if ready else
                f"当前仍有 {len(issues)} 项结构性问题 / {s['thin']} 个空泛条目。"
                "若你判断这些不影响主线，可以直接开工 —— 但它们是已知的返工风险。"),
        "actions": [{
            "target": "开工前确认清单",
            "dims": [], "hints": [], "suggested_feedback": "",
            "next_step": "1) 通读 global.md 确认主线/结局；"
                         "2) 确认预计章节数与项目配置一致；"
                         "3) approve.py --stage 2 通过审批门",
        }],
        "llm_calls": 0,
        "tradeoff": "开工后再回头改大纲，代价远高于现在改（下游已有逐章大纲/正文）。",
    })

    # 推荐规则（按「严重度」短路，不是简单的固定顺序）：
    #   结构性问题 > 本轮退化 > **有 THIN 条目** > 停滞 > 开工
    #
    # ⚠️ 2026-09-21 修：首版用固定顺序 "densify 恒优先于 start_writing"，
    # 于是「只有 WARN 条目」时也推荐 densify —— 而收敛判定已是 `done`
    # （无结构性问题、无 THIN），推荐「继续补密度」与判定自相矛盾，
    # 把「可以开工」这个最重要的结论压掉了。
    # 现在只有**存在 THIN** 时 densify 才参与推荐；只有 WARN 时它仍是
    # 一个可选项（标为「可选优化」），但推荐落到 start_writing。
    ids = {o["id"] for o in options}
    if "fix_structure" in ids:
        recommended = "fix_structure"
    elif "rollback" in ids:
        recommended = "rollback"
    elif thin_actions and "densify" in ids:
        recommended = "densify"
    elif "change_approach" in ids and verdict == "stalled":
        recommended = "change_approach"
    else:
        recommended = "start_writing"

    # 排序：推荐项置顶，其余按严重度链
    order = ["fix_structure", "rollback", "densify", "change_approach", "start_writing"]
    options.sort(key=lambda o: (0 if o["id"] == recommended
                                else 1,
                                order.index(o["id"]) if o["id"] in order else 99))

    return {
        "verdict": verdict,
        "note": note,
        "recommended": recommended,
        "options": options,
        "has_backup": ov.latest_backup(history_dir) is not None,
        "metrics": {
            "total": s["total"], "ok": s["ok"], "warn": s["warn"], "thin": s["thin"],
            "issues": len(issues), "avg_score": series[-1]["avg_score"] if series else 0,
            "expected_chapters": review.get("expected_chapters"),
            "verdict": s["verdict"],
        },
    }


def _avg_round_cost():
    """从账本读「大纲迭代」历史平均每轮费用。无记录返回 (None, 0)。"""
    try:
        import sqlite3
        p = Path("logs/runs.db")
        if not p.exists():
            return None, 0
        conn = sqlite3.connect(str(p))
        row = conn.execute(
            "SELECT AVG(cost_yuan), COUNT(*) FROM cost_log"
            " WHERE stage=2 AND COALESCE(chapter,0)>0").fetchone()
        conn.close()
        if not row or not row[1]:
            return None, 0
        return float(row[0] or 0), int(row[1])
    except Exception:                                  # noqa: BLE001
        return None, 0


def format_text(adv):
    """人可读输出。"""
    if adv.get("error"):
        return f"[outline_advisor] {adv['error']}"
    m = adv["metrics"]
    avg, n = _avg_round_cost()
    lines = [
        "[outline_advisor] 开工方向建议",
        f"  当前体检: {m['total']} 条目（OK {m['ok']} / WARN {m['warn']} / "
        f"THIN {m['thin']}）· 结构性问题 {m['issues']} · 平均分 {m['avg_score']}"
        f" · 单版体检 {m['verdict']}",
        f"  收敛判定: {adv['verdict']} —— {adv['note']}",
        "",
        f"  可选方向（推荐：{adv['recommended']}）:",
    ]
    for i, o in enumerate(adv["options"], 1):
        rec = " ★推荐" if o["id"] == adv["recommended"] else ""
        cost = ""
        if o["llm_calls"]:
            if avg:
                cost = f" · 约 {o['llm_calls']} 次调用（按历史均值约 {avg * o['llm_calls']:.4f} 元）"
            else:
                cost = f" · 约 {o['llm_calls']} 次 LLM 调用"
        else:
            cost = " · 零调用"
        lines.append(f"  [{i}] {o['title']}{rec}{cost}")
        lines.append(f"      {o['why']}")
        for a in o["actions"][:6]:
            if a.get("target"):
                fb = a.get("suggested_feedback") or ""
                lines.append(f"      · {a['target']}" + (f"：{fb}" if fb else ""))
        if len(o["actions"]) > 6:
            lines.append(f"      · …（其余 {len(o['actions']) - 6} 条同上）")
        lines.append(f"      取舍：{o['tradeoff']}")
        if o["actions"]:
            lines.append(f"      下一步：{o['actions'][0].get('next_step', '')}")
        lines.append("")
    lines.append("  决定权在你 —— 上述是依据体检结果推出的候选方向，不是自动执行。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="开工方向建议（确定性，零 token）")
    ap.add_argument("--outline", default=GLOBAL)
    ap.add_argument("--setting", default=SETTING)
    ap.add_argument("--history", default=HISTORY_DIR)
    ap.add_argument("--window", type=int, default=3, help="收敛判定看最近几轮")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    adv = advise(args.outline, args.setting, args.history, window=args.window)
    if args.json:
        print(json.dumps(adv, ensure_ascii=False, indent=1))
    else:
        print(format_text(adv))
    return 0 if not adv.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
