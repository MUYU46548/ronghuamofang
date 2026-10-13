# -*- coding: utf-8 -*-
"""丙4 大纲分段审批 CLI —— 把「一次读完全本」拆成可断点续审的若干段。

判据全部在 `utils/outline_segments`（与 approve.py / nf_api 共用同一份），
本文件只做输入输出，不自己判任何东西。

用法（workdir = 项目根）：

    python scripts/segment_cli.py list              # 列出分段 + 通过状态
    python scripts/segment_cli.py status            # 一段话总览（含还差几段）
    python scripts/segment_cli.py approve s03       # 标记第 3 段已读通过
    python scripts/segment_cli.py revoke s03        # 撤销该段（改了大纲后重审）

典型闭环：

    python scripts/segment_cli.py list
    #（逐段打开 data/outline/global.md 读对应段）
    python scripts/segment_cli.py approve s01
    ...
    python scripts/approve.py --stage 2             # 全部通过后才会放行

`approve.py --stage 2` 在分段未全过时会拒绝并回显本 CLI 的用法 —— 这是丙4 的
放行契约，不是报错。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.outline_segments import gate_status, set_segment, summary  # noqa: E402
from utils.file_io import read_text  # noqa: E402
from utils.outline_segments import DOC_REL  # noqa: E402

STATUS_CN = {"approved": "已通过", "pending": "未通过"}


def _print_list(sm):
    if not sm["segments"]:
        print("（没有可分段的大纲：%s 不存在或没有标题）" % DOC_REL)
        return
    print("分段审批：已通过 %d/%d%s" % (
        sm["approved"], sm["total"],
        "　—— 全部通过后 approve.py --stage 2 才会放行" if not sm["all_approved"]
        else "　—— 已全过，可以放行阶段2"))
    for s in sm["segments"]:
        flag = "v" if s.get("status") == "approved" else " "
        print("  [%s] %-5s %-28s %6s 字%s" % (
            flag, s.get("id"), (s.get("title") or "")[:28], s.get("chars"),
            "  （%s）" % (s.get("approved_at") or "") if s.get("approved_at") else ""))
    if sm.get("invalidations"):
        print("  注：大纲改过 %d 次，每次改动都会把全部分段打回未通过"
              "（旧审核不为新内容背书）" % sm["invalidations"])


def main():
    parser = argparse.ArgumentParser(description="大纲分段审批（丙4）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="列出分段与通过状态")
    sub.add_parser("status", help="一段话总览")
    sp = sub.add_parser("approve", help="标记某段已读通过")
    sp.add_argument("id", help="段号，如 s03（用 list 看）")
    sp = sub.add_parser("revoke", help="撤销某段的通过标记")
    sp.add_argument("id")
    args = parser.parse_args()

    if args.cmd == "list":
        _print_list(summary("."))
        return 0

    if args.cmd == "status":
        sm = summary(".")
        print("大纲分段：%d 段，已通过 %d，未通过 %d%s" % (
            sm["total"], sm["approved"], sm["pending"],
            "（已全过）" if sm["all_approved"] else ""))
        if not sm["doc_exists"]:
            print("  注意：%s 还不存在" % DOC_REL)
        ok, why = gate_status(".")
        print("阶段2 放行：" + ("可以" if ok else "还不行"))
        if not ok:
            for ln in why.splitlines():
                print("  " + ln)
        return 0 if ok else 1

    ok, msg = set_segment(".", args.id, approved=(args.cmd == "approve"))
    print(("[approve] " if args.cmd == "approve" else "[revoke] ") + msg)
    if not ok:
        return 1
    sm = summary(".")
    print("进度：%d/%d 已通过" % (sm["approved"], sm["total"]))
    if sm["all_approved"]:
        print("  → 全部分段已通过，可以放行：python scripts/approve.py --stage 2")
    else:
        print("  → 还差 %d 段（用 list 看是哪几段）" % sm["pending"])
    # 提醒：大纲正开着没读
    try:
        if not read_text(DOC_REL).strip():
            print("  注意：%s 是空的" % DOC_REL)
    except Exception:                                        # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
