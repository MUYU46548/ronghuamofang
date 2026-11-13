# -*- coding: utf-8 -*-
"""素材 / 碎片 / 设定域（GET 读侧）：素材列表与读取、碎片列表与读取、设定读取。

## 边界

本模块只承载**读**端点（`do_GET` 侧）。同名路径的写端点（`/materials/save`、
`/scraps/delete`、`/setting/save` 等）留在 `do_POST`，其实现后续迁往
`materials_write.py` / `scraps_write.py`。读写分家是有意的：
写路径要遵守“破坏性操作先快照 + 显式 confirm”的红线，读路径不需要，
混在一个模块里容易让红线被稀释。

## 碎片目录

碎片目录由 `config/project.yaml` 的 `materials.scraps_dir` 决定
（默认 `materials/original_scraps`），经 `api.scraps_dir_path()` 解析。
自由命名是刻意的设计：用户随手写下的碎片不该被强制改名。

## 关于 /scraps/list 的“索引陈旧”标记

`data/setting/scraps_index.json` 里的 `content_fingerprint` 是**内容指纹**。
指纹不一致 → `index_stale: true`，前端提示“碎片有改动，建议重跑 stage1”。
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


def handle_materials_reconcile(h):
    """索引与实物对账（GET /materials/reconcile，零 token，只读）。

    为什么放读侧：它只回答“登记表说的和盘上有的差多少”，不修任何东西。
    管线的真源一直是文件系统（stage1 扫盘 + `utils.meta_detect` 排元文件），
    这个端点只是把“元文件会骗人”这件事摆到作者面前。

    返回 `verdict` ∈ consistent | mismatch | no_meta；前端据此显示横幅。
    """
    try:
        from utils.index_reconcile import reconcile
        scraps = api.scraps_dir_path()
        scraps_rel = str(scraps)
        try:
            scraps_rel = str(scraps.relative_to(api.ROOT)).replace("\\", "/")
        except ValueError:
            pass
        return 200, reconcile(api.ROOT, [("raw", "materials/raw"),
                                         ("scraps", scraps_rel)])
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


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
    """读取设定集 data/setting/setting.json。

    附带 `mtime`（2026-11-13，问题总账第 7 条同族）：界面保存时把它原样带回来，
    盘上变了就拒收。界面持的是**打开那一刻**的副本，中间 CLI / 外部 Agent /
    另一个窗口改过盘时，整份提交会把别人的改动**静默覆盖**（设定集尤其伤）。
    """
    try:
        setting_path = api.ROOT / "data" / "setting" / "setting.json"
        if setting_path.exists():
            return 200, {"ok": True,
                         "mtime": setting_path.stat().st_mtime,
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


def handle_materials_move(h, body):
    """POST /materials/move {names:[...], to:"cards"|"scraps"} → 批量移动（可撤回）。

    2026-11-05（试跑第 6 条）：作者把素材误拖进“结构化素材”后无法撤回。
    这里一次给三件：批量、目标明确、可撤回（日志见 utils.materials_ops）。
    """
    import nf_api as api
    try:
        from utils import materials_ops as ops
        names = body.get("names") or []
        if not isinstance(names, list) or not names:
            return 400, {"ok": False, "error": "names 必填（文件名数组）"}
        to = str(body.get("to") or "").strip()
        ok, res = ops.move_many(api.ROOT, names, to)
        if not ok:
            return 400, {"ok": False, **res}
        res["ok"] = True
        return 200, res
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_materials_delete(h, body):
    """POST /materials/delete {name} 或 {names:[...], confirm:true} → 删除（进回收站，可撤回）。

    2026-11-05（试跑第 6 条）：删除改成**可撤回** —— 文件搬进
    `data/state/materials_trash/<ts>/`，`/materials/undo` 能搬回来。
    · 单条（`name`）沿用老形状，界面上的“删除”按钮不用带 confirm；
    · 批量（`names` 且 >1）必须 `confirm: true`（界面二次确认后带）。
    """
    import nf_api as api
    try:
        from utils import materials_ops as ops
        if isinstance(body.get("names"), list) and body["names"]:
            names = body["names"]
        elif body.get("name"):
            names = [body["name"]]
        else:
            return 400, {"ok": False, "error": "name 或 names 必填"}
        if len(names) > 1 and not body.get("confirm"):
            return 400, {"ok": False,
                         "error": "批量删除需要 confirm=true（界面二次确认后带上）"}
        ok, res = ops.delete_many(api.ROOT, names)
        res["ok"] = bool(ok)
        return (200 if ok else 400), res
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_materials_undo_get(h):
    """GET /materials/undo → 可撤回的操作清单（新的在前，最多 20 条）。"""
    import nf_api as api
    try:
        from utils import materials_ops as ops
        return 200, {"ok": True, "entries": ops.journal(api.ROOT)}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_materials_undo(h, body):
    """POST /materials/undo → 撤回最近一步（移动搬回原目录 / 删除从回收站搬回）。"""
    import nf_api as api
    try:
        from utils import materials_ops as ops
        ok, res = ops.undo_last(api.ROOT)
        res["ok"] = bool(ok)
        return (200 if ok else 400), res
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
def handle_materials_import_tavern(h, body):
    """导入酒馆角色卡 / 世界书（竞品三路之二，2026-11-07）。

    `{paths:[...]}` 或 `{path:"..."}`；`dry_run` 只报告不落盘。
    **只写 materials/raw**：导入是“把料放进来”，归并仍走阶段 1 与作者审批。
    同名素材卡默认不覆盖（素材是作者的数据）——要覆盖必须显式 `overwrite:true`。
    """
    try:
        body = body or {}
        paths = body.get("paths") or ([body.get("path")] if body.get("path") else [])
        paths = [str(x) for x in paths if str(x or "").strip()]
        if not paths:
            return 400, {"ok": False, "error": "paths 必填（角色卡 .png/.json 或世界书 .json）"}
        from utils import tavern_import as TI
        out = api.ROOT / "materials" / "raw"
        results = [TI.import_file(p, out_dir=out, dry_run=bool(body.get("dry_run")),
                                  overwrite=bool(body.get("overwrite")))
                   for p in paths]
        ok = all(r.get("ok") for r in results)
        return (200 if ok else 400), {"ok": ok, "results": results,
                                      "written": sum(len(r.get("written") or []) for r in results),
                                      "skipped": sum(len(r.get("skipped") or []) for r in results)}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/materials/list", handle_materials_list),
    ("POST", "/materials/import_tavern", handle_materials_import_tavern),
    ("POST", "/materials/move", handle_materials_move),
    ("POST", "/materials/delete", handle_materials_delete),
    ("GET", "/materials/undo", handle_materials_undo_get),
    ("POST", "/materials/undo", handle_materials_undo),
    ("GET", "/materials/reconcile", handle_materials_reconcile),
    ("GET", "/materials/read/{name}", handle_materials_read),
    ("GET", "/scraps/list", handle_scraps_list),
    ("GET", "/scraps/read", handle_scraps_read),
    ("GET", "/setting/current", handle_setting_current),
    ("GET", "/setting/appearances", handle_setting_appearances),
)
