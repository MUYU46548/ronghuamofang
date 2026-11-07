# -*- coding: utf-8 -*-
"""段落批注的落盘与“漂移”判定（B5，2026-11-06）。

## 为什么需要这一层

此前的“段落批注”只活在一次交互里：在界面上写一句意见 → 立刻交给模型重写 → 意见就没了。
于是有两件事做不到：

1. **回头看**：昨天说过“结尾那段要收住”，今天打开章节，那条意见不存在；
2. **漂移提示**：正文一改，就算存了段落号，也可能指到别的段落 —— 而且**没有任何提示**，
   作者以为批注还挂着，其实早就错位。

这一层把批注存成“**引用原文 + 段落号 + 前后文指纹**”：

- 打开章节时先按原文精确重定位（段落被前后挪动也能找回）；
- 原文找不到就退一步按前后文指纹定位（段落被小改也能找回），并标 `drift=True`；
- 都找不到 → `status="missing"`，界面**标红**说“这句已改/已删”，绝不悄悄指向别的段。

## 只提示不自动改

漂移时只标红 + 给出新位置，**不自动改写批注内容**（那等于替作者改变意见）。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REL_DIR = "data/state/annotations"
_FP_LEN = 12          # 指纹取前后各 12 个汉字（够区分相邻段，又不至于因为一处小改就失效）


def _path(root: str | Path, chapter: int) -> Path:
    return Path(root) / REL_DIR / ("ch%02d.json" % int(chapter))


def _load(root: str | Path, chapter: int) -> List[Dict[str, Any]]:
    p = _path(root, chapter)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("annotations") if isinstance(data, dict) else data
    return [a for a in (items or []) if isinstance(a, dict)]


def _save(root: str | Path, chapter: int, items: List[Dict[str, Any]]) -> None:
    p = _path(root, chapter)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"chapter": int(chapter), "annotations": items},
                            ensure_ascii=False, indent=2), encoding="utf-8")


def _fp(text: str) -> str:
    """去掉标点空白后取指纹（改个标点不该让批注失效）。"""
    from utils.homogeneity import cjk_only
    s = cjk_only(text or "")
    return s[:_FP_LEN] + "|" + s[-_FP_LEN:]


def _norm(text: str) -> str:
    from utils.homogeneity import cjk_only
    return cjk_only(text or "")


def split_paragraphs(text: str) -> List[str]:
    """与段落精修同一条切分口径（同一份实现，别再造第二套）。"""
    try:
        import refine_paragraph
        return refine_paragraph.split_paragraphs(text or "")
    except Exception:                                        # noqa: BLE001
        return [p.strip() for p in (text or "").split("\n\n") if p.strip()]


def add(root: str | Path, chapter: int, note: str, para_index: int,
        paragraphs: List[str], layer: str = "refined") -> Dict[str, Any]:
    """挂一条批注：记下引用原文 + 段落号 + 前后文指纹。"""
    note = (note or "").strip()
    if not note:
        raise ValueError("批注内容不能为空")
    try:
        i = int(para_index)
    except (TypeError, ValueError):
        raise ValueError("para_index 必须是段落序号（从 0 起）")
    if i < 0 or i >= len(paragraphs or []):
        raise ValueError("段落序号 %s 超出范围（本章 %d 段）" % (i, len(paragraphs or [])))
    quote = (paragraphs[i] or "").strip()
    items = _load(root, chapter)
    item = {
        "id": "a%d_%s" % (int(time.time()), hashlib.sha1(
            (quote + note).encode("utf-8")).hexdigest()[:6]),
        "quote": quote,
        "fp": _fp(quote),
        "prev_fp": _fp(paragraphs[i - 1]) if i > 0 else "",
        "next_fp": _fp(paragraphs[i + 1]) if i + 1 < len(paragraphs) else "",
        "para_index": i,
        "layer": layer,
        "note": note,
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "status": "open",
    }
    items.append(item)
    _save(root, chapter, items)
    return item


def resolve(root: str | Path, chapter: int, aid: str) -> bool:
    """把一条批注标成“已处理”（不删，留痕）。"""
    items = _load(root, chapter)
    hit = False
    for a in items:
        if a.get("id") == aid:
            a["status"] = "resolved"
            a["resolved_at"] = time.strftime("%Y-%m-%d %H:%M")
            hit = True
    if hit:
        _save(root, chapter, items)
    return hit


def remove(root: str | Path, chapter: int, aid: str) -> bool:
    """删除一条批注（写错了才用；正常处理请 resolve）。"""
    items = _load(root, chapter)
    keep = [a for a in items if a.get("id") != aid]
    if len(keep) == len(items):
        return False
    _save(root, chapter, keep)
    return True


def relocate(items: List[Dict[str, Any]], paragraphs: List[str]) -> List[Dict[str, Any]]:
    """按当前正文重定位批注，返回带 `drift` / `status` / 新段落号的副本。

    三档：
    - 精确命中引用原文 → 段落号更新，`drift=False`；
    - 原文找不到但前后文指纹能对上 → 认为“这一段被小改过”，`drift=True`，给出新位置；
    - 都找不到 → `status="missing"`，界面标红“引用的原句已改/已删”。
    """
    paras = [p or "" for p in (paragraphs or [])]
    norms = [_norm(p) for p in paras]
    out: List[Dict[str, Any]] = []
    for a in items or []:
        row = dict(a)
        q = _norm(a.get("quote") or "")
        idx = a.get("para_index")
        row["drift"] = False
        found = None
        # ① 先看原位置还是不是它（最常见：没动过）
        if isinstance(idx, int) and 0 <= idx < len(norms) and norms[idx] == q and q:
            found = idx
        else:
            # ② 全文找同一句（段落被前后挪动）
            for j, t in enumerate(norms):
                if q and t == q:
                    found = j
                    break
        if found is None and q:
            # ③ 退一步：前后文指纹定位（这一段被改了几个字，但邻居没变）
            pf, nf = a.get("prev_fp") or "", a.get("next_fp") or ""
            for j in range(len(paras)):
                prev_ok = (not pf) or (j > 0 and _fp(paras[j - 1]) == pf)
                next_ok = (not nf) or (j + 1 < len(paras) and _fp(paras[j + 1]) == nf)
                if prev_ok and next_ok:
                    found = j
                    row["drift"] = True
                    break
        if found is None:
            row["status"] = "missing"
            row["current_text"] = ""
            row["why"] = "引用的原句在这一层里找不到（已被改写或删除）"
        else:
            row["status"] = a.get("status") or "open"
            row["para_index"] = found
            row["current_text"] = paras[found]
            if found != a.get("para_index") and not row["drift"]:
                row["drift"] = True
                row["why"] = "段落位置变了（从第 %s 段移到第 %s 段）" % (
                    int(a.get("para_index") or 0) + 1, found + 1)
            elif row["drift"]:
                row["why"] = "这一段被改过（按前后文找回来的），原句已不是现在这句"
        out.append(row)
    return out


def list_for_chapter(root: str | Path, chapter: int, paragraphs: Optional[List[str]] = None
                     ) -> Dict[str, Any]:
    items = _load(root, chapter)
    rows = relocate(items, paragraphs) if paragraphs is not None else items
    return {
        "ok": True,
        "chapter": int(chapter),
        "total": len(rows),
        "open": len([r for r in rows if r.get("status") == "open"]),
        "drifted": len([r for r in rows if r.get("drift")]),
        "missing": len([r for r in rows if r.get("status") == "missing"]),
        "annotations": rows,
        "relocated": paragraphs is not None,
    }
