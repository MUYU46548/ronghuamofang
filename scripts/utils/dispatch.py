# -*- coding: utf-8 -*-
"""派单候选的落盘与逐条采用（2026-11-25，总账 F7 的前半段）。

## 这一层解决什么

派单是“一次调用改多段”（作者定：分次调用会让成本增加三五倍）。一次调用的产出是
N 段改后文本，它们**不能直接覆盖正文** —— 一次改多段，某一段跑偏就得整批回滚。
所以中间必须有一步：产出先落成**候选**，作者逐条看过再采用。

## 与“段落精修”的关系

不是第二套机制：采用一条候选 = 调 `refine_paragraph.save_paragraph_text`，
与手改/精修走同一条回写路径（同一份段号口径、同一份历史版本）。

## 数据形状（`data/state/dispatch/ch03.json`）

    {
      "chapter": 3,
      "layer": "refined",
      "created_at": "2026-11-25 12:00",
      "source": "dispatch",          # 谁写的：dispatch / cli / 手工导入
      "items": [
        {"id": "d1", "para": 4, "note": "这段收得太急",
         "before": "原句…", "after": "改后…", "status": "open"}
      ]
    }

`status`: open / adopted / rejected。采用后保留记录（留痕，不删）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REL_DIR = "data/state/dispatch"


def _path(root: str | Path, chapter: int) -> Path:
    return Path(root) / REL_DIR / ("ch%02d.json" % int(chapter))


def load(root: str | Path, chapter: int) -> Optional[Dict[str, Any]]:
    p = _path(root, chapter)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                        # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("items", [])
    return data


def save(root: str | Path, chapter: int, items: List[Dict[str, Any]],
         layer: str = "refined", source: str = "dispatch") -> Dict[str, Any]:
    """整份写入（重复写入视为“这一轮派单的结果”，覆盖上一轮）。"""
    data = {
        "chapter": int(chapter),
        "layer": layer,
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
        "source": source,
        "items": items,
    }
    p = _path(root, chapter)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def set_status(root: str | Path, chapter: int, item_id: str, status: str) -> bool:
    data = load(root, chapter)
    if not data:
        return False
    hit = False
    for it in data.get("items", []):
        if it.get("id") == item_id:
            it["status"] = status
            if status == "adopted":
                it["adopted_at"] = time.strftime("%Y-%m-%d %H:%M")
            hit = True
    if hit:
        save(root, chapter, data.get("items", []),
             layer=data.get("layer", "refined"), source=data.get("source", "dispatch"))
    return hit


def find(root: str | Path, chapter: int, item_id: str) -> Optional[Dict[str, Any]]:
    data = load(root, chapter)
    if not data:
        return None
    for it in data.get("items", []):
        if it.get("id") == item_id:
            return it
    return None
