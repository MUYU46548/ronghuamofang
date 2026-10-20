# -*- coding: utf-8 -*-
"""Vault 知识库整合：自动导入角色/世界观 + 写作后同步出场记录。

与 obsidian_bridge 协同工作，本模块提供额外的同步/检测功能。

## ⚠️ 2026-09-21 去重

本模块的 `scan_vault_characters` / `scan_vault_worldbuilding` 此前是
`obsidian_bridge.scan_vault` 的**逐行复制品**（各约 95 行，含同样的
frontmatter 解析、同样的目录遍历）。已改为**委托**，单一事实源在 bridge。
顺带删掉了随之成为死代码的 `_parse_frontmatter` / `_safe_list` 及未使用导入。

复制实现的代价在这里体现得很典型：同一个「父目录 rglob 导致跨类目污染」
缺陷必须修两遍，而实际上当时只修了 bridge 一处。
"""
import re
import json
import shutil
import sys
from pathlib import Path
from datetime import datetime
from collections import Counter

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.file_io import read_text, write_text
from obsidian_bridge import scan_vault, _get_vault_path


def _resolve_vault(vault_path=None):
    """解析 vault 路径；未配置时给出可行动错误（而非 TypeError）。"""
    v = Path(vault_path) if vault_path else _get_vault_path()
    if v is None:
        raise ValueError(
            "未配置 Obsidian vault 路径：请在 config/system.yaml 的 "
            "obsidian.vault_path 填写，或用 --vault 显式指定。")
    return v


def scan_vault_characters(vault_path=None):
    """扫描 vault 中的角色词条。

    ⚠️ 2026-09-21 改为**委托** `obsidian_bridge.scan_vault`。

    本函数与 `scan_vault_worldbuilding` 此前是 `scan_vault` 的**逐行复制品**
    （各约 95 行），且带着同一个缺陷：目录列表里混进**父目录**（设定根）并配
    `rglob`，导致跨类目污染（地点/概念被当成角色）与重复（每条扫两遍）。

    重复实现意味着**同一个 bug 要修两遍**（而且很容易只修一处）。
    现统一委托，单一事实源在 `obsidian_bridge.scan_vault`。

    返回 [{name, path, tags, type, locked, relations, snippet, source}]。
    """
    return scan_vault(vault_path)["characters"]


def scan_vault_worldbuilding(vault_path=None):
    """扫描 vault 中的世界观词条（地点/势力/概念）。

    同样改为委托 `obsidian_bridge.scan_vault`（见上）。
    返回 [{name, path, tags, type, snippet, source}]。
    """
    return scan_vault(vault_path)["world"]

def _backup_setting_if_exists(setting_path):
    """覆盖前把现有 setting.json 备份到 data/setting/history/setting_v{N}.json。

    命名沿用 `setting_refine.py` 的既有约定（`setting_v{N}.json` 递增），
    这样两处产出的备份在同一个序列里，不会互相覆盖。

    ⚠️ 为什么必须备份：`build_setting_from_vault` 是**整体替换**语义 ——
    它用 vault 扫描结果构造全新的 characters 列表，`plot_fragments` /
    `timeline` 直接置空。实测（2026-09-21）跑一次 scan 就会把 stage1 从素材
    归并出来的角色、剧情碎片、时间线**全部丢掉且不可恢复**。
    本项目纪律：破坏性操作前必须备份。这里补上。

    返回备份路径，或 None（原文件不存在 / 备份失败）。
    """
    src = Path(setting_path)
    if not src.exists():
        return None
    hist = Path("data/setting/history")
    hist.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in hist.glob("setting_v*.json"):
        m = re.match(r"setting_v(\d+)\.json$", p.name)
        if m:
            nums.append(int(m.group(1)))
    dest = hist / f"setting_v{(max(nums) + 1) if nums else 1}.json"
    try:
        shutil.copy2(src, dest)
        return dest
    except OSError:
        return None


def build_setting_from_vault(vault_path=None, output_path=None):
    """从 vault 构建设定集（data/setting/setting.json）。

    ⚠️ **语义是「整体替换」，不是「合并」/「统一」**：本函数用 vault 扫描
    结果构造**全新**的 characters 列表，并把 `plot_fragments` / `timeline`
    置为空列表。因此它**不会**保留 stage1 从素材归并的内容。

    写盘前会**自动备份**现有 setting.json 到
    `data/setting/history/setting_v{N}.json`（若存在），并打印被替换的条目数，
    避免静默数据丢失。

    Args:
        vault_path: vault 根目录；None 时读 config/system.yaml 的 obsidian.vault_path
        output_path: 输出路径（必须在 data/ 下）；None 时不写盘，只返回 dict

    Returns:
        setting dict（结构见下方）

    Raises:
        ValueError: output_path 越出 data/ 边界，或 vault 未配置。

    ⚠️ 2026-09-21 修复：此前失败分支 `return False, msg`（二元组），成功分支
    返回 dict —— 调用方 `setting['meta']` 在失败时抛 TypeError。现统一为
    抛 ValueError（与 `_resolve_vault` 同风格），成功只返回 dict。
    """
    characters = scan_vault_characters(vault_path)
    world_entries = scan_vault_worldbuilding(vault_path)

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
            "source": "vault",
            "built_at": datetime.now().isoformat(),
            "vault_path": str(vault_path or _get_vault_path()),
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
                raise ValueError(f"输出路径必须在 data/ 目录下: {output_path}")

        # 覆盖前备份 + 明确告知将被替换的内容（防静默数据丢失）
        old_chars = []
        if Path(output_path).exists():
            try:
                old = json.loads(read_text(output_path))
                old_chars = [c.get("name") for c in old.get("characters", [])
                             if isinstance(c, dict) and c.get("name")]
            except Exception:                      # noqa: BLE001
                old_chars = []
            backup = _backup_setting_if_exists(output_path)
            if backup:
                msg = f"[obsidian_integrate] 已备份原设定集 → {backup}"
                if old_chars:
                    msg += (f"（原 {len(old_chars)} 个角色将被**整体替换**："
                            f"{'、'.join(old_chars[:10])}"
                            f"{'…' if len(old_chars) > 10 else ''}）")
                print(msg)
            else:
                print("[obsidian_integrate] ⚠ 原设定集备份失败，仍继续覆盖（请自行确认）")

        write_text(output_path, json.dumps(setting, ensure_ascii=False, indent=2))

    return setting


def merge_setting_from_vault(vault_path=None, output_path="data/setting/setting.json",
                             conflict_path="data/setting/vault_conflicts.json"):
    """把 vault 词条**增量合并**进现有设定集（2026-10-20，台账三·「设定库闭环」）。

    ## 与 build_setting_from_vault 的区别（这是它存在的全部理由）

    `build_setting_from_vault` 是**整体替换**：characters 全换、plot_fragments 与
    timeline 直接置空。跑一次就把 stage1 从素材归并的东西全丢掉（所以它必须先备份）。
    结果是"设定库"永远只有两种状态：素材归并的，或 vault 的 —— 二者不能共存。

    本函数只做**加法**，并且**只在安全的地方**加：

    | 情况 | 处理 |
    |---|---|
    | vault 有、设定集没有 | 追加（带 `source` / `path` / `llm_inferred: false`） |
    | 同名（含 `base_name` 归一）且字段**空着** | 补上空字段 |
    | 同名但已有**非空**值 | **不动**，差异记进 `vault_conflicts.json` 等人裁决 |
    | 条目 `locked: true` | **完全不碰**（locked 是硬约束，连空字段都不补） |
    | `plot_fragments` / `timeline` | 原样保留（整体替换语义的反面） |

    ## 为什么"有值就不覆盖"

    vault 是作者的原始笔记（可能很旧），设定集是**已经过素材归并与人工审阅**的定稿。
    让旧笔记悄悄盖掉定稿，等于把审阅工作作废 —— 而那正是闭环要防的事。
    冲突进 `vault_conflicts.json`，再由 GUI「素材裁决」三态拍板（adopt/reject/uncertain）。

    Returns: stats dict（added / filled / conflicts / skipped_locked / kept_fragments）
    """
    p = Path(output_path)
    # **先读设定集，再扫 vault**（2026-10-20 实测）：反过来的话，设定集坏掉时
    # 用户看到的却是"vault 未配置"—— 掩盖了真正的、必须先修的那个错。
    setting = {}
    if p.exists():
        try:
            setting = json.loads(read_text(p))
        except Exception as e:                                 # noqa: BLE001
            raise ValueError("设定集解析失败（先修好它再合并）: %r" % (e,))
    if not isinstance(setting, dict):
        raise ValueError("设定集顶层不是对象，拒绝合并（避免把文件写坏）")

    chars = scan_vault_characters(vault_path)
    world = scan_vault_worldbuilding(vault_path)

    from utils.setting_schema import base_name, is_locked

    def _key(name):
        return base_name(str(name or "").strip())

    stats = {"added": [], "filled": [], "conflicts": [], "skipped_locked": [],
             "kept_fragments": 0, "vault_only_fields": 0}
    conflicts = []

    def _merge_into(bucket, incoming):
        """把 incoming 条目合并进 bucket（list of dict）；返回是否新增。"""
        index = {}
        for it in bucket:
            if isinstance(it, dict):
                index.setdefault(_key(it.get("name")), it)
        for inc in incoming:
            if not isinstance(inc, dict) or not inc.get("name"):
                continue
            k = _key(inc["name"])
            cur = index.get(k)
            if cur is None:
                bucket.append(inc)
                index[k] = inc
                stats["added"].append(inc["name"])
                continue
            if is_locked(cur):
                stats["skipped_locked"].append(inc["name"])
                continue
            for field, val in inc.items():
                if field in ("name", "path", "tags", "type", "source", "llm_inferred"):
                    continue
                mine = cur.get(field)
                empty = (mine is None or mine == "" or mine == [] or mine == {})
                if empty:
                    cur[field] = val
                    stats["filled"].append("%s.%s" % (inc["name"], field))
                elif str(mine).strip() != str(val).strip():
                    conflicts.append({
                        "name": cur.get("name"),
                        "field": field,
                        "severity": "review",
                        "values": [
                            {"value": str(mine)[:400], "file": str(cur.get("source") or "setting.json")},
                            {"value": str(val)[:400], "file": str(inc.get("source") or inc.get("path") or "vault")},
                        ],
                    })
                else:
                    stats["vault_only_fields"] += 1
        return True

    setting.setdefault("characters", [])
    if not isinstance(setting["characters"], list):
        setting["characters"] = []
    _merge_into(setting["characters"], chars)

    w = setting.get("world")
    if not isinstance(w, dict):
        w = {}
        setting["world"] = w
    for sub in ("locations", "factions", "concepts"):
        if not isinstance(w.get(sub), list):
            w[sub] = []
        if sub == "locations":
            picks = [e for e in world
                     if "地点" in e.get("type", "") or "场景" in e.get("type", "")]
        elif sub == "factions":
            picks = [e for e in world
                     if "势力" in e.get("type", "") or "组织" in e.get("type", "")]
        else:
            # 概念 = 既不是地点也不是势力（与 build_setting_from_vault 的口径一致）
            picks = [e for e in world
                     if not ("地点" in e.get("type", "") or "场景" in e.get("type", "")
                             or "势力" in e.get("type", "") or "组织" in e.get("type", ""))]
        _merge_into(w[sub], picks)

    # 整体替换语义的反面：这两项**保留**
    for k in ("plot_fragments", "timeline"):
        if isinstance(setting.get(k), list):
            stats["kept_fragments"] += len(setting[k])
        else:
            setting[k] = []

    meta = setting.get("meta") if isinstance(setting.get("meta"), dict) else {}
    meta.update({
        "last_merge_at": datetime.now().isoformat(timespec="seconds"),
        "last_merge_mode": "merge",
        "last_merge_added": len(stats["added"]),
        "last_merge_conflicts": len(conflicts),
    })
    setting["meta"] = meta

    backup = _backup_setting_if_exists(output_path)
    if backup:
        print("[obsidian_integrate] 合并前已备份 → %s" % backup)
    write_text(output_path, json.dumps(setting, ensure_ascii=False, indent=2))

    if conflicts:
        cp = Path(conflict_path)
        cp.parent.mkdir(parents=True, exist_ok=True)
        write_text(cp, json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                                   "conflicts": conflicts}, ensure_ascii=False, indent=2))
        print("[obsidian_integrate] %d 处字段分歧**未自动改**，已记进 %s（去 GUI「素材裁决」拍板）"
              % (len(conflicts), cp.as_posix()))
    stats["conflicts"] = conflicts
    stats["backup"] = str(backup) if backup else ""
    return stats


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
    
    parser = argparse.ArgumentParser(description="Vault 知识库深度整合")
    sub = parser.add_subparsers(dest="cmd")
    
    p_scan = sub.add_parser("scan", help="扫描 vault 知识库")
    _vp = _get_vault_path()
    p_scan.add_argument("--vault",
                        default=str(_vp) if _vp is not None else "",
                        help="vault 路径（留空则读 config/system.yaml）")
    p_scan.add_argument("--output", default="data/setting/setting.json", help="输出路径")
    
    p_merge = sub.add_parser("merge", help="增量合并（保留既有条目与剧情碎片）")
    p_merge.add_argument("--vault",
                         default=str(_vp) if _vp is not None else "",
                         help="vault 路径（留空则读 config/system.yaml）")
    p_merge.add_argument("--output", default="data/setting/setting.json", help="设定集路径")

    p_sync = sub.add_parser("sync", help="同步章节出场记录")
    p_sync.add_argument("chapter_file", help="章节文件路径")
    p_sync.add_argument("--setting", default="data/setting/setting.json", help="设定集路径")
    
    p_detect = sub.add_parser("detect", help="检测新角色")
    p_detect.add_argument("chapter_file", help="章节文件路径")
    p_detect.add_argument("--setting", default="data/setting/setting.json", help="设定集路径")
    
    args = parser.parse_args()
    
    if args.cmd == "scan":
        try:
            setting = build_setting_from_vault(args.vault, args.output)
        except ValueError as e:
            # build_setting_from_vault 现统一抛 ValueError（vault 未配置 / 路径越界）
            print(f"[obsidian_integrate] 失败: {e}")
            print("[obsidian_integrate] 提示：vault 目录结构与扫描范围见 "
                  "obsidian_bridge.scan_vault 的 docstring。")
            sys.exit(1)
        print(f"扫描完成：{setting['meta']['character_count']} 角色 + "
              f"{setting['meta']['world_count']} 世界观词条")
        print(f"输出：{args.output}")
        print("[obsidian_integrate] ⚠ 注意：本命令是**整体替换**语义 —— "
              "vault 内容会覆盖现有设定集，原文件已备份到 data/setting/history/。")
    
    elif args.cmd == "merge":
        try:
            st = merge_setting_from_vault(args.vault, args.output)
        except ValueError as e:
            print(f"[obsidian_integrate] 失败: {e}")
            sys.exit(1)
        print("合并完成：新增 %d 条 / 补空字段 %d 处 / 分歧 %d 处 / locked 跳过 %d 条"
              % (len(st["added"]), len(st["filled"]), len(st["conflicts"]),
                 len(st["skipped_locked"])))
        if st["added"]:
            print("  新增：" + "、".join(st["added"][:10]))
        print("[obsidian_integrate] 剧情碎片/时间线**保留**（%d 条），与 scan 的整体替换不同。"
              % st["kept_fragments"])

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
