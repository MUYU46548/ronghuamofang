# -*- coding: utf-8 -*-
"""作者交来的作品文件：.docx / .pdf 也要能拆（2026-11-04，试跑第 3/4/5 条）。

## 要治的病

作者原话：“试图在参考作品拆解导入，结果发现居然不支持 pdf？可实际中用最多的就是 pdf。
这次先降级到 word 版运行。” —— 随后“拆解为画像”“提取候选片段”双双失败。

根因不是格式白名单，而是**读文件的方式**：`style_profile.decompose()` 与
`/decompose/snippets/candidates`、`benchmark.import_path()` 一律 `read_text()`
—— 对 .docx/.pdf 读出的是压缩包/PDF 二进制，`prose_char_count` 恒 0，
于是报“有效正文不足 1000 字”（一句与格式无关的废话，作者根本看不出是格式问题）。
“降级到 word 版”也一样失败，因为 word 也是二进制。

## 判据（每条都能变红）

- F1 `.docx` → 拆出章节 + 画像落盘（真跑，不是 mock）
- F2 `.pdf` 在缺 PyMuPDF 时给**可行动的错误**（点名 pymupdf + 另存为），不静默
- F3 `.xlsx` 这类不支持的类型 → 明确说“只收哪四种”
- F4 候选片段（`/decompose/snippets/candidates` 走的同一函数）对 .docx 也能出候选
- F5 基准集导入 `benchmark.import_path` 共用同一口径（.docx 能进）
- F6 反证：`.md` 行为不变（不能被这次改动弄坏）

用法：python tests/unit/test_source_formats.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import style_profile as SP                              # noqa: E402
from utils import benchmark as BM                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("" if cond else "  → %s" % (detail,))))


BODY = ("盐埕旧祠在盐场西边，三间瓦房，瓦上长着草。正屋里供的不是神，是一面墙，"
        "墙上钉着几百块木牌，牌上刻着名字，名字底下刻着年份。他伸手摸了一下最近的那块，"
        "木头的边缘已经磨得发亮，像被人反复摸过很多年。\n\n"
        "他把手伸进衣襟，摸到那枚铜片，铜片是凉的。屋外的风把门帘掀了一下，"
        "又落回去。远处有人在数数，一声一声，慢得像在称东西。"
        "“你迟到了。”老者没有抬头，手指在桌沿上慢慢摩挲，指节泛白。\n\n") * 6

TEXT = ("第1章 归舟\n\n" + BODY + "第2章 空屋\n\n" + BODY)


def make_docx(path):
    import docx
    d = docx.Document()
    for line in TEXT.split("\n"):
        if line.strip():
            d.add_paragraph(line.strip())
    d.save(str(path))
    return path


def make_md(path):
    path.write_text(TEXT, encoding="utf-8")
    return path


def main():
    print("=" * 66)
    print("作品文件格式：.docx / .pdf 也要能拆")
    tmp = Path(tempfile.mkdtemp(prefix="nf_srcfmt_"))
    try:
        print("\n【F6】先确认 .md 老行为没被弄坏")
        md = make_md(tmp / "样本.md")
        ok, text, err = SP.read_source_text(md)
        check("F6 .md 读得出正文", ok and len(text) > 1000, err)
        ok2, msg2, prof2 = SP.decompose(str(md), out_path=str(tmp / "p_md.json"))
        check("F6 .md 拆解成功", ok2 and prof2 is not None, msg2)

        print("\n【F1】.docx → 抽出正文 + 拆章 + 画像落盘")
        dx = make_docx(tmp / "样本.docx")
        ok, text, err = SP.read_source_text(dx)
        check("F1 .docx 抽得出正文（不是二进制乱码）", ok and len(text) > 1000,
              (err, len(text)))
        out = tmp / "profile.json"
        ok3, msg3, prof = SP.decompose(str(dx), out_path=str(out))
        check("F1 .docx 拆解成功", ok3 and prof is not None, msg3)
        check("F1 画像真落盘且可回读",
              out.is_file() and json.loads(out.read_text(encoding="utf-8")).get("summary"),
              out)
        check("F1 章节数 ≥ 2（拆章走的是同一个 split_chapters）",
              ok3 and len(prof.get("chapters") or []) >= 2,
              (prof or {}).get("chapters"))

        print("\n【F2】.pdf：缺库时给可行动错误")
        pdf = tmp / "样本.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%fake\n")
        ok, text, err = SP.read_source_text(pdf)
        try:
            import fitz                                            # noqa: F401
            has_fitz = True
        except Exception:                                          # noqa: BLE001
            has_fitz = False
        if has_fitz:
            check("F2 环境装了 PyMuPDF → 走真解析（坏 pdf 报读取失败，不静默）",
                  (not ok) and bool(err), (ok, err))
        else:
            check("F2 缺库时点名 pymupdf 并给替代方案",
                  (not ok) and "pymupdf" in err.lower(), err)
            check("F2 不静默成空文件（错误必须非空）", bool(err.strip()), err)

        print("\n【F2b】真 pdf（有文本层）→ 能抽出中文正文")
        try:
            import pymupdf
            doc = pymupdf.open()
            page = doc.new_page()
            y = 80
            chunk = "盐埕旧祠在盐场西边，三间瓦房，瓦上长着草。正屋里供的不是神，是一面墙。"
            for i in range(6):
                page.insert_text((50, y + i * 24), chunk, fontname="china-s", fontsize=11)
            real_pdf = tmp / "真样本.pdf"
            doc.save(str(real_pdf))
            doc.close()
            ok_p, text_p, err_p = SP.read_source_text(real_pdf)
            check("F2b 真 pdf 抽出中文正文", ok_p and len(text_p) > 100,
                  (err_p, len(text_p)))
        except Exception as e:                                     # noqa: BLE001
            print("      （跳过：本机 PyMuPDF 造不出中文 pdf — %s: %s）"
                  % (type(e).__name__, e))

        print("\n【F3】不支持的类型 → 说清只收哪四种")
        xl = tmp / "样本.xlsx"
        xl.write_bytes(b"PK\x03\x04")
        ok, text, err = SP.read_source_text(xl)
        check("F3 报不支持", not ok)
        check("F3 错误里列出四种可收格式",
              all(e in err for e in (".txt", ".md", ".docx", ".pdf")), err)

        print("\n【F4】候选片段对 .docx 也能出（同一口径）")
        ok4, text4, _e = SP.read_source_text(dx)
        picks = SP.extract_snippets(text4, per_kind=2) if ok4 else []
        check("F4 候选非空", bool(picks), len(picks))

        print("\n【F5】基准集导入共用同一口径")
        ws = Path(tempfile.mkdtemp(prefix="nf_bm_"))
        try:
            ok5, msg5, ent = BM.import_path(str(dx), "正例一", True, root=ws)
            check("F5 .docx 能导进基准集", ok5 and ent is not None, msg5)
            ok6, msg6, _ = BM.import_path(str(xl), "坏格式", True, root=ws)
            check("F5 不支持的类型在导入时也说清（不写进去）", not ok6, msg6)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

        print("\n【F7】接线：三条链路都别再自己 read_text")
        rt = (ROOT / "scripts" / "nf_api_domains" / "runtime.py").read_text(encoding="utf-8")
        check("F7 candidates 用 read_source_text", "read_source_text(path)" in rt)
        bm = (ROOT / "scripts" / "utils" / "benchmark.py").read_text(encoding="utf-8")
        check("F7 benchmark 用 read_source_text", "read_source_text(p)" in bm)
        sp = (ROOT / "scripts" / "utils" / "style_profile.py").read_text(encoding="utf-8")
        check("F7 decompose 用 read_source_text（不再裸 read_text）",
              "read_source_text(p)" in sp and "text = read_text(p)\n" not in sp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
