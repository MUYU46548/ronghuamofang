---
name: nf-project
description: "绒花墨坊·项目级任务接线卡：子任务派发、快照回退、补救诊断、跨阶段项目管理。外部 Agent 按本卡编排项目级任务。触发词：nf-project、墨坊项目、派发任务、快照回退、补救建议、项目管理。**破坏性操作一律先预览再执行，快照回退必须作者确认。**"
version: 1.0.0
metadata:
  hermes:
    tags: [novelforge, 绒花墨坊, phase-skill, project, snapshot, dispatch]
    category: worldbuilding
---

# nf-project：项目级任务

> 阶段 skill 是**编排卡**：项目级操作多为破坏性（回退/覆盖），纪律是
> **先预览、后执行、执行要作者确认**。工具名以 `nf_mcp.MCP_TOOLS` 为准。

## 覆盖范围

- 子任务派发：`nf_dispatch_task` / `nf_get_agent_run`
- 快照与回退：`nf_restore_snapshot`
- 补救诊断：`nf_get_remedy`
- 全景：`nf_get_state`

## 标准动线

### 快照回退（破坏性，默认预览）
1. `nf_restore_snapshot`（不传 confirm）—— **只预览**：列出将覆盖/补齐哪些产物。
2. 把预览结果原样给作者看，说明影响面（哪些文件会被覆盖）。
3. 作者确认后 `nf_restore_snapshot(confirm=true)`。
4. ⚠️ 回退前系统会自动打 `pre_restore` 快照（折返点）——告诉作者这条保底。

### 补救诊断
1. `nf_get_remedy` —— 一键体检：一致性 / 产物完整性 / 孤儿文件 / 建议命令。
2. 把建议按可执行性分组报给作者：能自动修的（列出命令）/ 需要作者决策的。

### 子任务派发
1. `nf_dispatch_task` —— 生成任务书并拉起子会话（异步）。返回 run_id。
2. `nf_get_agent_run` —— 轮询状态（间隔 ≥10s）。done 后读结果摘要。
3. 派发 ≠ 免检：子会话的产物**照常走阶段判据**，不因为它跑过就放行。

## 判据（跑完怎么算完）

- 快照回退：预览与执行是**两次调用**，中间有作者确认（不是一把梭）。
- 补救建议：每条都有对应命令或明确的"需作者决策"标注。
- 子任务：run 终态是 done 且产物落盘可验证（不只信 run 的自述）。

## 什么时候必须停下来问作者

- `nf_restore_snapshot` 的 confirm=true（覆盖书稿数据）。
- `delete_extra=true`（会删"快照里没有"的文件 —— 默认 false，别轻易开）。
- 任何"回退到某个历史版本"的选择（选哪个版本是作者的记忆，不是 Agent 的）。

## 红线

- 不跳过预览直接 confirm=true。
- 不用 delete_extra=true 除非作者明确知道会删什么。
- 不在项目数据目录做删除/覆盖以外的"顺手整理"（搬文件、改名都算破坏性）。
- `--root`/多书档场景：先确认操作的是哪本书（回退错书档是不可逆事故）。
