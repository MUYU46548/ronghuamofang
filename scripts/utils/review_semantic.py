# -*- coding: utf-8 -*-
"""语义审稿（④ 编辑式讨论，2026-11-03）。

## 为什么是"窄口"

作者 11-01 的原话：**"逻辑检查用脚本 = 压根没有逻辑检查"**。但把整个 stage5 换成
LLM 判据，要先回答"什么算好"——那是一整套标准，不是一次改动能定的。

所以先只做**一件能验收的事**：让模型对**两件具体的事**（情绪曲线 / 视角漂移）
写一份讨论稿，落在 `data/discuss/NN/审稿-<焦点>.md` 里。

产物是**讨论**，不是分数：没有"8 分"这种没法反驳的东西。作者看过、定稿后，
结论会照常进写作提示词（与其它讨论同一条通路）。

本模块只做**确定性**部分：读正文 → 开讨论 → 生成任务书。不调模型、零 token。
"""
from __future__ import annotations

from pathlib import Path

FOCUS = ("情绪曲线", "视角漂移")


def _chapter_text(chapter, root="."):
    for layer in ("refined", "checked", "raw"):
        p = Path(root) / "data" / "chapters" / layer / ("%02d.md" % int(chapter))
        if p.exists():
            from utils.file_io import read_text
            return p, read_text(p)
    return None, ""


def build_review(chapter, focus=None, root="."):
    """开一个审稿讨论 + 写任务书。返回 {ok, thread, task, note}。零 token。"""
    from utils import discussion

    chapter = int(chapter)
    src, text = _chapter_text(chapter, root)
    if src is None:
        return {"ok": False, "error": "第 %d 章还没有正文（raw/checked/refined 都没有）" % chapter}
    topic = "审稿-%s" % (focus or FOCUS[0])
    md, _js = discussion.open_thread(
        chapter, topic,
        "这一章在“%s”上有没有问题？哪里开始不对、怎么改？" % (focus or FOCUS[0]),
        root=root)

    task_dir = Path(root) / "data" / "state" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    task = task_dir / ("stage5_semantic_ch%02d.md" % chapter)
    body = "\n".join([
        "# 语义审稿：第 %d 章 · %s" % (chapter, focus or FOCUS[0]),
        "",
        "你是一位编辑，只谈“%s”，不要泛泛夸或贬。" % (focus or FOCUS[0]),
        "",
        "## 正文（来自 %s）" % src.as_posix(),
        "",
        text.strip(),
        "",
        "## 怎么交作业",
        "",
        "1. 用 MCP 工具 `nf_discuss_say`（chapter=%d, topic=%s, role=ai）写一轮；" % (chapter, topic),
        "   里面必须是**可执行的改法**（改哪一段、改成什么走向），不要写分数。",
        "2. 至少说清三件事：哪里开始不对 / 读者会怎么理解 / 具体怎么改。",
        "3. 定稿是作者的事，你没有 `nf_discuss_decide`。",
        "",
    ])
    from utils.file_io import write_text
    write_text(task, body)
    return {"ok": True, "chapter": chapter, "topic": topic,
            "thread": md.as_posix(), "task": task.as_posix(),
            "source": src.as_posix(),
            "note": "任务书已生成（零 token）。跑完把发言用 /discuss/say 追加进同一个讨论。"}


def list_pending(root="."):
    """还没定稿的审稿讨论（供界面/Agent 看"审了没"）。"""
    from utils import discussion
    return [s for s in discussion.open_threads(root=root)
            if str(s.get("topic") or "").startswith("审稿-")]
