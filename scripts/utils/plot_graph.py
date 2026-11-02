# -*- coding: utf-8 -*-
"""情节图数据（2026-11-02，视频 v0.5 对标②：“复杂情节可收起、选中一步只看相关”）。

## 为什么只做"数据"不做"渲染"

渲染层已经有现成的：`RoleGraph.vue` 用 d3-force 做到了拖拽、缩放、选中看邻居。
差的是**把它接到情节上** —— 而接之前得先有一份"章 = 节点、因果/角色 = 连线"的数据。
所以这里只干一件事：把 `data/outline/chapters/*.md` 解析成图，零 token。

## 连线的两种

- `seq`：上一章 → 这一章（顺序，实线）。**唯一确定的因果**，不猜。
- `role`：两章共享至少一个角色（虚线）。只连**相邻 3 章内**的，否则 N 章两两配对
  会变成一团毛线（200 章 = 上万条），图就没法看了。

## 卷

卷边界不猜：优先读 `data/outline/volumes/*.md` 里的章号范围，读不到就按
`chapters_per_volume` 均匀切（默认 10）。**必须有个分组**，否则"收起"无从谈起。
"""
import re
from pathlib import Path

from utils.chapter_blueprint import parse_chapter_md

CHAP_DIR = Path("data") / "outline" / "chapters"
VOL_DIR = Path("data") / "outline" / "volumes"
ROLE_LINK_WINDOW = 3          # 只连相邻 3 章内的共享角色


def _root(root):
    return Path(root) if root else Path(".")


def _chapters(root, limit=None):
    d = _root(root) / CHAP_DIR
    out = []
    try:
        files = sorted(d.glob("*.md"))
    except OSError:
        return out
    for p in files:
        m = re.match(r"^(\d{1,4})", p.stem)
        if not m:
            continue
        n = int(m.group(1))
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append((n, parse_chapter_md(text)))
    out.sort(key=lambda x: x[0])
    if limit:
        out = out[:limit]
    return out


def _volumes(root, chapters, per_volume=10):
    """返回 {章号: 卷号}。优先从 volumes/*.md 的章号范围读，读不到就均匀切。"""
    mapping = {}
    d = _root(root) / VOL_DIR
    if d.is_dir():
        for p in sorted(d.glob("*.md")):
            m = re.search(r"(\d+)\D+(\d+)", p.stem)
            if not m:
                continue
            try:
                lo, hi = int(m.group(1)), int(m.group(2))
                vol = int(re.search(r"(\d+)", p.stem).group(1))
            except (AttributeError, ValueError):
                continue
            for n, _ in chapters:
                if lo <= n <= hi:
                    mapping[n] = vol
    if not mapping:
        for n, _ in chapters:
            mapping[n] = (n - 1) // max(1, per_volume) + 1
    return mapping


def build_graph(root=None, per_volume=10, limit=None):
    """章大纲 → {nodes, links, volumes}。没有章大纲时返回空图（不造节点）。"""
    chapters = _chapters(root, limit=limit)
    if not chapters:
        return {"nodes": [], "links": [], "volumes": [],
                "note": "还没有章大纲（data/outline/chapters/）。先跑阶段 3。"}

    volmap = _volumes(root, chapters, per_volume=per_volume)
    nodes, links = [], []
    prev = None
    for n, d in chapters:
        nodes.append({
            "id": "%02d" % n, "n": n, "vol": volmap.get(n, 1),
            "label": ("第%d章 %s" % (n, d.get("title") or "")).strip(),
            "title": d.get("title") or "",
            "function": d.get("function") or "",
            "roles": d.get("roles") or [],
            "events": (d.get("events") or [])[:2],
            "hook": d.get("hook") or "",
            "carryover": d.get("carryover") or "",
        })
        if prev is not None:
            links.append({"source": "%02d" % prev, "target": "%02d" % n, "kind": "seq"})
        prev = n

    # 共享角色：只连窗口内的，避免 N² 爆炸
    by_n = {n: (d.get("roles") or []) for n, d in chapters}
    nums = [n for n, _ in chapters]
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            if b - a > ROLE_LINK_WINDOW:
                break
            shared = set(by_n[a]) & set(by_n[b])
            if shared:
                links.append({"source": "%02d" % a, "target": "%02d" % b, "kind": "role",
                              "roles": sorted(shared)})

    vols = {}
    for n in nums:
        v = volmap.get(n, 1)
        vols.setdefault(v, {"vol": v, "chapters": []})["chapters"].append(n)
    return {
        "nodes": nodes,
        "links": links,
        "volumes": [vols[k] for k in sorted(vols)],
        "note": "节点=章；实线=顺序衔接，虚线=相邻 %d 章内共享角色" % ROLE_LINK_WINDOW,
    }
