# -*- coding: utf-8 -*-
"""人工改动率的口径判据（2026-11-28）。

为什么要有它：改动率这种指标最危险的地方不是算得慢，而是**算错了还很像对的**。
一条 87.3% 的数摆在那里，没人会去验它；可如果口径搞错了（把标点算进字数、
把 autojunk 打开、把"只有一版"当成 0%），得出的数会稳定地偏，而且**永远不会报错**。

所以这里用构造样本把三件事钉死：

1. **只数汉字**：同一段文字只改半角标点 → 改动率必须**恰好 0**
   （否则 `text_norm` 的自动归一化会被算成"人改了"）。
2. **算法自洽**：完全复制 → 0；整段替换 → 接近 1；改一半 → 落在中间。
   三条一起才能证明 SequenceMatcher 的取用方式没错（只用 matching blocks）。
3. **没数据不许冒充 0%**：history 里只有一版、或目录不存在 → `status=no_data`，
   绝不返回 `char_displaced=0`（那会被读成"AI 写得完美，人一个字没改"）。

用法：python tests/unit/test_human_edit_rate.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import human_edit_rate as her  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def main():
    print("=" * 66)
    print("人工改动率的口径判据")
    print("=" * 66)

    # ---------------------------------------------------------------- 1. 只数汉字
    print("\n【1】只数汉字：改标点不算人的改动")
    base = "他推开门，雨还在下。院子里没有灯。"
    # 半角标点版本（模拟 text_norm 自动归一化前后的差别）
    final = "他推开门,雨还在下.院子里没有灯."
    r = her.compare(base, final)
    check("只改标点 → 改动率恰好 0", r["char_displaced"] == 0, r["char_displaced"])
    check("只改标点 → 句子改写率也是 0", r["sentences_rewritten"] == 0,
          r["sentences_rewritten"])
    # 他推开门(4) 雨还在下(4) 院子里没有灯(6) = 14 个汉字，标点 5 个不计
    check("汉字数不把标点算进去",
          r["chars_base"] == len(her.han_only(base)) == 14, r["chars_base"])

    # ---- 1b：只增不改也必须算作改动（本轮实测踩过的真缺陷）----
    # 首版分母只用基线字数：人往稿子里加了半页，基线每个字都还在 →
    # kept/n_base = 1 → 改动率报 0%，等于说"人一个字没改"。
    more = base + "他站了一会儿，忽然想起什么，又推门出去了。"
    r_add = her.compare(base, more)
    check("只往后加字 → 改动率 > 0（新增也是人的工作）",
          r_add["char_displaced"] is not None and r_add["char_displaced"] > 0,
          r_add["char_displaced"])
    check("只往后加字 → 删除量为 0、新增量 > 0",
          r_add["chars_removed"] == 0 and r_add["chars_added"] > 0,
          {"added": r_add["chars_added"], "removed": r_add["chars_removed"]})

    # ---------------------------------------------------------------- 2. 算法自洽
    print("\n【2】算法自洽：0 / 一半 / 全换")
    r_same = her.compare(base, base)
    check("完全没改 → 0", r_same["char_displaced"] == 0, r_same["char_displaced"])
    check("完全没改 → 整章重写为假", r_same["is_full_rewrite"] is False)

    # 把后半句整段换掉（前半句保留）
    half = "他推开门，雨还在下。屋子里的灯亮着，炉子上烧着水。"
    r_half = her.compare(base, half)
    check("改一半 → 落在 (0.1, 0.9) 之间",
          r_half["char_displaced"] is not None
          and 0.1 < r_half["char_displaced"] < 0.9, r_half["char_displaced"])

    # 完全不同的文字
    other = "风停了，海面上浮起一层薄雾，远处有人在敲钟。"
    r_other = her.compare(base, other)
    check("全换掉 → 改动率接近 1", r_other["char_displaced"] > 0.9,
          r_other["char_displaced"])
    check("全换掉 → 判为整章重写", r_other["is_full_rewrite"] is True)

    # ---------------------------------------------------------------- 3. 句子改写率
    print("\n【3】句子改写率：整句换掉即使字数不变也能算出来")
    s_base = "他推开门。雨还在下。院子里没有灯。"
    # 第 2 句换成一句**同样长度的**别的句子。长度由程序自己调，不靠手数汉字 ——
    # 手数错了会得到一条"看起来失败、其实是样本写错"的假红（本轮已踩两次）。
    orig_sent = "雨还在下"
    want_len = len(her.han_only(orig_sent))
    candidates = ["风从窗缝进来", "风已经停了", "雨点打在瓦", "雪落下来了",
                  "屋檐在滴水", "他站着没动", "没完没了", "雨停了又下"]
    repl = next((c for c in candidates
                 if len(her.han_only(c)) == want_len and c != orig_sent), None)
    check("找到一个与被替换句等长的候选句（样本前提）", repl is not None,
          {"want": want_len, "cands": [(c, len(her.han_only(c))) for c in candidates]})
    if repl:
        s_final = "他推开门。%s。院子里没有灯。" % repl
        r_s = her.compare(s_base, s_final)
        check("三句里改一句 → 句子改写率 ≈ 1/3",
              r_s["sentences_rewritten"] is not None
              and abs(r_s["sentences_rewritten"] - 1 / 3) < 0.02,
              r_s["sentences_rewritten"])
        check("句子改写率与字数无关（两稿汉字数完全相同）",
              r_s["chars_base"] == r_s["chars_final"],
              {"base": r_s["chars_base"], "final": r_s["chars_final"],
               "want_len": want_len, "repl": repl})

    # ---------------------------------------------------------------- 4. 没数据不冒充 0%
    print("\n【4】没有数据不许冒充 0%")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # 4a：history 目录根本不存在
        rep = her.collect(root)
        check("history 目录不存在 → status=no_data", rep["status"] == "no_data",
              rep["status"])
        check("history 目录不存在 → 不给改动率数字",
              rep["summary"] == {} and rep["chapters"] == [], rep["summary"])
        check("说明里讲清“还没人改过”", "改过" in rep["note"] or "历史" in rep["note"],
              rep["note"][:80])

        # 4b：有一版但只有一版（存过一次、没改过）
        hist = root / "data" / "chapters" / "history"
        hist.mkdir(parents=True)
        (hist / "ch01_v1.md").write_text("## 第1章\n\n他推开门，雨还在下。",
                                         encoding="utf-8")
        rep = her.collect(root)
        check("只有一版 → 仍是 no_data（不当 0%）", rep["status"] == "no_data",
              rep["status"])
        check("只有一版 → 记进 skipped 并说明原因",
              rep.get("skipped") and rep["skipped"][0]["chapter"] == 1,
              rep.get("skipped"))

        # 4c：两版 → 真的算出数
        (hist / "ch01_v2.md").write_text("## 第1章\n\n他推开门，雨还在下。院子里的灯亮着。",
                                         encoding="utf-8")
        rep = her.collect(root)
        check("两版 → status=ok", rep["status"] == "ok", rep["status"])
        check("两版 → 算出改动率且不是 0/1",
              rep["summary"]["char_displaced_avg"] not in (None, 0.0),
              rep["summary"])
        check("两版 → 记录基线版与最终版",
              rep["chapters"][0]["base_version"] == 1
              and rep["chapters"][0]["final_version"] == 2,
              rep["chapters"][0])

        # 4d：三版 → 用**第一版和最后一版**比，不是相邻两版
        (hist / "ch01_v3.md").write_text("## 第1章\n\n风停了。", encoding="utf-8")
        rep = her.collect(root)
        c = rep["chapters"][0]
        check("三版 → 基线取 v1、最终取 v3（净改动，不是相邻两版）",
              c["base_version"] == 1 and c["final_version"] == 3, c)

    # ---------------------------------------------------------------- 5. 输出契约
    print("\n【5】输出契约")
    rep_nodata = her.collect(Path(tempfile.gettempdir()) / "_no_such_ws_xyz")
    text = her.render_text(rep_nodata)
    check("无数据时文本输出明说不给数", "没有可用的数据" in text, text[:80])
    check("无数据时不出现 0.0% 这种会被误读的数",
          "平均改动率" not in text, text[:120])

    print("\n" + "-" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
