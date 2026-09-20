# -*- coding: utf-8 -*-
"""素材 / 碎片 / 设定域（GET 读侧）：素材列表与读取、碎片列表与读取、设定读取。

## 边界

本模块只承载**读**端点（`do_GET` 侧）。同名路径的写端点（`/materials/save`、
`/scraps/delete`、`/setting/save` 等）留在 `do_POST`，其实现后续迁往
`materials_write.py` / `scraps_write.py`。读写分家是有意的：
写路径要遵守「破坏性操作先快照 + 显式 confirm」的红线，读路径不需要，
混在一个模块里容易让红线被稀释。

## 碎片目录

碎片目录由 `config/project.yaml` 的 `materials.scraps_dir` 决定
（默认 `materials/original_scraps`），经 `api.scraps_dir_path()` 解析。
自由命名是刻意的设计：用户随手写下的碎片不该被强制改名。

## 关于 /scraps/list 的「索引陈旧」标记

`data/setting/scraps_index.json` 里的 `content_fingerprint` 是**内容指纹**。
指纹不一致 → `index_stale: true`，前端提示「碎片有改动，建议重跑 stage1」。
这是确定性计算，零 LLM 调用。
"""
import nf_api as api


def handle_materials_list(h):
    """素材列表（materials/raw/*）。"""
    try:
        from utils.materials_manager import MaterialsManager
        mm = MaterialsManager(api.ROOT / "materials" / "raw")
        return 200, mm.list_materials()
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_materials_read(h, name):
    """读取单个素材正文（名字经 unquote 解析，`MaterialsManager` 内部防穿越）。"""
    try:
        from utils.materials_manager import MaterialsManager
        mm = MaterialsManager(api.ROOT / "materials" / "raw")
        content = mm.read_content(name)
        return 200, {"ok": True, "name": name, "content": content}
    except Exception as e:                                  # noqa: BLE001
        return 400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_scraps_list(h):
    """原始碎片列表：按簇分组 + 时间轴 + 前瞻备忘（确定性，零 LLM）。

    `index_stale` 的语义：`data/setting/scraps_index.json` 记录的内容指纹
    与当前碎片目录不一致 → 说明碎片有改动但还没重跑 stage1。
    """
    try:
        from utils.scrap_cluster import collect as _scollect
        sdir = api.scraps_dir_path()
        index, warnings = _scollect(str(sdir))
        stale = True
        idx = api.ROOT / "data" / "setting" / "scraps_index.json"
        if idx.exists():
            try:
                stale = (api.json.loads(idx.read_text(encoding="utf-8"))
                         .get("content_fingerprint")
                         != index["content_fingerprint"])
            except Exception:                               # noqa: BLE001
                stale = True
        rel = str(sdir.relative_to(api.ROOT)).replace("\\", "/") \
            if str(sdir).startswith(str(api.ROOT)) else str(sdir)
        return 200, {
            "ok": True,
            "dir": rel,
            "count": index["stats"]["scrap_count"],
            "stats": index["stats"],
            "clusters": index["clusters"],
            "timeline": index["timeline"],
            "lookaheads": index["lookaheads"],
            "warnings": warnings + index["warnings"],
            "index_stale": stale,
            "index_path": "data/setting/scraps_index.json",
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_scraps_read(h):
    """读取单个碎片正文（?name=）。"""
    name = (h._query().get("name") or [""])[0]
    try:
        from utils.materials_manager import MaterialsManager
        mm = MaterialsManager(api.scraps_dir_path())
        return 200, {"ok": True, "name": name, "content": mm.read_content(name)}
    except Exception as e:                                  # noqa: BLE001
        return 400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_setting_current(h):
    """读取设定集 data/setting/setting.json。"""
    try:
        setting_path = api.ROOT / "data" / "setting" / "setting.json"
        if setting_path.exists():
            return 200, {"ok": True,
                         "setting": api.json.loads(setting_path.read_text(encoding="utf-8"))}
        return 404, {"error": "setting.json 不存在"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_setting_appearances(h):
    """角色出场统计（只读）：供关系图定节点大小/描边。

    附带 `alias` 表（data/setting/alias.json）—— 前端把别名归并到主名的
    判定不能各写一份，否则同一角色在图上会裂成两个节点。
    """
    try:
        ap = api.ROOT / "data" / "state" / "appearances.json"
        if not ap.exists():
            return 200, {"ok": False,
                         "hint": "先跑 python scripts/appearances.py"
                                 "（或 POST /appearances/refresh）"}
        data = api.json.loads(ap.read_text(encoding="utf-8"))
        alias = {}
        alias_path = api.ROOT / "data" / "setting" / "alias.json"
        if alias_path.exists():
            try:
                alias = api.json.loads(alias_path.read_text(encoding="utf-8"))
            except Exception:                               # noqa: BLE001
                alias = {}
        return 200, {"ok": True, "alias": alias, **data}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/materials/list", handle_materials_list),
    ("GET", "/materials/read/{name}", handle_materials_read),
    ("GET", "/scraps/list", handle_scraps_list),
    ("GET", "/scraps/read", handle_scraps_read),
    ("GET", "/setting/current", handle_setting_current),
    ("GET", "/setting/appearances", handle_setting_appearances),
)
