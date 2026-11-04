# -*- coding: utf-8 -*-
"""GUI-only 被拒时要说清“是哪一种拒”（2026-11-04，试跑第 21 条）。

## 要治的病

`/discuss/decide`（作者拍板）、服务商保存、止烧阈值这类端点在来源不可信时，
原先一律回一句话：“只能由作者在界面上做”。

问题：**作者本人点了也读这句**。他刚在桌面上点“定稿”，界面告诉他
“只能由作者在界面上做” —— 他会得出的结论是“讨论功能不可用”。

实际有三种情形，处置完全不同：

| 情形 | 真实原因 | 该怎么办 |
|---|---|---|
| `GUI_TOKEN_MISSING` | 主进程没签发界面令牌（客户端刚启动 / 没起来） | 重启客户端 |
| `GUI_TOKEN_STALE` | 令牌不匹配（升级或主进程重启过，旧页面还开着） | 刷新或重启 |
| `NOT_GUI` | 真不是 GUI（外部 Agent / CLI） | 保持原话 |

真机复现（18787 端口，临时 root）：`GET /discuss/list` 200、`POST /discuss/open` 200、
`POST /discuss/say` 200、`GET /discuss/read` 200（110 字），只有
`POST /discuss/decide` 403 —— 因为判据要求 GUI 双因子，而脚本只能带头不能带令牌。
所以“讨论不可用”在功能上是假的，卡的是**来源判据的文案**。

## 判据（每条都能变红）

- G1 没签发令牌 → `GUI_TOKEN_MISSING`，文案里有“重启”这个可执行动作
- G2 有令牌但来源头不是 gui → `NOT_GUI`，文案仍是“只能由作者”（对 Agent 说）
- G3 有令牌、有 gui 头但令牌不对 → `GUI_TOKEN_STALE`，文案里有“刷新/重启”
- G4 全对 → `(None, "")` 放行
- G5 `gui_only` 与 `/discuss/decide` 用的是**同一份**解释（不各写一套）

用法：python tests/unit/test_gui_refusal.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import agent_guard as G                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("" if cond else "  → %s" % (detail,))))


class H:
    def __init__(self, headers):
        self.headers = headers


def main():
    print("=" * 66)
    print("GUI-only 被拒的三种情形")
    real = G.issued_gui_token

    print("\n【G1】主进程没签发令牌（作者最常撞的那一种）")
    G.issued_gui_token = lambda: ""
    try:
        code, why = G.gui_refusal({"X-Mofang-Source": "gui"})
        check("G1 code = GUI_TOKEN_MISSING", code == "GUI_TOKEN_MISSING", (code, why))
        check("G1 文案给出可执行动作（重启）", "重启" in why, why)
        check("G1 不再说“只能由作者在界面上做”",
              "只能由作者" not in why, why)
        # 端点层同一份解释
        from utils import gui_guard
        deny = gui_guard.gui_only(H({"X-Mofang-Source": "gui"}), "服务商配置")
        check("G1 gui_only 也报 GUI_TOKEN_MISSING",
              deny and deny[1].get("code") == "GUI_TOKEN_MISSING", deny)
        check("G1 gui_only 的拒文里带上了端点名",
              deny and "服务商配置" in deny[1].get("error", ""), deny)
    finally:
        G.issued_gui_token = real

    print("\n【G2】真不是 GUI（外部 Agent / CLI）→ 保持原话")
    G.issued_gui_token = lambda: "tok-123"
    try:
        code, why = G.gui_refusal({"X-Mofang-Source": "agent"})
        check("G2 code = NOT_GUI", code == "NOT_GUI", (code, why))
        check("G2 文案仍是“只能由作者”", "只能由作者" in why, why)
        code2, why2 = G.gui_refusal({})
        check("G2 无来源头同样判 NOT_GUI", code2 == "NOT_GUI", (code2, why2))

        print("\n【G3】令牌过期/不匹配（旧页面还开着）")
        code3, why3 = G.gui_refusal({"X-Mofang-Source": "gui",
                                     "X-Mofang-Token": "old-token"})
        check("G3 code = GUI_TOKEN_STALE", code3 == "GUI_TOKEN_STALE", (code3, why3))
        check("G3 文案给出刷新/重启", ("刷新" in why3 or "重启" in why3), why3)

        print("\n【G4】全对 → 放行")
        code4, why4 = G.gui_refusal({"X-Mofang-Source": "gui",
                                     "X-Mofang-Token": "tok-123"})
        check("G4 返回 (None, '')", code4 is None and why4 == "", (code4, why4))
        from utils import gui_guard
        check("G4 gui_only 放行", gui_guard.gui_only(
            H({"X-Mofang-Source": "gui", "X-Mofang-Token": "tok-123"}), "x") is None)
    finally:
        G.issued_gui_token = real

    print("\n【G5】同一个解释只有一个出处（别各写一套）")
    src_g = (ROOT / "scripts" / "utils" / "gui_guard.py").read_text(encoding="utf-8")
    src_d = (ROOT / "scripts" / "nf_api_domains" / "discuss_thread.py").read_text(encoding="utf-8")
    check("G5 gui_guard 用 gui_refusal", "gui_refusal(headers)" in src_g)
    check("G5 discuss/decide 用 gui_refusal", "gui_refusal(h.headers)" in src_d)
    check("G5 两处都不再自己拼“只能由作者…”",
          "只能由作者在桌面上做" not in src_g and "只能由作者在界面上做" not in src_d)
    ag = (ROOT / "scripts" / "utils" / "agent_guard.py").read_text(encoding="utf-8")
    check("G5 解释函数只有一份", ag.count("def gui_refusal(") == 1)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
