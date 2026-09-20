# -*- coding: utf-8 -*-
"""将《心音辑录（星河篇）》角色语音设定 md 转 Word 正式作品。

读取语音设定目录（--src 指定，默认 materials/voice/）-> 解析核心设定/语音记录
-> 按角色分章生成 docx。
- 核心设定：表格呈现
- 语音记录：保留原始触发场景分类（按 md 中的子标题分隔），无子标题则单列
- 独立版式设计（不使用小说通用模板）：全局中文字体 = 微软雅黑，
  显式写入 w:eastAsia，确保中文混排与 PDF 导出可读性。
不依赖 NovelForge 小说流水线，专用于既有语音集导出。

用法：
  python scripts/voice_to_docx.py [--src 目录] [--out 文件.docx] [--single]
  # 路径也可用环境变量 NF_VOICE_SRC / NF_VOICE_OUT 指定
"""
import os
import re
import argparse
from pathlib import Path

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# 路径不再写死本机绝对路径：改用「参数 > 环境变量 > 项目内默认」
DEFAULT_SRC_DIR = "materials/voice"
DEFAULT_OUT = "output/角色语音设定集.docx"
DEFAULT_OUT_SINGLE = "output/角色语音设定集_样例.docx"

# 独立版式字体策略（正式出版感 + 无版权风险）：
#   中文(eastAsia)：霞鹜文楷 LXGW WenKai —— SIL OFL 开源，典雅书香，可商用/嵌入 PDF
#   西文/数字(ascii,hAnsi)：Times New Roman —— 系统预装、无版权风险、衬线，与楷体协调
FONT_CN = "LXGW WenKai"
FONT_EN = "Times New Roman"
BASE_SIZE = Pt(11)
H1_SIZE = Pt(16)
H2_SIZE = Pt(13)
TITLE_SIZE = Pt(28)


def parse_md(text: str):
    """返回 (title, core_rows, voice_groups)。

    core_rows: list[(k, v)]  核心设定区块
    voice_groups: list[(group_title_or_None, list[(key, line)])]
    """
    lines = text.splitlines()
    title = None
    core_rows = []
    voice_groups = []
    cur_group = None
    cur_items = []
    in_core = False
    item_kv = re.compile(r"^\s*-\s*(.+?)\s*[:：]\s*(.*)$")

    def flush_group():
        nonlocal cur_group, cur_items
        if cur_items:
            voice_groups.append((cur_group, cur_items))
        cur_group, cur_items = None, []

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#") and title is None:
            title = re.sub(r"^#+\s*", "", s).strip()
            continue
        if re.match(r"^#+\s*核心设定", s):
            flush_group()
            in_core = True
            continue
        if re.match(r"^#+\s*语音记录", s):
            flush_group()
            in_core = False
            continue
        m = re.match(r"^#+\s*(.+)$", s)
        if m and not s.startswith("# 核心设定") and not s.startswith("# 语音记录"):
            if not in_core:
                flush_group()
                cur_group = m.group(1).strip()
            continue
        km = item_kv.match(s)
        if km:
            k, v = km.group(1).strip(), km.group(2).strip()
            if in_core:
                core_rows.append((k, v))
            else:
                cur_items.append((k, v))
    flush_group()
    return title, core_rows, voice_groups


def _set_rfonts(el, cn=FONT_CN, en=FONT_EN):
    """在 rPr 上写入三套字体名：eastAsia=中文(楷体)，ascii/hAnsi=西文(衬线)。

    关键：eastAsia 决定中文渲染，必须显式写入否则回退系统衬线体导致混排错乱/PDF 失真。
    """
    rpr = el.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), cn)
    rfonts.set(qn("w:ascii"), en)
    rfonts.set(qn("w:hAnsi"), en)


def apply_run_font(run):
    run.font.name = FONT_EN          # 影响西文/数字
    _set_rfonts(run._element)        # 同时固化 eastAsia=中文


def apply_style_font(style, size=None):
    style.font.name = FONT_EN
    _set_rfonts(style.element)
    if size is not None:
        style.font.size = size


def add_core_table(doc, rows):
    if not rows:
        return
    table = doc.add_table(rows=0, cols=2)
    table.style = "Light Grid Accent 1"
    table.autofit = True
    for k, v in rows:
        cells = table.add_row().cells
        cells[0].text = k
        cells[1].text = v
        for p in cells[0].paragraphs:
            for r in p.runs:
                r.bold = True
                apply_run_font(r)
        for p in cells[1].paragraphs:
            for r in p.runs:
                apply_run_font(r)
    doc.add_paragraph("")


def add_voice_groups(doc, groups):
    if not groups:
        return
    for grp_title, items in groups:
        if grp_title:
            doc.add_heading(grp_title, level=2)
        for k, v in items:
            p = doc.add_paragraph()
            rk = p.add_run(f"{k}：")
            rk.bold = True
            p.add_run(v)
            for r in p.runs:
                apply_run_font(r)


def build_docx(files, out_path):
    doc = Document()

    # —— 独立版式：全局字体（含 eastAsia）——
    apply_style_font(doc.styles["Normal"], size=BASE_SIZE)
    # 正文行距更舒展，提升可读性
    doc.styles["Normal"].paragraph_format.line_spacing = 1.3
    doc.styles["Normal"].paragraph_format.space_after = Pt(2)
    if "Title" in doc.styles:
        apply_style_font(doc.styles["Title"], size=TITLE_SIZE)
    if "Heading 1" in doc.styles:
        apply_style_font(doc.styles["Heading 1"], size=H1_SIZE)
    if "Heading 2" in doc.styles:
        apply_style_font(doc.styles["Heading 2"], size=H2_SIZE)

    # 封面
    h = doc.add_heading("心音辑录（星河篇）", level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph("角色语音设定正式集")
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in sub.runs:
        apply_run_font(r)
        r.font.size = Pt(13)
    doc.add_paragraph("")

    for f in files:
        text = f.read_text(encoding="utf-8")
        title, core_rows, groups = parse_md(text)
        if title:
            doc.add_heading(title, level=1)
        if core_rows:
            doc.add_heading("核心设定", level=2)
            add_core_table(doc, core_rows)
        add_voice_groups(doc, groups)
        doc.add_page_break()

    # 兜底：确保所有 run 都带 eastAsia 字体（表格/标题等）
    for p in doc.paragraphs:
        for r in p.runs:
            apply_run_font(r)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for r in p.runs:
                        apply_run_font(r)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


def main():
    parser = argparse.ArgumentParser(description="角色语音设定 md → Word")
    parser.add_argument("--src", default=os.environ.get("NF_VOICE_SRC") or DEFAULT_SRC_DIR,
                        help="语音设定 md 所在目录（默认 materials/voice/）")
    parser.add_argument("--out", default=os.environ.get("NF_VOICE_OUT") or "",
                        help="输出 docx 路径（默认 output/角色语音设定集[_样例].docx）")
    parser.add_argument("--single", action="store_true",
                        help="只导出第一份（样例模式）")
    args = parser.parse_args()

    src_dir = Path(args.src)
    if not src_dir.is_dir():
        print(f"语音设定目录不存在: {src_dir}（用 --src 指定）")
        return 2
    if args.single:
        files = sorted(src_dir.glob("*_语音设定.md"))[:1]
        out = Path(args.out or DEFAULT_OUT_SINGLE)
    else:
        files = sorted(src_dir.glob("*_语音设定.md"))
        out = Path(args.out or DEFAULT_OUT)
    if not files:
        print(f"未在 {src_dir} 找到 *_语音设定.md 文件")
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    p = build_docx(files, out)
    print(f"OK: {p}  (角色数={len(files)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
