# -*- coding: utf-8 -*-
"""设定条目来源链路（F21，2026-11-03）。

## 要治的病

台账里 F21 一直是 ⚠️：设定集条目有 `source` 字段（来自哪个文件），但**没有
“这个数是从第几版来的”链路**。作者改了设定以后，看到某个数值/性格/关系，
没法回答三件事：

1. 这条**最早**什么时候进库的？
2. 现在这个值**从哪一版起**是这样的？（上一次改动落在哪版）
3. 它是**哪次导入/哪个文件**带进来的？

## 口径（确定性、零 token、不给假答案）

- 版本序列 = `data/setting/history/setting_vN.json`（升序）+ **当前** `setting.json`；
- 字段级“从哪版起”：从最新往前比，第一个与本条当前值**不同**的版本之后，
  就是当前值的起点；字段名走 `setting_schema` 的同一套（role/traits/rel/简介）；
- **没有历史版本就如实说 `traceable: false`** —— 不拿“当前值”冒充“第 1 版就有”；
- 条目不存在 → `found: false`，不返回半截结论。

## 为什么不做逐字段哈希

历史快照只存条目原文，不存每一笔编辑。硬造“第 3 次修改”的粒度等于编造：
只能诚实到“哪一版开始变成现在这样”。
"""
import json
import re
import time
from pathlib import Path

LIVE_REL = Path("data") / "setting" / "setting.json"
HIST_REL = Path("data") / "setting" / "history"
V_RE = re.compile(r"^setting_v(\d+)\.json$")

#: 与 setting_versions.CHAR_FIELDS 同口径（两边都改了才算改完）
CHAR_FIELDS = (("role", "角色定位"), ("traits", "性格"), ("relations", "关系"),
               ("snippet", "简介"), ("locked", "锁定"))


def _root(root):
    return Path(root) if root else Path(".")


def _load(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:                                        # noqa: BLE001
        return None


def _norm(v):
    if isinstance(v, list):
        return sorted(str(x).strip() for x in v if str(x).strip())
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in sorted(v.items())}
    return str(v or "").strip()


def _name_of(e):
    if not isinstance(e, dict):
        return ""
    return str(e.get("name") or e.get("title") or e.get("名称") or "").strip()


def _index(doc, kind):
    """按名字建索引。kind: characters / world。"""
    out = {}
    if not isinstance(doc, dict):
        return out
    for e in doc.get(kind) or []:
        n = _name_of(e)
        if n:
            out[n] = e
    return out


def _versions(root):
    """[(v|None, doc, label, at)] 升序；末项是当前（v=None, label='当前'）。"""
    hist = _root(root) / HIST_REL
    items = []
    try:
        files = list(hist.glob("setting_v*.json"))
    except OSError:
        files = []
    for p in files:
        m = V_RE.match(p.name)
        if not m:
            continue
        doc = _load(p)
        if doc is None:
            continue
        try:
            at = time.strftime("%Y-%m-%d %H:%M",
                               time.localtime(p.stat().st_mtime))
        except OSError:
            at = ""
        items.append((int(m.group(1)), doc, "v%d" % int(m.group(1)), at))
    items.sort(key=lambda x: x[0])
    live = _load(_root(root) / LIVE_REL)
    if live is not None:
        items.append((None, live, "当前", ""))
    return items


def _field_value(e, key):
    if not isinstance(e, dict):
        return ""
    if key == "role":
        return e.get("role") or e.get("kind") or ""
    return e.get(key)


def trace(name, root=None, kind="characters"):
    """追溯一个条目的来源。返回 dict（见模块 docstring）。

    `kind` 只影响索引哪个列表；找不到时会自动试另一个（角色 / 世界条目）。
    """
    name = str(name or "").strip()
    out = {"ok": True, "name": name, "found": False, "traceable": False,
           "kind": kind, "current": {}, "field_since": [], "changed_in": [],
           "imports": [], "versions": [], "why": ""}
    if not name:
        return {"ok": False, "error": "name 必填"}

    steps = _versions(root)
    live_doc = steps[-1][1] if steps and steps[-1][0] is None else None
    if live_doc is None:
        out["why"] = "当前还没有 setting.json（阶段 1 尚未完成）"
        return out

    # 先按指定 kind 找，找不到再试另一个
    kinds = [kind] + [k for k in ("characters", "world") if k != kind]
    picked = None
    for k in kinds:
        idx = _index(live_doc, k)
        if name in idx:
            picked = (k, idx[name])
            break
    if picked is None:
        out["why"] = "当前设定集里没有这个条目"
        return out
    kind, cur = picked
    out["kind"] = kind
    out["found"] = True
    out["current"] = {
        "fields": {lb: _field_value(cur, k) for k, lb in CHAR_FIELDS},
        "source": str(cur.get("source") or ""),
        "status": str(cur.get("status") or ""),
    }

    # 逐版出现序列
    seen = []
    for v, doc, label, at in steps:
        e = _index(doc, kind).get(name)
        if e is not None:
            seen.append((v, label, at, e))
    out["versions"] = [{"v": v, "label": lb, "at": at} for v, lb, at, _e in seen]

    hist_seen = [s for s in seen if s[0] is not None]
    out["traceable"] = bool(hist_seen)
    if not hist_seen:
        out["why"] = ("还没有历史版本（覆盖设定时才会生成 setting_vN.json）"
                      "—— 只能说“当前值如此”，无法追溯更早来源")
        first_label = "当前"
    else:
        first = hist_seen[0]
        first_label = first[1]
        out["first_seen"] = {"v": first[0], "label": first[1], "at": first[2]}

    # 字段级“从哪版起是现在这个值”
    for key, label in CHAR_FIELDS:
        cur_val = _norm(_field_value(cur, key))
        since_label = first_label
        since_v = None
        for i in range(len(seen) - 1, -1, -1):
            v, lb, _at, e = seen[i]
            if _norm(_field_value(e, key)) != cur_val:
                # i 之后那一版就是当前值的起点
                if i + 1 < len(seen):
                    since_label = seen[i + 1][1]
                    since_v = seen[i + 1][0]
                break
            since_label = lb
            since_v = v
        out["field_since"].append({
            "field": label,
            "value": _field_value(cur, key),
            "since": since_label,
            "since_v": since_v,
        })

    # 逐版变化（有历史才有意义）
    prev = None
    for v, lb, at, e in seen:
        if prev is not None:
            changed = [lb2 for k, lb2 in CHAR_FIELDS
                       if _norm(_field_value(prev, k)) != _norm(_field_value(e, k))]
            if changed:
                out["changed_in"].append({"v": v, "label": lb, "at": at,
                                          "fields": changed})
        prev = e

    src = str(cur.get("source") or "").strip()
    if src:
        out["imports"].append({"source": src, "note": "设定集条目的 source 字段"})
    if not out["imports"]:
        out["imports_note"] = "该条目没有 source 字段（无法指认导入文件）"
    return out
