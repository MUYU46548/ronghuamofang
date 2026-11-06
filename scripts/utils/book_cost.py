# -*- coding: utf-8 -*-
"""本书成本账（2026-11-05，试跑第 4 条）。

## 为什么不能"自动读到全部"

`engine: self` / `hermes` 下，真正花钱的是**外部 Agent**（它调模型、它计费），
墨坊这边 `cost_log` 恒 0 —— 程序拿不到 Agent 的账单。所以这本账只能分两半：

| 半边 | 谁填 | 内容 |
|---|---|---|
| 机器侧 | 程序自动 | 章数、汉字数、各阶段状态与时间、墨坊自己的 token 与费用 |
| Agent 侧 | 执行方跑完填一行 | `--yuan 45 --note "Hermes，12 章"` |

**不猜**：Agent 侧没填就如实显示"未上报"，不按字数折算成钱（那会造出假账）。

## 落在哪

`data/state/book_costs.json` —— 跟着工作区走，换书归档时随书搬。
每次 `orchestrator.run()` 收尾自动 upsert 一次（机器侧刷新，Agent 侧已填的保留）。

## 用法

    python scripts/book_cost.py                       # 看当前这本书
    python scripts/book_cost.py auto                  # 只刷新机器侧读数
    python scripts/book_cost.py record --yuan 45 --note "Hermes 4.x，12 章"
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .word_count import count_cn_words

REL = Path("data") / "state" / "book_costs.json"
SCHEMA = 1


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _book_name(root: Path) -> str:
    prog = _read_json(root / "data" / "state" / "progress.json", {}) or {}
    if prog.get("project"):
        return str(prog["project"])
    try:
        from .config_io import load_config_yaml
        cfg = load_config_yaml(root / "config" / "project.yaml") or {}
        return str((cfg.get("book") or {}).get("name") or "未命名")
    except Exception:                                       # noqa: BLE001
        return "未命名"


def chapter_words(root: Path):
    """(章数, 汉字数)：优先润色稿，没有就用原稿。"""
    for layer in ("refined", "checked", "raw"):
        d = root / "data" / "chapters" / layer
        files = sorted(d.glob("*.md")) if d.is_dir() else []
        if files:
            total = sum(count_cn_words(f.read_text(encoding="utf-8", errors="replace"))
                        for f in files)
            return len(files), total, layer
    return 0, 0, ""


def _stage_times(root: Path):
    prog = _read_json(root / "data" / "state" / "progress.json", {}) or {}
    stages = prog.get("stages") or {}
    out = {}
    for k, v in stages.items():
        if isinstance(v, dict):
            out[str(k)] = {"status": v.get("status"),
                           "finished_at": v.get("finished_at")}
    return out


def snapshot(root: Path, db=None) -> dict:
    """机器侧读数（不写盘）。"""
    root = Path(root)
    chapters, words, layer = chapter_words(root)
    try:
        if db is None:
            from .db import RunDB
            db = RunDB(root / "logs" / "runs.db")
        yuan = float(db.sum_cost() or 0.0)
        tokens = int(db.sum_tokens() or 0)
    except Exception:                                       # noqa: BLE001
        yuan, tokens = 0.0, 0
    stages = _stage_times(root)
    times = sorted(v["finished_at"] for v in stages.values() if v.get("finished_at"))
    return {
        "book": _book_name(root),
        "chapters": chapters,
        "han_words": words,
        "words_layer": layer,
        "stages": stages,
        "first_stage_at": times[0] if times else None,
        "last_stage_at": times[-1] if times else None,
        "mofang_yuan": round(yuan, 4),
        "mofang_tokens": tokens,
    }


def load(root: Path) -> dict:
    data = _read_json(Path(root) / REL, None)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "updated_at": None, "books": {}}
    data.setdefault("books", {})
    return data


def _save(root: Path, data: dict) -> Path:
    p = Path(root) / REL
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def upsert(root: Path, agent_yuan=None, note=None, source="auto", db=None) -> dict:
    """刷新一本书的记录。`agent_yuan=None` 表示**不覆盖**已经填过的值。"""
    root = Path(root)
    data = load(root)
    snap = snapshot(root, db=db)
    name = snap["book"]
    rec = dict(data["books"].get(name) or {})
    rec.update(snap)
    rec.setdefault("agent_yuan", None)
    rec.setdefault("agent_note", "")
    rec.setdefault("agent_reported_at", None)
    rec["last_auto_at"] = _now()
    rec["last_auto_source"] = source
    if agent_yuan is not None:
        rec["agent_yuan"] = round(float(agent_yuan), 4)
        rec["agent_reported_at"] = _now()
    if note:
        rec["agent_note"] = str(note)[:400]
    data["books"][name] = rec
    _save(root, data)
    return rec


def auto_record(root: Path, db=None) -> dict:
    """orchestrator 收尾钩子：刷新机器侧，不碰 Agent 侧。"""
    return upsert(root, db=db, source="run")


def total_yuan(rec: dict) -> float:
    return float(rec.get("mofang_yuan") or 0.0) + float(rec.get("agent_yuan") or 0.0)


def summary_line(rec: dict) -> str:
    agent = rec.get("agent_yuan")
    agent_txt = ("¥%.2f（自报）" % agent) if agent is not None else "未上报"
    return ("%s：%d 章 / %d 汉字 · 墨坊 ¥%.2f + Agent %s"
            % (rec.get("book") or "?", rec.get("chapters") or 0,
               rec.get("han_words") or 0, float(rec.get("mofang_yuan") or 0.0), agent_txt))
