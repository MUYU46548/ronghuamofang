# -*- coding: utf-8 -*-
"""出厂清单与「出厂样例」检测/清理（0.6.7 出厂数据与用户数据分治）。

## 为什么要有这套东西

`examples/sample-book/` 是仓库自带的示例书（雾港核心 + 19 张素材卡）。历史上它被
试跑**手工搬进真实用户工作区**（落在 `materials/raw/` 里），与用户自己写的素材卡
**没有任何标记可分辨** —— 用户分不清哪张卡是出厂的、哪张是自己攒的，也不敢删
（删了怕丢自己的东西，留着又污染素材归并）。

本模块把「出厂内容」与「用户数据」分治：

  · **清单**（`data/state/factory_manifest.json`）：本次播种**实际复制**进工作区的
    文件 + sha256，由 `console/main/index.js` 的 seedWorkspace 写入 —— 「哪些文件
    是机器发的」从此有账可查。
  · **检测**（`detect_sample_matches`）：拿样例随包目录（`examples/`）逐文件算 sha256，
    与工作区 `materials/` 逐字节比对 —— **内容完全一致**才算出厂样例。
    用户改过一个字的卡片不会被命中（那已经变成用户数据）。
  · **清理**（`clean_factory`）：命中的文件**移**进回收站
    `data/books/_trash/factory__<时间戳>/`（保留原相对路径），不是删除 —— 可人工捞回。

## 三条硬边界

1. **只动 `materials/` 开头的相对路径**：`clean_factory` 对越界路径直接抛错，
   不「尽力而为」。用户数据（`data/`、`config/`、`progress.json`）与本功能无关。
2. **逐字节相等才动手**：sha256 不一致 = 用户改过 = 不是出厂内容，永不命中。
3. **幂等**：文件已被移走（源不存在）时静默跳过，重复调用不报错、不重复计数。

路径一律用**相对 POSIX 风格字符串**（`materials/raw/潮神_角色卡.md`），
Windows 反斜杠会在入口归一 —— 否则同一份清单在两个平台读出两套 key。
"""
import hashlib
import json
import shutil
import time
from pathlib import Path

# 清单落点（相对工作区根）
MANIFEST_REL = "data/state/factory_manifest.json"
# 允许被清理的路径前缀（硬边界）
MATERIALS_PREFIX = "materials/"
# 归档回收站（排除在检测范围外：那里是「已归档的旧书」，不是当前工作区素材）
_TRASH_PREFIX = "data/books/_trash/"

_CHUNK = 65536


def _posix(rel):
    """把任意相对路径归一成 POSIX 风格字符串（去前导 ./、统一斜杠）。"""
    s = str(rel).replace("\\", "/").strip()
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def sha256_file(path):
    """逐块算文件 sha256（大文件不一次性读进内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def build_seed_records(root, rel_paths):
    """给 `root` 下的一批相对路径算 sha256 → `[{path, sha256}]`。

    路径不存在的条目**跳过**（播种是尽力而为的记录，不该因为一个文件读不到
    就把整份清单写失败）。
    """
    root = Path(root)
    out = []
    for rel in rel_paths:
        p = root / _posix(rel)
        try:
            if not p.is_file():
                continue
            out.append({"path": _posix(rel), "sha256": sha256_file(p)})
        except OSError:
            continue
    return out


def read_manifest(workspace):
    """读清单。缺失 / 损坏 / 结构不对一律返回 `{}`（读侧绝不抛）。"""
    p = Path(workspace) / MANIFEST_REL
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if not isinstance(data.get("files"), dict):
        data["files"] = {}
    return data


def write_manifest(workspace, files, seed_version=None):
    """**合并**写清单：已有记录保留，新复制的追加/覆盖。

    `files` 形如 `{相对路径: sha256}` 或 `[{"path":..., "sha256":...}, ...]`。
    `seed_version` 取新旧较大者（回滚 SEED_VERSION 不该让清单倒退）。
    返回合并后的完整清单（便于调用方直接回读断言）。
    """
    workspace = Path(workspace)
    existing = read_manifest(workspace)
    merged = dict(existing.get("files") or {})
    if isinstance(files, dict):
        items = [(_posix(k), v) for k, v in files.items()]
    else:
        items = [(_posix(r.get("path", "")), r.get("sha256", "")) for r in (files or [])]
    for rel, sha in items:
        if not rel or not sha:
            continue
        merged[rel] = str(sha)

    old_ver = existing.get("seed_version")
    ver = int(old_ver) if isinstance(old_ver, int) else 0
    if seed_version is not None:
        ver = max(ver, int(seed_version))

    manifest = {
        "seed_version": ver,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "files": merged,
    }
    target = workspace / MANIFEST_REL
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(target)
    except OSError as e:
        raise OSError("写入出厂清单失败 %s: %s" % (target, e))
    return manifest


def _sha_index(sample_root):
    """walk 样例目录 → `{sha256: 样例内相对路径}`（sha 相同先到先得）。"""
    index = {}
    sample_root = Path(sample_root)
    try:
        entries = sorted(sample_root.rglob("*"))
    except OSError:
        return index
    for p in entries:
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        try:
            sha = sha256_file(p)
        except OSError:
            continue
        index.setdefault(sha, _posix(p.relative_to(sample_root)))
    return index


def detect_sample_matches(workspace, sample_root):
    """在 `workspace/materials/` 里找与样例逐字节一致的文件。

    范围说明：
      · 只扫 `materials/`（含 `raw/`、`original_scraps/`、`_backup/` 等全部子目录）
        —— 与 `clean_factory` 的硬边界（只准动 `materials/` 开头）对齐：
        检测出来的东西必须**一定清得动**，否则用户会收到一半成功一半报错。
      · `data/books/_trash/` 与其下归档天然不在范围内（防御性跳过，见下）；
        已归档旧书里的样例卡属于「归档数据」，要清得先恢复那本书，
        不该由本功能顺手搬动。

    返回按 `path` 排序的 `[{path, sample_rel, sha256}]`。样例目录缺失 → `[]`。
    """
    workspace = Path(workspace)
    if not sample_root:
        return []
    sample_root = Path(sample_root)
    if not sample_root.is_dir():
        return []

    index = _sha_index(sample_root)
    if not index:
        return []

    materials = workspace / "materials"
    if not materials.is_dir():
        return []

    hits = []
    for p in sorted(materials.rglob("*")):
        if not p.is_file():
            continue
        rel = _posix(p.relative_to(workspace))
        if rel.startswith(_TRASH_PREFIX):          # 防御：回收站绝不参与
            continue
        if not rel.startswith(MATERIALS_PREFIX):   # 兜底：出界一律不收
            continue
        try:
            sha = sha256_file(p)
        except OSError:
            continue
        sample_rel = index.get(sha)
        if sample_rel:
            hits.append({"path": rel, "sample_rel": sample_rel, "sha256": sha})
    hits.sort(key=lambda r: r["path"])
    return hits


def clean_factory(workspace, matches):
    """把命中的出厂样例移进回收站（保留原相对路径）。

    返回 `{"moved": [相对路径...], "trash_dir": <绝对路径字符串>}`。

    硬边界：`matches` 里出现任何**不以 `materials/` 开头**的相对路径，
    直接抛 `ValueError`（一个都不动）—— 这不是「跳过不合规项」，
    而是「调用方传了越界输入就该立刻炸」，否则半清理状态最难排查。

    幂等：源文件已被移走（不存在）时跳过，不计入 `moved`、不报错。
    `progress.json` 与未匹配文件一概不碰（本函数只处理传进来的命中列表）。
    """
    workspace = Path(workspace)
    rels = []
    for m in matches or []:
        rel = _posix(m.get("path", "") if isinstance(m, dict) else m)
        if not rel:
            continue
        parts = rel.split("/")
        if ".." in parts or not rel.startswith(MATERIALS_PREFIX):
            raise ValueError(
                "拒绝移动 materials/ 之外的路径（出厂清理只允许动素材目录）: " + rel)
        rels.append(rel)

    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    trash_dir = workspace / "data" / "books" / "_trash" / ("factory__" + ts)
    moved = []
    for rel in rels:
        src = workspace / rel
        if not src.exists():
            continue                      # 幂等：已移走
        dst = trash_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(rel)
    return {"moved": moved, "trash_dir": str(trash_dir)}
