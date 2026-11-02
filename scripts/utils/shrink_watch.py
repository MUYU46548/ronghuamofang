# -*- coding: utf-8 -*-
"""字数缩水记录（2026-11-02，不可用清单第 7 条）。

## 要治的病

用户实测：`raw/03.md` = 7184 字 → `refined/03.md` = **2474 字**；
`raw/05.md` = 7024 → `refined/05.md` = 2257。**写出来的被下游压回去，而且不被告知。**

这条只在判定报告里以数字出现过，产品路径上看不见 —— 成品就是短的，一声不响。

## 口径

- `ratio = (before - after) / before`，正数 = 变短；
- 超 `10%` 就记一条（清单口径）。**不拦**：润色本来就该删水，拦掉等于不许修；
  记下来是为了让界面说得出口"这几章被删掉了 37%"。
- stage6 另有 ±20% 铁律（超了直接判失败），那条不动 —— 这里是 10%~20% 的
  灰区，以前完全静默。

## 为什么放在 progress 而不是只打日志

日志没人看。`/state` 透传后界面能标红，才算"看见了"。
"""
import time

SHRINK_WARN_RATIO = 0.10


def ratio_of(before, after):
    """(before - after) / before，正数表示变短。数据不合法 → 0。"""
    try:
        before, after = int(before), int(after)
    except (TypeError, ValueError):
        return 0.0
    if before <= 0 or after < 0:
        return 0.0
    return (before - after) / before


def note(progress, stage_no, n, before, after, threshold=SHRINK_WARN_RATIO):
    """缩减超阈值就记进 progress。返回 detail（没超 → None）。

    `progress` 传 None 时只计算不记录（给 CLI 单跑用）。
    """
    ratio = ratio_of(before, after)
    if ratio <= threshold:
        return None
    detail = {
        "before": int(before), "after": int(after),
        "ratio": round(ratio, 3),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if progress is None:
        return detail
    try:
        st = progress.data.setdefault("stages", {}).setdefault(str(stage_no), {})
        st.setdefault("shrink", {})[str(n)] = detail
        progress.save()
    except Exception:                                        # noqa: BLE001
        pass
    return detail


def describe(detail):
    """一句人话，给日志/界面用。"""
    if not detail:
        return ""
    return ("字数被压掉 %d%%（%d → %d）"
            % (round(detail["ratio"] * 100), detail["before"], detail["after"]))
