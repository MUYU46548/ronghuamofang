# -*- coding: utf-8 -*-
"""知识库索引与检索（倒排索引 + 关键词精确匹配）。

设计取舍：
- 对结构化词条（每篇独立 markdown + frontmatter），精确关键词匹配 >> 语义向量
- 零依赖纯标准库（无 torch/sentence-transformers）
- 索引结构：{term -> set(entry_paths)} + 词条元数据缓存
- 检索流程：提取查询关键词 -> 倒排索引求交 -> 按匹配词数排序 -> top-K
"""


import math
import os
import pickle
import re
from pathlib import Path
from datetime import datetime

# 跳过目录与 obsidian_bridge **同一配置源**（A1 修复：本处原是第二份写死清单，
# 且与 bridge 漂移——本处少跳过了一个目录，模板目录会被索引进倒排库、污染检索）。
from utils.setting_schema import get_skip_dirs, is_uninjectable

# 需要忽略的停用词（中文常见虚词 + 超长无意义词）
STOP_WORDS = set(
    "的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 到 说 要 去 你 会 着 没有 看 好 自己 这 他 她 它 们 那 些 什么 怎么 吗 吧 呢 啊 嗯 哈 呀 嘛 被 把 让 给 从 对 与 等 最 更 太 非常 已经 可以 可能 应该 必须 需要 进行 通过 使用 作为 属于 由于 因为 所以 但是 如果 则 而 且 或 但 却 并 且 以及 中 后 前 内 外 下 时 地 得 里 中 之间 方面 部分 类型 方法 系统 功能 信息 内容 结构 方式 过程 结果 作用 目的 意义 影响 问题 情况 工作 学习 研究 发展 应用 技术 设计 实现 支持 提供 包含 具有 采用 基于 结合 利用 建立 创建 完成 实现 形成 产生 发生 存在 包括 涉及 相关 主要 重要 基本 一定 一定 通常 一般 往往 容易 难以 不同 相同 相似 类似 对应 对应 相应"
    .split()
)

# 词长过滤
MIN_TERM_LEN = 2
MAX_TERM_LEN = 12

# frontmatter 标签键（用于加权）
TAG_KEYS = ["tags", "tag", "aliases", "alias", "category", "categories"]
NAME_KEYS = ["name", "title", "id"]


def normalize_term(term):
    """归一化词：小写、去首尾标点、去纯数字。"""
    t = term.strip().lower().rstrip("的了是在我")
    if len(t) < MIN_TERM_LEN or len(t) > MAX_TERM_LEN:
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
            if term not in STOP_WORDS and MIN_TERM_LEN <= len(term) <= MAX_TERM_LEN:
                terms.add(term)
    return terms


# 通用小节标题兜底词（2026-10-24）：**只在结构判据失效时用**。
#
# 主判据是下面的 `_h1_is_topic` 里的**结构信号**（看这一页有几个 H1）—— 零词汇、
# 对任何库都成立。词表只是第二道保险，所以这里只留跨库通用的中文/英文小节名。
# ⚠️ **不许再往这个集合里塞"在某个真实库里见过的小节标题"**（上一版就塞了
# “哲学内核”“选址与结构”，那是把一个库的写作习惯当通例 —— 本文件的
# 通用性判据是"换个库照样成立"，不是"在我这个库上跑通了"）。
_GENERIC_H1 = {
    "概述", "简述", "简介", "背景", "基本信息", "定义", "设定",
    "历史", "经过", "事件", "参数", "数据", "规格", "目录", "参考",
    "注释", "说明", "分析", "影响", "结局", "评价", "其他", "附录",
    "总览", "结构", "特征", "特点", "能力", "关系",
    "overview", "introduction", "background", "details", "parameters",
    "history", "notes", "references", "appendix",
}


def _strip_ordinals(name):
    """去掉“一、”“1.”“22 ”这类序号前缀，用于词表比对。"""
    s = re.sub(r'^[一二三四五六七八九十百]+[、.．]\s*', '', str(name or '').strip())
    s = re.sub(r'^\d+[.、．)）]?\s*', '', s).strip().strip('*_`')
    return s


def _h1_is_topic(h1s, candidate):
    """候选 H1 是否像**主题名**（而不是笔记内部的小节标题）。

    两条判据，任一成立即否决 —— 宁可用文件名，也别给模型一个无辨识度的名字：

    ① **结构判据（主，零词汇）**：这一页有 ≥2 个 H1 → H1 是**分节结构**，
       第一个只是某一小节，绝不是主题名。这个事实与库怎么组织无关。
    ② **词表判据（兜底）**：去序号后命中通用小节名（概述/基本信息…）。
       只在单 H1 时才用到。

    实测依据：某真实库 417 篇设定的 frontmatter 普遍没有 name/title 键、
    H1 全是“一、概念定义”“基本信息”，照单取 H1 会让注入的词条名全是同一个串，
    模型无法区分两条不同的词条。
    """
    if len(h1s) >= 2:
        return False
    s = _strip_ordinals(candidate)
    return bool(s) and s.lower() not in _GENERIC_H1


def _norm_roots(vault_path):
    """把 str / list 统一成 [(Path, skip_dirs|None)]。"""
    if isinstance(vault_path, (list, tuple)):
        out = []
        for x in vault_path:
            if isinstance(x, dict):
                p, sk = x.get("path"), x.get("skip_dirs")
            else:
                p, sk = x, None
            if p:
                out.append((Path(p), sk))
        return out
    return [(Path(vault_path), None)] if vault_path else []


def _skip_for(skip_dirs):
    """某根本次生效的排除集合。None = 全局（默认 + 当前库本地追加）。"""
    if skip_dirs is None:
        return get_skip_dirs()
    from utils.setting_schema import _DEFAULT_SKIP_DIRS
    return set(_DEFAULT_SKIP_DIRS) | {str(s).strip() for s in skip_dirs if str(s).strip()}


def build_index(vault_path, out_path=None, progress=None, persist=True):
    """构建知识库倒排索引（支持一个或多个 vault 根）。

        Args:
            vault_path: vault 根目录，或 `[{"path","skip_dirs"}]` 列表（多库，2026-11-07）
        out_path: 索引输出路径（默认 data/state/kb_index.pkl）
        progress: 可选进度回调 (current, total, filename)
        persist: 是否落盘 pkl（默认 True）。stage4 逐章召回用**内存索引**，
            传 False —— 每章往 data/state 写一次 pkl 既无必要又引入陈旧问题。

    Returns:
        index dict
    """
    roots = _norm_roots(vault_path)
    if not roots:
        raise ValueError("没有可索引的 vault 根")

    # 每个根按**自己的**排除项收集（多库时不能让当前库的本地排除项串台）
    per_root = []
    for root, sk in roots:
        skip = _skip_for(sk)
        files = [f for f in root.rglob("*.md")
                 if not any(part in skip for part in f.relative_to(root).parts)]
        per_root.append([root, files])
    multi = len(per_root) > 1

    tasks, labels = [], set()
    for ri, (root, files) in enumerate(per_root):
        label = root.name or ("vault%d" % ri)
        while label in labels:              # 两个库同名：加序号区分，不许互相覆盖
            label = "%s#%d" % (root.name or "vault", ri)
        labels.add(label)
        tasks += [(f, root, label) for f in files]

    index = {
        "terms": {},           # term -> [entry_key, ...]
        "entries": {},         # entry_key -> {name, title, tags, snippet, term_count, root, rel}
        "vault_path": str(per_root[0][0].resolve()),
        "vaults": [{"path": str(r.resolve()), "name": r.name, "files": len(f)}
                   for r, f in per_root],
        "built_at": datetime.now().isoformat(),
        "total_files": len(tasks),
        "conflicts": [],
        "skipped_same_name": [],
    }
    seen_names = {}

    for i, (f, vault, _label) in enumerate(tasks):
        if progress:
            progress(i + 1, len(tasks), f.name)

        try:
            text = f.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        # 提取 frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---', text, re.S)
        fm_text = fm_match.group(1) if fm_match else ""
        body = text[fm_match.end():] if fm_match else text

        # 提取名称（优先 frontmatter，其次正文首个一级标题，最后文件名）
        # H1 这一档（2026-10-24）：Obsidian 笔记可以没有 YAML 头，此时**正文标题
        # 才是这条笔记在讲什么**。只取文件名会让注入给模型的名字变成“随便写的
        # 文件名”这种零信息量的串 —— 实测非标准布局库里这种情况是常态。
        name = ""
        for k in NAME_KEYS:
            m = re.search(rf'^{k}\s*:\s*(.+)', fm_text, re.M)
            if m:
                name = m.group(1).strip().strip('"').strip("'")
                break
        if not name:
            # 结构判据要这一页的**所有** H1（≥2 个 → H1 是分节结构，
            # 第一个只是某一小节，不是主题名）。单 H1 时才走词表兜底。
            h1s = re.findall(r'^#\s+(.+)', body, re.M)
            if h1s and _h1_is_topic(h1s, h1s[0]):
                name = h1s[0].strip().strip('*_`')
        if not name:
            name = f.stem

        # 提取 tags
        tags = []
        for k in TAG_KEYS:
            m = re.search(rf'^{k}\s*:\s*(.+)', fm_text, re.M)
            if m:
                raw = m.group(1).strip()
                # 解析 YAML list 或逗号分隔
                tags = [t.strip().strip('"').strip("'") for t in re.split(r'[,，\[\]]', raw) if t.strip()]
                break

        # 空骨架页不进索引（与 scan_vault 同一判据，单源在 setting_schema）
        if is_uninjectable(body):
            continue

        # 提取摘要（正文前 300 字）
        plain = re.sub(r'[#*`\[\]]', '', body).strip()
        snippet = plain[:300].replace('\n', ' ').strip()

        # 提取所有词
        all_text = f"{name} {' '.join(tags)} {body}"
        terms = extract_terms(all_text)

        rel_path = f.relative_to(vault).as_posix()
        # 多库：同名条目只留先出现的（当前库在最前 → 当前库优先）。被覆盖的记下来，
        # 不静默吞 —— “这条设定来自哪个库”必须永远可回答。
        _nm = normalize_term(name)
        if multi and _nm:
            if _nm in seen_names:
                index["skipped_same_name"].append(
                    {"name": name, "kept": seen_names[_nm], "dropped": _label})
                continue
            seen_names[_nm] = _label
        entry_key = ("%s/%s" % (_label, rel_path)) if multi else rel_path

        # 条目名本身作为精确词加入（优先匹配）
        name_term = normalize_term(name)
        if name_term:
            terms.add(name_term)

        # 写入索引
        for term in terms:
            if term not in index["terms"]:
                index["terms"][term] = []
            if entry_key not in index["terms"][term]:
                index["terms"][term].append(entry_key)

        index["entries"][entry_key] = {
            "name": name,
            "title": name,
            "tags": tags[:10],
            "snippet": snippet,
            "path": entry_key,
            "rel": rel_path,
            # 绝对根：多库时同一条 key 不再是“相对某个根”，重读正文要靠这一项
            "root": str(vault.resolve()),
            "vault": _label,
            "term_count": len(terms),
        }

    # 落盘（persist=False 时跳过：stage4 逐章召回只用内存索引）
    if persist:
        out = Path(out_path) if out_path else Path("data/state/kb_index.pkl")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'wb') as fp:
            pickle.dump(index, fp)

    return index


def load_index(index_path=None):
    """加载索引，不存在返回 None。"""
    p = Path(index_path) if index_path else Path("data/state/kb_index.pkl")
    if not p.exists():
        return None
    with open(p, 'rb') as fp:
        return pickle.load(fp)


def search(query, index=None, top_k=5, vault_path=None):
    """检索相关词条。

    Args:
        query: 查询文本
        index: 已加载的索引（None 自动加载）
        top_k: 返回数量
        vault_path: vault 路径（用于读取 snippet）

    Returns:
        [(entry_path, name, snippet, match_tags), ...]
    """
    if index is None:
        index = load_index()
    if not index:
        return []

    query_terms = extract_terms(query)
    if not query_terms:
        return []

    # 计分：IDF 加权（罕见词得分高）+ 条目名精确匹配加分
    scores = {}  # path -> score
    total_entries = len(index["entries"])
    for term in query_terms:
        entries = index["terms"].get(term, [])
        if not entries:
            continue
        # IDF：log(总文档数 / 包含该词的文档数)
        idf = math.log(total_entries / (len(entries) + 1)) + 1
        for path in entries:
            scores[path] = scores.get(path, 0) + idf
            # 条目名精确匹配额外加分
            entry = index["entries"].get(path, {})
            name_term = normalize_term(entry.get("name", ""))
            if name_term and term == name_term:
                scores[path] = scores.get(path, 0) + 3

    # 排序取 top
    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]

    results = []
    vault = Path(vault_path) if vault_path and not isinstance(vault_path, (list, tuple)) \
        else Path(index.get("vault_path", "") or ".")

    for path, score in ranked:
        entry = index["entries"].get(path, {})
        # 尝试重新读取 snippet（保持最新）。多库时每条自带绝对根与相对路径，
        # 不能拿主根去拼 "库名/相对路径"（那个目录根本不存在）。
        base = Path(entry["root"]) if entry.get("root") else vault
        full_path = base / entry.get("rel", path)
        snippet = entry.get("snippet", "")
        if full_path.exists():
            try:
                text = full_path.read_text(encoding='utf-8', errors='ignore')
                body_match = re.match(r'^---\s*\n.*?\n---\s*\n', text, re.S)
                body = text[body_match.end():] if body_match else text
                plain = re.sub(r'[#*`\[\]]', '', body).strip()
                snippet = plain[:300].replace('\n', ' ').strip()
            except Exception:
                pass

        results.append((path, entry.get("name", ""), snippet, score))

    return results


# ------------------------------------------------- stage4 通道（2026-10-24）

# 进程内索引缓存：同一 run 逐章写作时只建一次索引（实测千篇 vault 建索引需数秒，
# 逐章重建会把这部分开销乘以章数）。失效判据是 vault 指纹而不是 TTL —— mtime 变了
# 就重建，避免“改了库但索引还是旧的”这类静默陈旧。
_MEM_CACHE = {"key": None, "index": None}


def _vault_fingerprint(vault, skip_dirs=None):
    """vault 指纹：根路径 + 可索引文件数 + 最新 mtime。

    收集判据与 `build_index` 完全同源（都走 `get_skip_dirs`），指纹对不上就说明
    收集面变了（文件增删 / 改名 / 用户改了 skip_dirs 配置），必须重建索引。
    """
    vault = Path(vault)
    skip = _skip_for(skip_dirs)
    md = [f for f in vault.rglob("*.md")
          if not any(p in skip for p in f.relative_to(vault).parts)]
    newest = max((f.stat().st_mtime for f in md if f.exists()), default=0.0)
    return (str(vault.resolve()), len(md), round(newest, 3))


def recall(query, vault_path, top_k=5, max_chars=600):
    """全文倒排召回：为**任意布局**的 vault 提供检索（stage4 写作注入通道）。

    与 `obsidian_bridge.inject_context` 的分工（两路**并联**，不是替换）：

    - `inject_context` 只精确匹配 name/tags —— 标准布局下快、且带 locked 语义；
      但扫描准入靠目录名，布局对不上时**一条都扫不到**。
    - 本函数索引 name + **正文全文**（中文 2-4 gram + IDF）—— 不看目录名、
      不要求 frontmatter、用户写任何文件名都能被正文里的词命中。

    Args:
        query: 查询文本（stage4 传章大纲的“涉及角色”行或大纲开头）
        vault_path: vault 根（**必填**，由调用方从配置解析后传入 —— utils 层
            不反向依赖上层模块，避免重蹈路径常量固化那类坑）
        top_k: 命中条数
        max_chars: 注入正文的总字符预算（按分数从高到低累加，超出即停）

    Returns:
        [(path, name, snippet, score), ...]；无命中 / 索引失败 → **[]**
        （召回是增益，失败不许拖垮写作主链路 —— 异常在此吞掉并由调用方决定
        是否提示，与 stage4 注入块的三态纪律一致）
    """
    if not query or not str(query).strip() or not vault_path:
        return []
    roots = [(p, sk) for p, sk in _norm_roots(vault_path) if p.exists()]
    if not roots:
        return []

    try:
        # 多库指纹 = 每根的指纹按顺序拼起来：任一库文件变了都重建，
        # 也不会因为“第一个库没变”而漏掉第二个库的新增。
        key = tuple(_vault_fingerprint(p, sk) for p, sk in roots)
        if _MEM_CACHE["key"] != key:
            _MEM_CACHE["index"] = build_index(
                [{"path": str(p), "skip_dirs": sk} for p, sk in roots], persist=False)
            _MEM_CACHE["key"] = key
        idx = _MEM_CACHE["index"]
        hits = search(query, index=idx, top_k=top_k, vault_path=str(roots[0][0]))
    except Exception:                                          # noqa: BLE001
        return []

    out, used = [], 0
    for path, name, snippet, score in hits:
        if used >= max_chars:
            break
        s = (snippet or "").strip()
        if not s:
            continue
        limit = max_chars - used
        if len(s) > limit:
            # 省略号本身占一个字符，必须算进预算（否则实测总长会超 1 字）
            s = (s[:limit - 1].rstrip() if limit > 1 else "") + "…"
        out.append((path, name, s, score))
        used += len(s)
    return out


def get_snippet(entry_path, vault_path=None, max_chars=500):
    """读取指定词条的摘要。vault_path 为空时不做路径拼接（返回空串）。"""
    vault = Path(vault_path) if vault_path else None
    if vault is None:
        return ""
    full = vault / entry_path
    if not full.exists():
        return ""
    try:
        text = full.read_text(encoding='utf-8', errors='ignore')
        body_match = re.match(r'^---\s*\n.*?\n---\s*\n', text, re.S)
        body = text[body_match.end():] if body_match else text
        plain = re.sub(r'[#*`\[\]]', '', body).strip()
        return plain[:max_chars].replace('\n', ' ').strip()
    except Exception:
        return ""


def search_to_context(query, index=None, top_k=5, max_chars=500, vault_path=None):
    """检索并格式化为可注入模板的上下文字符串。"""
    results = search(query, index=index, top_k=top_k, vault_path=vault_path)
    if not results:
        return ""

    parts = []
    for path, name, snippet, score in results:
        tag_str = f"（相关度：{score}）"
        parts.append(f"### {name}{tag_str}\n{snippet}\n")

    return "\n".join(parts)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="知识库索引工具")
    parser.add_argument("--vault", default=os.environ.get("NF_VAULT_PATH") or "",
                        help="vault 路径（留空则读取 config/system.yaml 的 obsidian.vault_path）")
    parser.add_argument("--out", default="data/state/kb_index.pkl", help="索引输出路径")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("build", help="构建索引")
    p_search = sub.add_parser("search", help="检索词条")
    p_search.add_argument("query", help="查询文本")
    p_search.add_argument("--top", type=int, default=5, help="返回数量")
    p_search.add_argument("--chars", type=int, default=300, help="每词条摘要长度")

    args = parser.parse_args()

    if args.cmd == "build":
        def prog(cur, total, name):
            if cur % 50 == 0 or cur == total:
                print(f"  [{cur}/{total}] {name}")
        idx = build_index(args.vault, args.out, progress=prog)
        print(f"完成：{idx['total_files']} 个词条，{len(idx['terms'])} 个词项")

    elif args.cmd == "search":
        results = search(args.query, top_k=args.top, vault_path=args.vault)
        for path, name, snippet, score in results:
            print(f"[{score}] {name} ({path})")
            print(f"  {snippet[:100]}...")
            print()
    else:
        parser.print_help()
