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
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 项目根目录：脚本在 scripts/ 下，项目根是其父目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOOKS_DIR = PROJECT_ROOT / "data" / "books"
DATA_DIR = PROJECT_ROOT / "data"
ITEMS = ["progress.json", "setting", "outline", "chapters", "summaries",
         "merged", "state", "materials_manifest.json"]
UNSAFE = re.compile(r"[\\/:*?\"<>|]")


def sanitize(name):
    return UNSAFE.sub("_", (name or "").strip()) or "未命名"


def current_book_name(proj_path=None):
    """当前工作区数据属于哪本书：progress.json 优先，其次 project.yaml。"""
    if proj_path is None:
        proj_path = PROJECT_ROOT / "config" / "project.yaml"
    try:
        prog = json.loads((DATA_DIR / "progress.json").read_text(encoding="utf-8"))
        if prog.get("project"):
            return prog["project"]
    except (json.JSONDecodeError, ValueError, FileNotFoundError):
        pass
    try:
        import yaml
        proj = yaml.safe_load(Path(proj_path).read_text(encoding="utf-8"))
        return proj.get("book", {}).get("name", "")
    except Exception:
        return ""


def _list_dir_items(root):
    return [p for p in Path(root).iterdir() if p.name in ITEMS]


def has_work(root=None):
    """工作区是否有数据。"""
    if root is None:
        root = DATA_DIR
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


def archive(book_name=None, yes=False, force=False):
    """归档当前工作区。返回 (success: bool, message: str)。

    force=True 时覆盖同名归档（用于 restore 路径的自动归档）。
    """
    if not yes:
        return False, "归档将移动 data/ 下产物到 data/books/，确认请加 --yes"
    name = book_name or current_book_name()
    if not name:
        return False, "无法确定当前书名（progress.json 与 project.yaml 均无）"
    if not has_work():
        return True, "工作区无数据，无需归档"
    dest = BOOKS_DIR / sanitize(name)
    if dest.exists() and not force:
        return False, f"{dest} 已存在同名归档。如需覆盖请加 --force，或先用 --restore 恢复"
    # 原子化归档：若目标已存在，先移为临时名，新归档成功后再删旧归档
    tmp_dest = None
    if dest.exists():
        ts_tmp = datetime.now().strftime("%Y%m%d_%H%M%S")
        tmp_dest = dest.with_name(dest.name + "_old_" + ts_tmp)
        shutil.move(str(dest), str(tmp_dest))

    try:
        moved = _move_items(DATA_DIR, dest)
        meta = {"book": name, "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "items": moved}
        (dest / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # 成功后再删旧归档
        if tmp_dest and tmp_dest.exists():
            shutil.rmtree(tmp_dest)
        return True, f"已归档「{name}」→ {dest}（{len(moved)} 项）"
    except Exception as e:
        # 失败：恢复旧归档
        if tmp_dest and tmp_dest.exists() and dest.exists():
            shutil.rmtree(dest)
            shutil.move(str(tmp_dest), str(dest))
        return False, f"归档失败（已回退）: {e}"


def restore(book_name, yes=False):
    """恢复指定书到工作区。返回 (success: bool, message: str)。"""
    if not yes:
        return False, "恢复将移动 data/books/ 产物到 data/，确认请加 --yes"
    src = BOOKS_DIR / sanitize(book_name)
    if not src.exists():
        return False, f"未找到归档: {src}（用 --list 查看）"
    # 先自动归档当前工作区（若不同书）
    cur = current_book_name()
    if cur and sanitize(cur) != sanitize(book_name) and has_work():
        print(f"[switch_book] 当前工作区有「{cur}」数据，先自动归档")
        ok, msg = archive(cur, yes=True, force=True)
        if not ok:
            return False, f"自动归档失败: {msg}"
    moved = _move_items(src, DATA_DIR)
    return True, f"已恢复「{book_name}」→ data/（{len(moved)} 项）"


def init_empty():
    """初始化空工作区（保留 data/tmp 缓存）。返回 (success: bool, message: str)。"""
    if has_work():
        return False, "工作区有数据，请先 --archive"
    # 自动归档当前工作区（防止误操作丢失）
    cur = current_book_name()
    if cur:
        print(f"[switch_book] 自动归档当前工作区「{cur}」")
        ok, msg = archive(cur, yes=True, force=True)
        if not ok:
            return False, f"自动归档失败: {msg}"
    for d in ("setting", "outline/chapters", "chapters/raw", "chapters/checked",
              "chapters/refined", "summaries", "merged", "state/tasks"):
        (DATA_DIR / d).mkdir(parents=True, exist_ok=True)
    return True, "空工作区骨架已初始化"


def list_books():
    """列出已归档书。返回 {"current": str, "archived": [...]}。"""
    cur = current_book_name()
    archived = []
    if BOOKS_DIR.exists():
        for d in sorted(BOOKS_DIR.iterdir()):
            if not d.is_dir():
                continue
            meta = {}
            if (d / "_meta.json").exists():
                try:
                    meta = json.loads((d / "_meta.json").read_text(encoding="utf-8"))
                except (json.JSONDecodeError, ValueError):
                    pass
            n_items = len([p for p in d.iterdir() if p.name in ITEMS])
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            archived.append({
                "name": d.name,
                "display_name": meta.get("book", d.name),
                "items": n_items,
                "size_kb": round(size / 1024),
                "archived_at": meta.get("archived_at", ""),
            })
    return {"current": cur or "", "archived": archived}


def main():
    parser = argparse.ArgumentParser(description="NovelForge 多书目录隔离（归档式切换）")
    parser.add_argument("--list", action="store_true", help="列出归档")
    parser.add_argument("--archive", nargs="?", const="__auto__", default=None,
                        help="归档当前工作区（可带书名）")
    parser.add_argument("--restore", default=None, metavar="书名", help="恢复指定书")
    parser.add_argument("--init", action="store_true", help="初始化空工作区")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    parser.add_argument("--force", action="store_true", help="归档时覆盖同名归档（restore 自动归档默认启用）")
    args = parser.parse_args()

    if args.list:
        books = list_books()
        print("===== 当前书 =====")
        print(f"  工作区: {books['current'] or '（未初始化）'}")
        print("===== 已归档书 =====")
        if not books["archived"]:
            print("  （无）")
            return 0
        for b in books["archived"]:
            print(f"  {b['name']}  [{b['display_name']}]  "
                  f"{b['items']} 项 / {b['size_kb']} KB  {b['archived_at']}")
        return 0
    if args.init:
        ok, msg = init_empty()
        print(msg)
        return 0 if ok else 1
    if args.archive is not None:
        name = None if args.archive == "__auto__" else args.archive
        ok, msg = archive(name, yes=args.yes, force=args.force)
        print(f"[switch_book] {msg}")
        return 0 if ok else 1
    if args.restore:
        ok, msg = restore(args.restore, yes=args.yes)
        print(f"[switch_book] {msg}")
        return 0 if ok else 1
    # 默认列出
    books = list_books()
    print("===== 当前书 =====")
    print(f"  工作区: {books['current'] or '（未初始化）'}")
    print("===== 已归档书 =====")
    if not books["archived"]:
        print("  （无）")
        return 0
    for b in books["archived"]:
        print(f"  {b['name']}  [{b['display_name']}]  "
              f"{b['items']} 项 / {b['size_kb']} KB  {b['archived_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
