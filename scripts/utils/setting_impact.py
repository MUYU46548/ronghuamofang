# -*- coding: utf-8 -*-
"""设定改了，哪些已检查的章可能受影响（P1-5，2026-11-19）。

## 与 `setting_stale` 方向相反，别混

| 文件 | 谁变了 | 结论 |
|---|---|---|
| `setting_stale` | **章正文**变了 | 引用它的**设定条目**待核（条目要不要重写） |
| `setting_impact`（本文件） | **设定条目**变了 | 引用它的**章**待复检（章要不要重查） |

两个方向都需要：只做前者，作者改了设定之后，前面已过检的章看上去一切正常。

## 为什么不把全章都标待复检

改一个配角的年龄，全书都红 → 作者看一眼就学会无视这张清单（本仓最怕的
"误报训练作者忽略红灯"）。所以按**条目 → 章**的引用关系算：只有正文里真出现过
这个条目名的章才算受影响。引用关系的算法直接复用 `setting_stale` 那一套
（长词优先、短名不参与、别名一并算），同一个问题只许一份判据。

## 基线为什么必须落盘

"这一章是按哪版设定检查的"是历史事实，现算不出来 —— 它只能记在
`data/state/stage5_baseline.json`：

```json
{"at": "2026-11-19T12:00:00",
 "setting_fp": "ab12cd34ef56",
 "chapters": {"3": {"at": "...", "entries": {"characters|沈砚": "9f8e7d6c5b4a"}}}}
```

**不落"stale 标记"**：状态文件一旦存在，就会出现"设定了但标记没刷"这类静默失真；
本文件只落基线（过去的事实），待复检在读的时候现算。

## `checked=false` 的含义

没有任何基线（还没跑过阶段 5 / 基线文件坏了）→ `checked=false`，意思是**没查过**，
不是"全都对得上"。与冲突清单、设定过期的红线同一条。
"""
import json
import time
from pathlib import Path

from utils.file_io import read_text

BASELINE_REL = "data/state/stage5_baseline.json"
VERSION_CHARS = 12


def baseline_path(root=None):
    return (Path(root) if root else Path(".")) / BASELINE_REL


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _fp(entry):
    """条目指纹：该条目 JSON 的 sha256 前 12 位（字段级改动即变）。"""
    import hashlib
    try:
        raw = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(entry)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:VERSION_CHARS]


def _key(it):
    """条目定位键：kind + 名字。**不用位置序号**（插入一条就全体错位，会报假 stale）。"""
    name = str(it.get("name") or "").strip()
    return "%s|%s" % (it.get("kind") or "?", name or ("#" + str(it.get("uid") or "")))


def _checked_chapters(root=None):
    """已过检的章：`data/chapters/checked/NN.md` → {章号: 正文}。

    刻意不取 refined：这是"作者改**设定**之后，检查稿还成不成立"的问题，
    与正文后来润色过没有无关（正文变了是 `setting_stale` 的活）。
    """
    base = Path(root) if root else Path(".")
    out = {}
    d = base / "data" / "chapters" / "checked"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        if not p.stem.isdigit():
            continue
        try:
            out[int(p.stem)] = read_text(p)
        except Exception:                                    # noqa: BLE001
            continue
    return out


def _load_baseline(root=None):
    p = baseline_path(root)
    if not p.exists():
        return None, "还没有基线（%s 不存在）—— 阶段 5 跑过一次之后才有" % BASELINE_REL
    try:
        data = json.loads(read_text(p))
    except Exception as e:                                   # noqa: BLE001
        return None, "基线解析失败（%s）: %s" % (BASELINE_REL, str(e)[:160])
    if not isinstance(data, dict) or not isinstance(data.get("chapters"), dict):
        return None, "基线形状不对（%s）" % BASELINE_REL
    return data, ""


def _write_baseline(root, data):
    p = baseline_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def record(root=None, chapters=None):
    """给已过检的章记基线（章 → 这一章正文里出现过的条目 → 各自指纹）。

    `chapters` 只影响记录范围（单章重跑时传它）；默认记全部 checked 章。
    重跑之后同名章会被**覆盖**成新基线 —— 重检过就该反过来当它是新的，
    否则一章改完永远挂着"待复检"。
    """
    from utils import setting_stale as _ss
    data, reason = _ss.load_setting(root)
    if data is None:
        return {"ok": False, "reason": reason, "recorded": 0}
    texts = _checked_chapters(root)
    if chapters:
        want = {int(x) for x in chapters if str(x).isdigit()}
        texts = {n: t for n, t in texts.items() if n in want}
    if not texts:
        return {"ok": False, "reason": "没有已过检的章（data/chapters/checked 为空）",
                "recorded": 0}
    items = _ss.collect_entries(data)
    by_uid = {it["uid"]: it for it in items}
    tokens = _ss._token_map(items)
    mentions = _ss._scan_mentions({n: {"text": t} for n, t in texts.items()}, tokens)
    uid_chapters = {}
    for uid, chs in mentions.items():
        for n in chs:
            uid_chapters.setdefault(n, set()).add(uid)

    base, _ = _load_baseline(root)
    base = base or {"chapters": {}}
    base.setdefault("chapters", {})
    base["at"] = _now()
    base["setting_fp"] = _fp(data)
    n_rec = 0
    for n in sorted(texts):
        entries = {}
        for uid in sorted(uid_chapters.get(n) or []):
            it = by_uid.get(uid)
            if not it:
                continue
            entries[_key(it)] = _fp(it["entry"])
        base["chapters"][str(n)] = {"at": _now(), "entries": entries}
        n_rec += 1
    _write_baseline(root, base)
    return {"ok": True, "recorded": n_rec, "reason": ""}


def status(root=None):
    """现在算：哪些已过检的章因为设定条目改过而需要复检。

    返回 `{checked, reason, baseline_at, stale: [{chapter, reasons: [...]}],
    missing: [章号], chapters: 基线章数}`。
    """
    from utils import setting_stale as _ss
    base, reason = _load_baseline(root)
    if base is None:
        return {"checked": False, "reason": reason, "baseline_at": None,
                "stale": [], "missing": [], "chapters": 0}
    data, why = _ss.load_setting(root)
    if data is None:
        # 设定集读不到 ≠ 没有变化。照旧按“没查”上报，不冒绿。
        return {"checked": False, "reason": why, "baseline_at": base.get("at"),
                "stale": [], "missing": [], "chapters": len(base.get("chapters") or {})}
    now = {}
    for it in _ss.collect_entries(data):
        now[_key(it)] = _fp(it["entry"])
    present = set(_checked_chapters(root))
    stale, missing = [], []
    for ch, rec in sorted((base.get("chapters") or {}).items()):
        try:
            no = int(ch)
        except (TypeError, ValueError):
            continue
        if no not in present:
            missing.append(no)      # 章没了：不是"待复检"，也不是"没问题"
            continue
        reasons = []
        for key, fp in sorted((rec.get("entries") or {}).items()):
            if key not in now:
                reasons.append("“%s”已从设定集移除" % key.split("|", 1)[-1])
            elif now[key] != fp:
                reasons.append("“%s”改过" % key.split("|", 1)[-1])
        if reasons:
            stale.append({"chapter": no, "reasons": reasons})
    return {"checked": True, "reason": "", "baseline_at": base.get("at"),
            "stale": stale, "missing": missing,
            "chapters": len(base.get("chapters") or {})}
