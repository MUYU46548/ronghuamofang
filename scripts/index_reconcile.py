# -*- coding: utf-8 -*-
"""索引与实物对账 CLI（零 token，确定性，只读）。

素材目录里常混着 `00_大纲收束索引.md`、`碎片填充计划.md` 这类**登记表**：它们声称
“共 96 篇 / 9 维齐全”“已登记 24 个日期”，而磁盘上实际只有 41 张卡、18 个碎片日期。
管线本身以文件系统为准（`stage1_consolidate` 扫盘，`utils.meta_detect` 排掉元文件），
所以这些数字**不影响生产，但会误导人**：作者看索引以为齐了，就不再补素材。

本命令把“声称 vs 实物”的差异摊出来 —— 不修任何东西，只说实话。

用法（workdir = 项目根，Python 用项目 .venv）:

    python scripts/index_reconcile.py              # 人读摘要
    python scripts/index_reconcile.py --json       # 机读（GUI / Agent 用同一份）
    python scripts/index_reconcile.py --dir 素材=path/to/x --dir 碎片=path/to/y
    python scripts/index_reconcile.py --root <其他项目根>

退出码：0 = 一致 / 没有登记表；1 = 发现不一致（需要人看）；2 = 读取失败。

判据：元文件识别与 stage1 **同一份实现**（`utils/meta_detect`），不另写一套。
"""
import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from utils.interp_guard import ensure_working_python  # noqa: E402

ensure_working_python("scripts/index_reconcile.py")

from utils.index_reconcile import reconcile  # noqa: E402

SCRAPS_DIR_DEFAULT = "materials/original_scraps"


def _scraps_dir(root):
    """碎片目录由 config/project.yaml 的 `materials.scraps_dir` 决定。"""
    try:
        # 走 config_io 的严格读取（重复键/语法错会带行号报出来），不裸解析 ——
        # 项目纪律：配置类读取只有一个入口（tests/unit/test_config_io.py 守这条）。
        from utils.config_io import load_config_yaml
        proj = load_config_yaml(root / "config" / "project.yaml", {}) or {}
        d = (proj.get("materials") or {}).get("scraps_dir")
        if d:
            return str(d)
    except Exception:                                       # noqa: BLE001
        pass
    return SCRAPS_DIR_DEFAULT


def _print_human(res):
    print("索引与实物对账（%s）" % res["checked_at"])
    posts = res["actual"]["posts"]
    print("  实物：%s" % " · ".join("%s %d 篇" % (k, v) for k, v in posts.items()))
    print("  实物日期：%d 个" % res["actual"]["dates_total"])
    print("")
    if res["verdict"] == "no_meta":
        print("  没有登记表（索引/清单），无需对账。")
        return
    for m in res["meta_files"]:
        mark = "OK  " if m["consistent"] else "差异"
        print("[%s] %s" % (mark, m["path"]))
        c = m["claims"]
        if c["posts"] is not None:
            print("    声称篇数：%d  |  实物：%d" % (c["posts"], m["diff"]["posts_actual"]))
        if c["dimensions"]:
            print("    声称维度：%d 维（无法用文件系统核验）" % c["dimensions"])
        if c["dates"]:
            print("    声称日期：%d 个，命中 %d 个"
                  % (m["diff"]["dates_claimed"], m["diff"]["dates_matched"]))
        for issue in m["issues"]:
            print("    - " + issue)
        if m["diff"]["dates_missing"]:
            head = "、".join(m["diff"]["dates_missing"][:8])
            more = len(m["diff"]["dates_missing"]) - 8
            print("    找不到的日期：%s%s"
                  % (head, (" …等 %d 个" % len(m["diff"]["dates_missing"])) if more > 0 else ""))
    print("")
    print("结论：" + res["summary"])
    if res["verdict"] == "mismatch":
        print("提示：以实物为准 —— 管线读素材扫的是文件系统，登记表不参与生产。")
    for w in res.get("warnings") or []:
        print("[WARN] " + w)


def main():
    ap = argparse.ArgumentParser(description="索引与实物对账（零 token，只读）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="项目根目录")
    ap.add_argument("--json", action="store_true", help="输出机读 JSON")
    ap.add_argument("--dir", action="append", default=[], metavar="KEY=PATH",
                    help="自定义对账范围（可多次）。默认 raw + 碎片目录")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if args.dir:
        scopes = []
        for item in args.dir:
            if "=" not in item:
                print("[ERROR] --dir 需要 KEY=PATH 形式: " + item, file=sys.stderr)
                return 2
            key, path = item.split("=", 1)
            scopes.append((key.strip() or path, path.strip()))
    else:
        scopes = [("raw", "materials/raw"), ("scraps", _scraps_dir(root))]

    try:
        res = reconcile(root, scopes)
    except Exception as e:                                  # noqa: BLE001
        print("[ERROR] 对账失败: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        _print_human(res)
    return 0 if res["verdict"] in ("consistent", "no_meta") else 1


if __name__ == "__main__":
    sys.exit(main())
