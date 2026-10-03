# -*- coding: utf-8 -*-
"""Obsidian 知识库联动桥（只读 + 沙盒写入）。

设计原则：
- Vault 本体只读——所有产物写入沙盒（默认项目内 `data/state/obsidian_sandbox/`，
  可在 `config/system.yaml` 的 `obsidian.sandbox_dir` 指到你自己库内的收件目录）
- frontmatter 是唯一事实源；文件名 stem 仅作 fallback
- locked 条目不可违逆（写作时必须遵循）
- 新角色自动标记为 candidate，供 obsidian_postprocess 生成设定草稿

功能：
1. scan_vault() — 扫描 Obsidian vault，建立结构化索引
2. inject_context() — 为写作阶段注入相关词条
3. check_consistency() — 检查写作产物是否偏离正典
4. write_sandbox() — 写入沙盒（只读原稿，只写沙盒）

配置（config/system.yaml 的 obsidian 节点，支持 system.local.yaml 本地覆盖）：
- sandbox_dir: 沙盒目录（唯一可写位置）；留空 → 回落项目内 data/state/obsidian_sandbox/
- vault_path: vault 路径（用户本地知识库；留空 = 未启用联动）
- path_traversal_guard: 路径遍历防护（默认开）
- vault_dirs: 目录映射五键（characters/locations/factions/concepts/timeline，
  默认=通用虚构布局；每人真实结构在 system.local.yaml 覆盖——A1 配置化）
- skip_dirs: 跳过目录（默认通用四项；私人项走本地覆盖）
"""

import re
import json
from pathlib import Path
from datetime import datetime

from utils.file_io import read_text, write_text
# locked 语义与别名根名的**唯一实现**在 setting_schema（不在本文件重写一份）。
# obsidian 节配置读取（含 system.local.yaml 本地覆盖，拍板③「手动指定」载体）
# 也**单源**在 setting_schema——本文件不自持副本，防两处漂移（A1 修复）。
from utils.setting_schema import (
    base_name, is_locked, is_character,
    load_obsidian_config, get_vault_dirs, get_skip_dirs,
    reset_obsidian_config_cache,
)

# 默认配置（被 config/system.yaml 的 obsidian 节点覆盖）
# 刻意**不设**任何用户本机绝对路径：vault 未配置时视为「未启用联动」，
# 沙盒未配置时回落到项目内目录，保证开箱即用且不泄露任何私人路径。
DEFAULT_VAULT_PATH = ""
DEFAULT_SANDBOX_DIR = "data/state/obsidian_sandbox"


def _load_obsidian_config():
    """加载 Obsidian 配置（system.yaml + system.local.yaml 键级覆盖，单源转发）。

    实现在 utils.setting_schema.load_obsidian_config。配置文件变更后
    应调 _reload_config()（其内部会清 setting_schema 的配置缓存）。
    """
    return load_obsidian_config()


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
    global _vault_path, _sandbox_dir, _skip_dirs
    reset_obsidian_config_cache()
    _vault_path = get_vault_path()
    _sandbox_dir = get_sandbox_dir()
    _skip_dirs = get_skip_dirs()


# 跳过目录（非正典内容）——A1 配置化：常量已删，改由 utils.setting_schema
# 的 get_skip_dirs() 提供（默认通用四项：.obsidian/.git/.hermes/.agent_context）。
# 私人项（数字前缀模板目录、其他项目目录等）由用户在 system.local.yaml 的
# obsidian.skip_dirs 里追加——发布物零私人痕迹（拍板③）。
_skip_dirs = get_skip_dirs()

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

    # 角色目录。⚠️ 2026-09-21 修复**跨类目污染与重复**：
    # 原写法把**父目录**（设定根）也放进列表并配 `rglob`，于是它递归扫到了
    # 地点/概念等兄弟类目，把地点名、法规名这类条目也当成角色，
    # 且角色目录下的条目被扫两遍（各重复一次）—— 实测一个小 vault 就扫出重复角色，
    # 加上了若干本该归 world 的条目。这正是归档数据「characters 数组里大半不是人」
    # 的生产者侧根因。
    #
    # 现在**只认角色目录**（rglob，它有子类目）。这与 `utils.setting_schema.path_kind`
    # 的判据一致：`<设定根>\<角色目录>\<子类目>\<名>.md` 才是人物，
    # `<设定根>\<其他类目>\...` 不是。
    #
    # 刻意**不把设定根放进角色列表**：直接躺在设定根下的松散文件
    # （总纲/说明/索引页）绝大多数不是人物，归入 world 更合理（见下）。
    # 早先版本把父目录同时放进 char/world 两处，靠"角色优先"的排除规则决出归属，
    # 会把「散装说明」这类条目判成角色。
    vd = get_vault_dirs()

    def _sub(rel):
        """vault 下相对目录：`设定/人物` → vault/设定/人物（段数任意，按 `/` 或 `\\` 拆）。"""
        return Path(vault, *[s for s in str(rel).replace("\\", "/").split("/") if s])

    def _root_of(rel):
        """目录键的第一段（「设定/人物」→「设定」）：世界观散装兜底与角色根同源。"""
        seg = [s for s in str(rel).replace("\\", "/").split("/") if s]
        return seg[0] if seg else ""

    def _warn_missing(path, key, value):
        """A1 修法 2：零扫告警——目录落空不再静默 continue。"""
        print(f"[obsidian_bridge] ⚠️ 目录不存在、跳过：{path}（vault_dirs.{key}={value!r}）"
              "——若与你的 vault 结构不符，请在 config/system.yaml 或 "
              "config/system.local.yaml 的 obsidian.vault_dirs 填真实目录。")

    # 角色根 = vault_dirs.characters（默认「设定/人物」；用户真实结构走本地覆盖）。
    char_dirs = [
        (_sub(vd["characters"]), True),
    ]
    for char_dir, recursive in char_dirs:
        if not char_dir.exists():
            _warn_missing(char_dir, "characters", vd["characters"])
            continue
        for md_file in (char_dir.rglob("*.md") if recursive
                        else char_dir.glob("*.md")):
            if any(part in _skip_dirs for part in md_file.relative_to(vault).parts):
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

    # 世界观目录。同样：类目 rglob，父目录仅 glob（见上面的污染说明）。
    # 设定根兜底 = characters 键的第一段（「设定/人物」→「设定」），与角色根同源。
    world_dirs = [
        (_sub(vd["locations"]), True),
        (_sub(vd["factions"]), True),
        (_sub(vd["concepts"]), True),
        (_sub(_root_of(vd["characters"])), False),   # 兜底：仅直接子文件
    ]
    for world_dir, recursive in world_dirs:
        if not world_dir.exists():
            continue  # 类目/兜底缺失不告警（根缺失时 characters 已告警过一次）
        for md_file in (world_dir.rglob("*.md") if recursive
                        else world_dir.glob("*.md")):
            if any(part in _skip_dirs for part in md_file.relative_to(vault).parts):
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

    # 时间线目录 = vault_dirs.timeline（默认「年表」）
    timeline_dir = _sub(vd["timeline"])
    if not timeline_dir.exists():
        _warn_missing(timeline_dir, "timeline", vd["timeline"])
    if timeline_dir.exists():
        for md_file in timeline_dir.rglob("*.md"):
            if any(part in _skip_dirs for part in md_file.relative_to(vault).parts):
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

    # 去重 + 跨类目排除。
    # 去重键用 `path` 而非 `name`：同名异实体是合法的
    # （真实数据里「鲸民」是种族、「鲸民之城」是地点）。
    # 排除是为了防「同一个文件既算角色又算世界观」——父目录 glob 与类目 rglob
    # 理论上不相交，但配置变更/符号链接下可能重叠，这里显式兜住。
    _seen = set()
    _uniq_chars = []
    for c in characters:
        if c["path"] in _seen:
            continue
        _seen.add(c["path"])
        _uniq_chars.append(c)
    characters = _uniq_chars
    _char_paths = {c["path"] for c in characters}

    _seen = set()
    _uniq_world = []
    for w in world_entries:
        if w["path"] in _seen or w["path"] in _char_paths:
            continue
        _seen.add(w["path"])
        _uniq_world.append(w)
    world_entries = _uniq_world

    _seen = set()
    _uniq_tl = []
    for t in timeline_entries:
        if t["path"] in _seen:
            continue
        _seen.add(t["path"])
        _uniq_tl.append(t)
    timeline_entries = _uniq_tl

    # A1 修法 2：零扫汇总告警。vault 已启用却三类全空 ≈ vault_dirs 与真实
    # 结构不匹配（旧版此时静默返回全空、下游注入恒空——F4 的近亲）。
    if not (characters or world_entries or timeline_entries):
        print("[obsidian_bridge] ⚠️ vault 已启用但扫描结果全空——大概率是 "
              "obsidian.vault_dirs 与你的 vault 真实结构不匹配。"
              "每人 Obsidian 布局不同：请在 config/system.yaml 或 "
              "config/system.local.yaml 的 obsidian.vault_dirs 里填入你的"
              "真实目录（角色/地点/势力/概念/年表五键），无需改代码。")

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


# ---------------------------------------------------------------- locked 违例（2026-10-01）

def _iter_setting_entries(setting):
    """设定集 → [(来源区块, 条目)]。只取顶层「list of dict」区块。

    刻意**不递归**：设定集是 `{"characters": [...], "world": [...],
    "timeline": [...]}` 这样的结构，locked 条目就躺在这些列表里。
    递归下去只会把角色条目内部的 relations 当作独立条目。
    """
    out = []
    if not isinstance(setting, dict):
        return out
    for key, val in setting.items():
        if not isinstance(val, list):
            continue
        for it in val:
            if isinstance(it, dict):
                out.append((str(key), it))
    return out


def _norm_path(p):
    """路径归一（只用于「是不是同一条目」的判定，不用于写盘）。"""
    return str(p or "").replace("/", "\\").strip().strip("\\").lower()


def _match_setting_entry(entries, path=None, name=None):
    """在设定集条目里找匹配项。优先级：vault `path` → `name` → 别名根名。

    抽成函数是为了让 `check_locked_violations` 与 `sync_diff` 用**同一份**匹配判据
    —— 本仓吃过「判据复制 N 份 = 同一 bug 修两遍只修一处」的亏（见 MEMORY）。
    返回 `(来源区块, 条目, 命中方式)` 或 `None`。
    """
    lpath = _norm_path(path)
    lname = str(name or "").strip()
    lroot = base_name(lname)
    if lpath:
        for where, e in entries:
            if _norm_path(e.get("path")) == lpath:
                return (where, e, "path")
    if lname:
        for where, e in entries:
            if str(e.get("name") or "").strip() == lname:
                return (where, e, "name")
    if lroot:
        for where, e in entries:
            if base_name(str(e.get("name") or "")) == lroot:
                return (where, e, "alias")
    return None


def check_locked_violations(vault_data=None, setting_path=None):
    """确定性检查：vault 里 `locked=true` 的条目是否在最终设定集中被删除 / 丢锁定 / 改名。

    ## 诚实边界：只能做「结构性违逆」

    「locked 不可违逆」的**完整**语义是"不许与它冲突"（语义级）——机器判不了
    "本章事实是否与 locked 设定矛盾"，硬做只会产出噪音。但违逆有几种
    **结构性**表现是确定可判的，本函数只做这几种：

    | type | 判据 | 真实危害 |
    |---|---|---|
    | `missing` | locked 条目在设定集里**找不到**（依次按 vault path / name / 别名根名匹配） | stage1 归并把硬约束条目弄丢 |
    | `lock_lost` | 条目还在，但 `locked` 标志**不为真** | 角色卡不再打印「⚠ 不可违逆」→ **静默降级** |
    | `renamed` | 设定集里同 vault path 的条目**换了名字** | 改名绕过硬约束 |

    ## ⚠️ 空结果 ≠ 通过

    两种情况本检查**不生效**，此时明确回报 `checked=False` + `reason`：
    ① vault 里一个 `locked=true` 都没有；② 最终设定集尚未生成。
    这是刻意的 —— 一个永远说"没问题"的检查器比没有检查器更危险
    （本模块此前就吃过这个亏：`locked_violations` 曾经恒为空列表）。

    返回 dict：{violations, locked_total, matched, checked, reason, setting_path}
    """
    result = {"violations": [], "locked_total": 0, "matched": 0,
              "checked": False, "reason": "", "setting_path": ""}

    if vault_data is None:
        vault_data = scan_vault()

    locked = [c for c in (vault_data or {}).get("characters", []) if c.get("locked")]
    result["locked_total"] = len(locked)
    if not locked:
        result["reason"] = ("vault 中没有任何 locked=true 条目 → 本检查未生效"
                            "（空结果 ≠ 无违例）")
        return result

    path = Path(setting_path) if setting_path else Path("data/setting/setting.json")
    result["setting_path"] = str(path)
    if not path.exists():
        result["reason"] = ("最终设定集不存在（" + str(path) + "）→ 无从比较"
                            "（空结果 ≠ 无违例）")
        return result
    try:
        setting = json.loads(read_text(path))
    except Exception as e:                                    # noqa: BLE001
        result["reason"] = "设定集解析失败：" + repr(e)
        return result

    entries = _iter_setting_entries(setting)
    for lc in locked:
        lname = str(lc.get("name") or "").strip()
        lpath = str(lc.get("path") or "").strip()

        # 匹配优先级：path（最稳，但只有 vault 路径写出的条目才有）→ name → 别名根名
        # （判据提取在 _match_setting_entry，与 sync_diff 共用同一份）
        hit = _match_setting_entry(entries, lpath, lname)

        if hit is None:
            result["violations"].append({
                "type": "missing", "name": lname, "path": lpath,
                "detail": ("locked 条目「" + (lname or lpath) + "」在最终设定集中"
                           "找不到 —— 硬约束已丢失"),
            })
            continue

        where, entry, how = hit
        result["matched"] += 1
        ename = str(entry.get("name") or "").strip()

        if (how == "path" and lname and ename
                and ename != lname and base_name(ename) != base_name(lname)):
            result["violations"].append({
                "type": "renamed", "name": lname, "to": ename, "where": where,
                "detail": ("locked 条目「" + lname + "」被改名为「" + ename +
                           "」（同一 vault 路径，名字被换）"),
            })
            continue

        if not is_locked(entry):
            result["violations"].append({
                "type": "lock_lost", "name": lname, "where": where,
                "detail": ("设定集条目「" + (ename or lname) + "」的 locked 标志丢失"
                           " —— 写作注入时不再提示「不可违逆」"),
            })

    result["checked"] = True
    return result


def _keyset(entries, get_path, get_name):
    """条目列表 → (path 集, name 集, 别名根名集)，用于「存在性」并集判定。"""
    paths, names, roots = set(), set(), set()
    for e in entries:
        p = _norm_path(get_path(e))
        if p:
            paths.add(p)
        n = str(get_name(e) or "").strip()
        if n:
            names.add(n)
            r = base_name(n)
            if r:
                roots.add(r)
    return paths, names, roots


def sync_diff(vault_data=None, setting_path=None):
    """vault ↔ 设定集 **内容级对账**（A4 同步 diff 闸门；确定性、零 token）。

    一条命令报全四类差异，而不是让用户东跑一个命令西跑一个：

    | 类别 | 判据 | 危害 / 处置 |
    |---|---|---|
    | `missing` | vault 有、设定集没有 | canon 被 stage1 归并吞掉 → 必须处理 |
    | `added` | 设定集有、vault 没有 | stage1 新造的实体 → 人工确认后回写 vault |
    | `renamed` | 同一 vault 路径，名字被换 | 改名绕过 locked 硬约束 |
    | `lock_lost` | 条目还在，但 `locked` 标志丢了 | 角色卡不再打印「⚠ 不可违逆」→ **静默降级** |

    `renamed` / `lock_lost` 直接复用 `check_locked_violations`（那份判据只做
    「结构性违逆」—— 语义冲突机器判不了，硬做只会变噪音），**不另写一份匹配逻辑**。

    ## ⚠️ 空结果 ≠ 一致

    vault 未配置 / 无角色条目 / 设定集未生成 → `checked=False` + `reason`，
    明确回报"没查"。绝不让「0 个差异」被读成"对得上"。

    `missing`/`added` 的存在性判定用「path ∨ name ∨ 别名根名」的**并集**
    （宽松，宁少报不多报），与 locked 检查「三者全不匹配才算 missing」等价。

    返回 dict：{checked, reason, vault_total, setting_total, setting_path,
                 missing, added, renamed, lock_lost, locked}
    """
    result = {
        "checked": False, "reason": "",
        "vault_total": 0, "setting_total": 0, "setting_path": "",
        "missing": [], "added": [], "renamed": [], "lock_lost": [],
    }
    if vault_data is None:
        vault_data = scan_vault()
    vchars = list((vault_data or {}).get("characters", []))
    result["vault_total"] = len(vchars)
    if not vchars:
        result["reason"] = ("vault 未配置或没有任何角色条目 → 无从对账"
                            "（空结果 ≠ 一致）")
        return result

    path = Path(setting_path) if setting_path else Path("data/setting/setting.json")
    result["setting_path"] = str(path)
    if not path.exists():
        result["reason"] = ("最终设定集不存在（" + str(path) + "）→ 无从对账"
                            "（空结果 ≠ 一致）")
        return result
    try:
        setting = json.loads(read_text(path))
    except Exception as e:                                    # noqa: BLE001
        result["reason"] = "设定集解析失败：" + repr(e)
        return result

    entries = _iter_setting_entries(setting)
    result["setting_total"] = len(entries)

    s_paths, s_names, s_roots = _keyset([e for _, e in entries],
                                        lambda e: e.get("path"),
                                        lambda e: e.get("name"))
    v_paths, v_names, v_roots = _keyset(vchars,
                                        lambda c: c.get("path"),
                                        lambda c: c.get("name"))

    def _present(p, n, paths, names, roots):
        p = _norm_path(p)
        n = str(n or "").strip()
        r = base_name(n)
        return bool((p and p in paths) or (n and n in names) or (r and r in roots))

    # vault → 设定集：canon 条目丢了
    for c in vchars:
        if _present(c.get("path"), c.get("name"), s_paths, s_names, s_roots):
            continue
        result["missing"].append({
            "name": str(c.get("name") or "").strip(),
            "path": str(c.get("path") or "").strip()})

    # 设定集 → vault：新增实体（只算被判为「角色」的条目，避免世界观条目刷屏）
    for where, e in entries:
        if not is_character(e):
            continue
        if _present(e.get("path"), e.get("name"), v_paths, v_names, v_roots):
            continue
        result["added"].append({
            "name": str(e.get("name") or "").strip(), "where": where,
            "path": str(e.get("path") or "").strip()})

    lock = check_locked_violations(vault_data, setting_path)
    for v in lock["violations"]:
        if v["type"] == "renamed":
            result["renamed"].append(v)
        elif v["type"] == "lock_lost":
            result["lock_lost"].append(v)
    result["locked"] = lock

    result["checked"] = True
    return result


def check_consistency(text, vault_data=None):
    """检查写作产物是否偏离正典。

    返回 {
        "conflicts": [...],            # ⚠ 恒为空，见下
        "warnings": [...],             # 疑似新角色（启发式，召回有限）
        "locked_violations": [...],    # ✅ 已实现（仅结构性违逆，见 check_locked_violations）
        "locked_report": {...},        # locked 检查的元信息（是否生效 / 为何未生效）
        "implemented": [...],          # 本次**真正执行**的检查项
        "unimplemented": [...],        # **未实现**的检查项（空结果 ≠ 无问题）
        "caveat": str,                 # 使用前必读的说明
    }

    ## ⚠️ 2026-09-21 诚实化（此前是「假安心」）

    本函数此前**恒返回全零**，而调用方/CLI 会打印「冲突：0 个 / 警告：0 个 /
    locked 违例：0 个」—— 看起来像「一致性检查通过」，实际是**什么都没查**。
    这正是本项目最忌讳的假成功：一个永远说"没问题"的检查器比没有检查器更危险，
    因为它制造虚假信心。

    实测（2026-09-21）：输入「苏芷与观澜在砾城对峙，观澜质问封锁法令。」
    （其中「观澜」是 vault 中不存在的角色、且提到了 locked 的「苏芷」），
    三个计数**全是 0**。

    两个具体原因：
    1. `conflicts` / `locked_violations` 是**硬编码的空列表**，代码里从未 append。
       「locked 不可违逆」目前只是**提示级**约束（`build_character_card` 会在
       角色卡里写一行「⚠ locked 条目，绝对不可违逆」），**没有任何验证器**。
       要做成检查级需要语义判断（正典事实 vs 本章事实），属设计决策，未实现。
    2. `warnings` 用 `re.findall(r'[一-鿿]{2,4}', text)` —— 对连续汉字串做**贪心
       切片**，切出的 4 字块与人名边界完全对不上。「苏芷与观澜在砾城对峙」被切成
       「苏芷与观」/「潮神在砾城」/「对峙」，**「观澜」根本不会作为一个候选出现**。
       中文未登录人名识别需要分词器（jieba 之类），本项目未引入该依赖。

    因此现在**显式声明**实现范围，让调用方无法把空结果误读为"通过"。

    ## ✅ 2026-10-01：`locked_violations` 落地（只做结构性判据）

    `locked` 检查接上了，但**只覆盖结构性违逆**：条目在最终设定集中
    缺失 / 丢了 locked 标记 / 被改名（判据见 `check_locked_violations`）。
    语义级冲突（"本章事实是否与 locked 设定矛盾"）**依旧不碰** ——
    机器判不准，硬做只会把噪音塞进报告。

    元信息放在返回值的 `locked_report` 里：`checked=False` 表示本次检查
    **没有生效**（vault 无 locked 条目，或设定集尚未生成）。调用方必须
    据此区分「查过且没问题」与「根本没查」。
    """
    if vault_data is None:
        vault_data = scan_vault()

    conflicts = []
    warnings = []
    # locked 检查（确定性）。⚠️ 不吞异常：拿不到结论时必须显式说明「没查」，
    # 而不是静默返回空列表 —— 后者正是本函数此前被诟病的那种假安心。
    try:
        locked_report = check_locked_violations(vault_data=vault_data)
    except Exception as e:                                    # noqa: BLE001
        locked_report = {"violations": [], "locked_total": 0, "matched": 0,
                         "checked": False, "reason": "locked 检查异常：" + repr(e),
                         "setting_path": ""}
    locked_violations = locked_report["violations"]

    existing_names = {c["name"] for c in vault_data["characters"]}
    existing_aliases = set()
    for c in vault_data["characters"]:
        for alias in c.get("tags", []):
            if alias:
                existing_aliases.add(alias)

    from collections import Counter
    # 改用**滑动窗口**（每个位置取 2/3/4 字）收集候选，替代原来的贪心切片。
    # 这能显著提高召回（「观澜」在「苏芷与观澜在」里能被窗口覆盖到），
    # 但代价是候选里混入大量跨词边界的噪声 —— 靠下面的 known-name 剔除 +
    # 停用词 + 频次阈值压制。**这不是分词，召回与精确率都是启发式的。**
    cn_runs = re.findall(r'[一-鿿]+', text)
    candidates = []
    for run in cn_runs:
        for n in (2, 3, 4):
            for i in range(len(run) - n + 1):
                candidates.append(run[i:i + n])
    counter = Counter(candidates)

    # 与已知角色名/别名有重叠的候选一律剔除（避免把「苏芷与」「汐与暮」当成新角色）
    known = {n for n in (existing_names | existing_aliases) if n}

    def _overlaps_known(cand):
        return any(k and (k in cand or cand in k) for k in known)

    # 常见非角色词（原表保留；跨词边界的噪声主要靠频次阈值过滤）
    common_words = {
        "角色", "设定", "世界观", "剧情", "情节", "故事", "小说", "章节",
        "大纲", "素材", "写作", "创作", "作者", "读者", "作品", "文本",
        "描述", "介绍", "说明", "注释", "参考", "引用", "来源", "出处",
        "推测", "猜测", "假设", "可能", "应该", "必须", "需要", "可以",
        "不行", "不能", "不会", "不要", "不是", "没有", "无法",
        "解释", "表达", "表示", "显示", "展示", "呈现",
        "之际", "之时", "之间", "之后", "之前", "的话", "一个", "什么",
        "怎么", "这个", "那个", "他们", "她们", "自己", "已经", "而是",
        "但是", "可是", "然而", "不过", "虽然", "尽管", "因为", "所以",
        "如果", "那么", "而且", "并且", "或者", "还是", "要么", "既然",
        "于是", "突然", "忽然", "然后", "接着", "随后", "最后", "终于",
        "开始", "结束", "发现", "质问", "对峙", "封锁", "法令",
    }

    for name, count in counter.most_common(60):
        if count < 3:                      # 滑动窗口噪声大 → 阈值比原来(2)更严
            continue
        if name in known or name in STOP_WORDS or name in common_words:
            continue
        if _overlaps_known(name):
            continue
        warnings.append({
            "type": "new_character_suspect",
            "entry_name": name,
            "detail": (f"文本中出现 {count} 次，但 vault 中无匹配角色名"
                       f"（启发式候选，可能是跨词边界噪声，需人工确认）"),
        })

    caveat = ("空结果**不代表**写作产物与正典一致：conflicts 尚未实现；"
              "locked_violations 只覆盖结构性违逆（缺失 / 丢锁定 / 改名），"
              "不覆盖语义冲突；warnings 仅为启发式候选。")
    if not locked_report.get("checked"):
        caveat += (" ⚠️ 本次 locked 检查**未生效**：" +
                   str(locked_report.get("reason") or "原因未知") +
                   " —— 此时的空结果更不能当成「通过」。")
    return {
        "conflicts": conflicts,
        "warnings": warnings,
        "locked_violations": locked_violations,
        "locked_report": locked_report,
        "implemented": ["new_character_suspect（疑似新角色，启发式）",
                        "locked_violations（locked 条目 缺失/丢锁定/改名，确定性）"],
        "unimplemented": ["conflicts（正典事实冲突 —— 需语义判断，机器判不准）"],
        "caveat": caveat,
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


def write_sandbox(filename, content, subdir="", kind="", source=""):
    """写入沙盒（只读原稿，只写沙盒），并登记为**待审**产物。

    Args:
        filename: 文件名
        content: 文件内容
        subdir: 子目录（如 "角色"、"世界观"）
        kind: 产物类型（如 "outline_proposal"），供审核队列分类
        source: 产物来源（如 "outline_refine v3"），供审核时判断上下文

    Returns:
        (ok, message)

    ⚠️ 2026-09-21：写入后自动在 `data/state/sandbox_manifest.json` 登记为
    `pending`（待审）。此前沙盒只是「往目录丢文件」，用户无法区分新稿与已确认稿，
    也没有「驳回」这个动作。
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

    # 登记审核状态（登记失败不影响写入 —— 但要说明，否则成审核盲区）
    try:
        from utils import sandbox_review as srv
        rel = str(out.relative_to(sandbox)).replace("\\", "/")
        entry, changed = srv.register(rel, sandbox, kind=kind, source=source)
        tag = "待审" if changed else "内容未变，保留原审核状态"
        print(f"[sandbox] 已登记审核状态: {rel}（{tag}）")
    except Exception as e:                         # noqa: BLE001
        print(f"[sandbox] ⚠ 审核状态登记失败（该产物会在审核队列里缺失）: {e}")
    return True, f"已写入沙盒: {out}"


def push_to_sandbox(source_path, subdir="", kind="", source=""):
    """推送产物到沙盒（只读原稿，只写沙盒），并登记为**待审**。

    Args:
        source_path: 源文件路径
        subdir: 子目录
        kind / source: 供审核队列分类（见 `write_sandbox`）

    Returns:
        (ok, message)
    """
    src = Path(source_path)
    if not src.exists():
        return False, f"源文件不存在: {src}"

    ok, err = _validate_sandbox_path(src.name, subdir)
    if not ok:
        return False, err

    sandbox = _get_sandbox_dir()
    if subdir:
        sandbox = sandbox / subdir
    sandbox.mkdir(parents=True, exist_ok=True)

    out = sandbox / src.name
    content = read_text(src)
    write_text(out, content)

    try:
        from utils import sandbox_review as srv
        rel = str(out.relative_to(sandbox)).replace("\\", "/")
        entry, changed = srv.register(rel, sandbox, kind=kind, source=source)
        print(f"[sandbox] 已登记审核状态: {rel}（{'待审' if changed else '内容未变，保留原状态'}）")
    except Exception as e:                         # noqa: BLE001
        print(f"[sandbox] ⚠ 审核状态登记失败（该产物会在审核队列里缺失）: {e}")
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

    p_locked = sub.add_parser(
        "locked", help="检查 locked 条目是否被违逆（确定性，零 token）")
    p_locked.add_argument("--setting", default=None,
                          help="设定集路径（默认 data/setting/setting.json）")

    p_diff = sub.add_parser(
        "diff", help="vault 与设定集内容级对账（缺失/新增/改名/丢 locked，零 token）")
    p_diff.add_argument("--setting", default=None,
                        help="设定集路径（默认 data/setting/setting.json）")
    p_diff.add_argument("--json", action="store_true", help="输出 JSON")

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
        # ⚠️ 输出必须让「什么没查」比「查了什么」更醒目 ——
        # 否则「0 个」会被读成「通过」，而实际是未实现。
        print("已实现的检查：")
        for c in result.get("implemented", []):
            print(f"  ✓ {c}")
        print("未实现的检查（空结果 ≠ 无问题）：")
        for u in result.get("unimplemented", []):
            print(f"  ✗ {u}")
        print("")
        print(f"疑似新角色候选：{len(result['warnings'])} 个（启发式，需人工确认）")
        for w in result["warnings"][:20]:
            print(f"  [{w['type']}] {w['entry_name']}: {w['detail']}")
        print(f"正典事实冲突：{len(result['conflicts'])} 个（**该检查未实现**）")
        lr = result.get("locked_report") or {}
        if lr.get("checked"):
            print(f"locked 违例：{len(result['locked_violations'])} 个"
                  f"（vault 内 locked 条目 {lr.get('locked_total', 0)} 个，"
                  f"匹配到 {lr.get('matched', 0)} 个）")
            for v in result["locked_violations"]:
                print(f"  [{v['type']}] {v['detail']}")
        else:
            print(f"locked 违例：**本次未生效** —— {lr.get('reason', '原因未知')}")
        print("")
        print(f"注意：{result.get('caveat', '')}")

    elif args.cmd == "locked":
        try:
            rep = check_locked_violations(setting_path=args.setting)
        except Exception as e:                                # noqa: BLE001
            print(f"无法执行 locked 检查：{e}")
            print("（vault 未配置时不启用联动；请在 config/system.yaml 的 "
                  "obsidian.vault_path 填你的知识库目录）")
            raise SystemExit(1)
        print("===== locked 条目检查（确定性）=====")
        print(f"vault 内 locked 条目：{rep['locked_total']} 个")
        print(f"设定集：{rep['setting_path']}")
        if not rep["checked"]:
            print(f"⚠️ 本次检查**未生效**：{rep['reason']}")
            raise SystemExit(1)
        print(f"匹配到：{rep['matched']} 个 / 违例：{len(rep['violations'])} 个")
        for v in rep["violations"]:
            print(f"  [{v['type']}] {v['detail']}")
        if not rep["violations"]:
            print("  （无结构性违例）")
            print("  注意：语义级冲突（正文是否与 locked 设定矛盾）**不在本检查范围**，"
                  "机器判不准。")
        raise SystemExit(1 if rep["violations"] else 0)

    elif args.cmd == "diff":
        try:
            rep = sync_diff(setting_path=args.setting)
        except Exception as e:                                # noqa: BLE001
            print(f"无法执行对账：{e}")
            print("（vault 未配置时不启用联动；请在 config/system.yaml 的 "
                  "obsidian.vault_path 填你的知识库目录）")
            raise SystemExit(1)
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
            raise SystemExit(0 if rep["checked"] else 1)
        print("===== vault ↔ 设定集 内容级对账（确定性，零 token）=====")
        if not rep["checked"]:
            print(f"⚠️ 本次对账**未生效**：{rep['reason']}")
            raise SystemExit(1)
        print(f"vault 角色 {rep['vault_total']} 个 · 设定集条目 {rep['setting_total']} 个"
              f" · 设定集：{rep['setting_path']}")
        print(f"缺失（vault 有、设定集没有）：{len(rep['missing'])} 个")
        for m in rep["missing"][:30]:
            print(f"  - {m['name']}  {m['path']}")
        print(f"新增（设定集有、vault 没有，需人工确认是否回写）：{len(rep['added'])} 个")
        for a in rep["added"][:30]:
            print(f"  + {a['name']}  [{a['where']}]")
        print(f"改名：{len(rep['renamed'])} 个")
        for r in rep["renamed"]:
            print(f"  [{r['type']}] {r['detail']}")
        print(f"丢 locked：{len(rep['lock_lost'])} 个")
        for l in rep["lock_lost"]:
            print(f"  [{l['type']}] {l['detail']}")
        if not (rep["missing"] or rep["added"] or rep["renamed"] or rep["lock_lost"]):
            print("  （无差异）")
            print("  注意：语义级冲突（正文是否与 locked 设定矛盾）**不在本检查范围**。")
        raise SystemExit(1 if (rep["missing"] or rep["renamed"] or rep["lock_lost"]) else 0)

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
