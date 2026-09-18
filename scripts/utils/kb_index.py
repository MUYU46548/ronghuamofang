# -*- coding: utf-8 -*-
"""知识库索引与检索（倒排索引 + 关键词精确匹配）。

设计取舍：
- 对结构化词条（每篇独立 markdown + frontmatter），精确关键词匹配 >> 语义向量
- 零依赖纯标准库（无 torch/sentence-transformers）
- 索引结构：{term -> set(entry_paths)} + 词条元数据缓存
- 检索流程：提取查询关键词 -> 倒排索引求交 -> 按匹配词数排序 -> top-K
"""


import json
import math
import pickle
import re
from pathlib import Path
from datetime import datetime

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


def build_index(vault_path, out_path=None, progress=None):
    """构建知识库倒排索引。

        Args:
            vault_path: vault 根目录
        out_path: 索引输出路径（默认 data/state/kb_index.pkl）
        progress: 可选进度回调 (current, total, filename)

    Returns:
        index dict
    """
    vault = Path(vault_path)
    md_files = list(vault.rglob("*.md"))

    # 跳过 Obsidian 配置和插件目录
    skip_dirs = {'.obsidian', '.git', '.hermes', '.agent_context', '.sitian'}
    md_files = [
        f for f in md_files
        if not any(part in skip_dirs for part in f.relative_to(vault).parts)
    ]

    index = {
        "terms": {},           # term -> [entry_path, ...]
        "entries": {},         # entry_path -> {name, title, tags, snippet, term_count}
        "vault_path": str(vault.resolve()),
        "built_at": datetime.now().isoformat(),
        "total_files": len(md_files),
    }

    for i, f in enumerate(md_files):
        if progress:
            progress(i + 1, len(md_files), f.name)

        try:
            text = f.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue

        # 提取 frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---', text, re.S)
        fm_text = fm_match.group(1) if fm_match else ""
        body = text[fm_match.end():] if fm_match else text

        # 提取名称（优先 frontmatter，否则文件名）
        name = ""
        for k in NAME_KEYS:
            m = re.search(rf'^{k}\s*:\s*(.+)', fm_text, re.M)
            if m:
                name = m.group(1).strip().strip('"').strip("'")
                break
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

        # 提取摘要（正文前 300 字）
        plain = re.sub(r'[#*`\[\]]', '', body).strip()
        snippet = plain[:300].replace('\n', ' ').strip()

        # 提取所有词
        all_text = f"{name} {' '.join(tags)} {body}"
        terms = extract_terms(all_text)

        rel_path = str(f.relative_to(vault))

        # 条目名本身作为精确词加入（优先匹配）
        name_term = normalize_term(name)
        if name_term:
            terms.add(name_term)

        # 写入索引
        for term in terms:
            if term not in index["terms"]:
                index["terms"][term] = []
            if rel_path not in index["terms"][term]:
                index["terms"][term].append(rel_path)

        index["entries"][rel_path] = {
            "name": name,
            "title": name,
            "tags": tags[:10],
            "snippet": snippet,
            "path": rel_path,
            "term_count": len(terms),
        }

    # 保存
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
    vault = Path(vault_path) if vault_path else Path(index.get("vault_path", "E:/图书馆/ROSA"))

    for path, score in ranked:
        # 跳过索引页/报告页（导航性质，非正典内容）
        if "\\01 索引\\" in path or "索引\\" in path or "报告" in path or "消歧义" in path:
            continue
        entry = index["entries"].get(path, {})
        # 尝试重新读取 snippet（保持最新）
        full_path = vault / path
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


def get_snippet(entry_path, vault_path=None, max_chars=500):
    """读取指定词条的摘要。"""
    vault = Path(vault_path) if vault_path else Path("E:/图书馆/ROSA")  # fallback default
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
    parser.add_argument("--vault", default="E:/图书馆/ROSA", help="vault 路径")
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
