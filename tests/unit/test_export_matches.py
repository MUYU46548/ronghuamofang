# -*- coding: utf-8 -*-
"""导出件与源逐章核对（F22，2026-11-03）：结构对 ≠ 正文对。

## 要治的病

`check_docx_output` 只数章标题、查占位符 —— 这两项**结构**过关不代表**正文**过关：
缺一段、串了章、被截断半章，标题数照样对。用户拿到成品才发现"第二章少了一半"，
而系统说"已完成"。

## 判据（每条都能变红）

- X1 两章正文原样进成品 → `ok=True, matched=2`
- X2 第二章被截断 → 只报它，且提示"疑似截断"
- X3 整章缺失 → 报缺，`matched` 相应减少
- X4 换行/空格差异不算差异（排版不是内容）
- X5 源为空（章还没写）→ 跳过，不误报
- X6 接线：stage7 把报告写到 `data/state/export_check.json`

用法：python tests/unit/test_export_matches.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.export_check import check_export_matches_source      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


BODY1 = "叶清和把第六封信摊在柜台上。邮戳是二〇一一年三月，字迹清瘦。她读了三遍，才把信折回去。"
BODY2 = ("陈姨说，一九九七年有个女人在这里寄过七封信。收信人栏空着，只写了沅口邮局。"
         "那天雨很大，女人的鞋是全湿的，走的时候留下一个信封。")


def make_docx(path, paras):
    from docx import Document
    doc = Document()
    for p in paras:
        doc.add_paragraph(p)
    doc.save(path)
    return path


def main():
    print("=" * 66)
    print("导出件与源逐章核对（F22）")

    tmp = Path(tempfile.mkdtemp())
    chapters = {1: BODY1, 2: BODY2}

    print("\n【1】全对")
    d = make_docx(tmp / "ok.docx", ["第一章", BODY1, "第二章", BODY2])
    rep = check_export_matches_source(d, chapters)
    check("X1 两章都算一致", rep["matched"] == 2 and rep["ok"] is True, rep)
    check("X1 计数口径正确", rep["total"] == 2 and rep["source_chars"] > 0, rep)

    print("\n【2】截断")
    half = BODY2[:len(BODY2) // 2]
    d = make_docx(tmp / "half.docx", ["第一章", BODY1, "第二章", half])
    rep = check_export_matches_source(d, chapters)
    bad = {it["n"]: it for it in rep["mismatched"]}
    check("X2 只报第 2 章", list(bad) == [2], rep)
    check("X2 说清是截断", "截断" in bad[2]["why"], bad.get(2))
    check("X2 第 1 章不算错", rep["matched"] == 1, rep)

    print("\n【3】整章缺失")
    d = make_docx(tmp / "missing.docx", ["第一章", BODY1])
    rep = check_export_matches_source(d, chapters)
    check("X3 报缺第 2 章", [it["n"] for it in rep["mismatched"]] == [2], rep)
    check("X3 缺整章不误判成截断",
          "截断" not in rep["mismatched"][0]["why"], rep["mismatched"][0])

    print("\n【4】排版差异不算差异")
    d = make_docx(tmp / "space.docx", ["第一章", BODY1.replace("。", "。  ")])
    rep = check_export_matches_source(d, {1: BODY1})
    check("X4 多空格仍是 0 差异", rep["ok"] is True, rep)

    print("\n【5】空源跳过")
    d = make_docx(tmp / "empty.docx", ["第一章", BODY1])
    rep = check_export_matches_source(d, {1: BODY1, 2: ""})
    check("X5 空章不参与比对、不误报", rep["ok"] is True and rep["total"] == 1, rep)
    rep = check_export_matches_source(tmp / "nope.docx", {1: BODY1})
    check("X5b 文件不存在 → 不抛、判不一致", rep["ok"] is False, rep)

    print("\n【6】接线")
    s7 = (ROOT / "scripts" / "stage7_convert.py").read_text(encoding="utf-8")
    check("X6 stage7 调了逐章核对", "check_export_matches_source(" in s7)
    check("X6 报告落在 data/state/export_check.json",
          "data/state/export_check.json" in s7)
    check("X6 不一致只报告、不删成品（不假装拦下）",
          "只报告，不删成品" in s7)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
