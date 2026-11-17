# -*- coding: utf-8 -*-
"""沙盒候选与现有设定集的冲突预检（2026-11-17，沙盒 P0）。

## 为什么要有它

沙盒队列此前只回答“这条改过没有”，不回答“它和书里已有的设定冲不冲突”：
作者点“通过”之前，得自己去设定集里搜一遍名字，且**搜到了也不一定看出来**
（同名不同身份、锁定设定被改写）。

这一层只做确定性比对，零 token、只读：

1. **同名**：沙盒词条名与 `setting.json` 的角色/世界条目同名 → 警告（锁定设定额外点名）；
2. **近名**：一个名字包含另一个且长度差 ≤2 → 提示（可能是同一人/同一物）；
3. **无从比较**：设定集缺口条目录 → 明说“无从比较”，**不许当成“无冲突”**。

## 边界（刻意不做）

- 不判断“哪个设定更对”（创作决策归作者）；
- 不写任何文件（审核状态都不动，这一层只读）；
- 只比名字与锁定状态，不做全文语义比对（那需要模型，且判据不硬）。

名字来源优先级：frontmatter 的 name/title/id → 正文第一个 H1 → 文件名 stem。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

SETTING_REL = "data/setting/setting.json"
NAME_KEYS = ("name", "title", "id")
_TYPE_KEYS = ("role", "kind", "type", "identity", "category")


def _norm(s: Any) -> str:
    """比较用归一：去空白/间隔号/下划线，转小写（“沈 砚” = “沈砚”）。"""
    return re.sub(r"[\s·・._\-—]+", "", str(s or "")).strip().lower()


def parse_frontmatter(text: str) -> Dict[str, str]:
    """容忍型 frontmatter 解析（只认 `key: value` 单行，够读名字与类型）。"""
    m = re.match(r"^---\s*\n(.*?)\n---", text or "", re.S)
    if not m:
        return {}
    fm: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip().lstrip("- ").strip()] = v.strip().strip('"').strip("'")
    return fm


def item_name(text: str, path: str | Path = "") -> str:
    fm = parse_frontmatter(text)
    for k in NAME_KEYS:
        if str(fm.get(k) or "").strip():
            return str(fm[k]).strip()
    m = re.search(r"^#\s+(.+)$", text or "", re.M)
    if m:
        return m.group(1).strip().lstrip("#").strip()
    return Path(str(path)).stem if path else ""


def load_entries(root: str | Path = ".") -> Tuple[List[Dict[str, Any]], str]:
    """读设定集里的角色/世界条目。返回 (entries, 缺数据的说明)。

    缺数据时 entries 为空、说明非空 —— 调用方据此说“无从比较”，不许说“无冲突”。
    """
    p = Path(root) / SETTING_REL
    if not p.exists():
        return [], "设定集还没有 setting.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], "setting.json 读不出来（坏文件），没法比对"
    out: List[Dict[str, Any]] = []
    for key, label in (("characters", "角色"), ("world", "世界")):
        for e in (data.get(key) or []):
            if isinstance(e, dict) and str(e.get("name") or e.get("title") or "").strip():
                out.append({"kind": label, "entry": e})
    if not out:
        return [], "设定集里还没有角色/世界条目"
    return out, ""


def _role_of(entry: Dict[str, Any]) -> str:
    for k in _TYPE_KEYS:
        v = str(entry.get(k) or "").strip()
        if v:
            return v
    return ""


def check_item(text: str, path: str | Path, entries: List[Dict[str, Any]],
               why_no_data: str = "") -> Dict[str, Any]:
    """一个沙盒词条 vs 设定集。返回 {checked, why, level, items[], name}。"""
    if not entries:
        return {"checked": False, "why": why_no_data or "设定集里还没有条目，无从比较",
                "level": "none", "items": []}
    name = item_name(text, path)
    n = _norm(name)
    conflicts: List[Dict[str, str]] = []
    for row in entries:
        e = row["entry"]
        en = _norm(e.get("name") or e.get("title"))
        if not n or not en:
            continue
        if n == en:
            role = _role_of(e)
            detail = "同名%s已有词条%s" % (row["kind"], ("（身份：%s）" % role) if role else "")
            if e.get("locked") or e.get("lock") or e.get("immutable"):
                detail += "，而且是锁定设定（不能靠这条覆盖）"
            conflicts.append({"kind": "same_name", "level": "warn", "detail": detail})
        elif (n in en or en in n) and abs(len(n) - len(en)) <= 2:
            conflicts.append({"kind": "near_name", "level": "info",
                              "detail": "疑似同名：这条=%s / 已有=%s"
                                        % (name, e.get("name") or e.get("title"))})
    level = "warn" if any(c["level"] == "warn" for c in conflicts) else (
        "info" if conflicts else "none")
    return {"checked": True, "why": "", "level": level,
            "items": conflicts, "name": name}
