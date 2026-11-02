# -*- coding: utf-8 -*-
"""自己改一段（2026-11-02，不可用清单第 8 条）。

## 要治的病

以前只有一条路：选段 → 填一句意见 → 提交重写 job → 等 → 看 diff。
**没有"这一处我自己改"的路径** —— 改一个字也得让模型重跑一遍，改得怎么样全看它。
于是人只能"再许愿一次"。

## 判据（每条都能变红）

- M1 改第 2 段：只有第 2 段变，其它段一字不动
- M2 整章有备份（`.history/chXX_vN.md`）
- M3 旧内容进段落历史（手改也可回退）
- M4 空内容拒写
- M5 内容含空行拒写（那会变成两段）
- M6 段落号越界拒写
- M7 内容没变：返回成功但不写文件
- M8 端点与 Agent 禁止名单接线

用法：python tests/unit/test_paragraph_save.py
（会 chdir 到临时目录：refine_paragraph 的路径是 CWD 相对的）
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 66)
    print("自己改一段")
    print("=" * 66)
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.chdir(tmp)
            import refine_paragraph as rp

            chap = Path("data/chapters/refined/03.md")
            chap.parent.mkdir(parents=True, exist_ok=True)
            chap.write_text("## 第3章 试\n\n第一段原文。\n\n第二段原文，要改这一句。\n\n第三段原文。\n",
                            encoding="utf-8")
            before = chap.read_text(encoding="utf-8")

            # 段落切分把**标题行算第 0 段**（split_paragraphs 按空行切，
            # 前端用同一个函数，所以索引一致）。第 2 段才是"第二段原文"。
            # ---- M1 只改第 2 段 ----
            ok, msg = rp.save_paragraph_text(3, 2, "第二段我改了。")
            check("M1 保存成功", ok, msg)
            after = chap.read_text(encoding="utf-8")
            check("M1 目标段变了", "第二段我改了。" in after, after[:120])
            check("M1 第一段没动", "第一段原文。" in after)
            check("M1 第三段没动", "第三段原文。" in after)
            check("M1 标题没动", "## 第3章" in after)
            check("M1 旧内容不在了", "第二段原文，要改这一句。" not in after)

            # ---- M2 整章备份 ----
            baks = list(Path("data/chapters.history").glob("ch03_v*.md"))
            check("M2 有整章备份", len(baks) >= 1, [b.name for b in baks])
            check("M2 备份是改之前的", baks and baks[0].read_text(encoding="utf-8") == before)

            # ---- M3 段落历史 ----
            hists = list(Path("data/chapters/para_history").glob("ch03_p2_h*.json"))
            check("M3 旧内容进了段落历史", len(hists) >= 1, [h.name for h in hists])
            if hists:
                import json
                d = json.loads(hists[0].read_text(encoding="utf-8"))
                check("M3 历史里有原文", "第二段原文" in (d.get("original") or ""), d)
                check("M3 历史里有新文", "第二段我改了" in (d.get("rewritten") or ""), d)

            # ---- M4 空 ----
            ok4, m4 = rp.save_paragraph_text(3, 0, "   ")
            check("M4 空内容拒写", ok4 is False, m4)

            # ---- M5 含空行 ----
            ok5, m5 = rp.save_paragraph_text(3, 0, "上一句\n\n下一句")
            check("M5 含空行拒写", ok5 is False, m5)

            # ---- M6 越界 ----
            ok6, m6 = rp.save_paragraph_text(3, 99, "随便")
            check("M6 越界拒写", ok6 is False, m6)

            # ---- M7 没变 ----
            snap = chap.read_text(encoding="utf-8")
            n_bak = len(list(Path("data/chapters.history").glob("ch03_v*.md")))
            ok7, m7 = rp.save_paragraph_text(3, 2, "第二段我改了。")
            check("M7 没变也返回成功", ok7 is True, m7)
            check("M7 但没多写备份", len(list(Path("data/chapters.history")
                                              .glob("ch03_v*.md"))) == n_bak)
            check("M7 文件没动", chap.read_text(encoding="utf-8") == snap)

            # ---- M9 章不存在 ----
            ok9, m9 = rp.save_paragraph_text(77, 0, "x")
            check("M9 章不存在拒写", ok9 is False, m9)
        finally:
            os.chdir(old_cwd)

    # ---- M8 接线 ----
    refine = (ROOT / "scripts" / "nf_api_domains" / "refine.py").read_text(encoding="utf-8")
    check("M8 有保存端点", "def handle_paragraph_save" in refine)
    check("M8 端点注册了", '"/chapters/paragraph/save"' in refine)
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("M8 分发链接上了", "dom_refine.handle_paragraph_save" in api)
    guard = (ROOT / "scripts" / "utils" / "agent_guard.py").read_text(encoding="utf-8")
    check("M8 外部 Agent 不得替作者改稿", '"/chapters/paragraph/save"' in guard)
    panel = (ROOT / "console" / "src" / "ParagraphRefinePanel.vue").read_text(encoding="utf-8")
    check("M8 面板里有\u300c自己改\u300d", "自己改" in panel and "/chapters/paragraph/save" in panel)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
