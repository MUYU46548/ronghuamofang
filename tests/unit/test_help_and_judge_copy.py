# -*- coding: utf-8 -*-
"""内置手册 + 事件勾选文案（2026-10-31）。

## 两份东西，一条根因

用户的意见分两半：
  ① 「这一章发生了几件事」我看不懂；
  ② 本来这些东西都应该写在手册里持久化备查的。

②是①的根因 —— 界面上一句话讲不清判据，讲清了也会被忘掉。所以这轮做了：
  · 把三处黑话改成白话（「计入下限」→「不算进 2 件」；四类补例子；补「不算一件事」按钮）；
  · 加一份**随包内置、离线可查**的手册（`console/src/help/*.md` + `md.js` + `HelpPanel`）。

## 判据

- H1 手册文件齐全，且**判定那篇**真的回答了"几件事怎么算"（不是空目录占位）
- H2 渲染器：块级语法（标题/列表/表格/代码/引用）+ 行内（粗体/行内码/链接）
- H3 渲染器**先转义**（手册里贴用户来稿片段不该变成可执行 HTML）
- H4 入口三处齐（设置侧栏 / 命令面板 / 首启引导）
- H5 事件勾选的面板文案不含黑话（「计入下限」这类词必须消失）
- H6 「不算一件事」标记在前后端都通了（组件有按钮、后端收 rejected）

用法：python tests/unit/test_help_and_judge_copy.py
"""
import io
import re
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "console" / "src"
HELP = SRC / "help"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def run_node(js):
    """跑一段 node（渲染器是 ESM，Python 侧只能用 node 验）。"""
    try:
        p = subprocess.run(["node", "--input-type=module", "-e", js],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, cwd=str(SRC.parent))
        return (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:                                   # noqa: BLE001
        return "", str(e)


def main():
    print("=" * 66)
    print("内置手册 + 事件勾选文案")
    print("=" * 66)

    print("\n【H1】手册文件")
    mds = sorted(HELP.glob("*.md")) if HELP.is_dir() else []
    check("help/ 目录存在且有手册", len(mds) >= 4, [p.name for p in mds])
    titles = {}
    for p in mds:
        text = p.read_text(encoding="utf-8")
        m = re.search(r"^#\s+(.+)$", text, re.M)
        titles[p.stem] = (m.group(1).strip() if m else "")
        check("%s 有一级标题（目录靠它显示）" % p.name, bool(m.group(1) if m else ""),
              text[:40])
    check("有「判定怎么用」这一篇（且排在第 2 位，作者最常查）",
          any("judge" in k for k in titles) and list(titles)[1].endswith("judge"),
          list(titles))

    judge = (HELP / "02-judge.md")
    if judge.is_file():
        jt = judge.read_text(encoding="utf-8")
        # 用户卡住的三件事：动作 vs 事件、四类各是什么、勾完会怎样
        check("判定篇讲清「动作不算事」（用具体例子）",
              "不算" in jt and ("档案室" in jt or "抄下" in jt), jt[:80])
        check("判定篇列了四类，且每类带例子",
              all(k in jt for k in ("人物处境", "信息占有", "关系", "目标"))
              and "被抓" in jt, "")
        check("判定篇讲清「不计入」是什么意思（不是悄悄少算）",
              "不计入" in jt and "不算进" in jt, "")
        check("判定篇写了勾完之后落哪三档",
              "合格" in jt and "需定点重写" in jt and "需重写本章" in jt, "")
        check("判定篇写了「没勾完 = 待作者勾选，不是合格」",
              "待作者勾选" in jt, "")

    print("\n【H2/H3】渲染器")
    md = SRC / "md.js"
    check("md.js 存在", md.is_file())
    if md.is_file():
        js = (
            'import {renderMarkdown, inline} from ' + repr(md.as_uri()) + ';\n'
            'const src = [\n'
            '"# 标题", "", "- 一", "- 二", "", "1. 甲", "2. 乙", "",\n'
            '"| A | B |", "|---|---|", "| 1 | 2 |", "",\n'
            '"```py", "x = 1", "```", "", "> 引用", "", "普通 **粗** 和 `码` 与 [链](https://a)",\n'
            '].join("\\n");\n'
            'console.log(renderMarkdown(src));\n'
            'console.log("---XSS---");\n'
            'console.log(renderMarkdown("<script>alert(1)</script>"));\n'
        )
        out, err = run_node(js)
        if err:
            # node 不接受 file:// URI 直接 import 时退回到读源码再 eval
            src_js = md.read_text(encoding="utf-8")
            js2 = src_js + "\n" + (
                'const src = ["# 标题","","- 一","- 二","","1. 甲","2. 乙","",'
                '"| A | B |","|---|---|","| 1 | 2 |","","```py","x = 1","```","",'
                '"> 引用","","普通 **粗** 和 `码` 与 [链](https://a)"].join("\\n");\n'
                'console.log(renderMarkdown(src));\n'
                'console.log("---XSS---");\n'
                'console.log(renderMarkdown("<script>alert(1)</script>"));\n')
            out, err = run_node(js2)
        check("渲染器能跑（node 执行无错）", not err, err[:160])
        if out:
            before, _, after = out.partition("---XSS---")
            check("一级标题渲染为 h2（手册标题不跟页面 h1 抢层级）",
                  "<h2>标题</h2>" in before, before[:80])
            check("无序列表 / 有序列表都渲染",
                  "<ul" in before and "<ol" in before, "")
            check("表格渲染成真表格（手册里全靠表格对比）",
                  "<table" in before and "<th>A</th>" in before and "<td>1</td>" in before,
                  before[:120])
            check("代码块渲染为 pre/code",
                  "<pre" in before and "x = 1" in before, "")
            check("引用渲染为 blockquote", "<blockquote" in before, "")
            check("行内：粗体 / 行内码 / 链接",
                  "<b>粗</b>" in before and "<code>码</code>" in before
                  and 'href="https://a"' in before, before[-120:])
            check("**先转义**：<script> 不会变成真标签（手册可能贴用户来稿片段）",
                  "<script>" not in after and "&lt;script&gt;" in after, after[:120])

    print("\n【H4】入口三处")
    app = (SRC / "App.vue").read_text(encoding="utf-8")
    check("设置侧栏有「使用手册」分类", 'id: "help", name: "使用手册"' in app)
    check("命令面板有「打开使用手册」",
          'label: "打开使用手册"' in app and 'if (id === "manual") return openHelp()' in app)
    check("首启引导第 4 步有「使用手册」按钮",
          '<button class="mini" @click="openHelp">使用手册</button>' in app)
    check("openHelp 会切到设置页的手册分类",
          "function openHelp()" in app and 'selectSettingsTab("help")' in app)

    print("\n【H5】事件勾选文案：黑话必须消失")
    comp = (SRC / "JudgeEvents.vue").read_text(encoding="utf-8")
    check("「计入下限」这个说法已从界面文案里删掉（测试/注释里可以留）",
          "计入下限" not in code_only(comp), "界面上不该出现这个词")
    check("改成了「不算进 N 件」（能看懂的说法）", "不算进" in comp)
    check("常显一段判据说明（只数改变了状态的事 / 动作不算）",
          "改变了状态" in comp and "不算" in comp and "je-guide" in comp)
    check("四类各带例子（按钮 title + 选中后显示）",
          all(x in comp for x in ("被抓", "得知真相", "结盟", "决定复仇")))
    check("有「已过目 X/Y」进度（区分“没看”与“判断为不算”）",
          "已过目" in comp and "judgedCount" in comp)
    check("章纲零事件时说明为什么不能勾（不是空白）",
          "核心事件" in comp and "不可判定" in comp)

    print("\n【H6】「不算一件事」前后端都通")
    check("组件有该按钮且有互斥逻辑",
          "je-reject" in comp and "toggleReject" in comp and "delete checked.value[i]" in comp)
    rec = (ROOT / "scripts" / "utils" / "judge" / "records.py").read_text(encoding="utf-8")
    check("后端 set_events 收 rejected", "rejected=None" in rec and '"rejected": rejected' in rec)
    check("rejected 与 checked 互斥在服务端也有防御",
          'if int(x) not in checked' in rec)
    dom = (ROOT / "scripts" / "nf_api_domains" / "judge.py").read_text(encoding="utf-8")
    check("端点把 rejected 回传给界面（reload 后标记还在）",
          '"rejected": i in rejected' in dom and '"rejected": bool(i in _a_rejected)' in dom)
    check("judged / total 进度也回传", '"judged"' in dom and '"total_events"' in dom)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 66)
    return 0 if not FAIL else 1


def code_only(text):
    """去掉注释与模板里的 HTML 注释后再做字符串断言（注释里解释黑话是好事）。"""
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    return re.sub(r"(?m)^\s*(//|\*).*$", "", text)


if __name__ == "__main__":
    sys.exit(main())
