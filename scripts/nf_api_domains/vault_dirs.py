from pathlib import Path
def handle_config_vault_dirs(h):
    """`GET /config/vault_dirs` —— 设定目录映射的**候选清单**（产品通用化）。

    为什么不是让用户填五个字符串：每个人的库布局不同（`设定/人物`、`03 设定/01 人物`、
    `World/Characters`…），把某一种当通例写进代码/文档，换个人就废。
    所以这里只做两件事：**把库里真实存在的目录列出来**（带 md 篇数），
    让用户在界面上点选；代码里一个具体目录名都不出现。
    """
    import nf_api as api
    import os as _os

    keys = ["characters", "locations", "factions", "concepts", "timeline"]
    try:
        from utils.config_io import load_config_yaml
        cfg = load_config_yaml(api.ROOT / "config" / "system.local.yaml", default={}) or {}
        base = (cfg.get("obsidian") or {}).get("vault_dirs") or {}
        if not base:
            cfg2 = load_config_yaml(api.ROOT / "config" / "system.yaml", default={}) or {}
            base = (cfg2.get("obsidian") or {}).get("vault_dirs") or {}
    except Exception:                                        # noqa: BLE001
        base = {}

    vault = ""
    try:
        from obsidian_bridge import _get_vault_path
        vault = str(_get_vault_path() or "")
    except Exception:                                        # noqa: BLE001
        vault = ""

    candidates = []
    if vault and Path(vault).is_dir():
        root = Path(vault)
        for dirpath, dirnames, filenames in _os.walk(vault):
            dirnames[:] = [d for d in dirnames if d not in
                           (".git", ".obsidian", "node_modules", ".trash", "attachments")]
            rel = Path(dirpath).relative_to(root)
            depth = 0 if str(rel) == "." else len(rel.parts)
            if depth > 2:                                    # 只看到两层：够用且不刷屏
                dirnames[:] = []
                continue
            n = sum(1 for f in filenames if f.lower().endswith((".md", ".markdown")))
            if n:
                candidates.append({"path": "" if str(rel) == "." else rel.as_posix(),
                                   "level": depth, "md": n})
        # 目录自身的 md 可能为 0，但子目录有 —— 补一层父目录
        for c in list(candidates):
            if c["level"] == 1:
                parent = c["path"].split("/")[0]
                if not any(x["path"] == parent for x in candidates):
                    candidates.append({"path": parent, "level": 0, "md": 0})
    candidates.sort(key=lambda x: (x["level"], x["path"]))
    return 200, {"ok": True, "vault_path": vault, "keys": keys,
                 "current": {k: (base.get(k) or "") for k in keys},
                 "candidates": candidates[:200],
                 "note": ("目录名全部取自你自己的库；这里只列目录与篇数，不猜哪个是“人物”。"
                          "选完保存，写进 config/system.local.yaml（不进 Git）。"
                          if candidates else
                          "库路径没有配置或库里没有 md —— 先在“知识库”里填库路径。")}


def handle_config_vault_dirs_save(h, body):
    """`POST /config/vault_dirs {characters, locations, ...}` —— 保存用户点选的映射。

    校验：相对路径、不许 `..`、不许绝对路径、目录必须真的存在（否则保存了也读不到，
    等于给用户一个"看起来配好了"的假象）。空字符串 = 不映射（合法）。
    """
    import nf_api as api

    keys = ["characters", "locations", "factions", "concepts", "timeline"]
    body = body if isinstance(body, dict) else {}
    try:
        from obsidian_bridge import _get_vault_path
        vault = str(_get_vault_path() or "")
    except Exception:                                        # noqa: BLE001
        vault = ""
    if not vault:
        return 400, {"ok": False, "error": "还没配置知识库路径"}

    values, bad = {}, []
    for k in keys:
        v = str(body.get(k) or "").strip().replace("\\", "/").strip("/")
        if not v:
            values[k] = ""
            continue
        if v.startswith("/") or ":" in v or ".." in v.split("/"):
            bad.append("%s=%r（只收库内的相对路径）" % (k, v))
            continue
        if not (Path(vault) / v).is_dir():
            bad.append("%s=%r（目录不存在）" % (k, v))
            continue
        values[k] = v
    if bad:
        return 400, {"ok": False, "error": "；".join(bad)}

    try:
        from utils.config_io import set_section_scalars
        ok, msg = set_section_scalars(("obsidian", "vault_dirs"), values,
                                      path=api.ROOT / "config" / "system.local.yaml")
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if not ok:
        return 500, {"ok": False, "error": msg}
    return 200, {"ok": True, "saved": values, "message": msg,
                 "hint": "已写入 config/system.local.yaml（不进 Git）；下次扫描/生成生效。"}
