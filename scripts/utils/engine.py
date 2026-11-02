# -*- coding: utf-8 -*-
"""引擎判定：全仓**唯一**一处回答“配置没写 engine 时算哪个引擎”。

## 为什么需要（2026-10-30）

改造前各模块各自写缺省值，且**答案不一致**：

    llm_client.make_client   cfg.get("engine", "hermes")   ← 实际按子会话跑
    estimate_tokens          cfg.get("engine") or "direct"  ← 预估按直连算
    nfctl.check              ... or "direct"                ← 体检按直连算
    orchestrator             cfg.get("engine")              ← 横幅打 "engine=None"

同一份缺键配置：预估说“直连、按 tokenhub 单价”，实际却拉起 hermes 子会话，
Key 校验、单价、模型、预算口径全都对着**另一个引擎**说话 —— 而且不报错。
静默走错引擎正是本项目最忌讳的那类失败（同族事故：hermes 下 ¥ 记账恒 0）。

## 缺省为什么是 direct（不是 hermes）

- `direct` 是文档里的主引擎；缺 API Key 时它会**当场报错**（可见），
  而 `hermes` 在没装 hermes 的机器上只会给你一串“章节文件未生成”。
- 4 个模块里有 3 个本来就缺省 direct，只有 make_client 是 hermes —— 少数派服从多数。
- 显式写 `engine: hermes` 的配置（含出厂配置）行为**完全不变**，本改动只影响缺键。

## 边界

`resolve_engine` 只判“哪个引擎”，不校验各引擎自己的配置是否齐全
（那由 make_client 的 base_url / api_key 检查负责 —— 一处只做一件事）。
"""
from __future__ import annotations

DEFAULT_ENGINE = "direct"
VALID_ENGINES = ("direct", "hermes")

# 未知取值只提示一次（GUI 会反复调 estimate_tokens，不能每调一次刷一行）
_WARNED = set()


def resolve_engine(cfg):
    """从配置取引擎名（小写、去空白）。缺键/空值 → DEFAULT_ENGINE。

    未知取值（如 `engine: Hermes模式`）按 DEFAULT_ENGINE 执行，并打印一次 WARN ——
    静默回落等于让用户以为“配了就生效”，这里必须出声。
    """
    raw = str(((cfg or {}).get("engine") or "")).strip().lower()
    if not raw:
        return DEFAULT_ENGINE
    if raw in VALID_ENGINES:
        return raw
    if raw not in _WARNED:
        _WARNED.add(raw)
        print("[engine] WARN 未知 engine=%r（支持：%s）→ 按 %s 执行"
              % (raw, " / ".join(VALID_ENGINES), DEFAULT_ENGINE))
    return DEFAULT_ENGINE
