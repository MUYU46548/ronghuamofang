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

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.file_io import read_text, write_text


def _src_of(entry):
    """取条目 source 字段（可能缺失/None）。"""
    if isinstance(entry, dict):
        s = entry.get("source")
        return s.strip() if isinstance(s, str) and s.strip() else None
    return None


def _world_entries(world):
    """世界观条目（条目, 分类）。兼容两种形状：

    - `dict`：出厂/提示词要求的分类键（locations / factions / magic_system / items）；
    - `list`：模型有时直接把条目排成列表（没有分类键）。

    2026-11-12：只认 dict 时，`world` 是列表的设定集会让 `build_vault_links` 整段抛
    `AttributeError: 'list' object has no attribute 'get'` —— 而调用点只打印一句
    "不影响设定集"，于是索引静默为空（问题总账第 10 条）。
    """
    out = []
    if isinstance(world, dict):
        for key in ("locations", "factions", "magic_system", "items"):
            for item in world.get(key, []) or []:
                if isinstance(item, dict) and item.get("name"):
                    out.append((item, "world." + key))
    elif isinstance(world, list):
        for item in world:
            if isinstance(item, dict) and item.get("name"):
                kind = str(item.get("type") or item.get("category") or "").strip()
                out.append((item, ("world." + kind) if kind else "world"))
    return out


def collect_entries(setting):
    """把 setting.json 展平为 (source, category, name) 列表。"""
    entries = []
    if not isinstance(setting, dict):
        return entries
    for c in setting.get("characters", []) or []:
        if isinstance(c, dict) and c.get("name"):
            entries.append((_src_of(c), "characters", c["name"]))
    for item, cat in _world_entries(setting.get("world", {}) or {}):
        entries.append((_src_of(item), cat, item["name"]))
    for pf in setting.get("plot_fragments", []) or []:
        if isinstance(pf, dict) and pf.get("summary"):
            entries.append((_src_of(pf), "plot_fragments",
                            f"{pf.get('id', '?')} {str(pf['summary'])[:40]}"))
    for tl in setting.get("timeline", []) or []:
        if isinstance(tl, dict):
            # 键名两种写法都有：`event`（出场记录口径）/ `what`（设定集口径）
            name = str(tl.get("event") or tl.get("what") or "").strip()
            if name:
                entries.append((_src_of(tl), "timeline", name[:50]))
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
