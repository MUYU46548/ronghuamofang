# -*- coding: utf-8 -*-
"""R07“成品与源稿一致”的回归锁（C6 / N2，2026-11-07）。

## 这个文件锁什么

改造前 R07 把**内部注释**（`<!-- summary: … -->` / `<!-- quality: … -->`）也算进源稿字数，
而成品 docx 里本来就没有这些注释 —— 于是每一本正常导出的书都被判“已过期”：

    实测（测试用书1104，12 章）：每章差值 150~250 汉字，12 章全红

一个永远红的提示等于没有提示，作者很快就学会忽略它。判据因此定成：
**两边都剥掉内部注释再比字数**，且容差 ≥30 字（格式差异）。

## 判据（正反两面）

1. 正：源稿带内部注释、成品不带 → 判**一致**，且两边字数相等；
2. 反：成品真少了一段（内容丢失）→ 必须判**已过期**并给出字数凭据。
   只做正面测试的话，"永远返回一致"这个假实现也能过。

用法：python tests/unit/test_artifact_fidelity.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


from utils.judge import artifact as A                            # noqa: E402

PARAS = [
    "他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。",
    "她没有回头，只把伞靠在门边，手指在伞柄上蹭了一下。",
    "窗外的雨声忽然大了，两个人都不说话，屋里只剩水壶在响。",
]


def _mk_workspace(doc_chapters=1, drop_last=False):
    """造一个最小工作区：源稿带内部注释，成品不带（模拟真实导出）。"""
    import docx
    tmp = Path(tempfile.mkdtemp(prefix="nf_fid_"))
    src_dir = tmp / "data" / "chapters" / "refined"
    src_dir.mkdir(parents=True)
    body = "## 第1章 起风\n\n" + "\n\n".join(PARAS)
    body += "\n\n<!-- summary: 他进门，她没回头；雨大，两人沉默。 -->"
    body += "\n<!-- quality: 8/10 -->"
    (src_dir / "01.md").write_text(body, encoding="utf-8")

    (tmp / "output").mkdir()
    doc = docx.Document()
    doc.add_paragraph("测试书")                       # 封面书名（成品附加页）
    doc.add_paragraph("第1章 起风")
    for p in PARAS:
        doc.add_paragraph(p)
    if drop_last:
        doc.paragraphs[-1]._element.getparent().remove(doc.paragraphs[-1]._element)
    doc.save(str(tmp / "output" / "测试书_完整版.docx"))
    return tmp


def case_faithful_not_stale():
    print("\n【1】正：内部注释不进成品，仍判一致（不再假红）")
    tmp = _mk_workspace()
    st = A.status(tmp)
    check("存在成品", st.get("exists") is True, st.get("status"))
    check("不判过期", st.get("stale") is False, st.get("note"))
    row = (st.get("chapters") or [{}])[0]
    check("两边字数相等（注释已剥掉）",
          row.get("docx_words") == row.get("src_words"),
          (row.get("docx_words"), row.get("src_words")))
    check("理由为空（没有凭空报错）", not row.get("reasons"), row.get("reasons"))


def case_真丢内容必须报红():
    print("\n【2】反：成品真少一段 → 必须判过期并给字数凭据")
    tmp = _mk_workspace(drop_last=True)
    st = A.status(tmp)
    check("判过期", st.get("stale") is True, st.get("note"))
    ev = " ".join(st.get("evidence") or [])
    check("凭据里有字数对比", "字数对不上" in ev, ev[:120])


def case_strip_helper():
    print("\n【3】剥注释是幂等的，且不碰正文")
    s = "正文一。<!-- summary: x -->\n正文二。\n<!-- quality: 8/10 -->"
    once = A.strip_internal_comments(s)
    twice = A.strip_internal_comments(once)
    check("剥后不含注释标记", "<!--" not in once)
    check("幂等（再剥一次不变）", once == twice)
    check("正文一个字没少", "正文一。" in once and "正文二。" in once, once)


def main():
    case_faithful_not_stale()
    case_真丢内容必须报红()
    case_strip_helper()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
