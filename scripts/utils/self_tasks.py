# -*- coding: utf-8 -*-
"""engine: self 的任务书产出（**零 token**，唯一实现）。

## 这是什么

`engine: self` 的语义：程序**不调用任何模型**，只把“这一步该写什么、写到哪、
什么样算合格”写成任务书；调用方（人或外部 Agent）执行完、把产物写到位后，
程序下次运行时按盘上产物续跑。

本模块只做三件事，全部是确定性文件 IO：

1. `write_task(...)` —— 给任务书加“交回协议”头（目标产物 / 合格判据 / 自检命令）
   后落盘，**并把“输入文件”段引用的材料内联进正文**（见下）；
2. `strip_protocol(text)` —— 去掉协议头，取回**原任务书正文**（与各阶段
   `build_*_task` 的产物逐字相同 —— 任务书不许出现"self 专用版本"的分叉文本）；
3. `summarize(...)` —— 统一 `emit_tasks` 的返回形状。

## 材料靠注入，不靠路径（2026-11-12，问题总账第 2 条）

以前任务书只写“输入文件：`materials/raw/…`（用 read_file 读取）”—— 等于把找料
这一步丢给执行方。实测后果：AI 拿到任务书先 `grep` 整个仓库找线索，找到别的书、
别的稿，或者干脆按文件名猜。四颗钉子的第 ③ 条就是"材料靠注入不靠路径"。

现在 `write_task` 直接调 `llm_client.inline_inputs()`（**与直连引擎同一份实现**）：
把“## 输入文件”段列出的文件内容整段贴到文末【内联输入】区，执行方不用去读盘。
路径仍保留（作为出处），但正文里那句"（用 read_file 读取）"会被换成
"已内联，直接用"。

同一段内出现"有路径但格式不认"的行时，`inline_inputs` 会抛
`InputSectionFormatError` —— 这里**不吞**：材料构造错了当场停，比让执行方
基于空输入写一整章便宜得多。

## 为什么单独立模块

它被 6 个阶段共用，且要能被单测直接打（判据 T1~T6 见
`tests/unit/test_engine_self.py`）。放进任一 stage 模块都会让其余五个反向依赖。

## 交回协议为什么这么写

`engine: self` 的完成判据是**盘上的产物**，不是执行方说"我完成了"。所以头部
第一句就写明这一点：报告会撒谎，产物不会；写错路径等于没写。
"""
from pathlib import Path

#: 内联时替换“（用 read_file 读取）”的那句话。与直连引擎的说法分开写：
#: 直连下确实"没有文件工具"，self 下是"程序已经把料摆好了，别再去找"。
INLINE_NOTE = ("（本次为自任务引擎：下列文件的内容**已由程序内联在文末【内联输入】区**，"
               "直接使用；不要再自己去仓库里找文件，也不要按文件名猜内容）")

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
    lines.append("- 交回方式：按上面的路径把产物写进**候选区**；不要写到别处，也不要"
                 "在对话里复述“已完成”就算数 —— 程序只认作者批准过的候选版本，"
                 "不认执行方的成功报告。")
    if note:
        lines.append("- 附注：%s" % note)
    lines.append("")
    return "\n".join(lines)


def write_task(stage, name, body, targets, criteria, check=None, note=None,
               task_dir=None, stage_name=None, inline=True):
    """把任务书正文**内联材料 + 加协议头**后落盘。返回任务记录：

    `{"path": 相对路径, "target": 产物路径(或列表), "check": 自检命令,
      "inlined": 内联了几个文件, "missing": [没找到的输入]}`

    **材料靠注入**（2026-11-12）：正文里的“## 输入文件”段由
    `llm_client.inline_inputs()` 展开（与直连引擎同一份实现）—— 执行方拿到的是
    一份自包含的任务书，不需要去仓库里找文件。找不到的输入照实列在文末，
    不让执行方基于空输入硬写。

    **幂等**：同一 stage/name 再写一次是覆盖（内容一致时逐字节相同），
    这样"产物还缺 → 重跑再出任务书"不会留下第二份说法不同的任务书。
    """
    d = stage_dir(stage, task_dir)
    p = d / name
    inlined, missing = 0, []
    if inline and body:
        from utils.llm_client import inline_inputs
        try:
            refs = None
            from utils.llm_client import extract_input_paths
            refs = extract_input_paths(body)
            body, missing = inline_inputs(body, task_path=p.as_posix(), note=INLINE_NOTE)
            inlined = len(refs or []) - len(missing or [])
        except Exception as e:                                # noqa: BLE001
            # 格式不认 / 读不出来：**不吞**。材料构造错了必须当场停 ——
            # 让执行方基于空输入写一整章，比这里报错贵得多。
            print("[self_tasks] ERROR 任务书输入段无法内联（%s）：%s: %s"
                  % (name, type(e).__name__, str(e)[:400]))
            raise
    header = render_header(stage, stage_name or "阶段%s" % stage, targets,
                           criteria, check=check, note=note)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + SEPARATOR + body, encoding="utf-8")
    tg = _as_list(targets)
    return {"path": p.as_posix(),
            "target": tg[0] if len(tg) == 1 else tg,
            "check": check or CHECK_CMD,
            "inlined": inlined,
            "missing": list(missing or [])}


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
