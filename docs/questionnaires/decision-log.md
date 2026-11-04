# 墨坊决策日志（decision-log）

> 唯一权威位置：`docs/questionnaires/decision-log.md`（方寸接入后整体迁到方寸 task-data，路径随之更新，key 不变）。
> 只记**决定**与**已落地事实**，不记任务拆分（任务进方寸卡片）。
> 同一 key 允许多条：新条目必须写 `supersedes`，读的时候取最新一条。

## 一、怎么加一条

```yaml decision
key: mofang.example.key
date: 2026-11-04
value: 示例值
kind: decision        # decision = 作者拍板；landed = 代码已落地（非拍板，但同样不该再问）
scope: 全项目
source: 来源（哪份卷 / 哪次拍板）
draft: true           # 仅示例；读侧（lint / 收卷器）忽略 draft: true
supersedes: ""        # 若覆盖旧条目，填旧条目的 key@date
```

规矩（三条，都是踩出来的）：

1. **key 稳定、语义单一**：`mofang.<领域>.<对象>`；不存在"顺手记一句"的自由文本条目。
2. **landed ≠ decision**：代码已经这样做的事实单独标 `kind: landed`，出卷前必须看它，否则会把既成事实当新决策问（2026-11-04 那批卷子就是这么错的）。
3. **改主意 = 追加**，不删旧条；旧条目的效力由 `supersedes` 链决定，审计时可回溯。

---

## 二、已有条目（预填，无需重答）

| key | 日期 | 值 | 类型 |
|---|---|---|---|
| `mofang.service_scope` | 2026-09-15 | 服务对象：作者本人 | decision |
| `mofang.boundary.game_copy` | 2026-09-15 | 游戏文案永不进 | decision |
| `mofang.sandbox.anonymity` | 2026-10-25 | 出沙盒单子不带"墨坊"字样 | decision |
| `mofang.mingjian.position` | 2026-10-30 | 产线后段；锚=文章改完后（2027 五六月窗） | decision |
| `mofang.direction.agent_intelligence` | 2026-11-01 | 必须搞复杂的 agent 智能 | decision |
| `mofang.budget.stage5_max_tokens` | 2026-10-29 | 40000 | landed |
| `mofang.budget.stage6_max_tokens` | 2026-10-29 | 25000 | landed |
| `mofang.judge.defect_ratio` | 2026-10-29 | 0.20（缺陷段占比阈值 J10） | decision |
| `mofang.judge.engine` | 2026-10-29 | 确定性规则产依据 + 作者裁定；阈值只在口径表 | landed |
| `mofang.plan.four_state` | 2026-10-29 | idea / selected / written / dropped | landed |

```yaml decision
key: mofang.service_scope
date: 2026-09-15
value: 作者本人
kind: decision
scope: 全项目
source: 预填（10-28 基线沿用）
supersedes: ""
```

```yaml decision
key: mofang.boundary.game_copy
date: 2026-09-15
value: 游戏文案永不进
kind: decision
scope: 全项目
source: 预填（10-28 基线沿用）
supersedes: ""
```

```yaml decision
key: mofang.sandbox.anonymity
date: 2026-10-25
value: 出沙盒单子不带“墨坊”字样
kind: decision
scope: 对外件
source: 预填
supersedes: ""
```

```yaml decision
key: mofang.mingjian.position
date: 2026-10-30
value: 产线后段；锚=文章改完后（2027 五六月窗）
kind: decision
scope: 全项目
source: 预填
supersedes: ""
```

```yaml decision
key: mofang.direction.agent_intelligence
date: 2026-11-01
value: 必须搞复杂的 agent 智能，否则产物质量太低
kind: decision
scope: 全项目
source: 预填
supersedes: ""
```

```yaml decision
key: mofang.budget.stage5_max_tokens
date: 2026-10-29
value: 40000
kind: landed
scope: 阶段5
source: prompts/stage5_check.md:4
supersedes: ""
```

```yaml decision
key: mofang.budget.stage6_max_tokens
date: 2026-10-29
value: 25000
kind: landed
scope: 阶段6
source: prompts/stage6_polish.md:4
supersedes: ""
```

```yaml decision
key: mofang.judge.defect_ratio
date: 2026-10-29
value: 0.20
kind: decision
scope: 判定引擎
source: 作者拍板（记于 data/judge/caliber.json J10）
supersedes: ""
```

```yaml decision
key: mofang.judge.engine
date: 2026-10-29
value: 确定性规则产依据 + 作者裁定；阈值只在口径表
kind: landed
scope: 判定引擎
source: docs/agents/judge-engine.md
supersedes: ""
```

```yaml decision
key: mofang.plan.four_state
date: 2026-10-29
value: idea / selected / written / dropped
kind: landed
scope: 大纲与规划
source: scripts/utils/plan_state.py:12
supersedes: ""
```

---

## 收卷 2026-11-04（自动追加）

```yaml decision
key: mofang.p0.scope
date: 2026-11-04
value: backtest_plus_locks
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  注：零token绝对不成立，每次工具调用都在消耗宝贵的token，就连压缩上下文都算token。
```

```yaml decision
key: mofang.judge.benchmark
date: 2026-11-04
value: user_import_from_gui
kind: decision
scope: 墨坊
source: 卷面附言（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  应该让用户自己提供，不是我提供。根本不该写死在代码本体里，用户自行从客户端界面导入，随时可撤。免责声明（版权、使用等）必须到位。而且我记得我提过至少两次，居然没做？
```

```yaml decision
key: mofang.judge.defect_ratio
date: 2026-11-04
value: keep
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  注：已出结论无所谓，世界上现有用户不超过三个人，没人跑真实项目，随便改。
```

```yaml decision
key: mofang.judge.third_evidence
date: 2026-11-04
value: model_plus_human
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  注：3–5 千 token/章和当前开发每天几千万token起步相比九牛一毛，多的时候绒花墨坊一天开发能烧两三亿token。
```

```yaml decision
key: mofang.ship_line
date: 2026-11-04
value: keep
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
```

```yaml decision
key: mofang.schedule.anchor
date: 2026-11-04
value: deferred
kind: decision
scope: 墨坊
source: 卷面附言（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  看不懂，维持现状。
```

```yaml decision
key: mofang.schedule.zero_token_now
date: 2026-11-04
value: accept_zero_token
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
```

```yaml decision
key: mofang.test_material
date: 2026-11-04
value: own_fictional_material
kind: decision
scope: 墨坊
source: 卷面附言（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  圣所素材他妈和写作一点关系都没有，别听原卷瞎扯。我自有素材，一直在等（完全虚拟）。
  位置：桌面；后续可能添加；用途是客户端试跑，暂不用于回测（2026-11-04 追问）。
```

```yaml decision
key: mofang.stage5.rewrite_authority
date: 2026-11-04
value: review_gate
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
```

```yaml decision
key: mofang.judge.backtest_sample
date: 2026-11-04
value: write_new
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  注：30 万 token、2–3 天的预估严重失真，写这个数据的人100%从没用过绒花墨坊。根据以往试跑经验，约为3000万token，1-2小时。
```

```yaml decision
key: mofang.pause.zero_token_definition
date: 2026-11-04
value: mechanical_with_veto
kind: decision
scope: 墨坊
source: 卷面附言（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  C 默认机器判据，但用户随时保留最终否决权。
```

```yaml decision
key: mofang.decision_log.authority
date: 2026-11-04
value: keyed_supersedes
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  注：决策日志本身值得单开一轮讨论。没有什么文件是永恒的，人工维护是不可能的，实际上半个月都不会打开一次。
```

```yaml decision
key: mofang.questionnaire.phase1_acceptance
date: 2026-11-04
value: end_to_end
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-宪法层-r2.md）
supersedes: ""
note: |-
  方寸"AI 规划"的形态本身从未落下，我默认按方寸无内置AI智能，当大号harness用理解，最终执行由外部agent连接进行。
```

```yaml decision
key: mofang.handoff.card
date: 2026-11-04
value: wire_up
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-裁决层A-r2.md）
supersedes: ""
```

```yaml decision
key: mofang.outline.approval_wiring
date: 2026-11-04
value: full
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-裁决层A-r2.md）
supersedes: ""
```

```yaml decision
key: mofang.setting.stale
date: 2026-11-04
value: auto_stale
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-裁决层A-r2.md）
supersedes: ""
```

```yaml decision
key: mofang.rerun.merge
date: 2026-11-04
value: merge
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-裁决层A-r2.md）
supersedes: ""
note: |-
  注：我记得全书重跑是另一个单独的按钮？虽然我在界面上没找到。用户永远不会读代码，只会在GUI找东西。
```

```yaml decision
key: mofang.collab.rules_home
date: 2026-11-04
value: add_book_rules
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-裁决层A-r2.md）
supersedes: ""
note: |-
  我不太理解协作规矩是什么，我按照作品写作约束来理解。应该随作品（项目）单独设置。
```

```yaml decision
key: mofang.broe.segment_scope
date: 2026-11-04
value: probe_first
kind: decision
scope: 墨坊
source: 卷面勾选（20261104-裁决层A-r2.md）
supersedes: ""
```

```yaml decision
key: mofang.test_material.exclude_sacred
date: 2026-11-04
value: 圣所素材与写作无关，禁止作为墨坊测试素材
kind: decision
scope: 墨坊
source: 卷面附言（20261104-宪法层-r2.md · M7）
supersedes: ""
note: |-
  原卷把“圣所素材”列为猜测项，作者明确否认：与写作一点关系都没有。
```

```yaml decision
key: mofang.cost.per_ten_chapters
date: 2026-11-04
value: 约 3000 万 token / 1–2 小时（10 章实测口径）
kind: landed
scope: 成本估算
source: 作者实测口径（20261104-宪法层-r2.md · N2 附言）
supersedes: ""
note: |-
  卷面原估 30 万 token / 2–3 天，作者指出“写这个数据的人 100% 从没用过绒花墨坊”。
  后续任何“写 N 章”的代价估算以此为准，不要再拍脑袋。
```

```yaml decision
key: mofang.judge.backtest_sample
date: 2026-11-04
value: deferred
kind: decision
scope: 墨坊
source: 作者追问（2026-11-04 收卷后）
supersedes: mofang.judge.backtest_sample（同日勾选 write_new）
note: |-
  素材是客户端试跑用的，作者“想象不出任何用的场景”；新写 10 章暂缓。
  回测等有真稿（或明确要跑一次客户端试跑）时再做。
```

```yaml decision
key: mofang.broe.d1_probe
date: 2026-11-04
value: 探针工具就绪；真稿未测，D1 仍为“未确认”
kind: landed
scope: 接管半径
source: scripts/d1_probe.py + docs/agents/d1-probe-20261104.md
supersedes: ""
note: |-
  结论：当前段身份不存在，只有数组下标，跨阶段不可传递。
  方法：段身份=内容指纹，改稿后按相似度重锚；carry_rate 为可行性指标。
  真值测不出：data/chapters/raw 为空；阈值 0.8/0.6 仅为建议，未进口径表。
```
