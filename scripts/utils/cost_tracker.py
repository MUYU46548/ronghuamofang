# -*- coding: utf-8 -*-
"""Token/费用记账与预算熔断。

单价表为刊例价（元/百万 token）。记账主库为 runs.db 的 cost_log 表（db.py），
本模块负责计价与预算判定。

计价口径（P2 引擎无关化，2026-09-09）：
- 按账单语义计价：直连 provider 的输入不产生 cache_read 溢价的规则不同，
  统一按 (model_id in 单价 + provider 为 hermes 时走角色计价) 处理；
- hermes 引擎按模型角色（default/writer/checker）计价（子会话 stdout 无模型名），
  角色单价取 model.rates 覆盖，缺省回退 DEFAULT_ROLE_RATES[角色] 再回退 default 角色；
- 直连引擎按真实 model_id 计价（usage 来自 API 返回，无估算）；
- 未知模型自动回退 default 角色单价并打印告警（保守侧，熔断不会失效）。
"""
from utils.db import RunDB

# ---- 刊例价（元/百万 token）----
# 官方核实（腾讯云计费概述 2026-06 版）：
#   hunyuan-a13b  in 0.5 / out 2.0
#   hunyuan-lite  免费（0）
# hy3：opencode 实测 USD/M 汇率 ~7.2 折算（in 0.14/out 0.58/cache_read 0.035）
RATES = {
    "hunyuan-a13b":       {"in": 0.5, "out": 2.0},   # 官方核实 2026-06
    "hunyuan-lite":       {"in": 0.0, "out": 0.0},   # 官方免费
    "hy3":                {"in": 1.0, "out": 4.2},   # opencode 折算
    # 仅历史 cost_log 回看兼容（2026-08-18 已统一切 hy3，新运行不命中）
    "deepseek-v4-flash":  {"in": 1.0, "out": 4.0},
    "deepseek-v3":        {"in": 2.0, "out": 8.0},
}
# turbos 等未录入模型 → 回退 default 角色单价（保守），跑批前按控制台账单补录

# Hermes 引擎按角色计价（hy3 单价；角色→单价）
DEFAULT_ROLE_RATES = {
    "default": {"in": 1.0, "out": 4.2},
    "writer":  {"in": 1.0, "out": 4.2},
    "checker": {"in": 1.0, "out": 4.2},
}

DEFAULT_MODEL = "hunyuan-a13b"
DEFAULT_ROLE = "default"


def resolve_rate(model=None, provider=None, role=None):
    """解析计价条目。返回 (rate dict, display_model)。"""
    if provider == "hermes":
        rates_map = ((role or {}).get("rates") if isinstance(role, dict) else None)
        r = rates_map or DEFAULT_ROLE_RATES.get(role) or DEFAULT_ROLE_RATES[DEFAULT_ROLE]
        return dict(r), "hermes:" + (role or DEFAULT_ROLE)
    m = model or DEFAULT_MODEL
    if m in RATES:
        return dict(RATES[m]), m
    print("[cost_tracker] WARN 未知模型计价回退默认（跑批前请补录 RATES）: " + m)
    return dict(DEFAULT_ROLE_RATES[DEFAULT_ROLE]), m


def estimate_cost_yuan(tokens_in, tokens_out, model=None, provider=None, role=None):
    """按计价条目估算单次调用费用（元）。兼容旧签名 estimate_cost(t, t, model)。"""
    rate, _ = resolve_rate(model, provider, role)
    return round((tokens_in * rate["in"] + tokens_out * rate["out"]) / 1_000_000, 6)


class CostTracker:
    """预算熔断器：所有费用经由此处记账并判定是否暂停。"""

    def __init__(self, db: RunDB, limit_yuan=300.0, warn_ratio=0.7):
        self.db = db
        self.limit = limit_yuan
        self.warn_ratio = warn_ratio

    @staticmethod
    def estimate_cost_yuan(tokens_in, tokens_out, model=None, provider=None, role=None):
        return estimate_cost_yuan(tokens_in, tokens_out, model, provider, role)

    def charge_cost(self, run_id, stage, chapter, result):
        """按 run_task 返回 dict 记账（model/provider/role 取自结果元数据）。

        返回预算状态字符串：'ok' | 'warn' | 'pause'。
        """
        model = result.get("model") or DEFAULT_MODEL
        provider = result.get("provider") or "hermes"
        role = result.get("model_key") or DEFAULT_ROLE
        cost = estimate_cost_yuan(result.get("tokens", 0), result.get("tokens_out", 0),
                                  model=model, provider=provider, role=role)
        self.db.log_cost(run_id, stage, chapter, model, result.get("tokens", 0),
                         result.get("tokens_out", 0), cost,
                         estimated=1 if result.get("estimated") else 0)
        return self.status(run_id)[0]

    def record(self, run_id, stage, chapter, tokens_in, tokens_out, model=None,
               estimated=False, provider=None, role=None):
        """兼容旧签名：按 (tokens_in, tokens_out) 记账。"""
        cost = estimate_cost_yuan(tokens_in, tokens_out, model=model,
                                  provider=provider, role=role)
        self.db.log_cost(run_id, stage, chapter, model or DEFAULT_MODEL, tokens_in,
                         tokens_out, cost, estimated=1 if estimated else 0)
        return self.status(run_id)[0]

    def spent(self, run_id=None, stage=None):
        return self.db.sum_cost(run_id, stage)

    def status(self, run_id=None):
        """返回 ('ok'|'warn'|'pause', spent_yuan)。"""
        spent = self.db.sum_cost(run_id)
        if spent >= self.limit:
            return "pause", spent
        if spent >= self.limit * self.warn_ratio:
            return "warn", spent
        return "ok", spent
