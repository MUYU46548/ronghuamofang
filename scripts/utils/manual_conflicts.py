# -*- coding: utf-8 -*-
"""人工摆出的素材分歧（当素材卡格式认不出时，唯一还能裁决的通道）。

## 为什么要有它

`material_state.detect_conflicts` 只能比**格式规范**的素材卡（`# 角色：名字` +
`## 基本信息` 下的 `- 字段：值`）。作者的素材实际是自由文本（设定稿、名词表、角色名册），
实测 46 份素材 0 张卡 —— 自动冲突检测一条都报不出来。

于是那条路只剩两种结局：要么作者去学卡格式（不现实），要么分歧永远没人提。
这一层补的是第三种：**把分歧整理成清单，作者在界面上逐条选。**

## 分工（别搞混）

- `manual_conflicts.json`   —— **摆事实**：同一件事有哪几种说法、各自出处。谁都能写（Agent 整理、
  脚本抽取），写完不生效。
- `decision_ledger.json`    —— **作者的裁决**：选了哪一版、为什么、什么时候。只追加，不改历史。
  写入端点在 API 层是 GUI-only（Agent 不能替作者签字）。

## 纪律

- 裁决**不改设定集**：这一步只记账。设定集里的值要不要跟着改，是另一件事
  （字段映射容易错，宁可留一步人工，也不许把猜测写进正典）。
- 账本只追加：同一条再裁一次就多一条记录，最新一条为准 —— 回看时看得见"改过主意"。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

MANUAL_REL = ("data", "setting", "manual_conflicts.json")
LEDGER_REL = ("data", "setting", "decision_ledger.json")

#: 两种"不选任何一版"的表态（与选具体值同一套校验）
KEEP = "__keep__"      # 保持设定集现状（记一笔"确认过，不改"）
REJECT = "__reject__"  # 整条作废（这些说法都不采用）


def manual_path(root) -> Path:
    return Path(root).joinpath(*MANUAL_REL)


def ledger_path(root) -> Path:
    return Path(root).joinpath(*LEDGER_REL)


def _read_json(p: Path, default):
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return default
    return default


def _write_json(p: Path, data) -> None:
    """原子写：先写 .tmp 再 replace。崩在写一半 = 半张表，比没写更糟。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load(root) -> dict:
    """读分歧清单。文件不存在 = 空表（不是错误：没整理过就是没有）。"""
    data = _read_json(manual_path(root), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("conflicts", [])
    if not isinstance(data["conflicts"], list):
        data["conflicts"] = []
    return data


def save(root, data: dict) -> None:
    data = dict(data or {})
    data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    _write_json(manual_path(root), data)


def load_ledger(root) -> dict:
    data = _read_json(ledger_path(root), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("entries", [])
    if not isinstance(data["entries"], list):
        data["entries"] = []
    return data


def as_conflicts(root) -> list:
    """把分歧清单转成与 `material_state` 冲突同形的条目（前端一套渲染）。

    多带四个字段：`id`（裁决用）、`source="manual"`、`question`（问作者什么）、
    `suggestion`（整理者的倾向，可推翻）、`current`（设定集里现在是哪个值）。
    """
    out = []
    for c in load(root)["conflicts"]:
        if not isinstance(c, dict):
            continue
        vals = []
        for v in c.get("values") or []:
            if isinstance(v, dict):
                vals.append({"value": str(v.get("value") or "")[:300],
                             "file": str(v.get("file") or ""),
                             "excerpt": str(v.get("excerpt") or "")[:300]})
            else:
                vals.append({"value": str(v)[:300], "file": "", "excerpt": ""})
        out.append({
            "id": str(c.get("id") or ""),
            "source": "manual",
            "name": str(c.get("name") or ""),
            "field": str(c.get("field") or ""),
            "severity": "review",
            "question": str(c.get("question") or ""),
            "suggestion": str(c.get("suggestion") or ""),
            "current": str(c.get("current") or ""),
            "values": vals,
            "decided": c.get("decided") if isinstance(c.get("decided"), dict) else None,
        })
    return out


def stats(root) -> dict:
    items = as_conflicts(root)
    return {"total": len(items),
            "pending": sum(1 for c in items if not c.get("decided")),
            "decided": sum(1 for c in items if c.get("decided"))}


def ledger_latest(root) -> dict:
    """每条分歧的最新裁决（账本只追加，这里取最后一条）。"""
    latest = {}
    for e in load_ledger(root)["entries"]:
        if isinstance(e, dict) and e.get("id"):
            latest[str(e["id"])] = e
    return latest


def resolve(root, cid: str, chosen: str, reason: str) -> tuple:
    """记一条裁决。返回 (ok, payload)。

    校验从严（每条都对应一种"看起来成功了其实没存对"）：
      · id 必须存在；`chosen` 必须是该条 values 里的某个值，或 __keep__ / __reject__；
      · reason 必填 —— 无理由的裁决，三天后连自己都看不懂为什么；
      · 账本只追加，同时把分歧条目标成已裁决（原文不动，留作比对）。
    """
    cid = str(cid or "").strip()
    chosen = str(chosen or "").strip()
    reason = str(reason or "").strip()
    data = load(root)
    hit = None
    for c in data["conflicts"]:
        if isinstance(c, dict) and str(c.get("id") or "") == cid:
            hit = c
            break
    if hit is None:
        return False, {"error": "没有这条分歧：%s" % (cid or "(空)"),
                       "hint": "先 GET /setting/conflicts 看 id；清单在 data/setting/manual_conflicts.json"}
    allowed = [str(v.get("value") if isinstance(v, dict) else v)
               for v in (hit.get("values") or [])]
    if chosen not in allowed + [KEEP, REJECT]:
        return False, {"error": "chosen 必须是这条分歧里的某个值，或 __keep__ / __reject__，收到: "
                                 + (chosen or "(空)"),
                       "allowed": allowed}
    if not reason:
        return False, {"error": "裁决必须写理由（为什么选它）—— 无理由的裁决留不住"}

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = {"id": cid, "name": str(hit.get("name") or ""),
             "field": str(hit.get("field") or ""), "chosen": chosen,
             "reason": reason, "at": now,
             "values": hit.get("values") or []}
    led = load_ledger(root)
    led["entries"].append(entry)
    led["updated_at"] = now
    _write_json(ledger_path(root), led)

    hit["decided"] = {"chosen": chosen, "reason": reason, "at": now}
    save(root, data)
    return True, {"ok": True, "entry": entry,
                  "stats": stats(root),
                  "note": "已记账。设定集里的值没有自动改 —— 要不要跟着改，是下一步的事。"}
