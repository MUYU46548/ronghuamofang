# 判定引擎（P0，2026-10-29）

**一句话**：把"这章行不行"从模型自评手里拿回来 —— 确定性规则产依据、作者勾事件、
规则映射三档结论、单一记录落盘。全程**零 token、断网可跑**，结论里**没有分数**。

## 为什么有它（2026-10-28 复盘的六条实证）

| 事实 | 后果 |
|---|---|
| `prompts/stage5_check.md` 要"输出修正后的全文"，而 `max_tokens: 8000` | 6000 字装不下 → 模型自己删 → 5967 → 2186，`finish_reason` 还是 `stop` |
| 章节质量分是模型自己在文末写的 `<!-- quality: 8/10 -->` | 空转章拿 8.14 分、`complete: true` 放行 |
| 口径 10-26 才提到 [5000,9000]，存量不重判 | 8/10 章低于新口径，界面仍写 done |
| stage5 评审同时改稿（写 `checked/`）再给改后稿打分 | 判定对象是它自己改过的稿 |
| `style_rules` 来自 `writing_form.style_blocks`，默认注入极简派规格 | 作者从没选过这套风格 |
| `verify.min_chapter_words: 5000` 没有任何运行时消费方 | "配了没人读" |

## 模块分工

| 模块 | 管什么 |
|---|---|
| `utils/judge/caliber.py` | 判据登记表（14 条）+ 版本（改值产新版本，历史只读）+ **空判据检查** |
| `utils/judge/rules.py` | F01 体检 / F02 伪具体候选 / F03 可替换骨架 / F04 章纲密度 / F05 正文与章纲对账 |
| `utils/judge/destroy.py` | R02 三稿对照：逐句删除/改写 + 缩减率 + "两次压缩 → 判定 B" |
| `utils/judge/verdict.py` | R15 三档结论 + 不可判定 A/B；**缺陷段占比阈值 20%** |
| `utils/judge/records.py` | F13 单一出口 `data/judge/records.json`；F03b 事件勾选；F12 逐句裁定 |
| `utils/judge/backtest.py` | F14 回测台（只读 raw、零模型）+ 判据矩阵 |
| `utils/judge/budget.py` | R01 预算—篇幅联动：装不下就**一个请求都不发** |

## 三条不许破的界

1. 规则只产依据，档位只在 `verdict.decide` 映射 —— 规则自己说"合格"就是越权。
2. 阈值只在 `caliber` 里；代码里出现硬编码阈值 = 又造一条"配了没人读"。
3. 判定记录只写 `records.json` 一处（单测枚举写入目录做反证）。

## 用法

```bash
python scripts/judge.py run                 # 对 raw 全量回测（写 records.json）
python scripts/judge.py report              # 最近一次总账
python scripts/judge.py chapter 3           # 单章缺陷单（第几段/哪句/为什么）
python scripts/judge.py destroy 3           # 三稿对照：raw 5967 → checked 2186
python scripts/judge.py events 3 --checked 1,2 --class 1=关系 --evidence "第 12 段"
python scripts/judge.py sentence 3 --paragraph 5 --text "…" --feeling 打中了 --reason "…"
python scripts/judge.py caliber --set J10=0.25
```

GUI 两个入口，勾选那块是**同一个组件**（`console/src/JudgeEvents.vue`）：

- **分章页**（I3 的规定位置）：选中一章 → 章纲卡片下方“这一章发生了几件事（你来定）”。
  走 `GET /judge/events?chapter=n&outline=1` 这条**轻读**路径（只解析章纲 + 读作者记录），
  不跑完整分析 —— 分章页不需要三档与三稿对照。
- **章节页签顶部“判定”摘要条** → 整本一页（档位表）→ 单章（缺陷卡 + 事件勾选 + 原文）
  → “改标准”（合格标准表，带消费方）。

⚠️ 两处各写一份必然漂移（本项目在“判据写两遍”上栽过多次），改勾选交互只改那个组件。

## 端点（零 token）

`GET /judge/report|chapter|events|caliber|destroy|artifact`、
`POST /judge/run|events|sentence|caliber|caliber/preview|export`。
- `/judge/caliber/preview`（R06）：改口径**前**算影响面 —— 受影响章数 / 预计转不合格 /
  需你重录条数三行分列，**一个字都不写**。
- `/judge/artifact`（R07）：成品 docx 与源稿比对，过期必须给**两项凭据**（字数 + 时间）；
  没成品时是“未生成”，语义不同于“已过期”。
- `/judge/export`：显式导出 `output/judge_report.md`。
- `/judge/events` 与 `/judge/sentence` 列进 `FORBIDDEN_IN_AGENT_MODE`：**事件勾选与逐句裁定
  是作者的判断**，外部 Agent 可读不可代替签字。
- `/judge/events?chapter=n&outline=1`（2026-10-31 加）：附带章纲事件列表 + 已勾状态 +
  `event_hint`（字面重合提示）。**两个计数别混**：`events` = 勾了几条，
  `counted_events` = 勾了 + 分了类 + 有证据句（R24 AC-24-2 的“不计入下限”）。
  **判档目前按 `events`**，切到 `counted_events` 是有意留给人拍板的一次改动（见 verdict.py 注释）。

## 证据分级（R18）

报告带两个区，**不许混**：
- `capability`：要基准集（`data/judge/benchmark/`）才算数；没有就写“不误报未验证”。
- `demo`：勾了几章只是**流程验证，不构成能力证明**。

## 已知边界（P0 不做）

- 不做成品失效与重出（F10 的 P0 形态是"比对 + 标已过期"，重出走 P1）。
- 不做评审与改稿分离的三栏视图（F11，P1）。
- ~~不做正例库（F21，P1）~~ → 2026-11-04 已做（用户界面导入，见下节）；F02 只召回候选，成立与否由作者裁定。
- 规则的**精度**未经真机标定：F02 侧重"不漏"（宁多报、由作者裁定），
  F04/F05 是结构启发式，阈值都在口径表里可改。

## 正例集（F21，2026-11-04）

判据是**清单里有没有条目**，不是"目录在不在"（空目录曾让 `verified` 误报为 true）。

- 存储：`data/judge/benchmark/index.json` + `files/<sha12>.md` + `_removed/`（移除区，可恢复）。
- 导入：**作者从审稿页导入**（选文件或粘贴正文，≥300 字），必须勾免责声明确认；
  `accept_license` 与版本写进条目，可审计。
- 写侧全部 GUI-only（`/judge/benchmark/import|remove|restore` 在 `FORBIDDEN_IN_AGENT_MODE`）：
  正例是作者的审美样本，Agent 自选标准自判 = 代签。
- "移除"只移到 `_removed/`，不删文件；`restore` 可恢复。
- 回测报告 `evidence.capability.verified` 只在条目数 > 0 时为 true，并给出份数。
- 判据：`tests/unit/test_benchmark.py`（28 项，含 API 层）。

## 第三条证据（M4=B，2026-11-04 作者拍板）

> 问卷原话（`docs/questionnaires/20261104-宪法层-r2.md` M4）：证据由**模型先出**、作者**终裁**；
> 越层代裁的判据要被打回。验收 = ① 判据清单标明产出方 ② 越层写入被拒 ③ 真跑一次模型能产合格证据。

### 判断分三层，每层只有一个写入口

| 层 | 谁 | 能写什么 | 写不进什么 |
|---|---|---|---|
| 规则 | `utils/judge/rules.py` | 依据（字数/句长/复读/密度/落地） | 档位、分数、作者裁定 |
| 模型 | `utils/judge/feelings.py` | `pending` 候选（段号 + 原句 + 感受 + 理由） | 档位 / status / source / 终裁 → **400** |
| 作者 | 界面“第三条证据”/ `POST /judge/feelings/settle` | 采纳 / 推翻 / 驳回（推翻与驳回都必须给理由） | —— |

### 判据清单带产出方（`producer` / `settled_by`）

- `caliber.FACTORY` 现 15 条，每条 `producer ∈ {rules, model, author}`、
  `settled_by ∈ {verdict.decide, author}`。J12（事件数）= 作者产作者裁；
  **J15（第三条证据）= 模型产、作者裁**。
- `GET /judge/caliber` 经 `describe()` 逐条给 `producer_name` / `settled_by_name`；
  界面“改标准”新增“产出方 / 谁终裁”一列。
- **老工作区（v1 的 14 条、无 producer）不自动顶上**：`needs_upgrade` 只报缺什么，
  `POST /judge/caliber/upgrade` 显式补登记（产新版本、旧版进 history、不动任何阈值）。
  为什么不做读盘兜底：口径模块的铁律是"判定时只读登记表"；自动顶替会让"表里没写"
  看起来像"表里写了"，而 M4 要的正是**登记过的事实**。

### 越层一律拒收（验收②）

`POST /judge/feelings/propose`（Agent 可调用）顶层只收 `chapter/items/model/note`，
条目只收 `paragraph_index/sentence/feeling/reason`。带 `verdict` / `status` /
`settled_by` / `source` / `book_reject` / `quality` / `score` / `checked` … 任一 →
**400 且整批不落盘**（不许"部分接受"，静默丢弃比报错更糟）。

证据本身还要过三关（与缺陷单同一把尺）：原句必须在 raw 正文里定位到（先按句子、
再退化为"整段包含"——跨句引用也认，但**不许编造**）、段号必须与正文一致、理由 ≥4 字。
落盘状态一律 `pending`：模型提案**不会**自动变成作者裁定。

### 终裁只认作者

- `POST /judge/feelings/settle` 列进 `FORBIDDEN_IN_AGENT_MODE`（Agent → 403）：
  采纳 / 推翻 / 驳回；**已终裁的不许再裁**（不覆盖）；正文改过、原句定位不到 → 拒。
- 作者裁定仍走 `records.set_sentence(..., source="author", from_proposal=<提案 id>)`，
  报告才数得出"最终结论里有多少条是模型先出的"（`third_evidence.author_from_model`）。
- 提案与终裁都写 `records.json` 的 `proposals.feelings` —— F13 单一出口不破。

### 真跑一次（验收③，要花钱）

```bash
python scripts/judge_feelings.py --chapter 3 --dry-run   # 零调用：看提示词与预估
python scripts/judge_feelings.py --chapter 3             # 一次 direct 调用 → 合格候选落盘
python scripts/judge_feelings.py --chapter 3 --print     # 只看已有候选与终裁状态
```

提示词 `prompts/judge_feelings.md`（明写"不许评档位/分数、不许编造原句"）。
`engine: self|hermes` 下不跑真调用（self 按设计禁调模型），`--dry-run` 仍可用。

判据：`tests/unit/test_judge_third_evidence.py`（73 项，含越层/编造/终裁/单一出口/CLI 预演）
+ `tests/e2e/e2e_judge_panel.py` §I（真机：提案 → 待你裁 → 采纳 → 计数变化 → 口径表带产出方）。
