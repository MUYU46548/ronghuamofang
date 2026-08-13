# -*- coding: utf-8 -*-
"""多书目录隔离（P0.7 #10，归档式切换）。

NovelForge 是单书工作区：data/ 只存当前一本书。换书时必须清空 data/，
旧书产物直接丢失。本脚本把每本书的数据归档到 data/books/{书名}/，
切书 = 归档当前 + 恢复目标，保留全部产物，可随时切回。

用法：
  python scripts/switch_book.py --list                    # 列出已归档书 + 当前书
  python scripts/switch_book.py --archive                 # 归档当前工作区（书名取 progress/project.yaml）
  python scripts/switch_book.py --archive "书名"           # 归档到指定书名
  python scripts/switch_book.py --restore "书名"           # 恢复指定书（自动先归档当前）
  python scripts/switch_book.py --init                    # 初始化空工作区（保留 data/tmp 缓存）

设计约束：
- 归档用 shutil.move（先复制目标就绪再移），失败时工作区保持原样
- data/tmp/（模板缓存）不归档，跨书复用
- 破坏性动作前打印清单，需 --yes 确认
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text

BOOKS_DIR = Path("data/books")
DATA_DIR = Path("data")
ITEMS = ["progress.json", "setting", "outline", "chapters", "summaries",
         "merged", "state", "materials_manifest.json"]
UNSAFE = re.compile(r"[\\/:*?\"<>|]")


def sanitize(name):
    return UNSAFE.sub("_", (name or "").strip()) or "未命名"


def current_book_name(proj_path="config/project.yaml"):
    """当前工作区数据属于哪本书：progress.json 优先，其次 project.yaml。"""
    try:
        prog = json.loads(read_text(str(DATA_DIR / "progress.json")))
        if prog.get("project"):
            return prog["project"]
    except (json.JSONDecodeError, ValueError, FileNotFoundError):
        pass
    try:
        import yaml
        proj = yaml.safe_load(read_text(proj_path))
        return proj.get("book", {}).get("name", "")
    except Exception:
        return ""


def _list_dir_items(root):
    return [p for p in Path(root).iterdir() if p.name in ITEMS]


def has_work(root=DATA_DIR):
    """工作区是否有数据。"""
    return bool(_list_dir_items(root))


def _move_items(src_root, dest_root):
    moved = []
    dest_root.mkdir(parents=True, exist_ok=True)
    for item in ITEMS:
        s = Path(src_root) / item
        if s.exists():
            d = Path(dest_root) / item
            if d.exists():
                shutil.rmtree(d) if d.is_dir() else d.unlink()
            shutil.move(str(s), str(d))
            moved.append(item)
    return moved


def archive(book_name=None, yes=False):
    if not yes:
        print("[switch_book] 归档将移动 data/ 下产物到 data/books/，确认请加 --yes")
        return 1
    name = book_name or current_book_name()
    if not name:
        print("[switch_book] 无法确定当前书名（progress.json 与 project.yaml 均无）")
        return 1
    if not has_work():
        print("[switch_book] 工作区无数据，无需归档")
        return 0
    dest = BOOKS_DIR / sanitize(name)
    if dest.exists():
        print(f"[switch_book] {dest} 已存在同名归档。"
              "如需覆盖请手动处理，或先用 --restore 恢复")
        return 1
    moved = _move_items(DATA_DIR, dest)
    meta = {"book": name, "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "items": moved}
    write_text(str(dest / "_meta.json"), json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"[switch_book] 已归档「{name}」→ {dest}（{len(moved)} 项）")
    return 0


def restore(book_name, yes=False):
    if not yes:
        print("[switch_book] 恢复将移动 data/books/ 产物到 data/，确认请加 --yes")
        return 1
    src = BOOKS_DIR / sanitize(book_name)
    if not src.exists():
        print(f"[switch_book] 未找到归档: {src}（用 --list 查看）")
        return 1
    # 先自动归档当前工作区（若不同书）
    cur = current_book_name()
    if cur and sanitize(cur) != sanitize(book_name) and has_work():
        print(f"[switch_book] 当前工作区有「{cur}」数据，先自动归档")
        if archive(cur, yes=True) != 0:
            return 1
    moved = _move_items(src, DATA_DIR)
    print(f"[switch_book] 已恢复「{book_name}」→ data/（{len(moved)} 项）")
    return 0


def init_empty():
    """初始化空工作区（保留 data/tmp 缓存）。"""
    if has_work():
        print("[switch_book] 工作区有数据，请先 --archive")
        return 1
    for d in ("setting", "outline/chapters", "chapters/raw", "chapters/checked",
              "chapters/refined", "summaries", "merged", "state/tasks"):
        (DATA_DIR / d).mkdir(parents=True, exist_ok=True)
    print("[switch_book] 空工作区骨架已初始化")
    return 0


def list_books():
    print("===== 当前书 =====")
    cur = current_book_name()
    print(f"  工作区: {cur or '（未初始化）'}")
    print("===== 已归档书 =====")
    if not BOOKS_DIR.exists():
        print("  （无）")
        return 0
    for d in sorted(BOOKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta = {}
        if (d / "_meta.json").exists():
            try:
                meta = json.loads(read_text(str(d / "_meta.json")))
            except (json.JSONDecodeError, ValueError):
                pass
        n_items = len([p for p in d.iterdir() if p.name in ITEMS])
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        print(f"  {d.name}  [{meta.get('book', '?')}]  "
              f"{n_items} 项 / {size/1024:.0f} KB  {meta.get('archived_at', '')}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="NovelForge 多书目录隔离（归档式切换）")
    parser.add_argument("--list", action="store_true", help="列出归档")
    parser.add_argument("--archive", nargs="?", const="__auto__", default=None,
                        help="归档当前工作区（可带书名）")
    parser.add_argument("--restore", default=None, metavar="书名", help="恢复指定书")
    parser.add_argument("--init", action="store_true", help="初始化空工作区")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    args = parser.parse_args()

    if args.list:
        return list_books()
    if args.init:
        return init_empty()
    if args.archive is not None:
        name = None if args.archive == "__auto__" else args.archive
        return archive(name, yes=args.yes)
    if args.restore:
        return restore(args.restore, yes=args.yes)
    list_books()
    return 0


if __name__ == "__main__":
    sys.exit(main())
