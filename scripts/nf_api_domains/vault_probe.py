# -*- coding: utf-8 -*-
"""`/config/vault_probe`：给 AI 的扫描证据包（2026-11-03）。

只读盘、零 token：返回每个目录的 md 篇数、样本文件名、样本首行。
AI 据此给提案（哪类对应哪个目录），**保存仍由作者在设置页做**。
"""
from pathlib import Path


def handle_config_vault_probe(h):
    """`GET /config/vault_probe` —— 给 AI 的**证据包**，让它判断哪个目录算什么。

    为什么要有它（2026-11-03 作者的追问："扫描本身不应该由 AI 介入吗？"）：
    目录名千奇百怪，靠人一个个点选是苦力，靠代码猜是碰运气。正确分工是
    **AI 读证据给提案、作者拍板**。这里只提供证据，不给结论：

      · 每个目录：md 篇数、若干**样本文件名**、若干**样本首行**（截断）
      · 当前映射（可能是空的）
      · 跳过集合（这些不参与）

    零 token（只读盘）。AI 拿着这份证据用 `nf_ask` 或直接在对话里给提案，
    作者在设置页保存 —— 保存那一步仍然是人。
    """
    import nf_api as api
    import os as _os

    keys = ["characters", "locations", "factions", "concepts", "timeline"]
    try:
        from obsidian_bridge import _get_vault_path, get_skip_dirs
        vault = str(_get_vault_path() or "")
        skip = set(get_skip_dirs() or [])
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if not vault or not Path(vault).is_dir():
        return 200, {"ok": True, "vault_path": vault, "dirs": [], "keys": keys,
                     "note": "库路径没配或不存在 —— 先让作者在“知识库”里填路径。"}

    try:
        from utils.config_io import load_config_yaml
        local = load_config_yaml(api.ROOT / "config" / "system.local.yaml", default={}) or {}
        current = ((local.get("obsidian") or {}).get("vault_dirs") or {})
    except Exception:                                        # noqa: BLE001
        current = {}

    root = Path(vault)
    dirs = []
    for dirpath, dirnames, filenames in _os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        rel = Path(dirpath).relative_to(root)
        depth = 0 if str(rel) == "." else len(rel.parts)
        if depth > 2:
            dirnames[:] = []
            continue
        mds = sorted(f for f in filenames if f.lower().endswith((".md", ".markdown")))
        if not mds:
            continue
        samples, heads = [], []
        for f in mds[:6]:
            samples.append(f)
            if len(heads) < 3:
                try:
                    t = (Path(dirpath) / f).read_text(encoding="utf-8", errors="replace")
                    for line in t.splitlines():
                        s = line.strip().lstrip("#->* ").strip()
                        if s and not s.startswith("---") and len(s) > 3:
                            heads.append(s[:60])
                            break
                except OSError:
                    pass
        dirs.append({"path": "" if str(rel) == "." else rel.as_posix(),
                     "level": depth, "md": len(mds),
                     "samples": samples, "heads": heads})
    dirs.sort(key=lambda x: (-x["md"], x["path"]))

    return 200, {"ok": True, "vault_path": vault, "keys": keys,
                 "current": {k: (current.get(k) or "") for k in keys},
                 "skip": sorted(skip), "dirs": dirs[:120],
                 "ask": ("请只依据上面证据给一份**提案**：%s 各对应哪个目录（没有就留空）。"
                         "说明依据（样本文件名/首行），不要猜；提案交作者确认，"
                         "你不能自己保存。" % "/".join(keys))}
