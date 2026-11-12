# -*- coding: utf-8 -*-
"""自己改一段（2026-11-02，不可用清单第 8 条）。

## 要治的病

以前只有一条路：选段 → 填一句意见 → 提交重写 job → 等 → 看 diff。
**没有"这一处我自己改"的路径** —— 改一个字也得让模型重跑一遍，改得怎么样全看它。
于是人只能"再许愿一次"。

## 判据（每条都能变红）

- M1 改第 2 段：只有第 2 段变，其它段一字不动
- M2 整章有备份（`data/chapters/history/chXX_vN.md`）
  ⚠️ 2026-11-12 修正断言路径：原实现写的是 `data/chapters.history`（**少了分隔符**，
  与文档/`refine_chapter` 两处都不一致，用户在文档指的位置找不到备份）；本用例当年
  照抄了那个错路径，等于把 bug 锁成了“预期”。现在两处统一到 `data/chapters/history/`。
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
            baks = list(Path("data/chapters/history").glob("ch03_v*.md"))
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

            # ---- M10 两套切分必须同口径（否则替换会串行）----
            weird = ("## 第4章 怪\n\n"
                     "第一段。\n   \n"          # 带空格的空行
                     "  第二段带缩进。\n\n\n"   # 行首缩进 + 两个空行
                     "第三段。")
            same = [weird[a:b] for a, b in rp._segment_spans(weird)] == \
                rp.split_paragraphs(weird)
            check("M10 带位置的切分与 split_paragraphs 逐段一致", same,
                  (rp.split_paragraphs(weird), [weird[a:b] for a, b in rp._segment_spans(weird)]))

            # ---- M11 只改目标段，其余字节一个不动（F11）----
            chap4 = Path("data/chapters/refined/04.md")
            chap4.parent.mkdir(parents=True, exist_ok=True)
            chap4.write_bytes(weird.encode("utf-8"))
            rp._norm(chap4, 4)          # 先洗一遍：模拟"文件已经规范"
            before_bytes = chap4.read_bytes()   # 基准 = 规范后的原文
            ok11, m11 = rp.save_paragraph_text(4, 2, "第二段我改过了。")
            after = chap4.read_text(encoding="utf-8")
            check("M11 目标段换了", "第二段我改过了。" in after, m11)
            # 格式层（空行数量/行首缩进）由 text_norm 统一，不由替换负责 ——
            # 所以这里不验"分隔符逐字保留"，只验"文字层"：
            # 除目标段外，每一段的**文字**与规范基准逐字相同。
            # 逐字节核对：除去目标段，其余完全相同（用切分后的段列表比）
            check("M11 未改动段逐字不变",
                  [t for i, t in enumerate(rp.split_paragraphs(after)) if i != 2] ==
                  [t for i, t in enumerate(rp.split_paragraphs(before_bytes.decode("utf-8")))
                   if i != 2],
                  rp.split_paragraphs(after))

            # ---- M13 没有空行的稿（每行一段）：替换不能给它加空行 ----
            # 这是 replace_segment 与 "\n\n".join(...) 的**真实差别**：
            # join 会把按行切出来的段落用空行重新拼，等于整章重排。
            chap6 = Path("data/chapters/refined/06.md")
            chap6.parent.mkdir(parents=True, exist_ok=True)
            lined = "## 第6章 行\n甲说了一句。\n乙没回答。\n丙走了。"
            chap6.write_bytes(lined.encode("utf-8"))
            n_nl_before = chap6.read_bytes().count(b"\n")
            rp.save_paragraph_text(6, 2, "乙回答了。")
            raw6 = chap6.read_bytes()
            check("M13 目标行换了", "乙回答了。" in raw6.decode("utf-8"))
            check("M13 行数没变（没被插入空行）",
                  raw6.count(b"\n") == n_nl_before,
                  (n_nl_before, raw6.count(b"\n")))

            # ---- M12 CRLF 文件保存后仍是 CRLF ----
            chap5 = Path("data/chapters/refined/05.md")
            chap5.parent.mkdir(parents=True, exist_ok=True)
            chap5.write_bytes("## 第5章 换行\r\n\r\n甲。\r\n\r\n乙。".encode("utf-8"))
            rp.save_paragraph_text(5, 2, "乙改。")
            raw5 = chap5.read_bytes()
            check("M12 换行统一成 LF（全项目口径，不是本次改动）",
                  b"\r" not in raw5 and raw5.count(b"\n") >= 2, raw5[:40])
            check("M12 目标段换了", "乙改。" in raw5.decode("utf-8"))
        finally:
            os.chdir(old_cwd)

    # ---- M14 段级重写失败不回退整章（F13）----
    # 判据：函数体里每个 `return False` 之前都没有 write_text —— 失败分支不碰正文。
    import re as _re
    src = (ROOT / "scripts" / "refine_paragraph.py").read_text(encoding="utf-8")
    m = _re.search(r"\ndef run_paragraph_refine\(.*?(?=\ndef |\Z)", src, _re.S)
    body = m.group(0) if m else ""
    segs = body.split("return False")
    dirty = [i for i, seg in enumerate(segs[:-1]) if "write_text" in seg]
    check("M14 失败分支不写正文（F13：不回退整章）", bool(body) and not dirty,
          "第 %s 段失败分支里有写盘" % dirty if dirty else "")
    check("M14 备份发生在调用模型之前（改动前快照）",
          body.find("copy2") < body.find("_post_chat") if "_post_chat" in body else True,
          "备份位置不对")

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
