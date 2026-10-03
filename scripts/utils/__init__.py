# -*- coding: utf-8 -*-
"""NovelForge scripts.utils 包。

## 包级「控制台安全网」（2026-10-03）

导入本包的**任何**子模块都会触发 `ensure_utf8_stdout()`。为什么放在这里：

stdout 被捕获（GUI / 后台任务 / CI / 测试运行器）时 Python 用本地编码（本机 cp936），
而 `¥`(U+00A5) / `⚠️` / `💰` / `✅` / `❌` / `✓` 这些字符**不在 GBK 里** →
`UnicodeEncodeError`。实测后果都不是"日志难看"，而是**行为错**：

- `orchestrator.py --dry-run`（engine: hermes）第一行横幅就崩 → 流水线起不来；
- `quality_gate.py` 的**通过行**含 ✅ → 门禁明明通过却退 1（**假红**）；
- `leak_scan.py` 同理（0 命中却退 1）；
- `stage4` 的 KB 注入失败分支在 `except` 里打印 ⚠ → **异常处理自己变成新的异常源**。

项目里每个脚本都要读写文件/调 LLM，因而都会 `from utils...` 或 `import utils.x`
—— 本包初始化是覆盖面最广、且**只需维护一处**的挂钩（比在二十个 `main()` 里各写一行
可靠：新脚本自动获得保护，不会因为"忘了加"而留坑）。

⚠️ 刻意**不改 errors**：UTF-8 能编码任何正常字符；改成 `replace` 反而会在
MCP stdio 这类**协议流**上把数据静默毁掉（那比崩溃更坏）。
"""
import sys as _sys

__version__ = "0.1.0"

try:
    from utils.console import ensure_utf8_stdout as _ensure_utf8_stdout
    _ensure_utf8_stdout()
except Exception:                                   # noqa: BLE001
    # 兜底：绝不允许"日志编码"这种小事让整个包 import 失败。
    # （import 顺序异常时退回原地实现，语义与 utils.console 一致。）
    for _stream in (_sys.stdout, _sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:                           # noqa: BLE001
            pass
