# -*- coding: utf-8 -*-
"""素材搬运：批量移动 / 批量删除 / 撤回上一步（零 token，只碰文件系统）。

## 要治的病（试跑第 6 条）

作者原话：“不知道素材扔到哪里，误操作全部扔进了“结构化素材”，狂按撤回键无效。
也没有任何批量操作入口。”

三件事此前都不存在：多选、批量、撤回。这个模块负责**可被撤回的搬运**：

- 每一次批量移动 / 删除都记一条日志（`data/state/materials_undo.json`，留 20 条）；
- 删除不是真删：文件进 `data/state/materials_trash/<ts>/`，撤回时搬回原目录；
- 搬家用 `shutil.move`（rename 语义），**不改 mtime** —— 搬家不是重写。

## 安全边界

- 文件名只接受**裸文件名**（含 `/ \\ ..` 一律拒收）—— 这是本库唯一的写盘点，
  不能让一次批量操作把文件搬出 materials/。
- 目标已存在 → 默认**拒收那一条**并说明（不覆盖、不静默改名）。
- 撤回逐条反做：源没了 / 目标被占，那一条记 skipped，其余照常回滚（不整批失败）。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

RAW = Path("materials") / "raw"
SCRAPS = Path("materials") / "original_scraps"
STATE = Path("data") / "state" / "materials_undo.json"
TRASH = Path("data") / "state" / "materials_trash"
MAX_JOURNAL = 20

KIND_DIRS = {"cards": RAW, "scraps": SCRAPS}
KIND_LABEL = {"cards": "结构化卡片", "scraps": "原始碎片"}


def _p(root, rel):
    return (Path(root) if root else Path(".")) / rel


def safe_name(name):
    """只允许裸文件名。返回 (ok, 原因)。"""
    n = str(name or "").strip()
    if not n:
        return False, "空文件名"
    if "/" in n or "\\" in n:
        return False, "含路径分隔符（只接受裸文件名）"
    if n in (".", "..") or n.startswith(".."):
        return False, "非法文件名"
    if Path(n).is_absolute() or (len(n) > 1 and n[1] == ":"):
        return False, "绝对路径不被接受"
    return True, ""


def _load(root):
    p = _p(root, STATE)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(root, entries):
    p = _p(root, STATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries[-MAX_JOURNAL:], ensure_ascii=False, indent=1),
                 encoding="utf-8")


def journal(root):
    """可撤回的操作清单（新的在前）+ 每步的中文描述。"""
    out = []
    for e in reversed(_load(root)):
        items = e.get("items") or []
        out.append({
            "id": e.get("id"),
            "ts": e.get("ts"),
            "kind": e.get("kind"),
            "count": len(items),
            "label": ("移动到" + KIND_LABEL.get(e.get("to"), e.get("to") or "")
                      if e.get("kind") == "move" else "删除"),
            "names": [i.get("name") for i in items][:8],
        })
    return out


def move_many(root, names, to, allow_overwrite=False):
    """把 names 批量搬到 `to`（cards / scraps）。返回 (ok, result)。

    result: {moved:[{name,from,to}], skipped:[{name,reason}], journal_id}
    """
    if to not in KIND_DIRS:
        return False, {"error": "to 只能是 cards 或 scraps"}
    src_rel, dst_rel = None, KIND_DIRS[to]
    dst_dir = _p(root, dst_rel)
    # 源目录：从另一个容器搬（不猜“当前在哪”，按文件实际存在的那一侧找）
    moved, skipped = [], []
    for raw_name in names or []:
        ok, why = safe_name(raw_name)
        if not ok:
            skipped.append({"name": str(raw_name)[:60], "reason": why})
            continue
        name = str(raw_name).strip()
        src = None
        for kind, rel in KIND_DIRS.items():
            cand = _p(root, rel) / name
            if cand.is_file():
                src = (kind, rel, cand)
                break
        if src is None:
            skipped.append({"name": name, "reason": "两个素材目录里都没有这个文件"})
            continue
        kind, rel, src_path = src
        if kind == to:
            skipped.append({"name": name, "reason": "已经在" + KIND_LABEL[to]})
            continue
        dst = dst_dir / name
        if dst.exists() and not allow_overwrite:
            skipped.append({"name": name, "reason": "目标已存在同名文件（不覆盖）"})
            continue
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst))
        except OSError as e:
            skipped.append({"name": name, "reason": "移动失败：%s" % e})
            continue
        moved.append({"name": name, "from": str(rel).replace("\\", "/"),
                      "to": str(dst_rel).replace("\\", "/")})

    if not moved:
        return True, {"moved": [], "skipped": skipped, "journal_id": None}

    entries = _load(root)
    jid = time.strftime("%Y%m%d_%H%M%S_") + str(int(time.time() * 1000) % 1000)
    entries.append({"id": jid, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "kind": "move", "to": to, "items": moved})
    _save(root, entries)
    return True, {"moved": moved, "skipped": skipped, "journal_id": jid}


def delete_many(root, names):
    """批量删除 → 进回收站（不是真删）。返回 (ok, result)。"""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    trash_dir = _p(root, TRASH) / stamp
    moved, skipped = [], []
    for raw_name in names or []:
        ok, why = safe_name(raw_name)
        if not ok:
            skipped.append({"name": str(raw_name)[:60], "reason": why})
            continue
        name = str(raw_name).strip()
        src = None
        for kind, rel in KIND_DIRS.items():
            cand = _p(root, rel) / name
            if cand.is_file():
                src = (rel, cand)
                break
        if src is None:
            skipped.append({"name": name, "reason": "找不到这个素材"})
            continue
        rel, src_path = src
        dst = trash_dir / name
        try:
            trash_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst))
        except OSError as e:
            skipped.append({"name": name, "reason": "删除失败：%s" % e})
            continue
        moved.append({"name": name, "from": str(rel).replace("\\", "/"),
                      "to": (TRASH / stamp).as_posix()})
    if not moved:
        return True, {"deleted": [], "skipped": skipped, "journal_id": None}
    entries = _load(root)
    jid = stamp + "_del"
    entries.append({"id": jid, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "kind": "delete", "items": moved})
    _save(root, entries)
    return True, {"deleted": moved, "skipped": skipped, "journal_id": jid}


def undo_last(root):
    """撤回最近一步（移动或删除）。返回 (ok, result)。

    逐条反做：能回的都回，回不了的记 skipped —— **不整批失败**，
    否则“撤一半”会变成“一条都没撤”的更糟状态。
    """
    entries = _load(root)
    if not entries:
        return True, {"undone": [], "skipped": [], "kind": None,
                      "message": "没有可撤回的操作"}
    entry = entries.pop()
    undone, skipped = [], []
    for it in entry.get("items") or []:
        name = it.get("name")
        ok, why = safe_name(name)
        if not ok:
            skipped.append({"name": str(name)[:60], "reason": why})
            continue
        cur_rel = it.get("to")          # 现在在哪
        back_rel = it.get("from")       # 回到哪
        cur = _p(root, cur_rel) / name
        back_dir = _p(root, back_rel)
        back = back_dir / name
        if not cur.is_file():
            skipped.append({"name": name, "reason": "当前位置找不到文件（可能已被手工搬走）"})
            continue
        if back.exists():
            skipped.append({"name": name, "reason": "原位置已有同名文件（不覆盖）"})
            continue
        try:
            back_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(cur), str(back))
        except OSError as e:
            skipped.append({"name": name, "reason": "撤回失败：%s" % e})
            continue
        undone.append({"name": name, "back_to": back_rel})
    if not undone:
        # 一条都没撤成 → 把日志塞回去，别让用户以为“撤回成功但文件没动”
        entries.append(entry)
        _save(root, entries)
        return False, {"undone": [], "skipped": skipped, "kind": entry.get("kind"),
                       "error": "这一条撤回失败（文件已不在原位）"}
    _save(root, entries)
    return True, {"undone": undone, "skipped": skipped, "kind": entry.get("kind")}
