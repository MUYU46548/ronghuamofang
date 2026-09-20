# -*- coding: utf-8 -*-
"""Obsidian 知识库联动桥（只读 + 沙盒写入）。

设计原则：
- Vault 本体只读——所有产物写入沙盒（默认 Obsidian_AI_Sandbox/10_Inbox/）
- frontmatter 是唯一事实源；文件名 stem 仅作 fallback
- locked 条目不可违逆（写作时必须遵循）
- 新角色自动标记为 candidate，供 obsidian_postprocess 生成设定草稿

功能：
1. scan_vault() — 扫描 Obsidian vault，建立结构化索引
2. inject_context() — 为写作阶段注入相关词条
3. check_consistency() — 检查写作产物是否偏离正典
4. write_sandbox() — 写入沙盒（只读原稿，只写沙盒）

配置（config/system.yaml 的 obsidian 节点）：
- sandbox_dir: 沙盒目录（唯一可写位置）；留空 → 回落项目内 data/state/obsidian_sandbox/
- vault_path: vault 路径（用户本地知识库；留空 = 未启用联动）
- path_traversal_guard: 路径遍历防护（默认开）
"""

import re
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from utils.file_io import read_text, write_text

# 默认配置（被 config/system.yaml 的 obsidian 节点覆盖）
# 刻意**不设**任何用户本机绝对路径：vault 未配置时视为「未启用联动」，
# 沙盒未配置时回落到项目内目录，保证开箱即用且不泄露任何私人路径。
DEFAULT_VAULT_PATH = ""
DEFAULT_SANDBOX_DIR = "data/state/obsidian_sandbox"


def _load_obsidian_config():
    """从 config/system.yaml 加载 Obsidian 配置。"""
    try:
        import yaml
        cfg = yaml.safe_load(read_text("config/system.yaml")) or {}
        return cfg.get("obsidian", {})
    except Exception:
        return {}


def get_vault_path():
    """获取 vault 路径（未配置时为 None）。

    未配置（空）时返回 None —— 调用方应视为「未启用 Obsidian 联动」，
    而不是回落到某个写死的本机路径（那会让其他用户开箱即失败）。

    注意：**不要**返回 `Path("")` —— 它 str() 后是 `"."`（当前目录），
    会让「是否已配置」的判断永远为真，从而把未启用误判为已启用。
    """
    cfg = _load_obsidian_config()
    raw = (cfg.get("vault_path") or DEFAULT_VAULT_PATH or "").strip()
    return Path(raw) if raw else None


def get_sandbox_dir():
    """获取沙盒目录（唯一可写位置）。

    未配置时回落项目内 data/state/obsidian_sandbox/ —— 开箱即用，
    有配置则用配置（支持绝对路径或相对项目根的路径）。
    """
    cfg = _load_obsidian_config()
    raw = (cfg.get("sandbox_dir") or DEFAULT_SANDBOX_DIR or "").strip()
    return Path(raw) if raw else Path(DEFAULT_SANDBOX_DIR)


def is_vault_configured():
    """vault 是否已配置（联动功能的总开关）。"""
    return get_vault_path() is not None


# 延迟初始化（避免导入时读取配置文件）
_vault_path = None
_sandbox_dir = None


def _get_vault_path():
    global _vault_path
    if _vault_path is None:
        _vault_path = get_vault_path()
    return _vault_path


def _get_sandbox_dir():
    global _sandbox_dir
    if _sandbox_dir is None:
        _sandbox_dir = get_sandbox_dir()
    return _sandbox_dir


def _reload_config():
    """重新加载配置（测试/配置变更时调用）。"""
    global _vault_path, _sandbox_dir
    _vault_path = get_vault_path()
    _sandbox_dir = get_sandbox_dir()


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
    """扫描 Obsidian vault，建立结构化索引。

    返回 {
        "characters": [{name, path, tags, type, locked, relations, snippet}],
        "world": [{name, path, tags, type, snippet}],
        "timeline": [{name, path, tags, snippet}],
        "meta": {vault_path, built_at, character_count, world_count}
    }
    """
    vault = Path(vault_path) if vault_path else _get_vault_path()
    if vault is None:
        raise ValueError(
            "未配置 Obsidian vault 路径：请在 config/system.yaml 的 "
            "obsidian.vault_path 填写你的知识库目录（vault 本体只读；"
            "不用 Obsidian 联动可忽略本功能）。")
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
                "source": "vault",
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
                "source": "vault",
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
                "source": "vault",
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

    # 检查新角色是否与已有角色重名
    existing_names = {c["name"] for c in vault_data["characters"]}
    existing_aliases = set()
    for c in vault_data["characters"]:
        for alias in c.get("tags", []):
            if alias:
                existing_aliases.add(alias)

    # 提取文本中的候选角色名（2-4 字中文词，出现 >=2 次）
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
        warnings.append({
            "type": "new_character",
            "entry_name": name,
            "detail": f"文本中出现 {count} 次，但 vault 中无此角色",
        })

    return {
        "conflicts": conflicts,
        "warnings": warnings,
        "locked_violations": locked_violations,
    }


def _validate_sandbox_path(filename, subdir=""):
    """校验最终路径在沙盒内，防止路径遍历。

    返回 (ok, error_message)。
    """
    cfg = _load_obsidian_config()
    path_traversal_guard = cfg.get("path_traversal_guard", True)

    if path_traversal_guard:
        # 拒绝绝对路径
        if filename.startswith("/") or filename.startswith("\\") or (len(filename) > 1 and filename[1] == ":"):
            return False, f"拒绝绝对路径: {filename}"

        # 构建最终路径
        sandbox = _get_sandbox_dir()
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

    sandbox = _get_sandbox_dir()
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

    sandbox = _get_sandbox_dir()
    if subdir:
        sandbox = sandbox / subdir
    sandbox.mkdir(parents=True, exist_ok=True)

    out = sandbox / source.name
    content = read_text(source)
    write_text(out, content)
    return True, f"已推送到沙盒: {out}"


def list_sandbox(subdir=""):
    """列出沙盒内容。"""
    sandbox = _get_sandbox_dir()
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
    sandbox = _get_sandbox_dir()
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

    p_scan = sub.add_parser("scan", help="扫描 Obsidian vault")
    p_scan.add_argument("--vault", default=None, help="vault 路径（覆盖配置）")
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

    p_config = sub.add_parser("config", help="显示当前配置")

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

    elif args.cmd == "config":
        print(f"vault_path: {_get_vault_path()}")
        print(f"sandbox_dir: {_get_sandbox_dir()}")

    else:
        parser.print_help()
