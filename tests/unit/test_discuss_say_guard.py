# -*- coding: utf-8 -*-
"""`/discuss/say` 的作者闸（C12，2026-11-16）。

## 要治的病

`POST /discuss/say {role:"author", text:"…"}` 此前没有任何来源判断：
本机任何进程（外部 Agent / 脚本）都能往讨论稿里写一句"作者原话"。
危害不是"多一行字"：讨论稿会被当**记忆**喂给之后所有轮次（会话体），
一句伪造的作者发言会污染后续全部判断，而且不可追溯 ——
作者本人也不会记得自己没说过那句话。

## 判据（每条都能变红）

- S1 外部来源写 author → 403（NOT_GUI / GUI_TOKEN_MISSING）
- S2 外部来源写 ai → 200（作者 2026-11-16 拍板：要让 Agent 主动带方向）
- S3 界面双因子（来源头 + 令牌）写 author → 200
- S4 来源头对但令牌不匹配 → 403 GUI_TOKEN_STALE（升级后旧页面就是这个）
- S5 MCP 工具说明里不再把 author 当可选值（否则外部 Agent 会一直撞 403）

用法：python tests/unit/test_discuss_say_guard.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api                                                # noqa: E402
from utils import agent_guard, discussion                    # noqa: E402
from nf_api_domains import discuss_thread as dom             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


class H:
    """最小 handler 替身：只需要 headers。"""

    def __init__(self, headers=None):
        self.headers = headers or {}


def gui_headers(token):
    return {agent_guard.GUI_SOURCE_HEADER: "gui",
            agent_guard.GUI_TOKEN_HEADER: token}


def main():
    print("=" * 66)
    print("讨论发言的作者闸（C12）")
    tmp = Path(tempfile.mkdtemp())
    orig_root = nf_api.ROOT
    orig_token = agent_guard.issued_gui_token
    nf_api.ROOT = tmp
    discussion.open_thread(3, "作者闸", root=str(tmp))
    agent_guard.issued_gui_token = lambda: "TOK-123"
    base = {"chapter": 3, "topic": "作者闸", "text": "我今天不同意这么写"}

    try:
        code, data = dom.handle_discuss_say(H({}), dict(base, role="author"))
        check("S1 外部来源冒充作者被拒 403",
              code == 403 and data.get("code") in ("NOT_GUI", "GUI_TOKEN_MISSING"),
              (code, data))

        code, data = dom.handle_discuss_say(H({}), dict(base, role="ai"))
        check("S2 外部来源说 ai 放行", code == 200, (code, data))
        check("S2 那一轮真写进稿了", (data or {}).get("round") == 1, data)

        code, data = dom.handle_discuss_say(H(gui_headers("TOK-123")),
                                            dict(base, role="author"))
        check("S3 界面双因子写作者放行", code == 200, (code, data))

        code, data = dom.handle_discuss_say(H(gui_headers("WRONG")),
                                            dict(base, role="author"))
        check("S4 令牌不匹配 → GUI_TOKEN_STALE",
              code == 403 and data.get("code") == "GUI_TOKEN_STALE", (code, data))

        code, data = dom.handle_discuss_say(
            H({agent_guard.GUI_SOURCE_HEADER: "agent"}), dict(base, role="author"))
        check("S4b 声明 agent 来源也拒（不是只看头子凑数）",
              code == 403, (code, data))

        mcp = (ROOT / "scripts" / "nf_mcp.py").read_text(encoding="utf-8")
        check("S5 MCP 说明里不再把 author 列成可选值",
              "ai（默认）/ author / editor" not in mcp, "描述仍是三选一")
        check("S5 MCP 说明写清 author 会被拒", "author 会被拒（403）" in mcp)
    finally:
        agent_guard.issued_gui_token = orig_token
        nf_api.ROOT = orig_root

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
