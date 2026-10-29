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

GUI：**章节页签顶部「判定」摘要条** → 整本一页（档位表）→ 单章（缺陷卡 + 事件勾选 + 原文）
→ 「改标准」（合格标准表，带消费方）。

## 端点（零 token）

`GET /judge/report|chapter|events|caliber|destroy`、
`POST /judge/run|events|sentence|caliber`。
`/judge/events` 与 `/judge/sentence` 列进 `FORBIDDEN_IN_AGENT_MODE`：**事件勾选与逐句裁定
是作者的判断**，外部 Agent 可读不可代替签字。

## 已知边界（P0 不做）

- 不做成品失效与重出（F10 的 P0 形态是"比对 + 标已过期"，重出走 P1）。
- 不做评审与改稿分离的三栏视图（F11，P1）。
- 不做正例库（F21，P1）；F02 只召回候选，成立与否由作者裁定。
- 规则的**精度**未经真机标定：F02 侧重"不漏"（宁多报、由作者裁定），
  F04/F05 是结构启发式，阈值都在口径表里可改。
