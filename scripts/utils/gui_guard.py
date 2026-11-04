# -*- coding: utf-8 -*-
"""GUI-only 写入的唯一判据（设置类端点共用，2026-11-04）。

## 为什么单独一个文件

本模块住在 `utils/` 而不是 `nf_api_domains/`：它**不是域模块**（没有端点），
放在域目录里会被 `test_api_domains` 的“孤立模块”判据抓（抓得对）。

`_gui_only` 原来只在 `project.py` 里（token_limit / target_words 等设置类端点用）。
新增“服务商增删改”也要同一条判据 —— 如果照抄一份到 `models.py`，两份判据迟早
各自漂移（一份改成 fail-open、另一份还是 fail-closed，后门就在漂移缝里）。
所以提到这里，域模块之间**只共享判据，不共享业务**。

判据本身只有一行：`agent_guard.is_trusted_gui(headers)` —— 双因子
（来源头 + 主进程签发 token），与 `nf_api.do_POST` 的禁用名单共用同一实现。
"""
from utils import agent_guard


def gui_only(h, what):
    """设置类写入必须来自**可信的 GUI 本体**。允许时返回 None，否则返回 (403, payload)。

    为什么与 agent_mode 无关也要求：这些是**成本/安全相关的设置**
    （预算与止烧阈值、服务商与密钥）—— 外部 Agent 若能改，就等于自己给自己发通行证。
    """
    headers = getattr(h, "headers", None) or {}
    try:
        ok = agent_guard.is_trusted_gui(headers)
    except Exception:                                        # noqa: BLE001
        ok = False                                           # 判据坏了也要 fail-closed
    if ok:
        return None
    # 拒文零通道名（V5）：不提缺哪个头、该填什么值 —— 拒文本身是攻击面。
    return 403, {"ok": False,
                 "error": "%s 仅允许用户本人在桌面端手动修改；本次调用不是可信的 "
                          "GUI 来源。请停手并报告用户。" % what}
