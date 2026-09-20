# -*- coding: utf-8 -*-
"""素材完备度体检 CLI（P1.5-1）。

确定性、零成本：对 setting.json 的**人物**条目按 6 维打分，
并检查 world/plot_fragments/timeline 的完备性，输出体检报告
（data/setting/material_review.md）与终端摘要。

定位：stage1 归并成功后自动生成；供用户在 stage2 审批前判断
"设定集是否够厚、哪些角色是碎片"，引导是否跑 setting_refine.py 补全。

## ⚠️ 2026-09-20 修复：类型不感知

本脚本历史上**只遍历 `setting["characters"]` 数组**，不区分实体类型。
ROSA 归档数据把「绒花帝国魔法管制法」「茶叶统计」「AX-B-16型隐形空天战机」
这类**机构/法规/装备/文明条目也塞在 characters 数组里**（480 条中只有 168 条是人），
于是体检报告判出 **480 个「碎片角色」**，`thin_names` 塞进 orchestrator 的
审批提醒里，用户看到的是一个毫无意义的巨型名单。

修法：先过 `utils.setting_schema.is_character` 类型门（判据见该模块文档），
只对真人物做 6 维打分；非人物条目**单独汇总**，不再污染角色结论。

**返回形状刻意不变**（仍是 4 元组）—— 4 个调用点
（orchestrator ×2 / setting_refine ×2）无需改动。
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils.file_io import read_text, write_text
from utils.setting_schema import normalize_character  # noqa: E402

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
    """对每个**人物**条目 6 维打分。返回 (角色结果列表, 非人物条目名列表)。

    ⚠️ 类型门是本次修复的核心：不再对「绒花帝国魔法管制法」这类条目
    按角色维度打分。判据集中在 `utils.setting_schema.is_character`，
    这里不重复实现、不各自维护词表。

    ⚠️ **按 `(name, path)` 去重**：vault 产出的设定集每条目重复两次
    （`build_setting_from_vault` 把 concepts 与 all 一起展开）。
    实测 480 条里 85 组完全重复 —— 不去重的话结论清单会变成回音
    （「刘锋炎、锋炎（自然罗盘）…刘锋炎、锋炎（自然罗盘）」），
    角色数也会翻倍失真（168 vs 真实 84）。
    """
    results = []
    non_characters = []
    seen = set()
    for item in setting.get("characters", []):
        if not isinstance(item, dict):
            continue
        nc = normalize_character(item)
        if nc is None:                       # 连 name 都没有 → 不是有效条目
            continue
        k = (nc["name"], str(item.get("path", "") or ""))
        if k in seen:
            continue
        seen.add(k)
        # 类型门：非人物条目单独汇总，不参与角色维度评分
        if not nc["is_character"]:
            non_characters.append(nc["name"])
            continue

        # 打分**统一走归一后的字段**：stage1 schema(role/traits) 与
        # vault schema(type/tags/snippet) 在这里已经被对齐，
        # 否则两种 schema 会被打出一套系统性偏差的分数。
        name = nc["name"]
        blob = _norm(json.dumps(item, ensure_ascii=False))
        text = _norm((nc["kind"] or "") + " " + " ".join(nc["traits"]) + " " + nc["snippet"])

        scores = {}
        # 1 身份/定位
        role = _norm(nc["kind"])
        scores["identity"] = len(role) >= 8
        # 2 性格特征
        traits = [t for t in nc["traits"] if isinstance(t, str)]
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
        scores["source"] = bool(nc["source"])

        score = sum(1 for v in scores.values() if v)
        thin_dims = [DIMENSIONS[i][1] for i, v in enumerate(scores.values()) if not v]
        level = OK if score >= 4 else (WARN if score == 3 else THIN)
        results.append({
            "name": name, "id": nc["id"], "role": role or "(无角色定位)",
            "score": score, "level": level, "scores": scores,
            "thin_dims": thin_dims, "others": others,
            "counts": counts, "source": bool(nc["source"]),
        })
    return results, non_characters


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
    """主入口：返回 (char_results, world_items, thin_names, warn_names)。

    ⚠️ **返回形状刻意保持不变**（4 元组）—— orchestrator 与 setting_refine
    共 4 个调用点都按这个形状解包。非人物条目走 `char_results` 的兄弟结构
    汇总，通过 `format_report` 的 `non_characters` 参数传入，不改变本签名。
    """
    setting = json.loads(read_text(setting_path))
    all_names = [c.get("name") for c in setting.get("characters", [])
                 if isinstance(c, dict) and c.get("name")]

    # 收集 normalized 素材全文（若目录不存在则空）
    materials_text = []
    norm_dir = Path(normalized_dir)
    if norm_dir.is_dir():
        for p in sorted(norm_dir.glob("*.md")):
            try:
                materials_text.append(_norm(read_text(p)))
            except Exception:
                continue

    char_results, non_characters = review_characters(setting, materials_text, all_names)
    # 非人物条目名挂在函数属性上，供 format_report / print_summary 取用。
    # 刻意不做成返回值的一部分：那会改掉 review() 的签名，
    # orchestrator ×2 与 setting_refine ×2 共 4 个调用点全部要动。
    review._last_non_characters = non_characters
    world_items = review_world(setting)
    thin_names = [r["name"] for r in char_results if r["level"] == THIN]
    warn_names = [r["name"] for r in char_results if r["level"] == WARN]
    return char_results, world_items, thin_names, warn_names


def get_last_non_characters():
    """取上一次 `review()` 汇总出的非人物条目名（供报告渲染）。

    刻意不做成返回值的一部分：那会改掉 `review()` 的签名，
    4 个调用点全部要动。用伴生函数传递，兼容性优先。
    """
    return list(getattr(review, "_last_non_characters", []))


def format_report(char_results, world_items, thin_names, warn_names, setting_path, normalized_dir):
    non_characters = get_last_non_characters()
    lines = []
    lines.append("# 素材完备度体检报告（material_review）")
    lines.append("")
    # ⚠️ 这里曾是写死的字面量「（脚本运行时）」——报告归档后无法判断新旧。
    lines.append(f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 设定集: `{setting_path}`")
    lines.append(f"- 素材目录: `{normalized_dir}`")
    lines.append(f"- 条目构成: 原始 characters {len(char_results) + len(non_characters)} 条 → "
                 f"**人物 {len(char_results)} 条** / 非人物 {len(non_characters)} 条（已排除）")
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
    lines.append("## 非人物条目（不参与角色评分）")
    lines.append("")
    if non_characters:
        lines.append(f"共 {len(non_characters)} 条机构/法规/场所/装备/文明等条目被排除在角色维度之外。")
        lines.append("这不是缺陷 —— 它们的完备度要看 `world` 一节，而不是「身份/性格/关系网」。")
        lines.append("")
        preview = non_characters[:40]
        for n in preview:
            lines.append(f"- {n}")
        if len(non_characters) > len(preview):
            lines.append(f"- …（其余 {len(non_characters) - len(preview)} 条见 `--json` 输出）")
    else:
        lines.append("无（设定集里没有非人物条目混入）")
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
        shown = thin_names[:30]
        lines.append(f"- **存在 {len(thin_names)} 个碎片角色**（{('、'.join(shown))}"
                     f"{'…' if len(thin_names) > len(shown) else ''}）："
                     f"建议在阶段2审批前跑 `python scripts/setting_refine.py` 补全。")
    elif warn_names:
        shown = warn_names[:30]
        lines.append(f"- 无碎片角色，但 {len(warn_names)} 个角色为 WARN"
                     f"（{('、'.join(shown))}{'…' if len(warn_names) > len(shown) else ''}）：可选择性补全。")
    else:
        lines.append("- 设定集完备度良好，无碎片角色。")
    if not thin_names and not warn_names:
        lines.append("- 可直接进入大纲阶段。")
    lines.append("")
    return "\n".join(lines)


def print_summary(char_results, world_items, thin_names, warn_names):
    non_characters = get_last_non_characters()
    print(f"[material_review] 角色评分（人物 {len(char_results)} 条，"
          f"另有 {len(non_characters)} 条非人物已排除）：")
    for r in char_results:
        print(f"  {r['name']:<8} {r['score']}/6 {r['level']:<5} 缺失: {'、'.join(r['thin_dims']) or '无'}")
    print("[material_review] 世界观：")
    for name, val, lvl in world_items:
        print(f"  {name:<22} {val}  {lvl}")
    if thin_names:
        shown = thin_names[:15]
        print(f"[material_review] 结论：{len(thin_names)} 个碎片角色"
              f"（{'、'.join(shown)}{'…' if len(thin_names) > len(shown) else ''}），"
              f"建议先 setting_refine 补全")
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
                          "total": len(char_results),
                          "non_characters": get_last_non_characters()},
                         ensure_ascii=False))
        return 0

    report = format_report(char_results, world_items, thin_names, warn_names,
                           args.setting, args.normalized)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_text(args.out, report)
    print(f"[material_review] 报告 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
