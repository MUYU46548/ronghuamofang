# -*- coding: utf-8 -*-
"""滚动摘要：占位文本不许被当成摘要数据。

## 本用例守什么（2026-11-28，真机发现）

真机工作区的 `data/summaries/rolling.md` 只有 34 字节，内容全是占位文本：
    # 滚动摘要
    ## 全书摘要
    （待首次压缩生成）
    ## 近期章节

而 `_render_rolling` 用的是 `if g:` 判空 —— 占位文本是真值 → **每章提示词里都会
多注入一行「【全书】（待首次压缩生成）」**。模型不会因此写错，但那是纯噪声，
还会让模型以为"全书摘要已经有了，只是内容是这句话"。

判据只有一条核心：**占位文本读出来必须是空值**，而写文件时仍然写出可读的占位
（人打开文件要知道"这里还没生成"，不能变成一个空文件）。

用法：python tests/unit/test_summary_chain.py
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
        print("  FAIL  " + name + ("  → " + str(detail)[:260] if detail else ""))


def main():
    print("=" * 70)
    print("滚动摘要：占位文本不许当数据")
    print("=" * 70)

    from utils import summary_chain as sc

    tmp = Path(tempfile.mkdtemp(prefix="nf_sum_"))
    try:
        # ---------- [1] 空数据 → 写出占位文本（人看得懂）----------
        print("\n[1] 没有摘要时：文件里要有可读占位，读回来要是空")
        p = tmp / "rolling.md"
        sc.write_rolling(p, {"global_summary": "", "chapters": {}})
        raw = p.read_text(encoding="utf-8")
        check("文件里有占位文本（人能看懂这里还没生成）",
              sc.GLOBAL_PLACEHOLDER in raw, raw[:120])
        got = sc.load_rolling(p)
        check("**读回来 global_summary 是空串**（占位不是数据）",
              got["global_summary"] == "", repr(got["global_summary"]))
        check("chapters 也是空", not got["chapters"], got["chapters"])

        # ---------- [2] 真机那个形状：只有占位、没章节 ----------
        print("\n[2] 真机形状逐字复现（34 字节那份）")
        p2 = tmp / "rolling_real.md"
        p2.write_text("# 滚动摘要\n## 全书摘要\n（待首次压缩生成）\n\n## 近期章节\n",
                      encoding="utf-8")
        g2 = sc.load_rolling(p2)
        check("真机文件读出的 global_summary 为空",
              g2["global_summary"] == "", repr(g2["global_summary"]))
        # 这是本用例存在的理由：旧实现会得到 "（待首次压缩生成）"
        check("**不会把占位当成摘要文本**",
              "待首次压缩" not in (g2["global_summary"] or ""),
              g2["global_summary"])

        # ---------- [3] 有真摘要时照常读出 ----------
        print("\n[3] 真摘要照常读出（修占位判据别把真数据也挡了）")
        p3 = tmp / "rolling_full.md"
        sc.write_rolling(p3, {"global_summary": "第1章: 主角登船；第2章: 到港",
                              "chapters": {1: "登船遇风暴", 2: "盐场初探"}})
        g3 = sc.load_rolling(p3)
        check("全书摘要读出", g3["global_summary"].startswith("第1章"),
              g3["global_summary"])
        check("章摘要读出（2 章）", len(g3["chapters"]) == 2, g3["chapters"])
        check("第 1 章内容正确", g3["chapters"].get(1) == "登船遇风暴",
              g3["chapters"].get(1))

        # ---------- [4] 追加一章后：章节进文件，占位仍在（全书摘要确实还没生成）----------
        print("\n[4] 追第一章摘要后：章节进文件；全书占位仍在（设计如此）")
        p4 = tmp / "rolling_append.md"
        sc.write_rolling(p4, {"global_summary": "", "chapters": {}})
        sc.append_chapter_summary(p4, 1, "第1章摘要：她在盐场遇见老人")
        raw4 = p4.read_text(encoding="utf-8")
        # 少于 rolling_window（默认 15）章时不会压缩，全书摘要确实是空的 ——
        # 占位文本保留是对的（人打开文件能看到"这里还没生成"）。
        # ⚠️ 关键是**读回来必须是空**，而不是文件里不许有占位（那会让文件看起来像坏了）。
        check("章节内容已进文件", "盐场" in raw4, raw4[-120:])
        g4 = sc.load_rolling(p4)
        check("读回来有第 1 章", 1 in g4["chapters"], g4["chapters"])
        check("chapter 内容正确", "盐场" in (g4["chapters"].get(1) or ""),
              g4["chapters"].get(1))
        check("**全书摘要读回来仍是空**（占位不算数据）",
              g4["global_summary"] == "", repr(g4["global_summary"]))

        # ---------- [5] 幂等：同一章重复追加不翻倍 ----------
        print("\n[5] 同章重复追加是覆盖语义（不是叠加）")
        sc.append_chapter_summary(p4, 1, "第1章摘要：改过的版本")
        g5 = sc.load_rolling(p4)
        check("仍只有 1 章", len(g5["chapters"]) == 1, g5["chapters"])
        check("内容是最后一次的", "改过的版本" in (g5["chapters"].get(1) or ""),
              g5["chapters"].get(1))

        # ---------- [6] 注入侧：占位不产生注入行 ----------
        print("\n[6] 注入侧：空摘要不该往提示词里塞一行")
        try:
            from utils import memory_policy as mp
            budget = {"rolling_chars": 2000, "rolling_window": 5}
            r_empty = mp._render_rolling({"global_summary": "", "chapters": {}},
                                         2, budget)
            txt_placeholder = r_empty[0] if isinstance(r_empty, tuple) else r_empty
            check("空摘要渲染结果里没有「全书」行",
                  "【全书】" not in txt_placeholder, repr(txt_placeholder[:120]))
            # 对照：有摘要时必须渲染出来（防止把函数改成永远返回空）
            r_real = mp._render_rolling(
                {"global_summary": "第1章: 登船", "chapters": {1: "风雨"}}, 2, budget)
            txt_real = r_real[0] if isinstance(r_real, tuple) else r_real
            check("有摘要时照常渲染「全书」行（判据没把功能改死）",
                  "【全书】" in txt_real, repr(txt_real[:120]))
            check("近期章节照常渲染", "【第1章】" in txt_real, repr(txt_real[:160]))
        except Exception as e:                                # noqa: BLE001
            check("memory_policy._render_rolling 可调用", False, e)

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
