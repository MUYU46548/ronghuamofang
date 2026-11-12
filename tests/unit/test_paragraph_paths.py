# -*- coding: utf-8 -*-
"""段落精修的路径判据（问题总账第 7 条续）—— 零 token、临时目录。

## 为什么单独一条

`refine_paragraph` 是“撤回”的另一半（段落历史 / 回退 / 手改 / 候选采用）。它原来完全
按 **CWD 相对** 路径读写，而且整章备份目录写成了 `data/chapters.history`（**少了分隔符**，
笔误）：文档说备份在 `data/chapters/history/`，实际落在旁边一个带点的目录里 ——
用户在文档指的位置找不到“回退用的备份”，`--root` 起服务时还会读写到另一个项目。

现在与 `refine_chapter` 同一口径：**显式 `root=` 参数**（API 侧一律传 `api.ROOT`，
不传 = 老行为 cwd）。不用模块级全局 —— 那种写法会被任何进程内 `import nf_api`
的代码钉死（`test_stage4_ch14_fixes` 当场打回过一次）。

用法：python tests/unit/test_paragraph_paths.py
"""
import json
import os
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


CHAP = """## 第1章 甲

第一段原文。

第二段原文。

第三段原文。
"""


def build_ws():
    tmp = Path(tempfile.mkdtemp(prefix="para_paths_"))
    (tmp / "data" / "chapters" / "refined").mkdir(parents=True)
    (tmp / "data" / "chapters" / "refined" / "01.md").write_text(CHAP, encoding="utf-8")
    return tmp


def main():
    import refine_paragraph as rp

    print("\n[A] 路径函数：跟 root 走，且笔误已修")
    check("A0 模块里没有 ROOT 全局（隐式全局会隔着几层改别人行为）",
          not hasattr(rp, "ROOT"), getattr(rp, "ROOT", None))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        hist = rp._history_dir(root)
        check("A1 整章备份目录 = <root>/data/chapters/history（不再是 chapters.history）",
              hist == root / "data" / "chapters" / "history", hist)
        check("A2 段落历史目录跟 root",
              rp._para_history_dir(root) == root / "data" / "chapters" / "para_history")
        check("A3 候选文件跟 root",
              rp._pending_path(root) == root / "data" / "state" / "para_pending.json")

    print("\n[B] 段落历史 / 回退 / 手改：全部落在传进来的那个根上")
    ws = build_ws()
    try:
        chap = ws / "data" / "chapters" / "refined" / "01.md"
        paras = rp.split_paragraphs(chap.read_text(encoding="utf-8"))
        # 第 0 段是章标题，正文从第 1 段开始（与前端段落编号同一口径）
        check("B0 夹具切成 4 段（标题 + 三段正文）", len(paras) == 4, paras)

        rec = rp.save_paragraph_history(1, 1, paras[1], "第一段改后。", "测试", root=ws)
        check("B1 段落历史写到 <root>/data/chapters/para_history",
              (ws / "data" / "chapters" / "para_history"
               / ("ch01_p1_h%d.json" % rec["id"])).exists())
        got = rp.list_paragraph_history(1, 1, root=ws)
        check("B2 列表读得回同一条", len(got) == 1 and got[0]["rewritten"] == "第一段改后。", got)

        ok, msg = rp.restore_paragraph_version(1, 1, rec["id"], root=ws)
        now = chap.read_text(encoding="utf-8")
        check("B3 回退成功且整章备份进 <root>/data/chapters/history",
              ok and list((ws / "data" / "chapters" / "history").glob("ch01_v*.md")),
              msg)
        # 标题会被 `_norm` 做中文标点/标题归一（"第1章 甲" → "第1章甲"），这是既有行为；
        # 这里只要求"标题还在、另两段一字不动、目标段真的回来了"。
        check("B4 只换目标段：标题还在、另两段原样、目标段回到原文",
              now.startswith("## 第1章") and now.count("第二段原文。") == 1
              and now.count("第三段原文。") == 1 and "第一段原文。" in now, now)
        check("B5 老笔误目录没被用到（data/chapters.history 不存在）",
              not (ws / "data" / "chapters.history").exists())

        ok2, msg2 = rp.save_paragraph_text(1, 2, "第二段手改。", root=ws)
        after = chap.read_text(encoding="utf-8")
        check("B6 手改一段：只动那一段", ok2 and "第二段手改。" in after
              and "第一段原文。" in after, msg2)
        check("B7 手改也进段落历史（可回退）", len(rp.list_paragraph_history(1, 2, root=ws)) == 1)
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)

    print("\n[C] 候选（preview → 采用 / 放弃）也跟 root")
    ws = build_ws()
    try:
        rec = {"chapter": 1, "index": 3, "before": "第三段原文。", "after": "第三段候选。",
               "feedback": "测试", "backup": "", "version": 1, "at": "2026-11-12T00:00:00"}
        rp._save_pending([rec], ws)
        check("C1 候选写在 <root>/data/state/para_pending.json",
              (ws / "data" / "state" / "para_pending.json").exists())
        check("C2 另一个根读不到它（不是进程级共享）",
              rp.get_pending(1, 3, root=Path(tempfile.mkdtemp())) is None)
        check("C3 自己的根读得到", rp.get_pending(1, 3, root=ws) is not None)
        ok, msg = rp.apply_pending(1, 3, root=ws)
        chap = (ws / "data" / "chapters" / "refined" / "01.md").read_text(encoding="utf-8")
        check("C4 采用候选 → 正文真的变了", ok and "第三段候选。" in chap, msg)
        check("C5 采用后候选出队", rp.get_pending(1, 3, root=ws) is None)
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)

    print("\n[D] 不传 root = 老行为（cwd）")
    old = os.getcwd()
    with tempfile.TemporaryDirectory() as cwd:
        try:
            os.chdir(cwd)
            check("D1 不传 root：cwd 下没有章 → 定位不到",
                  rp._pick_chapter_path(1) is None)
            check("D2 不传 root：历史列表为空（不会去翻别的项目）",
                  rp.list_paragraph_history(1, 0) == [])
        finally:
            os.chdir(old)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
