# -*- coding: utf-8 -*-
"""壳持状态的收口判据（问题总账第 7 条 / 路 B）—— 零 token、临时目录。

## 这条判据守的是什么

“状态只有一份壳不持状态”（钉④）在 GUI 侧的两次具体违例：

1. **章节列表把全书三层正文读进 `chapters` ref，且只走 Electron 独有 IPC**：
   浏览器/headless 里那条通道不存在（垫片只能编字符串），而 CLI / 外部 Agent /
   阶段重跑改了盘之后，界面**永远不重读**（要重开窗口）。
2. **章节历史与回退走 CWD 相对路径**（`refine_chapter.HISTORY_DIR` /
   `_pick_chapter_path`），而保存那侧走 `api.ROOT` —— `--root` 下列表恒空、
   回退找不到目标文件。2026-11-12 真机走“路B 改一章”时实测到（界面
   “暂无历史版本”，盘上明明有 `chNN_vM.md`）。

A 段是**源码契约**（防回潮，跑得快）；B 段是**行为判据**（真跑 `refine_chapter`，
证明列表与回退都跟着 ROOT 走，不是跟着启动目录走）。

用法：python tests/unit/test_ui_state_sources.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if (extra and not cond) else ""))


SRC = REPO / "console" / "src"


def read(name):
    return (SRC / name).read_text(encoding="utf-8")


def func_body(src, name):
    """取一个顶层函数的函数体（按花括号配平；不是语法分析，够契约断言用）。

    找不到就返回 ""，调用方的 in 断言自然变红 —— 函数被改名时不该静默通过。
    """
    m = re.search(r"^(?:async\s+)?function\s+%s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    i = src.find("{", m.end())
    if i < 0:
        return ""
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    return src[i:]


# ====================================================================== A
def case_source_contract():
    print("\n[A] 前端源码契约：章节正文只有一个取数入口")
    app = read("App.vue")
    ed = read("ChapterEditor.vue")
    desk = read("ChapterDesk.vue")

    check("A1 章节列表不再走 Electron 独有 IPC（readPreview('data/chapters/...')）",
          'readPreview("data/chapters/' not in app and "readPreview('data/chapters/" not in app,
          "App.vue 里仍有 IPC 读章节正文")
    check("A2 有唯一取数函数 reloadChapterRow（走 HTTP /chapters/content）",
          "async function reloadChapterRow" in app and "/chapters/content?n=" in app)
    check("A3 有唯一收口 afterExternalChapterWrite",
          "async function afterExternalChapterWrite" in app)
    check("A4 历史回退走收口（不再只 loadChapters）",
          re.search(r"restoreChapterVersion[\s\S]{0,1200}afterExternalChapterWrite", app) is not None)
    check("A5 整章重写完成也走收口",
          re.search(r"_reportRewrite[\s\S]{0,600}afterExternalChapterWrite", app) is not None)
    check("A6 进章节页无条件按盘上重读（不是 '加载过一次就永远不重读'）",
          re.search(r'if \(t === "chapters"\)\s*loadChapters\(\);', app) is not None
          and 'if (t === "chapters" && !chaptersLoaded.value)' not in app)
    check("A7 编辑器接收 epoch（外部写盘 → 重读）",
          "epoch: { type: Number, default: 0 }" in ed and ":epoch=" in app)
    check("A8 编辑器监听 epoch 并按盘上重读",
          re.search(r"watch\(\(\) => props\.epoch[\s\S]{0,400}reloadFromDisk", ed) is not None)
    check("A9 有未保存改动时**不静默覆盖**（dirty 时只告警）",
          re.search(r"async function reloadFromDisk\(\)[\s\S]{0,220}dirty\.value", ed) is not None)
    dead = [l for l in ed.splitlines()
            if l.strip().startswith("text.value = text.value")]     # 注释里提到不算
    check("A10 去掉死代码 `text.value = text.value`（看着像回读其实什么也没做）",
          not dead, dead)
    check("A11 审阅台把 epoch 透传给内联编辑器（否则中栏仍是旧稿）",
          ":epoch=" in desk and "epoch: { type: Number, default: 0 }" in desk)


# ====================================================================== B
def case_history_follows_root():
    print("\n[B] 章节历史 / 回退跟着 ROOT 走（不是跟着启动目录）")
    import refine_chapter as rc

    check("B0 本模块不再有模块级 ROOT 全局（隐式全局会隔着几层改别人的行为）",
          not hasattr(rc, "ROOT"),
          getattr(rc, "ROOT", None))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "data" / "chapters" / "refined").mkdir(parents=True)
        (root / "data" / "chapters" / "history").mkdir(parents=True)
        chap = root / "data" / "chapters" / "refined" / "01.md"
        chap.write_text("## 第1章 甲\n\n当前稿内容。\n", encoding="utf-8")
        (root / "data" / "chapters" / "history" / "ch01_v1.md").write_text(
            "## 第1章 甲\n\n旧稿内容。\n", encoding="utf-8")

        check("B1 _history_dir(root) 跟传进来的根",
              rc._history_dir(root) == root / "data" / "chapters" / "history",
              rc._history_dir(root))
        check("B2 _pick_chapter_path(root) 在该根下找到章",
              rc._pick_chapter_path(1, root) == chap, rc._pick_chapter_path(1, root))
        versions = rc.list_versions(1, root=root)
        check("B3 列表读的是该根那份历史（API 以前传空 → 恒为空）",
              len(versions) == 1 and versions[0]["version"] == 1, versions)
        ok, msg = rc.restore_version(1, 1, root=root)
        check("B4 回退真的改到该根那份章", ok and "旧稿内容" in chap.read_text(encoding="utf-8"), msg)
        check("B5 回退前当前稿也存了一版（回退本身可回退）",
              len(list((root / "data" / "chapters" / "history").glob("ch01_v*.md"))) >= 2)

    # 不传 root = 老行为（cwd）。这条防的是“又有人在模块里加个全局把默认值顶掉”。
    with tempfile.TemporaryDirectory() as cwd:
        old = os.getcwd()
        try:
            os.chdir(cwd)
            check("B6 不传 root 时仍按 cwd 找（CLI 直跑的老行为不变）",
                  rc._pick_chapter_path(99) is None and rc.list_versions(99) == [])
        finally:
            os.chdir(old)


# ====================================================================== C
def case_save_then_reread():
    print("\n[C] 保存后按盘上重读：界面不许拿自己的副本当真相（第 7 条·大纲/审稿/设定/设置）")
    app = read("App.vue")
    out = read("OutlineView.vue")
    rev = read("ReviewConsole.vue")
    vdp = read("VaultDirsPanel.vue")

    ls = func_body(app, "loadSetting")
    check("C1 去掉一次性门闩（原来进了设定页就永远不重读）",
          re.search(r"^\s*if \(settingLoaded\.value\)[^\n]*return;", app, re.M) is None,
          "App.vue 里仍有 settingLoaded 门闩")
    check("C2 loadSetting 收 force（点“刷新”能强制重读）",
          "async function loadSetting(force = false)" in app)
    check("C3 有未保存修改时不静默覆盖（只有 force 才丢修改）",
          "settingDirty.value && !force" in ls, ls[:200])
    check("C4 读回来的 mtime 记进 settingStamp（保存时要带回去）",
          "settingStamp.value = r.data.mtime" in ls)
    check("C5 进设定页按盘上重读（与章节页同一条规矩）",
          re.search(r'if \(t === "story"\)\s*loadSetting\(\);', app) is not None)

    sv = func_body(app, "saveSetting")
    check("C6 保存时带 expected_mtime（盘上指纹）",
          "expected_mtime: settingStamp.value" in sv, sv[:200])
    check("C7 保存成功后按盘上重读", "await loadSetting(true)" in sv)
    check("C8 盘上被改过时拒收并说清怎么办（409 分支）",
          "r.status === 409" in sv and "已被改过" in sv)

    rf = func_body(app, "refreshSetting")
    check("C9 刷新按钮不再是死按钮（绑 refreshSetting，且它真的重读）",
          '@click="refreshSetting"' in app and "await loadSetting(true)" in rf)
    check("C10 原文编辑（saveSettingEdit）保存后也重读",
          "await loadSetting(true)" in func_body(app, "saveSettingEdit"))
    check("C11 定价保存后重读", "await loadRates()" in func_body(app, "saveRates"))
    check("C12 风格笔记保存后重读", "await loadStyleNotes()" in func_body(app, "saveStyleNotes"))

    o = func_body(out, "save")
    check("C13 大纲保存后按盘上重读（不再把本地 raw 当已落盘）",
          "await load(" in o and "savedRaw.value = raw.value;" not in o, o[:240])

    hyd = func_body(rev, "loadSavedDecisions")
    check("C14 审稿决策从盘上回填勾选状态（以前只数了个条数）",
          "decisions.value[" in hyd and "d.action" in hyd, hyd[:240])
    check("C15 审稿决策保存后重读",
          "await loadSavedDecisions()" in func_body(rev, "saveDecisions"))

    sp = func_body(app, "savePrompt")
    check("C16 提示词保存后回读正文 + 备份链（编辑器不能停在提交值）",
          "await openPrompt(" in sp and "await loadPromptList()" in sp, sp[-260:])
    check("C17 知识库目录保存后按盘上重读（后端会归一化路径）",
          "await load();" in func_body(vdp, "save"))


for fn in (case_source_contract, case_history_follows_root, case_save_then_reread):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
