# -*- coding: utf-8 -*-
"""运行配置写侧（2026-11-17 拆壳第三批）：引擎切换 / 供应商切换 / 风格笔记。

为什么单独成域：这三个端点都是"改 config 里的一个键"，而它们的**共同事故史**
是同一条 —— 早期实现用 `yaml.dump(整份 cfg)` 覆盖写，`config/system.yaml` 里
那 172 行注释（止烧口径、模型白名单原因、provider 注意事项）**随之消失**，
用户点一下下拉就把说明书删了，还没有任何提示。
所以这一域的铁律写在代码里：**一律 `utils.config_io` 定向改写，只动目标行**。

协议同其它域：`handle_*(h, body)` 返回 `(status, payload)`。
ROOT 走 `api.ROOT` 属性访问（`from nf_api import ROOT` 是值拷贝，`--root` 之后失效）。
"""
import nf_api as api

ROLES = ("default", "architect", "outliner", "writer", "checker", "reviewer", "polisher")


def handle_config_engine(h, body):
    """切换执行引擎（config/system.yaml 的顶层键 `engine`）。

    2026-10-31 之前**只有读没有写** —— 设置页把 engine 当只读文字显示，
    用户想从“直连”换到“Agent 子会话”（或反过来）只能手改 YAML，
    而界面上那行字还写着“引擎与模型”，看起来能点。
    """
    eng = str(body.get("engine") or "").strip().lower()
    if eng not in ("direct", "hermes", "dsh", "self"):
        return 400, {"ok": False, "error":
                     "engine 只能是 direct（直连模型，按量计费）、hermes"
                     "（Agent 子会话，走订阅）、dsh（DeepSeek Harness headless，"
                     "走订阅）或 self（只出任务书，不调模型）"
                     "——收到 %r" % (body.get("engine"),)}
    try:
        from utils.config_io import set_section_scalar
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        # 定向改写：**只动 engine 那一行**，其余注释与配置原样保留
        w_ok, w_msg = set_section_scalar((), "engine", eng, path=sys_yaml)
        if not w_ok:
            return 400, {"ok": False, "error": "写入失败：" + w_msg}
        cur_cfg, _ = api.load_all()
        _label = {"hermes": "Agent 子会话（走订阅）",
                  "dsh": "DSH headless（走订阅）",
                  "self": "只出任务书（不调用任何模型）"}.get(
                      eng, "直连模型（按量计费）")
        _note = {
            "hermes": ("对下一次运行生效（不需要重启客户端）。"
                       "agent 模式下 config 的 model.* 不参与运行，"
                       "模型由外部 Agent 决定。"),
            "dsh": ("对下一次运行生效（不需要重启客户端）。"
                    "headless 没有 --model 旋钮，模型由 DSH profile 决定；"
                    "金额恒 0、token 照记。要先确认本机装了 DSH"
                    "（nfctl check 会报“DSH 可用”）；headless 默认不吃 DSH "
                    "桌面端的登录态，凭据走环境/.env，详见 AGENTS.md 引擎一节。"),
            "self": ("对下一次运行生效（不需要重启客户端）。"
                     "本引擎**不调用任何模型**：每阶段把任务书写到 "
                     "data/state/tasks/self/stageN/，执行完把产物写到位再重跑即可。"),
        }.get(eng, ("对下一次运行生效（不需要重启客户端）。"
                    "直连按量计费，注意 system.yaml 的 "
                    "budget.token_limit 止烧阈值。"))
        return 200, {
            "ok": True, "engine": eng,
            "message": "已切换引擎 → " + _label,
            "note": _note,
            "providers_count": len(cur_cfg.get("providers") or {}),
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_provider(h, body):
    """切换某阶段（role）的 provider。"""
    role = str(body.get("role") or "")
    new_provider = str(body.get("provider") or "")
    if role not in ROLES:
        return 400, {"error": "role 须为 " + "/".join(ROLES)}
    if not new_provider:
        return 400, {"error": "provider 必填"}
    cfg, _ = api.load_all()
    if new_provider not in cfg.get("providers", {}):
        return 400, {"error": "provider %s 不存在于 config/system.yaml" % new_provider}
    try:
        from utils.config_io import set_section_scalars
        w_ok, w_msg = set_section_scalars(
            ("model", role), {"provider": new_provider},
            path=api.ROOT / "config" / "system.yaml")
    except Exception as e:                                  # noqa: BLE001
        w_ok, w_msg = False, type(e).__name__ + ": " + str(e)[:160]
    if not w_ok:
        return 400, {"ok": False, "error": "写入失败：" + w_msg}
    # 换了供应商、模型名未必跟着换 —— 模型名跨供应商**不通用**，
    # 而本项目对 400/404 是 raise、**不走 fallback**（见 llm_client._post_chat），
    # 留着旧模型名等于该角色直接哑火。这里**不阻断**（available_models 未必全），
    # 只回报 warning 由 GUI 提示。
    cur_model = str(((cfg.get("model") or {}).get(role) or {}).get("id") or "")
    avail = ((cfg.get("providers", {}) or {}).get(new_provider) or {}).get(
        "available_models") or []
    warning = ""
    if cur_model and avail and cur_model not in avail:
        warning = ("模型“" + cur_model + "”不在 " + new_provider +
                   " 的 available_models 内，运行时会 404 —— 请同时改选模型")
    return 200, {"ok": True, "role": role, "provider": new_provider,
                 "model": cur_model, "warning": warning}


def handle_config_dsh_model(h, body):
    """设置 DSH 下某角色用哪个模型（2026-11-20，作者要求"角色模型表全部保留"）。

    ## 为什么不能复用 /models/switch

    `/models/switch` 写 `model.<role>.id`，并且过**模型白名单**
    （`utils/model_registry`，判据来自 `providers.<pid>.available_models`）。
    那是"墨坊自己去连某家 API"的语义。dsh 引擎下角色模型是**传给 DSH 的
    patch 参数**，名字必须来自 DSH 自己的 provider/model 清单 ——
    拿 tokenhub 的账本去校验它，会把一个合法的 DSH 模型名拒掉。

    ## 写入规则

    - 只写 `model.<role>.dsh_provider` / `model.<role>.dsh_model`，**不碰**
      `provider` / `id`（那两个是 direct 引擎的字段，切回直连还要用）。
    - 值为空 = 写空串；装配时 strip 成空 → 该角色回落 DSH profile 默认模型。
    - 定向改写，注释不丢（`config_io` 的既有保证，见本模块 docstring）。
    """
    role = str(body.get("role") or "").strip()
    if role not in ROLES:
        return 400, {"ok": False, "error": "role 须为 " + "/".join(ROLES)}
    fields = {}
    for key, dst in (("provider", "dsh_provider"), ("model", "dsh_model")):
        if key in body:
            fields[dst] = str(body.get(key) or "").strip()
    if not fields:
        return 400, {"ok": False, "error": "provider / model 至少给一个"}
    try:
        from utils.config_io import set_section_scalars
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        w_ok, w_msg = set_section_scalars(("model", role), fields, path=sys_yaml)
    except Exception as e:                                  # noqa: BLE001
        w_ok, w_msg = False, type(e).__name__ + ": " + str(e)[:160]
    if not w_ok:
        return 400, {"ok": False, "error": "写入失败：" + w_msg}
    _cfg, _ = api.load_all()
    cur = ((_cfg.get("model") or {}).get(role) or {})
    return 200, {"ok": True, "role": role,
                 "provider": cur.get("dsh_provider") or "",
                 "model": cur.get("dsh_model") or "",
                 "note": "对下一次运行生效（engine: dsh 时按角色传给 DSH）"}


def handle_config_style_notes(h, body):
    """保存用户风格笔记：定向改写 project.yaml（保留注释），写入前备份。"""
    content = body.get("content")
    if content is None:
        return 400, {"ok": False, "error": "缺少 content 字段"}
    if not isinstance(content, str):
        return 400, {"ok": False, "error": "content 必须为字符串"}
    if len(content) > 4000:
        return 400, {"ok": False, "error": "风格笔记过长（上限 4000 字）"}
    try:
        from utils.project_config import set_style_notes
        ok, msg = set_style_notes(content)
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    return (200, {"ok": ok, "message": msg}) if ok else (400, {"ok": False, "error": msg})
