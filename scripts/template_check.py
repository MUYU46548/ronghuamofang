# -*- coding: utf-8 -*-
"""Word 成品 × 模板规格校验（件一 Word 链出口闸，以 book.word_template 为基线）。

用法：
    python scripts/template_check.py                     # 校验当前书档成品
    python scripts/template_check.py --docx output/x.docx
    python scripts/template_check.py --template templates/别的.dotx
    python scripts/template_check.py --md data/merged/book.md   # 章节映射比对源

检查项（件一验收线，2026-10-10 执行单口径）：
    1. 成品 docx 可打开且非空
    2. 四个占位符零残留（[书籍标题] [作者] [目录占位符] [请输入文本]），书名/作者已填
    3. 章节映射：merged/book.md 的 ## → Heading 1、### → Heading 2、#(书名) → 跳过
    4. 样式继承：styles.xml 关键样式（Normal/Heading 1-3）与模板**逐字一致**，非硬编码
    5. 页面设置：pgSz + pgMar 与模板逐字一致（A4 / 四边 1418 由模板说了算）
    6. 正文段落无直写格式覆盖（w:spacing / w:ind 不多于模板自带数量）
    7. [目录占位符] 已换成 TOC 域
    8. 每个章标题 page_break_before（受 config chapter.page_break，出厂 true）

零 LLM、纯确定性。退出码：0 全过 / 1 有 FAIL。
"""
import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.console import ensure_utf8_stdout  # noqa: E402
from utils.config_io import load_config_yaml  # noqa: E402

PH = ("[书籍标题]", "[作者]", "[目录占位符]", "[请输入文本]")
# styleId 各模板不一（V1.3 是 a/1/2/3），一律按 <w:name> 反查；
# Word 内置标题样式名是**小写带空格**（"heading 1"），比对前统一归一化。
STYLE_NAMES = ("normal", "heading1", "heading2", "heading3")
HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")


def _norm_name(s):
    return re.sub(r"\s+", "", s).lower()

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


def _read(p):
    with zipfile.ZipFile(p) as z:
        return z.read("word/document.xml").decode("utf-8"), \
               z.read("word/styles.xml").decode("utf-8")


def _style_by_name(styles_xml, wname):
    """按 <w:name w:val="..."> 反查 style 片段（styleId 与模板相关，不可写死）。"""
    want = _norm_name(wname)
    for m in re.finditer(r"<w:style\b.*?</w:style>", styles_xml, re.S):
        seg = m.group(0)
        n = re.search(r'<w:name w:val="([^"]+)"', seg)
        if n and _norm_name(n.group(1)) == want:
            return seg
    return None


def _id2name(styles_xml):
    """styleId -> 归一化样式名（产物里 pStyle 只有 styleId）。"""
    out = {}
    for m in re.finditer(r"<w:style\b.*?</w:style>", styles_xml, re.S):
        seg = m.group(0)
        sid = re.search(r'w:styleId="([^"]+)"', seg)
        nm = re.search(r'<w:name w:val="([^"]+)"', seg)
        if sid and nm:
            out[sid.group(1)] = _norm_name(nm.group(1))
    return out


def _heading_paras(doc_xml, styles_xml):
    """产物里所有 heading 样式段落 → [(text, 样式名)]。"""
    id2n = _id2name(styles_xml)
    out = []
    for seg in re.findall(r"<w:p[ >].*?</w:p>", doc_xml, re.S):
        ps = re.search(r'<w:pStyle w:val="([^"]+)"', seg)
        if not ps:
            continue
        name = id2n.get(ps.group(1), ps.group(1))
        if not name.startswith("heading"):
            continue
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", seg))
        out.append((text, name))
    return out


def _pg_sect(doc_xml):
    m = re.search(r"<w:pgSz[^/]*/>\s*<w:pgMar[^/]*/>", doc_xml)
    return m.group(0) if m else ""


def _count(el, doc_xml):
    return len(re.findall(r"<w:%s\b[^>]*?/?>" % el, doc_xml))


def expected_heading_map(md_text):
    """merged/book.md → [(text, 期望样式)]：# 书名跳过（模板有书名页），## -> Heading 1。"""
    want = []
    first_h1 = True
    for line in md_text.splitlines():
        m = HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        if level == 1:
            if first_h1:
                first_h1 = False
                continue        # 书名行，模板已有书名页
            level_out = 1
        else:
            level_out = level - 1
        want.append((m.group(2).strip(), "Heading %d" % min(level_out, 3)))
    return want


def run_checks(docx, template, md_path=None, expect_title=None, expect_author=None):
    print("[template_check] 成品: %s" % docx)
    print("[template_check] 基线模板: %s" % template)

    if not Path(docx).exists() or Path(docx).stat().st_size == 0:
        check("成品 docx 可打开且非空", False, "文件不存在或为空")
        return
    try:
        doc_text, styles_out = _read(docx)
        tmpl_text, styles_tmpl = _read(template)
    except Exception as e:                       # noqa: BLE001
        check("成品 docx 可打开且非空", False, "%s: %s" % (type(e).__name__, e))
        return
    check("成品 docx 可打开且非空", True, "%d bytes" % Path(docx).stat().st_size)

    # --- 2 占位符 ---
    flat = re.sub(r"<[^>]+>", "", doc_text)
    residue = [p for p in PH if p in flat or p in doc_text]
    check("四个占位符零残留", not residue, "残留=%s" % (residue or "无"))
    if expect_title:
        check("书名已填", expect_title in flat, expect_title)
    if expect_author is not None and expect_author != "":
        check("作者已填", expect_author in flat, expect_author)

    # --- 3 章节映射 ---
    if md_path and Path(md_path).exists():
        want = [(t, _norm_name(s)) for t, s in
                expected_heading_map(Path(md_path).read_text(encoding="utf-8"))]
        got = _heading_paras(doc_text, styles_out)
        check("章节数一致（md 标题数 = docx heading 数）",
              len(want) == len(got), "md=%d docx=%d" % (len(want), len(got)))
        mismatch = [(w, g) for w, g in zip(want, got) if w != g]
        check("章节标题与层级逐条对上", not mismatch, mismatch[:3])
    else:
        print("  [SKIP] 章节映射比对（未提供 --md 或文件不存在）")

    # --- 4 样式继承 ---
    for nm in STYLE_NAMES:
        a, b = _style_by_name(styles_tmpl, nm), _style_by_name(styles_out, nm)
        if a is None:
            check("样式 %s 模板内存在" % nm, False, "模板未定义该样式")
            continue
        check("样式 %s 与模板逐字一致（非硬编码）" % nm, a == b,
              "" if a == b else "产物被改写")

    # --- 5 页面设置 ---
    pt, po = _pg_sect(tmpl_text), _pg_sect(doc_text)
    check("页面设置与模板一致", bool(pt) and pt == po, po[:160])

    # --- 6 正文直写格式不多于模板自带 ---
    for el in ("spacing", "ind"):
        n_t, n_o = _count(el, tmpl_text), _count(el, doc_text)
        check("段落直写 w:%s 不多于模板（%d）" % (el, n_t), n_o <= n_t,
              "模板=%d 产物=%d" % (n_t, n_o))

    # --- 7 TOC 域 ---
    check("TOC 域已插入", 'TOC \\o "1-3"' in doc_text, "")

    # --- 8 章前分页（读真实配置，不猜默认值） ---
    cfg = load_config_yaml(str(ROOT / "config" / "system.yaml")) or {}
    if cfg.get("chapter", {}).get("page_break", True):
        id2n = _id2name(styles_out)
        segs = []
        for seg in re.findall(r"<w:p[ >].*?</w:p>", doc_text, re.S):
            ps = re.search(r'<w:pStyle w:val="([^"]+)"', seg)
            # 只查**章**标题（heading 1，md 的 ##）；节标题 heading 2 不分页
            if ps and id2n.get(ps.group(1), ps.group(1)) == "heading1":
                segs.append(seg)
        no_pb = [s for s in segs if "<w:pageBreakBefore" not in s]
        check("每个章标题 page_break_before", not no_pb, "%d 个缺失" % len(no_pb))
    else:
        print("  [SKIP] chapter.page_break=false，不查分页")


def main():
    ensure_utf8_stdout()
    ap = argparse.ArgumentParser(description="Word 成品 × 模板规格校验（零 LLM）")
    ap.add_argument("--docx", help="成品 docx（默认 output/<书名>_完整版.docx）")
    ap.add_argument("--template", help="模板（默认取 config/project.yaml book.word_template）")
    ap.add_argument("--md", default=str(ROOT / "data" / "merged" / "book.md"),
                    help="章节映射比对源（默认 data/merged/book.md）")
    args = ap.parse_args()

    proj = load_config_yaml(str(ROOT / "config" / "project.yaml")) or {}
    book = proj.get("book", {})
    tmpl = args.template or (book.get("word_template") or "").strip()
    if not tmpl:
        print("FAIL: config/project.yaml 未配置 book.word_template，无基线可校验")
        return 1
    tmpl_path = Path(tmpl)
    if not tmpl_path.is_absolute():
        tmpl_path = ROOT / tmpl_path
    if not tmpl_path.exists():
        print("FAIL: 模板不存在: %s" % tmpl_path)
        return 1
    docx = args.docx or str(ROOT / "output" / ("%s_完整版.docx" % book.get("name", "未命名")))

    run_checks(docx, tmpl_path, md_path=args.md,
               expect_title=book.get("name"), expect_author=book.get("author"))

    print("\n[template_check] PASS=%d FAIL=%d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("[template_check] 未通过项: %s" % "; ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
