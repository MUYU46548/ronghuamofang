# -*- coding: utf-8 -*-
"""许可协议闸的 HTTP 端点（2026-11-18）。

只做两件事，判据全在 `utils/license.py`（本模块不复制任何判定逻辑）：

  GET  /license       当前状态（acked / stale / 已签版本与时间）
  POST /license/ack   写入同意记录（**只接受桌面端 GUI 双因子来源**）

## 为什么同意只能由 GUI 签

这条闸防的就是"外部 Agent 替用户开闸"。若 `/license/ack` 接受普通 POST，
Agent 一次请求就能把自己放进来，"用户同意过"重新变成一句没有证据的话。
判据与审批族同源：`agent_guard.is_trusted_gui`（来源头 + 主进程签发 token）。

## 为什么服务端自己决定版本与指纹

客户端只发 `{"agree": true}`，版本号与正文指纹由服务端常量决定 ——
否则任何客户端都可以声称"我同意的是最新版"，而实际正文已经改过。
"""
from __future__ import annotations

import nf_api as api


def handle_license_get(h, body=None):
    """当前状态。前端据此决定是否弹首启强制确认。"""
    from utils import license as lic
    st = lic.state(api.ROOT)
    payload = {"ok": True}
    payload.update(st)
    return 200, payload


def handle_license_ack(h, body):
    """写入同意记录（GUI 双因子 + 显式 agree）。

    顺序：**先判来源，再判 agree**。
    反过来的话，未授权调用者会先收到“请带 agree:true” —— 等于把开闸的咒语告诉它，
    也把那两个 403/400 的差别变成一枚探针（能区分“来源被拒”与“业务不对”）。
    对可信 GUI 而言，漏带 agree 仍然得到 400 与清楚的原因。
    """
    from utils import license as lic
    from utils.agent_guard import is_trusted_gui

    headers = getattr(h, "headers", None) or {}
    if not is_trusted_gui(headers):
        # 与 /approve 同族：签名动作只能人在界面上做，不接受外部 Agent 代签。
        try:
            api._log_audit("/license/ack", "POST", 403, source="agent",
                           detail="license-ack-denied")
        except Exception:                                   # noqa: BLE001
            pass
        return 403, {"ok": False,
                     "error": "许可协议的确认只能由桌面端界面完成 —— "
                              "外部 Agent / 命令行不能代签（要使用请自己打开桌面端点同意）。",
                     "license_required": True}

    if not (body or {}).get("agree"):
        return 400, {"ok": False,
                     "error": "需要显式同意（请求体带 agree: true）—— 不默认识为同意。"}

    ok, res = lic.record(source="gui", root=api.ROOT)
    if not ok:
        return 500, {"ok": False, "error": res}
    try:
        api._log_audit("/license/ack", "POST", 200, source="gui", detail="license-acked")
    except Exception:                                       # noqa: BLE001
        pass
    out = {"ok": True, "message": "已记录同意（许可协议 v%s）" % lic.LICENSE_VERSION}
    out["license"] = res
    return 200, out
