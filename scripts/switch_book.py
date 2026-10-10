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

归档范围（2026-10-01 起）：
- `data/` 下 8 项产物（progress/setting/outline/chapters/summaries/merged/state/素材清单）
- **外加 `config/` 与 `materials/`** —— 此前不归档，于是「换书 = 丢配置与素材卡」
  （书名/类型/章节数在 `config/project.yaml`，素材与素材卡在 `materials/`）。
- `config/system.yaml` / `system.local.yaml` 是**应用级**配置（引擎/模型/预算/vault 路径），
  归档时会一并存进书档（便于回溯当时的配置），但**立刻复制一份回工作区** ——
  否则归档一完成工作区就缺 `config/system.yaml`，界面起不来。

设计约束：
- 归档用 shutil.move（先复制目标就绪再移），失败时工作区保持原样
- data/tmp/（模板缓存）不归档，跨书复用
- 恢复是**逐项合并**而非整体替换：书档里没有的文件不会删掉工作区现有那份
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

# 书档级内容（在项目根下，不在 data/ 里）：换书就该跟着走。
# 2026-10-01 之前只归档 data/ 的 8 项 → **换书会丢配置与素材卡**
# （`config/project.yaml` 里的书名/类型/章节数，`materials/` 里的素材与素材卡）。
ROOT_ITEMS = ["config", "materials"]

# 应用级配置：描述"这台机器怎么跑"（引擎/模型/预算/vault 路径），本质**不随书变**。
# 归档时仍存进书档（便于回溯"当时用的哪套配置"），但会立刻复制一份回工作区 ——
# 不复制的话，归档动作一完成工作区就缺 config/system.yaml，界面直接起不来。
APP_LEVEL_KEEP = ("system.yaml", "system.local.yaml")

UNSAFE = re.compile(r"[\\/:*?\"<>|]")


def sanitize(name):
    return UNSAFE.sub("_", (name or "").strip()) or "未命名"


def current_book_name(proj_path=None):
    """当前工作区数据属于哪本书：progress.json 优先，project.yaml 兜底。

    ⚠️ project.yaml 必须用**带重复键检测**的 loader（`utils.project_config.
    load_project_yaml`），不能用裸 safe_load（2026-10-03 修，P2）：

    PyYAML 对重复键**静默取后值**，不报错不告警 —— 于是 `book.name` 写了两遍时，
    换书拿到的是**后一个**（历史上踩到过 `user_outline` 重复 → 真实大纲被空值覆盖）。
    对建书工具而言后果更硬：可能把内容归档到**错误书名**的目录下。
    这类「看起来配好了、实际没生效」的坑必须能被机器一眼看出来。

    两个刻意的语义（别当成 bug 改掉）：
    1. **只要 project.yaml 存在就一定解析校验**（哪怕 progress.json 已经给出书名）——
       坏配置不该因为"这次恰好没读到它"而静默通过；同一份配置在有的机器上报错、
       有的机器上不报错，排查全靠运气，正是本项目最忌讳的失败形态。
    2. 解析失败**抛 ValueError**（不吞成空书名）：换书是破坏性操作（移动用户全部产物），
       配置不可信时**拒绝动手**比猜一个名字安全。CLI 层（`_dispatch`）会收敛成一行话。
    """
    path = Path(proj_path) if proj_path else (PROJECT_ROOT / "config" / "project.yaml")
    proj = None
    if path.exists():
        # 延迟导入：switch_book 在 scripts/ 不在 sys.path 时也要能 import
        from utils.project_config import load_project_yaml
        try:
            proj = load_project_yaml(path) or {}
        except Exception as e:                              # noqa: BLE001
            raise ValueError(
                "读取 " + str(path) + " 失败：" + str(e)[:200] +
                "　—— project.yaml 有重复键或格式错误时**不能**靠「取后一个值」猜书名"
                "（会把数据归档到错误书名下）。请修掉重复键后重试；"
                "体检：python scripts/nfctl.py check") from e
    # 书名：progress.json（真实位置 data/state/，旧布局在 data/ 下）优先，project.yaml 兜底。
    # ⚠️ 原先只读 `data/progress.json` —— 那是**旧布局**，现网 progress.json 在
    # data/state/ 下 → 该分支恒走不到，"progress 优先"的文档语义从未生效（一并修）。
    for cand in (DATA_DIR / "state" / "progress.json", DATA_DIR / "progress.json"):
        try:
            prog = json.loads(cand.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
        if isinstance(prog, dict) and prog.get("project"):
            return prog["project"]
    return ((proj or {}).get("book") or {}).get("name", "") or ""


def _list_dir_items(root):
    return [p for p in Path(root).iterdir() if p.name in ITEMS]


def has_work(root=None):
    """工作区是否有数据。"""
    if root is None:
        root = DATA_DIR
    if not Path(root).exists():
        # 新鲜工作区（全新克隆 / 出厂未播种，data/ 还没建）= 没数据，不是错误。
        # 这里抛 FileNotFoundError 会让 GET /state 直接 500 —— 而「工作区有没有
        # 数据」恰恰是新建项目向导在**最空的时刻**要问的问题（2026-10-10 CI 实测）。
        return False
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


def _has_root_items():
    """config/ 或 materials/ 里是否有「随书走」的内容。

    只补了应用级配置（归档后复制回来的 system.yaml / system.local.yaml）不算 ——
    否则一次归档之后就会永远认为"工作区还有数据"。
    """
    for item in ROOT_ITEMS:
        d = PROJECT_ROOT / item
        if not d.is_dir():
            continue
        for child in d.iterdir():
            if item == "config" and child.name in APP_LEVEL_KEEP:
                continue
            return True
    return False


def _archive_root_items(dest):
    """把 config/ 与 materials/ 归入书档。返回 (moved, kept)。

    kept = 复制回工作区的应用级配置文件名（见 APP_LEVEL_KEEP 的说明）。
    """
    moved, kept = [], []
    for item in ROOT_ITEMS:
        src = PROJECT_ROOT / item
        if not src.exists():
            continue
        d = dest / item
        if d.exists():
            shutil.rmtree(str(d)) if d.is_dir() else d.unlink()
        shutil.move(str(src), str(d))
        moved.append(item)
    # ⚠️ 保证工作区 `config/` 目录始终存在，且有一份**可被改写的** `project.yaml`：
    # 「新建项目」走的是**定向改写**（`set_book_fields` 读原文件再改），文件不在
    # 就直接 FileNotFoundError（实测：归档过的空工作区上 POST /project/create → 500）。
    (PROJECT_ROOT / "config").mkdir(parents=True, exist_ok=True)
    for name in APP_LEVEL_KEEP:
        back = dest / "config" / name
        if not back.exists():
            continue
        shutil.copy2(str(back), str(PROJECT_ROOT / "config" / name))
        kept.append(name)
    back_proj = dest / "config" / "project.yaml"
    if back_proj.exists():
        shutil.copy2(str(back_proj), str(PROJECT_ROOT / "config" / "project.yaml"))
        try:
            from utils import project_config as pcfg
            # 结构留、**书内容字段清空**；技术参数（word_template / target_words /
            # language / style_reference）不动 —— 那些描述"怎么写"，不是"写哪本"。
            pcfg.set_book_fields({"name": "示例书名（待填写）", "genre": "",
                                  "chapters": 3, "author": "", "user_outline": "",
                                  "style_notes": "", "style": ""})
            kept.append("project.yaml（骨架：book 值已清空）")
        except Exception as e:                            # noqa: BLE001
            kept.append("project.yaml（骨架清空失败，保留原值：" + str(e)[:40] + "）")
    return moved, kept


def _restore_root_items(src):
    """把书档里的 config/ 与 materials/ 并回工作区（**逐项合并**，不是整体替换）。

    合并而非替换是刻意的：书档里没有的文件（例如老归档根本没有 system.yaml）
    不该把工作区现有的那份删掉 —— 那会让工作区失去可运行的前提。
    """
    moved = []
    for item in ROOT_ITEMS:
        d = src / item
        if not d.exists():
            continue
        tgt = PROJECT_ROOT / item
        tgt.mkdir(parents=True, exist_ok=True)
        for child in d.iterdir():
            final = tgt / child.name
            if final.exists():
                shutil.rmtree(str(final)) if final.is_dir() else final.unlink()
            shutil.move(str(child), str(final))
        try:
            d.rmdir()
        except OSError:
            pass
        moved.append(item)
    return moved


def archive(book_name=None, yes=False, force=False):
    """归档当前工作区。返回 (success: bool, message: str)。

    force=True 时覆盖同名归档（用于 restore 路径的自动归档）。
    """
    if not yes:
        return False, "归档将移动 data/ 下产物与 config/、materials/ 到 data/books/，确认请加 --yes"
    name = book_name or current_book_name()
    if not name:
        return False, "无法确定当前书名（progress.json 与 project.yaml 均无）"
    if not has_work() and not _has_root_items():
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
        # 先搬「书档级」的 config/ 与 materials/，再搬 data/ 产物。
        # 顺序有意为之：root 项挪动失败时 data/ 还没动，回退面最小。
        root_moved, kept = _archive_root_items(dest)
        moved = _move_items(DATA_DIR, dest)
        meta = {"book": name, "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "items": moved, "root_items": root_moved,
                "kept_in_workspace": kept, "format": 2}
        (dest / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        # 成功后再删旧归档
        if tmp_dest and tmp_dest.exists():
            shutil.rmtree(tmp_dest)
        extra = ("，含 " + "、".join(root_moved)) if root_moved else ""
        keep_note = ("；应用级配置已复制回工作区（" + "、".join(kept) + "）") if kept else ""
        return True, f"已归档「{name}」→ {dest}（{len(moved)} 项{extra}）{keep_note}"
    except Exception as e:
        # 失败：先把已挪进书档的 config/ materials/ 并回工作区，再恢复旧归档
        try:
            _restore_root_items(dest)
        except Exception:                                 # noqa: BLE001
            pass
        if tmp_dest and tmp_dest.exists() and dest.exists():
            shutil.rmtree(dest)
            shutil.move(str(tmp_dest), str(dest))
        return False, f"归档失败（已尽力回退）: {e}"


def restore(book_name, yes=False):
    """恢复指定书到工作区。返回 (success: bool, message: str)。"""
    if not yes:
        return False, "恢复将移动 data/books/ 产物到 data/（含 config/、materials/），确认请加 --yes"
    src = BOOKS_DIR / sanitize(book_name)
    if not src.exists():
        return False, f"未找到归档: {src}（用 --list 查看）"
    # 先自动归档当前工作区（若不同书）
    cur = current_book_name()
    # 判据含 _has_root_items()：只靠 data/ 判断的话，「data 空但 config/materials
    # 有内容」的工作区会被直接覆盖掉，且没有任何归档留底。
    if cur and sanitize(cur) != sanitize(book_name) and (has_work() or _has_root_items()):
        print(f"[switch_book] 当前工作区有「{cur}」数据，先自动归档")
        ok, msg = archive(cur, yes=True, force=True)
        if not ok:
            return False, f"自动归档失败: {msg}"
    moved = _move_items(src, DATA_DIR)
    root_moved = _restore_root_items(src)
    extra = ("，含 " + "、".join(root_moved)) if root_moved else ""
    return True, f"已恢复「{book_name}」→ data/（{len(moved)} 项{extra}）"


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
    # config/ 同理要保证存在（新建书要写 project.yaml；system.yaml 由归档/模板提供）
    (PROJECT_ROOT / "config").mkdir(parents=True, exist_ok=True)
    return True, "空工作区骨架已初始化"


def list_books():
    """列出已归档书。返回 {"current": str, "archived": [...]}。"""
    cur = current_book_name()
    archived = []
    if BOOKS_DIR.exists():
        for d in sorted(BOOKS_DIR.iterdir()):
            if not d.is_dir():
                continue
            # _trash = 删除归档的回收站（POST /project/archive/delete 的默认落点）。
            # 不跳过的话它会以「一本叫 _trash 的书」出现在归档列表里。
            # 只认这一个名字：宽到 `_` 开头会连真叫「_某某」的书一起藏掉。
            if d.name == "_trash":
                continue
            meta = {}
            if (d / "_meta.json").exists():
                try:
                    meta = json.loads((d / "_meta.json").read_text(encoding="utf-8"))
                except (json.JSONDecodeError, ValueError):
                    pass
            n_items = len([p for p in d.iterdir() if p.name in (ITEMS + ROOT_ITEMS)])
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
    # 控制台安全网：本脚本**只在函数内**延迟 import utils（PROJECT_ROOT 与模块级 import 要
    # 保持轻量），所以模块导入时的包级挂钩此时还没生效 —— 必须在这里显式调一次。
    # 实测（2026-10-03）：换了目录 + 管道 stdout（cp936）跑本脚本，横幅里一个非 GBK 字符
    # 就 `UnicodeEncodeError` 把命令打死；而这是**破坏性操作**的入口，最不该在打印阶段崩。
    try:
        from utils.console import ensure_utf8_stdout
        ensure_utf8_stdout()
    except Exception:                                       # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="NovelForge 多书目录隔离（归档式切换）")
    parser.add_argument("--list", action="store_true", help="列出归档")
    parser.add_argument("--archive", nargs="?", const="__auto__", default=None,
                        help="归档当前工作区（可带书名）")
    parser.add_argument("--restore", default=None, metavar="书名", help="恢复指定书")
    parser.add_argument("--init", action="store_true", help="初始化空工作区")
    parser.add_argument("--yes", action="store_true", help="跳过确认")
    parser.add_argument("--force", action="store_true", help="归档时覆盖同名归档（restore 自动归档默认启用）")
    args = parser.parse_args()

    try:
        return _dispatch(args)
    except ValueError as e:
        # current_book_name 对「project.yaml 重复键 / 格式错误」**报错不吞**（P2）。
        # 这里把它收敛成**可行动的一行话**而不是 traceback：换书是破坏性操作，
        # 用户需要知道该去改哪个文件，而不是读一段栈。
        print("[switch_book] 拒绝执行（配置有问题，先修它再换书）：")
        print("  " + str(e).replace("\n", "\n  "))
        return 1


def _dispatch(args):
    # 「要动哪个项目」必须**每次都说清**（2026-10-03 事故后加的）。
    # PROJECT_ROOT 取自 `__file__`，**不是 CWD** —— 从别的目录敲 `python 某路径/switch_book.py`
    # 时，它动的是**脚本所在的那个仓库**，不是你现在所在的目录。这个差别很致命：
    # 归档 = 把当前书的产物搬走并重置工作区。实测就有一次"以为在临时目录里跑，结果归档了
    # 真实工作区"（已完整恢复）。所以这里在**任何写操作之前**把将要操作的根打出来，
    # 并显式提示「与 CWD 不同」这一情形。
    if not args.list:
        try:
            cwd = Path.cwd().resolve()
        except OSError:
            cwd = None
        print("[switch_book] 将操作的项目根: %s" % PROJECT_ROOT)
        if cwd is not None and cwd != PROJECT_ROOT:
            print("[switch_book] 注意：当前目录是 %s，**与项目根不同** ——" % cwd)
            print("              PROJECT_ROOT 取自脚本位置（不是 CWD），"
                  "本次操作只影响上面那个根。")
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
