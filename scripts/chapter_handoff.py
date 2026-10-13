# -*- coding: utf-8 -*-
"""分章 handoff 索引 + 三级读取协议（丙1 + 丙2）。

## 要解决的问题（任务书）

- 丙1: handoff 接手索引（作品身份+章目录+stage+绑定三字段）
  催更姬 #4 落地 —— 当前全仓零命中。
- 丙2: 三级读取协议（列表不带正文→get 才全文→不为一条连线读全书）
  催更姬 #3 落地。

## 设计

### 索引数据结构（data/state/chapter_handoff.json）

```json
{
  "version": 1,
  "generated_at": "2026-10-12 21:00:00",
  "work_identity": {
    "book_name": "雾港核心",
    "project_root": "/path/to/project"
  },
  "chapters": {
    "01": {
      "n": 1,
      "title": "第一章 标题",
      "file": "01.md",
      "stage": 4,
      "stage_status": "done",
      "sha256": "abc...",
      "size": 12345,
      "bound_at": "2026-10-12 20:30:00"
    }
  }
}
```

### 三级读取协议

- L1 list: 只返回到 stage 级（作品身份 + 章目录 + stage 状态）
  → 不吃正文，前端滚动 100 章的大列表也不爆
- L2 get: ?n=3 才拉该章正文（真实文件内容）
  → 编辑器打开时才读
- L3 full: 批量全文读取（默认禁用 / 需显式 ?full=true + 确认）
  → 内部调用方专用（如连续性追踪），前端禁止直接拉全书

### 绑定三字段（每条索引）

- chapter_no（章号）
- stage（阶段号 1-8）
- file_sha256（文件内容指纹）

### 与既有端点的关系

- /outline/chapters/list（L1）：现已有 `size` 字段但无 stage/stage_status/sha256
  → 本模块提供增强版索引文件，域模块可选消费
- /outline/chapters/get（L2）：现已有（读正文）
  → 三级读取协议的 L2 就是它，此处只补协议纪律（L1 不带正文 = 现已经满足）
- /chapters/full（L3）：新增，需显式 full=true

### 为什么不只改域模块的 list 端点

域模块做的是**在线直读**（每次调实时读磁盘），快但每次都是全量 I/O。
索引是**周期性物化快照**（build 一次、多次查询），给蓝图/连续性
追踪/进度可视化等需要"一眼扫全书"的场景用。两者互补，不替换。
"""
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

# 自举：打包态 sys.path 不含 scripts/，本地 import 会崩
sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.file_io import read_text, write_text
from utils.config_io import load_config_yaml
# 阶段可读名单一真源在 utils.stage_artifacts（自由模式的报缺语也要用同一份，
# 两处各写一份必然漂移）—— 这里转一手，供本模块 CLI 显示与外部 `from
# chapter_handoff import STAGE_NAMES` 两种用法共用。
from utils.stage_artifacts import STAGE_NAMES

INDEX_PATH = "data/state/chapter_handoff.json"


def _sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def _progress_stage_status(progress, stage_no, chapter_no):
    """从 progress.json 读取某章在某阶段的状态。"""
    if not progress:
        return "unknown"
    st = progress.get("stages", {}).get(str(stage_no), {})
    if st.get("status") == "done":
        # 进一步判该章是否完成（stage4+ 才有逐章）
        if stage_no >= 4:
            done_chapters = st.get("completed_chapters", [])
            if chapter_no in done_chapters:
                return "done"
            return "pending"
        return "done"
    return st.get("status", "unknown")


def build_index(progress=None, proj=None, index_path=None, root=None):
    """构建/重建 handoff 索引。返回索引 dict。

    `progress` 为 None 时自动读 `data/state/progress.json`。
    `proj` 为 None 时自动读 `config/project.yaml`。
    `root` 为 None 时默认当前目录（CWD）。
    """
    root = Path(root) if root else Path(".")
    if progress is None:
        pp = root / "data" / "state" / "progress.json"
        try:
            progress = json.loads(read_text(pp)) if pp.exists() else {}
        except Exception:
            progress = {}
    if proj is None:
        try:
            proj = load_config_yaml(str(root / "config" / "project.yaml")) or {}
        except Exception:
            proj = {}

    chapters_dir = root / "data" / "outline" / "chapters"
    chapter_files = sorted(chapters_dir.glob("*.md")) if chapters_dir.exists() else []

    chapters = {}
    for f in chapter_files:
        n = int(f.stem)
        content = read_text(f) if f.exists() else ""
        title = ""
        if content:
            first_line = content.split("\n")[0]
            title = first_line.lstrip("#").strip()
        chapters[f.stem] = {
            "n": n,
            "title": title or f"第{n}章",
            "file": f.name,
            # 索引只记录"当前阶段" = 最后一个把该章标 done 的 stage
            # 简化处理：取进度中最近完成阶段（≤stage4）= stage4 状态为准
            "stage": 4,
            "stage_status": _progress_stage_status(progress, 4, n),
            "sha256": _sha256(f),
            "size": f.stat().st_size if f.exists() else 0,
            "bound_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # 也追加 raw/checked/refined 的状态信息（让索引覆盖全流程）
    # 对每个章节目录：记录各产物存在性（避免三级协议的全量读取）
    for state_dir in ("raw", "checked", "refined"):
        dir_path = root / "data" / "chapters" / state_dir
        if not dir_path.exists():
            continue
        for f in sorted(dir_path.glob("*.md")):
            key = f.stem
            if key not in chapters:
                # 章节大纲没有但正文有（异常态）—— 仍登记
                chapters[key] = {
                    "n": int(f.stem),
                    "title": f"第{f.stem}章（仅{state_dir}产物）",
                    "file": f"{state_dir}/{f.name}",
                    "stage": {"raw": 4, "checked": 5, "refined": 6}.get(state_dir, 0),
                    "stage_status": "done",
                    "sha256": _sha256(f),
                    "size": f.stat().st_size,
                    "bound_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            else:
                # 记录该产物文件存在
                chapters[key][f"{state_dir}_exists"] = True

    index = {
        "version": 1,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "work_identity": {
            "book_name": (proj.get("book") or {}).get("name", ""),
            "project_root": str(Path(".").resolve()),
            "total_chapters": len({v["n"] for v in chapters.values()}),
        },
        "chapters": chapters,
    }

    out_path = Path(index_path or INDEX_PATH)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(out_path, json.dumps(index, ensure_ascii=False, indent=1))
    return index


# ---------------------------------------------------------------- 三级读取协议


def load_index(index_path=None, root=None):
    """读取索引。损坏/缺失 → 空 dict。"""
    if index_path:
        p = Path(index_path)
    else:
        r = Path(root) if root else Path(".")
        p = r / "data" / "state" / "chapter_handoff.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(read_text(p))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def list_l1(index_path=None, root=None):
    """三级读取 L1：只返回到 stage 级，**不带正文**。

    返回:
    {
      "work_identity": {...},
      "chapters": [{"n", "title", "stage", "stage_status", "sha256", "size", "bound_at"}],
      "generated_at", "version"
    }
    """
    idx = load_index(index_path, root)
    if not idx:
        return {"work_identity": {}, "chapters": [], "generated_at": "", "version": 0}
    chapters_list = sorted(idx.get("chapters", {}).values(), key=lambda c: c.get("n", 0))
    # L1 明确不返正文字段（按协议）
    return {
        "work_identity": idx.get("work_identity", {}),
        "chapters": chapters_list,
        "generated_at": idx.get("generated_at", ""),
        "version": idx.get("version", 0),
        "total": len(chapters_list),
    }


def get_l2(n, index_path=None, root=None):
    """三级读取 L2：读取该章正文。

    先查索引定位文件路径，再读真实文件（防路径被改）。
    找不到返回空 content。
    """
    idx = load_index(index_path, root)
    if not idx:
        return None
    stem = "%02d" % n
    entry = idx.get("chapters", {}).get(stem)
    if entry is None:
        return None
    file_path = entry.get("file", "")
    if not file_path:
        return None
    r = Path(root) if root else Path(".")
    full = r / "data" / "outline" / "chapters" / Path(file_path).name
    if not full.exists():
        return None
    try:
        content = read_text(full)
    except OSError:
        return None
    return {
        "n": entry.get("n"),
        "title": entry.get("title", ""),
        "file": entry.get("file"),
        "content": content,
        "sha256": entry.get("sha256", ""),
        "size": entry.get("size", 0),
        "stage": entry.get("stage"),
        "stage_status": entry.get("stage_status"),
    }


def get_l3_full(index_path=None, root=None, confirm=False):
    """三级读取 L3：批量全文读取。

    默认禁用（confirm=False → 返回空列表）。
    需显式 confirm=True 才执行。

    调用方：连续性追踪 / 批量精修 等内部逻辑。
    前端禁止调用（前端走 L2 逐章）。
    """
    if not confirm:
        return {"error": "L3 批量全文需显式 confirm=True（前端禁止）", "chapters": []}
    idx = load_index(index_path, root)
    if not idx:
        return {"chapters": []}
    full_chapters = []
    for stem, entry in sorted(idx.get("chapters", {}).items()):
        n = int(stem)
        data = get_l2(n, index_path, root)
        if data:
            full_chapters.append(data)
    return {"chapters": full_chapters, "total": len(full_chapters)}


def validate_bindings(index_path=None, root=None):
    """校验绑定三字段（chapter_no / stage / sha256）。

    返回各条目的校验结果 + 整体 pass/fail。
    用于快照比对（丙3 before-import 快照可复用该校验）。
    """
    idx = load_index(index_path, root)
    results = []
    all_ok = True
    for stem, entry in sorted(idx.get("chapters", {}).items()):
        # chapter_no 匹配文件名
        n_ok = entry.get("n", -1) == int(stem)
        # stage 范围
        stage_ok = 1 <= entry.get("stage", 0) <= 8
        # sha256 长度
        sha = entry.get("sha256", "")
        sha_ok = len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
        ok = n_ok and stage_ok and sha_ok
        if not ok:
            all_ok = False
        results.append({
            "file": entry.get("file", stem),
            "n_ok": n_ok, "stage_ok": stage_ok, "sha_ok": sha_ok, "ok": ok,
        })
    return {"all_ok": all_ok, "total": len(results), "results": results}


# ---------------------------------------------------------------- CLI


def main():
    import argparse
    parser = argparse.ArgumentParser(description="分章 handoff 索引 + 三级读取协议（丙1+丙2）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("build", help="构建索引")
    sp.add_argument("--output", default=INDEX_PATH)

    sp = sub.add_parser("list", help="三级读取 L1（只到 stage 级，不带正文）")
    sp.add_argument("--output", default=INDEX_PATH)

    sp = sub.add_parser("get", help="三级读取 L2（单章正文）")
    sp.add_argument("n", type=int)

    sp = sub.add_parser("full", help="三级读取 L3（批量全文，需 confirm）")
    sp.add_argument("--confirm", action="store_true")

    sp = sub.add_parser("validate", help="校验绑定三字段")
    sp.add_argument("--output", default=INDEX_PATH)

    sp = sub.add_parser("stats", help="索引统计")
    sp.add_argument("--output", default=INDEX_PATH)

    args = parser.parse_args()

    if args.cmd == "build":
        idx = build_index(index_path=args.output)
        print(f"索引已构建: {idx['work_identity']['total_chapters']} 章")
        print(f"生成时间: {idx['generated_at']}")
    elif args.cmd == "list":
        data = list_l1(args.output)
        if not data["chapters"]:
            print("（索引为空，请先 build）")
            return
        for c in data["chapters"]:
            # 带上阶段名：只给 "done" 看不出是哪道工序的 done（L1 不带正文，
            # 这行是接手时唯一的概览）
            sname = STAGE_NAMES.get(c.get("stage", 0), "")
            print(f"  [{c['n']:02d}] {c.get('stage_status', '?'):8s} "
                  f"{sname:8s} {c['title']}")
    elif args.cmd == "get":
        data = get_l2(args.n, args.output)
        if data is None:
            print(f"第{args.n}章不在索引中")
            return
        print(f"第{data['n']}章 / {data['title']} / {data['size']}B")
        print("-" * 40)
        print(data["content"][:500])
    elif args.cmd == "full":
        data = get_l3_full(args.output, confirm=args.confirm)
        if "error" in data:
            print(f"ERROR: {data['error']}")
            return
        print(f"批量全文: {data['total']} 章")
    elif args.cmd == "validate":
        result = validate_bindings(args.output)
        print(f"校验: {'PASS' if result['all_ok'] else 'FAIL'} ({result['total']} 条)")
        for r in result["results"]:
            if not r["ok"]:
                print(f"  x {r['file']}: n={r['n_ok']} stage={r['stage_ok']} sha={r['sha_ok']}")
    elif args.cmd == "stats":
        idx = load_index(args.output)
        if not idx:
            print("（索引为空）")
            return
        print(f"书: {idx['work_identity']['book_name']}")
        print(f"生成时间: {idx['generated_at']}")
        print(f"章数: {idx['work_identity']['total_chapters']}")


if __name__ == "__main__":
    main()
