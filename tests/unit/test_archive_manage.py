# -*- coding: utf-8 -*-
"""归档「查看 / 删除」端点自检（2026-10-06，B 档修复单）。

## 为什么

GUI 里已归档项目此前只有「恢复」一个动作：看不了内容、也删不掉 ——
归档成了死胡同（用户原话「留着堆肥？」）。本轮补三个端点：

    GET  /project/archive/tree?name=X     归档内容清单（只读）
    GET  /project/archive/file?name=X&path=rel   单文件预览（只读）
    POST /project/archive/delete {name, confirm, purge?}   删除（默认进回收站）

删除是**破坏性**操作，四道闸必须逐条验到：
1. 无 `X-Mofang-Source: gui` → 403（与止烧阈值同档，与 agent_mode 无关）
2. `confirm` 不等于书名 → 400
3. 默认移入 `data/books/_trash/`（列表不再显示），`purge=true` 才真删
4. 路径穿越（`..`、绝对路径、越界 rel）一律拒 —— sanitize 不拦 `..`

全程零网络、零 LLM；ROOT 用临时目录，**不碰真实仓库数据**。

用法：python tests/unit/test_archive_manage.py
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

# ⚠️ 必须在 import nf_api 之前钉死 NF_ROOT：模块加载期就 _set_root(_resolve_root())
TMP = Path(tempfile.mkdtemp(prefix="archive_test_"))
os.environ["NF_ROOT"] = str(TMP)

import nf_api as api                     # noqa: E402
import switch_book as sb                 # noqa: E402
from nf_api_domains import project as dp # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


class FakeH:
    """最小 handler 替身：域模块只用到 headers / _query()。"""

    def __init__(self, query=None, gui=True):
        self._q = query or {}
        self.headers = {"X-Mofang-Source": "gui"} if gui else {}

    def _query(self):
        return {k: [v] for k, v in self._q.items()}


def _mk_archive(name="雾港核心"):
    """在临时 ROOT 里造一份像样的归档。"""
    d = TMP / "data" / "books" / name
    (d / "chapters").mkdir(parents=True, exist_ok=True)
    (d / "_meta.json").write_text('{"book": "%s"}' % name, encoding="utf-8")
    (d / "chapters" / "01.md").write_text("# 第一章\n\n正文A", encoding="utf-8")
    (d / "chapters" / "02.md").write_text("# 第二章\n\n正文B", encoding="utf-8")
    (d / "binary.bin").write_bytes(b"\x00\x01\x02\x03" * 100)
    return d


def case_tree():
    print("\n[1] 归档清单 tree")
    _mk_archive()
    st, pay = dp.handle_archive_tree(FakeH({"name": "雾港核心"}))
    check("存在的归档 → 200", st == 200 and pay.get("ok"), (st, pay))
    paths = [e["path"] for e in pay.get("entries", [])]
    check("列出 _meta.json", "_meta.json" in paths, paths)
    check("列出章节目录内文件", "chapters/01.md" in paths, paths)
    check("count 与 entries 一致", pay.get("count") == len(paths), pay.get("count"))
    check("total_size > 0", pay.get("total_size", 0) > 0, pay.get("total_size"))

    st, pay = dp.handle_archive_tree(FakeH({"name": "不存在的书"}))
    check("不存在的归档 → 404", st == 404 and not pay.get("ok"), (st, pay))

    st, pay = dp.handle_archive_tree(FakeH({"name": ""}))
    check("缺 name → 400", st == 400, st)

    # 路径穿越：sanitize 不拦 `..`，必须在 _archive_dir 里显式拒
    st, pay = dp.handle_archive_tree(FakeH({"name": ".."}))
    check("name=`..` 被拒（不列出父目录）", st in (400, 404) and not pay.get("ok"),
          (st, str(pay)[:160]))
    st, pay = dp.handle_archive_tree(FakeH({"name": "../.."}))
    check("name=`../..` 被拒", st in (400, 404), (st, str(pay)[:160]))


def case_file():
    print("\n[2] 单文件预览 file")
    _mk_archive()
    st, pay = dp.handle_archive_file(FakeH({"name": "雾港核心", "path": "chapters/01.md"}))
    check("读文本文件 → 200 + 正文", st == 200 and "正文A" in pay.get("content", ""),
          (st, str(pay)[:120]))
    check("回带相对路径", pay.get("path") == "chapters/01.md", pay.get("path"))

    st, pay = dp.handle_archive_file(
        FakeH({"name": "雾港核心", "path": "../../config/project.yaml"}))
    check("rel 越界 `../../` → 拒", st in (400, 404) and not pay.get("ok"),
          (st, str(pay)[:160]))

    st, pay = dp.handle_archive_file(
        FakeH({"name": "雾港核心", "path": os.path.join("C:\\", "Windows", "win.ini")}))
    check("绝对路径 → 拒", st in (400, 404), (st, str(pay)[:160]))

    st, pay = dp.handle_archive_file(FakeH({"name": "雾港核心", "path": "binary.bin"}))
    check("二进制文件 → 415 不回内容", st == 415 and "content" not in pay, (st, pay))

    st, pay = dp.handle_archive_file(FakeH({"name": "雾港核心", "path": "nope.md"}))
    check("文件不存在 → 404", st == 404, st)

    st, pay = dp.handle_archive_file(FakeH({"name": "雾港核心"}))
    check("缺 path → 400", st == 400, st)


def case_delete_guards():
    print("\n[3] 删除四道闸")
    d = _mk_archive("闸门测试书")

    # 闸 1：非 GUI 来源
    st, pay = dp.handle_archive_delete(FakeH({"name": "闸门测试书"}, gui=False),
                                       {"name": "闸门测试书", "confirm": "闸门测试书"})
    check("非 GUI 来源 → 403", st == 403 and not pay.get("ok"), (st, str(pay)[:160]))
    check("403 未动数据", d.exists())

    # 闸 2：confirm 不等于书名
    st, pay = dp.handle_archive_delete(FakeH(), {"name": "闸门测试书", "confirm": "别的"})
    check("confirm 不匹配 → 400", st == 400 and not pay.get("ok"), (st, str(pay)[:160]))
    check("confirm 不匹配未动数据", d.exists())

    # 闸 2b：缺 confirm
    st, pay = dp.handle_archive_delete(FakeH(), {"name": "闸门测试书"})
    check("缺 confirm → 400", st == 400, st)
    check("缺 confirm 未动数据", d.exists())

    # 不存在的归档
    st, pay = dp.handle_archive_delete(FakeH(),
                                       {"name": "根本没有这本书", "confirm": "根本没有这本书"})
    check("不存在的归档 → 404", st == 404, st)


def case_delete_trash_and_purge():
    print("\n[4] 删除：默认回收站 / purge 真删")
    books = TMP / "data" / "books"

    # 4a 默认 → 移入 _trash
    d = _mk_archive("回收站测试书")
    st, pay = dp.handle_archive_delete(FakeH(),
                                       {"name": "回收站测试书", "confirm": "回收站测试书"})
    check("默认删除 → 200 mode=trashed", st == 200 and pay.get("mode") == "trashed",
          (st, pay))
    check("原目录已不在归档列表", not d.exists())
    trash_dir = books / "_trash"
    moved = list(trash_dir.glob("回收站测试书__*")) if trash_dir.is_dir() else []
    check("内容进了回收站且可找回", len(moved) == 1 and
          (moved[0] / "chapters" / "01.md").exists(), moved)

    # list_books 不该把 _trash 当成一本书
    old_root, old_books = sb.PROJECT_ROOT, sb.BOOKS_DIR
    try:
        sb.PROJECT_ROOT, sb.BOOKS_DIR = TMP, books
        lst = sb.list_books()
        names = [b["name"] for b in lst.get("archived", [])]
        check("list_books 跳过 _trash", "_trash" not in names and "闸门测试书" in names, names)
    finally:
        sb.PROJECT_ROOT, sb.BOOKS_DIR = old_root, old_books

    # 4b purge=true → 真删
    d2 = _mk_archive("真删测试书")
    st, pay = dp.handle_archive_delete(FakeH(),
                                       {"name": "真删测试书", "confirm": "真删测试书",
                                        "purge": True})
    check("purge → 200 mode=purged", st == 200 and pay.get("mode") == "purged", (st, pay))
    check("purge 后目录不存在", not d2.exists())
    check("purge 后回收站也没有", not list((books / "_trash").glob("真删测试书__*")))

    # 删除后的 tree 应 404（不是 500）
    st, pay = dp.handle_archive_tree(FakeH({"name": "真删测试书"}))
    check("删完再查 tree → 404", st == 404, st)


def case_gui_only_is_independent_of_agent_mode():
    print("\n[5] 守卫与 agent_mode 无关（_gui_only 恒查）")
    import inspect
    src = inspect.getsource(dp.handle_archive_delete)
    check("删除端点调用 _gui_only", "_gui_only(" in src)
    check("删除端点先守卫后动数据", src.index("_gui_only(") < src.index("shutil.rmtree")
          if "shutil.rmtree" in src else True)
    from utils import agent_guard
    check("/project/archive/delete 在 Agent 禁用名单",
          "/project/archive/delete" in agent_guard.FORBIDDEN_IN_AGENT_MODE)


def case_root_is_single_source():
    """`--root` 下 switch_book 与 nf_api 必须读同一个根。

    2026-10-06 真机验收踩到：`/project/list` 走 sb_mod.list_books()（按**脚本位置**
    固化的 BOOKS_DIR），而归档查看/删除按 api.ROOT —— 两者分叉时，
    项目页一边列出 A 项目的归档、一边按 B 项目解析，静默读错项目。
    """
    print("\n[6] 根唯一性（_set_root 刷新 switch_book 常量）")
    check("nf_api.ROOT == 临时根", str(api.ROOT) == str(TMP), api.ROOT)
    check("switch_book.PROJECT_ROOT 跟着 ROOT 走", sb.PROJECT_ROOT == api.ROOT, sb.PROJECT_ROOT)
    check("switch_book.BOOKS_DIR 指向 ROOT 下的 books",
          sb.BOOKS_DIR == api.ROOT / "data" / "books", sb.BOOKS_DIR)
    check("switch_book.DATA_DIR 指向 ROOT 下的 data",
          sb.DATA_DIR == api.ROOT / "data", sb.DATA_DIR)
    # list_books 与 handle_archive_tree 看到的必须是同一批归档
    books = api.ROOT / "data" / "books"
    names = {b["name"] for b in sb.list_books().get("archived", [])}
    on_disk = {d.name for d in books.iterdir()
               if d.is_dir() and not d.name.startswith("_")} if books.exists() else set()
    check("list_books 与磁盘一致", names == on_disk, (names, on_disk))


def case_forwarding_shape():
    print("\n[7] 薄转发形态（nf_api 只转发、不内联业务）")
    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("GET tree 是一行转发", "_dom(dom_project.handle_archive_tree(self))" in src)
    check("GET file 是一行转发", "_dom(dom_project.handle_archive_file(self))" in src)
    check("POST delete 是一行转发", "_dom(dom_project.handle_archive_delete(self, body))" in src)
    check("转发处无 shutil（业务不内联）",
          "shutil" not in src[src.find("project/archive/delete") - 200:
                              src.find("project/archive/delete") + 300])


def main():
    print("=" * 62)
    print("  归档查看 / 删除端点自检（临时 ROOT，不碰真实数据）")
    print("=" * 62)
    print("  ROOT =", TMP)
    try:
        case_tree()
        case_file()
        case_delete_guards()
        case_delete_trash_and_purge()
        case_gui_only_is_independent_of_agent_mode()
        case_root_is_single_source()
        case_forwarding_shape()
    finally:
        shutil.rmtree(str(TMP), ignore_errors=True)
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
