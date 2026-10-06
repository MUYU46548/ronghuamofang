# -*- coding: utf-8 -*-
"""解释器守卫：拦「用错 python」这类一开局就崩的事故。

背景（2026-10-06 试跑实锤）：外部 Agent 在项目根敲 `python scripts/orchestrator.py`，
PATH 上的 python 是宿主环境（如 Hermes 自带的 3.14，没装 PyYAML）→ `import yaml`
直接 ModuleNotFoundError 打死启动。agent 又把病因误诊成「.venv 缺 yaml」——
其实 .venv 里 PyYAML 一直好好装着，**错的从来不是依赖，是解释器**。

两个开跑入口共用本守卫（kickoff 写明的 `orchestrator.py` 与 `nfctl.py`）：

- 当前解释器缺 PyYAML、项目 .venv 存在 → stderr 打一行说明，切 .venv python 重跑；
- 切不动（.venv 自身缺依赖 / 项目根本没有 .venv）→ 返回 False，
  由调用方给出可行动报错或转成 nfctl 的阻塞自检项。

纪律：**绝不静默**。切解释器必在 stderr 留痕；nfctl 的 `--json` stdout 不容污染，
所以一律写 stderr。`NF_PY_GUARD=1` 防拉锯（切过一次仍缺 → 不再切，直接报）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_GUARD_ENV = "NF_PY_GUARD"

# scripts/utils/interp_guard.py → 项目根 = parents[2]
ROOT = Path(__file__).resolve().parents[2]


def venv_python(root: Path | None = None) -> Path:
    """项目 venv 解释器路径（POSIX 布局兜底 .venv/bin/python）。"""
    root = root or ROOT
    win = root / ".venv" / "Scripts" / "python.exe"
    posix = root / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def has_yaml() -> bool:
    """当前解释器能否 import PyYAML（orchestrator 启动的唯一硬依赖）。"""
    try:
        import yaml  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 —— 缺包/坏包一律当「不可用」
        return False


def ensure_working_python(entry: str) -> bool:
    """缺 PyYAML 时切 venv 重跑；切不动返回 False（不抛异常，报告权在调用方）。

    entry: 出错提示里给用户看的命令，如 "scripts/orchestrator.py"。
    正常（yaml 可用）时零副作用直接返回 True。
    """
    if has_yaml():
        return True
    vpy = venv_python()
    if vpy.exists() and not os.environ.get(_GUARD_ENV):
        print(
            "[py-guard] 当前 python（%s）缺 PyYAML，切换到项目 venv 重跑：%s"
            % (sys.executable, entry),
            file=sys.stderr,
        )
        env = dict(os.environ)
        env[_GUARD_ENV] = "1"
        raise SystemExit(subprocess.call([str(vpy), *sys.argv], env=env))
    return False


def missing_yaml_hint(root: Path | None = None) -> str:
    """切不动时的可行动文案（谁报错谁显示，一行说清怎么修）。"""
    root = root or ROOT
    vpy = venv_python(root)
    if vpy.exists():
        # 已经切过一次（或当前就是 venv）仍缺 → venv 自身缺依赖
        return ("项目 venv 缺依赖：\"%s\" -m pip install -r requirements.txt" % vpy)
    return ("当前 python（%s）缺 PyYAML 且项目没有 .venv —— "
            "源码版先建：python -m venv .venv && .venv/Scripts/python.exe "
            "-m pip install -r requirements.txt；"
            "安装版请从 GUI 里跑（GUI 用自带运行时 python）" % sys.executable)
