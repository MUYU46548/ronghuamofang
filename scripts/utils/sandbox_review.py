# -*- coding: utf-8 -*-
"""沙盒产物的审核状态机（2026-09-21 新增）。

## 为什么需要它

沙盒（`obsidian_bridge.write_sandbox`）此前只是**往目录里丢 .md 文件** ——
没有「这份产物我看过没有 / 通过没有」的记录。用户面对一堆文件，
无法区分「新生成的待审稿」与「已确认可粘贴的稿」，也没有「驳回」这个动作。

本模块给每份沙盒产物挂一个状态：

    pending（待审，默认） → approved（已通过，可粘贴进 vault）
                          → rejected（已驳回，附原因）

## 为什么不写进沙盒文件自己的 frontmatter

沙盒是「准备粘贴进 vault」的中转区。若把审核状态写进 frontmatter，
用户粘贴时会**把内部状态一起带进 vault 词条**（污染正典）。
所以状态独立存在 `data/state/sandbox_manifest.json`。

## 改过的文件必须重新审核

登记时记录内容 `sha256`。同一个路径：

- 内容**没变** → 保留原状态（重复写入不重置你的审核结论）
- 内容**变了**   → 状态重置为 `pending`

这条是正确性关键：产物被重新生成后仍显示「已通过」，等于让旧审核为
新内容背书 —— 正是本项目最忌讳的假成功。
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text

MANIFEST = "data/state/sandbox_manifest.json"

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"

STATUS_LABELS = {PENDING: "待审", APPROVED: "已通过", REJECTED: "已驳回"}


def _sha256(path):
    """文件内容指纹。读不到时返回空串（保守：视为"变了"）。"""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()


def load(manifest_path=MANIFEST):
    """读状态库。文件缺失/损坏时返回空 dict（不抛）。"""
    p = Path(manifest_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(read_text(p))
    except Exception:                              # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def save(data, manifest_path=MANIFEST):
    p = Path(manifest_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(data, ensure_ascii=False, indent=1))


def register(rel_path, sandbox_dir, kind="", source="", manifest_path=MANIFEST):
    """登记/更新一份沙盒产物。返回 (entry, changed)。

    `changed=True` 表示「内容变了 → 状态被重置为 pending」（需要重新审核）。
    """
    rel = str(rel_path).replace("\\", "/")
    full = Path(sandbox_dir) / rel
    digest = _sha256(full)
    data = load(manifest_path)
    prev = data.get(rel)

    if prev and prev.get("sha256") == digest and digest:
        # 内容没变 → 保留原状态（重复写入不重置审核结论）
        prev["last_seen_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save(data, manifest_path)
        return prev, False

    entry = {
        "path": rel,
        "kind": kind or (prev or {}).get("kind", ""),
        "source": source or (prev or {}).get("source", ""),
        "sha256": digest,
        "status": PENDING,
        "created_at": (prev or {}).get("created_at")
        or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reviewed_at": "",
        "note": "",
    }
    data[rel] = entry
    save(data, manifest_path)
    return entry, True


def set_status(rel_path, status, note="", manifest_path=MANIFEST):
    """设置审核状态。返回 (ok, msg)。"""
    if status not in (PENDING, APPROVED, REJECTED):
        return False, f"未知状态: {status}（可选 {PENDING}/{APPROVED}/{REJECTED}）"
    rel = str(rel_path).replace("\\", "/")
    data = load(manifest_path)
    entry = data.get(rel)
    if entry is None:
        return False, f"未登记: {rel}（先让产物写入沙盒，或用 --list 查看）"
    entry["status"] = status
    entry["reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if note:
        entry["note"] = note
    save(data, manifest_path)
    return True, f"{rel} → {STATUS_LABELS.get(status, status)}"


def list_items(status=None, manifest_path=MANIFEST):
    """全部/按状态筛取的条目（按更新时间倒序）。"""
    data = load(manifest_path)
    items = list(data.values())
    if status:
        items = [e for e in items if e.get("status") == status]
    return sorted(items, key=lambda e: e.get("updated_at") or "", reverse=True)


def stats(manifest_path=MANIFEST):
    """各状态计数 + 沙盒中「未登记」的孤儿文件数。

    孤儿检测很关键：产物是**手工**拷进沙盒、或换过沙盒目录时，
    会出现「文件在沙盒里但状态库里没有」——不报出来就成了审核盲区。
    """
    data = load(manifest_path)
    out = {PENDING: 0, APPROVED: 0, REJECTED: 0}
    for e in data.values():
        s = e.get("status")
        if s in out:
            out[s] += 1
    return out


def orphans(sandbox_dir, manifest_path=MANIFEST):
    """沙盒里存在、但状态库未登记的 .md 相对路径。"""
    root = Path(sandbox_dir)
    if not root.is_dir():
        return []
    known = set(load(manifest_path))
    found = []
    for p in sorted(root.rglob("*.md")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        if rel not in known:
            found.append(rel)
    return found
