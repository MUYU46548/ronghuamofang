# -*- coding: utf-8 -*-
"""NovelForge 本地质量门禁（P0：把 py_compile 升级为 pyflakes）。

## 为什么需要它

2026-09-19 工程审查的核心结论之一：项目以 `py_compile` 当质量门禁，
但它是**纯语法检查**。本轮发现的活缺陷里：

  - `orchestrator.py` 的 `datetime` 未导入（NameError）
  - `nf_api.py` 的 `only_stage` 未定义（NameError）
  - `nf_api.py` 的 `get_vault_path` 未导入（NameError，pyflakes 首次接入即报出）
  - `nf_api.py` 的 `nf_api.py` 内局部名遮蔽（UnboundLocalError 类）

这些**语法完全合法**，`py_compile` 一个都拦不住，但 `pyflakes` 全部命中。
本脚本即那道门禁，CI（.github/workflows/ci.yml）与本地（提交前 / 打包前）共用同一套判据。

## 判据分级

- **BLOCK（阻塞）**：`undefined name` —— 必然导致运行时 NameError/UnboundLocalError。
  本类必须为零，否则退出码 1。
- **WARN（告警）**：未使用导入、未使用变量、f-string 无占位符等历史存量。
  仅打印与计数，不阻塞（避免一次性冻结 49 处历史告警导致门禁无法落地）。

## 用法

  .venv/Scripts/python.exe scripts/quality_gate.py            # 全仓
  .venv/Scripts/python.exe scripts/quality_gate.py --changed  # 仅 git 改动文件
  .venv/Scripts/python.exe scripts/quality_gate.py --list-warn
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

BLOCK_PATTERN = re.compile(r"undefined name")


def run_pyflakes(targets):
    """跑 pyflakes，返回 (stdout 行列表, 是否成功执行)。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", *targets],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
    except Exception as e:                                    # noqa: BLE001
        print(f"[gate] 无法运行 pyflakes: {e}")
        print("[gate] 请先安装：.venv/Scripts/python.exe -m pip install pyflakes")
        return [], False
    # pyflakes 找到问题时返回非 0，属正常，故不据返回码判失败
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not lines and proc.stderr and "No module named pyflakes" in proc.stderr:
        print("[gate] pyflakes 未安装：.venv/Scripts/python.exe -m pip install pyflakes")
        return [], False
    return lines, True


def changed_py_files():
    """git 变更（含未跟踪）中的 .py 文件。"""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "-uall"],
                             capture_output=True, text=True, encoding="utf-8")
    except Exception:                                         # noqa: BLE001
        return []
    files = []
    for ln in (out.stdout or "").splitlines():
        p = ln[3:].strip().strip('"')
        if p.endswith(".py") and Path(p).exists():
            files.append(p)
    return files


def main():
    ap = argparse.ArgumentParser(description="NovelForge 本地质量门禁（pyflakes）")
    ap.add_argument("--changed", action="store_true", help="仅检查 git 变更中的 .py")
    ap.add_argument("--list-warn", action="store_true", help="完整打印 WARN 清单")
    ap.add_argument("targets", nargs="*", help="显式指定路径（默认 scripts/）")
    args = ap.parse_args()

    if args.targets:
        targets = args.targets
    elif args.changed:
        targets = changed_py_files() or ["scripts/"]
    else:
        targets = ["scripts/"]

    print(f"[gate] 检查目标: {', '.join(targets)}")
    lines, ok = run_pyflakes(targets)
    if not ok:
        return 2

    blocks = [ln for ln in lines if BLOCK_PATTERN.search(ln)]
    warns = [ln for ln in lines if not BLOCK_PATTERN.search(ln)]

    if warns and args.list_warn:
        print(f"\n--- WARN（{len(warns)} 条，不阻塞）---")
        for ln in warns:
            print("  " + ln)

    print(f"\n[gate] BLOCK（未定义名）: {len(blocks)}")
    for ln in blocks:
        print("  ❌ " + ln)
    print(f"[gate] WARN（历史存量）: {len(warns)}")

    if blocks:
        print("\n[gate] 失败：存在未定义名，运行时必抛 NameError。请修复后重试。")
        return 1
    print("[gate] 通过：未检出未定义名 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
