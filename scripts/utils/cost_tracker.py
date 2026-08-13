# -*- coding: utf-8 -*-
"""Token/费用记账与预算熔断。

单价表为占位估算，P0 MVP 跑批后按实际账单校准（架构文档 v2 1.4）。
记账主库为 runs.db 的 cost_log 表（db.py），本模块负责计价与预算判定。
"""
from utils.db import RunDB

# 单位：元 / 百万 token（占位单价，MVP 校准后更新）
RATES = {
    "deepseek-v4-flash": {"in": 1.0, "out": 4.0},
    "deepseek-v3":       {"in": 2.0, "out": 8.0},
}
DEFAULT_MODEL = "deepseek-v4-flash"


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
