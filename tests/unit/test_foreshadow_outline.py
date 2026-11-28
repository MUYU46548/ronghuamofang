# -*- coding: utf-8 -*-
"""伏笔台账：从整体大纲的“伏笔清单”收集（2026-11-28，作者裁决）。

## 这道断链是怎么发现的

真机《测试用书-1125》的整体大纲里，模型自己编了 **f001~f026** 并逐条写了埋设点
与回收计划（“本段兑现”段里全是 `f001（老太太的单子）` 这种引用）。但：

- 提示词**从来没要求**过 f 编号（`prompts/*.md` 里 "f0"、伏笔清单 零命中）；
- 伏笔台账只认**章大纲的“悬念钩子”**；
- 章大纲里一个 f 编号都没有。

结果：那 26 条在系统里**彻底失联** —— 界面上的伏笔台账只显示 6 条（来自章钩子），
另外 26 条一个字都看不到。作者看得到编号、系统不知道它存在。

作者 2026-11-28 裁决：**提示词正式要求 f 编号 + 台账改从整体大纲收集**。

## 本用例锁什么

1. 解析器认正规格式（`- f001 | 起 | 埋：… | 收：… | 状态：…`）；
2. **报问题必须严格**：跳号 / 编号重复 / 状态非法 / 引用不存在的编号 ——
   悄悄丢掉一条比报错更糟（作者会以为那条没问题）；
3. 台账里整体清单条目与章钩子条目**共存**（id 前缀不同，互不覆盖）；
4. 整体清单条目没有章号，**不参与"闲置 N 章"**（瞎算会给出假告警）；
5. 没有清单时如实说明"没有"，不返回假数据。

用法：python tests/unit/test_foreshadow_outline.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "utils"))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  → " + str(detail)[:280] if detail else ""))


GOOD = """# 《测试书》整体大纲

## 起
故事开端……

## 伏笔清单
- f001 | 起 | 埋：老太太在末班船上交来的石头与那句不许听的话 | 收：第 12 章挖出她孙子为何夜夜听见石头响 | 状态：未回收
- f002 | 起 | 埋：七遍口供与挂起的案子 | 收：第 20 章卷宗缺页的真相 | 状态：未回收
- f003 | 起 | 埋：船头挂灯五年只灭过两次 | 收：第 28 章灯灭那夜的登船者 | 状态：未回收
- f004 | 承 | 埋：明暗两层叠景，暗层无人承认 | 收：第 30 章暗层入口被打开 | 状态：未回收
- f005 | 承 | 埋：不肯补票的抱砖客 | 收：第 34 章盐砖上拍前夜 | 状态：已回收
- f006 | 转 | 埋：祁雉按住全街声音四十秒 | 收：第 41 章他为何要确认回声 | 状态：未回收

## 本段兑现
本段兑现：f001（老太太的单子）、f002（七遍口供）、f003（船头挂灯）。

## 预计章节数
45

## 章节规划
第 1-10 章：铺垫
"""


def main():
    print("=" * 70)
    print("伏笔台账：从整体大纲收集")
    print("=" * 70)

    from utils import foreshadow as fs

    # ---------- [1] 解析正常格式 ----------
    print("\n[1] 解析：正规格式必须逐字段认出来")
    items, problems = fs.parse_global_outline(GOOD)
    check("解析出 6 条", len(items) == 6, len(items))
    check("无问题（格式全对）", problems == [], problems)
    ids = [i["id"] for i in items]
    check("编号按 f001..f006", ids == ["f001", "f002", "f003", "f004", "f005", "f006"], ids)
    f1 = items[0]
    check("段位解析对（起）", f1["arc"] == "起", f1["arc"])
    check("埋设点解析对（不含“埋：”前缀）",
          f1["planted"].startswith("老太太") and "埋：" not in f1["planted"],
          f1["planted"])
    check("回收计划解析对（不含“收：”前缀）",
          "第 12 章" in f1["payoff"] and "收：" not in f1["payoff"], f1["payoff"])
    check("状态解析对（未回收）", f1["status"] == "未回收", f1["status"])
    check("第 5 条状态是已回收", items[4]["status"] == "已回收", items[4]["status"])

    # ---------- [2] 报问题要严格 ----------
    print("\n[2] 报问题：跳号 / 重复 / 非法状态 / 幽灵引用")
    skip = GOOD.replace("- f004 |", "- f009 |")
    _i, p_skip = fs.parse_global_outline(skip)
    check("跳号被报出来", any("不连续" in x for x in p_skip), p_skip)
    check("跳号报告里点名缺哪几个",
          any("f004" in x for x in p_skip), p_skip)

    dup = GOOD + "- f001 | 起 | 埋：抄了一遍 | 收：第 1 章 | 状态：未回收\n"
    _i, p_dup = fs.parse_global_outline(dup)
    check("编号重复被报出来", any("重复" in x for x in p_dup), p_dup)

    bad_status = GOOD.replace("状态：未回收", "状态：待定", 1)
    it_bs, p_bs = fs.parse_global_outline(bad_status)
    check("非法状态被报出来", any("不是" in x for x in p_bs), p_bs)
    check("非法状态的条目仍保留（不静默丢弃）", len(it_bs) == 6, len(it_bs))
    check("非法状态按“未回收”处理", it_bs[0]["status"] == "未回收", it_bs[0]["status"])

    ghost = GOOD + "\n补充：还有一条 f099 没写进表里。\n"
    _i, p_ghost = fs.parse_global_outline(ghost)
    check("**引用不存在的编号被报出来**",
          any("f099" in x for x in p_ghost), p_ghost)

    # ---------- [3] 空埋/空收不算伏笔 ----------
    print("\n[3] 埋或收写空的“伏笔”不算数（会被报出来）")
    hollow = ("# 书\n## 伏笔清单\n"
              "- f001 | 起 | 埋： | 收：第 5 章 | 状态：未回收\n")
    it_h, p_h = fs.parse_global_outline(hollow)
    check("空埋被丢弃", len(it_h) == 0, it_h)
    check("并且报出来（不静默忽略）", any("空" in x for x in p_h), p_h)

    # ---------- [4] 台账：两条来源共存 ----------
    print("\n[4] 台账：整体清单 + 章钩子共存，互不覆盖")
    tmp = Path(tempfile.mkdtemp(prefix="nf_fs_"))
    try:
        g = tmp / "global.md"
        g.write_text(GOOD, encoding="utf-8")
        chd = tmp / "chapters"
        chd.mkdir()
        (chd / "01.md").write_text(
            "## 第1章 重三钱\n\n- 核心事件：岑砚接下第一单\n"
            "- 涉及角色：岑砚\n- 小目标：把话压进石头\n"
            "- 悬念钩子：登记簿上那行后面被人添了“待拍”\n"
            "- 功能：铺垫\n- 衔接：开篇\n", encoding="utf-8")
        d = fs.collect(outline_dir=chd, global_outline=g, stale_after=30,
                       latest_chapter=1)
        ids = [i["id"] for i in d["items"]]
        check("整体清单的 6 条都在", all("f%03d" % n in ids for n in range(1, 7)), ids)
        check("章钩子的条目也在（ch01-1）", "ch01-1" in ids, ids)
        check("总数 = 6 + 1", d["summary"]["total"] == 7, d["summary"])
        srcs = {i["id"]: i.get("source") for i in d["items"]}
        check("整体清单条目 source=outline", srcs.get("f001") == "outline", srcs)
        check("章钩子条目 source=auto", srcs.get("ch01-1") == "auto", srcs)

        # ---------- [5] 整体清单条目不参与"闲置 N 章" ----------
        print("\n[5] 整体清单条目没有章号 → 不参与闲置告警（不许瞎算）")
        by = {i["id"]: i for i in d["items"]}
        f1 = by["f001"]
        check("f001 的 planted 是 None", f1.get("planted") is None, f1.get("planted"))
        check("f001 的 idle 也是 None", f1.get("idle") is None, f1.get("idle"))
        check("**f001 不因算不出闲置而被误标 stale**",
              f1.get("stale") is False, f1.get("stale"))
        check("章钩子条目仍有正常 idle 数值",
              isinstance(by["ch01-1"].get("idle"), int), by["ch01-1"].get("idle"))

        # ---------- [6] 渲染不崩（planted=None 的格式分支） ----------
        print("\n[6] 渲染：两种条目都能打印，不崩")
        try:
            md = fs.render_markdown(d)
            ok_render = True
        except Exception as e:                                # noqa: BLE001
            ok_render = False
            md = ""
            check("render_markdown 不抛异常", False, e)
        if ok_render:
            check("render_markdown 不抛异常", True)
            check("整体清单条目显示了段落与回收计划",
                  "f001" in md and "起" in md and "第 12 章" in md, md[:300])
            check("章钩子条目仍显示章号", "ch01-1" in md, md[-300:])

        # ---------- [7] 没有问题清单时如实说明 ----------
        print("\n[7] 两份来源都没有 → 如实说“没有”，不返回假数据")
        empty_dir = tmp / "empty"
        empty_dir.mkdir()
        no_g = tmp / "no_global.md"
        d2 = fs.collect(outline_dir=empty_dir, global_outline=no_g,
                        stale_after=30, latest_chapter=0)
        check("台账为空", d2["summary"]["total"] == 0, d2["summary"])
        check("notes 说明了为什么空（不是“没有伏笔”）",
              any("无从抽取" in n for n in d2["notes"]), d2["notes"])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------- [8] 提示词里真的要求了 f 编号 ----------
    print("\n[8] 提示词：stage2 必须正式要求伏笔清单")
    src = (REPO / "prompts" / "stage2_global_outline.md").read_text(encoding="utf-8")
    check("有“伏笔清单”段", "## 伏笔清单" in src)
    check("说明了 f 编号形态", "fNNN" in src or "f001" in src)
    check("要求连续编号（不许跳号）", "连续" in src)
    check("要求写清收束计划", "收：" in src)
    check("说明引用不存在的编号 = 断链", "断链" in src)

    print("\n" + "=" * 70)
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
