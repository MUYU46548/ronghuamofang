# -*- coding: utf-8 -*-
"""Token/费用记账与预算熔断。

单价表为占位估算，P0 MVP 跑批后按实际账单校准（架构文档 v2 1.4）。
记账主库为 runs.db 的 cost_log 表（db.py），本模块负责计价与预算判定。
"""
from utils.db import RunDB

# 单位：元 / 百万 token（已按 opencode 真实账单校准，非占位）
# 单价来源：opencode 文档（USD/M，汇率 ~7.2 折算）：
#   hy3  in $0.14 / out $0.58 / cache_read $0.035 → ≈ ¥1.0 / ¥4.2
# 计费口径说明：
#   - estimate_cost_yuan 仅按 in/out 计价，未建模 cache_read；
#     实际因提示词缓存命中（system/模板复用）更便宜，当前记账偏保守/偏高，
#     属安全侧误差（不会漏计导致熔断失效），待接入真实用量 API 后改为精确计费。
#   - deepseek-v4-flash / deepseek-v3 已于 2026-08-18 弃用（统一切 hy3），
#     保留条目仅为历史 cost_log 回看兼容；新运行不会再命中。
RATES = {
    "hy3":               {"in": 1.0, "out": 4.2},
    "deepseek-v4-flash": {"in": 1.0, "out": 4.0},   # 仅历史回看兼容，已弃用
    "deepseek-v3":       {"in": 2.0, "out": 8.0},   # 仅历史回看兼容，已弃用
}
DEFAULT_MODEL = "hy3"


def estimate_cost_yuan(tokens_in, tokens_out, model=DEFAULT_MODEL):
    """按模型单价估算单次调用费用（元）。未知模型按 default 计价。"""
    rate = RATES.get(model, RATES[DEFAULT_MODEL])
    return round((tokens_in * rate["in"] + tokens_out * rate["out"]) / 1_000_000, 6)


class CostTracker:
    """预算熔断器：所有费用经由此处记账并判定是否暂停。"""

    def __init__(self, db: RunDB, limit_yuan=300.0, warn_ratio=0.7):
        self.db = db
        self.limit = limit_yuan
        self.warn_ratio = warn_ratio

    @staticmethod
    def estimate_cost_yuan(tokens_in, tokens_out, model=DEFAULT_MODEL):
        """按模型单价估算单次调用费用（元）。"""
        return estimate_cost_yuan(tokens_in, tokens_out, model)

    def record(self, run_id, stage, chapter, tokens_in, tokens_out, model=DEFAULT_MODEL,
               estimated=False):
        """记账并返回预算状态字符串：'ok' | 'warn' | 'pause'。

        estimated=True 表示 token 为估算值（stdout 未解析到真实用量），
        写入 cost_log.estimated 便于审计。
        """
        cost = estimate_cost_yuan(tokens_in, tokens_out, model)
        self.db.log_cost(run_id, stage, chapter, model, tokens_in, tokens_out, cost,
                         estimated=estimated)
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
