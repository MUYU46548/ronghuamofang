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
- 模型没产出有效改稿时的兜底（复制 raw → 暂存）仍然保留：产物与 raw 相同 → 0 裁定项 → 自动合成，同时保留"本章未经检查"的警告。

### `engine: self` 下走产物闸（2026-11-12 改，原"闸不生效"的例外已拆掉）

原来这里写的是"self 下闸不生效：任务书由外部执行方直接写盘，没有裁定回传通道"。
2026-11-11 起那条前提不再成立：**执行方写候选区，作者批准，程序才合成**
（`utils/product_gate`，与阶段 1/2/3/4/6 同一道门）。

self 下的形状因此是：

```
raw/NN.md ──(外部执行方检查)──▶ data/_pending/stage5/data/chapters/checked/NN.md
                                          │
                                          ├── 界面上“看一眼”：只读预览 + 改了几段
                                          └── 作者批准 ──▶ checked/NN.md
```

- **逐段裁定不适用于 self**（不是因为锁失效，而是因为 self 下没有"程序驱动的模型改稿"这一步：
  执行方交回的就是整稿）。作者的决定是**整章批准 / 驳回**，批准前可以看 diff 条数。
- 未批准的候选**不进 `checked/`**：`self_done(5)` 先 `promote_approved()` 再判
  "checked 稿可用 + 七维账本覆盖全书"，两条都要过。
- 需要逐段勾的场合继续用 direct / hermes 引擎的那套（`gates.stage5_diff`）。

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

裁定面板是**逐处步进**（2026-11-14，P1-1）：一次只展开一处“原文 / 改后”，
`‹ 上一处 · 第 N / M 处 · 下一处 ›` 翻，一次决定的作用域只到这一处；
定完（采用 / 保留原文）自动跳到下一处没定的，按钮位置不动，连续点就是流水线。
下方宫格一处一格（✓ 采用 / ✕ 保留），点哪格看哪处；全定完才显示“可以合成”。
`/review/stage5/decide` 仍是**一项一次**提交，后端零改动。

**只改标点/空白的处不单独占一格**（2026-11-15，P1-2）：

- 判据（确定性、零 token）：`replace` 项的原文与改后**去掉空白与标点后骨架相同**
  （`stage5_gate._skeleton`：标点走 Unicode `P*`、空白走 `isspace()` + `Z*`，
  另剔不可见格式符 `Cf`；不用标点词表 —— 词表换个语言的引号就漏）。
- 挂靠：并到**最近的实质改动**（等距时取前面那一处），记
  `folded_into`（子项）/ `folds`（挂靠项）。那一处怎么定，这些标点改法就怎么走。
- 界面：不计入“第 N / M 处”，不占宫格；本章有一句
  “另有 N 处只是标点/空白的调整，已并入最近一处的决定”，
  挂靠项底下可展开看每一处的原文 / 改后（**不藏**）。
- 计数口径：`list_all` / `_summary` / `can_apply` / CLI `accept` 全部只算实质项，
  并入数单列 `folded`；直接给并入项下裁定会被拒，并说清该定第几处。
- 边界（如实声明，别当成“全都并了”）：① 段落拆并（一段变两段）在骨架层面是实质
  改动，仍单独成项；② 本章一处实质改动都没有时**不并** —— 没有可挂靠处，
  硬并会把“只修标点”的改法悄悄丢掉。

**整页看（版面）**（2026-11-15，P2-1）：

- 数据来自 `stage5_gate.layout_plan` / `layout_for`：用**同一份 `blocks`**（不重跑
  SequenceMatcher，避免分组漂移）把 raw 与 `checked_pending` 各排成一串段落，
  改过的段落带 `item` 号。API 在 `GET /review/stage5/chapter` 的 `layout` 字段返回。
- 界面：`逐处看`（默认，P1-1 的流水线）/ `整页看（版面）` 两个视图，同一份状态。
  版面按成书排版（衬线、两字缩进），改动段带底色 + “第 N 处”，没改的段落无标记。
- **双向定位**：点改动的那一段 → 当前这一处跟着变，切回逐处看停在同一处；
  逐处的“在版面里看这一处” → 切到版面并滚到那一段。
- 段落上直接定：鼠标移过改动段出现“采用 / 保留”，已定的段落显示**字的结论**
  （✓ 已采用 / ✕ 保留原文），不只靠底色。
- 版面模式下定完**不自动跳**下一处（跳走会让高亮环和刚定的一段对不上）；
  逐处模式仍按 P1-1 的流水线跳。
- 边界：两栏各自滚动、各按自己的段落顺序排 —— 新增项只在改后栏、删除项只在原稿栏，
  所以同一个号**不一定在同一水平线**（界面上有一句说明，不假装逐行对齐）。

---

## 五、回滚

1. 临时关闸：`config/system.yaml` → `gates.stage5_diff: off`，重跑阶段 5 即回到旧行为。
2. 清掉待批产物：删 `data/chapters/checked_pending/` 与 `data/review/stage5/`，重跑阶段 5。
3. 已合成的 `checked/` 是正常产物，按既有回退路径（快照 / 重跑阶段）处理。

---

## 六、验证

```
tests/unit/test_stage5_gate.py   44 项（含 API 层；未勾完拒绝、全拒=原文、stale、noop；
                                 第 9 节锁“标点项并入”：就近/等距挂靠、取舍跟着挂靠项
                                 走（采用与不采用两向都验）、计数只算实质项、无挂靠处
                                 不并、段落拆并仍单独成项；
                                 第 10 节锁“阅读态版面”：两栏能拼回各自原文、改过的段
                                 带 item 号、没改的段 None、新增/删除只在一边有段落、
                                 两稿缺一 → None）
tests/unit/test_product_gate_stages.py  【C】组（self：候选未批不进 checked、批准后合成、
                                         账本仍缺仍判未完成）
test_api_domains                 路由契约
test_judge_locks                 防"拆锁"回退
nfctl test                       全量
```

UI 验收：`tests/e2e/e2e_ux_verify.py` 三段 —— J 段（逐处步进 + 标点项并入）、
L 段（整页看版面 + 双向定位 + 段落上直接定，19 条）；假后端第 1 章给 J 用、
第 2 章给 L 用（J 会把第 1 章一路定完并合成，共用一章就把 L 的环境吃掉）。
安装包未重打 —— 界面改动要出包才到用户手上。
