# -*- coding: utf-8 -*-
"""Agent 模式安全守卫（HTTP 与 CLI 的**同一份判据**）。

## 语义（2026-09-21 用户确认，2026-10-03 补齐 CLI 侧）

`gates.agent_mode = true` 时：**外部 Agent 可读、可跑流水线，但不能代替用户审批**。
这是设计，不是 bug。

## 为什么要有本模块

原先这条守卫只写在 `nf_api.do_POST` 里（HTTP 侧，靠 `X-Mofang-Source: gui` 头区分来源），
而**同样的动作还有一条 CLI 后门**：外部 Agent 完全可以

    python scripts/approve.py --stage 2

绕过 403 直接审批 —— 判据写在 HTTP 层，CLI 层就是敞开的。
现在两侧共用本模块：禁止清单、模式判定、审计日志各只有一份实现。

## 两侧的「来源」取证方式不同（这是刻意的）

| 入口 | 人工来源的证据 |
|------|----------------|
| HTTP | 请求头 `X-Mofang-Source: gui`（Electron preload 注入） |
| CLI  | ① 环境变量 `MOFANG_SOURCE=gui`；② stdin+stdout 都是交互式 TTY（人在终端手敲）；③ 显式 `--human` |

CLI 没有请求头，所以用「TTY + 显式声明」代替。`--human` 是**逃生门**：
人在 CI / 包装脚本里确实无法提供 TTY 时，得自己把它写下来（= 明确表达"我是人"）。
**这不是沙箱**：它拦的是"Agent 照着文档跑一条命令就悄悄替用户拍板"，
拦不住蓄意伪造来源的调用者 —— 与 HTTP 侧的头部判据同等强度，不要对外宣称更强。
"""
import json
import os
import sys
import time
from pathlib import Path

# Agent 模式下**永不允许**非 GUI/非人工来源执行的动作（与 nf_api.do_POST 同源）
FORBIDDEN_IN_AGENT_MODE = frozenset({
    "/approve",              # 审批
    "/reject",               # 打回
    "/project/create",       # 新建项目
    "/project/archive",      # 归档
    "/project/restore",      # 恢复
    "/project/init",         # 首启初始化
    "/config/agent_mode",    # Agent 不得自行切换模式
})

# 人工来源的标记值（HTTP 头 / 环境变量通用）
HUMAN_SOURCES = frozenset({"gui", "human"})

AUDIT_LOG_PATH = "data/state/agent_audit.jsonl"


def agent_mode_enabled(cfg):
    """`gates.agent_mode` 是否开启。cfg 为 None/异常结构一律按关闭处理。"""
    try:
        return bool(((cfg or {}).get("gates") or {}).get("agent_mode", False))
    except Exception:                                       # noqa: BLE001
        return False


def normalize_source(raw):
    """归一来源标记。返回 ""（未声明）/ "gui" / "human" / 原样小写（如 "agent"）。"""
    return str(raw or "").strip().lower()


def is_forbidden_in_agent_mode(path):
    """该路径在 Agent 模式下是否禁止非人工来源调用。"""
    return str(path or "") in FORBIDDEN_IN_AGENT_MODE


def cli_human_evidence(source_env=None, isatty=None, explicit_human=False):
    """CLI 侧判定「本次调用是否人工来源」。返回 (is_human, 证据说明)。

    isatty: 便于测试注入。可以是 bool，也可以是 (stdin_isatty, stdout_isatty)
    二元组；默认查真实 stdin/stdout。**必须两个都是 TTY** —— 只查 stdout 的话，
    `echo x | python scripts/approve.py ...` 这类管道会被误判成人工。
    """
    src = normalize_source(source_env if source_env is not None
                           else os.environ.get("MOFANG_SOURCE"))
    if src in HUMAN_SOURCES:
        return True, "MOFANG_SOURCE=%s" % src
    if src:
        return False, "MOFANG_SOURCE=%s（非人工标记）" % src
    if explicit_human:
        return True, "显式 --human"
    if isatty is None:
        try:
            isatty = (bool(sys.stdin.isatty()), bool(sys.stdout.isatty()))
        except Exception:                                   # noqa: BLE001
            isatty = (False, False)
    if isinstance(isatty, (tuple, list)):
        stdin_tty, stdout_tty = bool(isatty[0]), bool(isatty[-1])
    else:
        stdin_tty = stdout_tty = bool(isatty)
    if stdin_tty and stdout_tty:
        return True, "交互式终端"
    if stdin_tty or stdout_tty:
        return False, "只有一端是终端（另一端被管道接管，视为非人工）"
    return False, "非交互式（无 TTY）、未声明 MOFANG_SOURCE、也未显式 --human"


def refusal_message(action, evidence, entry="CLI"):
    """拒绝执行的可行动文案（单一来源，两侧共用）。"""
    return (
        "[guard] 拒绝执行：gates.agent_mode=true，而本次调用**不是人工来源**"
        "（%s；入口 %s）。\n"
        "        含义：外部 Agent 可以读、可以跑流水线，但**不能代替用户审批**"
        "（设计如此，不是故障）。\n"
        "        人工执行：在**交互式终端**里直接跑该命令（人手动敲 = 有 TTY）；\n"
        "        确属人工但拿不到 TTY（CI / 包装脚本）：显式加 --human，"
        "或设 MOFANG_SOURCE=gui。\n"
        "        想长期免除此限：把 config/system.yaml 的 gates.agent_mode 设为 false。"
        % (evidence, action)
    )


def log_audit(path, method="POST", status=200, source="gui", detail="", root=None):
    """审计日志（data/state/agent_audit.jsonl）。**任何异常都不得影响主流程**。"""
    try:
        base = Path(root) if root else Path(".")
        target = base / AUDIT_LOG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "method": method, "path": path,
            "status": status, "source": source, "detail": str(detail)[:120],
        }, ensure_ascii=False)
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            f.write(entry + "\n")
    except Exception:                                       # noqa: BLE001
        pass
