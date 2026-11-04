# -*- coding: utf-8 -*-
"""判定引擎（P0，2026-10-29 起）：把"这章行不行"从模型自评手里拿回来。

## 一句话

**确定性规则产依据 → 作者勾事件 → 规则映射三档结论 → 单一记录落盘。**
全程零 token、断网可跑；结论里没有分数。

## 模块分工

| 模块 | 管什么 |
|---|---|
| `caliber` | 判据登记表 + 版本（R04/F08）；每条判据必须有消费方**与产出方** |
| `rules` | F01 体检 / F02 伪具体功能 / F03 可替换骨架 / F04 章纲密度 / F05 未落地 |
| `destroy` | R02 三稿对照：5967 → 2186 是哪一步干的 |
| `verdict` | R15 三档结论（+不可判定 A/B） |
| `records` | F13 单一出口 + F03b 事件勾选 + F12 逐句裁定 |
| `backtest` | F14 回测台（只对 raw，零 token） |
| `feelings` | M4=B 第三条证据：模型先出候选、作者终裁；越层自裁一律拒收 |

## 三条不许破的界

1. 规则只产**依据**，档位只在 `verdict.decide` 里映射；规则自己说"合格"就是越权。
2. 阈值只在 `caliber` 里；代码里出现硬编码阈值 = 又造一条"配了没人读"。
3. 判定记录只写 `records.json` 一处（F13）。
4. （M4，2026-11-04）**判断分三层**：规则可量的（producer=rules）、模型先出候选的
   （producer=model）、作者终裁的（producer=author）。越层写入一律拒收：
   模型写档位/终裁 → 400（`feelings.assert_no_overreach`），外部 Agent 代作者签字 → 403
   （`agent_guard.FORBIDDEN_IN_AGENT_MODE`）。
"""

__version__ = "1.1.0"

from . import (backtest, caliber, destroy, feelings, records, rules, text,  # noqa: F401
               verdict)

__all__ = ["backtest", "caliber", "destroy", "feelings", "records", "rules", "text",
           "verdict", "__version__"]
