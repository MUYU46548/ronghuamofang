# -*- coding: utf-8 -*-
"""许可协议总闸（2026-11-18）—— **未确认前，本软件不调用任何模型**。

## 为什么要有这个模块

2026-11-18 审计（`docs/审计-AI调用入口与授权闸-20261118.md`）的结论是：
免责声明确认只存在渲染层 localStorage，后端 / MCP / CLI 一处都不读 ——
也就是说“用户同意过”这件事，从来没有任何代码校验过。
外面任何进程（含 AI Agent）一条 HTTP POST 或一次 CLI 调用就能让程序开始花钱，
而作者以为“我同意过了才会跑”。

现在把这条从**文案**变成**判据**：
  · 判据只有一处（本文件）。`make_client()` 与 `nf_api` 全站前置都读它；
  · 同意记录落在**服务端**（`<root>/config/license_ack.json`），不是浏览器缓存；
  · 条款版本或正文指纹一变，旧同意自动失效 → 重新确认（同前端 ack 键的老机制）。

## 与前端的关系（两边必须同源）

条款正文的唯一事实源仍是 `console/src/disclaimer.js`（作者改字只改那里）。
本文件的 `LICENSE_VERSION` / `LICENSE_CONTENT_SHA` 是它在服务端的**镜像**，
由 `tests/unit/test_license_gate.py` 的【L5】逐项比对 —— 改正文忘了同步会红，
不靠人记。

## 三条边界（如实声明）

1. **它拦的是接口级自我解锁**：Agent 不能通过产品自身的 API 给自己开闸。
   本机恶意程序能直接改工作区文件 —— 那不是这套机制要解决的问题。
2. **测试旁路**：环境变量 `NF_LICENSE_ACK=1` 视为已同意（仅测试用，会往 stderr
   打一条 WARN）。生产启动路径不设它；`test_license_gate.py` 里显式清掉它做反证。
3. **不确认 = 什么都用不了**：全站只放行 `/health`、`/license`、`/license/ack`，
   其余一律 403 `license_required`；`make_client()` 直接抛错。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

#: 与 `console/src/disclaimer.js` 的 DISCLAIMER_VERSION 同值（服务端镜像）。
LICENSE_VERSION = "1.1"
#: 与 `console/src/disclaimer.js` 的 DISCLAIMER_CONTENT_SHA 同值（正文指纹前 12 位）。
LICENSE_CONTENT_SHA = "7e2e1bd3f529"

#: 同意记录（相对数据根）。放 config/ 而不是 data/：data 会被各种"清理"动作扫到，
#: 而"用户签过字"这件事不该跟着一起被清掉。
ACK_REL = Path("config") / "license_ack.json"

#: 测试旁路（仅测试进程设；值必须是 "1"）。
ENV_BYPASS = "NF_LICENSE_ACK"


def _root(explicit=None):
    """数据根：显式参数 → NF_ROOT（nf_api --root 会写进环境）→ 源码树。"""
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("NF_ROOT")
    if env:
        return Path(env).resolve()
    # scripts/utils/license.py → parents[2] = 仓库根
    return Path(__file__).resolve().parents[2]


def ack_path(root=None):
    return _root(root) / ACK_REL


def _norm_version(v):
    """版本号归一成字符串：1.1（JSON 数字）与 "1.1" 都算同一个版本。"""
    s = str(v if v is not None else "").strip()
    if s.endswith(".0") and s.count(".") == 1:
        s = s[:-2]
    return s


def _read_raw(root=None):
    p = ack_path(root)
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _bypass():
    """测试豁免判定。

    ⚠️ 这里**故意不打印任何东西**（2026-11-18 实测代价）：`nf_api` 的 stderr 常被
    测试重定向进工作区里的日志文件，而 `test_factory_api_http` 断言“盘点前后工作区
    逐字节不变” —— 一行 WARN 就把那条判据打红，且红得看不出原因。
    豁免这件事的“出声”放在**设置它的那一端**（`nfctl run_tests` 开跑前打一行），
    并通过 `state()["bypass"]` 如实上报（`GET /license` 可查）。
    """
    return str(os.environ.get(ENV_BYPASS) or "").strip() == "1"


def state(root=None):
    """当前许可状态（给 nf_api、GUI 与拒绝文案共用）。

    返回：
      acked    bool   —— 是否可调用模型
      bypass   bool   —— 是否因测试环境变量而豁免
      stale    bool   —— 签过但条款已变（版本/指纹不符），需重新确认
      ack      dict|None —— 原记录 {version, sha, at, source}
      required dict   —— 服务端认定的当前版本与指纹
    """
    raw = _read_raw(root)
    bypass = _bypass()
    req = {"version": LICENSE_VERSION, "sha": LICENSE_CONTENT_SHA}
    stale = False
    if raw:
        v_ok = _norm_version(raw.get("version")) == _norm_version(LICENSE_VERSION)
        s_ok = str(raw.get("sha") or "").strip() == LICENSE_CONTENT_SHA
        stale = not (v_ok and s_ok)
    acked = bool(bypass or (raw and not stale))
    return {
        "acked": bool(acked),
        "bypass": bool(bypass),
        "stale": bool(stale),
        "ack": {k: raw.get(k) for k in ("version", "sha", "at", "source")} if raw else None,
        "required": req,
    }


def is_acked(root=None):
    return bool(state(root).get("acked"))


def record(source="gui", root=None, at=None):
    """写入同意记录（**服务端版本与指纹由本模块决定**，不信客户端自称）。

    只应由 GUI 双因子来源调用（`nf_api.handle_license_ack` 里已校验）。
    返回 (ok, state_or_error)。
    """
    p = ack_path(root)
    payload = {
        "product": "绒花墨坊",
        "version": LICENSE_VERSION,
        "sha": LICENSE_CONTENT_SHA,
        "at": at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source or "unknown")[:40],
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except OSError as e:
        return False, "写入同意记录失败：%s（%s）" % (e, p)
    return True, state(root)


def clear(root=None):
    """撤销同意记录（测试与排障用；界面没有这个入口）。"""
    try:
        ack_path(root).unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def refusal_message(action="调用模型"):
    """未确认时的可行动拒文（HTTP 与 CLI 共用，避免两处说法漂移）。

    `action` 只用来补一句"被拦下的具体是什么"，不改变主句 —— 主句必须是同一个口径，
    否则同一件事在界面上和终端里读起来像两条不同的规矩。
    """
    return (
        "未确认许可协议（免责与知情声明）—— 按设计，确认之前本软件不执行任何操作，"
        "包括%s。\n"
        "怎么继续：打开桌面端，在首启弹窗里滚到底部、勾选并点“我已阅读并同意”；"
        "确认是**一次性的**，之后这个工作区不再询问（条款改版才会重新弹）。\n"
        "不想同意：点“不同意并退出”，本软件不会调用任何模型、也不会花你的钱。"
        % (str(action or "调用模型"))
    )


def self_check():
    """`nfctl check` 用：一行说清当前状态（不打印任何敏感内容）。"""
    st = state()
    if st["bypass"]:
        return True, "许可协议：已按测试环境变量豁免（生产启动路径不设它）"
    if st["acked"]:
        a = st["ack"] or {}
        return True, "许可协议：已确认（v%s，%s，来源 %s）" % (
            a.get("version"), a.get("at") or "时间未知", a.get("source") or "未知")
    if st["stale"]:
        return False, ("许可协议：条款已更新，需要重新确认 —— 打开桌面端会强制弹出"
                       "（旧记录 v%s 已失效）" % ((_read_raw() or {}).get("version"),))
    return False, ("许可协议：尚未确认 —— 桌面端首启会强制弹出；确认前所有 AI 功能不可用"
                   "（当前引擎即使配了也不会被调用）")
