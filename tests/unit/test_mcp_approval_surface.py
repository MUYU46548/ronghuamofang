# -*- coding: utf-8 -*-
"""MCP 暴露面自检：**审批/项目/模式/止烧类端点永不暴露给外部 Agent**（零网络）。

## 为什么值得一条结构性用例

MCP 是外部 Agent（Hermes 等）够到本项目的**最外层**通道。项目的安全边界写着
「外部 Agent 可读、可跑流水线，但不能代替用户审批」，靠的是两件事：
① `nf_api` 对禁止端点返回 403；② **MCP 白名单里根本不出现这些端点**。
②比①更硬（够都够不着），但它是一张**手写清单** —— 加新工具时手一滑就漏出去了，
而单测全绿、`tools/list` 也正常，只有真人拿 Agent 去审批才会发现。

所以这里用**判据**而不是靠自觉：
- 白名单里**任何**工具都不得指向禁止清单里的路径（含 `/config/token_limit`
  —— 外部 Agent 不得抬高自己的闸门）；
- 禁止清单一处定义（`agent_guard.FORBIDDEN_IN_AGENT_MODE`），本用例直接读它
  （不复制一份字面量，否则清单改了这里不会跟着变）；
- 每个 HTTP 型工具的目标端点必须**真的存在于 `nf_api` 的分发链**里（防"白名单登记了
  一个不存在的端点"这种死工具）；
- 工具数量**不写死**（以 `nf_mcp.MCP_TOOLS` 为唯一事实来源），但要有下限护栏。

用法：python tests/unit/test_mcp_approval_surface.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import agent_guard  # noqa: E402
import nf_mcp  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


def tool_path(t):
    """取工具的目标端点（HTTP loopback 型才有）。"""
    h = t.get("_http")
    if isinstance(h, (tuple, list)) and len(h) >= 2:
        return str(h[1])
    return None


def main():
    print("=" * 62)
    print("  MCP 暴露面自检（禁止端点永不外露）")
    print("=" * 62)

    tools = list(nf_mcp.MCP_TOOLS)
    names = [t.get("name") for t in tools]
    forbidden = set(agent_guard.FORBIDDEN_IN_AGENT_MODE)

    check("工具清单非空且有合理规模（≥10）", len(tools) >= 10, len(tools))
    check("工具名唯一", len(names) == len(set(names)), sorted(names))
    check("每个工具都有 name/description/inputSchema",
          all(t.get("name") and t.get("description") and isinstance(t.get("inputSchema"), dict)
              for t in tools))

    http_tools = [t for t in tools if tool_path(t)]
    print("  HTTP 型工具 %d 个 / 本地直调 %d 个" % (len(http_tools), len(tools) - len(http_tools)))
    check("存在 HTTP 型工具（白名单确实经 loopback 复用域模块）", len(http_tools) >= 5)

    # 核心判据 1：路径层面的禁止
    leaked = []
    for t in http_tools:
        p = tool_path(t)
        if p in forbidden:
            leaked.append("%s → %s" % (t["name"], p))
        # 前缀匹配也要拦：/project/create/xxx 这类带尾巴的写法同样不许
        for f in forbidden:
            if p == f or p.startswith(f.rstrip("/") + "/"):
                if "%s → %s" % (t["name"], p) not in leaked:
                    leaked.append("%s → %s" % (t["name"], p))
    check("**没有任何工具指向禁止端点**（审批/打回/项目/模式/止烧）", not leaked, leaked)

    # 核心判据 2：名称/描述层面也不许出现禁止语义（防改个名字绕过去）
    BANNED_WORDS = ("approve", "reject", "project_create", "project_archive",
                    "project_restore", "project_init", "agent_mode",
                    "token_limit", "set_token", "budget_set")
    hits = ["%s（%s）" % (t["name"], w) for t in tools for w in BANNED_WORDS
            if w in (t["name"] or "").lower()]
    check("工具**名字**里不出现禁止语义（防改名绕过）", not hits, hits)

    # 核心判据 3：禁止清单只有一份（在 agent_guard），MCP 侧不另立一份
    src = (REPO / "scripts" / "nf_mcp.py").read_text(encoding="utf-8")
    hard_coded = [f for f in forbidden if f in src]
    check("nf_mcp 里不硬编码任何禁止端点（禁止清单只在 agent_guard）",
          not hard_coded, hard_coded)
    check("nf_mcp 不定义自己的 FORBIDDEN_* 常量",
          "FORBIDDEN_IN_AGENT_MODE" not in src and "FORBIDDEN_PATHS" not in src)

    # 核心判据 4：白名单登记的端点必须真实存在（防死工具）
    api_src = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    known = set(re.findall(r'p\s*==\s*"([^"]+)"', api_src)) | \
        set(re.findall(r'p\.startswith\(\s*"([^"]+)"', api_src))
    dead = []
    for t in http_tools:
        p = tool_path(t)
        ok = p in known or any(p.startswith(k) for k in known if k.endswith("/"))
        if not ok:
            dead.append("%s → %s" % (t["name"], p))
    check("每个 HTTP 工具的端点在 nf_api 分发链里真实存在", not dead, dead)

    # 记录：本仓当前暴露面（数量不写死，只做下限与「禁止项为零」）
    print("\n  当前暴露的 %d 个工具：%s" % (len(tools), "、".join(sorted(names))))
    print("  禁止清单（%d 条）：%s" % (len(forbidden), "、".join(sorted(forbidden))))

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
