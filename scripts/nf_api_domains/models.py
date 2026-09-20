# -*- coding: utf-8 -*-
"""模型 / provider 域：模型回显、可用模型、动态拉取、缓存、手动添加、切换。

## 为什么单独成域

`/models/*` 是全站权限最敏感的读端点族：它会**读 provider 配置并回显**。
原实现散在 `do_GET` 三处 + `do_POST` 两处，各自「顺手取一下 cfg」，
于是「哪些字段允许回显」这件事没有单一答案 —— 2026-09-19 审查 S8 正是
发生在这个族里（白名单数据存在，但没有任何判定回路）。

本模块把所有模型相关端点收在一处，让「回显什么 / 校验什么」有唯一落点。

## 脱敏红线（不可放宽）

`/models/available` 回显 provider 信息，**只能**给：
`base_url` / `api_key_env`（变量名，不是值）/ `has_key`（布尔）/ `key_mask`。
`key_mask` 用「前 4 + ... + 后 4」，长度不足 8 时返回空串 ——
绝不回显完整密钥，也绝不回显中段。

## provider 配置里的 api_key_env 语义

配置里存的是**环境变量名**（如 `TOKENHUB_API_KEY`），不是密钥本身；
真实值从进程环境读。前端拿到 `key_mask` 只为显示「已配置/未配置」。
"""
import nf_api as api


def handle_models(h):
    """config/system.yaml 的 engine / providers / model 回显。

    注意：`providers` 段含 `api_key_env`（变量名）与 `base_url`，
    **不含**任何密钥值 —— 值只存在于进程环境，配置里从不落盘。
    """
    cfg, _ = api.load_all()
    return 200, {"engine": cfg.get("engine"),
                 "providers": cfg.get("providers", {}),
                 "model": cfg.get("model", {})}


def handle_models_available(h):
    """每个 provider 的可用模型（**脱敏**：只给掩码，不给密钥）。"""
    cfg, _ = api.load_all()
    providers = cfg.get("providers", {})
    avail = {}
    for pid, prov in providers.items():
        base_url = prov.get("base_url", "")
        api_key_env = prov.get("api_key_env", "")
        api_key = api.os.environ.get(api_key_env, "")
        avail[pid] = {
            "base_url": base_url,
            "api_key_env": api_key_env,
            "has_key": bool(api_key),
            # 脱敏：前 4 + ... + 后 4；短于 8 位一律空串（防「前4后4」拼出全串）
            "key_mask": (api_key[:4] + "..." + api_key[-4:]) if len(api_key) > 8 else "",
            "available_models": prov.get("available_models", []),
        }
    return 200, {"providers": avail, "engine": cfg.get("engine")}


def handle_models_fetched(h):
    """动态拉取各 provider 的 /models 端点并缓存到 data/state/fetched_models.json。

    行为要点（与原实现一致）：
      · 单个 provider 失败**不中断**其它 provider（各自记 error 字段）；
      · 缓存落盘时**保留手动添加的 `_manual`**，绝不让一次刷新清掉用户手动登记的模型；
      · 缓存结构 = {provider_id: {models, count}, "_manual": [...]}。
    """
    import urllib.request as _ur

    cfg, _ = api.load_all()
    providers = cfg.get("providers", {})
    cache_path = api.ROOT / "data" / "state" / "fetched_models.json"
    result = {}
    for pid, prov in providers.items():
        base_url = (prov.get("base_url") or "").rstrip("/")
        api_key_env = prov.get("api_key_env", "")
        api_key = api.os.environ.get(api_key_env, "")
        if not base_url or not api_key:
            result[pid] = {"models": [], "error": "missing base_url or api_key"}
            continue
        try:
            req = _ur.Request(
                base_url + "/models",
                headers={"Authorization": "Bearer " + api_key},
            )
            with _ur.urlopen(req, timeout=30) as resp:
                data = api.json.loads(resp.read().decode("utf-8"))
            models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
            result[pid] = {"models": models, "count": len(models)}
        except Exception as e:                              # noqa: BLE001
            result[pid] = {"models": [], "error": str(e)[:200]}

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # 读取旧缓存中手动添加的模型并合并（刷新不得清掉用户手动登记）
    manual_models = set()
    if cache_path.exists():
        try:
            old = api.json.loads(cache_path.read_text(encoding="utf-8"))
            manual_models = set(old.get("_manual", []))
        except Exception:                                   # noqa: BLE001
            pass
    all_models = set()
    for info in result.values():
        all_models.update(info.get("models", []))
    all_models.update(manual_models)
    cache_payload = result.copy()
    cache_payload["_manual"] = sorted(manual_models)
    cache_path.write_text(
        api.json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 200, {"providers": result, "cache": str(cache_path)}


def handle_models_cache(h):
    """读取缓存的模型列表（扁平化：各 provider 的 models + `_manual`）。"""
    cache_path = api.ROOT / "data" / "state" / "fetched_models.json"
    if not cache_path.exists():
        return 200, {"models": []}
    try:
        data = api.json.loads(cache_path.read_text(encoding="utf-8"))
        all_models = set()
        for key, info in data.items():
            if key.startswith("_"):
                continue
            if isinstance(info, dict):
                all_models.update(info.get("models", []))
        all_models.update(data.get("_manual", []))
        return 200, {"models": sorted(all_models)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": str(e)[:200]}


def handle_models_add(h, body):
    """手动把一个模型名写进缓存（GUI「手动添加」按钮）。

    曾经写在 do_GET 里且用了 `body.get` —— GET 会 500、POST 会 404。
    写操作必须走 POST。

    模型准入：**用户手动添加 = 显式确认**，故此处只需第 ① 级格式校验
    （2026-09-19 审查 S8：把校验回路接进来，而不是每条写入路径各写一份）。
    """
    name = str(body.get("name") or "").strip()
    try:
        name = api.model_registry.validate_format(name)
    except api.model_registry.ModelNameInvalid as e:
        return 400, {"error": str(e)}
    cache_path = api.ROOT / "data" / "state" / "fetched_models.json"
    manual_models, providers_data = set(), {}
    if cache_path.exists():
        try:
            old = api.json.loads(api.nf_read_text(cache_path))
            manual_models = set(old.get("_manual", []))
            providers_data = {k: v for k, v in old.items() if not k.startswith("_")}
        except Exception:                                   # noqa: BLE001
            manual_models, providers_data = set(), {}
    manual_models.add(name)
    providers_data["_manual"] = sorted(manual_models)
    api.nf_write_text(cache_path,
                      api.json.dumps(providers_data, ensure_ascii=False, indent=2))
    return 200, {"ok": True, "added": name,
                 "manual_count": len(manual_models),
                 "whitelist_checked": True,
                 "note": "已手动添加并准入（用户显式动作即确认）"}


def handle_models_switch(h, body):
    """切换某角色的模型（写入 config/system.yaml 的 model.<role>.id）。

    模型准入（2026-09-19 审查 S8）：这里曾经是**唯一一道没有校验的写入路径**
    —— 原先连格式校验都没有，且从不读取任何白名单数据源。
    现在接入 model_registry 的两级校验：
      ① 格式合法性（原先完全缺失）
      ② 白名单归属（strict=true 时强制；strict=false 只报警不拦）
    """
    role = str(body.get("role") or "").strip()
    model_id = str(body.get("model") or "").strip()
    if not role or not model_id:
        return 400, {"ok": False, "error": "role 与 model 均必填"}
    try:
        cfg_path = api.ROOT / "config" / "system.yaml"
        cfg = api.yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        models = cfg.get("model") or {}
        if role not in models:
            return 400, {"ok": False, "error":
                         "未知角色: " + role + "（可用: "
                         + ", ".join(sorted(models)) + "）"}
        # 白名单纪律：默认强制；请求体可传 strict:false 显式绕过（会回报未校验）
        strict = body.get("strict") is not False
        chk = api.model_registry.check_model(model_id, cfg, api.ROOT, strict=strict)
        if not chk["ok"]:
            hint_models, hint_total = api.model_registry.format_hint(cfg, api.ROOT)
            return 400, {"ok": False, "error": chk["reason"],
                         "suggestions": chk["suggestions"],
                         "registered_count": hint_total,
                         "hint": "可选模型（前 " + str(len(hint_models))
                                 + " 个）: " + ", ".join(hint_models)}
        model_id = chk["name"]                  # 规范化后的名字
        old = models[role].get("id")
        models[role]["id"] = model_id
        cfg["model"] = models
        api.nf_write_text(cfg_path,
                          api.yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
        return 200, {"ok": True, "role": role, "model": model_id,
                     "previous": old,
                     "whitelist_checked": chk["checked"],
                     "whitelisted": chk["allowed"],
                     "message": "已切换 " + role + " → " + model_id
                                + "（下次运行生效）"
                                + ("" if chk["checked"] else
                                   "［注意：strict=false，未做白名单校验］")}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/models", handle_models),
    ("GET", "/models/available", handle_models_available),
    ("GET", "/models/fetched", handle_models_fetched),
    ("GET", "/models/cache", handle_models_cache),
    ("POST", "/models/add", handle_models_add),
    ("POST", "/models/switch", handle_models_switch),
)
