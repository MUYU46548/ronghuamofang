# -*- coding: utf-8 -*-
"""章节正文读写域：用户「自带编辑器」的后端（2026-10-26）。

## 为什么需要它

用户第 1 条：「我之前提的画线批改之类的都没有，而且编辑还要强制用外部编辑器
打开，没有自带编辑器。」
第 2 条：「完全不是一个适合审阅、适合精修、适合讨论的形态。」

这一层是「编辑器」的地基：读三层现状、读单层全文、把手改内容**安全地**存回去。
三个动作都零 token（不调模型），所以判据可以做到确定性。

## 判据

A. `GET /chapters/layers`：三层各自存在性/字数/标题/mtime，并指出「当前稿」是哪层。
B. `GET /chapters/content`：读指定层全文；不存在的层 404（不返空串冒充成功）。
C. `POST /chapters/save` 的四道护栏：
   - 写前**备份**旧稿到 `history/chNN_vM.md`（保存本身可回退）
   - 缺 `## 第N章` **自动补**（用户常只贴正文；缺标题会让下游判失败）
   - 落盘前**标点归一化**（与流水线同一实现，幂等）
   - `expected_mtime` 不一致 → **409**，绝不覆盖流水线刚写的那一版
D. 拒绝路径：空内容 / 超长 / 非法 layer / 非法章号 —— 一律 400 且说清原因。
E. **手改不改 progress**：progress.json 一个字节都不动（阶段状态只归 orchestrator）。

全程离线、零 LLM、零费用。
用法：python tests/unit/test_chapter_editor.py
"""
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.pop("NF_ROOT", None)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:280]) if detail else ""))


class FakeHandler:
    """只提供域模块需要的那一个方法（_query）。"""

    def __init__(self, query=""):
        self.path = "/chapters/x" + (("?" + query) if query else "")

    def _query(self):
        from urllib.parse import parse_qs
        return parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}


def _seed(root, n=3):
    """造三章正文 + project/system 配置。"""
    cfg = Path(root) / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "project.yaml").write_text(
        "book:\n  name: 探针书\n  chapters: %d\n" % n, encoding="utf-8")
    (cfg / "system.yaml").write_text(
        "chapter:\n  target_words: [10, 100]\n  title_format: \"第{n}章 {title}\"\n",
        encoding="utf-8")
    for layer, suffix in (("raw", "原稿"), ("checked", "检查稿"), ("refined", "润色稿")):
        d = Path(root) / "data" / "chapters" / layer
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            (d / ("%02d.md" % i)).write_text(
                "## 第%d章测试%d\n\n第 %d 章%s正文，用来验证读取与保存。\n"
                % (i, i, i, suffix), encoding="utf-8")


def main():
    import nf_api
    from nf_api_domains import chapters as dom

    print("【A】三层现状（layers）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_editor_"))
    nf_api._set_root(tmp)
    try:
        _seed(tmp)
        code, p = dom.handle_chapters_layers(FakeHandler("n=2"))
        check("读三层 200", code == 200 and p.get("ok"), p)
        layers = p.get("layers") or {}
        check("三层都返回", set(layers) == {"raw", "checked", "refined"}, sorted(layers))
        check("raw 存在且字数 > 0",
              layers["raw"]["exists"] and layers["raw"]["words"] > 0, layers["raw"])
        check("标题被解析出来（带「第2章」）", "第2章" in layers["raw"]["title"],
              layers["raw"]["title"])
        check("当前稿取最高存在层（refined = 成品层，编辑器默认打开它）",
              p.get("current_layer") == "refined", p.get("current_layer"))
        check("另给「最近改动层」供界面提示",
              p.get("last_modified_layer") in {"raw", "checked", "refined"},
              p.get("last_modified_layer"))
        check("回显单章字数口径", p.get("target_words") == [10, 100], p.get("target_words"))
        code, p = dom.handle_chapters_layers(FakeHandler("n=abc"))
        check("章号非法 → 400", code == 400 and "n 必填" in p.get("error", ""), p)

        print("\n【B】读单层全文（content）")
        code, p = dom.handle_chapters_content(FakeHandler("n=1&layer=refined"))
        check("读 refined 200", code == 200 and p.get("ok"), p.get("error"))
        check("返回正文（不是路径）", "正文" in (p.get("content") or ""), p.get("content", "")[:60])
        check("返回字数与 mtime", p.get("words", 0) > 0 and p.get("mtime", 0) > 0, p)
        code, p = dom.handle_chapters_content(FakeHandler("n=99&layer=refined"))
        check("不存在的章 → 404（不返空串冒充成功）", code == 404, p)
        code, p = dom.handle_chapters_content(FakeHandler("n=1&layer=bogus"))
        check("非法 layer → 400", code == 400 and "layer" in p.get("error", ""), p)

        print("\n【C】保存（save）的四道护栏")
        # 记录 progress 原始字节，后面断言"手改不动 progress"
        prog = tmp / "data" / "state" / "progress.json"
        prog.parent.mkdir(parents=True, exist_ok=True)
        prog.write_text('{"stages": {"6": {"status": "done"}}}', encoding="utf-8")
        prog_before = prog.read_bytes()

        target = tmp / "data" / "chapters" / "refined" / "01.md"
        before_text = target.read_text(encoding="utf-8")
        mtime = target.stat().st_mtime
        new_body = "这是用户手改后的正文。\n\n他想加一段。\n"
        code, p = dom.handle_chapters_save(
            FakeHandler(), {"n": 1, "layer": "refined", "content": new_body,
                            "expected_mtime": mtime})
        check("保存 200", code == 200 and p.get("ok"), p)
        check("缺标题被自动补（title_added=true）", p.get("title_added") is True, p)
        saved = target.read_text(encoding="utf-8")
        check("落盘内容含用户正文", "用户手改后的正文" in saved, saved[:80])
        check("补的标题符合 title_format（第1章）", "## 第1章" in saved, saved.splitlines()[0])
        check("写前备份旧稿到 history",
              bool(p.get("backup")) and (tmp / "data" / "chapters" / "history" / p["backup"]).exists(),
              p.get("backup"))
        backup_file = tmp / "data" / "chapters" / "history" / (p.get("backup") or "x")
        if backup_file.exists():
            check("备份内容 = 改前原文", backup_file.read_text(encoding="utf-8") == before_text)
        check("返回字数", p.get("words", 0) > 0, p.get("words"))

        print("\n【C2】标点归一化（与流水线同一实现）")
        dirty = '## 第1章 测试\n\n他说："这样不对。"\n\n「好」她答。\n'
        code, p = dom.handle_chapters_save(
            FakeHandler(), {"n": 1, "layer": "raw", "content": dirty})
        check("保存 200", code == 200, p.get("error"))
        norm = (tmp / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
        check("半角引号转中文引号", '"' not in norm and "“" in norm, norm)
        check("直角引号转中文引号", "「" not in norm and "”" in norm, norm)
        check("归一化计数回传", isinstance(p.get("normalized"), dict) and p["normalized"], p.get("normalized"))

        print("\n【C3】并发保护：expected_mtime 不一致 → 409")
        target2 = tmp / "data" / "chapters" / "refined" / "02.md"
        stale = target2.stat().st_mtime - 100
        code, p = dom.handle_chapters_save(
            FakeHandler(), {"n": 2, "layer": "refined", "content": "## 第2章 手改\n\n正文。\n",
                            "expected_mtime": stale})
        check("过期 mtime → 409（不覆盖）", code == 409, (code, p))
        check("409 说明是并发冲突", "重新加载" in (p.get("error") or ""), p.get("error"))
        check("文件未被改动（还是原「润色稿」正文）",
              "润色稿" in target2.read_text(encoding="utf-8"),
              target2.read_text(encoding="utf-8")[:40])

        print("\n【D】拒绝路径")
        for name, body, want in [
            ("空内容 → 400", {"n": 1, "layer": "refined", "content": "   \n"}, 400),
            ("超长内容 → 400", {"n": 1, "layer": "refined", "content": "字" * 200001}, 400),
            ("非法 layer → 400", {"n": 1, "layer": "draft", "content": "正文"}, 400),
            ("非法章号 → 400", {"n": "x", "layer": "refined", "content": "正文"}, 400),
            ("缺 content → 400", {"n": 1, "layer": "refined"}, 400),
        ]:
            code, p = dom.handle_chapters_save(FakeHandler(), body)
            check(name, code == want, (code, p.get("error")))

        print("\n【E】手改不动 progress（阶段状态只归 orchestrator）")
        check("progress.json 一个字节都没变", prog.read_bytes() == prog_before)

        print("\n【F】字数提示（低于/超出区间要说出来）")
        code, p = dom.handle_chapters_save(
            FakeHandler(), {"n": 3, "layer": "refined", "content": "太短。"})
        check("低于下限 → warning 非空", code == 200 and "低于下限" in (p.get("warning") or ""),
              p.get("warning"))
        code, p = dom.handle_chapters_save(
            FakeHandler(), {"n": 3, "layer": "refined",
                            "content": "字" * 120})
        check("超过上限 → warning 非空", code == 200 and "超过上限" in (p.get("warning") or ""),
              p.get("warning"))
    finally:
        nf_api._set_root(ROOT)

    print("\n【F】前端接线（防回潮）")
    app_vue = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("章节页签引入就地编辑器", "<ChapterEditor" in app_vue)
    check("章节页签引入三栏审阅台", "<ChapterDesk" in app_vue)
    check("每章行有「编辑」入口", "openEditor(c.n)" in app_vue)
    check("编辑器挂到章节页签（列表视图外，两视图都能开）",
          '@saved="onChapterSaved"' in app_vue)
    check("审阅台入口按钮存在", "toggleChapterView" in app_vue)

    editor = (ROOT / "console" / "src" / "ChapterEditor.vue").read_text(encoding="utf-8")
    check("编辑器有保存（POST /chapters/save）", "/chapters/save" in editor)
    check("编辑器带 Ctrl+S", "ctrlKey" in editor and "save()" in editor)
    check("编辑器支持内联形态（审阅台中栏复用同一实现）",
          "embedded" in editor and "ce-embedded" in editor)
    check("段落批注走 /refine/paragraph", "/refine/paragraph" in editor)

    desk = (ROOT / "console" / "src" / "ChapterDesk.vue").read_text(encoding="utf-8")
    check("审阅台三栏：章列表 / 正文 / 大纲与评审",
          "desk-left" in desk and "desk-mid" in desk and "desk-right" in desk)
    check("审阅台读本章大纲", "/outline/chapters/get" in desk)
    check("审阅台读七维评审", "/review/ledger" in desk)

    print("\n【G】GUI 文案不带直角引号（用户第 5 条：中文引号是“”和‘’）")
    offenders = []
    for p in sorted((ROOT / "console" / "src").glob("*.vue")):
        t = p.read_text(encoding="utf-8")
        if chr(0x300C) in t or chr(0x300D) in t or chr(0x300E) in t or chr(0x300F) in t:
            offenders.append(p.name)
    check("console/src 下无「」『』残留", not offenders, offenders)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
