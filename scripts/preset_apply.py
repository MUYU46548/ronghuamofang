# -*- coding: utf-8 -*-
"""出厂预设铺设：把 examples/presets/<id> 铺成可跑的当前项目。

用法：
  python scripts/preset_apply.py --list
  python scripts/preset_apply.py --apply rail-end --dry-run
  python scripts/preset_apply.py --apply rail-end            # 空工作区直接铺
  python scripts/preset_apply.py --apply rail-end --replace  # 有数据时：快照→归档→重铺
  python scripts/preset_apply.py --apply mill-dirty --hidden-ok --replace

## 为什么要有这个脚本

预设是**能力验证用例**，不是文档。要求"一条命令从已知起点出发"：
不许手工复制素材、不许手工改 yaml。

2026-10-03 的事故就是这么来的——素材整套换掉、`user_outline` 没换，
写沈砚的章喂进去的是另一个宇宙的设定，而所有门禁全程绿灯。
所以本脚本的 write 顺序固定为：**素材先进盘，配置后写，写完回读校验**。

## 破坏性纪律

- 工作区有数据时**默认拒绝**，必须显式 `--replace`
- `--replace` 会先 `snapshot.py` 快照，再走 `switch_book.archive`（先移后删，失败可回滚）
- 覆盖 `materials/raw/` 前，现有文件先备份到 `data/state/preset_backup/<时间戳>/`
- `--dry-run` 只打印计划，不写任何字节
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPTS_DIR.parent

sys.path.insert(0, str(SCRIPTS_DIR))

# 配置读取统一走 utils.config_io（严格模式：重复键/语法错误带行号报出来）。
# 不许在本文件裸 yaml.safe_load —— tests/unit/test_config_io.py 的护栏会抓。
try:
    from utils.config_io import load_config_yaml
except Exception:  # pragma: no cover - 缺 PyYAML 时会走到这里
    sys.stderr.write("[preset] 需要 PyYAML，请用项目 .venv 的 python 运行本脚本\n")
    raise SystemExit(2)

CARD_SUFFIXES = ("角色卡", "场景卡", "物品卡", "概念卡", "组织卡")


def find_presets_dir(root):
    """预设可能在 workspace 外（打包态位于 payload）。解析链：

    ① 环境变量 `NF_SAMPLE_ROOT`（打包态主进程注入 `resources/payload/examples`，
       取其 `presets/`）—— **打包态唯一能命中的候选**：主进程把 nf_api 起在
       **工作区播种的 scripts 副本**上（`ws/scripts/nf_api.py`），DEFAULT_ROOT
       因此落在工作区，而播种只带 scripts/prompts/templates、不带 examples/；
       2026-10-24 实测：装机版向导「选预设」恒空报「找不到 examples/presets/」，
       预设功能在安装包里整体失效（源码态 DEFAULT_ROOT=仓库根，测不出来）。
    ② root/examples/presets（源码态 --root 指仓库根时命中）
    ③ 脚本位置兜底（DEFAULT_ROOT）
    """
    import os
    cands = []
    env = os.environ.get("NF_SAMPLE_ROOT")
    if env:
        cands.append(Path(env) / "presets")
    for base in (Path(root), DEFAULT_ROOT):
        cands.append(base / "examples" / "presets")
    for p in cands:
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    return None


def list_presets(pdir):
    out = []
    for d in sorted(pdir.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "preset.yaml"
        if not meta_path.exists():
            continue
        try:
            meta = load_config_yaml(meta_path) or {}
        except Exception as e:
            out.append({"id": d.name, "error": str(e)[:60]})
            continue
        out.append({
            "id": meta.get("id") or d.name,
            "name": meta.get("name") or "",
            "genre": meta.get("genre") or "",
            "chapters": meta.get("chapters"),
            "hidden": bool(meta.get("hidden")),
            "summary": (meta.get("summary") or "").strip().replace("\n", " ")[:50],
            "caps": meta.get("capabilities") or [],
        })
    return out


def cmd_list(root):
    pdir = find_presets_dir(root)
    if not pdir:
        print("[preset] 找不到 examples/presets/")
        return 1
    rows = list_presets(pdir)
    print("[preset] 目录: %s" % pdir)
    print("")
    print("  %-16s %-6s %-4s %-8s %s" % ("id", "章数", "隐藏", "题材", "书名"))
    for r in rows:
        if r.get("error"):
            print("  %-16s <解析失败: %s>" % (r["id"], r["error"]))
            continue
        print("  %-16s %-6s %-4s %-8s %s" % (
            r["id"], r["chapters"], "是" if r["hidden"] else "否", r["genre"], r["name"]))
    print("")
    print("  提示：--apply <id> 铺设；隐藏用例需同时加 --hidden-ok")
    return 0


def _count_cards(raw_dir):
    if not raw_dir.is_dir():
        return 0, []
    files = [p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() == ".md"]
    return len(files), [p.name for p in files]


def apply(preset_id, root, new_name=None, replace=False, dry_run=False, hidden_ok=False):
    root = Path(root).resolve()
    pdir = find_presets_dir(root)
    if not pdir:
        print("[preset] 找不到 examples/presets/")
        return 1
    src = pdir / preset_id
    meta_path = src / "preset.yaml"
    if not meta_path.exists():
        print("[preset] 没有这套预设: %s" % preset_id)
        print("[preset] 可用: %s" % ", ".join(r["id"] for r in list_presets(pdir)))
        return 1

    meta = load_config_yaml(meta_path) or {}
    if meta.get("hidden") and not hidden_ok:
        print("[preset] %s 是隐藏用例（脏输入/内部用），加 --hidden-ok 才能铺" % preset_id)
        return 1

    # 配置写入走 utils.project_config，它按脚本所在项目定位 config/project.yaml。
    # 两者不一致时写错地方 —— 宁可拒绝，不静默写偏。
    if root != DEFAULT_ROOT.resolve():
        print("[preset] --root 必须与脚本所在项目根一致（%s）" % DEFAULT_ROOT)
        print("[preset] 原因：配置写入统一走 utils.project_config，它按代码位置定位 config/；")
        print("[preset] 跨根铺设会让素材和配置落到两处，比不铺更糟。")
        return 1

    raw_src = src / "materials" / "raw"
    n_cards, card_names = _count_cards(raw_src)
    min_cards = int((meta.get("verify") or {}).get("min_cards") or 1)
    if n_cards < min_cards:
        print("[preset] 素材不完整：%s 只有 %d 张卡，preset.yaml 要求 >= %d"
              % (preset_id, n_cards, min_cards))
        return 1

    import switch_book as sb
    import snapshot as snap_mod
    from utils.project_config import set_book_fields as pc_set_book

    books = meta.get("name") or preset_id
    if new_name:
        books = str(new_name).strip()

    work = False
    cur = None
    try:
        work = sb.has_work()
        cur = sb.current_book_name()
    except Exception:
        pass

    print("[preset] 预置: %s（%s）" % (preset_id, books))
    print("[preset] 素材: %d 张卡 -> materials/raw/" % n_cards)
    print("[preset] 章数: %s / 题材: %s" % (meta.get("chapters"), meta.get("genre")))
    if work:
        print("[preset] 当前工作区: 「%s」（有数据）" % (cur or "未命名"))

    if dry_run:
        print("[preset] --dry-run：不做任何写入。")
        if work and not replace:
            print("[preset] 真跑需要加 --replace（会先快照再归档当前项目）。")
        return 0

    if work and not replace:
        print("[preset] 拒绝：当前工作区有数据。加 --replace 归档后重铺（会先快照）。")
        return 1

    notes = []
    if work:
        try:
            snap_mod.snapshot("preset_apply")
            notes.append("已快照")
        except Exception as e:
            notes.append("快照失败(不阻断): " + str(e)[:50])
        ok, msg = sb.archive(cur or books, yes=True, force=True)
        if not ok:
            print("[preset] 归档失败，已中止（未做改动）: %s" % msg)
            return 1
        notes.append("已归档「%s」" % (cur or books))

    ok, msg = sb.init_empty()
    if not ok:
        print("[preset] 初始化空工作区失败: %s" % msg)
        return 1
    notes.append(msg)

    # 素材目录：先备份现有文件，再清空（只删文件，不删目录），最后铺入
    dst_raw = root / "materials" / "raw"
    dst_raw.mkdir(parents=True, exist_ok=True)
    existing = [p for p in dst_raw.iterdir() if p.is_file()]
    if existing:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak = root / "data" / "state" / "preset_backup" / stamp
        bak.mkdir(parents=True, exist_ok=True)
        for p in existing:
            shutil.copy2(p, bak / p.name)
        for p in existing:
            p.unlink()
        notes.append("旧素材 %d 份已备份到 %s" % (len(existing), bak))
    for p in sorted(raw_src.glob("*.md")):
        shutil.copy2(p, dst_raw / p.name)
    notes.append("素材 %d 张已铺入" % n_cards)

    fields = {
        "name": books,
        "genre": meta.get("genre"),
        "chapters": meta.get("chapters"),
        "user_outline": (meta.get("user_outline") or "").strip() or None,
    }
    ok, msg = pc_set_book({k: v for k, v in fields.items() if v is not None})
    if not ok:
        print("[preset] 素材已铺入，但写 config/project.yaml 失败: %s" % msg)
        return 1
    notes.append(msg)

    # 出厂测试项目标记：这本从此可「一键还原」（utils/factory_reset）。
    # 换书/归档时由 switch_book 清除 —— 标记与书名绑定，惰性不误伤真书。
    try:
        from utils import factory_reset as fr
        fr.write_marker(root, "preset", preset_id, books)
        notes.append("已记出厂标记（可在界面一键还原到刚铺好的状态）")
    except Exception as e:                                     # 标记失败不阻断铺设
        notes.append("出厂标记写入失败(不阻断): %s" % str(e)[:50])

    print("")
    for n in notes:
        print("  · %s" % n)
    print("")
    print("[preset] 就绪。下一步:")
    print("  python scripts/orchestrator.py            # 跑全流程")
    print("  python scripts/nfctl.py status            # 看进度与 token 闸门")
    print("  python scripts/nfctl.py check             # 收尾闸门")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="铺设出厂预设（能力验证用例）")
    ap.add_argument("--list", action="store_true", help="列出全部预设")
    ap.add_argument("--apply", metavar="ID", help="铺设指定预设")
    ap.add_argument("--name", help="覆盖书名（默认用 preset.yaml 里的）")
    ap.add_argument("--replace", action="store_true", help="工作区有数据时归档后重铺")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写入")
    ap.add_argument("--hidden-ok", action="store_true", help="允许铺设隐藏用例")
    ap.add_argument("--root", default=None, help="项目根（默认=脚本所在项目）")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    if args.list:
        return cmd_list(root)
    if args.apply:
        return apply(args.apply, root, args.name, args.replace, args.dry_run, args.hidden_ok)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
