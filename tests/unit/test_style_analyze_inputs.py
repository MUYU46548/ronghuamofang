# -*- coding: utf-8 -*-
"""文风分析：范文入口 + “指定文件”失败原因（试跑第 9 条，2026-11-05）。

## 取证结论（不是猜的）

- 入口：范文粘贴框一直在“文风”页，但**手册里一个字都没提**，页面文案还把它指到
  “设置页写风格笔记”（那是另一件事）→ 作者找不到。
- “指定文件”失败：实测选 **.docx** 时 `nf_read_text()` 抛 `UnicodeDecodeError`，
  接口 500，界面只剩一句英文异常；PDF/旧 .doc 同理。

## 判据

- S1 .txt / .md（UTF-8 / GBK / UTF-16）都能分析 —— 编码不是问题，别乱改
- S2 .docx 能直接分析（走 import_docx.extract_text）
- S3 不支持的扩展名（.pdf / .doc）返回**可读中文原因**，不是堆栈
- S4 界面：文风页有“范文在哪配”指引 + “去配范文”按钮；选择器只列可分析类型
- S5 手册写了三步到达与支持的类型

用法：python tests/unit/test_style_analyze_inputs.py
"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []
TEXT = "第一章 风起。他走进屋子，看见桌上摆着一封信，信封上没有名字。" * 12


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    print("=" * 70)
    print("文风分析：入口 + 指定文件")
    print("=" * 70)

    import nf_api

    tmp = Path(tempfile.mkdtemp(prefix="nf_style_"))
    try:
        print("\n[S1] 文本文件（三种编码）")
        files = {}
        (tmp / "u8.md").write_text(TEXT, encoding="utf-8")
        (tmp / "gbk.txt").write_bytes(TEXT.encode("gbk"))
        (tmp / "u16.txt").write_text(TEXT, encoding="utf-16")
        for name in ("u8.md", "gbk.txt", "u16.txt"):
            r = nf_api.act_style_analyze({"source": "path", "path": str(tmp / name)})
            check("%s 能分析" % name, bool(r.get("ok")), (r.get("error") or "")[:80])
            files[name] = r

        print("\n[S2] .docx 直接分析（用 python-docx 造真文档，不是拼 zip）")
        docx = tmp / "范文.docx"
        try:
            from docx import Document
            d = Document()
            for i in range(0, len(TEXT), 40):
                d.add_paragraph(TEXT[i:i + 40])
            d.save(str(docx))
            made = True
        except Exception as e:                                 # noqa: BLE001
            made = False
            print("    （环境无 python-docx，跳过真文档用例：%s）" % e)
        if made:
            r2 = nf_api.act_style_analyze({"source": "path", "path": str(docx)})
            check("docx 能直接分析", bool(r2.get("ok")), r2.get("error"))
            check("docx 提取到正文", (r2.get("chars") or 0) > 100, r2.get("chars"))
        # 坏 docx（不是合法 OPC 包）也必须是“人话”，不能是 500/堆栈
        bad = tmp / "坏文件.docx"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("word/document.xml", "<w:document/>")
        r2b = nf_api.act_style_analyze({"source": "path", "path": str(bad)})
        check("坏 docx 不抛异常", isinstance(r2b, dict) and ("ok" in r2b), r2b.get("error"))
        check("坏 docx 也说清出路",
              (not r2b.get("ok")) and ("另存为" in (r2b.get("error") or "")), r2b.get("error"))

        print("\n[S3] 不支持的扩展名给可读原因")
        pdf = tmp / "范文.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%%EOF")
        r3 = nf_api.act_style_analyze({"source": "path", "path": str(pdf)})
        check("pdf 被拒且说明支持什么", (not r3.get("ok")) and "只支持" in (r3.get("error") or ""),
              r3.get("error"))
        old = tmp / "旧稿.doc"
        old.write_bytes(b"\xd0\xcf\x11\xe0")
        r4 = nf_api.act_style_analyze({"source": "path", "path": str(old)})
        check("doc 被拒且说明另存", (not r4.get("ok")) and "另存" in (r4.get("error") or ""),
              r4.get("error"))
        check("拒绝信息不含堆栈", "Traceback" not in str(r4.get("error") or ""))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[S4] 界面入口")
    vue = (ROOT / "console" / "src" / "StylePanel.vue").read_text(encoding="utf-8")
    check("有“范文在哪配”指引", "范文在哪配" in vue)
    check("有“去配范文”跳转", "scrollToReference" in vue and "去配范文" in vue)
    check("范文正文块可定位", 'id="style-reference"' in vue and "refBlockEl" in vue)
    check("选择器只列可分析类型", '"docx"' in vue and "可分析的文本" in vue)
    check("不再把范文指到“设置页风格笔记”", "可在“设置”页写入风格笔记" not in vue)

    print("\n[S5] 手册")
    md = (ROOT / "console" / "src" / "help" / "08-writing-rules.md").read_text(encoding="utf-8")
    check("手册有范文一节", "范文（样例稿）：在哪儿配、怎么用" in md)
    check("手册写三步到达", "三步到达" in md)
    check("手册写支持类型", ".docx" in md and "PDF" in md)

    print("\n" + "=" * 70)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
