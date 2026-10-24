# -*- coding: utf-8 -*-
"""大纲引用解析（2026-10-24）：把大纲里写死的素材文件名，变成写作时真能读到的正文。

## 为什么必须有这块

OB34 素材实测（桌面 OB34素材/）：大纲里直接点名要读某张碎片卡，例如

    云芥向观澜申请超算算力（碎片2026-4-8），推算裁定周期
    次日，观澜接收到荣安堂的报告邮件（碎片2026-5-15）

这类引用是**用户显式的取材指令**，不是修辞。此前流水线对它一无所知：写作模型
看到的只有「本章大纲 + 设定条目 + 滚动摘要 + bigram 召回」，那张被点名的碎片
**不会因为被点名而进上下文**，能不能被召回纯看字面重合度运气。

用户的判据（2026-10-24）：「连我这个最基础的场景都跑不好，发不出去根本没办法
应付用户丢来的各种奇怪素材（就像解谜游戏）」。所以引用必须**机械兑现**，不许赌。

## 设计

- `extract_citations(text)`：纯函数，零 IO。扫出 `（碎片2026-4-8）`/`(碎片2026-4-8)`
  这类括号引用，剥掉前后缀只留检索键（如 `碎片2026-4-8`）。全角半角括号都认。
- `resolve_citations(text, roots, ...)`：拿检索键去素材目录里找文件，返回命中详情
  （键 → 命中路径 + 字数 + 是否超长截断）。找不到的键**如实报缺**，绝不假装命中 ——
  宁可让写作模型知道「这张卡没找到」，也不能让它拿着不存在的卡当依据。
- 命中判据：文件**词干包含检索键**（如 `小说碎片2026-4-8.md` 命中 `碎片2026-4-8`）。
  多个候选时取词干最短的那个（`小说碎片X` 比 `守灯会设定碎片X` 更贴键），
  并列时按路径排序取首个 —— 判据必须确定性，否则同一本书两次跑出不同上下文。

⚠️ `（参见相关碎片）` 这类**没有具体文件名**的引用不会被抽出（键里必须含数字），
   否则会把"相关碎片"当成真文件名去找，永远找不到还徒增噪音。

用法（stage4 在分隔线之后注入，见 build_citation_block）：
    from utils.citations import resolve_citations, build_citation_block
    cites = resolve_citations(outline_text, roots=["materials/original_scraps", "materials/raw"])
    block = build_citation_block(cites)
"""
import re
from pathlib import Path

# 括号内以「碎片」开头、且含数字的引用。全角（）与半角()都认。
_CITE_RE = re.compile(r"[（(]\s*((?:小说)?碎片[\d\-—－_]{4,})\s*[)）]")

# 单张卡注入正文的上限：素材卡动辄数千字，全量注入会挤占写作预算。
# 超过就截断并**显式标注截断**，不静默（同 memory_policy 的"裁了什么要写出来"纪律）。
PER_CITE_CHARS = 4000
# 一章注入总上限：一张章大纲可能点名多张卡，总量失控同样伤正文质量。
PER_CHAPTER_CHARS = 12000
# 候选文件大小上限：超过直接跳过并报错，避免把几百 KB 的整书读进任务。
MAX_SOURCE_FILE_CHARS = 200000


def extract_citations(text):
    """从文本里抽出全部素材引用键，去重保序。纯函数，不碰文件系统。

    返回 ["碎片2026-4-8", "碎片2026-5-15", ...]。含数字的键才算数（见模块头）。
    """
    if not text:
        return []
    seen, out = set(), []
    for m in _CITE_RE.finditer(text):
        key = m.group(1).strip()
        if not re.search(r"\d", key):       # 「相关碎片」这类无日期的不算引用
            continue
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _stem_files(roots):
    """收集候选素材文件：{词干: Path}。跳过 _ 开头的目录与文件（备份区约定）。"""
    stems = {}
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in (".md", ".txt"):
                continue
            if any(part.startswith("_") or part.startswith(".") for part in f.parts):
                continue                  # _backup/ 之类的目录整棵跳过
            stems.setdefault(f.stem, f)
    return stems


def _best_match(key, stems):
    """键 → 最贴合的候选文件。词干包含键者胜；并列取词干最短，再并列按路径排序。"""
    hits = [s for s in stems if key in s]
    if not hits:
        return None
    return stems[sorted(hits, key=lambda s: (len(s), s))[0]]


def resolve_citations(text, roots):
    """把文本里的引用键逐个落到真实文件上。返回 dict 列表（确定性排序）。

    每项：{key, found, path, chars, truncated}
    - found=False：没找到对应文件。**如实报缺**，让上层把它写进任务 ——
      写作模型知道"这张卡缺失"好过以为设定里本来就没有。
    - truncated=True：正文超 PER_CITE_CHARS 被截断（截断处在块里显式标注）。
    """
    keys = extract_citations(text)
    if not keys:
        return []
    stems = _stem_files(roots)
    out = []
    for key in keys:
        hit = _best_match(key, stems)
        if hit is None:
            out.append({"key": key, "found": False, "path": "", "chars": 0,
                        "truncated": False})
            continue
        try:
            raw = hit.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            out.append({"key": key, "found": False, "path": str(hit), "chars": 0,
                        "truncated": False})
            print(f"[citations] 读取失败（本条按缺失处理）: {hit}: {e}")
            continue
        if len(raw) > MAX_SOURCE_FILE_CHARS:
            raw = raw[:MAX_SOURCE_FILE_CHARS]
        out.append({"key": key, "found": True, "path": str(hit),
                    "chars": len(raw), "truncated": False, "_text": raw})
    # 总量封顶：按出现顺序累加，超出就不再收录正文（条目保留、正文置空 + 标注）
    used = 0
    for it in out:
        if not it.get("found"):
            continue
        body = it.pop("_text", "")
        it["chars"] = len(body)
        # 单卡上限优先：超了就在块里给出"去读原文件"的指引，不整卡丢弃
        if len(body) > PER_CITE_CHARS:
            it["truncated"] = True
            it["_text"] = body[:PER_CITE_CHARS]
            used += PER_CITE_CHARS
            continue
        # 总量闸：按出现顺序累加，超了就只留"去读原文件"的指引（正文置空）
        if used + len(body) > PER_CHAPTER_CHARS:
            it["truncated"] = True
            it["_text"] = ""
            continue
        it["_text"] = body
        used += len(body)
    return out


def build_citation_block(cites):
    """把解析结果拼成注入块。空引用 → 空串（模板里渲染成空行，不占位）。"""
    if not cites:
        return ""
    lines = ["## 大纲点名的素材原文（**取材指令，非参考**）",
             "以下文件是本章大纲明确要求使用的素材。写作时必须采信其内容：",
             "专名、数值、因果关系一律以原文为准，不得凭想象改写。", ""]
    missing = []
    for it in cites:
        if not it.get("found"):
            missing.append(it["key"])
            continue
        body = (it.get("_text") or "").strip()
        if not body:
            lines.append(f"### 《{Path(it['path']).name}》")
            lines.append(f"（超出本章注入上限 {PER_CHAPTER_CHARS} 字，正文已让位给"
                         f"其他素材 —— 需要时请用 read_file 读取：{it['path']}）")
            lines.append("")
            continue
        lines.append(f"### 《{Path(it['path']).name}》")
        if it.get("truncated"):
            lines.append(f"（原文 {it['chars']} 字，超单卡上限 {PER_CITE_CHARS} 字，"
                         f"以下为前段 —— 完整原文见 {it['path']}）")
        lines.append(body)
        lines.append("")
    if missing:
        lines.append("### ⚠ 大纲点名但未找到的素材")
        lines.append("大纲引用了下列素材，但项目素材目录里找不到对应文件。"
                     "写作时**不得编造其内容**；如确需该设定，用中性表述带过，"
                     "并在文末 summary 注释里注明「缺素材：" + "、".join(missing) + "」。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
