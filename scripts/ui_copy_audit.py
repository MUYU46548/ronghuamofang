# -*- coding: utf-8 -*-
"""界面文案全量清单 + 黑话清零检查（2026-11-05，task-20261104-010）。

## 为什么要有这个工具

作者的原话是“整个软件里看不懂的地方太多了，到处都不说人话”。
这类问题的根因不是某一句写错，而是**没人知道界面上一共有多少句话**：
改文案靠翻文件，漏掉的永远比改掉的多。

所以这里做两件事：

1. `--write`：把 `console/src/*.vue` 里**用户真能看到的字**抽出来，
   按文件 + 行号落成 `docs/ui-copy-audit.md`。模板文字、可访问性属性、
   脚本里的提示串都算；注释不算（注释是写给开发看的，术语随便用）。
2. `--check`：拿同一份抽取结果去撞 `BANNED` 名单。命中就退 1 并列出位置。
   名单里放的是**内部标识与开发黑话**（`job_id`、`stage4`、`落盘`、`兜底`……），
   不是领域词（“判定”“审批门”这类有手册解释的，归 `09-terms.md` 管）。

抽取规则刻意保守：宁可漏掉拼接出来的半句，也不把注释误判成界面文案
（注释里解释黑话是好事，误报会逼人把解释删掉）。

用法：
    python scripts/ui_copy_audit.py --write     # 刷新全量清单
    python scripts/ui_copy_audit.py --check     # 黑话清零（CI / 用例调用）
    python scripts/ui_copy_audit.py --check -v  # 连允许项一起打印
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "console" / "src"
OUT = ROOT / "docs" / "ui-copy-audit.md"

# 界面文字属性（能被人读到的才收）
ATTRS = ("title", "placeholder", "label", "aria-label", "alt")

# 名单：内部标识 / 开发黑话。命中即失败（改完必须清到 0）。
# 每条 = (说明, 正则)。正则只匹配“裸用”的形态，允许 `data/state/tasks/self/` 这类路径。
BANNED = [
    ("内部任务号字段", r"(?<![A-Za-z_])job(?:_id)?(?![A-Za-z_])"),
    ("内部阶段号", r"(?<![A-Za-z_])stage\s*\d"),
    ("内部节点键", r"act_[起承转合]"),
    ("内部参数名", r"(?<![A-Za-z_])feedback(?![A-Za-z_])"),
    ("英文术语未译", r"few-shot|burstiness"),
    ("体检内部代号", r"\bTHIN\b|OK 更多"),
    ("英文 provider", r"(?<![A-Za-z_])provider(?![A-Za-z_])"),
    ("英文 vault", r"(?<![A-Za-z_])vault(?![A-Za-z_])"),
    ("英文 LLM", r"\bLLM\b"),
    ("开发黑话", r"落盘|写盘|兜底|静默|口径|台账|子会话|墓碑|正典|管线|拼合|施工权"),
    ("开发黑话（第二批）", r"垫片|代拉|逃生门|单一事实源|红线|水章"),
    ("开发黑话（第三批）", r"阈值|链路|产物|审计|正例|缺料|锚定|存盘|轻链路|闸门|熔断|判据"),
    # ---- 2026-11-20 补：英文标识。此前整份名单只有中文词，于是界面上印着
    # `electron-updater` / `engine=direct` / `sandbox_review.py` / `平台 win32`
    # 的时候，检查仍然报“0 命中”—— 检查全绿不等于界面干净。
    ("脚本文件名", r"[\w\-]+\.(?:py|ps1|bat|sh|cmd)\b"),
    ("依赖包名", r"electron-updater|app\.asar|node_modules"),
    ("编程术语", r"字面量|frontmatter|cache_read|max_tokens|toFixed|dry-run"),
    ("平台标识", r"\bwin32\b|\bdarwin\b"),
    ("配置项赋值", r"\b(?:engine|provider|model|budget|stage)\s*=\s*[A-Za-z0-9_]"),
]

# 债务：确实是界面上的内部路径（`data/chapters/raw/` 这类），但一次改不完。
# **不阻塞提交，但每次都要报数量** —— 不报就等于假装它不存在。
# 为什么不一并设成阻塞：64 处分布在 30 多个页面，一轮改不完；
# 阻塞项一旦常红，最后一定被人注释掉（docs/ui-copy-decisions.md 第五节的教训）。
DEBT = [
    ("内部路径", r"(?:^|[\s(（\"'：:])(?:data|config|output|scripts|prompts"
                 r"|console|tests|materials|deliverables|logs)/[\w\-./]*"),
]

ALLOW = [
    # 路径 / 配置键 / 协议名里的英文是必要信息，不是黑话
    r"data/state/tasks/self/",
    r"\.env\b",
    r"\bMCP\b",
    r"\bHTTP\b",
    r"\bAPI\b",
    # 给外部 Agent 的任务书正文（流水线页“复制任务书”）：它本来就是给机器/协作者
    # 读的执行说明，里面必须有命令行。只放行这一条明确形态，不连带放开别的脚本名。
    r"orchestrator\.py --stage",
    # 纯节点键（不渲染），只是脚本里的映射 key
    r"^act_[起承转合]$",
]
# 注：2026-11-20 把 `config/(system|project)\.yaml` 从允许项里删了 ——
# 它恰恰是问题表 P1-20 点名的坏例子（“也可以手工改 config/project.yaml 的 book.name”），
# 允许它等于把要治的病写进豁免表。


def check_text(s: str) -> str:
    """检查用文本：去掉 ${...} 插值 —— 渲染出来的是值，不是字段名。"""
    return re.sub(r"\$\{[^}]*\}", "…", s)


def strip_comments(text: str) -> str:
    """去掉 JS 注释，但保留行数与字符位置（行号才不会错）。"""
    text = re.sub(r"/\*[\s\S]*?\*/", lambda m: "\n" * m.group(0).count("\n"), text)
    text = re.sub(r"(?m)(?<![:'\"])//[^\n]*", "", text)
    text = re.sub(r"<!--[\s\S]*?-->", lambda m: "\n" * m.group(0).count("\n"), text)
    return text


def visible_strings(text: str):
    """yield (lineno, kind, s)；kind ∈ 界面文字 / 提示属性 / 脚本提示。"""
    starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def lineno(pos: int) -> int:
        import bisect
        return bisect.bisect_right(starts, pos)

    for tm in re.finditer(r"<template>([\s\S]*)</template>", text):
        base, body = tm.start(1), tm.group(1)
        clean = re.sub(r"<!--[\s\S]*?-->", lambda m: " " * len(m.group(0)), body)
        for m in re.finditer(r">([^<>{}]+)<", clean):
            s = " ".join(m.group(1).split())
            # 2026-11-20：原来只收“含中文”的节点，于是 `<span>electron-updater</span>`
            # 这类**纯英文**界面文字从源头就不在清单里，黑名单再全也永远匹配不到。
            # 现在含两个以上连续字母也收（`A+` 这类单个字母的按钮不误收）。
            if re.search(r"[\u4e00-\u9fff]", s) or re.search(r"[A-Za-z]{2}", s):
                yield lineno(base + m.start(1)), "界面文字", s
        for m in re.finditer(
                r'(?:%s)="([^"]*[\u4e00-\u9fff][^"]*)"' % "|".join(ATTRS), clean):
            yield lineno(base + m.start(1)), "提示属性", " ".join(m.group(1).split())

    for sm in re.finditer(r"<script[^>]*>([\s\S]*?)</script>", text):
        base, body = sm.start(1), strip_comments(sm.group(1))
        for m in re.finditer(r"""(['"`])((?:\\.|(?!\1)[^\\])*?)\1""", body):
            s = m.group(2)
            if re.search(r"[\u4e00-\u9fff]", s) and len(s) < 300:
                yield lineno(base + m.start(2)), "脚本提示", " ".join(s.split())


def collect():
    rows = []
    for f in sorted(SRC.glob("*.vue")):
        for ln, kind, s in visible_strings(f.read_text(encoding="utf-8")):
            rows.append((f.name, ln, kind, s))
    return rows


def write_md(rows, out: Path = OUT) -> None:
    by_file = {}
    for fn, ln, kind, s in rows:
        by_file.setdefault(fn, []).append((ln, kind, s))
    lines = [
        "# 界面文案全量清单",
        "",
        "> 由 `python scripts/ui_copy_audit.py --write` 生成，**不要手改**。",
        "> 只收用户看得见的字：模板文字、提示属性、脚本里的提示串；注释不算。",
        "> 术语的改法见 `console/src/help/09-terms.md`。",
        "",
        "合计 **%d** 条（%d 个文件）。" % (len(rows), len(by_file)),
        "",
    ]
    for fn, items in by_file.items():
        lines += ["## %s" % fn, "", "| 行 | 类型 | 文案 |", "|---|---|---|"]
        for ln, kind, s in items:
            cell = s.replace("|", "\\|")
            lines.append("| %d | %s | %s |" % (ln, kind, cell))
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("已写入 %s（%d 条，%d 个文件）" % (out, len(rows), len(by_file)))


def scan():
    """跑一遍名单，返回 (rows, hits, allowed, debt)。

    2026-11-20 抽出来给 `nfctl check` 用：工具能红但没人跑，等于没有回归保护
    （实测 `--check` 一直是绿的，而界面上印着 electron-updater / engine=direct）。
    """
    rows = collect()
    hits, allowed, debt = [], 0, []
    for fn, ln, kind, s in rows:
        probe = check_text(s)
        waived = any(re.search(a, probe) for a in ALLOW)
        for label, pat in DEBT:
            m = re.search(pat, probe)
            if m and not waived:
                debt.append((fn, ln, label, m.group(0), s))
                break
        for label, pat in BANNED:
            m = re.search(pat, probe)
            if not m:
                continue
            if waived:
                allowed += 1
                continue
            hits.append((fn, ln, label, m.group(0), s))
            break
    return rows, hits, allowed, debt


def check(verbose: bool = False) -> int:
    rows, hits, allowed, debt = scan()
    print("=" * 66)
    print("界面文案黑话清零")
    print("=" * 66)
    print("扫描 %d 条可见文案，命中 %d 条，允许 %d 条，债务 %d 条"
          % (len(rows), len(hits), allowed, len(debt)))
    if verbose:
        for fn, ln, label, word, s in hits:
            print("  [%s] %s:%d  “%s” %s" % (label, fn, ln, word, s[:90]))
    elif hits:
        for fn, ln, label, word, s in hits[:40]:
            print("  %s:%d  [%s] “%s” %s" % (fn, ln, label, word, s[:80]))
        if len(hits) > 40:
            print("  …… 另有 %d 条（-v 看全部）" % (len(hits) - 40))
    # 债务每次都要报：不阻塞，但绝不能让它安静地消失
    if debt:
        print("  -- 界面上的内部路径（不阻塞，已记入问题表） %d 条：" % len(debt))
        shown = debt if verbose else debt[:8]
        for fn, ln, label, word, s in shown:
            print("     %s:%d  “%s”" % (fn, ln, word))
        if len(debt) > len(shown):
            print("     …… 另有 %d 条（-v 看全部）" % (len(debt) - len(shown)))
    print("=" * 66)
    return 0 if not hits else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="界面文案全量清单 + 黑话清零")
    ap.add_argument("--write", action="store_true", help="刷新 docs/ui-copy-audit.md")
    ap.add_argument("--check", action="store_true", help="撞黑话名单，命中退 1")
    ap.add_argument("--out", default=str(OUT), help="清单落点（默认 docs/ui-copy-audit.md）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if not (args.write or args.check):
        ap.print_help()
        return 2
    rc = 0
    if args.write:
        write_md(collect(), Path(args.out))
    if args.check:
        rc = check(args.verbose)
    return rc


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
