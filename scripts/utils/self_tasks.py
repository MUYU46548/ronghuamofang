# -*- coding: utf-8 -*-
"""engine: self 的任务书产出（**零 token**，唯一实现）。

## 这是什么

`engine: self` 的语义：程序**不调用任何模型**，只把“这一步该写什么、写到哪、
什么样算合格”写成任务书；调用方（人或外部 Agent）执行完、把产物写到位后，
程序下次运行时按盘上产物续跑。

本模块只做三件事，全部是确定性文件 IO：

1. `write_task(...)` —— 给任务书加“交回协议”头（目标产物 / 合格判据 / 自检命令）
   后落盘；
2. `strip_protocol(text)` —— 去掉协议头，取回**原任务书正文**（与各阶段
   `build_*_task` 的产物逐字相同 —— 任务书不许出现"self 专用版本"的分叉文本）；
3. `summarize(...)` —— 统一 `emit_tasks` 的返回形状。

## 为什么单独立模块

它被 6 个阶段共用，且要能被单测直接打（判据 T1~T4 见
`tests/unit/test_engine_self.py`）。放进任一 stage 模块都会让其余五个反向依赖。

## 交回协议为什么这么写

`engine: self` 的完成判据是**盘上的产物**，不是执行方说"我完成了"。所以头部
第一句就写明这一点：报告会撒谎，产物不会；写错路径等于没写。
"""
from pathlib import Path

#: 任务书根目录（相对项目根）。刻意与 hermes 的 `data/state/tasks/` 分开：
#: 两者混在一个目录里，清理任务文件时会把另一引擎的产物一起删掉。
TASK_ROOT = "data/state/tasks/self"

#: 协议头与正文的分隔线（strip_protocol 按它切）。必须是正文里不会出现的形态。
SEPARATOR = "\n<!-- ==== 以上是交回协议，以下是任务正文 ==== -->\n\n"

#: 各阶段通用的自检命令。`engine: self` 下重跑 orchestrator 是**零 token** 的：
#: 产物齐了就直接放行，不齐就重新出任务书 —— 所以它就是最好的自检工具。
CHECK_CMD = "python scripts/orchestrator.py"


def stage_dir(stage, task_dir=None):
    """该阶段任务书目录（自动建）。`task_dir` 给根目录，内部再按 `stage{N}` 分。"""
    root = Path(task_dir or TASK_ROOT)
    d = root / ("stage%d" % int(stage))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _as_list(targets):
    if not targets:
        return []
    if isinstance(targets, str):
        return [targets]
    return [str(t) for t in targets]


def render_header(stage, name, targets, criteria, check=None, note=None):
    """交回协议头（纯文本，人读；不参与任何解析）。"""
    lines = [
        "# 交回协议（engine: self —— 程序不调用任何模型）",
        "",
        "- 阶段：%s（%s）" % (stage, name),
        "- 执行完必须出现的产物（相对项目根，**只认这些路径**）：",
    ]
    for t in _as_list(targets):
        lines.append("  - `%s`" % t)
    lines.append("- 合格判据：%s" % criteria)
    lines.append("- 自检命令：`%s`" % (check or CHECK_CMD))
    lines.append("- 交回方式：按上面的路径把产物写进项目；不要写到别处，也不要在对话里"
                 "复述“已完成”就算数 —— 程序只认盘上的产物，不认执行方的成功报告。")
    if note:
        lines.append("- 附注：%s" % note)
    lines.append("")
    return "\n".join(lines)


def write_task(stage, name, body, targets, criteria, check=None, note=None,
               task_dir=None, stage_name=None):
    """把任务书正文加协议头后落盘。返回任务记录：

    `{"path": 相对路径, "target": 产物相对路径或列表, "check": 自检命令}`

    **幂等**：同一 stage/name 再写一次是覆盖（内容一致时逐字节相同），
    这样"产物还缺 → 重跑再出任务书"不会留下第二份说法不同的任务书。
    """
    d = stage_dir(stage, task_dir)
    p = d / name
    header = render_header(stage, stage_name or "阶段%s" % stage, targets,
                           criteria, check=check, note=note)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + SEPARATOR + body, encoding="utf-8")
    tg = _as_list(targets)
    return {"path": p.as_posix(),
            "target": tg[0] if len(tg) == 1 else tg,
            "check": check or CHECK_CMD}


def strip_protocol(text):
    """去掉交回协议头，返回原任务书正文（用于校验"没有 self 专用分叉文本"）。"""
    idx = text.find(SEPARATOR)
    if idx < 0:
        return text
    return text[idx + len(SEPARATOR):]


def summarize(stage, tasks, hint="", done=False, blocked=""):
    """`emit_tasks` 的统一返回形状（orchestrator 只读这几个键）。

    - `done=True`   —— 本阶段无可做的事（产物已是完整的 / 素材无变化），直接放行；
    - `blocked`     —— 需要作者动手才能继续（如 materials/raw 为空），原文打印给人看。
    """
    return {"stage": int(stage), "tasks": list(tasks or []),
            "hint": hint, "done": bool(done), "blocked": blocked}
