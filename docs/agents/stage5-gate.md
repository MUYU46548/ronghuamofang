# stage5 改稿裁定闸（2026-11-04）

> 作者决定（`decision-log` · `mofang.stage5.rewrite_authority`）：**保留模型改稿，但逐条 diff 必须作者勾过才落 `checked/`**。
> 这篇写清它怎么落地、边界在哪、怎么回滚。

---

## 一、它解决什么

改之前：stage5 的模型把“修正后的整章”直接写进 `data/chapters/checked/`，成品链的下一个环节（stage6 润色）拿到的就是模型改过的稿。作者只能事后看 diff，**不能拒绝某一处修改**。

改之后：

```
raw/NN.md ──(模型改)──▶ checked_pending/NN.md ──(逐条裁定)──▶ checked/NN.md
                                  │
                                  └── data/review/stage5/NN.json（裁定项与状态）
```

- 模型产物**不再是成品**，只是"待批改稿"。
- 裁定粒度是**段落**：`replace` / `insert` / `delete`；没改的段落不产生裁定项（不逼作者点头）。
- 全部勾完才合成；合成规则：采用 → 用模型的新段；不采用 → 保留 raw 原段。
- 一条都不采用 = 成品与 `raw` **逐字节一致**（拒绝是合法的最终态）。

---

## 二、状态机

| 状态 | 含义 | 下一步 |
|---|---|---|
| `ready` | 有裁定项，一个都没勾 | 等作者 |
| `pending` | 勾了一部分 | 等作者 |
| `decided` | 全部勾完 | 可合成 |
| `noop` | 模型没改（0 项） | 自动合成（结果=raw） |
| `approved` | 已合成 `checked/` | 下游可跑 |
| `stale` | 裁定后 raw 或暂存稿变了 | 重新生成裁定项，旧裁定作废 |

`stale` 是防"拿旧裁定套新稿"：`raw_sha` 在裁定与合成之间对不上就拒绝。

---

## 三、流水线行为

- `gates.stage5_diff: block`（默认）时：
  - 有未裁定的章 → `progress.stages.5.status = "awaiting_review"`，orchestrator **退出码 3**（与审批门同码，等待人工）。
  - 再点运行 → stage5 发现已有提案，**不再重复调模型**，继续等；勾完合成后重跑才往下走。
- `gates.stage5_diff: off` → 旧行为（直接写 `checked/`）。
- `engine: self` → **闸不生效**：任务书由外部执行方直接写盘，没有裁定回传通道。这是已声明的边界，不是 bug。
- 模型没产出有效改稿时的兜底（复制 raw → 暂存）仍然保留：产物与 raw 相同 → 0 裁定项 → 自动合成，同时保留"本章未经检查"的警告。

---

## 四、接口

| 端点 | 谁可调 |
|---|---|
| `GET /review/stage5/list` | 读侧不拦 |
| `GET /review/stage5/chapter?n=` | 读侧不拦 |
| `POST /review/stage5/decide` | **仅 GUI**（`agent_guard` 403） |
| `POST /review/stage5/apply` | **仅 GUI**（同上） |

CLI 等价物（排障用，不需要起服务）：

```bash
python scripts/stage5_gate.py list
python scripts/stage5_gate.py show 3
python scripts/stage5_gate.py accept 3 --all          # 或 --reject-all / --item 1
python scripts/stage5_gate.py apply 3
python scripts/stage5_gate.py apply --all
```

界面：**审稿**页签顶部“stage5 改稿裁定”。

---

## 五、回滚

1. 临时关闸：`config/system.yaml` → `gates.stage5_diff: off`，重跑阶段 5 即回到旧行为。
2. 清掉待批产物：删 `data/chapters/checked_pending/` 与 `data/review/stage5/`，重跑阶段 5。
3. 已合成的 `checked/` 是正常产物，按既有回退路径（快照 / 重跑阶段）处理。

---

## 六、验证

```
tests/unit/test_stage5_gate.py   23 项（含 API 层；未勾完拒绝、全拒=原文、stale、noop）
test_api_domains                 路由契约
test_judge_locks                 防"拆锁"回退
nfctl test                       全量
```

UI 验收：`tests/e2e/e2e_ux_verify.py`（新增面板需真机目检；本轮仅 `vite build` 通过，安装包未重打）。
