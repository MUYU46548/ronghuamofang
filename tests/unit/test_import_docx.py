# -*- coding: utf-8 -*-
"""Word 导入器（活二）回归 —— 26 断言。

守四条红线（问题卡「不可越」）：
1. 输入 docx **一个字节都不动**（落盘前后 sha256 逐字节比）
2. **预检未过不落盘**（preview / 需确认两种形态都必须 0 写入）
3. 切分**复用** `book_split.split_chapters`（中文回目 / Markdown 标题两条模式都验）
4. 未识别章节结构 → 报「未识别」+ 两条出路，**不落半吊子产物**

外加：落位三档、覆盖保护（先打 before-import 快照）、doctor 对账、参数校验。
夹具 docx 用 python-docx 现造（自包含，不依赖外部文件）。
全程离线、零 token。
用法：python tests/unit/test_import_docx.py
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from import_docx import extract_text, import_docx, preview_lines  # noqa: E402

PASS, FAIL = [], []
TMP = None
HAS_DOCX = True
try:
    from docx import Document
except Exception:                                            # noqa: BLE001
    HAS_DOCX = False


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "  → " + str(detail)[:200] if detail else ""))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_docx(path, mode):
    """现造夹具 docx。mode: chinese（普通段落回目标题）/ heading（标题样式）/ plain"""
    doc = Document()
    if mode == "chinese":
        for i in range(1, 4):
            doc.add_paragraph("第%d章 风起" % i)              # 不套样式 → 中文回目模式
            doc.add_paragraph("正文第%d段：夜色压城，码头的灯一盏盏熄了。" % i)
    elif mode == "heading":
        for i in range(1, 3):
            doc.add_paragraph("第%d章 云涌" % i, style="Heading 1")
            doc.add_paragraph("这一章的正文。" * 15)
    elif mode == "plain":
        doc.add_paragraph("一整篇没有任何标题的散文。")
        doc.add_paragraph("第二段。")
        doc.add_paragraph("第三段。")
    doc.save(str(path))
    return path


def setup():
    global TMP
    TMP = Path(tempfile.mkdtemp(prefix="nf_imp_"))
    (TMP / "config").mkdir()
    (TMP / "config" / "project.yaml").write_text(
        "book:\n  name: 示例书名（待填写）\n  chapters: 3\n", encoding="utf-8")
    return TMP


def teardown():
    shutil.rmtree(TMP, ignore_errors=True)


def files_in(tier):
    d = TMP / "data" / "chapters" / tier
    return sorted(p.name for p in d.glob("*.md")) if d.is_dir() else []


# ---------------------------------------------------------------- 1. 中文回目
def test_chinese():
    print("\n[1] 中文回目结构 docx → raw/NN.md")
    setup()
    try:
        src = make_docx(TMP / "回目.docx", "chinese")
        before = sha(src)
        text = extract_text(src)
        check("1.1 取到文字（不搬格式）", "第1章 风起" in text and "夜色压城" in text)
        res = import_docx(src, tier="raw", root=TMP, yes=True, interactive=False)
        check("1.2 导入成功", res.get("ok") is True, res.get("error"))
        check("1.3 切分模式 = 中文回目（复用 book_split）",
              res.get("pattern") == "中文回目", res.get("pattern"))
        check("1.4 章数正确（3 章）", res.get("chapter_count") == 3,
              res.get("chapter_count"))
        check("1.5 落盘 3 个 NN.md", files_in("raw") == ["01.md", "02.md", "03.md"],
              files_in("raw"))
        check("1.6 内容不是空壳", "夜色压城" in
              (TMP / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8"))
        check("1.7 预检清单带每章字数",
              len(res.get("preview") or []) == 3
              and all("字" in x for x in res["preview"]), res.get("preview"))
        check("1.8 doctor 已跑 + 章节项全绿",
              isinstance(res.get("doctor"), dict)
              and (res["doctor"].get("chapter_blocking") == []),
              (res.get("doctor") or {}).get("note"))
        check("1.9 输入文件零写入（红线1，sha 逐字节一致）", sha(src) == before)
        check("1.10 导入器自报 input_unchanged", res.get("input_unchanged") is True)
        check("1.11 落盘前打了 before-import 快照（丙3）",
              bool(res.get("snapshot_id")), res.get("snapshot_id"))
        check("1.12 章节数与 project.yaml 目标一致 → 无黄提示",
              res.get("warnings") == [], res.get("warnings"))
    finally:
        teardown()


# ---------------------------------------------------------------- 2. 标题样式
def test_heading_style():
    print("\n[2] Word 标题样式 → 转 # 标题 → 切分")
    setup()
    try:
        src = make_docx(TMP / "样式.docx", "heading")
        text = extract_text(src)
        check("2.1 标题段被转成 Markdown # 行", "# 第1章 云涌" in text, text[:80])
        res = import_docx(src, tier="raw", root=TMP, yes=True, interactive=False)
        check("2.2 切分模式 = Markdown 标题", res.get("pattern") == "Markdown 标题",
              res.get("pattern"))
        check("2.3 章数正确（2 章）", res.get("chapter_count") == 2,
              res.get("chapter_count"))
    finally:
        teardown()


# ---------------------------------------------------------------- 3. 兜底
def test_fallback():
    print("\n[3] 识别不出章节 → 报未识别 + 两条出路，不落半吊子产物")
    setup()
    try:
        src = make_docx(TMP / "无结构.docx", "plain")
        res = import_docx(src, tier="raw", root=TMP, yes=True, interactive=False)
        check("3.1 判为未识别（ok=False）", res.get("ok") is False, res.get("ok"))
        check("3.2 报错文案含「未识别出章节结构」",
              "未识别出章节结构" in str(res.get("error")), res.get("error"))
        sugg = res.get("suggestions") or []
        check("3.3 出路一 --single", any("--single" in s for s in sugg), sugg)
        check("3.4 出路二 --split-by", any("--split-by" in s for s in sugg), sugg)
        check("3.5 一个文件都没落（无半吊子产物）", files_in("raw") == [],
              files_in("raw"))

        res2 = import_docx(src, tier="raw", root=TMP, yes=True, single=True,
                           interactive=False)
        check("3.6 --single 出路可用（整本当一章）",
              res2.get("ok") is True and res2.get("chapter_count") == 1, res2.get("error"))
        check("3.7 single 落盘 01.md", files_in("raw") == ["01.md"], files_in("raw"))

        res3 = import_docx(src, tier="checked", root=TMP, yes=True,
                           split_by="^第[二三]段", interactive=False)
        check("3.8 --split-by 自定义分隔可用",
              res3.get("ok") is True and res3.get("chapter_count") == 3,
              (res3.get("error"), res3.get("chapter_count")))
        check("3.9 自定义规则不匹配 → 明确报错不静默",
              (import_docx(src, tier="raw", root=TMP, yes=True,
                           split_by="^这个正则匹配不到任何行", interactive=False)
               .get("ok") is False))
    finally:
        teardown()


# ---------------------------------------------------------------- 4. 落位三档
def test_tiers():
    print("\n[4] 落位三档 = 起点三档")
    setup()
    try:
        src = make_docx(TMP / "回目.docx", "chinese")
        res = import_docx(src, tier="checked", root=TMP, yes=True, interactive=False)
        check("4.1 --as checked 落 checked/",
              res.get("ok") is True and files_in("checked") == ["01.md", "02.md", "03.md"],
              files_in("checked"))
        check("4.2 不会顺手写 raw/", files_in("raw") == [], files_in("raw"))
        res = import_docx(src, tier="refined", root=TMP, yes=True, interactive=False)
        check("4.3 --as refined 落 refined/",
              res.get("ok") is True and len(files_in("refined")) == 3, files_in("refined"))
        check("4.4 默认档是 raw（签名缺省值）",
              import_docx.__defaults__[0] == "raw", import_docx.__defaults__[0])
    finally:
        teardown()


# ---------------------------------------------------------------- 5. 覆盖保护
def test_overwrite_guard():
    print("\n[5] 覆盖保护（破坏性 → 先快照 + 显式确认）")
    setup()
    try:
        src = make_docx(TMP / "回目.docx", "chinese")
        src_sha = sha(src)
        import_docx(src, tier="raw", root=TMP, yes=True, interactive=False)
        old = (TMP / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
        # 改动目标层内容，再导一次（同名）
        (TMP / "data" / "chapters" / "raw" / "01.md").write_text(
            "旧稿标记-MARKER", encoding="utf-8")
        res = import_docx(src, tier="raw", root=TMP, yes=True, interactive=False)
        check("5.1 同名冲突不静默覆盖（ok=False）", res.get("ok") is False, res.get("status"))
        check("5.2 状态 = conflict 且给出 --overwrite 出路",
              res.get("status") == "conflict" and "--overwrite" in str(res.get("suggestion")),
              res.get("suggestion"))
        check("5.3 旧内容原样保留",
              (TMP / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
              == "旧稿标记-MARKER")
        res = import_docx(src, tier="raw", root=TMP, yes=True, overwrite=True,
                          interactive=False)
        check("5.4 --overwrite 才覆盖", res.get("ok") is True, res.get("error"))
        check("5.5 覆盖后内容已换回新稿", "MARKER" not in
              (TMP / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8"))
        check("5.6 覆盖前打了回退点（丙3 快照）", bool(res.get("snapshot_id")),
              res.get("snapshot_id"))
        check("5.7 被覆盖前的旧稿在快照里（能捞回来）",
              any("旧稿标记-MARKER" in p.read_text(encoding="utf-8")
                  for p in (TMP / "history").rglob("*.md")
                  if p.is_file()) if (TMP / "history").exists() else False)
        check("5.8 一路折腾下来输入 docx 仍零写入", sha(src) == src_sha,
              (src_sha, sha(src)))
    finally:
        teardown()


# ---------------------------------------------------------------- 6. 预检
def test_preview_gate():
    print("\n[6] 预检未过不落盘（红线2）")
    setup()
    try:
        src = make_docx(TMP / "回目.docx", "chinese")
        res = import_docx(src, tier="raw", root=TMP, preview_only=True)
        check("6.1 --preview 成功返回清单", res.get("ok") is True
              and len(res.get("preview") or []) == 3, res.get("status"))
        check("6.2 --preview 一个文件都没写", files_in("raw") == [], files_in("raw"))
        res = import_docx(src, tier="raw", root=TMP, yes=False, interactive=False)
        check("6.3 非交互且未给 --yes → needs_confirm 不落盘",
              res.get("status") == "needs_confirm" and files_in("raw") == [],
              (res.get("status"), files_in("raw")))
        check("6.4 needs_confirm 仍是 ok（没失败，只是等确认）", res.get("ok") is True)
    finally:
        teardown()


# ---------------------------------------------------------------- 7. 参数校验
def test_args():
    print("\n[7] 参数与输入校验")
    setup()
    try:
        check("7.1 文件不存在 → 人话报错",
              import_docx(TMP / "没有.docx", root=TMP, yes=True,
                          interactive=False).get("ok") is False)
        txt = TMP / "稿.txt"
        txt.write_text("纯文本", encoding="utf-8")
        check("7.2 非 .docx → 明确拒收并给转存建议",
              ".docx" in str(import_docx(txt, root=TMP, yes=True,
                                         interactive=False).get("error")))
        check("7.3 落位档非法 → 拒",
              import_docx(make_docx(TMP / "回目.docx", "chinese"),
                          tier="bogus", root=TMP, yes=True,
                          interactive=False).get("ok") is False)
        res = import_docx(make_docx(TMP / "回目.docx", "chinese"),
                          tier="raw", root=TMP, yes=True, interactive=False)
        check("7.4 对账结果可序列化（给接口用）",
              json.dumps({k: v for k, v in res.items() if k != "chapters"},
                         ensure_ascii=False) is not None)
    finally:
        teardown()


def main():
    if not HAS_DOCX:
        print("[FAIL] python-docx 不可用 —— 本用例无法运行（requirements.txt 应已含）")
        return 1
    test_chinese()
    test_heading_style()
    test_fallback()
    test_tiers()
    test_overwrite_guard()
    test_preview_gate()
    test_args()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
