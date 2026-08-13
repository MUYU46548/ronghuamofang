# -*- coding: utf-8 -*-
"""生成设定库引用索引 materials/vault_links.md（确定性，P0.7 #3）。

背景：project.yaml 的 vault.vault_links 声明"设定库引用索引（阶段1 生成/维护）"，
此前从未实现。本脚本从 setting.json 反向聚合：
素材文件 → 从中提取的设定条目（角色/世界观/情节碎片/时间线），
输出 Markdown 索引，供溯源与后续素材管理使用。

用法：
  python scripts/build_vault_links.py              # 默认读 data/setting/setting.json
  python scripts/build_vault_links.py --setting <path> --out <path>
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text


def _src_of(entry):
    """取条目 source 字段（可能缺失/None）。"""
    if isinstance(entry, dict):
        s = entry.get("source")
        return s.strip() if isinstance(s, str) and s.strip() else None
    return None


def collect_entries(setting):
    """把 setting.json 展平为 (source, category, name) 列表。"""
    entries = []
    for c in setting.get("characters", []) or []:
        if isinstance(c, dict) and c.get("name"):
            entries.append((_src_of(c), "characters", c["name"]))
    world = setting.get("world", {}) or {}
    for key in ("locations", "factions", "magic_system", "items"):
        for item in world.get(key, []) or []:
            if isinstance(item, dict) and item.get("name"):
                entries.append((_src_of(item), f"world.{key}", item["name"]))
    for pf in setting.get("plot_fragments", []) or []:
        if isinstance(pf, dict) and pf.get("summary"):
            entries.append((_src_of(pf), "plot_fragments",
                            f"{pf.get('id', '?')} {str(pf['summary'])[:40]}"))
    for tl in setting.get("timeline", []) or []:
        if isinstance(tl, dict) and tl.get("event"):
            entries.append((_src_of(tl), "timeline", str(tl["event"])[:50]))
    return entries


def build_vault_links(setting_path, out_path):
    """从 setting.json 生成引用索引 Markdown。返回 (条目数, 素材数)。"""
    data = json.loads(read_text(setting_path))
    entries = collect_entries(data)

    by_source = defaultdict(list)
    untraced = []
    for src, cat, name in entries:
        (by_source[src] if src else untraced).append((cat, name))

    lines = [
        "# 设定库引用索引（vault_links）",
        "",
        f"> 由 `scripts/build_vault_links.py` 生成：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 来源：{Path(setting_path).resolve()}",
        "",
        "## 素材 → 设定条目",
        "",
    ]
    total = 0
    for src in sorted(by_source, key=str.lower):
        items = by_source[src]
        total += len(items)
        lines.append(f"### {src}")
        for cat, name in sorted(items):
            lines.append(f"- `{cat}`: {name}")
        lines.append("")
    if untraced:
        total += len(untraced)
        lines += ["### （未溯源）", ""]
        for cat, name in untraced:
            lines.append(f"- `{cat}`: {name}")
        lines.append("")
    lines += ["## 统计", "",
              f"- 设定条目总数: {total}",
              f"- 溯源素材数: {len(by_source)}",
              f"- 未溯源条目: {len(untraced)}",
              "",
              "> 未溯源条目建议在下次归并时补充 source 字段。"]
    write_text(out_path, "\n".join(lines) + "\n")
    return total, len(by_source)


def main():
    parser = argparse.ArgumentParser(description="NovelForge 设定库引用索引生成")
    parser.add_argument("--setting", default="data/setting/setting.json")
    parser.add_argument("--out", default="materials/vault_links.md")
    args = parser.parse_args()
    if not Path(args.setting).exists():
        print(f"[vault_links] 设定集不存在: {args.setting}")
        return 1
    total, srcs = build_vault_links(args.setting, args.out)
    print(f"[vault_links] 生成完成: {total} 条目 / {srcs} 素材 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
