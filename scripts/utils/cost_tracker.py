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
import json
from pathlib import Path

from utils.db import RunDB

# ---- 刊例价（元/百万 token）----
# 官方核实（腾讯云计费概述 2026-06 版）：
#   hunyuan-a13b  in 0.5 / out 2.0
#   hunyuan-lite  免费（0）
# hy3：opencode 实测 USD/M 汇率 ~7.2 折算（in 0.14/out 0.58/cache_read 0.035）
# 价格向导：python scripts/price_wizard.py 可交互式增删改（不影响运行中的流水线）
RATES = {
    "hunyuan-a13b":       {"in": 0.5, "out": 2.0},   # 混元官方 2026-06
    "hunyuan-lite":       {"in": 0.0, "out": 0.0},   # 混元免费
    # TokenHub 广州（模型价格 2026-09-08，元/百万）
    "deepseek-v4-flash":  {"in": 1.0, "out": 2.0, "cache_read": 0.2},
    "deepseek-v4-pro":    {"in": 12.0, "out": 24.0, "cache_read": 1.0},
    "glm-5.3":            {"in": 8.0, "out": 28.0, "cache_read": 2.0},
    "glm-5.1":            {"in": 4.0, "out": 18.0, "cache_read": 1.0},
    "glm-5":              {"in": 6.0, "out": 22.0, "cache_read": 1.5},  # 32k+ 档保守计价（0-32k 档 4/18）；2026-10-09 下线
    "glm-5.3-flash":      {"in": 0.8, "out": 2.8, "cache_read": 0.23},  # 限时半价至 09-10，目录 1.6/5.6
    "qwen3.5-flash":      {"in": 0.5, "out": 1.5, "cache_read": 0.1},   # 占位价，待确认实际单价
    # 临时开发模型（2026-09-23 起，TokenHub 免费额度期测试用，**用完即弃**，
    # 不代表实际选型）：TokenHub 未查到刊例价 → 按 default 角色价保守占位；
    # 免费额度期实际扣费为 0。实测支持前缀缓存（第 3 次 2094/2190 ≈ 95.6%）。
    "minimax-m2.7":       {"in": 1.0, "out": 4.2, "cache_read": 0.2},
    "kimi-k3":            {"in": 20.0, "out": 100.0, "cache_read": 2.0},
    "hy3":                {"in": 1.0, "out": 4.0, "cache_read": 0.25},  # TokenHub 目录价（旧 opencode 折算 1.0/4.2 供历史对账）
    # 仅历史 cost_log 回看兼容
    "deepseek-v3":        {"in": 2.0, "out": 8.0},
}
# turbos 等未录入模型 → 回退 default 角色单价（保守），跑批前按控制台账单补录

# GUI 定价编辑器存储路径（与 RATES 合并生效，不修改源码）
CUSTOM_RATES_PATH = Path(__file__).resolve().parent.parent / "data" / "state" / "cost_rates.json"


def load_custom_rates():
    """加载 GUI 定价编辑器保存的自定义单价（data/state/cost_rates.json）。"""
    if not CUSTOM_RATES_PATH.exists():
        return {}
    try:
        data = json.loads(CUSTOM_RATES_PATH.read_text(encoding="utf-8"))
        return data.get("rates", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_custom_rates(rates):
    """保存自定义单价到 data/state/cost_rates.json（GUI 定价编辑器用）。"""
    CUSTOM_RATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_RATES_PATH.write_text(
        json.dumps({"rates": rates}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_merged_rates():
    """返回 RATES + 自定义覆盖的合并视图（标注来源）。"""
    merged = dict(RATES)
    custom = load_custom_rates()
    for name, rate in custom.items():
        merged[name] = rate
    return merged


# Hermes 引擎按角色计价（hy3 单价；角色→单价）
DEFAULT_ROLE_RATES = {
    "default": {"in": 1.0, "out": 4.2},
    "writer":  {"in": 1.0, "out": 4.2},
    "checker": {"in": 1.0, "out": 4.2},
}

DEFAULT_MODEL = "hunyuan-a13b"
DEFAULT_ROLE = "default"


def resolve_rate(model=None, provider=None, role=None):
    """解析计价条目。返回 (rate dict, display_model)。

    合并优先级：自定义定价（GUI 编辑器）> RATES（源码刊例价）> 默认角色单价。
    """
    if provider == "hermes":
        rates_map = ((role or {}).get("rates") if isinstance(role, dict) else None)
        r = rates_map or DEFAULT_ROLE_RATES.get(role) or DEFAULT_ROLE_RATES[DEFAULT_ROLE]
        return dict(r), "hermes:" + (role or DEFAULT_ROLE)
    m = model or DEFAULT_MODEL
    merged = get_merged_rates()
    if m in merged:
        return dict(merged[m]), m
    print("[cost_tracker] WARN 未知模型计价回退默认（跑批前请补录 RATES）: " + m)
    return dict(DEFAULT_ROLE_RATES[DEFAULT_ROLE]), m


def estimate_cost_yuan(tokens_in, tokens_out, model=None, provider=None, role=None, cache_read=0):
    """按计价条目估算单次调用费用（元）。

    cache_read: 缓存命中 token 数。RATES 中有 cache_read 字段时按缓存价计入，
    没有则按 0（保守不计，实际成本比估算低，熔断不会失灵）。
    """
    rate, _ = resolve_rate(model, provider, role)
    cache_rate = rate.get("cache_read", 0)
    return round((tokens_in * rate["in"] + tokens_out * rate["out"] + cache_read * cache_rate) / 1_000_000, 6)


class CostTracker:
    """预算熔断器：所有费用经由此处记账并判定是否暂停。"""

    def __init__(self, db: RunDB, limit_yuan=300.0, warn_ratio=0.7):
        self.db = db
        self.limit = limit_yuan
        self.warn_ratio = warn_ratio

    @staticmethod
    def estimate_cost_yuan(tokens_in, tokens_out, model=None, provider=None, role=None, cache_read=0):
        return estimate_cost_yuan(tokens_in, tokens_out, model, provider, role, cache_read=cache_read)

    def charge_cost(self, run_id, stage, chapter, result):
        """按 run_task 返回 dict 记账（model/provider/role 取自结果元数据）。

        返回预算状态字符串：'ok' | 'warn' | 'pause'。
        """
        model = result.get("model") or DEFAULT_MODEL
        provider = result.get("provider") or "hermes"
        role = result.get("model_key") or DEFAULT_ROLE
        cost = estimate_cost_yuan(result.get("tokens", 0), result.get("tokens_out", 0),
                                  model=model, provider=provider, role=role,
                                  cache_read=result.get("cache_read", 0))
        self.db.log_cost(run_id, stage, chapter, model, result.get("tokens", 0),
                         result.get("tokens_out", 0), cost,
                         estimated=1 if result.get("estimated") else 0,
                         cache_read=result.get("cache_read", 0))
        return self.status(run_id)[0]

    def record(self, run_id, stage, chapter, tokens_in, tokens_out, model=None,
               estimated=False, provider=None, role=None, cache_read=0):
        """兼容旧签名：按 (tokens_in, tokens_out, cache_read) 记账。"""
        cost = estimate_cost_yuan(tokens_in, tokens_out, model=model,
                                  provider=provider, role=role, cache_read=cache_read)
        self.db.log_cost(run_id, stage, chapter, model or DEFAULT_MODEL, tokens_in,
                         tokens_out, cost, estimated=1 if estimated else 0,
                         cache_read=cache_read)
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
