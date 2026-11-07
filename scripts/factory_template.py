# -*- coding: utf-8 -*-
"""生成“出厂预设模板”（2026-11-07，作者要求）。

## 为什么要造一份新的

作者的原话：**“重新造一个新的预设模板，防止我的真实模板混入打包带来麻烦。”**

事实（本轮核实）：

- 仓库里的 `templates/小说格式模板V1.3.dotx` 与作者 `Documents/自定义 Office 模板/小说格式模板V1.3.dotx`
  **sha256 完全相同** —— 那就是他的私人模板；
- 它被三处“出厂化”了：`payload/templates`（打进安装包）、`SEED_CODE_DIRS`（每次启动复制进工作区）、
  `DEFAULT_WORD_TEMPLATE`（默认落点）；仓库还是 **public**。

所以出厂应该自带一份**中性预设**：样式/页面与流水线兼容，但**不含**作者的任何私人版式偏好，
更不含他的原件。作者自己的模板继续放 `Documents`，在界面上选进 `book.word_template`。

## 产出

`templates/出厂预设模板.docx`（幂等：重复跑结果一致，不依赖 Word）。

包含（stage7 导出链要求的三个占位符 + A4 版式）：

- 段落 `[书籍标题]` / `[作者]` / `[目录占位符]` / `[请输入文本]`
  （导出时前三个替换成真书名、作者与 TOC 域，最后一个是**正文锚点**）
- A4（21 × 29.7 cm）、四边 2.5 cm、页眉 1.5 cm、页脚 1.75 cm（≈ 1418 / 851 / 992 twips，与老模板同一档）
- 样式 Normal（宋体 12pt，1.5 倍行距）/ Heading 1–3（章、节、小节）

用法：
    python scripts/factory_template.py            # 生成（已存在也重写，保证可复现）
    python scripts/factory_template.py --check    # 只校验现有文件是否合规
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "templates" / "出厂预设模板.docx"

PLACEHOLDERS = ("[书籍标题]", "[作者]", "[目录占位符]", "[请输入文本]")


def _set_east_asian_font(style, name="宋体", size_pt=None, bold=None):
    from docx.oxml.ns import qn
    from docx.shared import Pt
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    if size_pt:
        style.font.size = Pt(size_pt)
    if bold is not None:
        style.font.bold = bold


def build(out: Path = OUT) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    doc = Document()

    # 页面：A4 + 四边 2.5cm（与老模板同一档，导出侧的页面校验照旧能过）
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin = sec.bottom_margin = sec.left_margin = sec.right_margin = Cm(2.5)
    sec.header_distance, sec.footer_distance = Cm(1.5), Cm(1.75)

    _set_east_asian_font(doc.styles["Normal"], "宋体", 12)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.5
    doc.styles["Normal"].paragraph_format.space_after = Pt(6)
    for name, size in (("Heading 1", 18), ("Heading 2", 15), ("Heading 3", 13)):
        st = doc.styles[name]
        _set_east_asian_font(st, "黑体", size, bold=True)
        st.paragraph_format.space_before = Pt(12)
        st.paragraph_format.space_after = Pt(8)

    # 三个占位符（顺序固定：书名 → 作者 → 目录；导出侧按文本整段匹配）
    p = doc.add_paragraph("[书籍标题]", style="Title")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph("[作者]")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("")
    doc.add_paragraph("[目录占位符]")
    # 正文锚点：缺了它，stage7 会拿“最后一个段落”当锚点 —— 而那个段落正好是刚插入的
    # 目录域，于是 TOC 被清空（2026-11-07 实测踩到）。导出前校验也会直接拦下缺锚点的模板。
    doc.add_paragraph("[请输入文本]")

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def check(path: Path = OUT) -> list[str]:
    """校验预设模板：占位符齐、能打开、页面是 A4。返回问题清单（空 = 合规）。"""
    from docx import Document
    errs: list[str] = []
    if not path.exists():
        return ["模板不存在：%s" % path]
    try:
        doc = Document(str(path))
    except Exception as e:                                     # noqa: BLE001
        return ["打不开（%s: %s）" % (type(e).__name__, str(e)[:100])]
    text = "\n".join(p.text for p in doc.paragraphs)
    for ph in PLACEHOLDERS:
        if ph not in text:
            errs.append("缺占位符 %s" % ph)
    sec = doc.sections[0]
    if abs(sec.page_width.cm - 21.0) > 0.2 or abs(sec.page_height.cm - 29.7) > 0.2:
        errs.append("页面不是 A4（%.1f × %.1f cm）" % (sec.page_width.cm, sec.page_height.cm))
    if abs(sec.left_margin.cm - 2.5) > 0.15:
        errs.append("左右边距不是 2.5cm（%.2f）" % sec.left_margin.cm)
    # 预设里绝不能出现作者私人模板的名字（防止又混回去）
    if "小说格式模板" in text:
        errs.append("模板正文里出现了作者私人模板的名字")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="生成/校验出厂预设模板")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out = Path(args.out)
    if args.check:
        errs = check(out)
        print("[factory_template] %s" % ("合规 ✅" if not errs else "不合规："))
        for e in errs:
            print("  -", e)
        return 0 if not errs else 1
    p = build(out)
    errs = check(p)
    print("[factory_template] 已生成 %s（%d 字节）" % (p, p.stat().st_size))
    if errs:
        print("[factory_template] 自检未过：")
        for e in errs:
            print("  -", e)
        return 1
    print("[factory_template] 自检通过 ✅（三个占位符齐、A4、2.5cm 边距）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
