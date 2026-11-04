# -*- coding: utf-8 -*-
"""设定引用过期（stale）CLI —— M10，零 token。

判据全部在 `utils/setting_stale`（与 nf_api 共用同一份），本文件只做输入输出。

用法（workdir = 项目根）：

    python scripts/setting_stale.py status              # 谁在等核对、哪一章改过
    python scripts/setting_stale.py link                # 扫描章节，把现有引用登记进设定条目
    python scripts/setting_stale.py link --dry-run      # 只看会登记什么，不写盘
    python scripts/setting_stale.py confirm 沈砚 --chapter 3   # 核对通过（作者动作）
    python scripts/setting_stale.py drop 沈砚 --chapter 5      # 去掉一条来源（章没了/误匹配）

典型闭环：

    python scripts/setting_stale.py link        # 第一次：把已写好的章登记进来源
    python scripts/setting_stale.py status      # 改过渡章之后：看哪几条待核
    python scripts/setting_stale.py confirm 沈砚 --chapter 3   # 逐条核对后恢复

安全：`confirm` / `drop` 是**作者的判断**（与 /setting/stale/review 同族）。
`gates.agent_mode=true` 下 CLI 一律拒绝（判据单源 `utils/agent_guard.cli_approval_gate`），
只读的 `status` 与机械的 `link` 不受限。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils import agent_guard                                    # noqa: E402
from utils import setting_stale as SS                            # noqa: E402


def _guard(action):
    """作者动作在 agent_mode=true 下拒绝（与其它审批类 CLI 同一条判据）。"""
    if not agent_guard.agent_mode_enabled(agent_guard.load_project_config()):
        return True
    _allowed, evidence = agent_guard.cli_approval_gate()
    sys.stderr.write(agent_guard.refusal_message(action, evidence) + "\n")
    return False


def cmd_status(args):
    rep = SS.status(args.root)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    if not rep.get("checked"):
        print("[setting-stale] 没查成：%s" % rep.get("reason"))
        return 1
    c = rep["counts"]
    print("设定引用：%d 条条目，%d 条登记过来源，扫了 %d 章"
          % (c["entries"], c["quoted"], rep.get("chapters", 0)))
    print("  待核 %d · 新引用 %d · 对得上 %d · 未进正文 %d · 名字太短没法定位 %d"
          % (c["stale"], c["new"], c["ok"], c["absent"], c["unlocatable"]))
    for it in rep["items"]:
        if it["state"] == "stale":
            bad = [s for s in it["sources"] if s["state"] != "ok"]
            print("  × %s（%s）：%s" % (it["name"], it["label"],
                                       "；".join(s["reason"] or ("第 %d 章" % s["chapter"])
                                                for s in bad)))
        elif it["state"] == "new":
            print("  + %s（%s）：第 %s 章里有引用但还没登记"
                  % (it["name"], it["label"],
                     "、".join(str(n) for n in it["new_chapters"])))
    if rep.get("hint"):
        print("—— " + rep["hint"])
    return 0


def cmd_link(args):
    rep = SS.link(args.root, dry_run=bool(args.dry_run))
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1
    if not rep.get("ok"):
        print("[setting-stale] %s" % rep.get("error"))
        return 1
    print("[setting-stale] %s" % rep.get("message"))
    for a in rep.get("added") or []:
        print("  · %s（%s）：第 %s 章"
              % (a["name"], a["kind"], "、".join(str(n) for n in a["chapters"])))
    if rep.get("backup"):
        print("  旧设定集已备份：%s" % rep["backup"])
    return 0


def cmd_review(args):
    action = "drop" if args.cmd == "drop" else "confirm"
    if not _guard("核对设定引用"):
        return 1
    code, rep = SS.review(args.root, name=args.name, kind=args.kind,
                          chapters=args.chapter, action=action)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("ok") else 1
    if not rep.get("ok"):
        print("[setting-stale] %s" % rep.get("error"))
        for s in rep.get("skipped") or []:
            print("  · 第 %s 章：%s" % (s.get("chapter"), s.get("reason")))
        return 1
    print("[setting-stale] %s" % rep.get("message"))
    if rep.get("backup"):
        print("  旧设定集已备份：%s" % rep["backup"])
    return 0


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default="", help="项目根（默认当前目录）")
    common.add_argument("--json", action="store_true", help="输出机器可读 JSON")

    p = argparse.ArgumentParser(description="设定引用过期（stale）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", parents=[common], help="待核清单（读时现算，不写文件）")
    sp = sub.add_parser("link", parents=[common],
                        help="扫描章节，把现有引用登记进设定条目（只增不改）")
    sp.add_argument("--dry-run", action="store_true", help="只预演，不写盘")

    for name, help_text in (("confirm", "核对通过：把来源版本更新为当前正文"),
                            ("drop", "移除来源登记（章没了 / 误匹配）")):
        sp = sub.add_parser(name, parents=[common], help=help_text)
        sp.add_argument("name", help="条目名")
        sp.add_argument("--kind", default="", help="条目类别（同名多条时必填，如 characters / world.locations）")
        sp.add_argument("--chapter", type=int, action="append", default=None,
                        help="章号（可重复；confirm 省略=全部，drop 必填）")

    args = p.parse_args()
    return {"status": cmd_status, "link": cmd_link,
            "confirm": cmd_review, "drop": cmd_review}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
