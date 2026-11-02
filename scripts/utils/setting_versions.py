# -*- coding: utf-8 -*-
"""设定集版本：列出、对照、恢复（2026-11-02，视频③对标 / 承诺台账 C+）。

## 要治的病

快照 `data/setting/history/setting_vN.json` **一直在写**（`obsidian_integrate`
与 `setting_refine` 覆盖前都会备份），但**人看不见也拿不回来** ——
改错了只能去目录里翻 json 手工对比。催更姬 v0.5 做的那件事就是把它接上界面：
两版对照、点一下恢复。

## 口径

- 对照按**条目名**配对（角色/世界条目都以 name 为键），比的是"这条从什么样变成什么样"；
- 恢复是**整体替换**（与 `build_setting_from_vault` 同语义），所以恢复前**再备份当前** ——
  恢复本身也要可回退；
- 只认 `setting_v<N>.json` 这一种命名（历史约定），其它文件忽略。
"""
import json
import re
import shutil
import time
from pathlib import Path

LIVE_REL = Path("data") / "setting" / "setting.json"
HIST_REL = Path("data") / "setting" / "history"
V_RE = re.compile(r"^setting_v(\d+)\.json$")

#: 参与逐字段对照的角色字段（按 setting_schema 的统一形态取）
CHAR_FIELDS = (("role", "角色定位"), ("traits", "性格"), ("relations", "关系"),
               ("snippet", "简介"), ("locked", "锁定"))


def _root(root):
    return Path(root) if root else Path(".")


def live_path(root=None):
    return _root(root) / LIVE_REL


def hist_dir(root=None):
    return _root(root) / HIST_REL


def _load_json(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:                                        # noqa: BLE001
        return None


def _counts(d):
    if not isinstance(d, dict):
        return {}
    out = {}
    for k in ("characters", "world", "plot_fragments", "timeline"):
        v = d.get(k)
        if isinstance(v, (list, dict)):
            out[k] = len(v)
    return out


def list_versions(root=None):
    """历史版本（新的在前）。没有历史 → []。"""
    d = hist_dir(root)
    items = []
    try:
        files = list(d.glob("setting_v*.json"))
    except OSError:
        return items
    for p in files:
        m = V_RE.match(p.name)
        if not m:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        data = _load_json(p)
        items.append({
            "v": int(m.group(1)),
            "file": p.name,
            "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "size": st.st_size,
            "counts": _counts(data),
            "readable": data is not None,
        })
    items.sort(key=lambda x: x["v"], reverse=True)
    return items


def _char_index(doc):
    """按 name 建索引（角色）。只认能取到 name 的条目。"""
    idx = {}
    for e in (doc or {}).get("characters") or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or e.get("名称") or "").strip()
        if name:
            idx[name] = e
    return idx


def _world_index(doc):
    idx = {}
    for e in (doc or {}).get("world") or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or e.get("title") or e.get("名称") or "").strip()
        if name:
            idx[name] = e
    return idx


def _norm_val(v):
    """比较用的规范化：列表顺序无关、字符串去空白。"""
    if isinstance(v, list):
        return sorted(str(x).strip() for x in v if str(x).strip())
    if isinstance(v, dict):
        return {k: _norm_val(x) for k, x in sorted(v.items())}
    return str(v or "").strip()


def _field_rows(kind_label, older, newer):
    """一条条目的字段级变化。返回 [{field, label, from, to}]。"""
    rows = []
    if kind_label == "character":
        pairs = [(k, lb, older.get(k), newer.get(k)) for k, lb in CHAR_FIELDS]
        # setting_schema 的 role 在原始条目里可能叫 kind
        pairs = [(k, lb,
                  older.get(k) if k != "role" else (older.get("role") or older.get("kind")),
                  newer.get(k) if k != "role" else (newer.get("role") or newer.get("kind")))
                 for k, lb, _a, _b in pairs]
    else:
        pairs = [("snippet", "描述", older.get("snippet"), newer.get("snippet"))]
    for key, label, a, b in pairs:
        if _norm_val(a) != _norm_val(b):
            rows.append({"field": key, "label": label,
                         "from": a if not isinstance(a, list) else "、".join(map(str, a)),
                         "to": b if not isinstance(b, list) else "、".join(map(str, b))})
    return rows


def diff(v_from, v_to=None, root=None):
    """两版对照。`v_to=None` 表示和**当前** setting.json 比。

    返回 {from, to, added, removed, changed, counts}；取不到文件 → 抛 FileNotFoundError。
    """
    p_from = hist_dir(root) / ("setting_v%d.json" % v_from)
    a = _load_json(p_from)
    if a is None:
        raise FileNotFoundError("没有可读的历史版本 v%d" % v_from)
    if v_to is None:
        p_to, label = live_path(root), "当前"
        b = _load_json(p_to)
        if b is None:
            raise FileNotFoundError("当前没有 setting.json")
    else:
        p_to = hist_dir(root) / ("setting_v%d.json" % v_to)
        b = _load_json(p_to)
        if b is None:
            raise FileNotFoundError("没有可读的历史版本 v%d" % v_to)
        label = "v%d" % v_to

    out = {"from": "v%d" % v_from, "to": label,
           "added": [], "removed": [], "changed": [],
           "counts": {"character": {}, "world": {}}}
    for kind_label, idx_a, idx_b in (
            ("character", _char_index(a), _char_index(b)),
            ("world", _world_index(a), _world_index(b))):
        names_a, names_b = set(idx_a), set(idx_b)
        added = sorted(names_b - names_a)
        removed = sorted(names_a - names_b)
        changed = []
        for name in sorted(names_a & names_b):
            rows = _field_rows(kind_label, idx_a[name], idx_b[name])
            if rows:
                changed.append({"name": name, "fields": rows})
        if kind_label == "character":
            out["added"], out["removed"], out["changed"] = added, removed, changed
        else:
            out["world"] = {"added": added, "removed": removed, "changed": changed}
        out["counts"][kind_label] = {"added": len(added), "removed": len(removed),
                                     "changed": len(changed),
                                     "same": len(names_a & names_b) - len(changed)}
    return out


def restore(v, root=None):
    """把历史版本 v 恢复成当前设定。**恢复前再备份当前**（恢复也可回退）。

    返回 (ok, message)。
    """
    src = hist_dir(root) / ("setting_v%d.json" % v)
    if not src.exists():
        return False, "没有 setting_v%d.json" % v
    data = _load_json(src)
    if data is None:
        return False, "setting_v%d.json 读不出来（不是合法 JSON 对象）" % v

    live = live_path(root)
    backup_note = ""
    if live.exists():
        # 复用既有命名序列，避免两套备份规则打架
        d = hist_dir(root)
        nums = [int(m.group(1)) for p in d.glob("setting_v*.json")
                if (m := V_RE.match(p.name))]
        nxt = (max(nums) + 1) if nums else 1
        dest = d / ("setting_v%d.json" % nxt)
        try:
            shutil.copy2(live, dest)
            backup_note = "；当前版本已存为 v%d" % nxt
        except OSError as e:
            return False, "恢复前备份失败（不敢覆盖）：%s" % e

    live.parent.mkdir(parents=True, exist_ok=True)
    tmp = live.with_name(live.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(live)                                        # 原子替换，防读到半截
    chars = len(data.get("characters") or [])
    world = len(data.get("world") or [])
    return True, ("已恢复到 v%d（角色 %d / 世界 %d 条）%s" % (v, chars, world, backup_note))
