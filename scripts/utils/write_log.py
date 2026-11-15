# -*- coding: utf-8 -*-
"""写作账本：每一处正文改动留一条“什么时候、因为什么改的”（2026-11-15）。

## 为什么要有它（P2-2“一任务一工作区”的诚实落地）

正文在 `data/chapters/`，`data/` 不进 Git —— Papilot 那种“一任务一个 git worktree”
在这里不适用。但**“一任务一个可追溯单元”**这件事本身是需要的：审稿时最常问的是
“这一段怎么变成这样了”，而答案散在三处（段落历史记内容、候选区记待采用、快照记整章），
**没有一份按章按段按时间的流水**。于是同一个问题每轮都要靠翻文件回答。

这份账本只做一件事：正文每被写过一次，追加一行 —— 谁（哪条通道）、哪一章哪一段、
什么时候、字数怎么变、来源是什么（手改 / 采用候选 / 回退 / 段落重写）。

## 纪律

- **只追加**，不改历史；落 `data/state/write_log.jsonl`（本机，不进 Git）。
- **不存正文**：只存长度与来源。正文的真源只有 `data/chapters/` 一份，
  账本再存一份就变成两份真源（迟早对不上）。
- 上限 `MAX_ENTRIES` 条，超了丢最旧的 —— 它是线索，不是归档。
- 读失败一律当“没有账本”，不许因为记账坏了挡住写正文。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

LOG_REL = Path("data") / "state" / "write_log.jsonl"
MAX_ENTRIES = 2000


def _path(root=None) -> Path:
    base = Path(root) if root else Path(".")
    return base / LOG_REL


def record(chapter: int, paragraph: int | None, note: str, before_chars: int,
           after_chars: int, root=None, kind: str = "paragraph") -> dict:
    """追加一条改动记录。返回写进去的那条（记账失败也返回，只是没落盘）。"""
    entry = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chapter": int(chapter),
        "paragraph": (int(paragraph) if paragraph is not None else None),
        "kind": str(kind or "paragraph"),
        "note": str(note or ""),
        "before_chars": int(before_chars or 0),
        "after_chars": int(after_chars or 0),
    }
    try:
        p = _path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim(p)
    except OSError:
        pass                      # 记账失败不挡写正文
    return entry


def _trim(path: Path) -> None:
    """超上限就重写成“最新的 MAX_ENTRIES 条”。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_ENTRIES:
        return
    keep = [ln for ln in lines[-MAX_ENTRIES:] if ln.strip()]
    path.write_text("\n".join(keep) + "\n", encoding="utf-8")


def recent(chapter: int | None = None, limit: int = 50, root=None) -> list[dict]:
    """读最近的记录（新 → 旧）。给章号就只留那一章。"""
    try:
        raw = _path(root).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in reversed(raw):
        if not ln.strip():
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue                # 半行/坏行跳过，不连坐整份账本
        if chapter is not None and int(e.get("chapter") or 0) != int(chapter):
            continue
        out.append(e)
        if len(out) >= max(1, int(limit)):
            break
    return out
