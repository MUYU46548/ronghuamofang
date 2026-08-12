# -*- coding: utf-8 -*-
"""阶段 7：Markdown → Word（P0 降级策略，v2 4.6）。

- 合并 chapters/refined/*.md → merged/book.md（缺 refined 时回退 checked/raw）；
- 行解析转换：标题（#/##/###）→ Heading，列表 → List，其余 → 段落；
- 复杂元素（表格/图片/代码块）降级为普通段落文本；
- 中文字体设置（宋体正文 + 黑体标题）。
"""
import argparse
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
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


def _set_font(run, name, size, bold=False, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    if color:
        run.font.color.rgb = RGBColor(*color)


def md_to_docx(md_text, out_path, book_title="未命名"):
    """行解析转换，复杂元素降级为段落。"""
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    style.font.size = Pt(12)

    doc.add_heading(book_title, level=0)

    in_code = False
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
            doc.add_paragraph(line[3:].strip() or "（代码块已降级为文本）")
            continue
        if in_code:
            doc.add_paragraph(line)
            continue
        # 表格行降级
        if TABLE_ROW_RE.match(line):
            doc.add_paragraph("[表格已降级为文本] " + line.strip())
            continue
        # 图片降级
        if IMAGE_RE.search(line):
            alt = IMAGE_RE.search(line).group(1)
            doc.add_paragraph(f"[图片已降级为文本] {alt}")
            continue
        # 标题
        hm = HEADING_RE.match(line)
        if hm:
            _flush_list(doc, list_items); list_items = []
            level = len(hm.group(1))
            p = doc.add_heading(level=min(level, 3))
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
            _flush_list(doc, list_items); list_items = []
            continue
        # 普通段落
        _flush_list(doc, list_items); list_items = []
        p = doc.add_paragraph(line.strip())
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.line_spacing = 1.5
    _flush_list(doc, list_items)

    doc.save(str(out_path))
    print(f"[stage7] Word 输出: {out_path}")


def _flush_list(doc, items):
    if not items:
        return
    for text, bullet in items:
        p = doc.add_paragraph(style="List Bullet" if bullet else "List Number")
        p.add_run(text)
    items.clear()


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    book_name = proj.get("book", {}).get("name", "未命名")
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    book_md = Path("data/merged/book.md")
    book_docx = out_dir / f"{book_name}_完整版.docx"

    n = merge_book(Path("data/chapters/refined"), Path("data/chapters/checked"),
                   Path("data/chapters/raw"), book_md, book_name)
    md_to_docx(read_text(book_md), book_docx, book_title=book_name)

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
    proj = yaml.safe_load(read_text("config/project.yaml"))
    from utils.progress_manager import ProgressManager
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(None, proj, progress, None, None)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
