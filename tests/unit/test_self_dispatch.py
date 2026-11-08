# -*- coding: utf-8 -*-
"""阶段 4 派单记账 / 越界检测（2026-11-08，0.9.1 验收第 11 条）。

## 要治的病

作者在界面里把阶段 4 的章范围设成“只写 1-5 章”，跑完发现**全书十章都写完了**。

盘上的事实是：任务书**只发了一份**（`data/state/tasks/self/stage4/ch01.md`），
多出来的九章是外部执行方自己写的。管线拦不住别人的手，但**必须记账** ——
否则“按章范围跑”永远只是一句口头约定，事后谁也说不清哪几章没被派过。

## 判据（每条都能变红）

- D1 `find_overrun` 只认“这次新增合格的 − 派过的”：派过的章多写一遍不算越界
- D2 派发前就已合格的章不算越界（那是上一轮的成果，不是这一轮多写的）
- D3 告警文案点名章号，且说清后果（缺上一章衔接段与滚动摘要）
- D4 派单记账落盘可回读（同一个文件、同一份形状）
- D5 emit_tasks 真的接了：派单前后都写账，hint 里带告警

用法：python tests/unit/test_self_dispatch.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


import stage4_writing as S4                                       # noqa: E402


def case_find_overrun():
    print("\n【D1】只认“这次新增的 − 派过的”")
    check("D1 派第 3 章、盘上只多第 3 章 → 不算越界",
          S4.find_overrun([3], [1, 2], [1, 2, 3]) == [], S4.find_overrun([3], [1, 2], [1, 2, 3]))
    check("D1 派第 1 章、盘上多出 1..10 → 2..10 全算越界",
          S4.find_overrun([1], [], list(range(1, 11))) == list(range(2, 11)),
          S4.find_overrun([1], [], list(range(1, 11))))
    check("D1 多写的章里既有派过的也有没派的 → 只报没派的",
          S4.find_overrun([2], [1], [1, 2, 3, 4]) == [3, 4],
          S4.find_overrun([2], [1], [1, 2, 3, 4]))

    print("\n【D2】派发前就合格的章不算越界")
    check("D2 before 里的章不被算成这一轮多写的",
          S4.find_overrun([5], [1, 2, 3, 4], [1, 2, 3, 4, 5]) == [])
    check("D2 没派单（issued 空）+ 盘上一片空 → 无告警",
          S4.find_overrun([], [1, 2], [1, 2]) == [])


def case_warning_text():
    print("\n【D3】告警是人话，且说清后果")
    msg = S4.overrun_warning([1], [], list(range(1, 11)))
    check("D3 有话说", bool(msg), msg)
    check("D3 点名“越界”", "越界" in msg, msg)
    check("D3 点名派过的章", "第 1 章" in msg, msg)
    check("D3 点名多出来的章（2 与 10 都在）", "2" in msg and "10" in msg, msg)
    check("D3 说清后果（衔接段 / 滚动摘要）",
          "衔接段" in msg and "滚动摘要" in msg, msg)
    check("D3 没越界时返回空串（不打扰）",
          S4.overrun_warning([1], [], [1]) == "")


def case_dispatch_file():
    print("\n【D4】派单记账落盘可回读")
    tmp = Path(tempfile.mkdtemp(prefix="nf_disp_"))
    cwd = os.getcwd()
    os.chdir(tmp)
    old = S4._DISPATCH_PATH
    S4._DISPATCH_PATH = Path("data/state/self_dispatch.json")
    try:
        S4._write_dispatch({"stage": 4, "issued": [7], "complete": [1, 2, 3],
                            "at": "2026-11-08T00:00:00", "last_overrun": ""})
        got = S4._read_dispatch()
        _rel = str(S4._DISPATCH_PATH).replace("\\", "/")
        check("D4 落盘路径在 data/state/ 下", _rel == "data/state/self_dispatch.json", _rel)
        check("D4 回读一致", got.get("issued") == [7] and got.get("complete") == [1, 2, 3], got)
        check("D4 文件真存在", (tmp / "data" / "state" / "self_dispatch.json").exists())
        # 坏文件不许炸：读不到就当没派过
        (tmp / "data" / "state" / "self_dispatch.json").write_text("{坏", encoding="utf-8")
        check("D4 坏文件不炸（返回空）", S4._read_dispatch() == {})
    finally:
        S4._DISPATCH_PATH = old
        os.chdir(cwd)


def case_wiring():
    print("\n【D5】emit_tasks 真的接了（派单前后都记账，hint 带告警）")
    src = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    i = src.find("def emit_tasks(")
    j = src.find("def main()", i)
    body = src[i:j] if i > 0 and j > i else ""
    check("D5 找到 emit_tasks", bool(body))
    check("D5 派单前读账并算越界",
          "_read_dispatch()" in body and "overrun_warning(" in body)
    check("D5 派单后写账（issued 只有这一章）",
          "_write_dispatch({" in body and '"issued": [n]' in body)
    check("D5 全书齐备那条返回路径也写账（不然越界永远不会被发现）",
          body.count("_write_dispatch(") >= 2, body.count("_write_dispatch("))
    check("D5 hint 里带告警", '("；" + _warn)' in body, body[-400:])
    check("D5 任务书正文有“本轮只交这一章”的硬约束",
          "本轮只交这一章" in src)


def main():
    print("=" * 66)
    print("阶段 4 派单记账 / 越界检测")
    case_find_overrun()
    case_warning_text()
    case_dispatch_file()
    case_wiring()
    print("\n" + "-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
