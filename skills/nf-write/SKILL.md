---
name: nf-write
description: "绒花墨坊·写作阶段（stage 4-6）接线卡：逐章写作→逻辑检查→润色。外部 Agent 按本卡编排写作类任务：该调哪几个工具、判据是什么、什么时候停下来问作者。触发词：nf-write、墨坊写作、跑章节、写正文。**编排不代跑**：一次一个阶段，低分章转人工，不擅自重写。"
version: 1.0.0
metadata:
  hermes:
    tags: [novelforge, 绒花墨坊, phase-skill, write, chapters]
    category: worldbuilding
---

# nf-write：写作阶段（stage 4-6）

> 阶段 skill 是**编排卡**，不是代跑授权：一次一个阶段，跑完汇报，
> 审批门留给作者。工具名以 `nf_mcp.MCP_TOOLS` 为准。

## 阶段边界

| 阶段 | 做什么 | 产物 |
|---|---|---|
| 4 | 逐章写作（每章一次调用） | `data/chapters/raw/NN.md` |
| 5 | 逻辑检查（七维账本 + 审美五问） | `data/review/NN.json` + 账本汇总 |
| 6 | 润色 | `data/chapters/refined/NN.md`（**审批门**） |

## 标准动线

1. `nf_get_state` —— 确认 stage 1-3 已 done 且大纲已审批。**没批就先回去走 nf-plan**。
2. `nf_estimate_tokens` —— 写作是全流水线最贵的阶段（每章输入可达上万 token）。
   预估超作者额度 → **停下来问**，不要跑。
3. `nf_run_stage`（stage=4）—— 一次跑整阶段（后端逐章推进，失败章自动停）。
4. `nf_get_stream_status` —— 轮询 job（间隔 ≥5s）。写作是长任务，耐心等，
   **不要重复提交同一个 stage**（会双倍烧钱）。
5. 完成后 `nf_get_chapters_quality` —— 看逐章质量分。
6. 低分章处理顺序：`nf_run_auto_rewrite`（dry_run 默认 true，先预演）→
   作者同意后 dry_run=false 真改 → 仍不达标转 `nf_refine_chapter` 带**作者意见**定向精修。
7. **单章重写**（作者点名“重写第 N 章”时才做）：`nf_rewrite_chapter`（参数 n）——
   旧稿自动备份到 `data/chapters/rewrite_backup/`，只改 raw；**不要用 write_file
   直接覆盖 raw/checked/refined**（绕过拼装、校验与备份）。完成后仍要等 5/6/7。
8. 阶段 5：`nf_get_review_report` 看审稿报告 + 七维账本；有决策项 →
   `nf_save_review_decisions`（**逐条意见来自作者**，Agent 不代拍）。
9. 阶段 6 完成 = 润色稿就绪 → **等作者审批**（exit=3 正常态）。

## 判据（跑完怎么算完）

- `nf_get_state` 对应阶段 status=done；章节文件在 `data/chapters/{raw,refined}/` 落齐。
- 阶段 5 的评审账本完整（七维缺项会被后端标出；有缺项先跑 remedy 不硬放行）。
- 残稿（截断稿）在章节页签可见可采纳 —— 有残稿先报给作者，不擅自采纳。

## 什么时候必须停下来问作者

- 润色稿审批门（阶段 6 放行）。
- 任何自动重写/定向精修的**真跑**（dry_run=false 产生 LLM 费用）。
- 连续多章失败（多是系统性问题：模型挂了/提示词坏/预算断）—— 报症状，不要反复重试烧钱。
- token 熔断触发（budget.paused）—— 需要作者决定是否调阈值（Agent 不得自行抬闸门）。

## 红线

- 不把 `nf_run_auto_rewrite` 的 dry_run 默认改成 false（那是作者的开关）。
- 不代作者做审稿决策（`nf_save_review_decisions` 的意见必须是作者原话的结构化）。
- 不改 `chapter.target_words` 以外的写作判据配置；改它也要先问（创作决策）。
