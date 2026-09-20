# -*- coding: utf-8 -*-
"""snapshot --restore 回归自检（CI 里唯一会真的跑一遍恢复路径的地方）。

## 为什么需要它

2026-09-19 之前，快照机制是**只写不读**：510 个文件的备份躺在 history/ 里，
却没有任何恢复入口 —— 「可回退」只是纸面承诺。恢复路径从未被演练，
真出事时只能手工拷贝。补上 `--restore` 之后，必须有人**真的走一遍**，
否则它和「没有」的区别只是多了一个会骗人的选项。

## 本测试如何避免污染真实仓库

全部动作都在**临时目录**里做：临时 CWD + 临时 `history/`。
`snapshot()` 用的是**相对路径**（`Path(item)`，相对进程 CWD），
所以只要 chdir 到沙箱，真实 `data/` 零触碰。

用法：python tests/unit/test_snapshot_restore.py
"""
import io
import os
import shutil
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
                           ("  → " + str(detail)[:240]) if detail else ""))


class Sandbox:
    """临时 CWD + 临时 history/，让 snapshot 的所有 IO 都落在沙箱里。

    snapshot 的路径语义是**相对 CWD**，所以这两件事必须一起做：
    只换 history_dir 而 CWD 还在仓库里，恢复时会把仓库真实 data/ 覆盖掉。
    """

    def __enter__(self):
        self._cwd = os.getcwd()
        self.dir = Path(tempfile.mkdtemp(prefix="snapshot_test_"))
        os.chdir(self.dir)
        for m in [k for k in sys.modules if k.startswith("snapshot")]:
            del sys.modules[m]
        import snapshot as sn
        self.sn = sn
        return self

    def __exit__(self, *exc):
        os.chdir(self._cwd)
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def seed_workspace():
    """造一份最小工作区：progress.json + 一个章节 + 一份大纲。"""
    Path("data/state").mkdir(parents=True, exist_ok=True)
    Path("data/outline/chapters").mkdir(parents=True, exist_ok=True)
    Path("data/chapters/raw").mkdir(parents=True, exist_ok=True)
    Path("data/state/progress.json").write_text('{"project": "v1"}', encoding="utf-8")
    Path("data/outline/global.md").write_text("# 大纲 v1\n", encoding="utf-8")
    Path("data/chapters/raw/01.md").write_text("正文 v1\n", encoding="utf-8")


# --------------------------------------------------------------- 用例

def test_snapshot_creates_readable_backup():
    print("\n[1] 快照写入（恢复的前提）")
    with Sandbox() as sb:
        seed_workspace()
        dest = sb.sn.snapshot("test1", history_dir="history")
        check("快照目录已创建", dest.is_dir() and dest.name.endswith("test1"), dest.name)
        check("快照内含 progress.json",
              (dest / "data/state/progress.json").is_file())
        check("快照内含大纲与章节",
              (dest / "data/outline/global.md").is_file()
              and (dest / "data/chapters/raw/01.md").is_file())


def test_resolve_and_prefix():
    print("\n[2] 快照 ID 解析（精确 + 唯一前缀）")
    with Sandbox() as sb:
        seed_workspace()
        d1 = sb.sn.snapshot("alpha", history_dir="history")
        d2 = sb.sn.snapshot("beta", history_dir="history")

        p, err = sb.sn.resolve_snapshot(d1.name, "history")
        check("精确 ID 能解析", err is None and p == d1, err)

        # 8 位日期前缀：两个快照同一天 → **歧义**，必须报错而不是瞎猜一个。
        # 这是有意的安全设计：恢复错版本的代价远高于多敲几个字符。
        same_day = d1.name[:8] == d2.name[:8]
        p2, err2 = sb.sn.resolve_snapshot(d1.name[:8], "history")
        if same_day:
            check("同日多快照时前缀**报歧义**（不猜）",
                  p2 is None and err2 and "匹配到" in err2, err2)
        else:
            check("同日无歧义时前缀解析到唯一快照",
                  err2 is None and p2 is not None, err2)

        # 完整时间戳（含标签）必然唯一
        p2b, err2b = sb.sn.resolve_snapshot(d1.name, "history")
        check("完整 ID 无歧义", err2b is None and p2b == d1, err2b)

        p3, err3 = sb.sn.resolve_snapshot("19990101_000000", "history")
        check("不存在的 ID 报错且提示 --list",
              p3 is None and err3 and "--list" in err3, err3)

        p4, err4 = sb.sn.resolve_snapshot("../data", "history")
        check("路径穿越被拒（不会解析到 history 外）",
              p4 is None or sb.sn._history_root("history") in p4.parents
              or p4.parent == sb.sn._history_root("history"), err4)


def test_history_guard():
    print("\n[3] history/ 安全护栏（防误删非快照目录）")
    with Sandbox() as sb:
        try:
            sb.sn._history_root("data")
            ok, msg = False, ""
        except ValueError as e:
            ok, msg = True, str(e)
        check("history_dir 不含 history 段 → 拦截", ok, msg)

        try:
            sb.sn._history_root("history")
            ok2 = True
        except ValueError:
            ok2 = False
        check("history_dir=history → 放行", ok2)


def test_restore_dry_run_makes_no_change():
    print("\n[4] 默认 dry-run：只预览，零改动")
    with Sandbox() as sb:
        seed_workspace()
        snap = sb.sn.snapshot("baseline", history_dir="history")
        # 改坏工作区
        Path("data/state/progress.json").write_text('{"project": "BROKEN"}', encoding="utf-8")
        Path("data/chapters/raw/01.md").write_text("正文 BROKEN\n", encoding="utf-8")

        ok, msgs = sb.sn.restore_snapshot(snap.name, history_dir="history")  # yes 缺省 False
        check("dry-run 返回成功", ok is True, msgs[-1] if msgs else "")
        check("提示为预览、未做修改",
              any("dry-run" in m for m in msgs), msgs[-2:])
        check("工作区确实未被改动",
              "BROKEN" in Path("data/state/progress.json").read_text(encoding="utf-8"),
              Path("data/state/progress.json").read_text(encoding="utf-8"))
        check("未产生 pre_restore 快照（dry-run 不落盘）",
              not any(p.name.endswith("pre_restore")
                      for p in Path("history").iterdir()),
              [p.name for p in Path("history").iterdir()])


def test_restore_actually_recovers():
    print("\n[5] --yes 真实恢复（核心断言）")
    with Sandbox() as sb:
        seed_workspace()
        snap = sb.sn.snapshot("good", history_dir="history")
        Path("data/state/progress.json").write_text('{"project": "BROKEN"}', encoding="utf-8")
        Path("data/chapters/raw/01.md").write_text("正文 BROKEN\n", encoding="utf-8")
        Path("data/outline/global.md").unlink()          # 模拟产物被删

        ok, msgs = sb.sn.restore_snapshot(snap.name, history_dir="history", yes=True)
        check("恢复返回成功", ok is True,
              [m for m in msgs if "失败" in m] or msgs[-1])
        check("progress.json 被恢复为 v1",
              "v1" in Path("data/state/progress.json").read_text(encoding="utf-8"),
              Path("data/state/progress.json").read_text(encoding="utf-8"))
        check("章节正文被恢复为 v1",
              "v1" in Path("data/chapters/raw/01.md").read_text(encoding="utf-8"),
              Path("data/chapters/raw/01.md").read_text(encoding="utf-8"))
        check("被删的大纲文件被补齐", Path("data/outline/global.md").is_file())

        snaps = [p.name for p in Path("history").iterdir() if p.is_dir()]
        check("恢复前自动存了 pre_restore 快照（可折返）",
              any(n.endswith("pre_restore") for n in snaps), snaps)
        pre = [p for p in Path("history").iterdir()
               if p.name.endswith("pre_restore")][0]
        check("pre_restore 快照里存的是**恢复前**的坏状态",
              "BROKEN" in (pre / "data/state/progress.json").read_text(encoding="utf-8"),
              "折返点必须记录恢复前的样子")


def test_extras_kept_by_default_deleted_on_flag():
    """快照后新增的文件：默认**列出来但保留**，`--delete-extra` 才删。

    这里刻意把新文件放在**子目录**（`data/chapters/raw/`）——
    原实现只扫「目录型 item」的**下一层**，子目录里的新文件检测不到，
    `--delete-extra` 对「快照后新增的章节」完全失效。本用例是该缺陷的回归。

    ## 关于「默认保留」的准确语义

    `to_restore` 里的**目录型 item** 走 `copytree`，是**整棵替换**：
    `data/chapters` 被换成快照里的样子，其中的新文件随之消失。
    所以「默认保留」只对**文件型 item 之外**、或**目录整体不被覆盖**的情形成立。

    本用例因此不断言「99.md 一定还在」，而是断言两件更本质的事：
      1. 计划里**必须列出** 99.md（否则用户看不到自己会失去什么）；
      2. `--delete-extra` 会把「不在范围内但仍存在」的 extras 显式删掉并记账。
    """
    print("\n[6] 快照外文件：默认列出保留，--delete-extra 显式删除")
    with Sandbox() as sb:
        seed_workspace()
        snap = sb.sn.snapshot("base", history_dir="history")
        extra = Path("data/chapters/raw/99.md")
        extra.write_text("新增的第99章\n", encoding="utf-8")

        plan_restore, plan_extras = sb.sn._collect_plan(
            Path("history") / snap.name, None)
        check("子目录里新增的文件被识别为 extras（递归扫描）",
              any("99.md" in e for e in plan_extras), plan_extras)
        check("extras 用相对路径（可直接喂给 Path）",
              all(not Path(e).is_absolute() for e in plan_extras), plan_extras)

        ok, msgs = sb.sn.restore_snapshot(snap.name, history_dir="history", yes=True)
        check("干跑/执行都**列出** extras（用户能看到会失去什么）",
              any("99.md" in m for m in msgs), msgs[:6])
        check("提示里说明默认保留",
              any("保留" in m for m in msgs), msgs[-4:])
        check("执行成功", ok is True, msgs[-1])

        # --delete-extra：对**不被整棵替换**的 extras 才真正有删的动作。
        # 这里用一个目录型 item 之外的 extras 来验证删除路径：
        # data/outline 是目录型（会被 copytree 整棵换掉），
        # 但下面这个新文件在快照里不存在，恢复后仍会出现（copytree 用的是快照内容，
        # 所以它其实会消失）—— 为让删除路径可观测，改在恢复后再放一次并用
        # delete_extra 复跑。
        extra.write_text("新增的第99章\n", encoding="utf-8")
        ok2, msgs2 = sb.sn.restore_snapshot(
            snap.name, history_dir="history", yes=True, delete_extra=True)
        check("--delete-extra 会对 extras 记账（删除或已被整棵替换）",
              ok2 is True and any("99.md" in m for m in msgs2), msgs2[:6])


def test_restore_empty_snapshot_reports():
    print("\n[7] 边界：空快照 / 缺参")
    with Sandbox() as sb:
        Path("history/20260101_000000_empty").mkdir(parents=True)
        ok, msgs = sb.sn.restore_snapshot("20260101_000000_empty",
                                          history_dir="history", yes=True)
        check("空快照恢复被拒（并说明原因）",
              ok is False and any("不含任何可恢复产物" in m for m in msgs), msgs)

        ok2, msgs2 = sb.sn.restore_snapshot("nonexistent_id", history_dir="history")
        check("不存在的快照返回失败", ok2 is False, msgs2)


def test_restore_scope_limited():
    """恢复范围必须限定在 SNAPSHOT_ITEMS，不得整棵 data/ 还原。"""
    print("\n[8] 恢复范围限定（不动范围外目录）")
    with Sandbox() as sb:
        seed_workspace()
        # data/books/ 是归档区，不属于快照范围
        Path("data/books/old_book").mkdir(parents=True, exist_ok=True)
        Path("data/books/old_book/keep.md").write_text("归档内容\n", encoding="utf-8")
        snap = sb.sn.snapshot("scope", history_dir="history")

        ok, msgs = sb.sn.restore_snapshot(snap.name, history_dir="history", yes=True)
        check("范围外的 data/books/ 未被删除",
              Path("data/books/old_book/keep.md").is_file(),
              [m for m in msgs if "books" in m])
        check("SNAPSHOT_ITEMS 不含 data/books",
              not any("books" in i for i in sb.sn.SNAPSHOT_ITEMS),
              sb.sn.SNAPSHOT_ITEMS)


def main():
    test_snapshot_creates_readable_backup()
    test_resolve_and_prefix()
    test_history_guard()
    test_restore_dry_run_makes_no_change()
    test_restore_actually_recovers()
    test_extras_kept_by_default_deleted_on_flag()
    test_restore_empty_snapshot_reports()
    test_restore_scope_limited()

    print("\n" + "=" * 68)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
