# -*- coding: utf-8 -*-
"""阶段 4 的路由：有 text_writing 就走纯文本，没有就回 agent 流式。

这是 2026-11-27 对齐竞品的接线判据：正文一次 completion + Python 落盘，
不许因为客户端没实现 `run_task_stream` 就悄悄回退到 agent 循环。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from stage4_writing import _run_task_safe  # noqa: E402


PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  → " + str(detail) if detail else ""))


class TextOnlyClient:
    provider = "dsh"
    writes_files = False
    text_writing = True

    def __init__(self):
        self.text_calls = 0
        self.agent_calls = 0

    def run_text_task_stream(self, task, on_piece, stop_flag, **kw):
        self.text_calls += 1
        return {"exit_code": 0, "stdout_tail": "正文", "tokens": 0,
                "tokens_out": 0, "stopped": False, "session_id": "s"}

    def run_task(self, *a, **k):
        self.agent_calls += 1
        raise AssertionError("纯文本客户端不许回退到 run_task")


class AgentClient:
    provider = "dsh"
    writes_files = False
    text_writing = False

    def __init__(self):
        self.stream_calls = 0

    def run_task_stream(self, task, on_piece, stop_flag, **kw):
        self.stream_calls += 1
        return {"exit_code": 0, "stdout_tail": "", "tokens": 0,
                "tokens_out": 0, "stopped": False, "session_id": "s"}

    def run_task(self, *a, **k):
        raise AssertionError("有流式就不走非流式")


def main():
    print("=" * 66)
    print("阶段 4 纯文本写作路由")
    print("=" * 66)
    task = ROOT / "data" / "state" / "tasks" / "stage4_ch99.md"
    text_client = TextOnlyClient()
    res = _run_task_safe(text_client, task, on_piece=lambda p: None)
    check("text_writing 客户端走纯文本路径", text_client.text_calls == 1,
          text_client.text_calls)
    check("纯文本客户端没有回退 run_task", text_client.agent_calls == 0,
          text_client.agent_calls)
    check("返回值原样透传", res.get("exit_code") == 0, res)

    agent_client = AgentClient()
    res = _run_task_safe(agent_client, task, on_piece=lambda p: None)
    check("没开 text_writing 仍走 agent 流式", agent_client.stream_calls == 1,
          agent_client.stream_calls)
    check("agent 路径返回值正常", res.get("exit_code") == 0, res)

    print("-" * 66)
    print("PASS %d / FAIL %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
