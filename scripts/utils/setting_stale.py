# -*- coding: utf-8 -*-
"""设定条目来源登记（sources）与引用过期（stale）判定 —— M10，零 token。

## 要治的病

设定集条目写得再细，**没有任何东西记录它依据哪一章**。作者改完第 3 章，
引用第 3 章的设定条目在界面上完全看不出“可能已经不对了”—— 只能凭记忆重读全书。
现在每个条目可以带 `sources`（章号 + 版本），改章即待核，逐条核对后恢复。

## 口径（确定性、只读写文件、不调模型）

- 来源登记在**条目自身**的 `sources` 字段：
  `[{"chapter": 3, "version": "ab12cd34ef56", "layer": "refined",
     "linked_at": "...", "confirmed_at": "..."}]`
- `version` = 该章**当前稿**正文的 sha256 前 12 位。当前稿按
  `refined > checked > raw` 取第一份存在的（与编辑器/病灶扫描同一个顺序），
  `layer` 记它是哪一层 —— 正文变了或层变了都算变。
- **待核 = 登记时的 version/layer 与现在不一致，或那一章已经不存在。**
  这个判据在**读的时候现算**，不落状态文件：改了章，界面刷一下就红，
  不存在“标记没跟上”的二次故障（本仓最忌静默失败）。
- **从未登记 ≠ 待核 ≠ 已核对**：没登记就单列 `unlinked` / `absent`，
  绝不冒充“检查过没问题”。

## 为什么 link 只增不改

`link()` 只在**显式调用**时补登记，且已登记的条目**哪怕章已经改过也不刷新版本**
—— 否则“点一下扫描”就把待核全洗白了，机制等于摆设。恢复只能由作者逐条确认。

## 名字匹配为什么长词优先

条目名互相包含是常态（“沈砚”与“沈砚之”）。先把长词命中的位置挖空再匹配短词，
否则“沈砚之”出场的地方会被同时记成“沈砚”的引用 —— 白多一条待核。
短于 `MIN_NAME_CHARS` 的名字不做匹配（单字名在中文正文里必然误报），
但也不静默丢弃：报表里单列 `unlocatable`。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from utils.file_io import read_text, write_text

SETTING_REL = Path("data") / "setting" / "setting.json"
HISTORY_REL = Path("data") / "setting" / "history"
LAYERS = (("refined", "润色稿"), ("checked", "检查稿"), ("raw", "原稿"))
LAYER_LABEL = dict(LAYERS)

SOURCES_KEY = "sources"
MIN_NAME_CHARS = 2
VERSION_CHARS = 12

KIND_LABELS = {
    "characters": "角色",
    "world": "世界观",
    "timeline": "时间线",
    "plot_fragments": "情节碎片",
}

V_RE = re.compile(r"^setting_v(\d+)\.json$")


# --------------------------------------------------------------------- 基础

def setting_path(root=None):
    return (Path(root) if root else Path(".")) / SETTING_REL


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def fingerprint(text):
    """正文指纹：sha256 前 12 位（够短可读，撞车概率可忽略）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:VERSION_CHARS]


def load_setting(root=None):
    """读设定集。返回 (data, reason)：data 为 None 时 reason 说明为什么没读到。

    **不静默降级**：文件不存在 / 解析失败必须能被上层原样报出去
    （`checked=false` 的语义是“没查”，不是“没问题”）。
    """
    p = setting_path(root)
    if not p.exists():
        return None, "还没有设定集（%s 不存在）—— 阶段 1 完成后才有条目可登记" % p
    try:
        data = json.loads(read_text(p))
    except Exception as e:                                   # noqa: BLE001
        return None, "设定集解析失败（%s）: %s" % (p, str(e)[:160])
    if not isinstance(data, dict):
        return None, "设定集顶层不是对象（%s）" % p
    return data, ""


def load_chapters(root=None):
    """该书的当前稿：{章号: {layer, label, version, path, mtime, text}}。

    refined > checked > raw，与编辑器“当前稿”同一顺序；只管数字文件名的章。
    """
    base = Path(root) if root else Path(".")
    out = {}
    for layer, _label in LAYERS:
        d = base / "data" / "chapters" / layer
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                n = int(p.stem)
            except ValueError:
                continue
            if n in out:
                continue
            try:
                text = read_text(p)
                st = p.stat()
            except Exception:                                # noqa: BLE001
                continue
            out[n] = {"layer": layer, "label": LAYER_LABEL.get(layer, layer),
                      "version": fingerprint(text), "path": str(p),
                      "mtime": st.st_mtime, "text": text}
    return out


def _name_of(entry):
    return str(entry.get("name") or entry.get("title") or entry.get("名称") or "").strip()


def _aliases_of(entry):
    raw = entry.get("aliases")
    if raw is None:
        raw = entry.get("alias")
    if raw is None:
        raw = entry.get("别名")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple, set)):
        return [str(x) for x in raw]
    return []


def collect_entries(data):
    """设定集 → 条目清单（保留对原 dict 的引用，改它就是改设定集）。

    每项：{uid, kind, group, label, name, entry}。`kind` 对 world 子分组写
    `world.locations` 这种带点的形式 —— 它同时是确认接口的定位键。
    """
    items = []
    for kind in ("characters", "world", "timeline", "plot_fragments"):
        v = (data or {}).get(kind)
        buckets = []
        if isinstance(v, list):
            buckets.append((kind, kind, v))
        elif isinstance(v, dict):
            for gname in sorted(v):
                if isinstance(v[gname], list):
                    buckets.append(("%s.%s" % (kind, gname), gname, v[gname]))
        for full_kind, group, lst in buckets:
            for entry in lst:
                if not isinstance(entry, dict):
                    continue
                items.append({
                    "uid": "%s|%d" % (full_kind, len(items)),
                    "kind": full_kind,
                    "group": group,
                    "label": KIND_LABELS.get(kind, kind),
                    "name": _name_of(entry),
                    "entry": entry,
                })
    return items


def _token_map(items):
    """[(token, [uid...])]，长词在前；同一 token 可对应多条同名条目。"""
    by_token = {}
    for it in items:
        for tok in [it["name"]] + _aliases_of(it["entry"]):
            tok = str(tok or "").strip()
            if len(tok) < MIN_NAME_CHARS:
                continue
            by_token.setdefault(tok, []).append(it["uid"])
    return sorted(by_token.items(), key=lambda kv: (-len(kv[0]), kv[0]))


def _scan_mentions(chapters, tokens):
    """{uid: {章号...}}：每章正文里出现了哪些条目的名字（长词优先挖空）。"""
    hits = {}
    for n in sorted(chapters):
        masked = chapters[n]["text"]
        for tok, uids in tokens:
            if tok in masked:
                for uid in uids:
                    hits.setdefault(uid, set()).add(n)
                # 挖空：短词不得再匹配到长词内部（“沈砚之”不产生“沈砚”的引用）
                masked = masked.replace(tok, "\x00" * len(tok))
    return hits


def _sources(entry):
    """条目上现有的 sources（只保留能用的：chapter 是正整数）。保留其它字段。"""
    raw = entry.get(SOURCES_KEY)
    if not isinstance(raw, list):
        return []
    out = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        try:
            n = int(s.get("chapter"))
        except (TypeError, ValueError):
            continue
        if n < 1:
            continue
        item = dict(s)
        item["chapter"] = n
        out.append(item)
    return out


def _excerpt(entry):
    for k in ("role", "description", "detail", "snippet", "summary", "详情"):
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            return re.sub(r"\s+", " ", v.strip())[:80]
    return ""


def _backup(sp):
    """写前备份到 data/setting/history/setting_vN.json（与 setting_refine 同一命名）。"""
    hist = sp.parent / "history"
    hist.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in hist.glob("setting_v*.json"):
        m = V_RE.match(p.name)
        if m:
            nums.append(int(m.group(1)))
    dest = hist / ("setting_v%d.json" % ((max(nums) + 1) if nums else 1))
    shutil.copy2(sp, dest)
    return dest


def _write_setting(sp, data):
    """原子写 + 先备份。备份失败就不写（不敢在没退路时覆盖正典）。"""
    backup = _backup(sp) if sp.exists() else None
    sp.parent.mkdir(parents=True, exist_ok=True)
    write_text(sp, json.dumps(data, ensure_ascii=False, indent=1))
    return backup


# --------------------------------------------------------------------- 报表

def status(root=None):
    """当前待核清单（**读时现算**，不写任何文件）。

    返回的 `checked=false` 表示“根本没查”（设定集缺失/损坏），
    此时 `items` 为空**不代表**没有问题 —— 调用方必须原样透出。
    """
    root = Path(root) if root else Path(".")
    sp = setting_path(root)
    data, reason = load_setting(root)
    base = {"ok": True, "setting_path": str(sp)}
    if data is None:
        base.update({"checked": False, "reason": reason, "items": [], "counts": {
            "entries": 0, "quoted": 0, "stale": 0, "new": 0, "ok": 0,
            "absent": 0, "unlocatable": 0, "unnamed": 0}})
        return base

    chapters = load_chapters(root)
    items = collect_entries(data)
    tokens = _token_map(items)
    hits = _scan_mentions(chapters, tokens)

    counts = {"entries": len(items), "quoted": 0, "stale": 0, "new": 0, "ok": 0,
              "absent": 0, "unlocatable": 0, "unnamed": 0}
    out_items = []
    for it in items:
        name = it["name"]
        if not name:
            counts["unnamed"] += 1
            continue
        toks = [t for t in [name] + _aliases_of(it["entry"])
                if len(str(t or "").strip()) >= MIN_NAME_CHARS]
        if not toks:
            counts["unlocatable"] += 1
            continue
        srcs = _sources(it["entry"])
        mentioned = hits.get(it["uid"], set())
        counts["quoted"] += 1 if srcs else 0

        rows, stale_n = [], 0
        for s in srcs:
            n = s["chapter"]
            cur = chapters.get(n)
            if cur is None:
                rows.append({"chapter": n, "state": "missing",
                             "recorded_version": s.get("version") or "",
                             "recorded_layer": s.get("layer") or "",
                             "current_version": "", "current_layer": "",
                             "mentioned_now": False,
                             "reason": "第 %d 章已不存在" % n})
                stale_n += 1
                continue
            changed_version = str(s.get("version") or "") != cur["version"]
            changed_layer = bool(s.get("layer")) and s.get("layer") != cur["layer"]
            st = "changed" if (changed_version or changed_layer) else "ok"
            if st == "changed":
                stale_n += 1
            why = ""
            if changed_version:
                why = "第 %d 章的正文改过（%s）" % (n, cur["label"])
            elif changed_layer:
                why = "第 %d 章的当前稿换了一层（%s → %s）" % (
                    n, LAYER_LABEL.get(s.get("layer"), s.get("layer")), cur["label"])
            rows.append({
                "chapter": n, "state": st,
                "recorded_version": str(s.get("version") or ""),
                "recorded_layer": s.get("layer") or "",
                "current_version": cur["version"], "current_layer": cur["layer"],
                "mentioned_now": n in mentioned,
                "mtime": cur["mtime"],
                "reason": why,
            })

        new_chapters = sorted(n for n in mentioned if n not in {s["chapter"] for s in srcs})
        if stale_n:
            state = "stale"
        elif new_chapters:
            state = "new"
        elif srcs:
            state = "ok"
        else:
            state = "absent"
        counts[state] = counts.get(state, 0) + 1
        if state in ("stale", "new"):
            out_items.append({
                "kind": it["kind"], "group": it["group"], "label": it["label"],
                "name": name, "state": state, "excerpt": _excerpt(it["entry"]),
                "sources": rows, "new_chapters": new_chapters,
            })

    out_items.sort(key=lambda x: (0 if x["state"] == "stale" else 1, x["name"]))
    base.update({
        "checked": True, "reason": "",
        "chapters": len(chapters),
        "items": out_items,
        "counts": counts,
        "hint": _hint(counts, len(chapters)),
    })
    try:
        base["setting_mtime"] = sp.stat().st_mtime
    except OSError:
        pass
    return base


def _hint(counts, n_chapters):
    if not n_chapters:
        return "还没有任何章节正文 —— 来源没法登记。写完第一章后再回来。"
    if counts.get("quoted") == 0:
        return "还没有条目登记过来源。点“扫描章节并记来源”把现有章节的引用记下来。"
    if counts.get("stale"):
        return ("有 %d 条设定的引用章改过了：逐条看一遍，确认还成立就点“已核对”；"
                "不成立的去改设定集本身。" % counts["stale"])
    if counts.get("new"):
        return "有 %d 条设定在没登记过的章里出现了（新引用）。点“扫描章节并记来源”补登记。" % counts["new"]
    return "引用全部对得上当前正文。"


# --------------------------------------------------------------------- 登记

def link(root=None, dry_run=False):
    """把“条目 × 章节”的现有引用补登记进 sources（**只增不改**）。

    已登记的条目不刷新 version —— 章改过就该一直待核，直到作者确认。
    """
    root = Path(root) if root else Path(".")
    sp = setting_path(root)
    data, reason = load_setting(root)
    if data is None:
        return {"ok": False, "error": reason}
    chapters = load_chapters(root)
    if not chapters:
        return {"ok": False, "error": "还没有任何章节正文（data/chapters/{raw,checked,refined} 都是空的）"}
    items = collect_entries(data)
    tokens = _token_map(items)
    hits = _scan_mentions(chapters, tokens)

    now = _now()
    added, unlocatable = [], 0
    for it in items:
        if not it["name"]:
            continue
        toks = [t for t in [it["name"]] + _aliases_of(it["entry"])
                if len(str(t or "").strip()) >= MIN_NAME_CHARS]
        if not toks:
            unlocatable += 1
            continue
        mentioned = hits.get(it["uid"], set())
        srcs = _sources(it["entry"])
        have = {s["chapter"] for s in srcs}
        add = sorted(n for n in mentioned if n not in have)
        if not add:
            continue
        for n in add:
            cur = chapters[n]
            srcs.append({"chapter": n, "version": cur["version"], "layer": cur["layer"],
                         "linked_at": now, "confirmed_at": None})
        it["entry"][SOURCES_KEY] = srcs
        added.append({"kind": it["kind"], "group": it["group"], "name": it["name"],
                      "chapters": add})

    backup = None
    if added and not dry_run:
        try:
            backup = _write_setting(sp, data)
        except Exception as e:                                # noqa: BLE001
            return {"ok": False, "error": "写入设定集失败（未改动任何东西）: %s" % str(e)[:200]}

    return {
        "ok": True, "dry_run": bool(dry_run),
        "chapters": len(chapters), "entries": len(items),
        "added": added, "added_pairs": sum(len(a["chapters"]) for a in added),
        "unlocatable": unlocatable,
        "backup": str(backup) if backup else "",
        "message": ("预演：会新增 %d 条引用登记（未写盘）" % sum(len(a["chapters"]) for a in added))
                   if dry_run else
                   ("已登记 %d 条引用（涉及 %d 个条目）"
                    % (sum(len(a["chapters"]) for a in added), len(added))),
    }


# --------------------------------------------------------------------- 核对

def review(root=None, name="", kind=None, chapters=None, action="confirm"):
    """作者逐条核对后的动作：`confirm`（确认还成立）或 `drop`（去掉这条来源）。

    - `confirm`：把选中来源的 version/layer 更新为当前正文 → 不再待核；
      章已不存在的来源**不会**被确认（返回 skipped，要 `drop` 才去掉）。
    - `drop`：移除选中来源（章没了 / 这个引用本来就是误匹配）。
    """
    root = Path(root) if root else Path(".")
    sp = setting_path(root)
    action = str(action or "confirm").strip().lower()
    if action not in ("confirm", "drop"):
        return 400, {"ok": False, "error": "action 只能是 confirm / drop"}
    name = str(name or "").strip()
    if not name:
        return 400, {"ok": False, "error": "name 必填（要核对的条目名）"}
    want = None
    if chapters:
        try:
            want = sorted({int(x) for x in (chapters if isinstance(chapters, (list, tuple)) else [chapters])})
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "chapters 必须是章号数组"}
        if not want:
            want = None
    if action == "drop" and not want:
        return 400, {"ok": False, "error": "移除来源必须指明章号（防一次清空整条条目的来源）"}

    data, reason = load_setting(root)
    if data is None:
        return 400, {"ok": False, "error": reason}
    items = collect_entries(data)
    same = [it for it in items if it["name"] == name]
    if not same:
        return 404, {"ok": False, "error": "设定集里没有叫“%s”的条目" % name}
    if kind:
        same = [it for it in same if it["kind"] == kind]
        if not same:
            kinds = sorted({it["kind"] for it in items if it["name"] == name})
            return 404, {"ok": False,
                         "error": "“%s”不在类别 %s 下（同名条目在：%s）"
                                  % (name, kind, "、".join(kinds))}
    if len(same) > 1:
        return 400, {"ok": False,
                     "error": "设定集里“%s”有 %d 条同名条目，无法自动定位 ——"
                              "请在设定页改名区分后再核对" % (name, len(same))}

    it = same[0]
    srcs = _sources(it["entry"])
    if not srcs:
        return 400, {"ok": False, "error": "“%s”没有登记过来源，无可核对" % name}
    chapters_map = load_chapters(root)
    now = _now()

    selected = [s for s in srcs if (want is None or s["chapter"] in want)]
    if not selected:
        return 400, {"ok": False,
                     "error": "这条条目没有登记在这些章里（%s）"
                              % "、".join("第 %d 章" % n for n in want)}

    changed, skipped = [], []
    if action == "drop":
        keep = [s for s in srcs if (want is not None and s["chapter"] not in want)]
        it["entry"][SOURCES_KEY] = keep
        changed = [s["chapter"] for s in selected]
    else:
        for s in srcs:
            if want is not None and s["chapter"] not in want:
                continue
            cur = chapters_map.get(s["chapter"])
            if cur is None:
                skipped.append({"chapter": s["chapter"], "reason": "第 %d 章已不存在，无法核对（可移除该来源）"
                                % s["chapter"]})
                continue
            s["version"] = cur["version"]
            s["layer"] = cur["layer"]
            s["confirmed_at"] = now
            changed.append(s["chapter"])
        it["entry"][SOURCES_KEY] = srcs

    if not changed:
        return 400, {"ok": False,
                     "error": "没有可确认的来源：选中的章都已不存在。章没了的来源请用移除。",
                     "skipped": skipped}

    try:
        backup = _write_setting(sp, data)
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": "写入设定集失败（未改动任何东西）: %s" % str(e)[:200]}

    return 200, {
        "ok": True, "name": name, "kind": it["kind"], "action": action,
        "chapters": sorted(changed), "skipped": skipped,
        "backup": str(backup) if backup else "",
        "message": ("已核对第 %s 章（这条设定不再待核）"
                    % "、".join(str(n) for n in sorted(changed)))
                   if action == "confirm" else
                   ("已移除第 %s 章的来源登记"
                    % "、".join(str(n) for n in sorted(changed))),
    }
