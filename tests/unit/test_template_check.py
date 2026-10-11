# -*- coding: utf-8 -*-
"""件一 Word 链出口闸回归（2026-10-11）：template_check 判据 + stage7 模板继承。

守三件事不漂回去：
| # | 缺陷/契约 | 判据形态 |
|---|---|---|
| 1 | stage7 模板模式硬编码 space_after/line_spacing 直写段落（覆盖模板 Normal 继承） | 真实生成 docx → document.xml 无直写 w:spacing |
| 2 | 样式比对写死 styleId（V1.3 是 a/1/2/3，别家模板不同） | 按 w:name 反查 + 归一化（heading 1 小写带空格） |
| 3 | 占位符残留 / 章节映射错 / 页面设置被改 | template_check.run_checks 全 PASS |

用法：python tests/unit/test_template_check.py
"""
import io
import re
import shutil
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
                           ("  -> " + str(detail)[:200]) if detail else ""))


TEMPLATE = ROOT / "templates" / "小说格式模板V1.3.dotx"

MD = """# 测试书

## 第一章 起锚

正文段落甲。

### 第一节 细节

正文段落乙。

## 第二章 入港

正文段落丙。
"""

BOOK = {"name": "测试书", "author": "某人"}


def _gen(d):
    """用真实 stage7 + 真实 V1.3 模板生成一份成品。"""
    import stage7_convert as s7
    md = d / "book.md"
    md.write_text(MD, encoding="utf-8")
    out = d / "out.docx"
    s7.md_to_docx(MD, out, book_title=BOOK["name"], author=BOOK["author"],
                  template_path=str(TEMPLATE))
    return md, out


def case_template_inheritance():
    print("\n【1】stage7 模板模式：段落格式全继承，无直写")
    d = Path(tempfile.mkdtemp(prefix="nf_tcheck_"))
    md, out = _gen(d)
    xml = zipfile.ZipFile(out).read("word/document.xml").decode("utf-8")
    check("成品生成且非空", out.exists() and out.stat().st_size > 0)
    n_space = len(re.findall(r"<w:spacing\b", xml))
    check("正文段落零直写 w:spacing（不覆盖模板 Normal）", n_space == 0, "出现 %d 处" % n_space)
    # 分页是**功能**不是格式覆盖，必须仍在
    check("章标题仍有 page_break_before", "<w:pageBreakBefore" in xml, "")
    shutil.rmtree(d, ignore_errors=True)


def case_style_lookup_by_name():
    print("\n【2】样式比对按 w:name 反查（styleId 不可写死）")
    import template_check as tc
    z = zipfile.ZipFile(TEMPLATE)
    styles = z.read("word/styles.xml").decode("utf-8")
    check("V1.3 的 Normal styleId 不是 'Normal'（写死必炸）",
          tc._style_by_name(styles, "Normal") is not None)
    check("heading 1 小写带空格也能命中", tc._style_by_name(styles, "heading1") is not None)
    check("归一化忽略大小写与空格", tc._norm_name("Heading 1") == tc._norm_name("heading 1"))
    check("不存在的样式返回 None（不误命中）", tc._style_by_name(styles, "不存在样式") is None)


def case_full_check_passes():
    print("\n【3】template_check 对合规成品全 PASS")
    import template_check as tc
    d = Path(tempfile.mkdtemp(prefix="nf_tcheck_"))
    md, out = _gen(d)
    tc.PASS.clear(); tc.FAIL.clear()
    tc.run_checks(out, TEMPLATE, md_path=md,
                  expect_title=BOOK["name"], expect_author=BOOK["author"])
    check("15 项全 PASS", not tc.FAIL, "FAIL=%s" % tc.FAIL)
    shutil.rmtree(d, ignore_errors=True)


def case_negative_placeholder():
    print("\n【4】反向：占位符未替换必须被抓")
    import template_check as tc
    d = Path(tempfile.mkdtemp(prefix="nf_tcheck_"))
    # 直接拿模板当成品（占位符原样在）
    tc.PASS.clear(); tc.FAIL.clear()
    tc.run_checks(TEMPLATE, TEMPLATE, md_path=None,
                  expect_title=BOOK["name"], expect_author=BOOK["author"])
    check("占位符残留被抓", any("占位符" in f for f in tc.FAIL), tc.FAIL)
    check("书名未填被抓", any("书名" in f for f in tc.FAIL), tc.FAIL)
    shutil.rmtree(d, ignore_errors=True)


def case_missing_docx():
    print("\n【5】成品缺失 = FAIL（不静默跳过）")
    import template_check as tc
    tc.PASS.clear(); tc.FAIL.clear()
    tc.run_checks(str(ROOT / "output" / "绝对不存在的书_完整版.docx"), TEMPLATE)
    check("缺失成品报 FAIL", bool(tc.FAIL), tc.FAIL)


def main():
    print("=" * 62)
    print("  件一 Word 链出口闸回归（template_check + stage7 继承）")
    print("=" * 62)
    if not TEMPLATE.exists():
        print("FATAL: 测试基线模板缺失 %s" % TEMPLATE)
        return 1
    case_template_inheritance()
    case_style_lookup_by_name()
    case_full_check_passes()
    case_negative_placeholder()
    case_missing_docx()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
