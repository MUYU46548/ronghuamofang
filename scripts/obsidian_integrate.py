# -*- coding: utf-8 -*-
"""ROSA 知识库深度整合：自动导入角色/世界观 + 写作后同步出场记录。

竞品（AI-Novel、InkOS、NovelForge 开源版）都没有与 Obsidian 知识库的原生整合。
这是 NovelForge 的核心护城河：ROSA 正典设定实时同步，写作时自动引用，
章节完成后自动更新出场记录。

设计原则：
- ROSA 库本体只读——所有产物写入项目内 data/ 目录，人工审阅后自行发布
- frontmatter 是唯一事实源；文件名 stem 仅作 fallback
- locked 条目不可违逆（写作时必须遵循）
- 新角色自动标记为 candidate，供 obsidian_postprocess 生成设定草稿

本模块是 obsidian_bridge 的兼容层，保持现有 API 不变。
"""

import re
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from utils.file_io import read_text, write_text

# ROSA vault 路径（只读）
ROSA_VAULT = Path("E:/图书馆/ROSA")

# 跳过目录（非正典内容）
SKIP_DIRS = {'.obsidian', '.git', '.hermes', '.agent_context', '.sitian', '99 模板'}

# frontmatter 键
NAME_KEYS = ["name", "title", "id"]
TAG_KEYS = ["tags", "tag", "aliases", "alias", "category", "categories"]
TYPE_KEYS = ["type", "kind", "category"]
DESC_KEYS = ["description", "summary", "desc", "简介", "描述"]
LOCKED_KEYS = ["locked", "lock", "immutable"]
RELATION_KEYS = ["relations", "relationships", "relation"]

# 委托给 obsidian_bridge（只读 + 沙盒写入）
from obsidian_bridge import scan_vault, inject_context, check_consistency, write_sandbox, push_to_sandbox


def _parse_frontmatter(text):
    """解析 YAML frontmatter，返回 (dict, body_start_offset)。"""
    m = re.match(r'^---\s*\n(.*?)\n---', text, re.S)
    if not m:
        return {}, 0
    fm_text = m.group(1)
    fm = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        kv = re.match(r'^(\w+)[：:]\s*(.+)', line)
        if kv:
            key = kv.group(1).strip()
            val = kv.group(2).strip().strip('"').strip("'")
            # 解析列表

            fm[key] = val
    return fm, m.end()


def _safe_list(v):
    """确保值为列表。"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [v]
    return []


def scan_rosa_characters(vault_path=None):
    """扫描 ROSA 中的角色词条，返回 [{name, path, tags, type, locked, relations, snippet}]。"""
    vault = Path(vault_path) if vault_path else ROSA_VAULT
    characters = []
    
    # 角色通常在 03 设定/01 人物/ 下
    char_dirs = [
        vault / "03 设定" / "01 人物",
        vault / "03 设定",
    ]
    
    for char_dir in char_dirs:
        if not char_dir.exists():
            continue
        for md_file in char_dir.rglob("*.md"):
            # 跳过非正典目录
            if any(part in SKIP_DIRS for part in md_file.relative_to(vault).parts):
                continue
            # 跳过索引页/模板
            if "索引" in md_file.name or "模板" in md_file.name:
                continue
            
            try:
                text = md_file.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            
            fm, body_start = _parse_frontmatter(text)
            body = text[body_start:]
            
            # 提取名称
            name = ""
            for k in NAME_KEYS:
                v = fm.get(k)
                if v:
                    name = v if isinstance(v, str) else str(v[0])
                    break
            if not name:
                name = md_file.stem
            
            # 提取标签
            tags = []
            for k in TAG_KEYS:
                v = fm.get(k)
                if v:
                    tags = _safe_list(v)
                    break
            
            # 提取类型
            type_ = ""
            for k in TYPE_KEYS:
                v = fm.get(k)
                if v:
                    type_ = v if isinstance(v, str) else str(v[0])
                    break
            
            # 是否 locked
            locked = False
            for k in LOCKED_KEYS:
                v = fm.get(k)
                if v and str(v).lower() in ('true', 'yes', '1', '是'):
                    locked = True
                    break
            
            # 提取关系
            relations = []
            for k in RELATION_KEYS:
                v = fm.get(k)
                if v:
                    relations = _safe_list(v)
                    break
            
            # 提取描述摘要
            desc = ""
            for k in DESC_KEYS:
                v = fm.get(k)
                if v:
                    desc = v if isinstance(v, str) else str(v[0])
                    break
            if not desc:
                # 用正文前 200 字
                plain = re.sub(r'[#*`\[\]]', '', body).strip()
                desc = plain[:200].replace('\n', ' ').strip()
            
            rel_path = str(md_file.relative_to(vault))
            characters.append({
                "name": name,
                "path": rel_path,
                "tags": tags[:10],
                "type": type_,
                "locked": locked,
                "relations": relations[:20],
                "snippet": desc[:300],
                "source": "rosa",
            })
    
    return characters


def scan_rosa_worldbuilding(vault_path=None):
    """扫描 ROSA 中的世界观词条（地点/势力/概念），返回 [{name, path, tags, type, snippet}]。"""
    vault = Path(vault_path) if vault_path else ROSA_VAULT
    entries = []
    
    world_dirs = [
        vault / "03 设定" / "02 地点",
        vault / "03 设定" / "03 势力",
        vault / "03 设定" / "04 概念",
        vault / "03 设定",
    ]
    
    for world_dir in world_dirs:
        if not world_dir.exists():
            continue
        for md_file in world_dir.rglob("*.md"):
            if any(part in SKIP_DIRS for part in md_file.relative_to(vault).parts):
                continue
            if "索引" in md_file.name or "模板" in md_file.name:
                continue
            
            try:
                text = md_file.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            
            fm, body_start = _parse_frontmatter(text)
            body = text[body_start:]
            
            name = ""
            for k in NAME_KEYS:
                v = fm.get(k)
                if v:
                    name = v if isinstance(v, str) else str(v[0])
                    break
            if not name:
                name = md_file.stem
            
            tags = []
            for k in TAG_KEYS:
                v = fm.get(k)
                if v:
                    tags = _safe_list(v)
                    break
            
            type_ = ""
            for k in TYPE_KEYS:
                v = fm.get(k)
                if v:
                    type_ = v if isinstance(v, str) else str(v[0])
                    break
            
            plain = re.sub(r'[#*`\[\]]', '', body).strip()
            snippet = plain[:300].replace('\n', ' ').strip()
            
            rel_path = str(md_file.relative_to(vault))
            entries.append({
                "name": name,
                "path": rel_path,
                "tags": tags[:10],
                "type": type_,
                "snippet": snippet,
                "source": "rosa",
            })
    
    return entries


def build_setting_from_rosa(vault_path=None, output_path=None):
    """从 ROSA 知识库构建设定集（data/setting/setting.json）。
    
    返回 setting 字典，结构：
    {
      "characters": [...],
      "world": [...],
      "plot_fragments": [...],
      "timeline": [...],
      "meta": {"source": "rosa", "built_at": "...", "vault_path": "..."}
    }
    """
    characters = scan_rosa_characters(vault_path)
    world_entries = scan_rosa_worldbuilding(vault_path)
    
    # 分类 world 条目
    locations = [e for e in world_entries if "地点" in e.get("type", "") or "场景" in e.get("type", "")]
    factions = [e for e in world_entries if "势力" in e.get("type", "") or "组织" in e.get("type", "")]
    concepts = [e for e in world_entries if "概念" in e.get("type", "") or e not in locations and e not in factions]
    
    setting = {
        "characters": characters,
        "world": {
            "locations": locations,
            "factions": factions,
            "concepts": concepts,
            "all": world_entries,
        },
        "plot_fragments": [],
        "timeline": [],
        "meta": {
            "source": "rosa",
            "built_at": datetime.now().isoformat(),
            "vault_path": str(vault_path or ROSA_VAULT),
            "character_count": len(characters),
            "world_count": len(world_entries),
        }
    }
    
    if output_path:
        # 校验输出路径在项目内 data/ 目录下（防止路径遍历）
        out = Path(output_path)
        if out.is_absolute():
            # 绝对路径只允许在 data/ 下
            if not str(out).replace("\\", "/").startswith("data/"):
                return False, f"输出路径必须在 data/ 目录下: {output_path}"
        write_text(output_path, json.dumps(setting, ensure_ascii=False, indent=2))
    
    return setting


def extract_character_names_from_text(text):
    """从正文中提取可能的中文人名/地名（简单规则：2-4 字连续中文，排除停用词）。"""
    # 匹配 2-4 字中文词
    candidates = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
    # 过滤停用词
    stopwords = {"什么", "怎么", "这个", "那个", "他们", "她们", "它们", "自己", "没有", "可以", "已经",
                 "不是", "而是", "但是", "可是", "然而", "不过", "虽然", "尽管", "因为", "所以",
                 "如果", "那么", "而且", "并且", "或者", "还是", "要么", "既然", "于是", "所以",
                 "突然", "忽然", "然后", "接着", "随后", "最后", "终于", "开始", "结束", "发现"}
    return [c for c in candidates if c not in stopwords]


def sync_chapter_appearances(chapter_text, chapter_no, setting_path="data/setting/setting.json"):
    """分析章节文本，提取角色出场信息，更新 setting.json 中的 appearances 字段。

    幂等：同一章重复调用（stage4 重跑/补跑）不会重复累加 total_mentions；
    逐章精确计数以 data/state/appearances.json（appearances.refresh_appearances 全量重算）为准。
    返回 {character_name: {chapter, first_appearance, mentions}} 字典。
    """
    # 路径遍历防护：只允许写 data/ 下的文件
    sp = Path(setting_path)
    if sp.is_absolute():
        sp_str = str(sp).replace("\\", "/")
        if not sp_str.startswith("data/"):
            return {}, f"setting_path 必须在 data/ 目录下: {setting_path}"
    setting = json.loads(read_text(setting_path)) if Path(setting_path).exists() else {}
    characters = setting.get("characters", [])
    
    # 建立名字→角色映射
    name_to_char = {}
    for char in characters:
        name = char.get("name", "")
        if name:
            name_to_char[name] = char
        # 也匹配 aliases
        for alias in char.get("aliases", []):
            if alias:
                name_to_char[alias] = char
    
    # 统计出场
    appearances = {}
    for name, char in name_to_char.items():
        count = chapter_text.count(name)
        if count > 0:
            char_name = char.get("name", name)
            if char_name not in appearances:
                appearances[char_name] = {
                    "chapter": chapter_no,
                    "mentions": count,
                    "path": char.get("path", ""),
                }
            else:
                appearances[char_name]["mentions"] += count
    
    # 更新 setting.json
    if "appearances" not in setting:
        setting["appearances"] = {}
    
    for char_name, info in appearances.items():
        if char_name not in setting["appearances"]:
            setting["appearances"][char_name] = {
                "first_chapter": info["chapter"],
                "chapters": [info["chapter"]],
                "total_mentions": info["mentions"],
                "path": info["path"],
            }
        else:
            existing = setting["appearances"][char_name]
            chapters = existing.setdefault("chapters", [])
            if info["chapter"] in chapters:
                # 同一章重复同步（stage4 重跑）→ 不重复累加（幂等）
                continue
            chapters.append(info["chapter"])
            existing["total_mentions"] = existing.get("total_mentions", 0) + info["mentions"]
    
    write_text(setting_path, json.dumps(setting, ensure_ascii=False, indent=2))
    return appearances


def detect_new_characters(chapter_text, setting_path="data/setting/setting.json"):
    """检测章节中可能的新角色（不在 setting.json 中的 2-4 字词，出现 >=2 次）。
    
    返回 [{name, mentions, chapter}] 列表。
    """
    setting = json.loads(read_text(setting_path)) if Path(setting_path).exists() else {}
    existing_names = set()
    for char in setting.get("characters", []):
        existing_names.add(char.get("name", ""))
        for alias in char.get("aliases", []):
            existing_names.add(alias)
    
    candidates = extract_character_names_from_text(chapter_text)
    from collections import Counter
    counter = Counter(candidates)
    
    new_chars = []
    for name, count in counter.most_common(20):
        if name not in existing_names and count >= 2:
            new_chars.append({
                "name": name,
                "mentions": count,
                "candidate": True,
            })
    
    return new_chars


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="ROSA 知识库深度整合")
    sub = parser.add_subparsers(dest="cmd")
    
    p_scan = sub.add_parser("scan", help="扫描 ROSA 知识库")
    p_scan.add_argument("--vault", default=str(ROSA_VAULT), help="ROSA vault 路径")
    p_scan.add_argument("--output", default="data/setting/setting.json", help="输出路径")
    
    p_sync = sub.add_parser("sync", help="同步章节出场记录")
    p_sync.add_argument("chapter_file", help="章节文件路径")
    p_sync.add_argument("--setting", default="data/setting/setting.json", help="设定集路径")
    
    p_detect = sub.add_parser("detect", help="检测新角色")
    p_detect.add_argument("chapter_file", help="章节文件路径")
    p_detect.add_argument("--setting", default="data/setting/setting.json", help="设定集路径")
    
    args = parser.parse_args()
    
    if args.cmd == "scan":
        setting = build_setting_from_rosa(args.vault, args.output)
        print(f"扫描完成：{setting['meta']['character_count']} 角色 + {setting['meta']['world_count']} 世界观词条")
        print(f"输出：{args.output}")
    
    elif args.cmd == "sync":
        text = read_text(args.chapter_file)
        chapter_no = int(re.search(r"第\s*(\d+)\s*章", text).group(1)) if re.search(r"第\s*(\d+)\s*章", text) else 0
        appearances = sync_chapter_appearances(text, chapter_no, args.setting)
        print(f"第 {chapter_no} 章出场角色：{len(appearances)}")
        for name, info in appearances.items():
            print(f"  {name}: {info['mentions']} 次")
    
    elif args.cmd == "detect":
        text = read_text(args.chapter_file)
        new_chars = detect_new_characters(text, args.setting)
        print(f"疑似新角色：{len(new_chars)}")
        for c in new_chars[:10]:
            print(f"  {c['name']}: {c['mentions']} 次")
    
    else:
        parser.print_help()
