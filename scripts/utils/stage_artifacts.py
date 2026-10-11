# -*- coding: utf-8 -*-
"""阶段产物判定（唯一真源，2026-10-11 欠账甲2）。

## 为什么独立成模块

`STAGE_ARTIFACTS` 此前住在 `nf_api.py`，但**完成判定的咽喉在 orchestrator** ——
而 nf_api 顶层 `from orchestrator import run`（nf_api → orchestrator 单向），
orchestrator 反向 import nf_api 会成环。故把产物表与判定抽到 utils 单源，
nf_api 与 orchestrator 各自引用同一份。

## 判据（确定性，零 LLM）

- 路径不存在 → 记「缺失」
- 目录存在但空 → 记「空目录」（原行为保留）
- **文件存在但为空/全空白 → 记「空文件」（本次新增）** —— 0 行产物此前会
  被 exists() 放行并一路走完审批门（2026-10-11 实证事故：0 行 polish_report），
  完成判定必须看得见「有文件但没内容」。
"""
from pathlib import Path

# 各阶段「应该有」的产物（目录只判是否为空；文件判存在且非空）
STAGE_ARTIFACTS = {
    1: ["data/setting/setting.json"],
    2: ["data/outline/global.md"],
    3: ["data/outline/chapters"],
    4: ["data/chapters/raw"],
    5: ["data/chapters/checked"],
    6: ["data/chapters/refined"],
    7: ["output"],
}


def missing_artifacts(stage, root):
    """返回该阶段缺失/为空的产物路径列表（确定性，不读语义）。

    root = 判定基准目录。**必须与产物写入端同域**：阶段产物由 stage* 以
    相对路径写在 orchestrator 进程 CWD 下（GUI spawn 时 cwd=项目根），
    所以 orchestrator 判 "."、nf_api 判它的 ROOT —— 各自与自己的写入/读取域对齐。
    """
    missing = []
    for rel in STAGE_ARTIFACTS.get(stage, []):
        p = Path(root) / rel
        if not p.exists():
            missing.append(rel)
        elif p.is_dir():
            if not any(p.iterdir()):
                missing.append(rel + "（空目录）")
        else:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            if not content.strip():
                missing.append(rel + "（空文件）")
    return missing
