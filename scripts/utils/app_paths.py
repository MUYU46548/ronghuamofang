# -*- coding: utf-8 -*-
"""随包资源定位：**不依赖环境变量**找 `payload/examples`。

## 为什么不能只靠 `NF_SAMPLE_ROOT`

打包态主进程 spawn nf_api 时会注入 `NF_SAMPLE_ROOT`
（`console/main/index.js`: `process.resourcesPath/payload/examples`），
看起来够用 —— 但注入只覆盖**那一条启动路径**。同一份代码在下面这些进程里跑时
根本没有这个变量：

  · 用户 / 外部 Agent 在终端里敲 `python scripts/preset_apply.py`；
  · MCP 垫片自启动代拉的 nf_api（`nf_mcp_stdio_bridge._spawn_nf_api`）；
  · 计划任务 / 快捷方式 / 排障脚本 / 任何第三方壳。

2026-10-25 实测就是这个分裂现场：活的 nf_api（Electron 起的）
`GET /factory/presets` 正常返回 4 套预设，而同一台机器上终端里跑
`find_presets_dir` 打印「找不到 examples/presets/」。**把整条预设能力
押在一个环境变量上，等于让同一个功能一半可用一半不行。**

## 安装布局是固定的，可以从位置反推

    <安装目录>/resources/runtime/python/python.exe      ← sys.executable
    <安装目录>/resources/payload/examples/presets/      ← 目标

`sys.executable` 往上第 3 层（`resources/`）就是它的父。

## 源码态为什么不误伤

`<repo>/.venv/Scripts/python.exe` 与 `%APPDATA%/绒花墨坊/workspace/scripts/...`
两条祖先链上都没有 `payload/`，所以本模块在源码态**恒返回空列表**，
不会抢走 `root/examples`（源码态正解）的优先级。

本模块只用 stdlib，不 import 项目依赖 —— 它被解析链上最早的一环调用，
那里连 PyYAML 是否装好都还不知道。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 往上回溯几层：打包态命中第 3 层（resources/），留点余量给将来的布局变化。
# 别贪多 —— 层层 stat 是启动路径上的开销，且有撞上无关目录的风险。
_MAX_UP = 6


def payload_example_dirs(exe=None, src=None):
    """`payload/examples` 目录候选（按命中顺序，**不做存在性过滤**，调用方自己 is_dir）。

    两个起点各走一遍祖先链：

    - `exe`（默认 `sys.executable`）：打包态命中 `resources/payload/examples`；
    - `src`（默认本文件）：万一将来把 runtime 塞进 app 目录内部，这里能兜住。

    每个祖先都试 `payload/examples` 与 `resources/payload/examples` 两种拼法 ——
    起点不同（`resources/` 层 vs 安装目录层）差一层，不自作聪明去猜是哪种。
    """
    out, seen = [], set()
    for base in (exe or sys.executable, src or __file__):
        try:
            bases = list(Path(base).resolve().parents)[:_MAX_UP]
        except (OSError, RuntimeError):          # 路径异常（网络盘/超长路径）→ 跳过该起点
            continue
        for p in bases:
            for cand in (p / "payload" / "examples",
                         p / "resources" / "payload" / "examples"):
                key = str(cand)
                if key in seen:
                    continue
                seen.add(key)
                out.append(cand)
    return out
