# -*- coding: utf-8 -*-
"""补齐出厂配置键（老工作区升级用）。

## 什么时候用

`nfctl check` 报“关键配置键齐全”不通过时。那些键缺失会让功能**静默不生效**
（代码读不到就回落到默认值，全程没提示）—— 真机实测老工作区缺 46 个。

## 做什么（作者问卷 C1=B / C2=B 的口径）

- **补缺失键**：从出厂 `config/system.yaml` 连注释一起抄（注释写着这个键为什么存在）；
- **升级"值仍是旧出厂值"的键**：用户改过的一个字不动；
- 缺失的父节点整段补（`context` / `discuss` / `prose` 这类新段）；
- 写前备份、写后回读校验，坏文件自动还原。

## 不改什么

- 用户改过的值（判据：当前值 ≠ 旧出厂值）—— 那是创作决策；
- 任何注释（全部保留）；
- 不整份 dump（`yaml.safe_dump` 会吃掉 170+ 行注释，这是本仓历史事故）。

用法：
    python scripts/migrate_config_keys.py            # 只看（dry-run，零写入）
    python scripts/migrate_config_keys.py --yes      # 真改（写前自动备份）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def _repo_root() -> Path:
    """出厂文件所在位置（代码区）。

    ⚠️ 用途明确：只为了拿 **出厂 system.yaml** 当参照，不是猜用户工作区。
    用户工作区由 `--root` 或 CWD 决定。
    """
    return _HERE.parent


def main() -> int:
    ap = argparse.ArgumentParser(
        description="补齐出厂配置键（只补缺失 + 升级旧出厂值，绝不覆盖用户改过的）")
    ap.add_argument("--root", default=None,
                    help="用户工作区根（默认当前目录）。⚠️ 不是代码区 —— 出厂参照自动取代码区")
    ap.add_argument("--yes", action="store_true",
                    help="真的写盘（不加就是 dry-run，只报告要做什么）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    import json

    from utils import config_keys as ck

    workspace = Path(args.root).resolve() if args.root else Path.cwd()
    factory = _repo_root()
    if not (workspace / "config" / "system.yaml").exists():
        print("[config-keys] 这个目录下没有 config/system.yaml：%s" % workspace)
        print("            用 --root 指定工作区根（不是代码目录）")
        return 2

    d = ck.diff_keys(workspace, factory)
    miss = ck.critical_missing(workspace)

    if not args.yes:
        out = {"dry_run": True, "workspace": str(workspace),
               "factory": str(factory),
               "missing": d["missing"], "upgradable": d["upgradable"],
               "user_changed": d["changed_by_user"],
               "critical_missing": miss}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        print("出厂参照: %s" % (factory / "config" / "system.yaml"))
        print("目标工作区: %s" % (workspace / "config" / "system.yaml"))
        print()
        print("缺 %d 个键，可升级 %d 个，你改过的 %d 个（不动）"
              % (len(d["missing"]), len(d["upgradable"]), len(d["changed_by_user"])))
        if miss:
            print()
            print("⚠ 其中关键键 %d 个（缺失会让功能静默不生效）：" % len(miss))
            for m in miss:
                print("   - %-42s %s" % (m["key"], m["why"]))
        if d["upgradable"]:
            print()
            print("会升级（值仍等于旧出厂值）：")
            for it in d["upgradable"]:
                print("   %-42s %s → %s" % (it["key"], it["from"], it["to"]))
        if d["changed_by_user"]:
            print()
            print("你改过的值（绝不碰）：")
            for k in d["changed_by_user"][:12]:
                print("   %-42s 当前=%r" % (k, d["ws_flat"].get(k)))
        print()
        print("这是预演，没有改任何文件。要真改：加 --yes")
        return 0

    r = ck.apply(workspace, factory)
    if not r.get("ok"):
        print("[config-keys] 失败：%s" % r.get("error"))
        return 1
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        return 0
    print("补了 %d 个键，升级 %d 个，跳过你改过的 %d 个"
          % (len(r["added"]), len(r["upgraded"]), len(r["skipped_user"])))
    for it in r["added"]:
        print("   + %-44s = %r" % (it["key"], it["value"]))
    for it in r["upgraded"]:
        print("   ↑ %-44s %r → %r" % (it["key"], it["from"], it["to"]))
    if r.get("backup"):
        print()
        print("备份: %s" % r["backup"])
        print("要回滚：把备份文件覆盖回 config/system.yaml")
    for w in r["warnings"]:
        print("WARN:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
