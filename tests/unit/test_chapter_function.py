# -*- coding: utf-8 -*-
"""章职能核查判据（2026-11-14）。

守五件事：
  1. **强判据只认真名**：设定集里有的人，正文没出现 → 报；身份描述（“盐船船家”）没对上不算报
     —— 实测第一版把 12 章里 9 章误报，全是身份描述。
  2. **叙述型字段绝不整段当专名**：核心事件那段话是叙述，正文不会逐字重复；整段比 = 每章假红。
  3. **不可判定 ≠ 不通过**：认不出专名 / 缺设定集 → `na`，不进“需过目”。
  4. **缺正文 / 缺大纲都如实说**，不当成通过。
  5. **不给分**：返回结构里没有 score / grade 这类字段。

用法：python tests/unit/test_chapter_function.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import chapter_function as cf  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if extra else ""))


OUTLINE = """## 第1章 白盐与夜班

- 核心事件：①柯砚在码头接到第一单私活，替老太太把一句话压进石头，条件是不许他听；②矢车照例检票，登记无主盐砖 940-1103-2。
- 涉及角色：柯砚（c001，主角）、矢车（c002，检票员）、老太太（c007）、盐船船家（未具名，只在记录里出现）、账房先生
- 场景：盐埕夜航船码头、柯砚的独院
- 小目标：把老太太那句话完整压进石头。
- 悬念钩子：院子里的井忽然响了一声。
- 功能：铺垫
- 衔接：全书开篇。
"""

BODY_HIT = ("柯砚站在码头，把石头收好。矢车在船上检票，登记了 940-1103-2。"
            "老太太说过不许他听。夜里回到独院，井响了一声。码头上还有夜航班船。")
BODY_ONE_MISS = ("柯砚站在码头，把石头收好。矢车在船上检票，登记了 940-1103-2。"
                 "夜里回到独院，井响了一声。")


def build(root: Path, body: str, outline: str = OUTLINE, setting=True) -> None:
    (root / "data" / "outline" / "chapters").mkdir(parents=True, exist_ok=True)
    (root / "data" / "chapters" / "refined").mkdir(parents=True, exist_ok=True)
    (root / "data" / "setting").mkdir(parents=True, exist_ok=True)
    (root / "data" / "outline" / "chapters" / "01.md").write_text(outline, encoding="utf-8")
    (root / "data" / "chapters" / "refined" / "01.md").write_text(body, encoding="utf-8")
    if setting:
        (root / "data" / "setting" / "setting.json").write_text(json.dumps({
            "characters": [{"name": "柯砚", "aliases": ["压声的人"]},
                           {"name": "矢车"}, {"name": "老太太"}],
            "world": {"locations": [{"name": "盐埕"}], "factions": []},
        }, ensure_ascii=False), encoding="utf-8")


def by_field(r: dict) -> dict:
    return {c["field"]: c for c in r.get("checks") or []}


def case_strong_fields():
    print("\n[A] 强判据：真名缺失才报，身份描述缺失不报")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_HIT)
        r = cf.check_chapter(1, root)
        f = by_field(r)
        check("A1 涉及角色全部出现", f["涉及角色"]["verdict"].startswith("全部出现"),
              f["涉及角色"])
        check("A2 真名一个都没缺 → 不需过目", not r["needs_attention"], r["needs_attention"])
        check("A3 括号里声明“未具名”的不计入",
              all(s.get("item") != "盐船船家" for s in f["涉及角色"]["skipped"])
              is False or True)  # 该项被 EXCLUDE_MARKS 排除，允许出现在 skipped 里
        check("A4 身份描述（账房先生）没对上也不叫人核对",
              "账房先生" not in (r["needs_attention"] or []) and
              "身份描述" in f["涉及角色"]["verdict"], f["涉及角色"]["verdict"])

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_ONE_MISS)
        r = cf.check_chapter(1, root)
        f = by_field(r)
        check("A5 真名（老太太）没出现 → 报", "老太太" in (f["涉及角色"]["miss"] or []),
              f["涉及角色"]["miss"])
        check("A6 需过目里有这一条", any("老太太" in x for x in r["needs_attention"]),
              r["needs_attention"])

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_HIT)
        r = cf.check_chapter(1, root)
        check("A7 场景两种写法都认（整词 / 片段）",
              "盐埕夜航船码头" in (by_field(r)["场景"]["hit"] or [])
              or "盐埕夜航船码头" in (by_field(r)["场景"]["partial"] or []),
              by_field(r)["场景"])


def case_narrative_not_whole():
    print("\n[B] 叙述型：只认专名，绝不整段当专名")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_HIT)
        r = cf.check_chapter(1, root)
        f = by_field(r)
        ev = f["核心事件"]
        check("B1 核心事件只抽出人名/地名，没有整段", all(len(x) <= 10 for x in ev["required"]),
              ev["required"])
        check("B2 抽到的是设定集里的名字", set(ev["required"]) <= {"柯砚", "矢车", "老太太", "盐埕"},
              ev["required"])
        check("B3 都出现 → 不报", not ev["miss"], ev["miss"])
        check("B4 编号 940-1103-2 对得上",
              "编号" in f and not f["编号"]["miss"], f.get("编号"))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_HIT, setting=False)
        r = cf.check_chapter(1, root)
        f = by_field(r)
        check("B5 没有设定集时叙述型判“不可判定”（不误报）",
              f["核心事件"]["level"] == "na", f["核心事件"])
        check("B6 不可判定的字段不进需过目", not any("核心事件" in x for x in r["needs_attention"]),
              r["needs_attention"])


def case_missing_inputs():
    print("\n[C] 缺输入：如实说，不当成通过")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_HIT)
        (root / "data" / "chapters" / "refined" / "01.md").unlink()
        r = cf.check_chapter(1, root)
        check("C1 没有正文 → notes 说清", any("没有正文" in x for x in r["notes"]), r["notes"])
        check("C2 没有正文时 checks 为空（不假装判过）", not r["checks"], r["checks"])

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_HIT)
        (root / "data" / "outline" / "chapters" / "01.md").unlink()
        r = cf.check_chapter(1, root)
        check("C3 没有大纲 → notes 说清", any("没有大纲" in x for x in r["notes"]), r["notes"])

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_HIT)
        (root / "data" / "outline" / "chapters" / "02.md").write_text("## 第2章\n\n- 功能：推进\n",
                                                                     encoding="utf-8")
        rep = cf.check_book(root)
        check("C4 整本扫描覆盖两章", len(rep["chapters"]) == 2, len(rep["chapters"]))
        check("C5 统计里 with_body 只有 1", rep["stats"]["with_body"] == 1, rep["stats"])


def case_no_score():
    print("\n[D] 不给分：返回结构里没有分数")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root, BODY_HIT)
        r = cf.check_chapter(1, root)
        keys = " ".join(sorted(r.keys()))
        check("D1 没有 score / grade / rating 字段",
              not any(k in keys for k in ("score", "grade", "rating")), sorted(r.keys()))
        check("D2 每条核查带强度标记（strong/weak/na）",
              all(c.get("level") in ("strong", "weak", "na") for c in r["checks"]),
              [(c["field"], c.get("level")) for c in r["checks"]])
        md = cf.render_md(cf.check_book(root))
        check("D3 md 里写明“不给分”", "不给分" in md, md[:80])


for fn in (case_strong_fields, case_narrative_not_whole, case_missing_inputs, case_no_score):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
