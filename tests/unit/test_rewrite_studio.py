# -*- coding: utf-8 -*-
"""改写工作室（C 组三个对标活）+ 卡片画布接线（2026-11-03）。

判据：
- W1 三种模式都能出任务书与讨论（零 token，不调模型）
- W2 任务书含"要求"与"不要做的事"，且明确"给出可直接采用的文字"
- W3 只讨论某一段（excerpt）时任务书里只有那段
- W4 没有正文的章 → 明确报错
- W5 非法 mode → 报错并列出可选值
- W6 接线：HTTP /rewrite/studio、MCP nf_rewrite_studio、界面 PlotBoard 存在
用法：python tests/unit/test_rewrite_studio.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import rewrite_studio, discussion                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name); print("  [PASS] " + name)
    else:
        FAIL.append(name); print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    tmp = Path(tempfile.mkdtemp()); root = str(tmp)
    d = tmp / "data" / "chapters" / "refined"; d.mkdir(parents=True)
    body = "## 第1章 雨夜\n\n叶清和把信摊在柜台上。\n\n陈姨在门口站着。\n"
    (d / "01.md").write_text(body, encoding="utf-8")

    print("【1】三种模式")
    for mode in ("sensory", "dehumanize", "reader"):
        r = rewrite_studio.build(mode, 1, root=root)
        check("W1 %s 出任务书" % mode, r.get("ok") and Path(r["task"]).exists(), r)
        txt = Path(r["task"]).read_text(encoding="utf-8") if r.get("ok") else ""
        check("W2 %s 含要求与禁止项" % mode, "## 要求" in txt and "不要做的事" in txt)
        check("W2 %s 要求给出可直接采用的文字" % mode, "可直接采用" in txt)
        check("W1 %s 开了讨论" % mode,
              any(s["topic"] == r["topic"] for s in discussion.list_threads(1, root=root)))

    print("【2】只讨论一段")
    r = rewrite_studio.build("dehumanize", 1, excerpt="陈姨在门口站着。", root=root)
    txt = Path(r["task"]).read_text(encoding="utf-8")
    check("W3 只包含摘录", "陈姨在门口站着。" in txt and "叶清和把信摊在柜台上" not in txt, txt[-200:])

    print("【3】边界")
    check("W4 没有正文 → 报错", rewrite_studio.build("sensory", 9, root=root).get("ok") is False)
    bad = rewrite_studio.build("telepathy", 1, root=root)
    check("W5 非法 mode → 列出可选", bad.get("ok") is False and "sensory" in bad["error"], bad)

    print("【4】接线")
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("W6 HTTP /rewrite/studio", '"/rewrite/studio"' in api)
    mcp = (ROOT / "scripts" / "nf_mcp.py").read_text(encoding="utf-8")
    check("W6 MCP nf_rewrite_studio", '"name": "nf_rewrite_studio"' in mcp)
    board = ROOT / "console" / "src" / "PlotBoard.vue"
    check("W6 卡片画布存在且挂进 App", board.exists() and
          "<PlotBoard" in (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8"))
    bt = board.read_text(encoding="utf-8")
    check("W6 卡片可点开讨论（复用讨论面板）", "DiscussThreadPanel" in bt and "@click=\"pick" in bt)
    check("W6 便签列出未定稿讨论", "stickies" in bt and "discuss/list" in bt)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL)); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
