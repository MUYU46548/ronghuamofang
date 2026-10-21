---
name: nf-plan
description: "绒花墨坊·规划阶段（1-3）接线卡：素材归并→整体大纲→逐章大纲。外部 Agent 按本卡编排规划类任务：该调哪几个工具、判据是什么、什么时候停下来问作者。触发词：nf-plan、墨坊规划、跑大纲、归并素材。**编排不代跑**：一次一个阶段，审批门必须留给作者。"
version: 1.0.0
metadata:
  hermes:
    tags: [novelforge, 绒花墨坊, phase-skill, plan, outline]
    category: worldbuilding
---

# nf-plan：规划阶段（stage 1-3）

> 五个阶段 skill（nf-plan / nf-write / nf-revise / nf-memory / nf-project）是
> **编排卡**：告诉你这一步调什么、判据是什么、何时停。**不写自主循环** ——
> 一次一个阶段，跑完汇报，审批门留给作者。工具名以 `nf_mcp.MCP_TOOLS` 为准。

## 阶段边界

| 阶段 | 做什么 | 产物 |
|---|---|---|
| 1 | 素材卡归并成设定集 | `data/setting/setting.json` |
| 2 | 整体大纲 | `data/outline/global.md`（**审批门**） |
| 3 | 逐章大纲（六字段） | `data/outline/chapters/NN.md` |

## 标准动线

1. `nf_get_state` —— 看当前阶段、审批门、预算。**预算见底就停**，不要硬跑。
2. `nf_estimate_tokens` —— 预估本次要烧多少。与作者给的预算不符就先问。
3. `nf_get_setting_conflicts` —— 有待人工拍板的设定分歧 → **停下来给作者裁决清单**，不要代拍。
4. `nf_run_stage`（stage=1/2/3）—— 一次只跑一个阶段。返回 job_id。
5. `nf_get_stream_status` —— 轮询 job 直到结束（间隔 ≥5s，不要密集轮询）。
6. 阶段 2 完成后：`nf_get_outline`（review=true）看体检报告；空泛节点多 →
   `nf_refine_outline` 带作者意见修订（**意见必须来自作者**，不要自己编）。
   手工整理过的大纲用 `nf_save_outline` 落盘（自带格式校验 + 旧版备份）。
7. 质量存疑 → `nf_get_remedy` 拿补救建议；大纲版本回退 → `nf_restore_snapshot`。

## 判据（跑完怎么算完）

- 阶段 2 产物 `global.md` 存在且体检无 P0 项 → **等作者审批**（exit=3 是正常态，不是失败）。
- 阶段 3 章大纲六字段齐全（缺字段的章会由后端判不合格并重写，无需你补）。
- `nf_get_state` 里对应阶段 status=done。

## 什么时候必须停下来问作者

- 审批门（阶段 2 大纲放行）——**永远是作者的决定**，Agent 不能代批。
- 设定冲突裁决（`nf_get_setting_conflicts` 有未决项）。
- 预估 token 超出作者授权额度。
- 素材为空或书名仍是占位符 —— 先向作者要素材/信息，不要空跑。

## 红线

- 不跑 `nf_run_stage` 的 stage>3（那是 nf-write 的地盘）。
- 不改 `config/system.yaml` 的预算/模型键（止烧闸门不许 Agent 抬高）。
- 不删除 `data/` 下任何产物；回退只用 `nf_restore_snapshot`。
