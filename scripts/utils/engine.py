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
#: `self`（2026-11-04）：**不调用任何模型**，只产任务书交给外部执行。
#: 它必须在这里算"已知引擎"，否则会被当成拼错的取值：打 WARN、再按 direct 执行
#: —— 也就是"配了 self，实际偷偷按量计费跑模型"，正是本项目最忌讳的静默失真。
VALID_ENGINES = ("direct", "hermes", "self")

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


# ---------------------------------------------------------------- engine: self 的前置拒绝
# 2026-11-04，作者试跑第 19/21 条：“AI 修订也失败了…提交精修也失败了。"
# 根因之一：他为了避开 hermes 十分钟卡死，把引擎切成了 self（只出任务书、不调模型），
# 于是所有“要模型”的按钮必然失败 —— 而失败时给作者看的是给开发者看的那句
# “发起调用的阶段代码走错了分支 —— 应在 orchestrator 的 self 分支里用 emit_tasks”。
# 作者没走错分支，他只是点了按钮。
NEEDS_MODEL_HINT = (
    "当前运行引擎是“只出任务书”（engine: self）—— 这个功能要调用模型，所以在这里用不了。\n"
    "要用它：在“设置 → 运行引擎”切到 direct（直连，按量计费）或 hermes（子会话，走订阅）。\n"
    "不想换引擎也行：self 的全部产出都是任务书（data/state/tasks/self/），"
    "把对应那一份交给外部执行方写，写完重跑即可。"
)


def needs_model_block_reason(cfg):
    """`engine: self` 下“要模型”的端点应被前置拦下并给人话。返回空串 = 放行。

    为什么在入口拦而不是让它抛：`make_client` 在 self 下**故意**抛错
    （防“配了 self 却偷偷跑模型”的静默失真），但那条错误是**开发者视角**的。
    作者看到它只会得出“这软件坏了”。入口这一层负责把它翻成人话 + 指出下一步。
    """
    if resolve_engine(cfg) == "self":
        return NEEDS_MODEL_HINT
    return ""
