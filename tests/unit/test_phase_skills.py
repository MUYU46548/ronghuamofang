# -*- coding: utf-8 -*-
"""阶段 skill 包的双向同步判据（2026-10-21，roadmap 10.2）。

## 为什么值得写

阶段 skill 是给**外部 Agent**看的编排卡 —— 它写错一个工具名，Agent 照卡调用
就是 "unknown tool"，而且这种错只有真机跑一次才暴露（2026-10-21 实测抓到：
nf_run_stage 的描述一直指向 nf_export_markdown，而该工具从未注册进 MCP_TOOLS）。

双向判据：
1. **skill → MCP**：卡里提到的每个 `nf_*` 工具名必须在 `nf_mcp.MCP_TOOLS` 里
   （防文档写不存在的工具）。
2. **MCP → skill**：`MCP_TOOLS` 里每个工具至少被一张卡提到
   （防"新增工具而 skill 未同步"—— 工具加了没人知道等于没加）。

用法：python tests/unit/test_phase_skills.py
"""
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = ROOT / "skills"
PHASE_SKILLS = ("nf-plan", "nf-write", "nf-revise", "nf-memory", "nf-project")

PASS, FAIL = [], []

# 模块/脚本名不是工具名 —— 卡里引用 `nf_mcp.MCP_TOOLS` 是对的，别误判成虚构工具。
NON_TOOL_TOKENS = {"nf_mcp", "nf_api", "nf_ctl"}


def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("" if detail == "" else "  -> %r" % (detail,)))


def main():
    print("=" * 62)
    import nf_mcp
    tool_names = {t["name"] for t in nf_mcp.MCP_TOOLS}

    print("\n[P1] 五张卡齐且有 frontmatter")
    texts = {}
    for name in PHASE_SKILLS:
        p = SKILLS_DIR / name / "SKILL.md"
        check("P1 %s 存在" % name, p.exists(), p)
        if p.exists():
            texts[name] = p.read_text(encoding="utf-8")
    if len(texts) != len(PHASE_SKILLS):
        print("  （缺卡，后续断言跳过）")
    else:
        for name, t in texts.items():
            check("P1 %s 有 frontmatter name/description" % name,
                  t.startswith("---") and "name: " in t.split("---")[1]
                  and "description: " in t.split("---")[1])

        print("\n[P2] skill → MCP：卡里的工具名必须真实存在")
        mentioned = {}
        for name, t in texts.items():
            found = {f for f in re.findall(r"\bnf_[a-z][a-z_]*\b", t)
                     if f not in NON_TOOL_TOKENS}
            mentioned[name] = found
            fake = {f for f in found if f not in tool_names}
            check("P2 %s 无虚构工具名" % name, not fake, fake)

        print("\n[P3] MCP → skill：每个工具至少被一张卡提到")
        all_mentioned = set().union(*mentioned.values()) if mentioned else set()
        missing = tool_names - all_mentioned
        check("P3 无工具被漏挂（新增工具须同步进卡）", not missing, sorted(missing))
        print("  （%d 个工具全部有卡承接）" % len(tool_names) if not missing else "")

        print("\n[P4] 编排不代跑：卡里不得出现自主循环/代批指令")
        # ⚠️ 只拦**肯定式**授权（"循环执行直到完成"）。否定语境（"不写自主循环"、
        # "不能代批"）恰恰是纪律本身 —— 粗词表会把纪律文本判成越权（实测误伤）。
        banned_pat = re.compile(
            r"(?<!不)(?<!没)(?<!禁止)(?<!不得)(?<!不要)(?<!不写)"
            r"(可以|应当|应该|直接|一律)?\s*(自主循环|循环执行直到|自动审批|代为审批|跳过审批门)")
        for name, t in texts.items():
            hit = [m.group(0) for m in banned_pat.finditer(t)]
            check("P4 %s 无越权措辞" % name, not hit, hit)

        print("\n[P5] 每张卡都写明停下来问作者的场景")
        for name, t in texts.items():
            check("P5 %s 有停下来问作者一节" % name,
                  ("停下来" in t or "必须停下来" in t) and "作者" in t)

    print("\n" + "=" * 62)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
