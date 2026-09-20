# -*- coding: utf-8 -*-
"""模型准入登记处（NovelForge 模型白名单纪律的唯一实现点）。

## 为什么有这个模块

2026-09-19 工程审查发现（S8）：`config/system.yaml` 的
`providers.<id>.available_models` 以及 `data/state/fetched_models.json`
（含 `_manual`）这两份白名单数据**确实存在**，且 `fetched_models.json` 作为
载体被 4 处路径常量引用、6 处代码读写——但**全部消费点都是「展示或缓存维护」，
不存在任何读取-判定回路**。结果是 `AGENTS.md` 的「模型白名单纪律」
（未经确认不得指定/更换付费模型）**在代码中从未实现**：任意格式合法的模型名
都能通过 `/models/switch` 与 `/models/add` 落盘并生效。

本模块就是那条「回路」：把已存在的白名单数据首次接进判定路径，
让写入层在落盘前做**两级校验**：

  ① 格式合法性（always）—— 非空、无路径分隔符、无控制字符、长度上限。
     这是原先 `/models/switch` **完全缺失**的一级（`/models/add` 有）。
  ② 白名单归属（可配）—— 模型名须属于「已登记集合」：
     `config/system.yaml` 的任一 provider 的 `available_models`
     ∪ 默认 provider 的 `fallback`
     ∪ `fetched_models.json` 的 `_manual`（**用户显式手动添加，即视为已确认**）
     ∪ `fetched_models.json` 各 provider 的 `models`（动态拉取到的真实可选模型）

## 设计取舍

- **默认 strict=True 但带逃生门**：`strict=False` 时只做格式校验并在响应里
  回报 `whitelist_checked=false`。原因是本机单人应用不该把用户锁死——
  供应商可能随时上架新模型而缓存未刷新。
- **`_manual` 是用户显式动作**，所以 `/models/add` 写入后该模型立即被准入，
  不需要二次确认——这既满足「未经确认不得指定付费模型」（用户手动添加就是确认），
  又避免把 GUI「手动添加」流程变成两步。
- 本模块**零外部依赖**（只用 stdlib + yaml），可被 nf_api / 未来 CLI 共用。
"""

from pathlib import Path

# 模型名格式：允许字母数字与 . _ - / : @（厂商常有 vendor/name、name:tag 形式），
# 显式禁止路径分隔符反斜杠与空白/控制字符。
import re as _re

MODEL_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]*$")
MODEL_NAME_MAX = 120

CACHE_REL = "data/state/fetched_models.json"


class ModelNameInvalid(ValueError):
    """格式非法（第 ① 级）。"""


class ModelNotAllowed(PermissionError):
    """格式合法但不在白名单内（第 ② 级）。"""


def validate_format(name):
    """第 ① 级：格式合法性。返回规范化后的名字，非法则抛 ModelNameInvalid。"""
    name = str(name or "").strip()
    if not name:
        raise ModelNameInvalid("模型名为空")
    if len(name) > MODEL_NAME_MAX:
        raise ModelNameInvalid(f"模型名过长（>{MODEL_NAME_MAX} 字符）")
    if not MODEL_NAME_RE.match(name):
        raise ModelNameInvalid(
            "模型名不合法（须以字母或数字开头，仅含字母数字与 . _ - : / @ +，"
            "不得含空格或路径分隔符）")
    return name


def load_registered_models(cfg, root):
    """收集「已登记集合」：config 白名单 ∪ fetched 缓存。

    返回 (allowed: set[str], sources: dict[str, list[str]])
    sources 用于响应里回报来源分布，便于 GUI / 排障看出这个模型为什么被准入。
    """
    allowed = set()
    sources = {}

    providers = (cfg or {}).get("providers") or {}
    for pid, prov in providers.items():
        if not isinstance(prov, dict):
            continue
        for key in ("available_models", "fallback"):
            for m in (prov.get(key) or []):
                m = str(m).strip()
                if m:
                    allowed.add(m)
                    sources.setdefault(m, []).append(f"config:{pid}.{key}")

    cache_path = Path(root) / CACHE_REL
    cache = {}
    if cache_path.exists():
        try:
            import json
            cache = json.loads(cache_path.read_text(encoding="utf-8")) or {}
        except Exception:                                      # noqa: BLE001
            cache = {}

    for m in (cache.get("_manual") or []):
        m = str(m).strip()
        if m:
            allowed.add(m)
            sources.setdefault(m, []).append("fetched:_manual(用户手动添加)")

    for key, info in (cache or {}).items():
        if key.startswith("_") or not isinstance(info, dict):
            continue
        for m in (info.get("models") or []):
            m = str(m).strip()
            if m:
                allowed.add(m)
                sources.setdefault(m, []).append(f"fetched:{key}")

    return allowed, sources


def check_model(name, cfg, root, strict=True):
    """两级校验总入口。

    返回 dict：{ok, name, allowed, checked, reason, suggestions}
    - allowed: 该名字是否在已登记集合内
    - checked: 是否执行了第 ② 级（strict=False 时为 False）
    - suggestions: 不在白名单时，给出近似候选（便于用户纠错）
    """
    try:
        name = validate_format(name)
    except ModelNameInvalid as e:
        return {"ok": False, "name": str(name or "").strip(), "allowed": False,
                "checked": False, "reason": "format: " + str(e), "suggestions": []}

    allowed_set, _sources = load_registered_models(cfg, root)
    if name in allowed_set:
        return {"ok": True, "name": name, "allowed": True, "checked": True,
                "reason": "", "suggestions": []}

    if not strict:
        return {"ok": True, "name": name, "allowed": False, "checked": False,
                "reason": "未在白名单内，但 strict=false 放行",
                "suggestions": _suggest(name, allowed_set)}

    return {"ok": False, "name": name, "allowed": False, "checked": True,
            "reason": ("模型「" + name + "」不在白名单内（config/system.yaml 的 "
                       "available_models/fallback 或 fetched_models.json 的 "
                       "_manual/动态清单）。如确需使用，请先在 GUI「模型」页"
                       "手动添加，或改用 strict=false。"),
            "suggestions": _suggest(name, allowed_set)}


def _suggest(name, allowed_set, limit=5):
    """近似候选：子串命中优先，其次最长公共前缀，最后 difflib 相似度兜底。

    典型场景：用户想切 `glm-6`，白名单里是 `glm-5.1` / `glm-5.3`。
    纯子串匹配会全部落空（两者谁都不包含谁），但共享前缀 `glm-`，
    所以必须有前缀通道，纠错提示才真正可用。
    """
    if not allowed_set:
        return []
    low = name.lower()
    hits = []
    seen = set()

    def add(m, why):
        if m not in seen:
            seen.add(m)
            hits.append(m)

    # 通道 1：子串命中
    for m in sorted(allowed_set):
        if low in m.lower() or m.lower() in low:
            add(m, "substring")

    # 通道 2：最长公共前缀 ≥ 3 字符（厂商名/系列名通常足够区分）
    import os as _os
    for m in sorted(allowed_set):
        pref = _os.path.commonprefix([low, m.lower()])
        if len(pref) >= 3:
            add(m, "prefix")

    # 通道 3：difflib 相似度兜底（阈值放宽到 0.5，宁可多给候选也不漏）
    import difflib
    for m in difflib.get_close_matches(name, sorted(allowed_set), n=limit * 2, cutoff=0.5):
        add(m, "similar")

    return hits[:limit]


def format_hint(cfg, root, limit=40):
    """给 GUI / 错误提示用：当前已登记可选模型（截断）。"""
    allowed_set, _ = load_registered_models(cfg, root)
    models = sorted(allowed_set)
    return models[:limit], len(models)
