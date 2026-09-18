# -*- coding: utf-8 -*-
"""角色出场统计（P1.6-1，确定性核心）。

输入：data/setting/setting.json（角色名）+ data/outline/chapters/NN.md（涉及角色字段）
     + data/chapters/raw|checked|refined/*.md（正文）
输出：data/state/appearances.json（{角色名: {first, chapters, total, per_chapter, level}}）

分级启发式（按用户决策：首次出场章序 + 戏份，不按固定百分比）：
- 主要角色（major）：首次出场 ≤ 40% 全书章数 且 出现章数 ≥3；或全书 ≤3 章时出现章数 ≥2
- 次要角色（minor）：首次出场 ≤ 75% 全书章数 或 出现章数 ≥2
- 背景与提及（background）：出现但未达次要标准
- 未出现（absent）：设定集/大纲有角色但正文零提及（警告项，防"写了忘了用"）
- 新角色候选（candidate_new）：逐章大纲提到但设定集无（obsidian_postprocess 据此生成设定草稿）

用法：
  python scripts/appearances.py                       # 统计并写 appearances.json
  python scripts/appearances.py --json                # 输出机器可读
"""
import argparse
import json
import re
import sys
from pathlib import Path

from utils.file_io import read_text, write_text

CHAPTER_DIRS = ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw")
OUTLINE_DIR = "data/outline/chapters"
SETTING = "data/setting/setting.json"
OUT = "data/state/appearances.json"

# 逐章大纲"涉及角色"行：形如 "luxi 露汐（写病历...）" 或 "小林（夜班护士）"
ROLE_LINE_RE = re.compile(r"涉及角色[：:]\s*(.+)")
# 角色项：可选 id + 中文名（不含括号），后跟（描述
ROLE_ITEM_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*\s+)?([\u4e00-\u9fff]{2,12})(?=\s*[（(])")
# 中文名直接出现（无描述括号）
ROLE_BARE_RE = re.compile(r"([\u4e00-\u9fff]{2,12})")
# 群体/概念名词（非单一人名），不视为新角色候选
GROUP_NOUNS = {"走私团伙", "匿名花匠", "御花园总管", "绒花族少女"}


def load_setting_names(setting_path=SETTING):
    """从设定集提取角色名集合。返回 {name: {"id": id}}。"""
    try:
        data = json.loads(read_text(setting_path))
    except Exception:
        return {}
    names = {}
    for c in data.get("characters", []):
        if isinstance(c, dict) and c.get("name"):
            names[c["name"]] = {"id": c.get("id", "")}
    return names


def load_outline_roles(outline_dir=OUTLINE_DIR):
    """从逐章大纲提取涉及角色名。返回 {name: {"id": id, "source": "outline"}}。

    解析"涉及角色"行中的角色项：优先 id+中文名（luxi 露汐），
    其次 中文名（描述，如 小林（夜班护士）），忽略括号内描述文字。
    """
    roles = {}
    od = Path(outline_dir)
    if not od.is_dir():
        return roles
    for p in sorted(od.glob("*.md")):
        try:
            text = read_text(p)
        except Exception:
            continue
        for line in text.splitlines():
            m = ROLE_LINE_RE.search(line)
            if not m:
                continue
            seg = m.group(1)
            # 1) id+名 或 名（描述
            for im in ROLE_ITEM_RE.finditer(seg):
                rid = (im.group(1) or "").strip()
                rname = im.group(2)
                if rname in GROUP_NOUNS:
                    continue
                if rname and rname not in roles:
                    roles[rname] = {"id": rid, "source": "outline"}
                elif rid and roles.get(rname):
                    roles[rname]["id"] = rid
            # 2) 剩余中文词（去括号内描述与已识别项）
            cleaned = ROLE_ITEM_RE.sub("", seg)
            cleaned = re.sub(r"[（(].*?[)）]", "", cleaned)
            for w in ROLE_BARE_RE.findall(cleaned):
                if w in GROUP_NOUNS:
                    continue
                if w not in roles:
                    roles[w] = {"id": "", "source": "outline"}
    return roles


def load_chapters(chapter_dirs=CHAPTER_DIRS):
    """收集各章正文。返回 [(章号, 正文)]，refined > checked > raw 优先级。"""
    seen = {}
    for d in chapter_dirs:
        for p in sorted(Path(d).glob("*.md")):
            try:
                n = int(p.stem)
            except ValueError:
                continue
            if n in seen:
                continue
            try:
                seen[n] = read_text(p)
            except Exception:
                continue
    return sorted(seen.items())


def count_appearances(setting_path=SETTING, outline_dir=OUTLINE_DIR,
                      chapter_dirs=CHAPTER_DIRS, chapter_count=None):
    """统计出场。返回 (stats, total_chapters, new_candidates)。

    stats: {name: {first, chapters, total, per_chapter, level, id, in_setting}}
    new_candidates: [name]（大纲提到但设定集无）
    """
    setting_names = load_setting_names(setting_path)
    outline_roles = load_outline_roles(outline_dir)
    chapters = load_chapters(chapter_dirs)
    total_chapters = chapter_count or len(chapters)
    if total_chapters == 0:
        return {}, 0, []

    # 角色全集：设定集 ∪ 大纲
    names = {}
    for n, info in setting_names.items():
        names[n] = {"id": info.get("id", ""), "in_setting": True}
    for n, info in outline_roles.items():
        if n not in names:
            names[n] = {"id": info.get("id", ""), "in_setting": False}
        elif not names[n]["id"]:
            names[n]["id"] = info.get("id", "")

    stats = {}
    for name, info in names.items():
        per = {}
        total = 0
        for n, text in chapters:
            c = text.count(name)
            if c > 0:
                per[n] = c
                total += c
        if not per:
            stats[name] = {"first": None, "chapters": 0, "total": 0,
                           "per_chapter": {}, "level": "absent", "id": info["id"],
                           "in_setting": info["in_setting"]}
            continue
        first = min(per)
        appear_chapters = len(per)
        stats[name] = {"first": first, "chapters": appear_chapters, "total": total,
                       "per_chapter": per, "level": "pending", "id": info["id"],
                       "in_setting": info["in_setting"]}

    # 分级（按用户决策：首次出场章序 + 戏份，不按固定百分比）
    for name, st in stats.items():
        if st["level"] == "absent":
            continue
        first = st["first"]
        ch = st["chapters"]
        # 主要：早期出场 + 持续戏份（短篇 3 章需 ≥3 章出现，避免配角误升）
        if (total_chapters <= 3 and ch >= 3) or \
           (first <= max(1, int(total_chapters * 0.4)) and ch >= max(3, total_chapters // 2)):
            st["level"] = "major"
        elif (first <= max(1, int(total_chapters * 0.75))) or ch >= 2:
            st["level"] = "minor"
        else:
            st["level"] = "background"

    new_candidates = [n for n, st in stats.items() if not st["in_setting"]]
    return stats, total_chapters, new_candidates


def write_appearances(stats, total_chapters, new_candidates, out=OUT):
    data = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "total_chapters": total_chapters,
        "characters": stats,
        "new_candidates": new_candidates,
    }
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    write_text(out, json.dumps(data, ensure_ascii=False, indent=2))
    return out


def refresh_appearances(chapter_count=None, out=OUT):
    """确定性重算并写入 appearances.json（零 LLM、零网络）。

    stage4 每章完成后调用：只看文件系统现状（设定集 + 逐章大纲 + 章节正文），
    天然幂等 —— 重跑、补跑、乱序跑都不会重复计数。
    返回 (输出路径 | None, stats, total_chapters, new_candidates)；无角色/无章节时路径为 None。
    """
    stats, total, new = count_appearances(chapter_count=chapter_count)
    if not stats:
        return None, {}, 0, []
    return write_appearances(stats, total, new, out), stats, total, new


def print_summary(stats, total_chapters, new_candidates):
    print(f"[appearances] 全书 {total_chapters} 章，角色 {len(stats)} 个")
    groups = {"major": [], "minor": [], "background": [], "absent": []}
    for n, st in stats.items():
        groups.get(st["level"], []).append(n)
    for lvl, label in (("major", "主要"), ("minor", "次要"),
                       ("background", "背景/提及"), ("absent", "未出场")):
        if groups[lvl]:
            print(f"  [{label}] {('、'.join(groups[lvl]))}")
    if new_candidates:
        print(f"  [新角色候选] {('、'.join(new_candidates))}（大纲提到但设定集无）")


def main():
    parser = argparse.ArgumentParser(description="NovelForge 角色出场统计（确定性）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()

    stats, total, new = count_appearances()
    if not stats:
        print("[appearances] 未找到角色或章节（先跑 stage3/4）")
        return 1
    out = write_appearances(stats, total, new, args.out)
    print_summary(stats, total, new)
    print(f"[appearances] → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
