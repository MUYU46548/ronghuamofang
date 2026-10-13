# -*- coding: utf-8 -*-
"""自由模式（工序勾选）的纯判据 —— 解析 / 排序 / 互斥 / 执行前报缺。

## 为什么独立成纯函数模块

问题卡活一的三条改动（多选解析、与旧参数互斥、执行前拦人）都是**确定性判断**，
不需要 config、不需要 progress、更不需要 LLM。抽出来单独成模块的原因有二：

1. `orchestrator.py` 顶层 `import stage1..stage8`，import 它就等于拉起整条链
   （含 python-docx / llm_client），单测想验"3,5,7 解析成 [3,5,7]"不该付这个价；
2. 判据只写一份 —— CLI 与（后续的）GUI/HTTP 入口共用同一套，不会两边各判各的。

## 语义边界（照问题卡，别自作主张扩）

- 多选**只改"执行哪些阶段"这个来源**：已完成跳过、审批门、断点续跑、预算熔断、
  规划四态全部走 orchestrator 的现有路径，本模块不碰它们。
- 报缺拦的是**上游输入**，不是"该阶段自己的产物"（后者是 skip 用的
  `missing_artifacts`，两者方向相反，别混用）。
- 报缺**只在多选模式下启用**。`--from N` / `--stage N` 保持原行为：它们是
  "续跑/重跑"语义，历史用法里上游可能本来就还没跑，加拦会打破既有流程与测试。
"""
import re

from utils.stage_artifacts import STAGE_INPUTS, STAGE_NAMES, missing_inputs

VALID_STAGES = tuple(range(1, 9))
_SEP = re.compile(r"[,，、;\s]+")


def parse_stages(raw):
    """把 `"3,5,7"` 解析成升序去重的 `[3, 5, 7]`；`None` 返回 `None`。

    报错一律 `ValueError`，文案是给人看的一句话（含正确写法），不吐栈。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        raise ValueError("多选列表是空的。写法：--stages 3,5,7（阶段号 1-8，逗号分隔）")
    out = []
    for tok in (t for t in _SEP.split(text) if t):
        try:
            v = int(tok)
        except ValueError:
            raise ValueError(
                f"「{tok}」不是阶段号。阶段号是 1-8 的整数，用逗号分隔，"
                f"例如：--stages 3,5,7") from None
        if v not in VALID_STAGES:
            names = "、".join(f"{n}{STAGE_NAMES.get(n, '')}" for n in VALID_STAGES)
            raise ValueError(
                f"阶段号 {v} 超出范围。可用阶段号：1-8（{names}）")
        if v not in out:
            out.append(v)
    if not out:
        raise ValueError("多选列表是空的。写法：--stages 3,5,7")
    return sorted(out)


def mutual_exclusion_error(stages, from_given=False, only_stage=None):
    """多选与旧参数同时给 → 返回人话报错串；不冲突返回 None（**不猜意图**）。"""
    if not stages:
        return None
    if only_stage is not None:
        return ("多选（--stages）与单跑某一件（--stage）二选一，不猜你想跑哪个。"
                "只跑一件就用 --stage N；要按自己挑的顺序跑多件就用 --stages，"
                "并去掉 --stage。")
    if from_given:
        return ("多选（--stages）与「从第 N 件跑到底」（--from）二选一，不猜你想跑哪个。"
                "跑到底就用 --from N（可去掉 --stages）；只跑自己挑的几件就用 "
                "--stages 3,5,7（可去掉 --from）。")
    return None


def stage_order(stages=None, only_stage=None, from_stage=1):
    """三选一得出**本次要按序执行的阶段列表**（不判完成/审批，那是 orchestrator 的活）。"""
    if stages:
        return sorted(set(stages))
    if only_stage:
        return [only_stage]
    return list(range(int(from_stage or 1), 9))


def _producer_note(problem):
    p = problem["produced_by"]
    return f"阶段{p}（{STAGE_NAMES.get(p, '?')}）"


def preflight_lines(stages, root, will_produce=None):
    """执行前统一报缺 —— **一次报全**，每条带三要素：缺什么 / 哪件产的 / 建议先跑哪件。

    `will_produce` 透传给 `missing_inputs`（本次同批会执行的阶段），
    由它决定"磁盘没有但这次会产出"算不算缺。
    """
    lines = []
    for n in stages:
        name = STAGE_NAMES.get(n, "?")
        probs = missing_inputs(n, root, will_produce=will_produce)
        if not probs:
            continue
        for p in (x for x in probs if x["kind"] == "all"):
            lines.append(
                f"阶段{n}（{name}）缺 {p['path']} —— 由{_producer_note(p)}产出；"
                f"建议先跑阶段{p['produced_by']}，或对它用「跳过（带理由）」放行后续")
        any_probs = [x for x in probs if x["kind"] == "any"]
        if any_probs:
            paths = "、".join(dict.fromkeys(p["path"] for p in any_probs))
            producers = sorted({p["produced_by"] for p in any_probs})
            first = producers[0]
            lines.append(
                f"阶段{n}（{name}）缺可用章节：{paths}（三层任一可用即可）都没有 —— "
                f"由阶段{'/'.join(map(str, producers))}产出；建议先跑阶段{first}"
                f"（{STAGE_NAMES.get(first, '?')}），或用 "
                f"python scripts/import_docx.py 把现成稿件放进其中一层")
    return lines


def upstream_stages(stages):
    """所选阶段涉及的上游阶段号（去重升序），仅用于提示语。"""
    out = set()
    for n in stages:
        spec = STAGE_INPUTS.get(n) or {}
        for producer, _rel in (spec.get("all") or []) + (spec.get("any") or []):
            out.add(producer)
    return sorted(out)
