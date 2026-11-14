# -*- coding: utf-8 -*-
"""旧段落池：把归档书档里的合格段落编成可检索的索引（P1=A，2026-11-14 作者拍板）。

## 拍板口径（别自己加戏）

作者原话：**“只在我要用时才召回”**。所以这个池子：

- **不自动进任何写作上下文** —— 写作提示词里一个字都不引用它；
- 只有作者在界面上输入关键词 / 选人物时才查，查到的是候选，复制走由作者决定；
- 不做“自动推荐相似段落”，那等于替作者决定用什么。

## 数据从哪来

`data/books/**/chapters/` —— **归档的书档**（当前在写的书不算废稿）。
每章取一层：`refined > checked > raw`（复用 `cliche_scan.chapter_path` 的同一套优先级，
否则同一章会出三份重复段落）。

## 顺手一条

索引里记 `id = 书名#章号#段号`，重建后不变 —— 作者引用过哪一段，回头还能找回来。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

#: 少于 30 个汉字的段不入选（过场短句复用的价值低，且会把结果淹掉）
MIN_CN = 30
#: 超过 4000 字的段通常是“整章没分段”，不是段落，检索时也没法用
MAX_CN = 4000
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
INDEX_REL = ("data", "paragraph_pool", "index.json")
LAYER_DIRS = ("refined", "checked", "raw")


def index_path(root) -> Path:
    return Path(root).joinpath(*INDEX_REL)


def _cn_count(text: str) -> int:
    from utils.word_count import count_cn_words
    return count_cn_words(text)


def _split(text: str):
    """段落切分：与段落精修同一口径（annotations 已把它转发到唯一实现）。"""
    from utils.annotations import split_paragraphs
    return split_paragraphs(text)


def _load_json(p: Path, default):
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return default
    return default


def _write_json(p: Path, data) -> None:
    """原子写：先 .tmp 再 replace（崩在写一半 = 半个索引，比没有更糟）。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _name_set(block) -> list:
    """从 setting.json 的某一块里取名字（兼容 list / dict-of-lists）。"""
    items = block if isinstance(block, list) else (
        [x for xs in (block or {}).values() if isinstance(xs, list) for x in xs]
        if isinstance(block, dict) else [])
    out = []
    for it in items:
        if isinstance(it, dict):
            n = str(it.get("name") or "").strip()
            if n:
                out.append(n)
    return out


def _book_tags(book_dir: Path) -> dict:
    """这本书里的人物名与地名（用来给段落打标签）。

    取自书档自己的 `setting/setting.json`；没有就返回空 —— **不跨书共用词表**：
    两本书里都叫“老周”的人不是同一个人。
    """
    sp = book_dir / "setting" / "setting.json"
    data = _load_json(sp, {})
    people = _name_set(data.get("characters") if isinstance(data, dict) else [])
    world = data.get("world") if isinstance(data, dict) else {}
    places = []
    if isinstance(world, dict):
        for key in ("locations", "factions"):
            places += _name_set(world.get(key))
    return {"people": people, "places": places}


def _chapters(book_dir: Path) -> list:
    """[(章号, 路径)]：每章只取一层（refined > checked > raw）。"""
    base = book_dir / "chapters"
    if not base.is_dir():
        return []
    chosen = {}
    for layer in LAYER_DIRS:                      # 前面的层优先，先到先得
        d = base / layer
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            stem = f.stem
            if not stem.isdigit():
                continue
            n = int(stem)
            chosen.setdefault(n, (layer, f))
    return [(n, chosen[n][0], chosen[n][1]) for n in sorted(chosen)]


def _tag_of(text: str, tags: dict) -> dict:
    return {"people": [p for p in tags["people"] if len(p) >= 2 and p in text],
            "places": [p for p in tags["places"] if len(p) >= 2 and p in text]}


def build(root, books_root=None) -> dict:
    """重建索引。零模型调用、只读书档、只写 `data/paragraph_pool/index.json`。

    返回统计：`{books, chapters, paragraphs, skipped_short, skipped_long, built_at}`。
    幂等：同样的盘上内容重建两次，`paragraphs` 与 id 集合一致。
    """
    root = Path(root)
    base = Path(books_root) if books_root else root / "data" / "books"
    items = []
    books = 0
    chapters = 0
    short = long_ = 0
    # 书档可能在 data/books/<书名>/，也可能在 data/books/_trash/<书名>/（归档）。
    # 认 `_meta.json` 找书档根 —— 只列一层会把 _trash 下的整批书漏掉（实测 0 本书）。
    book_dirs = sorted({p.parent for p in base.rglob("_meta.json")}) if base.is_dir() else []
    for book_dir in book_dirs:
        if not (book_dir / "chapters").is_dir():
            continue
        tags = _book_tags(book_dir)
        books += 1
        for n, layer, f in _chapters(book_dir):
            chapters += 1
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:                                  # noqa: BLE001
                continue
            for i, para in enumerate(_split(text)):
                cn = _cn_count(para)
                if cn < MIN_CN:
                    short += 1
                    continue
                if cn > MAX_CN:
                    long_ += 1
                    continue
                items.append({
                    "id": "%s#%02d#%d" % (book_dir.name, n, i),
                    "book": book_dir.name,
                    "layer": layer,
                    "chapter": n,
                    "index": i,
                    "chars": cn,
                    "text": para,
                    "tags": _tag_of(para, tags),
                })
    data = {
        "version": 1,
        "built_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "books_root": str(base),
        "stats": {"books": books, "chapters": chapters, "paragraphs": len(items),
                  "skipped_short": short, "skipped_long": long_},
        "items": items,
    }
    _write_json(index_path(root), data)
    return data["stats"]


def load(root) -> dict:
    data = _load_json(index_path(root), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("items", [])
    data.setdefault("stats", {})
    return data


def stats(root) -> dict:
    d = load(root)
    st = dict(d.get("stats") or {})
    st["built_at"] = d.get("built_at") or ""
    st["exists"] = bool(d.get("items"))
    return st


def facets(root) -> dict:
    """检索用的人名/地名清单（取索引里真出现过的，不是设定集全量）。"""
    people, places = {}, {}
    for it in load(root).get("items") or []:
        for p in (it.get("tags") or {}).get("people") or []:
            people[p] = people.get(p, 0) + 1
        for p in (it.get("tags") or {}).get("places") or []:
            places[p] = places.get(p, 0) + 1
    return {"people": sorted(people.items(), key=lambda x: -x[1]),
            "places": sorted(places.items(), key=lambda x: -x[1])}


def search(root, q="", person="", place="", book="", limit=DEFAULT_LIMIT, offset=0,
           dedupe=True) -> dict:
    """检索。**没有关键词也没有人物/地名时返回空 + 提示** —— 不一次性吐全量。

    `dedupe=True`：同一段文本在多个书档里都出现时只留最新的一本
    （测试书之间互相复制很常见，不去重会把结果淹成一片）。
    """
    q = str(q or "").strip()
    person = str(person or "").strip()
    place = str(place or "").strip()
    book = str(book or "").strip()
    try:
        limit = max(1, min(MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    try:
        offset = max(0, int(offset))
    except (TypeError, ValueError):
        offset = 0
    if not (q or person or place or book):
        return {"ok": True, "items": [], "total": 0,
                "hint": "给一个关键词，或选一个人物/地名 —— 池子不会一次性把全部段落吐出来"}

    hits = []
    for it in load(root).get("items") or []:
        text = it.get("text") or ""
        if q and q not in text:
            continue
        if person and person not in ((it.get("tags") or {}).get("people") or []):
            continue
        if place and place not in ((it.get("tags") or {}).get("places") or []):
            continue
        if book and book != it.get("book"):
            continue
        hits.append(it)

    if dedupe:
        seen = {}
        for it in hits:
            key = hashlib.sha1(it["text"].strip().encode("utf-8")).hexdigest()[:16]
            seen.setdefault(key, it)
        hits = list(seen.values())

    total = len(hits)
    page = hits[offset:offset + limit]
    return {"ok": True, "items": page, "total": total, "offset": offset, "limit": limit}
