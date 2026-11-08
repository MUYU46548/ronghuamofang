# -*- coding: utf-8 -*-
"""按钮级派单（engine: self）：GUI 上点一次“要模型”的按钮 → 任务书 → 草稿 → 你采纳才落盘。

## 为什么要这一层（2026-11-08，暮雨原话）

“精修大纲、提精修、段落重写、整章重写、运行审查……这些全是有用的东西，
如果不能点还有什么意义？难道绒花墨坊根本没搞定和 agent 的交互吗”

他说得对。`engine: self` 的第一版只做到**阶段级**：阶段 1~6 会出任务书，
外部执行方写完重跑即可。但**按钮级**（章节页的“重写”、编辑器的“让 AI 只改这一段”）
没有派单通道，只能前置拒绝 —— 表现就是“变灰、用不了”。

本模块补的就是这一层，三段式：

1. **派单**：把这一动作要什么（输入全文 / 输出路径 / 判据）写成自包含任务书；
2. **判完成**：只看盘上草稿（不是“我写完了”这句话），判据是确定性的；
3. **回写**：草稿先落在 `data/self_drafts/`，**你点“采用”才进正式路径**，并且
   先备份旧稿（不采用 = 正式稿一个字节不动）。

刻意不做“自动应用”：这是作者的书，机器只能把候选摊出来，采用与否是人的判断。
"""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from utils.file_io import read_text, write_text

TASK_DIR = "data/state/tasks/self/actions"
DRAFT_DIR = "data/self_drafts"
BACKUP_DIR = "data/chapters/rewrite_backup"


def _count_cn(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _target_words(cfg) -> tuple[int, int]:
    """读 `chapter.target_words`（唯一口径见 AGENTS：落盘必须是 [low, high]）。

    刻意**不复用** stage4 的私有函数：那是写作阶段的实现细节；
    这里只要能判草稿够不够长，读同一个配置键即可。
    """
    try:
        tw = ((cfg or {}).get("chapter") or {}).get("target_words") or []
        low, high = int(tw[0]), int(tw[1])
        if 0 < low < high:
            return low, high
    except Exception:                                       # noqa: BLE001
        pass
    return 4000, 7200


# ---------------------------------------------------------------- 任务书
def chapter_task_path(n: int) -> Path:
    return Path(TASK_DIR) / ("rewrite_ch%02d_%s.md" % (n, _stamp()))


def chapter_draft_path(n: int) -> Path:
    return Path(DRAFT_DIR) / ("chapter_%02d.md" % n)


def write_chapter_task(cfg, proj, n: int, feedback: str = "", kind: str = "rewrite") -> dict:
    """整章重写 / 按意见精修：出任务书。返回 {task, draft, criteria, n, kind}。

    `kind`：
      · `rewrite` = 重写这一章（保留事实，重写写法与节奏）；
      · `refine`  = 按作者的意见精修（**不是推翻重写**，只改意见点到的地方）。
    两者共用同一份草稿路径与采纳机制 —— 差别只在任务书怎么写、要不要意见。

    输入是**现有正文全文**（外部执行方不一定能读我们的文件系统，所以内联），
    输出路径固定为草稿目录 —— 不直接写 `data/chapters/raw/`，
    否则就成了“外部未经作者确认改正文”，与本项目红线冲突。
    """
    if kind == "refine" and not str(feedback or "").strip():
        raise ValueError("按意见精修必须写意见（否则跟“整章重写”没区别）")
    raw = Path("data/chapters/raw") / ("%02d.md" % n)
    if not raw.is_file():
        raise FileNotFoundError("第 %d 章还没有正文（%s 不存在）—— 先用阶段 4 写出来，或换一章"
                                % (n, raw.as_posix()))
    current = read_text(raw)
    low, high = _target_words(cfg)
    outline_rel = Path("data/outline/chapters") / ("%02d.md" % n)
    outline = read_text(outline_rel) if outline_rel.is_file() else ""
    draft = chapter_draft_path(n)
    task = chapter_task_path(n)

    body = "\n".join([
        "# 整章重写（第 %d 章）" % n,
        "",
        "## 干什么",
        "",
        ("把下面这一章的正文**按意见精修**：只改意见点到的地方，别整章推翻重来。"
         if kind == "refine" else
         "把下面这一章的正文**重写一遍**：保留事实与人物关系，重写的是写法与节奏。"),
        ("作者的意见（**必须逐条落实**）：%s" % feedback.strip()
         if kind == "refine" else
         "意见（如果作者写了）：%s" % (feedback.strip() or "（没写具体意见，按大纲与现有正文自己判断）")),
        "",
        "## 写到哪里（只写这一个文件）",
        "",
        "- 目标：`%s`" % draft.as_posix(),
        "- **不要**直接改 `data/chapters/raw/%02d.md`；正式稿由作者点“采用草稿”才换。" % n,
        "- 文末保留 `<!-- summary: ... -->` 与 `<!-- quality: X/10 -->` 两个注释。",
        "",
        "## 判据（写完会按这几条自动核对）",
        "",
        "1. 有标题行（`## 第%d章 …`）；" % n,
        "2. 正文汉字数在 **%d~%d** 之间（只数汉字，标点数字不算）；" % (low, high),
        "3. 不含占位符（如“待补”“TBD”“……略”）；",
        "4. 不是把原稿原样复制（重复率过高会判没写）。",
        "",
        "## 现有正文（要重写的就是这份）",
        "",
        "````markdown",
        current.rstrip(),
        "````",
        "",
        "## 本章大纲（重写要贴合它）",
        "",
        (outline.rstrip() or "（这一章的大纲不在滚动窗口里，按现有正文与上下章衔接判断）"),
        "",
    ])
    write_text(task, body)
    return {"n": n, "kind": kind, "task": task.as_posix(), "draft": draft.as_posix(),
            "criteria": {"words": [low, high]}}


# ---------------------------------------------------------------- 判完成 / 列草稿
def check_chapter_draft(n: int, cfg=None) -> dict:
    """按确定性判据核草稿。返回 {ok, reasons[], words, path}。"""
    draft = chapter_draft_path(n)
    out = {"n": n, "path": draft.as_posix(), "exists": draft.is_file(),
           "ok": False, "reasons": [], "words": 0}
    if not draft.is_file():
        out["reasons"].append("草稿还没生成：" + draft.as_posix())
        return out
    text = read_text(draft)
    out["words"] = _count_cn(text)
    low, high = _target_words(cfg or {})
    if not re.search(r"^##\s*第\s*%d\s*章" % n, text, re.M):
        out["reasons"].append("缺标题行（应为 `## 第%d章 …`）" % n)
    if not (low <= out["words"] <= high):
        out["reasons"].append("汉字数 %d 不在 %d~%d 之间" % (out["words"], low, high))
    for bad in ("待补", "TBD", "……略", "（略）"):
        if bad in text:
            out["reasons"].append("含占位符：" + bad)
    raw = Path("data/chapters/raw") / ("%02d.md" % n)
    if raw.is_file():
        old = re.sub(r"\s+", "", read_text(raw))
        new = re.sub(r"\s+", "", text)
        if old and new and old[:200] and old[:200] in new[:400]:
            out["reasons"].append("与现稿开头高度重复（像是没真重写）")
    out["ok"] = not out["reasons"]
    return out


def list_drafts(cfg=None) -> list[dict]:
    root = Path(DRAFT_DIR)
    if not root.is_dir():
        return []
    items = []
    for p in sorted(root.glob("chapter_*.md")):
        m = re.match(r"chapter_(\d+)\.md$", p.name)
        if not m:
            continue
        n = int(m.group(1))
        info = check_chapter_draft(n, cfg)
        info["mtime"] = time.strftime("%Y-%m-%d %H:%M",
                                      time.localtime(p.stat().st_mtime))
        items.append(info)
    return items


# ---------------------------------------------------------------- 采用 / 放弃
def apply_chapter_draft(n: int) -> dict:
    """把草稿换进正式稿：先备份旧稿，再整份替换。返回 {backup, from, to, words}。"""
    draft = chapter_draft_path(n)
    if not draft.is_file():
        raise FileNotFoundError("没有第 %d 章的草稿（%s）" % (n, draft.as_posix()))
    raw = Path("data/chapters/raw") / ("%02d.md" % n)
    backup = None
    if raw.is_file():
        Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        backup = Path(BACKUP_DIR) / ("%s_%02d.md" % (_stamp(), n))
        shutil.copy2(raw, backup)
    text = read_text(draft)
    write_text(raw, text)
    return {"n": n, "from": draft.as_posix(), "to": raw.as_posix(),
            "backup": backup.as_posix() if backup else "",
            "words": _count_cn(text)}


def drop_chapter_draft(n: int) -> dict:
    """放弃草稿（正式稿一个字不动）。"""
    draft = chapter_draft_path(n)
    if not draft.is_file():
        return {"n": n, "removed": False, "message": "本来就没有第 %d 章的草稿" % n}
    draft.unlink()
    return {"n": n, "removed": True, "message": "已放弃第 %d 章的草稿（正式稿未动）" % n}
