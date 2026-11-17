# -*- coding: utf-8 -*-
"""碎片写侧（2026-11-17 拆壳）：保存 / 删除 / 勾选信息点落成素材卡。

三条红线都在这里，所以单独成模块（读侧在 `scraps_write` 的同胞 `materials.py`）：

- **保存**：覆盖写前自动备份到 `materials/original_scraps/_backup/`；
- **删除**：破坏性操作 → 必须 `confirm=true`，且先 `snapshot` 再 `_backup_copy` 才 unlink；
  快照失败**不吞**（回执带 `snapshot_error`），但也**不假装删成功**；
- **提升**：勾选的信息点由 Python 落盘成 `materials/raw/<名>_<类型>.md`
  —— 模型写入白名单只有 `data/**`，所以这一步绝不能交给模型。

文件名一律过 `_safe_member`（禁路径穿越）。
"""
from pathlib import Path

import nf_api as api


def handle_scraps_save(h, body):
    """保存碎片正文（写前自动备份到 materials/original_scraps/_backup/）。"""
    name = str(body.get("name") or "").strip()
    content = body.get("content")
    okn, err = api._safe_member(name)
    if not okn:
        return 400, {"ok": False, "error": err}
    if not isinstance(content, str):
        return 400, {"ok": False, "error": "content 必须为字符串"}
    try:
        d = api.scraps_dir_path()
        target = d / name
        if not target.is_file():
            return 404, {"ok": False, "error": "碎片不存在: " + name}
        bak = api._backup_copy(target, d / "_backup")
        target.write_text(content, encoding="utf-8")
        return 200, {"ok": True, "name": name,
                     "size": target.stat().st_size, "backup": bak}
    except Exception as e:                                  # noqa: BLE001
        return 400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_scraps_delete(h, body):
    """删除碎片：破坏性操作 → 必须 confirm + 先快照。"""
    name = str(body.get("name") or "").strip()
    if not body.get("confirm"):
        return 400, {"ok": False,
                     "error": "删除碎片属破坏性操作，需传 confirm=true"}
    okn, err = api._safe_member(name)
    if not okn:
        return 400, {"ok": False, "error": err}
    try:
        d = api.scraps_dir_path()
        target = d / name
        if not target.is_file():
            return 404, {"ok": False, "error": "碎片不存在: " + name}
        snap_label = "before_scrap_delete_" + Path(name).stem[:20]
        snap_err = None
        try:
            api.snap_mod.snapshot(snap_label)   # 返回快照目录路径
        except Exception as e:                  # noqa: BLE001
            snap_err = str(e)[:200]
        bak = api._backup_copy(target, d / "_backup")
        target.unlink()
        return 200, {"ok": True, "name": name, "backup": bak,
                     "snapshot_error": snap_err}
    except Exception as e:                                  # noqa: BLE001
        return 400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_scraps_promote(h, body):
    """勾选信息点 → 落盘成 materials/raw/<名>_<类型>.md（覆盖前自动备份）。"""
    card_name = str(body.get("card_name") or "").strip()
    card_type = str(body.get("card_type") or "").strip()
    points = body.get("points") or []
    if not card_name:
        return 400, {"ok": False, "error": "card_name 必填"}
    if not isinstance(points, list) or not points:
        return 400, {"ok": False, "error": "points 必须是非空数组"}
    try:
        _, proj = api.load_all()
        path, bak = api.promote_to_card(
            card_name, card_type, points,
            open_questions=body.get("open_questions") or [],
            sources=body.get("sources") or [],
            book_name=(proj.get("book") or {}).get("name", ""),
            overwrite=body.get("overwrite", True))
        return 200, {"ok": True, "path": path, "backup": bak,
                     "hint": "已写入素材卡；下次跑 stage1 时进入设定集归并"}
    except Exception as e:                                  # noqa: BLE001
        return 400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("POST", "/scraps/save", handle_scraps_save),
    ("POST", "/scraps/delete", handle_scraps_delete),
    ("POST", "/scraps/promote", handle_scraps_promote),
)
