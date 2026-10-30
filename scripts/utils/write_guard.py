# -*- coding: utf-8 -*-
"""越权写盘检测：agent / 子会话引擎的**可观测性边界**（2026-10-30）。

## 为什么需要

`engine: direct` 下正文由本进程按 `===FILE:` 协议落盘，路径先过
`llm_client.allowed_paths`（只许 `data/**` 与 `logs/runs.db`）—— 越权写在机制上
不可能。`engine: hermes`（agent 模式）把 `-t file` 的 `write_file` 交给子会话：
它**能写仓库任何位置**（`config/`、`prompts/`、`materials/`……）。
AGENTS.md 里写的「机制约束」只对直连引擎成立 —— 这是一处**文档与实现不符**。

## 本模块的立场：只做检测，不假装是沙箱

真沙箱要改执行层（Hermes 自己的权限设置 / 容器 / 只读挂载），绒花墨坊这一侧
做不到。所以这里不做「阻断」也不做「自动回滚」：

- **不阻断**：产物已经写好了，判失败等于扔掉正文重跑一遍；
- **不回滚**：调用期间可能有别的东西在写（GUI 保存配置、用户手动改稿），
  自动回滚会连合法写入一起抹掉。

它只做一件事：把「这一章写过白名单之外的哪些文件」变成**事实**打出来、
并落一行审计（`data/state/write_guard.jsonl`）。看见了才谈得上处置。

## 白名单

`data/**` 与 `logs/**` —— 与 direct 引擎的 `allowed_paths` 同源。
其余目录（含新建文件）一律算越权。

## 成本

一次快照 = 一次目录遍历 + stat。跳过 `data/`、`.git`、`.venv`、`node_modules`
等大目录后通常几百个文件、几十毫秒；每章两次（前后各一次）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# 与 llm_client.allowed_paths 同源：这两个前缀之下随便写
ALLOW_PREFIXES = ("data/", "logs/")

# 这些目录不扫（要么本身就在白名单里，要么是依赖/缓存，扫了纯耗时）
SKIP_DIRS = {
    "data", "logs", "history", "output", "deliverables", "temp",
    "__pycache__", "node_modules", "venv", "env", "dist", "build",
}

AUDIT_REL = Path("data/state/write_guard.jsonl")


def _root(root="."):
    return Path(root).resolve()


def _allowed(rel_posix):
    return rel_posix.startswith(ALLOW_PREFIXES)


def _iter_files(root):
    """遍历 root 下**不在白名单、不在跳过大目录**里的文件（生成 Path）。"""
    for entry in root.iterdir():
        name = entry.name
        try:
            if entry.is_dir():
                if name in SKIP_DIRS or name.startswith("."):
                    continue
                for sub in entry.rglob("*"):
                    try:
                        if not sub.is_file():
                            continue
                    except OSError:
                        continue
                    if any(part in SKIP_DIRS or part.startswith(".")
                           for part in sub.relative_to(root).parts[:-1]):
                        continue
                    yield sub
            elif entry.is_file():
                # 根目录下的独立文件（含 .env 这类隐藏文件）都盯
                yield entry
        except OSError:
            continue


def snapshot(root="."):
    """记录「白名单外」每个文件的 (mtime_ns, size)。返回 {相对路径: (mtime, size)}。"""
    base = _root(root)
    out = {}
    for p in _iter_files(base):
        try:
            st = p.stat()
            rel = p.relative_to(base).as_posix()
        except (OSError, ValueError):
            continue
        if _allowed(rel):
            continue
        out[rel] = (st.st_mtime_ns, st.st_size)
    return out


def diff(before, root="."):
    """比对快照，返回越权写盘清单 [(相对路径, 'modified'|'created'|'deleted')]。"""
    after = snapshot(root)
    viol = []
    for rel, meta in after.items():
        old = (before or {}).get(rel)
        if old is None:
            viol.append((rel, "created"))
        elif old != meta:
            viol.append((rel, "modified"))
    for rel in (before or {}):
        if rel not in after:
            viol.append((rel, "deleted"))
    return sorted(viol)


def report(violations, task_name=""):
    """把越权清单打到控制台 + 落一行审计。返回条数。

    措辞纪律：不用 emoji（GUI/后台任务的 stdout 是 cp936，emoji 会
    UnicodeEncodeError，恰好把这条最该被看见的告警弄丢）。
    """
    if not violations:
        return 0
    print(f"[write_guard] WARN 子会话写了白名单（data/**、logs/**）之外的文件 "
          f"{len(violations)} 个：")
    for rel, action in violations[:20]:
        print(f"[write_guard]   {action}: {rel}")
    if len(violations) > 20:
        print(f"[write_guard]   ……另有 {len(violations) - 20} 个")
    print("[write_guard]   这不是沙箱，只是事后检测；真正的边界在 Hermes 自身的权限设置。")
    try:
        AUDIT_REL.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_REL, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task": str(task_name or ""),
                "violations": [{"path": p, "action": a} for p, a in violations],
            }, ensure_ascii=False) + "\n")
    except Exception as e:                                    # noqa: BLE001
        print(f"[write_guard] WARN 审计落盘失败: {type(e).__name__}: {e}")
    return len(violations)
