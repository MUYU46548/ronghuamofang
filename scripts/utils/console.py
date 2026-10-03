# -*- coding: utf-8 -*-
"""控制台输出安全网（进程级，幂等）。

## 为什么需要（2026-10-03 实测事故）

stdout 是**管道**时，Python 用本地编码（本机 cp936），而 ¥ / ⚠️ / 💰 / ✅ / ❌
这些字符**不在 GBK 里** → `UnicodeEncodeError: 'gbk' codec can't encode character`。

管道正是**被捕获**的形态：GUI（Electron spawn）、后台任务、测试运行器。
TTY 下 Python 用 UTF-8（PEP 528），所以这个问题**本地手跑好好的、一被捕获就现形**。

已实测到的两处后果（都不是"日志难看"，而是行为错）：

1. `engine: hermes` 下 `orchestrator.py` 的**第一行启动横幅**含 ¥ →
   `orchestrator.py --dry-run` 被管道捕获时直接退 1，流水线根本起不来；
2. `quality_gate.py` 的**通过行**含 ✅ → 门禁明明通过，退出码却是 1（假红），
   而 release-check / CI 正是靠退出码判定。

第 2 条还说明一件事：**门禁的退出码不该由一行日志的字符集决定**。

## 用法

在进程入口调一次（幂等，重复调用无副作用）：

    from utils.console import ensure_utf8_stdout
    ensure_utf8_stdout()

`utils/llm_client` 在 import 时也会调它 —— 该模块被所有会跑 LLM 的脚本导入，
是一条覆盖面最广的兜底（阶段脚本、精修脚本、审稿脚本、GUI 服务都在内）。
刻意**不改 errors**：默认是 strict，而 UTF-8 能编码任何正常字符；
把 errors 改成 replace 反而会在协议流（MCP stdio）上静默毁数据。
"""
import sys


def ensure_utf8_stdout():
    """把 stdout/stderr 切到 UTF-8。任何失败都静默忽略（不能因日志而死）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:                                   # noqa: BLE001
            pass
