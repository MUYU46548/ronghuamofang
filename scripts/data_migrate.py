# -*- coding: utf-8 -*-
"""书稿数据跨版本迁移 CLI（C3 · 升级断崖的机制侧）。

判据与执行全在 `utils/workspace_migrate`（单一来源），本文件只做输入输出 ——
CLI 与启动钩子各写一份判断，迟早漂移成「这边拦着、那边放行」。

用法（workdir = 项目根；多项目用 --root）：

    python scripts/data_migrate.py             # 状态（只读，默认）
    python scripts/data_migrate.py --dry-run   # 预演：跑哪几步、打不打快照
    python scripts/data_migrate.py --run       # 执行 safe 步骤（补字段/建索引类）
    python scripts/data_migrate.py --run --yes # 连同需要确认的步骤（会改写你的文件结构）

退出码：0 = 当前工作区可安全运行（或已按要求迁完）；
        1 = 需要处理 —— 数据比代码新 / 版本链断 / 迁移失败。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.workspace_migrate import (DATA_SCHEMA, STAMP_REL,  # noqa: E402
                                     migrate, status, stamp_path)
from utils import import_guard  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="书稿数据跨版本迁移（C3）")
    ap.add_argument("--root", default=None, help="项目根（默认当前目录）")
    ap.add_argument("--run", action="store_true", help="真正执行（默认只看状态）")
    ap.add_argument("--yes", action="store_true",
                    help="连同需要确认的步骤一起执行（它们会改写你的文件结构）")
    ap.add_argument("--dry-run", action="store_true", help="预演：不打快照、不写数据")
    args = ap.parse_args()

    root = os.path.abspath(args.root) if args.root else os.getcwd()
    st = status(root)
    print("[migrate] 项目根: %s" % root)
    print("[migrate] 代码期望数据结构 v%d；工作区 %s；戳: %s"
          % (DATA_SCHEMA,
             ("已记为 v%d" % st["found"]) if st["found"] is not None else "无版本戳",
             stamp_path(root)))
    print("[migrate] %s" % st["message"])

    if not args.run and not args.dry_run:
        if st["action"] in ("none", "baseline"):
            print("[migrate] 无需处理。要写入基线戳：python scripts/data_migrate.py --run")
        elif st["action"] == "migrate":
            print("[migrate] 预演一下：python scripts/data_migrate.py --dry-run；"
                  "真跑：python scripts/data_migrate.py --run")
        return 0 if st["action"] in ("none", "baseline", "migrate") else 1

    ok, rep = migrate(root, dry_run=args.dry_run,
                      allow_unsafe=(args.run and args.yes))
    for ln in rep["lines"]:
        print(ln)
    if rep.get("rollback") and not args.dry_run:
        print("[migrate] 回退这条迁移：%s" % rep["rollback"])
    elif st["action"] == "migrate":
        last = import_guard.rollback_hint(op="workspace_migrate", root=root)
        print("[migrate] 最近一次迁移的回退入口：%s" % last)
    if not ok:
        print("[migrate] 未完成 —— 版本戳没有推进，工作区保持可回退状态。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
