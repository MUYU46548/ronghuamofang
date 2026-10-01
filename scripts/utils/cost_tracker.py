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
import ast
import json
import re
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
    # 2026-09-29（随行件2）：kimi-k2.6 是当前 7 个角色（含 writer）的主力，
    # 但刊例里原先只有 kimi-k3 → 每次记账都走 "未知模型回退默认价 + WARN"，
    # 09-28 冒烟账单的绝对值因此不可信（冒烟报告「下一步②」也列了这条）。
    # ⚠️ 下列数值为**占位**（与 default 角色回退价同值，好在拿到官方价前不改变既有
    # 账单口径、只是不再刷 WARN）；**拿到 TokenHub 官方刊例价后请更新**。
    "kimi-k2.6":          {"in": 1.0, "out": 4.2, "cache_read": 0.2},
    "kimi-k3":            {"in": 20.0, "out": 100.0, "cache_read": 2.0},
    # 美团 LongCat（2026-10-01 预置）。开放平台以**免费额度**为主（注册赠千万级
    # token，部分模型另有每日额度），控制台未公布刊例价 → 先按 default 角色价占位，
    # 保证不再刷「未知模型回退」WARN。
    # ⚠️ 免费额度期实际扣费为 0 —— 面板上这两个模型显示的是**虚拟费用**
    # （整簿「估算口径」，与套餐/免费额度无关，系统目前不区分计费模式）。
    # 拿到官方价别改这里：走「成本 → 定价 → 📋 批量导入」，写 data/state/cost_rates.json
    # （用户数据、不进 git、优先级高于本表），定价变动时改一次就行。
    "LongCat-2.0":        {"in": 1.0, "out": 4.2, "cache_read": 0.2},
    "LongCat-2.5-Preview": {"in": 1.0, "out": 4.2, "cache_read": 0.2},
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


# ---------------------------------------------------------------------------
# 定价批量导入（2026-09-29）
#
# 一次粘贴整张价格表 → 解析 → 预览 → 确认，落进自定义定价 data/state/cost_rates.json。
# 分工刻意切干净，三层各管一件事（GUI 端点与 CLI 向导共用，判据只有一份）：
#   parse_rates_text    「文本 → 结构」，不含任何业务语义；
#   plan_rates_import   「合并策略」：cache 缺省怎么办、新增还是覆盖、与源码刊例价的关系；
#   upsert_custom_rates 「落盘」：只覆盖同名条目，**绝不删其他**（不是整体替换）。
#
# 纪律：**跳过任何一行都必须留痕** —— 识别不了的进 warnings，语义错误进 errors，
# 不存在「悄悄少导入几条」的可能。
# ---------------------------------------------------------------------------

_RATE_KEYS = ("in", "out", "cache_read")

# 数值字段：允许货币符、单位、"每百万 token" 这类尾巴（从账单页复制常见）
_NUM_RE = re.compile(
    r"^[¥$￥]?\s*(-?\d+(?:\.\d+)?)\s*"
    r"(?:元|圆|块|美元|rmb|cny|usd)?\s*"
    r"(?:(?:/|每)\s*(?:百万(?:\s*tokens?)?|m(?:\s*tokens?)?|1m|tokens?))?$",
    re.IGNORECASE,
)

# JSON / 字面量里的字段别名（中英文都认）
_KEY_ALIASES = {
    "in": "in", "input": "in", "input_price": "in", "prompt": "in",
    "输入": "in", "输入价": "in", "输入价格": "in", "输入单价": "in",
    "out": "out", "output": "out", "output_price": "out", "completion": "out",
    "输出": "out", "输出价": "out", "输出价格": "out", "输出单价": "out",
    "cache": "cache_read", "cache_read": "cache_read", "cacheread": "cache_read",
    "cache_price": "cache_read", "cached_input": "cache_read",
    "缓存": "cache_read", "缓存价": "cache_read", "缓存价格": "cache_read",
    "缓存读取": "cache_read", "缓存命中": "cache_read",
}
_NAME_KEYS = ("model", "name", "模型", "模型名", "模型名称", "model_id", "id")
# 表头关键词（注意别放 "in"/"out" —— 会误伤含这两个子串的英文词）
_HEADER_HINTS = ("模型", "名称", "输入", "输出", "缓存", "价格", "单价",
                 "model", "name", "input", "output", "cache", "price")
_FREE_WORDS = ("免费", "free", "0（免费）", "免费额度", "免费期")

# 「/ 百万 tokens」「元 / 百万」这类单位尾巴。数值本身不含单位信息，先整段剥掉再切分，
# 否则 `¥1.00 / M tokens` 会被空白切成 4 个字段、数值段判定直接失效。
_UNIT_TAIL_RE = re.compile(
    r"\s*(?:元|圆|块|美元|rmb|cny|usd)?\s*"
    r"(?:/\s*(?:百万(?:\s*tokens?)?|m(?:\s*tokens?)?|1m|tokens?))",
    re.IGNORECASE,
)
# 「8.0 元」这种数字与货币单位之间夹空格的写法
_CURRENCY_GAP_RE = re.compile(r"(\d)\s+(元|圆|块|美元|rmb|cny|usd)(?![a-z])", re.IGNORECASE)


def _strip_thousands(tok):
    """去掉千分位逗号：1,000.50 → 1000.50。"""
    return re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", tok)


def _extract_number(tok):
    """把一个字段解析成数值；**不是数值就返回 None，不做猜测**。"""
    t = (tok or "").strip()
    if not t:
        return None
    low = t.lower()
    if low in _FREE_WORDS:
        return 0.0
    m = _NUM_RE.match(_strip_thousands(t))
    if m:
        return float(m.group(1))
    # 「0 元（免费）」「免费/0」这类混写：仅当整串只剩一个数字（或没有数字）时才认
    if "免费" in t or "free" in low:
        nums = re.findall(r"-?\d+(?:\.\d+)?", _strip_thousands(t))
        if len(nums) == 1:
            return float(nums[0])
        if not nums:
            return 0.0
    return None


def _merge_thousands(parts):
    """把被逗号拆散的千分位数字拼回去（1 / 234 / 567.89 → 1,234 / 567.89）。"""
    out = []
    for p in parts:
        if out and re.fullmatch(r"\d{3}(?:\.\d+)?", p) and re.fullmatch(r"\d+", out[-1]):
            out[-1] = out[-1] + "," + p
        else:
            out.append(p)
    return out


def _split_fields(line):
    """切分一行。优先空白/制表/分号/竖线/顿号；这样切出的数值不够 2 个才试逗号。"""
    parts = [p for p in re.split(r"[\s;|、]+", line) if p]
    nums = sum(1 for p in parts if _extract_number(p) is not None)
    if nums < 2 and "," in line:
        alt = _merge_thousands([p for p in re.split(r",+", line) if p])
        if sum(1 for p in alt if _extract_number(p) is not None) > nums:
            return alt
    return parts


def _table_entries(raw, errors, warnings):
    """逐行解析表格写法。返回 [(name, {in,out,cache_read?})]。"""
    out = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        # 行内注释：仅当 # / // 出现在行首或空白之后才截断（模型名里不会含这两个）
        s = re.sub(r"(?:^|\s)#.*$", "", s).strip()
        s = re.sub(r"(?:^|\s)//.*$", "", s).strip()
        if not s:
            continue
        # 单位尾巴先剥掉（「¥1.00 / M tokens」→「¥1.00」；「8.0 元 / 百万」→「8.0」），
        # 否则会被空白切成多个字段、数值段判定失效。
        s = _UNIT_TAIL_RE.sub("", s)
        s = _CURRENCY_GAP_RE.sub(r"\1\2", s).strip()
        if not s:
            continue
        parts = _split_fields(s)
        # 从行尾往左取「连续数值段」= 价格字段；剩下的都算模型名
        # （故 "DeepSeek V4.1 Flash 1.0 4.2" 也能正确把 V4.1 归回名字）
        idx = len(parts) - 1
        while idx >= 0 and _extract_number(parts[idx]) is not None:
            idx -= 1
        nums = [_extract_number(p) for p in parts[idx + 1:]]
        name = " ".join(parts[:idx + 1]).strip()
        if not nums:
            hint = "疑似表头/说明" if any(x in s.lower() for x in _HEADER_HINTS) else "无数字"
            warnings.append("第 " + str(lineno) + " 行：跳过（" + hint + "）：" + s[:40])
            continue
        if not name:
            errors.append("第 " + str(lineno) + " 行：缺少模型名（价格前没有名称）：" + s[:50])
            continue
        if len(nums) < 2:
            errors.append("第 " + str(lineno) + " 行：只解析出 1 个数值，"
                          "至少需要「输入价 输出价」：" + s[:50])
            continue
        if len(nums) > 3:
            warnings.append("第 " + str(lineno) + " 行：" + name + " 有 " + str(len(nums)) +
                            " 个数值，只取前 3 个（输入/输出/缓存）")
        spec = {"in": nums[0], "out": nums[1]}
        if len(nums) >= 3:
            spec["cache_read"] = nums[2]
        out.append((name, spec))
    return out


def _json_entries(data, errors):
    """把 JSON / Python 字面量归一成 [(name, spec)]。"""
    if isinstance(data, dict) and "rates" in data and isinstance(data["rates"], (dict, list)):
        data = data["rates"]
    out = []
    if isinstance(data, list):
        for i, item in enumerate(data, 1):
            if not isinstance(item, dict):
                errors.append("第 " + str(i) + " 项不是对象，已跳过")
                continue
            name = ""
            for k in _NAME_KEYS:
                if k in item and str(item[k]).strip():
                    name = str(item[k]).strip()
                    break
            if not name:
                errors.append("第 " + str(i) + " 项缺少模型名（model / name / 模型）")
                continue
            out.append((name, item))
        return out
    if isinstance(data, dict):
        # 单条形态：{"model": "x", "in": 1, "out": 2}（含中文键 {"模型": .., "输入": ..}）
        for k in _NAME_KEYS:
            v = data.get(k)
            if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip():
                return [(str(v).strip(), data)]
        for name, spec in data.items():
            out.append((str(name).strip(), spec))
        return out
    errors.append("顶层必须是对象或数组（当前为 " + type(data).__name__ + "）")
    return out


def _coerce_spec(name, spec, errors, where):
    """把一条定价的各种写法归一为 {"in", "out", "cache_read"?}；失败返回 None。"""
    vals = {}

    def _num(raw):
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        return _extract_number(str(raw))

    def _put(key, raw):
        num = _num(raw)
        if num is None:
            errors.append(where + "：" + name + " 的 " + key + " 不是数值（" + repr(raw) + "）")
            return
        if num < 0:
            errors.append(where + "：" + name + " 的 " + key + " 为负数（" + str(num) + "）")
            return
        vals[key] = num

    if isinstance(spec, dict):
        for k, v in spec.items():
            key = _KEY_ALIASES.get(str(k).strip().lower())
            if key is not None:
                _put(key, v)
    elif isinstance(spec, (list, tuple)):
        for key, raw in zip(_RATE_KEYS, spec):
            _put(key, raw)
    elif isinstance(spec, str):
        parts = [p for p in re.split(r"[^\d.\-¥$￥]+", spec) if p.strip()]
        for key, raw in zip(_RATE_KEYS, parts):
            _put(key, raw)
    else:
        errors.append(where + "：" + name + " 的取值类型不支持（" + type(spec).__name__ + "）")
        return None

    if "in" not in vals or "out" not in vals:
        errors.append(where + "：" + name + " 缺少输入价或输出价")
        return None
    return vals


def parse_rates_text(text):
    """解析粘贴的定价文本。返回 (parsed, errors, warnings, fmt)。

    自动识别三种写法：
      ① JSON：{"kimi-k2.6": {"in": 1, "out": 4.2, "cache_read": 0.2}}
              或 [{"model": "kimi-k2.6", "in": 1, "out": 4.2}]
      ② Python 字面量：从 cost_tracker.py 的 RATES 块整段复制（`"名字": {"in": ..}`）
      ③ 表格行：每行「模型名 输入价 输出价 [缓存价]」，
               分隔符支持 空白 / 制表 / 分号 / 竖线 / 顿号 / 逗号；`#` `//` 注释与空行跳过
    """
    parsed, errors, warnings = {}, [], []
    raw = (text or "").strip()
    if not raw:
        return {}, ["内容为空：没有可导入的定价"], [], "empty"

    entries = None
    fmt = "table"
    if raw[0] in "{[":
        try:
            entries = _json_entries(json.loads(raw), errors)
            fmt = "json"
        except ValueError:
            entries = None                      # 也许只是单引号的 Python 写法，交给下面
    if entries is None:
        try:
            data = ast.literal_eval("{" + raw.rstrip().rstrip(",") + "}")
            if isinstance(data, dict):
                entries = _json_entries(data, errors)
                fmt = "python"
        except (ValueError, SyntaxError):
            entries = None
    if entries is None:
        entries = _table_entries(raw, errors, warnings)
        fmt = "table"

    where = {"json": "JSON", "python": "字面量", "table": "表格"}.get(fmt, fmt)
    for name, spec in entries:
        if not name:
            errors.append("存在空模型名，已跳过")
            continue
        if name in parsed:
            warnings.append(name + "：同一次粘贴里重复出现，后一条覆盖前一条")
        vals = _coerce_spec(name, spec, errors, where)
        if vals:
            parsed[name] = vals
    if not parsed and not errors:
        errors.append("未解析出任何定价条目（请检查粘贴内容）")
    return parsed, errors, warnings, fmt


def plan_rates_import(text):
    """解析 + 生成导入计划（**不落盘**，供 GUI 预览确认）。

    合并策略在这一层定，不在解析层：
    - 语义是 **upsert**：只覆盖粘贴里出现的模型，**绝不动其他条目**（不是整体替换）；
    - 缺 cache_read 时 **沿用该模型现有缓存价并告警** —— 静默置 0 会低估缓存命中成本
      （cache 单价低于 in 价，置 0 等于把命中部分当成不花钱），账单会偏乐观；
      现有条目也没有缓存价时才置 0，同样告警。
    """
    parsed, errors, warnings, fmt = parse_rates_text(text)
    merged = get_merged_rates()
    custom = load_custom_rates()
    plan = []
    for name, entry in parsed.items():
        cur = custom.get(name) or merged.get(name) or {}
        new = dict(entry)
        if "cache_read" not in new:
            if cur.get("cache_read"):
                new["cache_read"] = float(cur["cache_read"])
                warnings.append(name + "：未给缓存价，沿用现有的 " + str(cur["cache_read"]))
            else:
                new["cache_read"] = 0.0
                warnings.append(name + "：未给缓存价，按 0 计（该模型现有条目也没有缓存价）")
        if name in custom:
            action = "update"
        elif name in RATES:
            action = "override"
        else:
            action = "new"
        plan.append({
            "model": name,
            "action": action,
            "from": {k: cur.get(k) for k in _RATE_KEYS} if cur else None,
            "to": new,
        })
    return {
        "ok": not errors,
        "format": fmt,
        "count": len(plan),
        "plan": plan,
        "errors": errors,
        "warnings": warnings,
        "custom_total": len(custom),
    }


def upsert_custom_rates(entries):
    """把 {模型: {in,out,cache_read}} 合并进自定义定价。返回 (最终条数, 备份路径或 None)。

    **只覆盖同名条目，不删其他** —— 与面板「保存」的整体替换语义不同（那是显式全量提交）。
    落盘前把原文件另存为 cost_rates.json.bak（固定名，始终是「上一次」），可随时回退。
    """
    custom = load_custom_rates()
    backup = None
    if CUSTOM_RATES_PATH.exists():
        backup = CUSTOM_RATES_PATH.with_suffix(".json.bak")
        try:
            backup.write_text(CUSTOM_RATES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            backup = None
    for name, rate in (entries or {}).items():
        custom[str(name)] = {k: float(v) for k, v in rate.items() if k in _RATE_KEYS}
    save_custom_rates(custom)
    return len(custom), (str(backup) if backup else None)


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

    provider == "hermes" 恒返回 0（2026-10-01 用户定调）：agent 模式走订阅流量，
    无按量 ¥ 成本 —— token 照记（run_task 返回真实 usage），但预算熔断对 hermes
    引擎不生效是**刻意语义**（预算是 direct 引擎的按量保护），orchestrator 启动时
    会显式提示，不做「按任务文件大小 × 刊例价」的虚构记账。
    """
    if provider == "hermes":
        return 0.0
    rate, _ = resolve_rate(model, provider, role)
    cache_rate = rate.get("cache_read", 0)
    # ⚠️ 缓存命中的 token **已经在 tokens_in 里**了（OpenAI / TokenHub / DeepSeek /
    # Anthropic 口径一致：`prompt_tokens_details.cached_tokens` 是 `prompt_tokens`
    # 的**子集**）。所以必须先把它从全价部分扣掉，否则命中部分被计两次
    # （全价 + 缓存价）。
    # 危害不是"数字难看"：实测 deepseek-v4-flash 缓存占比可达 ~95%，
    # 输入成本被高估近一倍 → `CostTracker.status()` 按 sum_cost 判定 →
    # **预算熔断提前触发**（本该跑完的书被拦腰截断）。
    n_cache = max(0, int(cache_read or 0))
    billable_in = max(0, int(tokens_in or 0) - n_cache)
    return round((billable_in * rate["in"] + tokens_out * rate["out"]
                  + n_cache * cache_rate) / 1_000_000, 6)


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
