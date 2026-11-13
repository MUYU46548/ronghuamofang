# -*- coding: utf-8 -*-
"""三态对照（three_state_diff）判据 —— 零 token、临时目录。

守两件事：
  1. **能抓到**：raw 有、checked 没有的段落必须出现在"被删"里（不能因为段落被改写就整段漏掉）；
  2. **不越界**：归类只到“对白 / 长旁白 / 近似重复 / 未分类”，绝不出现"离题""与设定冲突"
     这类需要读全书的判断（脚本只给证据，判决归作者）。

用法：python tests/unit/test_three_state_diff.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import three_state_diff as tsd  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if extra else ""))


def build_book(root: Path) -> None:
    raw = root / "data" / "chapters" / "raw"
    checked = root / "data" / "chapters" / "checked"
    refined = root / "data" / "chapters" / "refined"
    for d in (raw, checked, refined):
        d.mkdir(parents=True, exist_ok=True)

    keep = "他推开门，屋里的灯还亮着。"
    gone_narration = ("她心里清楚，这一切都不是偶然，那些年积攒下来的疲惫在此刻涌上来，"
                      "让她几乎站不住脚，可她还是要撑着，把最后那句话说完。"
                      "窗外的雨声一阵紧过一阵，像有人在檐下反复敲着同一块铁皮。")
    gone_dialogue = "“你先回去。”"
    (raw / "01.md").write_text(
        "## 第1章 甲\n\n" + keep + "\n\n" + gone_narration + "\n\n" + gone_dialogue + "\n",
        encoding="utf-8")
    (checked / "01.md").write_text("## 第1章 甲\n\n" + keep + "\n", encoding="utf-8")
    (refined / "01.md").write_text("## 第1章 甲\n\n" + keep + "他顺手关灯。\n", encoding="utf-8")

    # 第二章只有 raw：缺层要在 summary/chapters 里如实反映，不许当成"删光了"
    (raw / "02.md").write_text("## 第2章 乙\n\n只有原始稿的一段。\n", encoding="utf-8")


def case_detect_and_report():
    print("\n[A] 抓得到 + 只给证据")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_book(root)
        rep = tsd.run(root)

        check("A1 两章都进来了（缺层不算丢章）", [c["chapter"] for c in rep["chapters"]] == [1, 2],
              [c["chapter"] for c in rep["chapters"]])
        ch1 = rep["chapters"][0]
        pair = [p for p in ch1["pairs"] if p["from"] == "raw"][0]
        texts = [x["text"] for x in pair["removed"]]
        check("A2 raw 有、checked 没有的两段都被抓到", len(texts) == 2, texts)
        check("A3 对白段带 dialogue 线索",
              any("dialogue" in x["clues"] for x in pair["removed"]))
        check("A4 长旁白带 long_narration 线索",
              any("long_narration" in x["clues"] for x in pair["removed"]))
        check("A5 保留段没被误报（灯还亮着）",
              not any("灯还亮着" in t for t in texts), texts)

        clues = set(rep["summary"]["by_clue"])
        check("A6 线索表里没有越界判断（离题/设定冲突）",
              not ({"off_topic", "conflict", "irrelevant"} & clues), clues)
        check("A7 线索只有约定那几类",
              clues <= {"dialogue", "long_narration", "near_duplicate", "unclassified"}, clues)

        # 落盘
        out = root / "out"
        rc = tsd.main(["--root", str(root), "--out", str(out)])
        md = (out / "three_state.md").read_text(encoding="utf-8")
        js = json.loads((out / "three_state.json").read_text(encoding="utf-8"))
        check("A8 退出码 0 且写出 json+md", rc == 0 and (out / "three_state.json").exists())
        check("A9 md 里写明“证据不是判决”", "证据清单，不是判决" in md)
        check("A10 md 汇总带净变化列", "净变化" in md)
        check("A11 json 里 chapter 2 只有 raw 层",
              js["chapters"][1]["layers"].get("raw") == 1
              and "checked" not in js["chapters"][1]["layers"])


def case_no_write_into_book_archive():
    print("\n[B] 不往用户的书档里塞文件")
    with tempfile.TemporaryDirectory() as td:
        book = Path(td) / "某书__20260101_000000"
        raw = book / "chapters" / "raw"
        checked = book / "chapters" / "checked"
        for d in (raw, checked):
            d.mkdir(parents=True)
        (raw / "01.md").write_text("## 第1章\n\n甲。\n\n乙。\n", encoding="utf-8")
        (checked / "01.md").write_text("## 第1章\n\n甲。\n", encoding="utf-8")
        rc = tsd.main(["--root", str(book)])
        check("B1 书档布局（chapters/ 在根下）也能读到", rc == 0)
        check("B2 没在里面建 data/review", not (book / "data").exists(),
              list(book.iterdir()))


for fn in (case_detect_and_report, case_no_write_into_book_archive):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
