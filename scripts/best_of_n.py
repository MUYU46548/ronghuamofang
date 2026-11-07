# -*- coding: utf-8 -*-
"""Best-of-N 的命令行入口（实现在 `utils/best_of_n.py`）。

为什么要这层薄壳：`utils/best_of_n.py` 是给 API / auto_rewrite 复用的库模块，
而作者要一个能直接敲的命令（`python scripts/best_of_n.py --chapter 3`）。
两份实现会漂移，所以这里只转发，不写第二套逻辑。

用法：
    python scripts/best_of_n.py --chapter 3 --n 3
    python scripts/best_of_n.py --chapter 3 --n 3 --dry-run
"""
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# 模块级自举（打包态 embeddable python 的 sys.path 不含脚本目录）：
# 必须写成**顶层语句**，护栏只认模块级 `sys.path.insert(...)`，包在 if 里的不算。
sys.path.insert(0, str(SCRIPT_DIR))

from utils.best_of_n import main                                        # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
