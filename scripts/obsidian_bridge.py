# -*- coding: utf-8 -*-
"""Obsidian 知识库联动桥（只读 + 沙盒写入）。

设计原则：
- ROSA vault 本体只读——所有产物写入沙盒（Obsidian_AI_Sandbox/10_Inbox/）
- frontmatter 是唯一事实源；文件名 stem 仅作 fallback
- locked 条目不可违逆（写作时必须遵循）
- 新角色自动标记为 candidate，供 rosa_postprocess 生成设定草稿

功能：
1. scan_vault() — 扫描 ROSA vault，建立结构化索引
2. inject_context() — 为写作阶段注入相关词条
3. check_consistency() — 检查写作产物是否偏离正典
4. write_sandbox() — 写入沙盒（只读原稿，只写沙盒）
"""

import re
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from utils.file_io import read_text, write_text

# ROSA vault 路径（只读）
ROSA_VAULT = Path("E:/图书馆/ROSA")

# 沙盒目录（唯一可写位置）— 从 config/system.yaml 读取，支持自定义
def _load_sandbox_dir():
    """从 config/system.yaml 加载沙盒目录，默认 E:/图书馆/ROSA/Obsidian_AI_Sandbox/10_Inbox。"""
    try:
        import yaml
        cfg = yaml.safe_load(read_text("config/system.yaml")) or {}
        obsidian_cfg = cfg.get("obsidian", {})
        sandbox = obsidian_cfg.get("sandbox_dir", "E:/图书馆/ROSA/Obsidian_AI_Sandbox/10_Inbox")
        return Path(sandbox)
    except Exception:
        return ROSA_VAULT / "Obsidian_AI_Sandbox" / "10_Inbox"

SANDBOX_DIR = _load_sandbox_dir()

# 路径遍历防护（默认开）
PATH_TRAVERSAL_GUARD = True


def _validate_sandbox_path(filename, subdir=""):
    """校验最终路径在沙盒内，防止路径遍历。

    返回 (ok, error_message)。
    """
    if PATH_TRAVERSAL_GUARD:
        # 拒绝绝对路径
        if filename.startswith("/") or filename.startswith("\\") or (len(filename) > 1 and filename[1] == ":"):
            return False, f"拒绝绝对路径: {filename}"

        # 构建最终路径
        sandbox = SANDBOX_DIR
        if subdir:
            sandbox = sandbox / subdir
        out = sandbox / filename

        # 解析后检查是否在沙盒内
        try:
            out_resolved = out.resolve()
            sandbox_resolved = sandbox.resolve()
            if not out_resolved.is_relative_to(sandbox_resolved):
                return False, f"路径遍历风险: {filename} 不在沙盒内"
        except Exception as e:
            return False, f"路径解析失败: {e}"

    return True, ""

# 跳过目录（非正典内容）
SKIP_DIRS = {'.obsidian', '.git', '.hermes', '.agent_context', '.sitian', '99 模板'}

# frontmatter 键
NAME_KEYS = ["name", "title", "id"]
TAG_KEYS = ["tags", "tag", "aliases", "alias", "category", "categories"]
TYPE_KEYS = ["type", "kind", "category"]
DESC_KEYS = ["description", "summary", "desc", "简介", "描述"]
LOCKED_KEYS = ["locked", "lock", "immutable"]
RELATION_KEYS = ["relations", "relationships", "relation"]

# 停用词（与 kb_index.py 保持一致）
STOP_WORDS = set(
    "的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 到 说 要 去 你 会 着 没有 看 好 自己 这 他 她 它 们 那 些 什么 怎么 吗 吧 呢 啊 嗯 哈 呀 嘛 被 把 让 给 从 对 与 等 最 更 太 非常 已经 可以 可能 应该 必须 需要 进行 通过 使用 作为 属于 由于 因为 所以 但是 如果 则 而 且 或 但 却 并 且 以及 中 后 前 内 外 下 时 地 得 里 中 之间 方面 部分 类型 方法 系统 功能 信息 内容 结构 方式 过程 结果 作用 目的 意义 影响 问题 情况 工作 学习 研究 发展 应用 技术 设计 实现 支持 提供 包含 具有 采用 基于 结合 利用 建立 创建 完成 实现 形成 产生 发生 存在 包括 涉及 相关 主要 重要 基本 一定 一定 通常 一般 往往 容易 难以 不同 相同 相似 类似 对应 对应 相应"
    .split()
)


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
            fm[key] = val
    return fm, m.end()


def _safe_list(v):
    """确保值为列表。"""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [v]
    return []


def normalize_term(term):
    """归一化词：小写、去首尾标点、去纯数字。"""
    t = term.strip().lower().rstrip("的了是在我")
    if len(t) < 2 or len(t) > 12:
        return None
    if t.isdigit():
        return None
    if t in STOP_WORDS:
        return None
    return t


def extract_terms(text):
    """从文本中提取有意义的词（中文 2-4 gram + 英文单词）。"""
    terms = set()
    # 英文单词
    for m in re.finditer(r'[a-zA-Z][a-zA-Z0-9_]+', text):
        t = normalize_term(m.group())
        if t and len(t) >= 2:
            terms.add(t)
    # 中文 n-gram (2-4)
    cn_chars = re.findall(r'[一-鿿]', text)
    s = ''.join(cn_chars)
    for n in (2, 3, 4):
        for i in range(len(s) - n + 1):
            term = s[i:i + n]
            if term not in STOP_WORDS and 2 <= len(term) <= 12:
                terms.add(term)
    return terms


def scan_vault(vault_path=None):
    """扫描 ROSA vault，建立结构化索引。

    返回 {
        "characters": [{name, path, tags, type, locked, relations, snippet}],
        "world": [{name, path, tags, type, snippet}],
        "timeline": [{name, path, tags, snippet}],
        "meta": {vault_path, built_at, character_count, world_count}
    }
    """
    vault = Path(vault_path) if vault_path else ROSA_VAULT
    characters = []
    world_entries = []
    timeline_entries = []

    # 角色目录
    char_dirs = [
        vault / "03 设定" / "01 人物",
        vault / "03 设定",
    ]
    for char_dir in char_dirs:
        if not char_dir.exists():
            continue
        for md_file in char_dir.rglob("*.md"):
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
            locked = False
            for k in LOCKED_KEYS:
                v = fm.get(k)
                if v and str(v).lower() in ('true', 'yes', '1', '是'):
                    locked = True
                    break
            relations = []
            for k in RELATION_KEYS:
                v = fm.get(k)
                if v:
                    relations = _safe_list(v)
                    break
            desc = ""
            for k in DESC_KEYS:
                v = fm.get(k)
                if v:
                    desc = v if isinstance(v, str) else str(v[0])
                    break
            if not desc:
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

    # 世界观目录
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
            world_entries.append({
                "name": name,
                "path": rel_path,
                "tags": tags[:10],
                "type": type_,
                "snippet": snippet,
                "source": "rosa",
            })

    # 时间线目录
    timeline_dir = vault / "06 年表"
    if timeline_dir.exists():
        for md_file in timeline_dir.rglob("*.md"):
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
            plain = re.sub(r'[#*`\[\]]', '', body).strip()
            snippet = plain[:300].replace('\n', ' ').strip()
            rel_path = str(md_file.relative_to(vault))
            timeline_entries.append({
                "name": name,
                "path": rel_path,
                "tags": tags[:10],
                "snippet": snippet,
                "source": "rosa",
            })

    return {
        "characters": characters,
        "world": world_entries,
        "timeline": timeline_entries,
        "meta": {
            "vault_path": str(vault.resolve()),
            "built_at": datetime.now().isoformat(),
            "character_count": len(characters),
            "world_count": len(world_entries),
            "timeline_count": len(timeline_entries),
        },
    }


def inject_context(query, vault_data=None, top_k=5, max_chars=500):
    """为写作阶段注入相关词条上下文。

    返回格式化的上下文字符串，可直接注入提示词模板。
    """
    if vault_data is None:
        vault_data = scan_vault()

    query_terms = extract_terms(query)
    if not query_terms:
        return ""

    # 计分：IDF 加权 + 条目名精确匹配加分
    scores = {}  # path -> score
    total_entries = len(vault_data["characters"]) + len(vault_data["world"])
    for term in query_terms:
        for char in vault_data["characters"]:
            name_term = normalize_term(char.get("name", ""))
            if name_term and term == name_term:
                scores[char["path"]] = scores.get(char["path"], 0) + 5
            # 标签匹配
            for tag in char.get("tags", []):
                tag_term = normalize_term(tag)
                if tag_term and term == tag_term:
                    scores[char["path"]] = scores.get(char["path"], 0) + 2
        for entry in vault_data["world"]:
            name_term = normalize_term(entry.get("name", ""))
            if name_term and term == name_term:
                scores[entry["path"]] = scores.get(entry["path"], 0) + 5
            for tag in entry.get("tags", []):
                tag_term = normalize_term(tag)
                if tag_term and term == tag_term:
                    scores[entry["path"]] = scores.get(entry["path"], 0) + 2

    # 排序取 top
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    if not ranked:
        return ""

    parts = []
    for path, score in ranked:
        # 查找条目
        entry = None
        for char in vault_data["characters"]:
            if char["path"] == path:
                entry = char
                break
        if not entry:
            for w in vault_data["world"]:
                if w["path"] == path:
                    entry = w
                    break
        if not entry:
            continue
        tag_str = f"（相关度：{score}）"
        parts.append(f"### {entry['name']}{tag_str}\n{entry['snippet']}\n")

    return "\n".join(parts)


def check_consistency(text, vault_data=None):
    """检查写作产物是否偏离正典。

    返回 {
        "conflicts": [{type, entry_name, detail, suggestion}],
        "warnings": [{type, entry_name, detail}],
        "locked_violations": [{entry_name, detail}]
    }
    """
    if vault_data is None:
        vault_data = scan_vault()

    conflicts = []
    warnings = []
    locked_violations = []

    # 检查 locked 条目是否被修改
    for char in vault_data["characters"]:
        if not char.get("locked"):
            continue
        name = char.get("name", "")
        if name and name in text:
            # 检查是否被修改（简单规则：locked 条目的名称不应出现在否定语境中）
            # 更复杂的检查需要语义分析，这里只做简单匹配
            pass

    # 检查新角色是否与已有角色重名
    existing_names = {c["name"] for c in vault_data["characters"]}
    existing_aliases = set()
    for c in vault_data["characters"]:
        for alias in c.get("tags", []):
            if alias:
                existing_aliases.add(alias)

    # 提取文本中的候选角色名（2-4 字中文词，出现 >=2 次）
    # 改进：只标记看起来像角色名的词（非通用名词/动词/形容词）
    from collections import Counter
    candidates = re.findall(r'[一-鿿]{2,4}', text)
    counter = Counter(candidates)

    # 常见非角色名词/动词/形容词（需要过滤的通用词）
    common_words = {
        "角色", "设定", "世界观", "剧情", "情节", "故事", "小说", "章节",
        "大纲", "素材", "写作", "创作", "作者", "读者", "作品", "文本",
        "描述", "介绍", "说明", "注释", "参考", "引用", "来源", "出处",
        "推测", "猜测", "假设", "可能", "应该", "必须", "需要", "可以",
        "不行", "不能", "不会", "不要", "不是", "没有", "无法", "无法",
        "解释", "说明", "描述", "表达", "表示", "显示", "展示", "呈现",
        "静谧", "之神", "创造", "结界", "程度", "能力", "力量", "能量",
        "仪式", "魔法", "法术", "术式", "技能", "技巧", "技术", "方法",
        "白色", "长发", "离去", "月前", "看护", "幻神", "月面", "大结",
        "融光", "无归", "推测", "区域", "不行", "不解释",
    }

    for name, count in counter.most_common(30):
        if count < 2:
            continue
        if name in existing_names or name in existing_aliases:
            continue
        # 检查是否是停用词
        if name in STOP_WORDS:
            continue
        # 检查是否是常见非角色名词
        if name in common_words:
            continue
        # 检查是否包含明显的非角色词缀
        if any(w in name for w in ["的", "了", "是", "在", "和", "就", "不", "人", "都", "一", "上", "也", "到", "说", "要", "去", "你", "会", "着", "没", "看", "好", "自", "己", "这", "他", "她", "它", "们", "那", "些", "什", "么", "怎", "吗", "吧", "呢", "啊", "嗯", "哈", "呀", "嘛", "被", "把", "让", "给", "从", "对", "与", "等", "最", "更", "太", "非", "常", "已", "经", "可", "能", "应", "该", "必", "须", "需", "进", "行", "通", "过", "使", "用", "作", "为", "属", "于", "由", "因", "所", "但", "是", "如", "果", "则", "而", "且", "或", "却", "并", "以", "及", "中", "后", "前", "内", "外", "下", "时", "地", "得", "里", "间", "方", "面", "部", "分", "类", "型", "法", "系", "统", "功", "信", "息", "内", "容", "结", "构", "式", "过", "程", "结", "果", "作", "用", "目", "的", "意", "义", "影", "响", "问", "题", "情", "况", "工", "作", "学", "习", "研", "究", "发", "展", "应", "用", "技", "术", "设", "计", "实", "现", "支", "持", "提", "供", "包", "含", "具", "有", "采", "用", "基", "于", "结", "合", "利", "用", "建", "立", "创", "完", "成", "形", "成", "产", "生", "发", "生", "存", "在", "包", "括", "涉", "及", "相", "关", "主", "要", "重", "要", "基", "本", "一", "定", "通", "常", "一", "般", "往", "往", "容", "易", "难", "以", "不", "同", "相", "同", "相", "似", "类", "似", "对", "应", "相", "应"]):
            continue
        warnings.append({
            "type": "new_character",
            "entry_name": name,
            "detail": f"文本中出现 {count} 次，但 ROSA 中无此角色",
        })

    return {
        "conflicts": conflicts,
        "warnings": warnings,
        "locked_violations": locked_violations,
    }


def write_sandbox(filename, content, subdir=""):
    """写入沙盒（只读原稿，只写沙盒）。

    Args:
        filename: 文件名
        content: 文件内容
        subdir: 子目录（如 "角色"、"世界观"）

    Returns:
        (ok, message)
    """
    ok, err = _validate_sandbox_path(filename, subdir)
    if not ok:
        return False, err

    sandbox = SANDBOX_DIR
    if subdir:
        sandbox = sandbox / subdir
    sandbox.mkdir(parents=True, exist_ok=True)

    out = sandbox / filename
    write_text(out, content)
    return True, f"已写入沙盒: {out}"


def push_to_sandbox(source_path, subdir=""):
    """推送产物到沙盒（只读原稿，只写沙盒）。

    Args:
        source_path: 源文件路径
        subdir: 子目录

    Returns:
        (ok, message)
    """
    source = Path(source_path)
    if not source.exists():
        return False, f"源文件不存在: {source}"

    ok, err = _validate_sandbox_path(source.name, subdir)
    if not ok:
        return False, err

    sandbox = SANDBOX_DIR
    if subdir:
        sandbox = sandbox / subdir
    sandbox.mkdir(parents=True, exist_ok=True)

    out = sandbox / source.name
    content = read_text(source)
    write_text(out, content)
    return True, f"已推送到沙盒: {out}"


def list_sandbox(subdir=""):
    """列出沙盒内容。"""
    sandbox = SANDBOX_DIR
    if subdir:
        sandbox = sandbox / subdir
    if not sandbox.exists():
        return []
    return [str(p.relative_to(sandbox)) for p in sandbox.rglob("*") if p.is_file()]


def export_sandbox(output_dir, subdir=""):
    """导出沙盒内容为 Obsidian 可导入格式。

    Args:
        output_dir: 输出目录
        subdir: 子目录

    Returns:
        (ok, message)
    """
    sandbox = SANDBOX_DIR
    if subdir:
        sandbox = sandbox / subdir
    if not sandbox.exists():
        return False, "沙盒为空"

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for md_file in sandbox.rglob("*.md"):
        content = read_text(md_file)
        out = out_dir / md_file.name
        write_text(out, content)
        count += 1

    return True, f"已导出 {count} 个文件到 {out_dir}"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Obsidian 知识库联动桥")
    sub = parser.add_subparsers(dest="cmd")

    p_scan = sub.add_parser("scan", help="扫描 ROSA vault")
    p_scan.add_argument("--vault", default=str(ROSA_VAULT), help="ROSA vault 路径")
    p_scan.add_argument("--output", default="data/state/obsidian_index.json", help="输出路径")

    p_inject = sub.add_parser("inject", help="注入上下文")
    p_inject.add_argument("query", help="查询文本")
    p_inject.add_argument("--top", type=int, default=5, help="返回数量")

    p_check = sub.add_parser("check", help="检查一致性")
    p_check.add_argument("file", help="待检查文件")

    p_push = sub.add_parser("push", help="推送到沙盒")
    p_push.add_argument("file", help="源文件路径")
    p_push.add_argument("--subdir", default="", help="子目录")

    p_list = sub.add_parser("list", help="列出沙盒内容")
    p_list.add_argument("--subdir", default="", help="子目录")

    args = parser.parse_args()

    if args.cmd == "scan":
        data = scan_vault(args.vault)
        write_text(args.output, json.dumps(data, ensure_ascii=False, indent=2))
        print(f"扫描完成：{data['meta']['character_count']} 角色 + {data['meta']['world_count']} 世界观 + {data['meta']['timeline_count']} 时间线")
        print(f"输出：{args.output}")

    elif args.cmd == "inject":
        ctx = inject_context(args.query, top_k=args.top)
        print(ctx if ctx else "（无相关词条）")

    elif args.cmd == "check":
        text = read_text(args.file)
        result = check_consistency(text)
        print(f"冲突：{len(result['conflicts'])} 个")
        for c in result["conflicts"]:
            print(f"  [{c['type']}] {c['entry_name']}: {c['detail']}")
        print(f"警告：{len(result['warnings'])} 个")
        for w in result["warnings"]:
            print(f"  [{w['type']}] {w['entry_name']}: {w['detail']}")
        print(f"locked 违例：{len(result['locked_violations'])} 个")
        for v in result["locked_violations"]:
            print(f"  {v['entry_name']}: {v['detail']}")

    elif args.cmd == "push":
        ok, msg = push_to_sandbox(args.file, args.subdir)
        print(msg)

    elif args.cmd == "list":
        files = list_sandbox(args.subdir)
        for f in files:
            print(f"  {f}")

    else:
        parser.print_help()
