# -*- coding: utf-8 -*-
"""阶段 7：Markdown → Word（模板底版 + 降级策略，v2 4.6 / P0.6 模板接入）。

- 合并 chapters/refined/*.md → merged/book.md（缺 refined 时回退 checked/raw）；
- 支持用户 Word 模板（templates/*.dotx/.docx，config/project.yaml book.word_template）：
  * .dotx 自动归一为可解析 .docx（content type 转换）；
  * 替换模板占位符：[书籍标题]→书名、[作者]→作者、[目录占位符]→TOC 域（Word 打开 F9 更新）；
  * 正文从 [请输入文本] 锚点之后开始写入；
- 无模板时回退 P0 硬编码方案（宋体正文 + 黑体标题）；
- 复杂元素（表格/图片/代码块）降级为普通段落文本；
- 章节标题按 chapter.page_break 决定是否新起一页（默认 true）。
"""
import argparse
import re
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from utils.file_io import read_text, write_text

HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
LIST_ITEM_RE = re.compile(r"^\s*[-*]\s+(.*)$")
NUM_ITEM_RE = re.compile(r"^\s*\d+[.、]\s+(.*)$")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
CODE_FENCE_RE = re.compile(r"^```")
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

# 模板占位符
PH_TITLE = "[书籍标题]"
PH_AUTHOR = "[作者]"
PH_TOC = "[目录占位符]"
PH_BODY = "[请输入文本]"

TEMPLATE_CACHE = Path("data/tmp/template_cache")


def merge_book(refined_dir, checked_dir, raw_dir, out_path, book_title="未命名"):
    """按优先级合并章节为 book.md：refined > checked > raw。"""
    sources = [refined_dir, checked_dir, raw_dir]
    chosen = None
    for d in sources:
        files = sorted(Path(d).glob("*.md"))
        if files:
            chosen = (d, files)
            break
    if not chosen:
        raise FileNotFoundError("无可用章节（refined/checked/raw 均为空）")
    dirname, files = chosen
    parts = [f"# {book_title}", ""]
    for f in files:
        parts.append(read_text(f).strip())
        parts.append("")
    text = COMMENT_RE.sub("", "\n".join(parts))  # 去除质量/摘要注释
    write_text(out_path, text.strip() + "\n")
    print(f"[stage7] 合并 {len(files)} 章（来源: {dirname}）→ {out_path}")
    return len(files)


# ---------- 模板工具 ----------

def dotx_to_docx(src, dst):
    """把 .dotx 归一为 .docx（替换 content type），供 python-docx 打开。"""
    with zipfile.ZipFile(src) as zin:
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "[Content_Types].xml":
                    data = data.replace(
                        b"wordprocessingml.template.main+xml",
                        b"wordprocessingml.document.main+xml")
                zout.writestr(item, data)
    return dst


def prepare_template(template_path):
    """返回可被 python-docx 打开的模板 docx 路径。.dotx 需先转换（缓存到 data/tmp）。"""
    src = Path(template_path)
    if src.suffix.lower() == ".docx":
        return src
    TEMPLATE_CACHE.mkdir(parents=True, exist_ok=True)
    dst = TEMPLATE_CACHE / (src.stem + ".docx")
    dotx_to_docx(src, dst)
    return dst


def _find_paragraph(doc, text):
    for p in doc.paragraphs:
        if p.text.strip() == text:
            return p
    return None


def _set_paragraph_text(p, text):
    """清空段落并写入新文本（保留段落样式）。"""
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    p.add_run(text)
    return p


def _insert_toc_field(p):
    """把段落内容替换为 TOC 域（Word 打开后 F9 更新）。"""
    for r in list(p.runs):
        r._element.getparent().remove(r._element)
    run = p.add_run()
    fld_begin = OxmlElement("w:fldChar"); fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText"); instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    fld_sep = OxmlElement("w:fldChar"); fld_sep.set(qn("w:fldCharType"), "separate")
    t = OxmlElement("w:t"); t.text = "（打开文档后按 F9 更新目录）"
    fld_end = OxmlElement("w:fldChar"); fld_end.set(qn("w:fldCharType"), "end")
    for el in (fld_begin, instr, fld_sep, t, fld_end):
        run._element.append(el)


def _set_font(run, name, size, bold=False, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color:
        run.font.color.rgb = RGBColor(*color)


def _add_paragraph_before(anchor, text, style=None):
    """在 anchor 段前插入段落。style 不存在时回退 Normal。"""
    p = anchor.insert_paragraph_before()
    if style:
        try:
            p.style = style
        except KeyError:
            pass
    p.add_run(text)
    return p


def md_to_docx(md_text, out_path, book_title="未命名", author="",
               template_path=None, page_break=True):
    """行解析转换。template_path 提供时以模板为底版，否则 P0 硬编码样式。"""
    use_template = template_path is not None and Path(template_path).exists()

    if use_template:
        doc = Document(str(prepare_template(template_path)))
    else:
        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "宋体"
        style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
        style.font.size = Pt(12)
        doc.add_heading(book_title, level=0)

    # 模板占位符填充
    anchor = None
    if use_template:
        p = _find_paragraph(doc, PH_TITLE)
        if p:
            _set_paragraph_text(p, book_title)
        p = _find_paragraph(doc, PH_AUTHOR)
        if p:
            _set_paragraph_text(p, author)
        p = _find_paragraph(doc, PH_TOC)
        if p:
            _insert_toc_field(p)
        anchor = _find_paragraph(doc, PH_BODY)
        if anchor is None:
            anchor = doc.paragraphs[-1]
        _set_paragraph_text(anchor, "")  # 锚点清空，正文插入其前

    in_code = False
    list_items = []
    skip_first_h1 = use_template  # 模板已有书名页，跳过正文开头的 # 书名

    def flush_list():
        nonlocal list_items
        if not list_items:
            return
        for text, bullet in list_items:
            if use_template:
                style = "List Bullet" if bullet else "List Number"
                p = _add_paragraph_before(anchor, "", style=style)
                p.runs[0].text = text if p.runs else text
                if not p.runs:
                    p.add_run(text)
            else:
                p = doc.add_paragraph(style="List Bullet" if bullet else "List Number")
                p.add_run(text)
        list_items = []

    for raw_line in md_text.splitlines():
        line = raw_line.rstrip()
        if COMMENT_RE.match(line):
            continue
        # 代码块围栏：整块降级为段落
        if CODE_FENCE_RE.match(line):
            in_code = not in_code
            if not in_code:
                continue
            flush_list()
            if use_template:
                _add_paragraph_before(anchor, line[3:].strip() or "（代码块已降级为文本）")
            else:
                doc.add_paragraph(line[3:].strip() or "（代码块已降级为文本）")
            continue
        if in_code:
            flush_list()
            if use_template:
                _add_paragraph_before(anchor, line)
            else:
                doc.add_paragraph(line)
            continue
        # 表格行降级
        if TABLE_ROW_RE.match(line):
            flush_list()
            text = "[表格已降级为文本] " + line.strip()
            if use_template:
                _add_paragraph_before(anchor, text)
            else:
                doc.add_paragraph(text)
            continue
        # 图片降级
        if IMAGE_RE.search(line):
            flush_list()
            alt = IMAGE_RE.search(line).group(1)
            text = f"[图片已降级为文本] {alt}"
            if use_template:
                _add_paragraph_before(anchor, text)
            else:
                doc.add_paragraph(text)
            continue
        # 标题
        hm = HEADING_RE.match(line)
        if hm:
            flush_list()
            hashes = len(hm.group(1))
            if skip_first_h1 and hashes == 1:
                skip_first_h1 = False
                continue  # 模板已有书名页
            if use_template:
                # 正文中 ## 为章标题（Heading 1），### 为节标题（Heading 2）
                level = hashes - 1
                style_name = f"Heading {max(1, min(level, 3))}"
                p = _add_paragraph_before(anchor, hm.group(2).strip(), style=style_name)
                if hashes == 2 and page_break:
                    p.paragraph_format.page_break_before = True
            else:
                level = min(hashes, 3)
                p = doc.add_heading(level=level)
                run = p.add_run(hm.group(2).strip())
                _set_font(run, "黑体", 16 - level * 2, bold=True, color=(0x1F, 0x3B, 0x57))
            continue
        # 列表
        lm = LIST_ITEM_RE.match(line)
        nm = NUM_ITEM_RE.match(line)
        if lm or nm:
            list_items.append((lm.group(1) if lm else nm.group(1), bool(lm)))
            continue
        # 空行：刷新列表
        if not line.strip():
            flush_list()
            continue
        # 普通段落
        flush_list()
        if use_template:
            p = _add_paragraph_before(anchor, line.strip())
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.5
        else:
            p = doc.add_paragraph(line.strip())
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.5
    flush_list()

    doc.save(str(out_path))
    print(f"[stage7] Word 输出: {out_path}"
          + ("（模板底版）" if use_template else "（无模板，硬编码样式）"))


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    book = proj.get("book", {})
    book_name = book.get("name", "未命名")
    author = book.get("author", "") or ""
    tmpl = (book.get("word_template") or "").strip()
    template_path = Path(tmpl) if tmpl and Path(tmpl).exists() else None
    page_break = bool((cfg or {}).get("chapter", {}).get("page_break", True))

    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    book_md = Path("data/merged/book.md")
    book_docx = out_dir / f"{book_name}_完整版.docx"

    n = merge_book(Path("data/chapters/refined"), Path("data/chapters/checked"),
                   Path("data/chapters/raw"), book_md, book_name)
    md_to_docx(read_text(book_md), book_docx, book_title=book_name, author=author,
               template_path=template_path, page_break=page_break)

    if not book_docx.exists() or book_docx.stat().st_size == 0:
        progress.set_stage(7, "failed", error="docx 未生成或为空")
        return False, "stage7 docx 生成失败"

    progress.mark_stage_done(7)
    print(f"[stage7] 完成：{n} 章 → {book_docx}")
    return True, f"stage7 完成（{n} 章）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段7：Markdown→Word")
    args = parser.parse_args()
    import yaml
    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    from utils.progress_manager import ProgressManager
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(cfg, proj, progress, None, None)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
