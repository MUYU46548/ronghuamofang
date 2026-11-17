---
name: nf-revise
description: "绒花墨坊·修订阶段（stage 7-8 + 精修回路）接线卡：Word 成品、Markdown 导出、大纲/章节/段落级精修、沙盒审核。外部 Agent 按本卡编排修订类任务。触发词：nf-revise、墨坊修订、导出成品、段落精修、大纲精修。**编排不代跑**：精修意见必须来自作者。"
version: 1.0.0
metadata:
  hermes:
    tags: [novelforge, 绒花墨坊, phase-skill, revise, export, refine]
    category: worldbuilding
---

# nf-revise：修订与交付（stage 7-8 + 精修回路）

> 阶段 skill 是**编排卡**：一次一件事，精修意见必须来自作者。
> 工具名以 `nf_mcp.MCP_TOOLS` 为准。

## 覆盖范围

- **交付**：stage 7（Word 成品）/ stage 8（Markdown 分卷导出）
- **精修回路**：大纲（`nf_refine_outline`）· 章节（`nf_refine_chapter`）·
  段落（`nf_refine_paragraph` / `nf_paragraph_restore` / `nf_get_paragraph_history`）
- **沙盒审核**：`nf_get_sandbox_queue` / `nf_sandbox_review`

## 标准动线

### 交付
1. `nf_get_state` —— 确认 stage 1-6 全部 done 且润色稿已审批。缺一环先回前序阶段。
2. `nf_run_stage`（stage=7 出 Word / 之后 `nf_export_markdown` 出分卷 Markdown）。
3. 成品路径见 `GET /state` 的产物段（`output/{书名}_完整版.docx`）。

### 改之前先读、先自查
1. `nf_get_chapter` —— 读这一章正文（默认取最高存在的层 refined > checked > raw）。
   **不读就改等于闭着眼睛改**。
2. `nf_review_chapter` —— 零 token 的确定性审查：正文段落 + 缺陷单（段落号/原句/
   为什么/怎么改）+ 作者的事件勾选。不用等流水线跑 stage5。
3. `nf_structure_metrics` —— 六项分布型结构指标（连排对白 / 短对白比 / 段末短句比 /
   说问密度 / 角色句长差 / 极短段密度）。**说"文风有问题"之前先看它**，别凭感觉。
   命中项可以 `nf_metrics_annotate` 变成可直接改的批注（不动正文，改哪处由作者定）。
4. `nf_chapter_annotations` —— 未收口批注清单（`open` 待处理；`drift`/`missing` = 原句
   已被改掉，别照着旧段落号改）。批注层必须与作者正在看的层一致。
5. `nf_chapters_paragraphs` —— 段落编号 + 每段原文 + 字数。**取准段落号再动手**。

### 精修（每轮同构）
1. 问清作者意见：**改哪里 + 改成什么样**。没有具体意见就不要提交精修 ——
   "再润色一下"式的意见会让模型自由发挥，产物不可预期。
2. `nf_refine_outline` / `nf_refine_chapter` / `nf_refine_paragraph` —— 带意见提交。
   段落级最便宜（只重写选中段），优先用它；整章贵；大纲改动会影响下游章。
   **`nf_refine_paragraph` 只算不落**：出 3 条候选写进 `para_pending.json`，正文不动；
   用 `nf_paragraph_pending` 看改前/改后，用 `nf_paragraph_diff` 看改了哪儿，
   候选不合格用 `nf_paragraph_drop` 丢掉。**采用（写正文）是作者在墨坊里点的**——
   采纳端点要求 GUI 双因子，Agent 调会被 403，这是设计不是 bug。
3. `nf_get_paragraph_history` + `nf_paragraph_restore` —— 作者反悔时回退段落。
4. 大纲改完提醒作者：**大纲变了，已写章节可能对不上**，是否重跑 stage 3/4 由作者定。

### 沙盒审核
1. `nf_get_sandbox_queue` —— 列待审条目。
2. `nf_sandbox_review` —— 通过/驳回**必须带作者的结论**；Agent 可以整理清单、
   陈述利弊，但不代拍。

## 判据（跑完怎么算完）

- stage 7 产物 docx 存在且非零字节；占位符（[书籍标题] 等）被真实内容替换。
- 每次精修后 `nf_get_paragraph_history` 能看到新版本（有据可回退）。
- 沙盒队列清零或剩余项都已标注"待作者"。

## 什么时候必须停下来问作者

- 任何精修提交（会改书稿）。
- 沙盒审核拍板。
- 大纲改动后是否重跑写作阶段（烧钱决策）。
- Word 模板/导出格式与作者预期不符 —— 报差异，不自行换模板。

## 红线

- 不在没有作者意见时提交任何 refine（空意见 = 让模型瞎改）。
- 不删除段落历史（那是唯一的回退路径）。
- 不动 `templates/` 下的模板文件。
