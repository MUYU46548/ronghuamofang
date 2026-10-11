# -*- coding: utf-8 -*-
"""GUI「复制给外部 Agent」两处出口的卫生自检（2026-10-11 用户实测事故）。

## 事故与防线

1. **阶段复制把模板当任务发出去**：流水线页签的复制按钮原样复制
   `prompts/stageN_*.md` 正文，里面的花括号占位符只有流水线渲染时才填 ——
   外部 Agent 拿到后按 kickoff 红线判「这是模板不是任务」，**试跑一步没走就炸**
   （用户原话）。现在复制的是**任务简报**（项目根 / 命令 / 产物 / 退出码），
   模板留给 Agent 自己按需读。本用例钉死：简报正文里**不得出现成对花括号**，
   且 `copyStagePrompt` 不再去取模板正文。
2. **启动提示词带了给人看的说明书**：`prompts/agent_kickoff.md` 开头的
   「用法」引言是写给**人**的，原样复制会让接手的 Agent 先读到一段操作说明。
   现在展示与复制都走 `kickoffBody()`，只取 `## 提示词正文` 之后的内容。
   本用例钉死：标记常量在、两处调用它、且标记行本身真的存在于源文件
   （标记被人删了 = 剥离静默失效，退回原文复制）。

零 LLM、零网络、零服务：只读两个文件的源码做静态断言。

用法：python tests/unit/test_gui_agent_copy.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
APP_VUE = ROOT / "console" / "src" / "App.vue"
KICKOFF = ROOT / "prompts" / "agent_kickoff.md"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


def _slice(src, start_marker, end_marker):
    i = src.index(start_marker)
    j = src.index(end_marker, i + len(start_marker))
    return src[i:j]


def main():
    print("=== 1. 文件在位 ===")
    check("console/src/App.vue 可读", APP_VUE.is_file(), str(APP_VUE))
    check("prompts/agent_kickoff.md 可读", KICKOFF.is_file(), str(KICKOFF))
    if not (APP_VUE.is_file() and KICKOFF.is_file()):
        print("FAIL: 缺文件，无法继续")
        return 1
    app = APP_VUE.read_text(encoding="utf-8")
    kick = KICKOFF.read_text(encoding="utf-8")

    print("\n=== 2. 阶段复制 = 任务简报，不是模板正文 ===")
    try:
        brief = _slice(app, "function stageTaskBrief(", "async function copyStagePrompt")
    except ValueError:
        brief = ""
    check("stageTaskBrief 存在", bool(brief), "" if brief else "找不到函数")
    check("简报正文无成对花括号（外部 Agent 的停下判据，出现即白改）",
          "{{" not in brief and "}}" not in brief,
          brief[brief.find("{{"):brief.find("{{") + 60] if "{{" in brief else "")
    for key, label in (("orchestrator.py --stage", "执行命令"),
                       ("预期产物", "产物"),
                       ("退出码", "退出码"),
                       ("项目根", "项目根")):
        check("简报含" + label + "（" + key + "）", key in brief)
    check("简报指出模板位置（Agent 可自行读作背景）", "prompts/" in brief and ".md" in brief)

    try:
        copy_fn = _slice(app, "async function copyStagePrompt", "function kickoffBody")
    except ValueError:
        copy_fn = ""
    check("copyStagePrompt 存在", bool(copy_fn))
    check("复制出口不再请求 /prompts/get（不再把模板正文塞剪贴板）",
          "/prompts/get" not in copy_fn)
    check("复制出口走 stageTaskBrief", "stageTaskBrief(" in copy_fn)

    print("\n=== 3. 启动提示词：人看的说明不进剪贴板 ===")
    check("KICKOFF_MARKER 常量在位", 'const KICKOFF_MARKER = "## 提示词正文"' in app)
    check("剥离按整行标题锚定匹配（说明文字里引用这行字也不会被下刀）",
          r"/^[ \t]*## 提示词正文[ \t]*$/m" in app)
    check("展示与复制都走 kickoffBody（所见即所贴）",
          app.count("kickoffBody(") >= 3)      # 定义 1 + loadKickoff 1 + copyKickoff 1
    # 与 JS 同规则取正文：第一个**独立成行**的标记之后
    lines = kick.splitlines()
    heads = [i for i, ln in enumerate(lines) if ln.strip() == "## 提示词正文"]
    check("源文件里标记作为独立标题存在（标记被删 = 剥离静默失效）", len(heads) >= 1, heads)
    if heads:
        body = "\n".join(lines[heads[0] + 1:]).strip()
        header = "\n".join(lines[:heads[0]])
        check("正文首行是提示词本体，不是说明残句", body.startswith("你现在操作的是"),
              body[:60])
        check("正文里没有给人看的复制说明", "不进剪贴板" not in body and "把下方" not in body)
        check("标记之前是给人看的用法说明（存在才有剥离的意义）", "用法" in header)
        check("正文里不再出现「把下方…复制」这类给人的指令",
              "把下方「提示词正文」全文复制" not in body)

    print("\n" + "=" * 62)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
