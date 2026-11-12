# -*- coding: utf-8 -*-
"""路 D“停下与回退”里**能零 token 验的那一半**：回退链。

问题总账第五节：路 D = **停止不亏稿、回退能回到上一版**。

“停止”那一半的真相要真跑一次写作才看得见（半截存残稿、已完成章不受影响），
本用例不假装验它 —— 那部分在 e2e 里只验“没任务时不给一个点了没用的停止按钮”和
“写完这章就停”的开关真落盘。

“回退”那一半是确定性文件操作，可以逐字验，而且**回退本身还得可回退**：

| 组 | 判据 |
|---|---|
| A 章节 | 改两版 → 退到 v1 → 再退到“回退前快照” → 逐字回到改后那一版 |
| B 段落 | 手改一段 → 退到历史 h1 → 再退一次（回退前快照 h2）→ 回到第一次手改的内容 |
| C 快照 | `snapshot.py` 打快照 → 改章 → `--restore <ID> --yes` → 章逐字回到快照时内容，且**恢复前自动留了 `pre_restore` 快照**（恢复错了能折返） |

用法：python tests/unit/test_restore_chains.py
"""
import os
import shutil
import subprocess
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


V1 = "## 第1章 甲\n\n第一版正文。\n\n第二段。\n"
V2 = "## 第1章 甲\n\n第二版正文（改过一句）。\n\n第二段。\n"
V3 = "## 第1章 甲\n\n第三版正文（又改了一句）。\n\n第二段。\n"


def build_ws():
    ws = Path(tempfile.mkdtemp(prefix="restore_chains_"))
    (ws / "data" / "chapters" / "refined").mkdir(parents=True)
    (ws / "data" / "setting").mkdir(parents=True)
    (ws / "data" / "state").mkdir(parents=True)
    (ws / "config").mkdir(parents=True)
    (ws / "data" / "chapters" / "refined" / "01.md").write_text(V1, encoding="utf-8")
    (ws / "data" / "setting" / "setting.json").write_text(
        '{"characters":[],"world":{},"plot_fragments":[],"timeline":[],"_meta":{"version":1}}',
        encoding="utf-8")
    return ws


def case_chapter_chain():
    print("\n[A] 章节回退链：回退本身也可回退")
    import refine_chapter as rc

    ws = build_ws()
    try:
        chap = ws / "data" / "chapters" / "refined" / "01.md"
        # 走"保存"同一条链：先备份旧稿，再写新稿
        v1 = rc._next_version(1, ws)                       # 1
        shutil.copy2(chap, rc._history_dir(ws) / ("ch01_v%d.md" % v1))
        chap.write_text(V2, encoding="utf-8")              # 现在盘上 = V2
        v2 = rc._next_version(1, ws)                       # 2（V2 的快照）
        shutil.copy2(chap, rc._history_dir(ws) / ("ch01_v%d.md" % v2))
        chap.write_text(V3, encoding="utf-8")              # 现在盘上 = V3

        ok, msg = rc.restore_version(1, v1, root=ws)
        check("A1 退到 v1：盘上逐字回到第一版", ok and chap.read_text(encoding="utf-8") == V1, msg)

        # 回退前那次覆盖会把 V3 存成新版本（回退本身可回退）
        vers = [v["version"] for v in rc.list_versions(1, root=ws)]
        check("A2 回退前自动留了一版（版本号只增不减）", vers and max(vers) >= 3, vers)
        ok2, msg2 = rc.restore_version(1, max(vers), root=ws)
        check("A3 再退到“回退前快照”：逐字回到第三版（V3）",
              ok2 and chap.read_text(encoding="utf-8") == V3, msg2)
        check("A4 两次回退都没丢版本（清单里 v1..v3 都在）",
              set(vers) >= {1, 2, 3} or {1, 2, 3}.issubset(set(vers)), vers)
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def case_paragraph_chain():
    print("\n[B] 段落回退链：手改 → 回退 → 再回退")
    import refine_paragraph as rp

    ws = build_ws()
    try:
        chap = ws / "data" / "chapters" / "refined" / "01.md"
        ok, msg = rp.save_paragraph_text(1, 1, "第一段手改甲。", root=ws)
        check("B1 手改第 1 段成功", ok and "第一段手改甲。" in chap.read_text(encoding="utf-8"), msg)
        hists = rp.list_paragraph_history(1, 1, root=ws)
        check("B2 手改进了段落历史（可回退）", len(hists) == 1, hists)
        first_id = hists[0]["id"]

        ok2, msg2 = rp.restore_paragraph_version(1, 1, first_id, root=ws)
        now = chap.read_text(encoding="utf-8")
        check("B3 回退到 h1：段落回到原文", ok2 and "第一版正文。" in now, msg2)

        hists2 = rp.list_paragraph_history(1, 1, root=ws)
        check("B4 回退前也存了一版（h2）", len(hists2) == 2, [h["id"] for h in hists2])
        newest = max(h["id"] for h in hists2)
        ok3, msg3 = rp.restore_paragraph_version(1, 1, newest, root=ws)
        check("B5 再退到“回退前快照”：回到手改后的内容",
              ok3 and "第一段手改甲。" in chap.read_text(encoding="utf-8"), msg3)
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def case_snapshot_chain():
    print("\n[C] 快照恢复：CLI 怎么用就怎么验")
    ws = build_ws()
    try:
        chap = ws / "data" / "chapters" / "refined" / "01.md"
        py = sys.executable
        snap = REPO / "scripts" / "snapshot.py"

        p1 = subprocess.run([py, str(snap), "路D快照"], cwd=str(ws), capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
        hist = sorted((ws / "history").glob("*"), key=lambda d: d.name)
        check("C1 快照目录建出来了", p1.returncode == 0 and hist, (p1.stdout or "")[-200:])
        if not hist:
            return
        snap_id = hist[-1].name

        chap.write_text(V3, encoding="utf-8")            # 快照之后又改了
        p2 = subprocess.run([py, str(snap), "--restore", snap_id], cwd=str(ws),
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("C2 不加 --yes 只预览、不动盘",
              chap.read_text(encoding="utf-8") == V3 and "--yes" in (p2.stdout or ""),
              (p2.stdout or "")[-300:])

        p3 = subprocess.run([py, str(snap), "--restore", snap_id, "--yes"], cwd=str(ws),
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("C3 --yes 后章逐字回到快照时内容", chap.read_text(encoding="utf-8") == V1,
              (p3.stdout or "")[-300:])
        names = [d.name for d in (ws / "history").glob("*")]
        check("C4 恢复前自动留了 pre_restore 快照（恢复错了还能折返）",
              any("pre_restore" in n for n in names), names)
    finally:
        shutil.rmtree(ws, ignore_errors=True)


for fn in (case_chapter_chain, case_paragraph_chain, case_snapshot_chain):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
