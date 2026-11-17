# -*- coding: utf-8 -*-
"""成书提炼产物的只读检索（2026-11-17，成书提炼 P0）。

## 为什么要有它

成书提炼把一本写完的书拆成逐章摘要 + 角色/设定/时间线，但产物只能靠人翻目录：
“前作里这个人在第几章做过什么”仍然要一章章点开。这一层把**已有摘要**做成查询：

- 按关键词（人名 / 地点 / 事件 / 专有名词）找章；
- 按角色名筛（摘要里的“人物”字段）；
- 明说哪些章**还没摘要**（不许把“没查过”当成“没有”）。

零 token、只读：不建索引文件、不写任何东西 —— 每次查询都按盘上现状算，
这样重跑成书提炼（补章）后结果立刻是新的，不会出现“索引过期”。

## 数据来源（写死，不猜）

    data/book_extract/<name>/manifest.json   chapters[{seq,title,summary,...}]
    data/book_extract/<name>/summaries/NNNN.json
        {章节, 人物[], 地点[], 事件[], 时间线, 伏笔动作[], 专有名词[]}

用法（Python）：
    from utils.book_extract.search import search_camp
    res = search_camp("old-book", query="沈砚", character="", limit=10)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 检索时拼接进“可命中文本”的字段（顺序即结果展示顺序）。
SEARCH_FIELDS = ("章节", "人物", "地点", "事件", "时间线", "专有名词")


def _as_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "；".join(str(x) for x in v if str(x).strip())
    if isinstance(v, dict):
        return "；".join("%s%s" % (k, v[k]) for k in v)
    return str(v)


def _snippet(text: str, q: str, width: int = 48) -> str:
    """命中词前后各截一段（作者要看到上下文，不是只看到一个“命中”）。"""
    i = text.lower().find(q.lower())
    if i < 0:
        return ""
    a = max(0, i - width // 2)
    b = min(len(text), i + len(q) + width // 2)
    return ("…" if a else "") + text[a:b] + ("…" if b < len(text) else "")


def load_camp(camp: str | Path, root: str | Path = ".") -> Dict[str, Any]:
    """读一册提炼产的 manifest + 逐章摘要。返回 {name, dir, chapters[], missing[]}。

    不 import runner：那是真跑管线的模块，检索只要它写下来的文件。
    """
    p = Path(camp)
    if not (p / "manifest.json").exists():
        p = Path(root) / "data" / "book_extract" / str(camp)
    man = p / "manifest.json"
    if not man.exists():
        raise FileNotFoundError("找不到这册提炼：%s（先跑 book_extract.py init/run）" % p)
    data = json.loads(man.read_text(encoding="utf-8"))
    chapters, missing = [], []
    for ch in data.get("chapters") or []:
        seq = int(ch.get("seq") or 0)
        rel = str(ch.get("summary") or "")
        sp = p / rel if rel else None
        row = {"seq": seq, "title": str(ch.get("title") or ""), "summary_path": str(sp or "")}
        if sp and sp.exists():
            try:
                row["summary"] = json.loads(sp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                row["summary"] = {}
                missing.append({"seq": seq, "title": row["title"],
                                "why": "摘要文件读不出来（坏文件）"})
        else:
            row["summary"] = {}
            missing.append({"seq": seq, "title": row["title"],
                            "why": "这一章还没摘要"})
        chapters.append(row)
    return {"name": p.name, "dir": str(p), "chapters": chapters, "missing": missing}


def search_camp(camp: str | Path, query: str = "", character: str = "",
                chapter: Optional[int] = None, limit: int = 20,
                root: str | Path = ".") -> Dict[str, Any]:
    """在已有摘要里找章。零 token、只读。

    返回：
      {camp, query, character, results:[{seq,title,fields:{name:snippet}, summary_path}],
       scanned: 有摘要的章数, missing:[{seq,title,why}], note}
    `query` 与 `character` 都空 = 只列缺口清单（不做全文扫描）。
    """
    data = load_camp(camp, root)
    q = str(query or "").strip()
    who = str(character or "").strip()
    results: List[Dict[str, Any]] = []
    scanned = 0
    for row in data["chapters"]:
        if chapter is not None and int(row["seq"]) != int(chapter):
            continue
        s = row.get("summary") or {}
        if not s:
            continue
        scanned += 1
        people = [str(x) for x in (s.get("人物") or [])]
        if who and not any(who in p or p in who for p in people if p):
            continue
        hits: Dict[str, str] = {}
        for field in SEARCH_FIELDS:
            text = _as_text(s.get(field))
            if not q:
                continue
            if q.lower() in text.lower():
                hits[field] = _snippet(text, q)
        if q and not hits:
            continue
        if not q and not who and chapter is None:
            continue
        results.append({"seq": row["seq"], "title": row["title"],
                        "fields": hits, "people": people,
                        "summary_path": row["summary_path"]})
    results.sort(key=lambda r: r["seq"])
    note = ""
    if not q and not who:
        note = "只看缺口清单（没给查询词或角色名，不扫全文）"
    elif not scanned:
        note = "这册还没有任何章节摘要 —— 先跑成书提炼（或补跑失败的章）"
    return {"camp": data["name"], "query": q, "character": who,
            "results": results[: max(1, int(limit))],
            "matched": len(results), "scanned": scanned,
            "missing": data["missing"], "note": note}
