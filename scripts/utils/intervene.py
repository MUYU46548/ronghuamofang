# -*- coding: utf-8 -*-
"""运行中的一次性指令（2026-11-02，不可用清单第 4 条）。

## 要治的病

用户原话的意思是：**运行中改不了方向。** 想调整只能等它跑完，然后重写已写的章。
`/intervene` 此前全项目 0 命中。

## 语义（刻意做窄）

- 指令**只对下一章生效**，不改大纲、不重写已写章节；
- **用掉即删**（一次性）：留着的"指令"会变成第二条大纲，谁也不知道它还算不算；
- 写作时贴在任务末尾并标明"作者当场要求"，与设定集 `locked` 冲突时以 locked 为准
  （硬约束不能被一句插话推翻，但要把它报出来）；
- 流水线**没在跑**时拒绝登记：那种"先存着以后用"的指令，到真跑时已经过时了，
  直接改大纲或提示词才是对的。

## 为什么不做队列

一次只能有一条。两条互相矛盾的要求同时挂着，等于让模型自己挑 ——
那就不是"作者改方向"，是"模型猜作者想改哪边"。
"""
import json
import time
from pathlib import Path

REL = Path("data") / "state" / "intervene.json"


def path(root=None):
    """指令文件路径。`root` 给了就用绝对路径（API 侧），否则按 CWD 相对（stage4 侧）。

    两边必须指向同一个文件 —— API 不 chdir，而 stage4 由 orchestrator 在项目根起。
    """
    return (Path(root) / REL) if root else REL


def read(root=None):
    """读当前指令（没有/损坏都返回 None，不抛）。"""
    p = path(root)
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not (data.get("instruction") or "").strip():
            return None
        return data
    except Exception:                                        # noqa: BLE001
        return None


def write(instruction, target_chapter=None, root=None):
    """登记一条指令。返回 dict（原样回给调用方做回执）。"""
    text = (instruction or "").strip()
    if not text:
        raise ValueError("指令不能为空")
    data = {
        "instruction": text[:2000],
        "target_chapter": target_chapter,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    p = path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


def consume(root=None):
    """取走并删除（写作时调用一次）。返回 dict 或 None。"""
    data = read(root)
    if data:
        try:
            path(root).unlink(missing_ok=True)
        except OSError:
            pass
    return data


def applies_to(data, chapter_no):
    """这条指令是不是给这一章的。

    `target_chapter` 为空 = 给"下一章"（写作时会话里最早到来的那一章）。
    """
    if not data:
        return False
    tgt = data.get("target_chapter")
    return tgt in (None, "", chapter_no)


def render_block(data):
    """把指令渲成任务里的一段。"""
    text = (data.get("instruction") or "").strip()
    quoted = "\n> ".join(text.splitlines())
    return (
        "\n\n## 作者当场要求（本次写作必须遵守）\n"
        "作者在运行中途提出的、只对**本章**生效的要求：\n\n"
        "> " + quoted + "\n\n"
        "它优先于章大纲里的写法（但不改大纲本身）。若与设定集里 locked 的事实冲突，"
        "**以 locked 为准**，并在章末摘要里写明冲突点 —— 不要自己抹平。\n")
