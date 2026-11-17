# -*- coding: utf-8 -*-
"""读改链的 agent 面自检：**改一段这件事，Agent 够得着的必须"先看后落"，够不着的必须是作者动作**（零网络）。

## 为什么需要这条用例

2026-11-17 走查（`docs/读改链走查-20261117.md`）与当天实读发现：读改链的端点在盘上**全都有**，
但一个都没进 `nf_mcp.MCP_TOOLS` —— 于是外部 Agent（含 DSH 会话）手里只有 `nf_refine_paragraph`
那一条**直接覆写整章**的路（`preview=False`），看不到批注、看不到六项指标、看不到 diff，
等于"只给刀、不给尺"。这跟"Agent 能不能写"无关，是**接口面**的缺口。

病根同样是**两份清单**：端点在 `nf_api` 的分发链里存在，而它对 Agent 是否可调，
靠的是 `nf_mcp.MCP_TOOLS` 里那张**手写白名单** —— 加端点时手一滑就漏，单测全绿。

## 判据（会红）

1. 读改链每个端点必须落在两种状态之一，且**与代码事实一致**：
   - `mcp`：白名单里有工具指向它，方法一致，schema 覆盖它读的参数；
   - `gui_only`：白名单里没有工具指向它，**且**处理函数里真有 `_gui_only`（不是"没登记"冒充"不让调"）。
2. **不许留盲写入口**：任何工具都不得指向 `/refine/paragraph`（整章回写、正文立刻变）。
3. 链上端点必须真的存在于 `nf_api` 分发链（防"白名单登记了死端点"）。
4. `apply` 永不进白名单（采纳 = 作者拍板，与审批同族）。

用法：python tests/unit/test_read_modify_surface.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import nf_mcp  # noqa: E402

PASS, FAIL = [], []

#: 读改链：端点 → 应有的状态。改这张表等于改产品口径，必须连同代码一起改。
#: 状态语义见文件头；`apply` 的 `gui_only` 是设计（采纳是作者动作），不是欠账。
CHAIN = {
    "/chapters/paragraphs": "mcp",           # 段落编号与段落级字数
    "/chapters/paragraph/pending": "mcp",    # 待采用候选（改前/改后）
    "/chapters/paragraph/diff": "mcp",       # 这一段改了什么
    "/chapters/paragraph/preview": "mcp",    # 只算不落
    "/chapters/paragraph/drop": "mcp",       # 放弃候选（正文不动）
    "/chapters/annotations": "mcp",          # 未收口批注清单
    "/review/structure": "mcp",              # 六项分布型指标
    "/chapters/metrics/annotate": "mcp",     # 指标命中项 → 批注
    "/chapters/paragraph/apply": "gui_only",  # 采用候选：写正文，作者本人动作
}

#: 任何工具都不许指向的端点：盲写（正文立刻变、没有先看后落）。
NEVER_TARGETED = {"/refine/paragraph": "整章回写、preview=False —— 绕过了“先看候选”这道闸"}

#: 端点读的参数（只校验 key 存在；值域由端点自己判）。
PARAMS = {
    "/chapters/paragraphs": ["n"],
    "/chapters/paragraph/pending": ["chapter", "index"],
    "/chapters/paragraph/diff": ["n", "p"],
    "/chapters/paragraph/preview": ["chapter", "paragraph_index", "feedback"],
    "/chapters/paragraph/drop": ["chapter", "paragraph_index"],
    "/chapters/annotations": ["n"],
    "/chapters/metrics/annotate": ["n"],
    "/review/structure": [],
}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def tool_path(t):
    h = t.get("_http")
    if isinstance(h, (tuple, list)) and len(h) >= 2:
        return str(h[0]).upper(), str(h[1])
    return None, None


def dispatch_table():
    """(GET 路径集, POST 路径集) —— 从 nf_api.py 的分发链实读，不另立清单。"""
    src = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    idx = src.find("def do_POST")
    get_src, post_src = (src[:idx], src[idx:]) if idx > 0 else (src, src)
    pat = r'p\s*==\s*"([^"]+)"'
    return set(re.findall(pat, get_src)), set(re.findall(pat, post_src))


def handler_has_gui_only(endpoint):
    """端点对应处理函数里是否真有 `_gui_only` 守卫（不是"没登记"冒充"不让调"）。"""
    for f in (REPO / "scripts" / "nf_api_domains").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        m = re.search(r'\(\s*"(?:GET|POST)",\s*"%s",\s*(\w+)\)' % re.escape(endpoint), text)
        if not m:
            continue
        fn = m.group(1)
        body = text.split("def %s(" % fn, 1)[-1][:2500]
        return "_gui_only" in body
    return None


def main():
    print("=" * 62)
    print("  读改链 agent 面自检（先看后落 / 采纳归作者）")
    print("=" * 62)

    tools = list(nf_mcp.MCP_TOOLS)
    by_path = {}
    for t in tools:
        method, path = tool_path(t)
        if path:
            by_path.setdefault(path, []).append((t.get("name"), method, t))

    get_paths, post_paths = dispatch_table()
    router = get_paths | post_paths
    print("  白名单 %d 个工具（HTTP 型 %d）" % (len(tools), len(by_path)))

    print("\n[1] 链上端点必须真实存在（防白名单登记死端点）")
    phantom = [p for p in CHAIN if p not in router]
    check("1 链上端点都在 nf_api 分发链里", not phantom, phantom)

    print("\n[2] 每个端点只有一个答案：要么 mcp，要么 gui_only")
    wrong = []
    for p, want in CHAIN.items():
        hits = by_path.get(p, [])
        if want == "mcp":
            if not hits:
                wrong.append("%s 应为 mcp，实际没有任何工具指向它" % p)
                continue
            name, method, tool = hits[0]
            expect_method = "GET" if p in get_paths else "POST"
            if method != expect_method:
                wrong.append("%s 方法应为 %s，工具 %s 写的是 %s" % (p, expect_method, name, method))
            schema = tool.get("inputSchema") or {}
            props = schema.get("properties") or {}
            need = [k for k in PARAMS.get(p, []) if k not in props]
            if need:
                wrong.append("%s 的工具 %s 缺参数声明 %s" % (p, name, need))
            if schema.get("type") != "object":
                wrong.append("%s 的工具 %s inputSchema 不是 object" % (p, name))
        else:  # gui_only
            if hits:
                wrong.append("%s 不该暴露给 Agent，实际有工具 %s 指向它"
                             % (p, hits[0][0]))
            guard = handler_has_gui_only(p)
            if guard is not True:
                wrong.append("%s 登记为 gui_only，但处理函数里没有 _gui_only 守卫"
                             "（guard=%r）" % (p, guard))
    check("2 **每个链上端点状态与代码事实一致**", not wrong, wrong)

    print("\n[3] 不许留盲写入口（绕不过“先看后落”）")
    blind = []
    for p, why in NEVER_TARGETED.items():
        for name, _method, _t in by_path.get(p, []):
            blind.append("%s → %s（%s）" % (name, p, why))
    check("3 **没有任何工具直接覆写段落正文**", not blind, blind)

    print("\n[4] 采纳永不外露")
    check("4 /chapters/paragraph/apply 不在白名单",
          "/chapters/paragraph/apply" not in by_path,
          by_path.get("/chapters/paragraph/apply", []))

    print("\n[5] 只算不落链完整（预览 → 读候选 → 采纳）")
    pair = ("/chapters/paragraph/preview", "/chapters/paragraph/pending")
    check("5 预览与读候选成对暴露（缺一个模型就看不见自己改了什么）",
          all(p in by_path for p in pair),
          {p: p in by_path for p in pair})

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
