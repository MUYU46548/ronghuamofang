# -*- coding: utf-8 -*-
"""字数口径：**只计汉字**（2026-11-05 作者拍板，产品基线 Q-1/P2）。

## 为什么要有这份唯一实现

此前全仓有三份 `count_cn_words`，判据还不一样：

| 位置 | 旧口径 | 后果 |
|---|---|---|
| `quality_checklist.py` | 只计汉字 | 与另两处读数不同 |
| `utils/verify_chapter.py`（写作端） | 去空白与 Markdown 符号后**计字符** | 标点、拉丁字母、数字全算字数 |
| `utils/judge/text.py`（判定端） | 同上 | 与写作端同源，但都把标点算进去了 |

两端“同源”并不等于“对”：用户在 2026-11-01 给了硬样本 ——
`“不达标”这个结论本身是假的。` 含标点 15、只计汉字 **12**，全书差 **19.6%**。
5000 字的下限被标点灌了水，判定端还会据此说“达标”。

## 口径

只计 **汉字**（含扩展 A 区与兼容区），不计标点、空白、数字、拉丁字母、Markdown
符号。对话引号、破折号、省略号都不算字 —— 这是“正文实际的字数”。

## 纪律

**只有这一份实现。** 任何地方要数字数，import 本模块；不要再写第四份。
（三处旧函数已改为 re-export，几十个调用点不需要改名。）
"""
from __future__ import annotations

import re

# 汉字：基本区 + 扩展 A + 兼容表意文字。不含标点、假名、谚文。
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def count_cn_words(text) -> int:
    """返回文本里的汉字个数（唯一字数口径）。"""
    if not text:
        return 0
    return len(_HAN.findall(str(text)))
