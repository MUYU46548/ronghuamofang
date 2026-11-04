# 问卷规范 v1（2026-11-04）

> 这套东西的由来：2026-11-04 那批墨坊问卷的三类事故——出处指向不存在的文件、问的是已经落地的事、选项不成形——都能被机器拦住，不该再靠人肉复核。
> 本目录是“问卷规范”的第一版实现，目标是**出卷 → 答 → 落盘 → 下游任务读到**这条链能一次性走通，然后整体搬进方寸。

---

## 一、目录里有什么

| 文件 | 用途 |
|---|---|
| `README.md` | 本规范 |
| `decision-log.md` | 决策日志（唯一权威位置；只记决定与已落地事实） |
| `20261104-宪法层-r2.md` | 墨坊问卷 · 宪法层（M1–M7 + N1–N5） |
| `20261104-裁决层A-r2.md` | 墨坊问卷 · 裁决层 A（M8–M12 + N6） |
| `../../scripts/questionnaire_lint.py` | 出卷校验器（不调模型、断网可跑） |
| `../../scripts/questionnaire_collect.py` | 收卷器（勾选 + 附言 → decision-log） |

---

## 二、出卷 → 答 → 落盘

1. **出卷**：按第三节格式写题；每题必须有 `current`（现状 + 证据行号）与 `acceptance`（可判定的验收线）。
2. **校验**：一次传**全部**问卷文件（题间的 `requires` / `conflicts` 可能跨文件）。

   ```bash
   python scripts/questionnaire_lint.py docs/questionnaires/*-r2.md
   python scripts/questionnaire_lint.py docs/questionnaires/*-r2.md --json   # 给方寸读
   ```

   有 `E` 就不许发卷；`W` 允许，但要逐条看（"已答过""基线已变"这两类警告通常意味着该删题）。
3. **答**：在卷面上把 `[ ]` 改 `[x]`，一题只勾一个；"驳回：…" / "缓：…" 直接写在选项下方。
4. **落盘**：把选中项写进 `decision-log.md`（第四节格式）。`缓` 不进日志——**缓不是决定**。
5. **下游读到**：任务开工前先读 decision-log；`kind: landed` 的条目用来判断"这事已经这样了，别再当新决策问"。

---

## 三、题目格式（人和机器看同一份文件）

正文照常写人话；机器只读围栏块。**两块必须同时存在**，正文解释"为什么问"，围栏解释"依据是什么"。

```yaml question
id: mofang.p0.scope                 # 稳定 key，领域.对象
question: 一期范围
baseline: {repo: NovelForge, commit: "982f149", generated_at: "2026-11-04T10:40+08:00"}
current:
  state: partial                   # done | partial | pending | defaulted | unknown
  summary: |-                      # 现状用人话写，必须能被证据支撑
    预算与判定引擎已落地；拆锁只拆了 writing；回测未跑。
  evidence:                        # path:line / path#片段；前缀 ! 表示"断言不存在"
    - prompts/stage5_check.md:4
    - "!data/judge/records.json"
citations:                         # 出处：必须能打开、引文必须对得上
  - path: docs/产品定义基线-20261028.md
    line: 153
    quote: "### 3.3 P0 二十项功能"
options:
  - key: backtest_only
    label: 只跑回测
    cost: {token: 0, days: 0.5}    # 代价必须写，否则作者只能按篇幅猜权重
    reversible: true
requires: []                       # 依赖的题 id（前置未答时，这题暂缓）
conflicts: [mofang.schedule.anchor] # 与哪题互斥（答完要回来看）
acceptance:
  check: judge.py run 后 records.json 覆盖 10 章
  artifact: data/judge/records.json
reconfirm: false                   # true = 明知已答/已落地，刻意再确认一次
open_ended: false                  # true = 允许自由作答（此时可不配 citations）
```

**证据写法**

| 写法 | 含义 | 例子 |
|---|---|---|
| `a/b.md:12` | 文件第 12 行 | `prompts/stage6_polish.md:4` |
| `a/b.json#J10` | 文件里含该片段 | `data/judge/caliber.json#J10` |
| `!a/b` | 断言**不存在**（文件不存在或目录为空） | `!data/judge/records.json` |

---

## 四、决策日志格式

```yaml decision
key: mofang.judge.defect_ratio
date: 2026-10-29
value: 0.20
kind: decision        # decision = 作者拍板；landed = 代码已这样（不是拍板，但同样不该再问）
scope: 判定引擎
source: 作者拍板（记于 data/judge/caliber.json J10）
supersedes: ""
```

规则：

1. `key` 稳定、语义单一；不存在自由文本条目。
2. `landed ≠ decision`：已落地事实单独标，出卷前必须看。
3. 改主意＝追加新条目 + `supersedes` 指向旧条；不删旧条。

---

## 五、校验器判据（`scripts/questionnaire_lint.py`）

| 级别 | 判据 | 为什么 |
|---|---|---|
| E | 必填字段缺失（id / question / baseline / current / options / acceptance） | 缺一项就不是可执行的题 |
| E | `evidence` 为空，或引用路径不存在 / 行号越界 / 片段对不上 | 没有证据的现状＝回忆 |
| E | `citations` 为空（非开放式），或文件不存在 / 行号越界 / 引文对不上 | 2026-11-04：8 条出处 5 红 3 黄 |
| E | 选项 < 2、key 重复、缺 label / cost / reversible | 没有代价的选项只能按篇幅猜 |
| E | `acceptance.check` 为空 | 验收线不可判定 ＝ 没验收 |
| E | `requires` / `conflicts` 指向不存在的 id | 题间依赖不能是口头约定 |
| E | `current.state` 非法、`current.summary` 为空 | 现状必须写清 |
| W | 卷面 commit ≠ 当前 HEAD | 基线变了，整卷可能过期（本次事故根因） |
| W | 现状是 done/defaulted 但选项里没有"确认/维持" | 这题可能只是补签 |
| W | 与 decision-log 同 key 且未标 `reconfirm` | 已答过，别再问 |

`--json` 输出 `{ok, files, errors, warnings, head}`，供方寸直接消费。

---

## 六、与方寸的对接（一期契约）

1. **输入**：卷面 markdown（人读）+ `--json`（机器读）。方寸不解析正文，只消费围栏块。
2. **状态**：每题的 `current.state` 是"要不要问"的第一判据——已 `done` 的题默认不问，除非作者显式 `reconfirm`。
3. **落盘**：decision-log 的 `key` 是唯一连接点；方寸卡片只引用 key，不复述决定内容。
4. **迁移**：本目录整体搬到方寸 task-data 后，`docs/questionnaires/decision-log.md` 保留为只读镜像，key 不变。

一期验收（见宪法层 N5）：一次完整走通并产出 decision-log 新条目 + lint 通过。

---

## 七、这套东西不管什么

- 不判断题目**该不该问**（那是编排/规划能力，仍归人或上游 agent）。
- 不判断选项**好不好**，只查它是否成形（有代价、可逆、互斥）。
- 不替作者答。所有 `reconfirm` 与勾选都是作者动作。

---

## 八、术语（写卷面时必须用作者听得懂的词）

2026-11-04 收卷时踩到的三条，写卷时直接用右列：

| 不要写 | 改写成 | 为什么 |
|---|---|---|
| 零 token、不烧钱 | **不调模型** | 不调模型 ≠ 零成本：每次工具调用、上下文压缩都计 token（作者当场纠正） |
| 协作规矩 | **作品写作约束** | 作者按“这本书允许写什么”理解，不是“AI 该怎么配合” |
| 排期锚 | **什么时候开工** | 术语无人能懂，M6 直接被答成“看不懂，维持现状” |

`cost.token` 的语义同时固定为：**模型调用 token 估算**；`0` 表示不调模型，不是会话总成本为零。

## 九、成本估算的硬要求

任何"写 N 章 / 跑 N 轮"的代价必须来自实测，不许拍脑袋。已有基线：

| 项 | 实测 | 来源 |
|---|---|---|
| 10 章试跑 | 约 3000 万 token / 1–2 小时 | 作者口径，2026-11-04（卷面原估 30 万，差 100 倍） |
| 单章正文 | 约 1 万 token 量级（未实测，仅提示） | — |
