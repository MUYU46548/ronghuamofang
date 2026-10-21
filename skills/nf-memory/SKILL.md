---
name: nf-memory
description: "绒花墨坊·记忆与设定阶段接线卡：canon 快照、设定冲突裁决、素材状态、成本账本、状态总览。外部 Agent 按本卡查询/整理记忆类信息，拍板留给作者。触发词：nf-memory、墨坊设定、canon、设定冲突、素材裁决、成本账本。"
version: 1.0.0
metadata:
  hermes:
    tags: [novelforge, 绒花墨坊, phase-skill, memory, setting, canon]
    category: worldbuilding
---

# nf-memory：记忆与设定

> 阶段 skill 是**编排卡**：整理与查询是 Agent 的活，**拍板是作者的活**。
> 工具名以 `nf_mcp.MCP_TOOLS` 为准。

## 覆盖范围

- canon 快照（已定稿设定集）：`nf_get_canon`
- 设定冲突裁决：`nf_get_setting_conflicts` + `nf_set_material_status`
- 全景与成本：`nf_get_state` / `nf_get_costs`

## 标准动线

### 查询（随时可做，零 token）
1. `nf_get_state` —— 一屏看懂：什么阶段、卡在哪、预算还剩多少。
2. `nf_get_costs` —— 成本按阶段/模型聚合。**给作者报成本时用它**，不要自己估。
3. `nf_get_canon`（full=false）—— canon 元信息（冻结时间 + 条目数）。
   需要全文再传 full=true（可能几万字符，别默认拉全量）。

### 冲突裁决整理（Agent 整理，作者拍板）
1. `nf_get_setting_conflicts` —— 拿「机器裁不了」的分歧清单。
   ⚠️ `checked=false` 表示**根本没查**（设定集缺失），空清单 ≠ 没冲突 ——
   报给作者时必须区分这两种情况。
2. 把分歧整理成决策清单给作者：每条附**双方出处 + 你的建议**（可以给建议，
   不代拍）。
3. 作者拍板后 `nf_set_material_status`（name + action + reason）。
   reject **必须有 reason**（墓碑靠它才有意义）。

## 判据（跑完怎么算完）

- 冲突清单每条都有出处（无出处的分歧不要报，先补查）。
- 作者的每个决定都带 reason 落了盘（`nf_get_setting_conflicts` 回读可见）。
- 报成本时数字来自 `nf_get_costs`，不掺估算。

## 什么时候必须停下来问作者

- **任何** `nf_set_material_status` 的拍板动作（adopt/reject/uncertain 都是作者的决定）。
- canon 已冻结但作者要改设定 —— 说明影响面（已写章节可能对不上）。
- 成本账本出现作者没授权过的消耗 —— 报异常，不要自行解释掉。

## 红线

- 不在 `checked=false` 时宣称"没有冲突"。
- 不批量 adopt/reject（逐条过，每条都是独立决定）。
- 不把设定全文默认塞进上下文（先 full=false 拿元信息）。
