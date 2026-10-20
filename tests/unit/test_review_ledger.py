# -*- coding: utf-8 -*-
"""七维评审账本的自检（2026-10-20，台账二·「七维评审 + 原文举证」）。

## 主判据（正反两方都钉）

**必须算分**：
1. 七维齐全 + 每维引一句真实原句 → `complete=True`，均分 = 七维平均
2. 举证换标点形态（「」→“”、半角逗号）仍算命中 —— 不许把正常转述判成编造
3. 省略号引用（`前句……后句`）两段都在原文里 → 算命中
4. 三种 dims 形态（list / dict / 平铺）都读得出来

**必须不算分**（每一条都对应一种真实的偷懒/编造）：
5. 举证是编的（原文里没有）→ `verified=False`、`score=None`（不是扣分，是整维作废）
6. 举证太短（< 8 个有效字符，如「他点了点头」）→ 不算
7. 分数越界（11 / -1 / "abc"）→ 该维作废，即使举证是真的
8. 七维缺一维 → `missing` 记录 + `complete=False`（**不补默认分**）
9. 省略号引用里有一段是编的 → 整条不算
10. 模型编了本批不存在的章号 → 进 `unknown`，不进统计
11. 同章重复记录 → 只认第一条（不合并、不猜）

## 为什么这些断言值得写

旧 stage5 的判据是「check_report.md 有内容」——模型写满一屏漂亮话就通过了。
本模块把「通过」重新定义为**七维都能在原文里找到落点**。上面前四条防误伤
（正常引用被冤枉），后七条防放水（没读原文也能得分）。

用法：python tests/unit/test_review_ledger.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import review_ledger as R                                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (detail,)))


CHAPTER = (
    "## 第1章 锈锁\n\n"
    "他推开门的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。\n\n"
    "“你迟到了。”老者没有抬头，手指在桌沿上慢慢摩挲。\n\n"
    "院子里的老狗趴在墙根，尾巴动了一下，又沉下去。\n"
)
QUOTE_A = "他推开门的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。"
QUOTE_B = "“你迟到了。”老者没有抬头，手指在桌沿上慢慢摩挲。"
QUOTE_C = "院子里的老狗趴在墙根，尾巴动了一下，又沉下去。"


def entry(no=1, scores=None, quotes=None, dims=None):
    """造一条章节记录；默认七维全部 8 分、全部引 QUOTE_A。"""
    scores = scores or {}
    quotes = quotes or {}
    if dims is not None:
        return {"chapter": no, "dims": dims}
    return {"chapter": no, "dims": [
        {"dim": c, "score": scores.get(c, 8), "quote": quotes.get(c, QUOTE_A),
         "note": "（测试）"}
        for c in R.DIM_CODES
    ]}


# ====================================================================== P1
def case_happy():
    print("\n[P1] 七维齐全 + 真实举证 → 算分，且均分正确")
    r = R.check_chapter(entry(scores={"plot": 10, "timeline": 6}), CHAPTER, 1)
    check("P1 七维全部 verified", r["verified_count"] == 7, r["unverified"])
    check("P1 complete=True", r["complete"] is True)
    check("P1 均分 = 七维平均（10+6+8*5)/7 = 8.0", r["mean"] == 8.0, r["mean"])
    check("P1 结论由分数推导（10→ok / 6→warn）",
          r["dims"]["plot"]["verdict"] == "ok" and r["dims"]["timeline"]["verdict"] == "warn")
    check("P1 七维顺序与 DIMENSIONS 一致", list(r["dims"]) == R.DIM_CODES, list(r["dims"]))
    check("P1 七维数量 = 7（不多不少）", len(R.DIM_CODES) == 7, len(R.DIM_CODES))


def case_tolerance():
    print("\n[P2] 正常转述不许被冤枉（标点/引号形态差异 / 省略号引用）")
    ok1, _ = R.quote_ok("他推开门的时候，屋檐上的水线正连成了串", CHAPTER)
    check("P2 标点形态微调仍命中", ok1)
    ok2, _ = R.quote_ok("「你迟到了。」老者没有抬头", CHAPTER)
    check("P2 引号形态换成直角引号仍命中", ok2)
    ok3, _ = R.quote_ok("他推开门的时候，屋檐上的水线正连成了串……院子里的老狗趴在墙根", CHAPTER)
    check("P2 省略号跨句（两段都在原文）命中", ok3)
    ok4, why4 = R.quote_ok("他推开门的时候，屋檐上的水线正连成了串……老王在屋顶放风筝", CHAPTER)
    check("P2 省略号跨句（有一段落是编的）不算", not ok4, why4)
    # 多空白/换行
    ok5, _ = R.quote_ok("他推开门的时候，\n屋檐上的水线正连成了串", CHAPTER)
    check("P2 换行与空格不影响命中", ok5)


def case_fabrication():
    print("\n[P3] 编造 / 太短 / 分数越界 → 整维不算分")
    r = R.check_chapter(entry(quotes={"plot": "他掏出怀表看了一眼，转身走进了雨里。"}), CHAPTER, 1)
    check("P3 编造的举证 → verified=False", r["dims"]["plot"]["verified"] is False)
    check("P3 编造的举证 → score 置 None（不算评分，不是扣分）",
          r["dims"]["plot"]["score"] is None, r["dims"]["plot"]["score"])
    check("P3 但 raw_score 保留原值（可审计模型给了几分）",
          r["dims"]["plot"]["raw_score"] == 8, r["dims"]["plot"]["raw_score"])
    check("P3 该维 conclusion=unverified", r["dims"]["plot"]["verdict"] == "unverified")
    check("P3 整章不完整（一维未举证即不完整）", r["complete"] is False)

    r2 = R.check_chapter(entry(quotes={"plot": "他点了点头"}), CHAPTER, 1)
    check("P3 太短的举证不算（即使真的存在）",
          r2["dims"]["plot"]["verified"] is False and "太短" in r2["dims"]["plot"]["reason"],
          r2["dims"]["plot"]["reason"])

    for bad in (11, -1, "abc", None, True):
        r3 = R.check_chapter(entry(scores={"plot": bad}), CHAPTER, 1)
        check("P3 分数 %r 越界 → 该维作废" % (bad,),
              r3["dims"]["plot"]["verified"] is False and r3["dims"]["plot"]["score"] is None,
              r3["dims"]["plot"])
    # 字符串数字容错
    r4 = R.check_chapter(entry(scores={"plot": "7"}), CHAPTER, 1)
    check("P3 字符串 \"7\" 容错为整数 7", r4["dims"]["plot"]["score"] == 7, r4["dims"]["plot"])


def case_missing_and_dups():
    print("\n[P4] 缺维 / 编章号 / 重复章 / 形态兼容")
    dims = [d for d in entry()["dims"] if d["dim"] != "space"]
    r = R.check_chapter({"chapter": 1, "dims": dims}, CHAPTER, 1)
    check("P4 缺一维 → missing 记录", r["missing"] == ["space"], r["missing"])
    check("P4 缺的那维不补默认分（score=None）", r["dims"]["space"]["score"] is None)
    check("P4 缺维 → 整章不完整", r["complete"] is False)

    texts = {1: CHAPTER, 2: CHAPTER}
    payload = {"chapters": [entry(1), entry(99), entry(1, scores={"plot": 3})]}
    results, unknown = R.validate_payload(payload, texts)
    check("P4 不存在的章号进 unknown（不参与统计）", unknown == [99], unknown)
    check("P4 同章重复只认第一条", len(results) == 1 and results[0]["dims"]["plot"]["score"] == 8,
          [x["dims"]["plot"]["score"] for x in results])
    check("P4 缺评审记录的章可被点名", R.missing_chapters(results, texts) == [2],
          R.missing_chapters(results, texts))

    # dict 形态
    dd = {"chapter": 1, "dims": {c: {"score": 7, "quote": QUOTE_B} for c in R.DIM_CODES}}
    r2 = R.check_chapter(dd, CHAPTER, 1)
    check("P4 dims 用 dict 也认", r2["verified_count"] == 7, r2["unverified"])

    # 平铺形态
    flat = {"chapter": 1}
    for c in R.DIM_CODES:
        flat[c] = {"score": 7, "quote": QUOTE_C}
    r3 = R.check_chapter(flat, CHAPTER, 1)
    check("P4 维度平铺在章上也认", r3["verified_count"] == 7, r3["unverified"])


def case_parse_and_io():
    print("\n[P5] 解析容错 + 落盘 + 汇总")
    body = json.dumps({"chapters": [entry(1)]}, ensure_ascii=False)
    for label, text in (
        ("裸 JSON", body),
        ("代码围栏", "```json\n" + body + "\n```"),
        ("JSON 前后有废话", "好的，以下是评审结果：\n" + body + "\n以上。"),
        ("顶层是数组", json.dumps([entry(1)], ensure_ascii=False)),
    ):
        try:
            p = R.load_payload(text)
            ok = isinstance(p.get("chapters"), list) and len(p["chapters"]) == 1
        except R.LedgerError as e:                             # noqa: BLE001
            ok, p = False, str(e)
        check("P5 能解析：%s" % label, ok, p)

    for label, text in (("空字符串", ""), ("不是 JSON", "报告：都没问题。"),
                        ("缺 chapters", '{"note": "ok"}')):
        try:
            R.load_payload(text)
            ok = False
        except R.LedgerError:
            ok = True
        check("P5 明确拒绝：%s" % label, ok)

    tmp = Path(tempfile.mkdtemp())
    try:
        r = R.check_chapter(entry(1), CHAPTER, 1)
        p = R.write_ledger(tmp, r)
        check("P5 单章落盘为两位章号 NN.json", p.name == "01.json", p.name)
        back = json.loads(p.read_text(encoding="utf-8"))
        check("P5 落盘内容可读回且七维齐全", len(back["dims"]) == 7, back.get("dims"))
        s = R.write_summary(tmp, [r])
        check("P5 summary.json 落盘", (tmp / "summary.json").exists())
        check("P5 ledger.md 落盘", (tmp / "ledger.md").exists())
        check("P5 汇总均分 = 8.0", s["mean"] == 8.0, s["mean"])
        check("P5 汇总按维统计 verified 数",
              all(v["verified"] == 1 for v in s["dims"].values()),
              s["dims"])
        md = (tmp / "ledger.md").read_text(encoding="utf-8")
        check("P5 人读版含七维表头", "情节连贯性" in md and "| 章 |" in md, md[:120])

        # 不完整的章：ledger.md 必须写「未举证」，不许留空让人误以为满分
        r2 = R.check_chapter(entry(1, quotes={"plot": "编的句子编的句子编的句子"}), CHAPTER, 1)
        md2 = R.render_markdown([r2], R.summarize([r2]))
        check("P5 未举证的维度在表格里写明「未举证」", "未举证" in md2, md2[:200])
        check("P5 低分项单独成节", "低分项" not in md2, "8 分不该进低分项")
        r3 = R.check_chapter(entry(1, scores={"plot": 3}), CHAPTER, 1)
        md3 = R.render_markdown([r3], R.summarize([r3]))
        check("P5 3 分会进低分项并带原文引用",
              "低分项" in md3 and QUOTE_A[:10] in md3, md3[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("  七维评审账本自检（docs/收口台账-20261019.md 二·七维评审）")
    print("=" * 62)
    case_happy()
    case_tolerance()
    case_fabrication()
    case_missing_and_dups()
    case_parse_and_io()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
