# -*- coding: utf-8 -*-
"""第一波防御 · 任务四验收（导出链模板化，纯离线，零 LLM）。

## 施工单验收线（2026-10-22）

> 占位素材导出 3 份：打开无格式错乱、零占位符残留；故意造 1 份坏数据，
> 必须被校验拦下不出文件。

## 本用例的对应

1. **3 份正常导出**：3 章占位素材 → stage7 全链（merge → 前校验 → docx →
   后校验）→ 成品可打开、章标题齐全、四占位符零残留；连续 3 次幂等。
2. **坏数据拦截（四类各一）**：
   - 占位符残留（正文带 XXX）→ 前校验拦下，output/ 无文件
   - 结构错位（空章）→ 前校验拦下
   - 模板缺失（book.word_template 指向不存在的文件）→ 前校验拦下
     （改动前是静默降级成硬编码样式 —— 用户以为用了模板其实没有）
   - 成品层兜底：伪造"占位符替换失败"的 docx → 后校验拦下并删除文件
3. **判据单元**：check_merged_md / check_template / check_docx_output
   各自的边界（协议标记/绝对路径泄漏/章号重复/缺 Heading 1 样式）。
4. **挂账兑现**：占位模板（templates/小说格式模板V1.3.dotx）已就位，
   用户自有模板到手后改 book.word_template 即插即用（本用例走的就是
   配置指定模板的路径，证明"换模板不改代码"）。

用法：python tests/unit/test_export_chain.py
"""
import io
import os
import random
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.export_check import (check_merged_md, check_template,   # noqa: E402
                                check_docx_output)
from utils.progress_manager import ProgressManager                # noqa: E402
import stage7_convert as s7                                       # noqa: E402

PASS, FAIL = [], []

_POOL = ("他她它的是了在有人不来去上下天地山水风雨云光暗门窗桌椅灯火茶酒"
         "剑刀机关齿轮锁链绳索封印阵法符文石砖瓦木铜铁银金布衣裘袍靴帽")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def _gen_body(target_chars, seed):
    rng = random.Random(seed)
    paras, total, i = [], 0, 0
    while total < target_chars:
        i += 1
        n = rng.randint(180, 260)
        s = "第%d段" % i + "".join(rng.choice(_POOL) for _ in range(n))
        paras.append(s)
        total += len(s)
    return "\n\n".join(paras)


def _chapter(n, chars=2400):
    return ("## 第%d章 测试章\n\n" % n) + _gen_body(chars, 7 + n) + "\n"


def build_workspace(total=3, word_template="templates/小说格式模板V1.3.dotx"):
    """临时工作区：3 章占位素材 + 占位模板（复制自仓库）。"""
    tmp = Path(tempfile.mkdtemp(prefix="expchain_"))
    (tmp / "config").mkdir()
    tmpl_rel = ""
    if word_template:
        (tmp / "templates").mkdir()
        shutil.copy2(ROOT / "templates" / "小说格式模板V1.3.dotx",
                     tmp / "templates" / "小说格式模板V1.3.dotx")
        tmpl_rel = word_template
    proj = ['book:', '  name: "导出链测试书"', '  chapters: %d' % total]
    if tmpl_rel:
        proj.append('  word_template: "%s"' % tmpl_rel)
    (tmp / "config" / "project.yaml").write_text("\n".join(proj) + "\n",
                                                 encoding="utf-8")
    (tmp / "config" / "system.yaml").write_text(
        "chapter:\n  page_break: true\n", encoding="utf-8")
    raw = tmp / "data" / "chapters" / "raw"
    raw.mkdir(parents=True)
    for n in range(1, total + 1):
        (raw / ("%02d.md" % n)).write_text(_chapter(n), encoding="utf-8")
    (tmp / "data" / "state").mkdir(parents=True, exist_ok=True)
    return tmp


def run_stage7(tmp, proj_overrides=None):
    import yaml
    os.chdir(tmp)
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    if proj_overrides:
        proj.setdefault("book", {}).update(proj_overrides)
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s7.run_stage(cfg, proj, progress, None, None)
    return ok, msg, progress


def _docx_flat(p):
    import zipfile
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    return re.sub(r"<[^>]+>", "", xml)


def case_three_good_exports():
    print("\n【1】验收线主体：占位素材导出 3 份 —— 可打开、无错乱、零占位符残留")
    tmp = build_workspace(total=3)
    origin = os.getcwd()
    try:
        for round_no in (1, 2, 3):
            ok, msg, progress = run_stage7(tmp)
            docx = tmp / "output" / "导出链测试书_完整版.docx"
            check("第 %d 轮导出成功" % round_no, ok is True, msg)
            check("第 %d 轮成品在盘且非空" % round_no,
                  docx.exists() and docx.stat().st_size > 0)
            if docx.exists():
                flat = _docx_flat(docx)
                residue = [p for p in ("[书籍标题]", "[作者]", "[目录占位符]",
                                       "[请输入文本]", "XXX", "TODO")
                           if p in flat]
                check("第 %d 轮零占位符残留" % round_no, residue == [], residue)
                check("第 %d 轮书名已填" % round_no, "导出链测试书" in flat)
                import zipfile
                with zipfile.ZipFile(docx) as z:
                    styles = z.read("word/styles.xml").decode("utf-8", "replace")
                    docxml = z.read("word/document.xml").decode("utf-8", "replace")
                heads = len(re.findall(r'<w:pStyle w:val="(?:1|Heading1)"', docxml))
                check("第 %d 轮 3 个章标题（Heading 1）齐全" % round_no,
                      heads == 3, heads)
                check("第 %d 轮 TOC 域已替换占位符" % round_no,
                      'TOC \\o "1-3"' in docxml)
                check("第 %d 轮模板样式在成品里（非硬编码回退）" % round_no,
                      "<w:style" in styles, "styles.xml 有样式定义")
            check("第 %d 轮阶段标 done" % round_no,
                  progress.stage_status(7) == "done")
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_bad_data_blocked():
    print("\n【2】坏数据拦截：占位符残留 / 空章 / 模板缺失 —— 全部拦下且不出文件")
    # ---- a. 正文带 XXX ----
    tmp = build_workspace(total=2)
    origin = os.getcwd()
    try:
        raw = tmp / "data" / "chapters" / "raw"
        (raw / "02.md").write_text("## 第2章 测试章\n\n此处待补充正文 XXX TODO。\n",
                                   encoding="utf-8")
        ok, msg, progress = run_stage7(tmp)
        docx = tmp / "output" / "导出链测试书_完整版.docx"
        check("占位符残留章 → 导出判失败", ok is False, msg)
        check("占位符残留章 → 文件未产出（校验不过不出文件）", not docx.exists())
        check("失败原因点名占位符", "占位符" in msg or "占位符" in
              str(progress.data["stages"].get("7", {}).get("error", "")),
              progress.data["stages"].get("7", {}))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- b. 空章 ----
    tmp = build_workspace(total=2)
    try:
        raw = tmp / "data" / "chapters" / "raw"
        (raw / "02.md").write_text("## 第2章 测试章\n\n   \n", encoding="utf-8")
        ok, msg, _ = run_stage7(tmp)
        docx = tmp / "output" / "导出链测试书_完整版.docx"
        check("空章 → 导出判失败且不出文件", ok is False and not docx.exists(), msg)
        check("空章 → 失败原因点名空章", "空章" in msg, msg)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- c. 模板缺失（改动前：静默降级硬编码样式照出文件）----
    tmp = build_workspace(total=2, word_template="templates/不存在的模板.dotx")
    try:
        ok, msg, _ = run_stage7(tmp)
        docx = tmp / "output" / "导出链测试书_完整版.docx"
        check("模板缺失 → 导出判失败且不出文件", ok is False and not docx.exists(),
              msg)
        check("模板缺失 → 失败原因点名模板", "模板不存在" in msg, msg)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_post_check_deletes():
    print("\n【3】成品层兜底：占位符替换失败的 docx → 后校验拦下并删除")
    tmp = build_workspace(total=1)
    origin = os.getcwd()
    try:
        # 先正常导出一次拿到底版，再往里塞一个残留占位符的段落
        ok, _, _ = run_stage7(tmp)
        check("前置：正常导出成功", ok is True)
        docx = tmp / "output" / "导出链测试书_完整版.docx"
        # 直接调 check_docx_output：伪造"成品里还有占位符"的场景
        import zipfile
        bad = tmp / "output" / "bad.docx"
        with zipfile.ZipFile(docx) as zin, \
                zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/document.xml":
                    data = data.replace(
                        "</w:body>".encode(),
                        ("<w:p><w:r><w:t>[请输入文本]</w:t></w:r></w:p>"
                         "</w:body>").encode())
                zout.writestr(item, data)
        errs, _ = check_docx_output(bad, md_text=None)
        check("残留 [请输入文本] 的成品被后校验拦下",
              any("[请输入文本]" in e for e in errs), errs)
        # 通过 stage7 的整链不适用（后校验在 run_stage 内部）——
        # 这里直接验证 run_stage 对"后校验失败 → 删文件"的行为：
        # 用 monkeypatch 让 md_to_docx 产出坏成品。
        import stage7_convert as s7m
        real = s7m.md_to_docx

        def _bad_writer(md_text, out_path, **kw):
            shutil.copy2(bad, out_path)

        s7m.md_to_docx = _bad_writer
        try:
            ok2, msg2, _ = run_stage7(tmp)
        finally:
            s7m.md_to_docx = real
        check("坏成品 → stage7 判失败", ok2 is False, msg2)
        check("坏成品 → 文件已删除（不出文件）", not docx.exists())
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_units():
    print("\n【4】判据单元：协议标记 / 绝对路径 / 章号重复 / 模板缺样式")
    errs, _ = check_merged_md("# 书\n\n## 第1章 x\n\n正文。\n\n===FILE: x===\n")
    check("协议标记残留被拦", any("协议标记" in e for e in errs), errs)
    errs, _ = check_merged_md("# 书\n\n## 第1章 x\n\n正文，路径 E:\\code\\x 写进去了。\n")
    check("绝对路径泄漏被拦", any("绝对路径" in e for e in errs), errs)
    errs, _ = check_merged_md("# 书\n\n## 第1章 x\n\n正文。\n\n## 第1章 重号\n\n正文。\n")
    check("章号重复被拦", any("章号重复" in e for e in errs), errs)
    errs, _ = check_merged_md("# 书\n\n没有章标题的稿子。\n")
    check("无章标题被拦", any("章标题" in e for e in errs), errs)
    errs, warns = check_merged_md(
        "# 书\n\n## 第1章 x\n\n| a | b |\n| - | - |\n正文。\n")
    check("复杂元素只警告不拦（导出时降级）",
          errs == [] and any("表格" in w for w in warns), (errs, warns))
    errs, _ = check_template(ROOT / "templates" / "小说格式模板V1.3.dotx")
    check("占位模板（V1.3）过样式校验 —— 挂账模板即插即用", errs == [], errs)


def case_api_path_guarded():
    print("\n【5】GUI 导出路（POST /export/word）也被校验拦 —— 防旁路回归")
    import importlib
    import nf_api as api_mod
    import nf_api_domains.post_misc as post_misc
    from utils import project_config as pc
    tmp = build_workspace(total=2)
    origin = os.getcwd()
    orig_py = pc.PROJECT_YAML
    try:
        # 把 handler 的配置族钉到本夹具根
        pc.PROJECT_YAML = tmp / "config" / "project.yaml"
        api_mod._set_root(tmp)
        os.chdir(tmp)
        # 坏数据：第 2 章带 XXX 占位符
        (tmp / "data/chapters/raw/02.md").write_text(
            "## 第2章 测试章\n\n此处待补充正文 XXX。\n", encoding="utf-8")
        status, payload = post_misc.handle_export_word(None, {})
        docx = tmp / "output" / "导出链测试书_完整版.docx"
        check("GUI 路坏数据 → 400 且点名占位符",
              status == 400 and "占位符" in str(payload.get("error", "")),
              (status, str(payload.get("error", ""))[:120]))
        check("GUI 路坏数据 → 文件未产出", not docx.exists())
        # 好数据 → 200 照常出文件（新校验不误伤正常导出）
        (tmp / "data/chapters/raw/02.md").write_text(_chapter(2), encoding="utf-8")
        status2, payload2 = post_misc.handle_export_word(None, {})
        check("GUI 路好数据 → 200 正常导出（不误伤）",
              status2 == 200 and docx.exists(), (status2, str(payload2)[:100]))
    finally:
        pc.PROJECT_YAML = orig_py
        api_mod._set_root(ROOT)
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    t0 = time.time()
    for fn in (case_units, case_three_good_exports,
               case_bad_data_blocked, case_post_check_deletes,
               case_api_path_guarded):
        fn()
    print("\n==== 结果: PASS %d / FAIL %d （%.1fs）===="
          % (len(PASS), len(FAIL), time.time() - t0))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
