# -*- coding: utf-8 -*-
"""素材完备度体检 CLI（P1.5-1）。

确定性、零成本：对 setting.json 的角色条目按 6 维打分，
并检查 world/plot_fragments/timeline 的完备性，输出体检报告
（data/setting/material_review.md）与终端摘要。

定位：stage1 归并成功后自动生成；供用户在 stage2 审批前判断
"设定集是否够厚、哪些角色是碎片"，引导是否跑 setting_refine.py 补全。

用法：
  python scripts/material_review.py                       # 默认路径
  python scripts/material_review.py --setting <path> --out <path>
  python scripts/material_review.py --json               # 输出机器可读摘要（供 orchestrator 判断 THIN）
"""
import argparse
import json
import re
import sys
from pathlib import Path

from utils.file_io import read_text, write_text

# ---------------------------------------------------------------- 维度定义

DIMENSIONS = [
    ("identity", "身份/定位", "role 存在且 ≥8 字"),
    ("personality", "性格特征", "traits ≥3 条且每条 ≥6 字"),
    ("ability", "能力/特殊设定", "文本含能力词（能力/使用/掌控/牵引/冰封/战斗等）"),
    ("relations", "关系网", "条目文本提到其他角色名"),
    ("coverage", "素材覆盖", "角色名在 normalized 素材中出现 ≥3 次"),
    ("source", "素材溯源", "条目带 source 字段"),
]

ABILITY_WORDS = (
    "能力", "使用", "掌控", "牵引", "冰封", "战斗", "剑", "法术", "魔法",
    "治疗", "侦查", "操控", "之力", "通晓", "擅长",
)

THIN = "THIN"
WARN = "WARN"
OK = "OK"


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def review_characters(setting, materials_text, all_names):
    """对每个角色条目 6 维打分。返回 {角色名: {维度: bool, score, thin_dims}} 列表。"""
    results = []
    for item in setting.get("characters", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("id") or "?"
        blob = _norm(json.dumps(item, ensure_ascii=False))
        text = _norm(item.get("role", "") + " " + " ".join(item.get("traits", [])))

        scores = {}
        # 1 身份/定位
        role = _norm(item.get("role", ""))
        scores["identity"] = len(role) >= 8
        # 2 性格特征
        traits = [t for t in item.get("traits", []) if isinstance(t, str)]
        scores["personality"] = len(traits) >= 3 and all(len(_norm(t)) >= 6 for t in traits)
        # 3 能力/特殊设定
        scores["ability"] = any(w in text for w in ABILITY_WORDS)
        # 4 关系网：条目文本提到其他角色名
        others = [n for n in all_names if n and n != name and _norm(n) in text]
        scores["relations"] = len(others) >= 1
        # 5 素材覆盖：角色名在素材中出现次数
        counts = sum(t.count(_norm(name)) for t in materials_text)
        scores["coverage"] = counts >= 3
        # 6 素材溯源
        scores["source"] = bool(item.get("source"))

        score = sum(1 for v in scores.values() if v)
        thin_dims = [DIMENSIONS[i][1] for i, v in enumerate(scores.values()) if not v]
        level = OK if score >= 4 else (WARN if score == 3 else THIN)
        results.append({
            "name": name, "id": item.get("id", ""), "role": role or "(无角色定位)",
            "score": score, "level": level, "scores": scores,
            "thin_dims": thin_dims, "others": others,
            "counts": counts, "source": bool(item.get("source")),
        })
    return results


def review_world(setting):
    """world / plot_fragments / timeline 完备性。返回 (条目列表, 统计)。"""
    world = setting.get("world", {})
    if isinstance(world, dict):
        world_keys = len(world)
        world_notes = len(_norm(json.dumps(world, ensure_ascii=False)))
    else:
        world_keys, world_notes = 0, 0

    frags = setting.get("plot_fragments", [])
    if not isinstance(frags, list):
        frags = []
    timeline = setting.get("timeline", [])
    if not isinstance(timeline, list):
        timeline = []

    items = []
    items.append(("world 键数", world_keys, "OK" if world_keys >= 3 else "WARN" if world_keys >= 1 else "THIN"))
    items.append(("plot_fragments 条数", len(frags), "OK" if len(frags) >= 3 else "WARN" if len(frags) >= 1 else "THIN"))
    items.append(("timeline 事件数", len(timeline), "OK" if len(timeline) >= 2 else "WARN" if len(timeline) >= 1 else "THIN"))
    return items


def review(setting_path, normalized_dir):
    """主入口：返回 (char_results, world_items, thin_names, warn_names)。"""
    setting = json.loads(read_text(setting_path))
    all_names = [c.get("name") for c in setting.get("characters", []) if isinstance(c, dict) and c.get("name")]

    # 收集 normalized 素材全文（若目录不存在则空）
    materials_text = []
    norm_dir = Path(normalized_dir)
    if norm_dir.is_dir():
        for p in sorted(norm_dir.glob("*.md")):
            try:
                materials_text.append(_norm(read_text(p)))
            except Exception:
                continue

    char_results = review_characters(setting, materials_text, all_names)
    world_items = review_world(setting)
    thin_names = [r["name"] for r in char_results if r["level"] == THIN]
    warn_names = [r["name"] for r in char_results if r["level"] == WARN]
    return char_results, world_items, thin_names, warn_names


def format_report(char_results, world_items, thin_names, warn_names, setting_path, normalized_dir):
    lines = []
    lines.append("# 素材完备度体检报告（material_review）")
    lines.append("")
    lines.append(f"- 生成时间: （脚本运行时）")
    lines.append(f"- 设定集: `{setting_path}`")
    lines.append(f"- 素材目录: `{normalized_dir}`")
    lines.append(f"- 判定规则: ≥4 维 OK=厚实 / 3 维 OK=WARN / ≤2 维 OK=THIN（碎片）")
    lines.append("")
    lines.append("## 角色维度评分")
    lines.append("")
    lines.append("| 角色 | 评分 | 判定 | 身份 | 性格 | 能力 | 关系网 | 素材覆盖 | 溯源 |")
    lines.append("|------|------|------|------|------|------|--------|----------|------|")
    for r in char_results:
        s = r["scores"]
        lines.append(
            f"| {r['name']} | {r['score']}/6 | {r['level']} | "
            f"{'✅' if s['identity'] else '❌'} | {'✅' if s['personality'] else '❌'} | "
            f"{'✅' if s['ability'] else '❌'} | {'✅' if s['relations'] else '❌'} | "
            f"{'✅' if s['coverage'] else '❌'} | {'✅' if s['source'] else '❌'} |"
        )
    lines.append("")
    if char_results:
        lines.append("### 薄弱项明细")
        lines.append("")
        for r in char_results:
            if r["level"] == THIN or r["level"] == WARN:
                lines.append(f"- **{r['name']}**（{r['score']}/6，{r['level']}）：{r['role']}")
                lines.append(f"  - 缺失维度: {('、'.join(r['thin_dims']) if r['thin_dims'] else '无')}")
                if not r["scores"]["relations"]:
                    lines.append(f"  - 关系网空：条目文本未提到其他角色名")
                if r["counts"] < 3:
                    lines.append(f"  - 素材覆盖弱：normalized 素材仅出现 {r['counts']} 次（≥3 为 OK）")
    lines.append("")
    lines.append("## 世界观完备性")
    lines.append("")
    lines.append("| 项目 | 数值 | 判定 |")
    lines.append("|------|------|------|")
    for name, val, lvl in world_items:
        lines.append(f"| {name} | {val} | {lvl} |")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    if thin_names:
        lines.append(f"- **存在 {len(thin_names)} 个碎片角色**（{('、'.join(thin_names))}）："
                     f"建议在阶段2审批前跑 `python scripts/setting_refine.py` 补全。")
    elif warn_names:
        lines.append(f"- 无碎片角色，但 {len(warn_names)} 个角色为 WARN"
                     f"（{('、'.join(warn_names))}）：可选择性补全。")
    else:
        lines.append("- 设定集完备度良好，无碎片角色。")
    if not thin_names and not warn_names:
        lines.append("- 可直接进入大纲阶段。")
    lines.append("")
    return "\n".join(lines)


def print_summary(char_results, world_items, thin_names, warn_names):
    print("[material_review] 角色评分：")
    for r in char_results:
        print(f"  {r['name']:<8} {r['score']}/6 {r['level']:<5} 缺失: {'、'.join(r['thin_dims']) or '无'}")
    print("[material_review] 世界观：")
    for name, val, lvl in world_items:
        print(f"  {name:<22} {val}  {lvl}")
    if thin_names:
        print(f"[material_review] 结论：{len(thin_names)} 个碎片角色"
              f"（{'、'.join(thin_names)}），建议先 setting_refine 补全")
    else:
        print("[material_review] 结论：设定集无碎片角色")


def main():
    parser = argparse.ArgumentParser(description="NovelForge 素材完备度体检（确定性）")
    parser.add_argument("--setting", default="data/setting/setting.json")
    parser.add_argument("--normalized", default="data/setting/normalized")
    parser.add_argument("--out", default="data/setting/material_review.md")
    parser.add_argument("--json", action="store_true", help="输出机器可读摘要（thin/warn 名单）")
    args = parser.parse_args()

    if not Path(args.setting).exists():
        print(f"[material_review] 设定集不存在: {args.setting}（先跑 stage1）")
        return 1

    char_results, world_items, thin_names, warn_names = review(args.setting, args.normalized)
    print_summary(char_results, world_items, thin_names, warn_names)

    if args.json:
        print(json.dumps({"thin": thin_names, "warn": warn_names,
                          "total": len(char_results)}, ensure_ascii=False))
        return 0

    report = format_report(char_results, world_items, thin_names, warn_names,
                           args.setting, args.normalized)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_text(args.out, report)
    print(f"[material_review] 报告 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
