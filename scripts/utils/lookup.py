# -*- coding: utf-8 -*-
"""作品资料检索索引（① 资料工具，2026-11-03）。

## 要治的病

此前 AI 拿到的是**预组装块**（`{{character_cards}}`、`{{kb_context}}`），
既不能按需查，也不能回写；设定集是一整份 JSON，全量塞提示词会顶爆，
不塞就只能靠流水线的固定拼装。催更姬的做法是"摘要索引 + 按需读全文"，
这里补的就是这一层。

## 产物

    data/index/lookup.json      # {version, built_at, entries:[{id,kind,name,one_line,path}]}
    data/notes/<标题>.md        # AI / 作者写下的案头笔记（不进正典，随时可删）

索引**只存摘要**（名字 + 一句话 + 出处），全文永远按 `path` 现读 —— 索引过期
最多是"摘要旧了"，不会把错内容当正文。

零 token：纯读盘 + 字符串匹配。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

INDEX_PATH = Path("data") / "index" / "lookup.json"
NOTES_DIR = Path("data") / "notes"

# 设定集里"条目列表"的常见键 → 规范 kind（大小写/中英文都认）
_KIND_KEYS = {
    "characters": "character", "character": "character", "角色": "character", "人物": "character",
    "locations": "location", "location": "location", "地点": "location", "场景": "location",
    "factions": "faction", "faction": "faction", "势力": "faction", "组织": "faction",
    "concepts": "concept", "concept": "concept", "概念": "concept", "设定": "concept",
    "items": "item", "item": "item", "物品": "item",
}


def _one_line(text, cap=80):
    """取一段文字里最适合当摘要的那一行。"""
    for line in (text or "").splitlines():
        s = line.strip().lstrip("#-*> ").strip()
        if s and not s.startswith("<!--"):
            return s[:cap]
    return ""


def _slug(s):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", str(s or "").strip()).strip("-.")
    return s[:60] or "note"


def _add(entries, kind, name, one_line, path):
    name = (name or "").strip()
    if not name:
        return
    entries.append({"id": "%s:%s" % (kind, name), "kind": kind, "name": name,
                    "one_line": one_line or "", "path": str(path)})


def build_index(root=".", materials_dirs=("materials/raw", "materials/original_scraps")):
    """重建索引：设定集条目 + 素材 md + 笔记。返回索引 dict（并落盘）。"""
    root = Path(root)
    entries = []

    # 1) 设定集（data/setting/setting.json）
    sp = root / "data" / "setting" / "setting.json"
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        data = {}
    for key, val in (data or {}).items():
        kind = _KIND_KEYS.get(str(key).lower(), None) or _KIND_KEYS.get(str(key), "concept")
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("名称") or item.get("title") or ""
                    desc = item.get("desc") or item.get("description") or item.get("summary") or ""
                    _add(entries, kind, name, (str(desc).strip()[:80] or ""), sp)
                elif isinstance(item, str):
                    _add(entries, kind, item[:40], "", sp)
        elif isinstance(val, dict):
            name = val.get("name") or key
            desc = val.get("desc") or val.get("description") or ""
            _add(entries, kind, str(name), str(desc)[:80], sp)

    # 2) 素材 md：标题 + 首行
    for d in materials_dirs:
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.md"))[:4000]:
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:600]
            except Exception:                                # noqa: BLE001
                continue
            m = re.search(r"^#\s+(.+)$", head, re.M)
            title = (m.group(1).strip() if m else p.stem)
            _add(entries, "material", title, _one_line(head.replace(title, "", 1)), p)

    # 3) 笔记
    nd = root / NOTES_DIR
    if nd.is_dir():
        for p in sorted(nd.glob("*.md")):
            try:
                head = p.read_text(encoding="utf-8", errors="replace")[:400]
            except Exception:                                # noqa: BLE001
                continue
            m = re.search(r"^#\s+(.+)$", head, re.M)
            _add(entries, "note", (m.group(1).strip() if m else p.stem), _one_line(head), p)

    idx = {"version": 1, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "root": str(root.resolve()), "count": len(entries), "entries": entries}
    from utils.file_io import write_text
    p = root / INDEX_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(idx, ensure_ascii=False, indent=2))
    return idx


def load_index(root="."):
    """读索引；没有/损坏就返回 None（调用方决定要不要重建，不自动写盘）。"""
    try:
        return json.loads((Path(root) / INDEX_PATH).read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return None


def _terms(q):
    q = str(q or "").strip()
    terms = [t for t in re.split(r"[\s,，、;；/]+", q) if t]
    if not terms and q:
        terms = [q]
    # 中文没有空格：整串也当词，并补 2-gram
    out = set()
    for t in terms:
        out.add(t.lower())
        if len(t) >= 2:
            for i in range(len(t) - 1):
                out.add(t[i:i + 2].lower())
    return out


def search(q, kind=None, limit=8, root="."):
    """按关键词查条目（名字命中权重更高）。返回 [{id,kind,name,one_line,path,score}]。"""
    idx = load_index(root) or build_index(root)
    terms = _terms(q)
    scored = []
    for e in idx.get("entries") or []:
        if kind and e.get("kind") != kind:
            continue
        name = (e.get("name") or "").lower()
        line = (e.get("one_line") or "").lower()
        score = sum(3 for t in terms if t and t in name) + \
                sum(1 for t in terms if t and t in line)
        if score:
            scored.append({**e, "score": score})
    scored.sort(key=lambda x: (-x["score"], x["name"]))
    return scored[:max(1, int(limit))]


def render(q, kind=None, limit=6, root="."):
    """渲染成给模型看的紧凑块（查不到返回空串）。"""
    hits = search(q, kind=kind, limit=limit, root=root)
    if not hits:
        return ""
    lines = ["## 作品资料命中（按需查的，不是全量）", ""]
    for h in hits:
        lines.append("- **%s**（%s）%s" % (h["name"], h["kind"],
                                          ("：" + h["one_line"]) if h["one_line"] else ""))
        lines.append("  · 出处 %s" % h["path"])
    return "\n".join(lines)


def add_note(title, text, root="."):
    """写一条案头笔记（data/notes/）。不进正典，不碰任何产物。返回路径。"""
    root = Path(root)
    p = root / NOTES_DIR / (_slug(title) + ".md")
    p.parent.mkdir(parents=True, exist_ok=True)
    from utils.file_io import write_text
    body = "# %s\n\n> 案头笔记（%s）。不是正典，随时可删；需要进正典请走设定/大纲的正式通道。\n\n%s\n" % (
        str(title or "笔记").strip(), time.strftime("%Y-%m-%d %H:%M"), str(text or "").strip())
    write_text(p, body)
    return p


def list_notes(root="."):
    nd = Path(root) / NOTES_DIR
    if not nd.is_dir():
        return []
    out = []
    for p in sorted(nd.glob("*.md"), key=lambda x: -x.stat().st_mtime):
        out.append({"title": p.stem, "path": str(p),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                time.localtime(p.stat().st_mtime))})
    return out
