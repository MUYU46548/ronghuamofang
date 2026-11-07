# -*- coding: utf-8 -*-
"""出厂预设模板的护栏（2026-11-07）。

## 这个文件锁什么

作者的模板（`小说格式模板V1.3.dotx`）与仓库里那份 sha256 完全相同，而它被三处“出厂化”了：
打进安装包、每次启动复制进工作区、当默认落点。作者要求：**他的真实模板不能混入打包**，
出厂只能自带一份中性预设。

所以四条判据：

1. 出厂预设存在、能被导出链接受（四个占位符齐 + A4）；
2. `DEFAULT_WORD_TEMPLATE` 指向的都是**出厂预设**，不是作者那份；
3. 打包配置里**显式排除**作者模板（`filter` 里有 `!小说格式模板V1.3.dotx`）——
   靠“记得别放进去”不叫纪律；
4. 用预设真跑一次导出，**目录域必须真的插进去**（缺 `[请输入文本]` 正文锚点时，
   stage7 会拿最后一段当锚点，而那段正好是刚插入的目录域 → TOC 被清空；
   2026-11-07 实测踩到，这条用例就是防它回潮）。

用法：python tests/unit/test_factory_template.py
"""
import io
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


PRESET = ROOT / "templates" / "出厂预设模板.docx"
USER_TEMPLATE_NAME = "小说格式模板V1.3.dotx"


def case_preset_valid():
    print("\n【1】出厂预设存在且合规（占位符齐 / A4 / 不含作者模板名）")
    import factory_template as ft

    check("预设文件在 templates/出厂预设模板.docx", PRESET.exists(), str(PRESET))
    errs = ft.check(PRESET) if PRESET.exists() else ["不存在"]
    check("自检零问题", not errs, errs)
    if PRESET.exists():
        from docx import Document
        text = "\n".join(p.text for p in Document(str(PRESET)).paragraphs)
        check("含正文锚点 [请输入文本]（缺它目录域会被清空）", "[请输入文本]" in text, text[:80])
        check("不含作者私人模板的名字", "小说格式模板" not in text)


def case_default_points_to_preset():
    print("\n【2】默认落点必须是出厂预设，不能是作者那份")
    from nf_api_domains import project as dom_proj

    check("DEFAULT_WORD_TEMPLATE 指向出厂预设",
          dom_proj.DEFAULT_WORD_TEMPLATE == "templates/出厂预设模板.docx",
          dom_proj.DEFAULT_WORD_TEMPLATE)
    check("默认落点的文件真实存在",
          (ROOT / dom_proj.DEFAULT_WORD_TEMPLATE).exists(),
          dom_proj.DEFAULT_WORD_TEMPLATE)
    check("默认落点不是作者模板", USER_TEMPLATE_NAME not in dom_proj.DEFAULT_WORD_TEMPLATE)


def case_packaging_excludes_user_template():
    print("\n【3】打包配置显式排除作者模板（靠“记得别放”不叫纪律）")
    pkg = json.loads((ROOT / "console" / "package.json").read_text(encoding="utf-8"))
    entries = []
    build = pkg.get("build") or {}
    for key in ("extraResources", "extraFiles", "files"):
        v = build.get(key)
        if isinstance(v, list):
            entries += [e for e in v if isinstance(e, dict)]
    tpl = [e for e in entries if str(e.get("to", "")).endswith("payload/templates")]
    check("找到 templates 的打包条目", bool(tpl), entries[:3])
    if tpl:
        e = tpl[0]
        filt = [str(x) for x in (e.get("filter") or [])]
        check("filter 里排除作者模板",
              any(x.startswith("!") and USER_TEMPLATE_NAME in x for x in filt), filt)
        check("filter 不是空清单（全排掉等于不带模板）", any(not x.startswith("!") for x in filt), filt)


def case_export_inserts_toc():
    print("\n【4】用预设真跑一次导出：目录域必须插进去（防“锚点缺失→TOC 被清空”回潮）")
    import stage7_convert as s7

    d = Path(tempfile.mkdtemp(prefix="nf_ftpl_"))
    md = d / "book.md"
    md.write_text("# 测试书\n\n## 第一章 起锚\n\n正文段落。\n", encoding="utf-8")
    out = d / "out.docx"
    s7.md_to_docx(str(md), out, book_title="测试书", author="某人",
                  template_path=str(PRESET))
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    check("成品生成", out.exists() and out.stat().st_size > 0)
    check("有 TOC 域指令", "TOC" in xml)
    check("有 fldChar（真域，不是纯文字）", "fldChar" in xml)
    check("占位符没残留", "[目录占位符]" not in xml and "[书籍标题]" not in xml)
    check("书名/作者已填", "测试书" in xml and "某人" in xml)


def main():
    case_preset_valid()
    case_default_points_to_preset()
    case_packaging_excludes_user_template()
    case_export_inserts_toc()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
