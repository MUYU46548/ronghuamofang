# -*- coding: utf-8 -*-
"""DSH 诊断（2026-11-26）：墨坊侧回答"DSH 基座 / profile / 插件"三端是否对得上。

## 为什么放在 nf_api，而不是 Electron 主进程

墨坊界面的数据全走本机 HTTP（`guiApi.js` / `App.vue#api`）。放在这里与既有面板
同一条路，零 IPC 改造，而且能直接单测。它**只读文件**：app.asar、profile 的
package.json、插件的 package.json —— 不起进程、不调模型、不写盘。

## 三端

1. **DSH 基座**：`%LOCALAPPDATA%\\Programs\\DeepSeek Harness\\resources\\app.asar`
   里的 `package.json.version`。**不是** DSH 安装目录下的 `version` 文件 —— 那是
   Electron 版本（2026-11-21 方寸侧踩过，拿它比兼容表只会得出假结论）。
2. **profile**：`~/.dsh/profiles/<name>/package.json` 的
   `dsh.profile.bundles` 与 `dependencies`。
3. **插件**：`dsh-novelforge` 的安装版本 / 是否进 bundles / `dsh.compatibility` 表。

判据沿用方寸的三态：ok / unknown / warn / bad。**只报告，不改配置。**
"""
from __future__ import annotations

import json
import os
import struct
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_NAME = "dsh-novelforge"

#: 本机 DSH 的常见安装形态（桌面版 NSIS 默认路径）
DSH_ASAR_REL = Path("Programs") / "DeepSeek Harness" / "resources" / "app.asar"


def _env(name):
    return str(os.environ.get(name) or "").strip()


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                        # noqa: BLE001
        return None


def _read_asar_package(asar_path):
    """读 asar 里的 package.json。

    asar 头格式：offset 0/4/8 各一个 u32，offset 12 = 头 JSON 长度，JSON 从 16 开始；
    文件数据起点按 4 字节对齐。**不要先 is_file()**：Electron 对 app.asar 的 stat
    有补丁（会当目录），预检在打包态恒为假。直接 open，坏路径自然返回 None。
    """
    try:
        with open(asar_path, "rb") as f:
            head = f.read(16)
            if len(head) < 16:
                return None
            header_len = struct.unpack("<I", head[12:16])[0]
            if not header_len or header_len > 64 * 1024 * 1024:
                return None
            raw = f.read(header_len)
            if len(raw) < header_len:
                return None
            header = json.loads(raw.decode("utf-8"))
            entry = (header.get("files") or {}).get("package.json") or {}
            size = int(entry.get("size") or 0)
            offset = int(entry.get("offset") or 0)      # 大数在 JSON 里是字符串
            if not size:
                return None
            base = 16 + ((header_len + 3) // 4) * 4
            f.seek(base + offset)
            data = f.read(size)
            if len(data) < size:
                return None
            return json.loads(data.decode("utf-8"))
    except Exception:                                        # noqa: BLE001
        return None


def _localappdata():
    return Path(_env("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def _dsh_asar_candidates():
    la = _localappdata()
    return [
        la / DSH_ASAR_REL,
        la / "DeepSeek Harness" / "resources" / "app.asar",
    ]


def _app_version():
    """墨坊本体版本：打包态读它自己的 resources/app.asar，开发态读 console/package.json。

    ⚠ 不要用 `_dsh_asar_candidates()` —— 那是 **DSH 的** app.asar（另一棵树）。
    墨坊的布局：`resources/payload/scripts/nf_api_domains/…`，app.asar 在
    `resources/app.asar`，即代码区 `parents[2]` 的上一级。
    """
    code_root = Path(__file__).resolve().parents[2]      # scripts/nf_api_domains → payload / 仓库根
    asar = code_root.parent / "app.asar"
    pkg = _read_asar_package(asar)
    if pkg and pkg.get("version"):
        return str(pkg["version"]), str(asar), "asar"
    for p in (code_root / "console" / "package.json", code_root / "package.json"):
        pkg = _read_json(p)
        if pkg and pkg.get("version"):
            return str(pkg["version"]), str(p), "package.json"
    return "", "", ""


def _dsh_version(home):
    """DSH 基座版本：app.asar 的 package.json（不是 Electron 的 version 文件）。"""
    for p in _dsh_asar_candidates():
        pkg = _read_asar_package(p)
        if pkg and pkg.get("version"):
            return str(pkg["version"]), str(p)
    # 便携/其它安装形态：profile 里若有一份 @deepseek-ai/dsh-desktop，也认
    for name in ("@deepseek-ai/dsh-desktop", "@deepseek-ai/dsh"):
        pkg = _read_json(home / "profiles" / "desktop" / "node_modules" / name / "package.json")
        if pkg and pkg.get("version"):
            return str(pkg["version"]), str(p)
    return "", ""


def _dsh_home():
    explicit = _env("DSH_HOME")
    return Path(explicit).resolve() if explicit else (Path.home() / ".dsh").resolve()


def _profile_dir(home):
    name = _env("DSH_PROFILE") or "desktop"
    d = home / "profiles" / name
    if d.is_dir():
        return name, d
    profs = home / "profiles"
    if profs.is_dir():
        for child in sorted(profs.iterdir()):
            if child.is_dir():
                return child.name, child
    return name, d


def _parse_version(v):
    parts = str(v or "").split(".")
    try:
        return tuple(int("".join(ch for ch in p if ch.isdigit()) or 0) for p in parts[:3])
    except Exception:                                        # noqa: BLE001
        return None


def _cmp_version(a, b):
    pa, pb = _parse_version(a), _parse_version(b)
    if not pa or not pb:
        return 0
    width = max(len(pa), len(pb))
    pa = pa + (0,) * (width - len(pa))
    pb = pb + (0,) * (width - len(pb))
    return (pa > pb) - (pa < pb)


def _plugin_info(profile_dir):
    pkg = _read_json(profile_dir / "package.json") or {}
    deps = pkg.get("dependencies") or {}
    bundles = (((pkg.get("dsh") or {}).get("profile") or {}).get("bundles") or [])
    spec = str(deps.get(PLUGIN_NAME) or "")
    installed = _read_json(profile_dir / "node_modules" / PLUGIN_NAME / "package.json") or {}
    compat = ((installed.get("dsh") or {}).get("compatibility") or {})
    releases = compat.get("dshReleases") if isinstance(compat.get("dshReleases"), dict) else {}
    return {
        "name": PLUGIN_NAME,
        "in_deps": PLUGIN_NAME in deps,
        "in_bundles": PLUGIN_NAME in [str(x) for x in bundles],
        "spec": spec,
        "version": str(installed.get("version") or ""),
        "compat_dsh": str(compat.get("dsh") or ""),
        "compat_releases": releases,
        "bundles": [str(x) for x in bundles],
        "deps": {str(k): str(v) for k, v in deps.items()},
    }


def _check(key, label, status, detail, action=""):
    return {"key": key, "label": label, "status": status, "detail": detail, "action": action}


def diagnose(home=None, app_version=None, dsh_version=None):
    """生成三端诊断。纯读；任何文件缺失都降级成 unknown/bad，不抛。

    `app_version` / `dsh_version` 只给单测注入用（避免测试依赖本机装没装 DSH）。
    """
    home = Path(home).resolve() if home else _dsh_home()
    app_ver, app_from, app_kind = (
        (app_version, "", "injected") if app_version is not None else _app_version()
    )
    dsh_ver, dsh_from = (
        (dsh_version, "") if dsh_version is not None else _dsh_version(home)
    )
    profile_name, profile_dir = _profile_dir(home)
    plugin = _plugin_info(profile_dir)

    checks = []
    checks.append(_check(
        "app", "墨坊本体",
        "ok" if app_ver else "unknown",
        ("v" + app_ver + ("（" + app_kind + "）" if app_kind else "")) if app_ver else "读不到墨坊本体版本（不影响使用）",
    ))
    checks.append(_check(
        "dsh", "DSH 基座",
        "ok" if dsh_ver else "bad",
        ("v" + dsh_ver) if dsh_ver else "本机没找到 DSH（app.asar 读不到）",
        "" if dsh_ver else "先安装 DeepSeek Harness，再回来装插件",
    ))

    plugin_installed = plugin["version"] or plugin["in_deps"]
    if not plugin_installed:
        plugin_status = "bad"
        plugin_detail = "没装 " + PLUGIN_NAME + "（profile 的 dependencies 里没有）"
        plugin_action = "在 DSH 里装 dsh-novelforge（npm 包 / GitHub Release 的 tgz 都行）"
    elif not plugin["version"]:
        plugin_status = "bad"
        plugin_detail = "装了，但读不到 package.json（link: 可能断了）"
        plugin_action = "重装 dsh-novelforge，或检查 link 目标目录还在不在"
    elif not plugin["in_bundles"]:
        plugin_status = "bad"
        plugin_detail = "已装 v" + plugin["version"] + "，但**没进 bundles**：装了也不会加载"
        plugin_action = "把 dsh-novelforge 加进 profile 的 dsh.profile.bundles"
    else:
        plugin_status = "ok"
        plugin_detail = "v" + plugin["version"] + "（已进 bundles"
        plugin_detail += "，link: " + plugin["spec"][5:] + "）" if plugin["spec"].startswith("link:") else "）"
        plugin_action = ""

    checks.append(_check("plugin", "绒花墨坊插件", plugin_status, plugin_detail, plugin_action))

    # 插件 ↔ DSH 兼容：只看插件自己登记的 compat 表（和方寸同口径）
    if not dsh_ver or not plugin["version"]:
        checks.append(_check("compat", "插件 ↔ DSH 兼容", "unknown", "缺一侧版本，无法核对"))
    else:
        rel = plugin["compat_releases"].get(dsh_ver)
        floor = plugin["compat_dsh"]
        if rel:
            checks.append(_check("compat", "插件 ↔ DSH 兼容", "ok",
                                 "插件自报：DSH " + dsh_ver + " = " + str(rel)))
        elif floor and floor.startswith(">=") and _cmp_version(dsh_ver, floor[2:].strip()) < 0:
            checks.append(_check("compat", "插件 ↔ DSH 兼容", "bad",
                                 "DSH " + dsh_ver + " 低于插件下限 " + floor + "，插件可能加载不上",
                                 "升级 DSH 或降级插件到覆盖该 DSH 的版本"))
        else:
            checks.append(_check("compat", "插件 ↔ DSH 兼容", "unknown",
                                 "插件没登记 DSH " + dsh_ver + "（不一定是坏，但没验过；插件声明范围 "
                                 + (floor or "未写") + "）",
                                 "升级 dsh-novelforge 到覆盖当前 DSH 的版本，或以实际使用为准"))

    return {
        "ok": True,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "app": {"version": app_ver, "from": app_from, "kind": app_kind},
        "dsh": {"version": dsh_ver, "from": dsh_from},
        "profile": {"name": profile_name, "dir": str(profile_dir),
                    "bundles": plugin["bundles"], "dependencies": plugin["deps"]},
        "plugin": plugin,
        "checks": checks,
    }


def handle_dsh_diagnose(h):
    """`GET /dsh/diagnose` —— 只读三端诊断。"""
    try:
        return 200, diagnose()
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
