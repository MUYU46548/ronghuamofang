# -*- coding: utf-8 -*-
"""2026-10-17 复盘修复的回归锁：润色报告合并 + 导出面板真实分流。

三组断言，各自锁一个已修的用户可见缺陷：

1. `_merge_vol_reports`（stage6_polish）——分卷润色报告合并成 polish_report.md：
   · 排序按**卷号数值**（vol10 排在 vol2 之后，不是文件名字典序）；
   · 幂等：重复跑产出一致（它是分片的派生物，覆盖写而非追加）；
   · 无分卷文件时返回 None 且**不动**已有的 polish_report.md（非分卷模式直接写它）。
   背景：GUI 审批门产物概况与「润色体检报告」按钮只认 polish_report.md，
   分卷模式下没人合并 → 产物概况空白、按钮报「文件不存在」。

2. `export_markdown`（stage8）——书名兜底 + frontmatter 勾选框真实现：
   · book_name=None/空串 → 回退 project.yaml 的 book.name（修「None_markdown」目录；
     根因是 GUI 曾显式传 book_name: null 压掉默认参数）；
   · include_frontmatter=True 时全书.md 以 `---`/`title:` 开头，False 时不带。

3. `handle_export_word`（nf_api_domains/post_misc）——导出面板「仅 Word/双格式」分支：
   · ROUTES 已注册；无章节可合并 → (400, {ok: False})（业务失败不是 500）；
   · 有章节 → 200 且真实产出非空 docx。

用法：python tests/unit/test_export_reports.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

# ⚠️ 必须在 import nf_api 之前钉死 NF_ROOT：模块加载期就 _set_root(_resolve_root())
TMP = Path(tempfile.mkdtemp(prefix="export_reports_test_"))
os.environ["NF_ROOT"] = str(TMP)

import nf_api as api                     # noqa: E402
import utils.project_config as pc        # noqa: E402
import stage6_polish as s6               # noqa: E402
import stage8_markdown_export as s8      # noqa: E402
from nf_api_domains import post_misc     # noqa: E402

PASS, FAIL = [], []
ORIG_CWD = os.getcwd()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:240]) if detail else ""))


def _strip_ts(text):
    """剥掉合并文件头里的生成时间戳行（分钟级，两次跑可能跨分钟）。"""
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.startswith("> 本文件由"))


# ------------------------------------------------- 1. _merge_vol_reports
def test_merge_vol_reports():
    print("\n[1] stage6 分卷润色报告合并 _merge_vol_reports")
    outline = TMP / "data" / "outline"
    outline.mkdir(parents=True, exist_ok=True)
    report = outline / "polish_report.md"
    for f in outline.glob("polish_report*"):
        f.unlink()

    # 三个分卷文件，内容各带可定位标记；文件名故意让字典序 ≠ 数值序
    for n, body in ((2, "卷二问题清单"), (10, "卷十问题清单"), (1, "卷一问题清单")):
        (outline / f"polish_report_vol{n}.md").write_text(
            f"# 卷{n}报告\n\n{body}\n", encoding="utf-8")

    os.chdir(TMP)
    try:
        out = s6._merge_vol_reports()
        check("返回合并路径", out is not None
              and Path(out).resolve() == report.resolve(), out)
        check("polish_report.md 已生成", report.exists())
        text = report.read_text(encoding="utf-8")
        p1 = text.find("polish_report_vol1.md")
        p2 = text.find("polish_report_vol2.md")
        p10 = text.find("polish_report_vol10.md")
        check("按卷号数值排序（vol1 < vol2 < vol10）",
              -1 not in (p1, p2, p10) and p1 < p2 < p10, (p1, p2, p10))
        check("各卷正文都并入", all(s in text for s in
                                   ("卷一问题清单", "卷二问题清单", "卷十问题清单")))

        # 幂等：重跑覆盖写，剥时间戳后内容一致
        s6._merge_vol_reports()
        text2 = report.read_text(encoding="utf-8")
        check("幂等：重复跑产出一致（覆盖写非追加）", _strip_ts(text) == _strip_ts(text2))

        # 无分卷文件：不动已有 polish_report.md
        for f in outline.glob("polish_report_vol*.md"):
            f.unlink()
        report.write_text("# 手写报告，别动我\n", encoding="utf-8")
        out3 = s6._merge_vol_reports()
        check("无分卷文件返回 None", out3 is None, out3)
        check("无分卷文件时不覆盖已有 polish_report.md",
              report.read_text(encoding="utf-8") == "# 手写报告，别动我\n")
    finally:
        os.chdir(ORIG_CWD)


# --------------------------------------------- 2. export_markdown 兜底
def _write_fixture_book():
    """临时根里的最小可导出夹具：project.yaml + 两个非空章节。"""
    (TMP / "config").mkdir(parents=True, exist_ok=True)
    (TMP / "config" / "project.yaml").write_text(
        "book:\n  name: 夹具测试书\n  author: 夹具作者\n", encoding="utf-8")
    raw = TMP / "data" / "chapters" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for f in raw.glob("*.md"):
        f.unlink()
    (raw / "01.md").write_text("## 第1章 起点\n\n测试正文一，若干字。\n", encoding="utf-8")
    (raw / "02.md").write_text("## 第2章 转折\n\n测试正文二，若干字。\n", encoding="utf-8")


def test_export_markdown_fallback():
    print("\n[2] stage8 export_markdown 书名兜底 + frontmatter")
    _write_fixture_book()
    out_root = TMP / "output"
    if out_root.exists():
        import shutil
        shutil.rmtree(out_root)
    os.chdir(TMP)
    try:
        ok, msg, path = s8.export_markdown(book_name=None)
        check("book_name=None 导出成功", ok, msg)
        check("目录用 project.yaml 书名（无 None_markdown）",
              (out_root / "夹具测试书_markdown").exists()
              and not (out_root / "None_markdown").exists(), path)

        ok2, msg2, _ = s8.export_markdown(book_name="   ")
        check("空串同样回退书名", ok2 and (out_root / "夹具测试书_markdown").exists(), msg2)

        ok3, msg3, _ = s8.export_markdown(book_name="显式书名",
                                          include_frontmatter=True)
        full = (out_root / "显式书名_markdown" / "全书.md").read_text(encoding="utf-8")
        check("include_frontmatter=True 时全书.md 带 frontmatter",
              ok3 and full.startswith("---\ntitle: 显式书名"), (msg3, full[:40]))

        ok4, msg4, _ = s8.export_markdown(book_name="无元数据",
                                          include_frontmatter=False)
        full4 = (out_root / "无元数据_markdown" / "全书.md").read_text(encoding="utf-8")
        check("include_frontmatter=False 时不带 frontmatter",
              ok4 and not full4.startswith("---"), (msg4, full4[:40]))
    finally:
        os.chdir(ORIG_CWD)


# --------------------------------------------- 3. /export/word 端点
def test_export_word():
    print("\n[3] POST /export/word（域模块 handle_export_word）")
    check("ROUTES 已注册 /export/word",
          any(m == "POST" and p == "/export/word" for m, p, _ in post_misc.ROUTES))

    # 配置族钉到夹具根（handler 内 get_project_config / system.yaml 都按 ROOT 读）
    orig_py = pc.PROJECT_YAML
    pc.PROJECT_YAML = TMP / "config" / "project.yaml"
    api._set_root(TMP)
    (TMP / "config" / "system.yaml").write_text("chapter:\n  page_break: true\n",
                                                encoding="utf-8")
    (TMP / "output").mkdir(parents=True, exist_ok=True)
    os.chdir(TMP)
    try:
        # 3a. 无章节 → merge_book 抛 FileNotFoundError → 400（业务失败不是 500）
        for sub in ("refined", "checked", "raw"):
            d = TMP / "data" / "chapters" / sub
            d.mkdir(parents=True, exist_ok=True)
            for f in d.glob("*.md"):
                f.unlink()
        status, payload = post_misc.handle_export_word(None, {})
        check("无章节返回 400 且 ok=false", status == 400 and payload.get("ok") is False,
              (status, payload))

        # 3b. 有章节 → 200 + 非空 docx（真跑 merge_book + md_to_docx）
        _write_fixture_book()
        status2, payload2 = post_misc.handle_export_word(None, {})
        docx = TMP / "output" / "夹具测试书_完整版.docx"
        check("有章节返回 200", status2 == 200 and payload2.get("ok") is True,
              (status2, payload2))
        check("docx 真实落盘且非空",
              docx.exists() and docx.stat().st_size > 1000,
              docx.stat().st_size if docx.exists() else "missing")
    finally:
        pc.PROJECT_YAML = orig_py
        api._set_root(ROOT)
        os.chdir(ORIG_CWD)


def main():
    test_merge_vol_reports()
    test_export_markdown_fallback()
    test_export_word()

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("  失败项:")
        for f in FAIL:
            print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
