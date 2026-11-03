# -*- coding: utf-8 -*-
"""改写工作室（C 组三个"抄催更姬"的活，2026-11-03）。

三个模式，**全部只产任务书 + 讨论**，不调模型（零 token）：

| mode | 对标 | 干什么 |
|---|---|---|
| `sensory` | Sudowrite Describe | 把一句概括扩成感官场景（看到/听到/闻到/手上触感） |
| `dehumanize` | 笔灵/蛙蛙 去 AI 腔 | 按检测出的 AI 腔改一遍（同质化/套话/解释性旁白） |
| `reader` | 阅文 读者视角 | 站读者位置找"弃书点"（哪里想跳过、哪里不信） |

产物落在讨论（`data/discuss/NN/改写-<模式>.md`），作者看过、定稿后，
结论照常进写作提示词。**不产分数** —— 打分的提示词会教模型讨好分数。

为什么不做成"一键改写"：改写是创作决策（改哪一段、改成什么），
自动改了没人看过就落进正文，等于把作者的笔拿走。这里给的是"可讨论的稿子"。
"""
from __future__ import annotations

from pathlib import Path

MODES = {
    "sensory": {
        "label": "感官扩写",
        "ask": "把下列概括写成可感知的场景：写出看到/听到/闻到/手上的触感，"
               "不要形容词堆砌，用一个具体物件锚住这个场景。",
        "bad": "只报告感受、不给具体画面",
    },
    "dehumanize": {
        "label": "去 AI 腔",
        "ask": "找出这段里像 AI 写的句子：解释性旁白（“她感到……”“这说明……”）、"
               "对称排比、万能形容词、每段都收在一个金句上。逐句给出改写版本。",
        "bad": "只批评不重写",
    },
    "reader": {
        "label": "读者视角",
        "ask": "你是一个随时会弃书的读者：从开头往下读，指出“我可能会在这里退出”的"
               "具体位置和原因（信息重复 / 没有新问题 / 人物动机看不出 / 节奏停住）。",
        "bad": "笼统说“不够吸引人”",
    },
}


def _chapter_text(chapter, root="."):
    for layer in ("refined", "checked", "raw"):
        p = Path(root) / "data" / "chapters" / layer / ("%02d.md" % int(chapter))
        if p.exists():
            from utils.file_io import read_text
            return p, read_text(p)
    return None, ""


def build(mode, chapter, excerpt="", root="."):
    """开一个改写讨论 + 写任务书。`excerpt` 给了就只讨论这一段。返回 dict。"""
    from utils import discussion

    mode = str(mode or "").strip()
    if mode not in MODES:
        return {"ok": False, "error": "mode 只能是 %s" % "、".join(MODES)}
    spec = MODES[mode]
    chapter = int(chapter)
    src, text = _chapter_text(chapter, root)
    if src is None:
        return {"ok": False, "error": "第 %d 章还没有正文（raw/checked/refined 都没有）" % chapter}
    body_text = (excerpt or "").strip() or text.strip()
    topic = "改写-%s" % spec["label"]
    md, _js = discussion.open_thread(chapter, topic, spec["ask"], root=root)

    task_dir = Path(root) / "data" / "state" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    task = task_dir / ("studio_%s_ch%02d.md" % (mode, chapter))
    parts = [
        "# 改写工作室：第 %d 章 · %s" % (chapter, spec["label"]),
        "",
        "## 要求",
        "",
        spec["ask"],
        "",
        "**不要做的事**：%s。" % spec["bad"],
        "",
        "## 材料%s" % ("（只讨论这一段）" if excerpt else "（本章正文，来自 %s）" % src.as_posix()),
        "",
        body_text,
        "",
        "## 怎么交作业",
        "",
        "用 MCP 工具 `nf_discuss_say`（chapter=%d, topic=%s, role=ai）写一轮，"
        "给出**可直接采用**的文字（改哪段、改成什么），不要只给建议。" % (chapter, topic),
        "定稿归作者：你没有 `nf_discuss_decide`。",
        "",
    ]
    from utils.file_io import write_text
    write_text(task, "\n".join(parts))
    return {"ok": True, "mode": mode, "label": spec["label"], "chapter": chapter,
            "topic": topic, "thread": md.as_posix(), "task": task.as_posix(),
            "source": src.as_posix(), "excerpt": bool(excerpt)}


def list_pending(root="."):
    from utils import discussion
    return [s for s in discussion.open_threads(root=root)
            if str(s.get("topic") or "").startswith("改写-")]
