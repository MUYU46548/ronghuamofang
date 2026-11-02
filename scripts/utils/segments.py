# -*- coding: utf-8 -*-
"""段身份（2026-11-02，一期 F12「段是唯一的重算单位」的地基）。

## 要治的病

一期评审点名的**单一失败原因**：没有段身份 → 没有段级重算 → "一处"永远不是一个单位。
现在系统只知道"第 3 章变了"，不知道"第 3 章第 5 段变了"。

## 判据：段 id = 内容哈希，不是序号

`id = sha1(规范化后的段文本)[:12]`

- **不随位置变**：前面插一段，后面所有段的 id 不变（这是「段增删后位置稳定」F12）；
- **内容一变 id 就变**：不需要额外版本号，也不可能"标了旧 id 其实内容换了"；
- 代价：同一段内容出现两次 → 同一个 id。用 `出现次数` 区分（`kept` 按多重集比较），
  这在小说里是真实情况（"他没有说话。"可以连续出现）。

## 为什么段切分复用 `refine_paragraph.split_paragraphs`

界面上的段落列表、手改保存、这里算 id **必须是同一套切法** ——
三套切法意味着"界面上第 5 段"和"这里第 5 段"不是同一段，id 对不上的时候
查起来是灾难。所以只调它，不另写。

## 边界（不假装）

- 只给"身份与对照"，**不做段级重算**（F10/F13 是另一件事，要改调用链）。
- 快照放 `data/chapters/_anchors/NN.json`，不写进正文（正文加注释会污染导出与字数）。
"""
import hashlib
import json
import re
import time
from pathlib import Path

from utils.file_io import read_text
from refine_paragraph import split_paragraphs

ANCHOR_REL = Path("data") / "chapters" / "_anchors"
ID_LEN = 12
_NORM_RE = re.compile(r"[\s\u3000]+")


def _norm(text):
    """算 id 前的规范化：只压空白（**不动标点** —— 标点改了就是改了，id 该变）。"""
    return _NORM_RE.sub("", str(text or ""))


def seg_id(text):
    return hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()[:ID_LEN]


def anchors(text):
    """返回 [{index, id, chars, preview}]。段落切分与界面同一套。"""
    out = []
    for i, t in enumerate(split_paragraphs(text or "")):
        out.append({"index": i, "id": seg_id(t), "chars": len(t),
                    "preview": t[:24]})
    return out


def anchor_path(chapter_no, root=None):
    base = (Path(root) if root else Path(".")) / ANCHOR_REL
    return base / ("%02d.json" % int(chapter_no))


def snapshot(chapter_no, text, root=None, note=""):
    """把某章当前的段身份写盘（覆盖）。返回写下的 dict。"""
    data = {
        "chapter": int(chapter_no),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note,
        "count": 0,
        "segments": [],
    }
    segs = anchors(text)
    data["count"] = len(segs)
    data["segments"] = segs
    p = anchor_path(chapter_no, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)                                    # 原子替换，防读到半截
    return data


def load_snapshot(chapter_no, root=None):
    p = anchor_path(chapter_no, root)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:                                 # noqa: BLE001
        return None


def diff_ids(old_segments, new_segments):
    """按**多重集**比较 id。返回 {kept, added, removed, changed_indexes}。

    多重集而非集合：同一段话出现两次是合法的，出现次数变了就是变了。
    """
    from collections import Counter
    a = Counter(s["id"] for s in (old_segments or []))
    b = Counter(s["id"] for s in (new_segments or []))
    kept = sum((a & b).values())
    added = sum((b - a).values())
    removed = sum((a - b).values())
    # 位置变了但内容没变（id 相同）→ 也算"未改动"，只是挪了
    moved = 0
    if new_segments and old_segments:
        old_idx = {s["id"]: s["index"] for s in old_segments}
        for s in new_segments:
            if s["id"] in old_idx and old_idx[s["id"]] != s["index"]:
                moved += 1
    return {"kept": kept, "added": added, "removed": removed,
            "moved": moved, "before": len(old_segments or []),
            "after": len(new_segments or [])}


def compare(chapter_no, new_text, root=None):
    """与最近一次快照对照。没有快照 → `{"has_snapshot": False}`（不编数字）。"""
    snap = load_snapshot(chapter_no, root)
    if not snap:
        return {"has_snapshot": False, "chapter": int(chapter_no)}
    new_segs = anchors(new_text)
    d = diff_ids(snap.get("segments") or [], new_segs)
    d["has_snapshot"] = True
    d["chapter"] = int(chapter_no)
    d["snapshot_at"] = snap.get("at")
    # 一句话结论，给界面直接用
    if d["added"] == 0 and d["removed"] == 0 and d["moved"] == 0 and d["kept"] == d["before"]:
        d["summary"] = "一个字都没动"
    else:
        # 口径（2026-11-02 修）：以前写“改动 added+removed 段”—— 改写一段会变成
        # “改动 2 段”（1 新增 + 1 删除），读起来像改了两处。分开说。
        parts = []
        if d["added"] and d["removed"] and d["added"] == d["removed"]:
            parts.append("改写 %d 段" % d["added"])
        else:
            if d["added"]:
                parts.append("新增 %d 段" % d["added"])
            if d["removed"]:
                parts.append("删掉 %d 段" % d["removed"])
        parts.append("未动 %d 段" % d["kept"])
        if d["moved"]:
            parts.append("%d 段挪了位置" % d["moved"])
        d["summary"] = "，".join(parts)
    return d


def compare_file(chapter_no, path, root=None):
    """从文件读新文本再对照。文件不存在 → `{"error": ...}`。"""
    p = Path(path)
    if not p.exists():
        return {"has_snapshot": False, "error": "章节文件不存在：%s" % p}
    return compare(chapter_no, read_text(p), root=root)
