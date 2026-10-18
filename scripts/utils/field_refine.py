# -*- coding: utf-8 -*-
"""定向改写的候选生成（第 3 件 · C 兜底，2026-10-18）。

## 它解决什么

Hermes 不在场时，讨论入口不能断。本模块提供 GUI 内的**单轮**精修：用户点某个
章大纲字段 → 写一句意见 → 后端一次调用产出 2-3 个候选改写 → 用户在卡片里挑一个
（或就地改）→ 走落地协议进待审队列 → 批准才写回。

与 Hermes 讨论（简报投递）的关系：那是**多轮**、有工具、有记忆的重通道；这里是
**单轮**、无工具的轻兜底。两者的产物走同一条落地协议，用户在同一个队列里审批。

## 粒度：只改一个字段，不做整章重写（2026-10-18 用户裁定）

「整章重写意义不大」—— 那等于 `refine_outline` 的章节版，改动面大、diff 难看、
批准风险高。定向改写一次只碰一个字段（叙事要点 / 核心事件 / 涉及角色 / 功能 /
承接 / 钩子 / 标题），diff 清晰，用户一眼能判断要不要。

## 记账

单轮直调 `_post_chat` 不经过 `run_task`，成本不会自动落库 —— 这里显式
`estimate_cost_yuan` + `RunDB.log_cost`，否则 GUI 成本页会漏掉这笔（静默漏账
比多花钱更难查）。
"""
import json
from datetime import datetime
from pathlib import Path

from utils.chapter_blueprint import parse_chapter_md, render_chapter_md
from utils.file_io import read_text, write_text
from utils.validator import parse_llm_json

CHAPTERS_DIR = "data/outline/chapters"

# 允许定向改写的字段。opt_point 走追加协议（不替换），故单列。
FIELD_LABELS = {
    "title": "章节标题",
    "events": "核心事件",
    "roles": "涉及角色",
    "function": "章节功能（铺垫/推进/转折/高潮/收束/过渡）",
    "carryover": "承接（与上一章的衔接）",
    "hook": "钩子（抛给下一章的悬念）",
}
LIST_FIELDS = {"events", "roles"}

CANDIDATE_COUNT = 3          # 一次几个候选（用户口径：2-3 个够挑）
MAX_TOKENS_PER_CANDIDATE = 900


def _chapter_path(n, root="."):
    return Path(root) / CHAPTERS_DIR / f"{int(n):02d}.md"


def current_field(n, field, root="."):
    """读某章某字段的当前值。返回 (ok, msg, value)。"""
    p = _chapter_path(n, root)
    if not p.exists():
        return False, f"第 {n} 章大纲不存在（{CHAPTERS_DIR}/{int(n):02d}.md）", None
    if field not in FIELD_LABELS:
        return False, "未知字段: " + str(field) + "（可选: " + ", ".join(FIELD_LABELS) + "）", None
    d = parse_chapter_md(read_text(p))
    v = d.get(field)
    if isinstance(v, list):
        v = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(v))
    return True, "", (v or "")


def _build_prompt(chapter_text, field, value, note):
    """构造单轮候选生成提示。

    刻意要求「只输出 JSON」：思考模型会把正文塞进 reasoning_content，
    所以提示里明确「先给结论再解释」，并在解析侧用 parse_llm_json 容错。
    """
    label = FIELD_LABELS[field]
    kind = "（列表字段：每行一条，行首保留序号）" if field in LIST_FIELDS else "（单段文本）"
    return f"""你是一名资深小说编辑，帮作者改一章的大纲。

## 章大纲全文（供你判断上下文，不要改动其他部分）

```markdown
{chapter_text}
```

## 任务

只重写其中一个字段：**{label}**{kind}

该字段当前值：

```
{value or "（空）"}
```

作者的意见：

{note or "（作者未给具体意见 —— 请让这个字段更具体、更有画面感，避免空泛口号）"}

## 要求

- 只改这一个字段，别碰大纲其他部分，别增删字段。
- 给 {CANDIDATE_COUNT} 个**方向不同**的候选，不要同一个意思换说法。
- 保持与大纲其余部分一致的语气与信息密度。
- 列表字段的候选，每条一行、行首保留序号。
- 不要解释你的改动，直接给结果。

## 输出格式

只输出一个 JSON 对象，不要任何其他文字、不要代码围栏：

{{"candidates": [{{"text": "候选一内容", "why": "一句话说明这个方向的取舍"}}, {{"text": "候选二内容", "why": "…"}}, {{"text": "候选三内容", "why": "…"}}]}}
"""


def generate_candidates(n, field, note="", client=None, cfg=None, root=".",
                        db=None, run_id=None):
    """生成候选改写。返回 (ok, msg, payload)。

    payload = {"field":..., "candidates":[{text,why}], "cost_yuan":...,
               "model":..., "tokens_in":...,"tokens_out":...}
    """
    ok, err, _ = current_field(n, field, root)
    if not ok:
        return False, err, None
    p = _chapter_path(n, root)
    chapter_text = read_text(p)
    d = parse_chapter_md(chapter_text)
    cur = d.get(field)
    if isinstance(cur, list):
        cur = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(cur))

    if client is None:
        from utils.llm_client import make_client
        if cfg is None:
            from orchestrator import load_config
            cfg = load_config()
        client = make_client(cfg, "outliner")

    prompt = _build_prompt(chapter_text, field, cur, note)
    from utils.llm_client import SYSTEM_PROMPT
    text, usage, model, finish = client._post_chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS_PER_CANDIDATE * CANDIDATE_COUNT)

    # 记账（单轮直调不经过 run_task，漏记会让成本页凭空少一笔）
    from utils import cost_tracker
    tin = int(usage.get("prompt_tokens") or 0)
    tout = int(usage.get("completion_tokens") or 0)
    cost = cost_tracker.estimate_cost_yuan(tin, tout, model)
    if db is not None:
        try:
            db.log_cost(run_id or "adhoc", "field_refine", int(n), model,
                        tin, tout, cost)
        except Exception as e:                          # noqa: BLE001
            print(f"[field_refine] 记账失败（不影响产出）：{e}")

    if finish == "length":
        return False, ("输出被 max_tokens 截断（finish_reason=length）→ 候选不完整，"
                       "已丢弃。提高该阶段 max_tokens 或换模型"), None

    data = parse_llm_json(text)
    cands = (data or {}).get("candidates") if isinstance(data, dict) else None
    if not isinstance(cands, list) or not cands:
        return False, ("模型未按 JSON 格式返回候选（parse_llm_json 失败或 candidates 为空）。"
                       "原文尾部: " + (text or "")[-200:]), None

    out = []
    for c in cands[:CANDIDATE_COUNT]:
        if isinstance(c, dict) and (c.get("text") or "").strip():
            out.append({"text": str(c["text"]).strip(),
                        "why": str(c.get("why") or "").strip()})
    if not out:
        return False, "候选内容全为空", None

    return True, "", {"field": field, "candidates": out, "cost_yuan": cost,
                      "model": model, "tokens_in": tin, "tokens_out": tout}


def submit_chosen(n, field, text, why="", note="", root=".", source="GUI 单轮精修"):
    """把选定的候选落成提案（**不直接写章大纲**）。

    字段类改动落 `blueprint_patch` 类型提案；批准时才真的替换字段。
    """
    from utils import discuss
    ok, err, cur = current_field(n, field, root)
    if not ok:
        return False, err, None
    anchor = {"kind": "blueprint_patch", "field": field,
              "old_value": (cur or "")[:2000], "target_n": int(n)}
    rationale = note or why or f"定向改写 {FIELD_LABELS.get(field, field)}"
    title = f"第{n}章 {FIELD_LABELS.get(field, field)} 改写"
    return discuss.submit(target_n=int(n), title=title, body=text.strip(),
                          rationale=rationale, anchor=anchor, rounds=1,
                          source=source, root=root)
