# -*- coding: utf-8 -*-
"""病灶词扫描（成书提炼⑦）在绒花墨坊侧的入口 —— 零 token、确定性、只检测不改写。

## 为什么单独开一层

实现本体是 `utils/book_extract/cliche_scanner.py`（移植自 SP-1024）。本模块只解决
**在本仓里怎么找到正文与词表**：

- 词表优先级：显式路径 > `config/system.yaml` 的 `quality.cliche_wordlist`
  > 内置默认 20 条（与原版一致，词表词之间不做长词优先去重）；
- 章节正文：按 `refined > checked > raw` 取第一份存在的（与编辑器「当前稿」同序）。

## 为什么不做成门禁

词表命中 ≠ 写坏：`微微一笑` 在一章里出现一次很正常。做成硬门只会误伤合格稿，
所以 `quality_checklist` 里它是**只报告**的一段；要当闸门就自己看密度（每千字）
与排行，别让机器替作者定"这句不能写"。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from utils.book_extract import cliche_scanner as _scanner
from utils.book_extract.wordlist import load_wordlist

LAYER_DIRS = ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw")


def config_wordlist_path(cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """读 `quality.cliche_wordlist`（相对项目根）；没有/读不到返回 None。"""
    try:
        if cfg is None:
            from utils.config_io import load_config_yaml
            cfg = load_config_yaml("config/system.yaml") or {}
        rel = ((cfg.get("quality") or {}).get("cliche_wordlist") or "").strip()
        if not rel:
            return None
        p = Path(rel)
        if not p.is_absolute():
            p = Path(".") / p
        return str(p) if p.exists() else None
    except Exception:                                          # noqa: BLE001
        return None


def scan_text(text: str, wordlist_path: Optional[str] = None,
              cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """扫描一段正文，返回原版口径的报告（总字数/命中/排行）。"""
    path = wordlist_path or config_wordlist_path(cfg)
    try:
        terms = load_wordlist(path)
    except (OSError, ValueError):
        terms = load_wordlist(None)      # 词表坏了回落默认：扫描本身不该因配置炸掉
    return _scanner.scan(text, wordlist=terms)


def chapter_path(n: int, root: str | Path = ".") -> Optional[Path]:
    """按 refined > checked > raw 找该章正文；都没有返回 None。"""
    base = Path(root)
    for rel in LAYER_DIRS:
        p = base / rel / ("%02d.md" % n)
        if p.exists():
            return p
    return None


def scan_chapter(n: int, root: str | Path = ".", wordlist_path: Optional[str] = None,
                 cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p = chapter_path(n, root)
    if p is None:
        raise FileNotFoundError("第 %d 章没有任何一层正文（refined/checked/raw）" % n)
    rep = scan_text(p.read_text(encoding="utf-8"), wordlist_path, cfg)
    rep["file"] = str(p)
    return rep


def top_hits(report: Dict[str, Any], limit: int = 5):
    """取排行前 N（词, 次数）；没有命中返回空列表。"""
    return list(report.get("排行") or [])[:max(0, int(limit))]


def format_report(report: Dict[str, Any]) -> str:
    return _scanner.format_report(report)
