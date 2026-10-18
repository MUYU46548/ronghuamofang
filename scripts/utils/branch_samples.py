# -*- coding: utf-8 -*-
"""分支样例生成（第 4 件 · 选项可视化）。

## 解决什么

用户反馈：「竞品有选项可视化，分支样例方向直观可选，显然根本没做进绒花墨坊里，
这条信息又在某次会话中丢失了。如果我这里不提就永远没了。」

与第 3 件（定向改写）的分工：那个改**章大纲的某个字段**（结构层），这个生成
**同一章的 2-3 个不同写作方向**（基调/视角/节奏），让用户在正文动笔前先选方向
——「同一件事，三种写法」，选中的那个作为该章 few-shot 锚点喂给 stage4。

## 为什么复用 field_refine 的骨架

候选生成的机械部分（单轮直调、截断防护、非 JSON 拒绝、记账）两件完全一致，
只有「改什么」不同。此处只写差异化的 prompt 构造 + 调用，其余直接复用 ——
两处各写一套的话，截断防护这类防线早晚有一处漏掉。
"""
import json

from utils import field_refine as fr
from utils.file_io import read_text
from utils.validator import parse_llm_json

BRANCH_KINDS = {
    "tone": "基调（如：克制隐忍 / 外放热烈 / 冷峻疏离）",
    "pov": "视角（如：主角内聚焦 / 全知俯瞰 / 旁观者侧写）",
    "pace": "节奏（如：密集聚焦单场景 / 舒缓铺陈多线 / 快慢交替）",
}

# 单个方向样例的长度上界：比字段候选长（要能体现基调），但不能长到挤掉设定切片。
# 用户口径：单章 5 万 token 甚至更高都可以 —— 这里 1500 字约 2-3K token，
# 三个方向合计仍在预算内。
MAX_TOKENS_PER_SAMPLE = 1500


def _build_prompt(chapter_text, kind, note):
    label = BRANCH_KINDS.get(kind, kind)
    return f"""你是一名资深小说编辑，帮作者在动笔前定这一章的写作方向。

## 章大纲（供你判断内容，不要改动）

```markdown
{chapter_text}
```

## 任务

围绕**{label}**，给 {fr.CANDIDATE_COUNT} 个**方向明显不同**的样例。

要求：
- 每个样例写本章开头 **500-1500 字**，让作者能直接比较「同一件事，不同写法」的差别。
- 三个方向要在**基调/视角/节奏**上有可感知的差异，不是同一段换几个形容词。
- 严格照章大纲的事件与角色写，别自己加情节。
- 每个样例配一句话说明「这个方向适合什么样的书」。

作者的意见（若有）：

{note or "（未给 —— 请按大纲本身最自然的三种可能来分叉）"}

## 输出格式

只输出一个 JSON 对象，不要任何其他文字、不要代码围栏：

{{"samples": [{{"label": "方向一的名字", "text": "500-1500 字样例", "why": "适合什么样的书"}}, {{"label": "方向二的名字", "text": "…", "why": "…"}}, {{"label": "方向三的名字", "text": "…", "why": "…"}}]}}
"""


def generate_branch_samples(n, kind="tone", note="", client=None, cfg=None,
                            root=".", db=None, run_id=None):
    """生成同一章的 2-3 个写作方向样例。返回 (ok, msg, payload)。

    payload = {"kind":..., "samples":[{label,text,why}], "cost_yuan":...,
               "model":..., "tokens_in":..., "tokens_out":...}
    """
    if kind not in BRANCH_KINDS:
        return False, ("未知 kind: " + str(kind)
                       + "（可选: " + ", ".join(BRANCH_KINDS) + "）"), None
    p = fr._chapter_path(n, root)
    if not p.exists():
        return False, (f"第 {n} 章大纲不存在（{fr.CHAPTERS_DIR}/{int(n):02d}.md），"
                       f"先跑 stage3"), None
    chapter_text = read_text(p)

    if client is None:
        from utils.llm_client import make_client
        if cfg is None:
            from orchestrator import load_config
            cfg = load_config()
        client = make_client(cfg, "outliner")

    prompt = _build_prompt(chapter_text, kind, note)
    from utils.llm_client import SYSTEM_PROMPT
    text, usage, model, finish = client._post_chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": prompt}],
        max_tokens=MAX_TOKENS_PER_SAMPLE * fr.CANDIDATE_COUNT)

    # 记账（单轮直调不经过 run_task，漏记会让成本页凭空少一笔）
    from utils import cost_tracker
    tin = int(usage.get("prompt_tokens") or 0)
    tout = int(usage.get("completion_tokens") or 0)
    cost = cost_tracker.estimate_cost_yuan(tin, tout, model)
    if db is not None:
        try:
            db.log_cost(run_id or "adhoc", "branch_sample", int(n), model,
                        tin, tout, cost)
        except Exception as e:                          # noqa: BLE001
            print(f"[branch_samples] 记账失败（不影响产出）：{e}")

    if finish == "length":
        return False, ("输出被 max_tokens 截断（finish_reason=length）→ 样例不完整，"
                       "已丢弃。提高该阶段 max_tokens 或换模型"), None

    data = parse_llm_json(text)
    samples = (data or {}).get("samples") if isinstance(data, dict) else None
    if not isinstance(samples, list) or not samples:
        return False, ("模型未按 JSON 格式返回样例（parse_llm_json 失败或 samples 为空）。"
                       "原文尾部: " + (text or "")[-200:]), None

    out = []
    for s in samples[:fr.CANDIDATE_COUNT]:
        if isinstance(s, dict) and (s.get("text") or "").strip():
            out.append({"label": str(s.get("label") or "").strip(),
                        "text": str(s["text"]).strip(),
                        "why": str(s.get("why") or "").strip()})
    if not out:
        return False, "样例内容全为空", None

    return True, "", {"kind": kind, "samples": out, "cost_yuan": cost,
                      "model": model, "tokens_in": tin, "tokens_out": tout}


def submit_chosen(n, kind, label, text, why="", note="", root=".",
                  source="GUI 分支样例"):
    """把选定的方向样例落成**样例类**提案（不直接写稿）。

    锚点 kind 固定为 sample —— stage4 的 chapter_samples 只挑这类注入成
    few-shot（见 utils/chapter_samples.py 的 _is_sample）。
    """
    ok, err, _ = fr.current_field(n, "title", root)
    if not ok:
        return False, err, None
    anchor = {"kind": "sample", "branch_kind": kind, "label": label,
              "target_n": int(n)}
    title = f"第{n}章 方向样例：{label or BRANCH_KINDS.get(kind, kind)}"
    rationale = note or why or f"选定写作方向（{BRANCH_KINDS.get(kind, kind)}）"
    from utils import discuss                              # 复用第 3 件的落地通道
    return discuss.submit(
        target_n=int(n), title=title, body=text.strip(), rationale=rationale,
        anchor=anchor, rounds=1, source=source, root=root)
