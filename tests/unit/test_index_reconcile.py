# -*- coding: utf-8 -*-
"""索引与实物对账自检（`utils.index_reconcile` + CLI，零 token）。

用户实测：`00_大纲收束索引.md` 声称 96 篇 / 9 维齐全，实际 41 篇；
`碎片填充计划.md` 登记 24 个日期，与碎片文件夹里 18 个日期无一重合。
本用例守五件事：

1. 篇数差异要报出来（声称 10 / 实际 3 → 差 7），不是只给一个“不一致”；
2. 日期跨目录比 —— 碎片登记表放在 raw 目录里时，仍能和碎片目录的实物对上，
   不许假报“无一重合”（那是把两个目录当成一个看）；
3. 元文件自己**不算**实物篇数；
4. 一致时不误报（声称 3 篇、实物 3 篇、日期全命中 → consistent）；
5. 没有登记表 → no_meta，不把“没东西可比”说成“对上了”。

专名纪律：夹具全部自造，不碰真实素材的专名。
用法：python tests/unit/test_index_reconcile.py
"""
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils.index_reconcile import reconcile  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        raw = root / "materials" / "raw"
        scraps = root / "materials" / "original_scraps"
        raw.mkdir(parents=True)
        scraps.mkdir(parents=True)

        def w(d, name, text):
            p = d / name
            p.write_text(text, encoding="utf-8")
            return p

        # ---------- 1. 声称 10 篇，实物 3 篇 ----------
        print("== 1. 篇数差异（声称 10 / 实际 3） ==")
        w(raw, "00_大纲收束索引.md",
          "# 遗声 · 大纲收束索引 v2（自动生成，未改动任何源文件）\n\n"
          "本批素材共 10 篇，按 9 维聚类，9 维齐全。\n\n"
          "| 维度 | 篇数 |\n|---|---|\n| 角色 | 3 |\n")
        w(raw, "角色卡甲.md", "# 角色卡\n\n- 名称：甲\n- 身份：巡道工\n")
        w(raw, "角色卡乙.md", "# 角色卡\n\n- 名称：乙\n- 身份：记账员\n")
        w(raw, "地点卡丙.md", "# 地点卡\n\n旧码头，雾天停摆。\n")

        res = reconcile(root, [("raw", "materials/raw")])
        m = res["meta_files"][0]
        print("    verdict=%s posts=%s" % (res["verdict"], m["diff"]["posts_actual"]))
        check("判为不一致", res["verdict"] == "mismatch")
        check("实物篇数不含元文件（3 篇）", m["diff"]["posts_actual"] == 3,
              m["diff"]["posts_actual"])
        check("篇数差报 -7", m["diff"]["posts_delta"] == -7, m["diff"]["posts_delta"])
        check("差异写进人话", any("差 -7" in i for i in m["issues"]), m["issues"])
        check("维度标为不可核验", m["diff"]["dimensions"] == 9
              and m["diff"]["dimensions_verifiable"] is False)

        # ---------- 2. 日期跨目录比（碎片登记表在 raw，实物在 scraps） ----------
        print("== 2. 日期跨目录匹配（不许假报“无一重合”） ==")
        w(raw, "碎片填充计划.md",
          "# 说明\n\n已使用的碎片在此登记，避免重复使用。\n\n# 登记列表\n"
          "2099-03-01 用掉\n2099-03-02 用掉\n2099-03-03 用掉\n")
        w(scraps, "碎片2099-3-1.md", "码头那天下了雾。\n")
        w(scraps, "碎片2099-3-2.md", "账本上一笔没对。\n")
        w(scraps, "碎片2099-3-3.md", "巡道工换了班。\n")

        res2 = reconcile(root, [("raw", "materials/raw"),
                                ("scraps", "materials/original_scraps")])
        plan = [x for x in res2["meta_files"] if x["name"] == "碎片填充计划.md"][0]
        print("    dates_claimed=%s matched=%s" %
              (plan["diff"]["dates_claimed"], plan["diff"]["dates_matched"]))
        check("碎片日期全部命中（3/3）", plan["diff"]["dates_matched"] == 3,
              plan["diff"]["dates_matched"])
        check("不报“找不到日期”", plan["diff"]["dates_missing"] == [],
              plan["diff"]["dates_missing"])
        check("该份登记表判为一致", plan["consistent"] is True, plan["issues"])
        check("整体仍因收束索引判不一致", res2["verdict"] == "mismatch")

        # ---------- 3. 一致场景 ----------
        print("== 3. 一致场景不误报 ==")
        with tempfile.TemporaryDirectory() as td2:
            r2 = Path(td2)
            (r2 / "materials" / "raw").mkdir(parents=True)
            d = r2 / "materials" / "raw"
            (d / "清单.md").write_text(
                "# 目录清单\n\n共 2 篇。\n\n2099-04-05\n2099-04-06\n", encoding="utf-8")
            (d / "甲2099-4-5.md").write_text("角色甲。\n", encoding="utf-8")
            (d / "乙2099-4-6.md").write_text("角色乙。\n", encoding="utf-8")
            r = reconcile(r2, [("raw", "materials/raw")])
            print("    verdict=%s" % r["verdict"])
            check("篇数日期全对上 → consistent", r["verdict"] == "consistent", r["summary"])

        # ---------- 4. 没有登记表 ----------
        print("== 4. 没有登记表 → no_meta ==")
        with tempfile.TemporaryDirectory() as td3:
            r3 = Path(td3)
            (r3 / "materials" / "raw").mkdir(parents=True)
            (r3 / "materials" / "raw" / "角色卡甲.md").write_text(
                "# 角色卡\n\n- 名称：甲\n", encoding="utf-8")
            r = reconcile(r3, [("raw", "materials/raw")])
            print("    verdict=%s" % r["verdict"])
            check("无登记表判 no_meta（不说成“对上了”）", r["verdict"] == "no_meta",
                  r["summary"])

        # ---------- 5. CLI 退出码 ----------
        print("== 5. CLI 退出码 ==")
        c1 = subprocess.run([sys.executable, str(REPO / "scripts" / "index_reconcile.py"),
                             "--root", str(root), "--json"],
                            capture_output=True, text=True, encoding="utf-8")
        print("    rc=%s" % c1.returncode)
        check("不一致 → 退出码 1", c1.returncode == 1, c1.returncode)
        try:
            payload = json.loads(c1.stdout)
            ok_json = payload["verdict"] == "mismatch"
        except Exception as e:                              # noqa: BLE001
            payload, ok_json = {}, False
            print("    JSON 解析失败: %s" % e)
        check("--json 输出机读结果", ok_json, str(payload)[:160])
        c2 = subprocess.run([sys.executable, str(REPO / "scripts" / "index_reconcile.py"),
                             "--root", str(td3)],
                            capture_output=True, text=True, encoding="utf-8")
        check("无登记表 → 退出码 0", c2.returncode == 0, c2.returncode)

    print("\n%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
