# -*- coding: utf-8 -*-
"""素材写侧（2026-11-17 拆壳）：保存素材正文、触发 stage1 重归并。

为什么与 `materials.py`（读侧）分家 —— 那个模块的 docstring 早就写明了：
**写路径要遵守“破坏性操作先快照 + 显式 confirm”的红线，读路径不需要**，
混在一起容易让红线被稀释。本模块就是那条路线里的写侧一半。

协议同其它域：`handle_*(h, body)` 返回 `(status, payload)`。
"""
import nf_api as api


def handle_materials_save(h, body):
    """保存素材文本内容（body: {name, content}）。"""
    name = str(body.get("name") or "").strip()
    content = body.get("content")
    if not name:
        return 400, {"ok": False, "error": "name 必填"}
    if content is None or not isinstance(content, str):
        return 400, {"ok": False, "error": "content 必须为字符串"}
    try:
        from utils.materials_manager import MaterialsManager
        mm = MaterialsManager(api.ROOT / "materials" / "raw")
        res = mm.save_content(name, content)
        res["ok"] = True
        return 200, res
    except Exception as e:                                  # noqa: BLE001
        return 400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_materials_remerge(h, body):
    """触发 stage1 重归并（清指纹 + 跑 stage1）。

    **必须先清掉指纹**：不清就轮到“素材无变化 → 复用设定集”的短路，
    按钮叫“重新归并”却什么都不做（名不副实）。
    """
    try:
        cfg, _ = api.load_all()
        try:
            import json as _json
            from utils.file_io import read_text as _rt, write_text as _wt
            mp = api.ROOT / "data" / "setting" / "materials_manifest.json"
            if mp.exists():
                _m = _json.loads(_rt(mp))
                if _m.get("_fingerprint"):
                    _m["_fingerprint"] = None
                    _wt(mp, _json.dumps(_m, ensure_ascii=False, indent=2))
        except Exception as _e:                             # noqa: BLE001
            print("[nf_api] 清指纹失败（不影响重归并）: " + str(_e))
        jid, err = api.start_job("remerge", api.act_run_stage(cfg, 1, 1))
        if err:
            return 409, {"error": err}
        return 202, {"job_id": jid, "message": "已触发素材重归并（阶段1）"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("POST", "/materials/save", handle_materials_save),
    ("POST", "/materials/remerge", handle_materials_remerge),
)
