# -*- coding: utf-8 -*-
"""文体（form）与「不适用」判据的自检（2026-10-20）。

## 起因

用户问了一句「如果根本不是传统叙事小说，会不会炸」。
会 —— 整条流水线的判据默认叙事小说，而且是硬编码。最典型的三处：

- 散文集按七维要求「情节连贯 / 人物关系 / 人设统一」→ 永远不完整；
- 散文没有对话，审美里的「对话区分度」永远拿不到分；
- 一首诗、一封短信会被 `verify_chapter` 的「段数 <6」判成退化。

本文件钉住两件事：**矩阵本身合法** + **不适用的项真的不计分、不算缺失、不进均值**。

## 主判据

1. 四种文体的维度/审美/章大纲字段都是合法 code 的子集（写错一个字母就红）
2. 默认（不写 form）= narrative，且 applicability 与「全适用」逐项一致
3. 未知 form → 回落 narrative **并给出 warning**（不静默）
4. essay：只有 commonsense 适用；审美没有 dialogue；无章大纲新字段；不做深化/分卷/伏笔
5. `check_chapter(dims=[...])`：不适用维度 `applicable=False`、不进 `unverified`、
   不进均值、不算 missing；只举证适用维度即 `complete=True`
6. 不传 dims（老调用方/老账本）行为与改动前**完全一致**
7. overrides 生效；未知键/未知值/类型错 **抛 ValueError**（静默忽略等于用户以为改了）
8. 渲染：不适用显示「—」/「不适用」，不显示「未举证」

用法：python tests/unit/test_writing_form.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import writing_form as WF                                # noqa: E402
from utils import review_ledger as R                                # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (str(detail)[:300],)))


CHAPTER = (
    "## 第1章 锈锁\n\n"
    "他推开门的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。\n\n"
    "院子里的老狗趴在墙根，尾巴动了一下，又沉下去。\n\n"
    "灯芯爆了一下，屋里暗了半格。\n"
)


def entry(dims=None, aes=None):
    return {"chapter": 1, "dims": dims or [], "aesthetic": aes or []}


def main():
    print("=" * 62)
    print("  文体与「不适用」判据自检")
    print("=" * 62)

    print("\n[P1] 矩阵合法性")
    check("P1 四种文体都在", set(WF.FORMS) == {"narrative", "letter", "episodic", "essay"},
          list(WF.FORMS))
    bad_dims, bad_aes, bad_fields = [], [], []
    for code, spec in WF.FORMS.items():
        if any(d not in R.DIM_CODES for d in spec["review_dims"]):
            bad_dims.append(code)
        if any(q not in R.AESTHETIC_CODES for q in spec["aesthetic"]):
            bad_aes.append(code)
        import stage3_chapter_outline as S3
        if any(f not in S3.NEW_FIELDS for f in spec["chapter_new_fields"]):
            bad_fields.append(code)
    check("P1 维度 code 全合法", not bad_dims, bad_dims)
    check("P1 审美 code 全合法", not bad_aes, bad_aes)
    check("P1 章大纲新字段全合法", not bad_fields, bad_fields)
    check("P1 narrative 全适用（七维 + 五问）",
          len(WF.FORMS["narrative"]["review_dims"]) == 7
          and len(WF.FORMS["narrative"]["aesthetic"]) == 5)
    check("P1 essay 只剩一维（常识）、审美无对话",
          WF.FORMS["essay"]["review_dims"] == ["commonsense"]
          and "dialogue" not in WF.FORMS["essay"]["aesthetic"])

    print("\n[P2] 解析与回落")
    code, warn = WF.resolve({})
    check("P2 不写 form → narrative 且无 warning", code == "narrative" and not warn, (code, warn))
    code2, warn2 = WF.resolve({"book": {"form": "essay"}})
    check("P2 写了 essay → 生效", code2 == "essay" and not warn2, (code2, warn2))
    code3, warn3 = WF.resolve({"book": {"form": "poetry"}})
    check("P2 未知文体 → 回落 narrative 且 warning 非空",
          code3 == "narrative" and "未知文体" in warn3, (code3, warn3))
    spec = WF.applicability({"book": {"form": "poetry"}})
    check("P2 applicability 也带 warning（不静默）", bool(spec.get("warning")), spec.get("warning"))

    print("\n[P3] 适用表与 overrides")
    n = WF.applicability({})
    check("P3 默认全适用", n["review_dims"] == R.DIM_CODES and n["aesthetic"] == R.AESTHETIC_CODES)
    check("P3 默认各开关为真",
          all(n[k] for k in ("deepen", "foreshadow", "volume",
                             "must_mention_character", "para_structure")))
    e = WF.applicability({"book": {"form": "essay"}})
    check("P3 essay 不做深化/分卷/伏笔",
          not e["deepen"] and not e["volume"] and not e["foreshadow"])
    check("P3 essay 不要求提角色名", e["must_mention_character"] is False)
    check("P3 说明里写明写法指导未接（不假装支持）", e["style_prompt_wired"] is False)

    ov = WF.applicability({"book": {"form": "essay",
                                   "form_overrides": {"review_dims": ["commonsense", "reference"],
                                                      "deepen": True}}})
    check("P3 overrides 覆盖维度", ov["review_dims"] == ["commonsense", "reference"], ov["review_dims"])
    check("P3 overrides 覆盖布尔", ov["deepen"] is True)
    check("P3 overrides 被记录（可审计）", ov["overrides_applied"] == ["review_dims", "deepen"],
          ov["overrides_applied"])

    for bad, label in (
        ({"book": {"form_overrides": {"nope": True}}}, "未知键"),
        ({"book": {"form_overrides": {"review_dims": ["plot", "nope"]}}}, "未知维度值"),
        ({"book": {"form_overrides": {"review_dims": "plot"}}}, "类型错（不是列表）"),
        ({"book": {"form_overrides": {"deepen": "yes"}}}, "类型错（不是布尔）"),
    ):
        try:
            WF.applicability(bad)
            raised = ""
        except ValueError as ex:
            raised = str(ex)
        check("P3 %s → 抛 ValueError（不静默忽略）" % label, bool(raised), raised)

    print("\n[P4] 不适用项真的不计分")
    # 散文：只有 commonsense 适用
    essay_entry = entry(
        dims=[{"dim": "commonsense", "score": 8, "quote": "院子里的老狗趴在墙根，尾巴动了一下，又沉下去。"}],
        aes=[{"q": "prose", "score": 8, "quote": "灯芯爆了一下，屋里暗了半格。"}],
    )
    r = R.check_chapter(essay_entry, CHAPTER, 1,
                        dims=e["review_dims"], questions=e["aesthetic"])
    check("P4 七维里只有一维适用 → 举证了就算完整", r["complete"] is True, r["unverified"])
    check("P4 不适用维度标 applicable=False",
          r["dims"]["plot"].get("applicable") is False, r["dims"]["plot"])
    check("P4 不适用维度**不进** unverified", r["unverified"] == [], r["unverified"])
    check("P4 不适用维度**不进** missing", r["missing"] == [], r["missing"])
    check("P4 均值只算适用维度", r["mean"] == 8.0, r["mean"])
    check("P4 记录不适用清单", len(r["not_applicable_dims"]) == 6, r["not_applicable_dims"])
    check("P4 审美里不适用的问题标 N/A",
          r["aesthetic"]["items"]["dialogue"].get("applicable") is False,
          r["aesthetic"]["items"]["dialogue"])
    check("P4 审美只缺适用的问题（其余三问）", r["aesthetic"]["missing"] == ["technique", "diction", "impact"],
          r["aesthetic"]["missing"])

    # 同一条记录，不按文体过滤 → 行为回到"七维全要"
    r2 = R.check_chapter(essay_entry, CHAPTER, 1)
    check("P4 不传 dims → 仍按七维全要（老行为不变）", r2["complete"] is False, r2["unverified"])
    check("P4 老行为下 missing 含六个缺维", len(r2["missing"]) == 6, r2["missing"])

    print("\n[P5] 汇总与渲染")
    s = R.summarize([r])
    check("P5 汇总的 unverified 只算适用章", s["dims"]["plot"]["unverified"] == 0,
          s["dims"]["plot"])
    check("P5 汇总记录不适用章数", s["dims"]["plot"]["not_applicable"] == 1,
          s["dims"]["plot"])
    md = R.render_markdown([r], s)
    check("P5 表格里不适用显示「—」", "| — |" in md, md[:400])
    check("P5 审美小节里不适用写「不适用」", "不适用（本文体没有这一项）" in md, md[-300:])

    print("\n[P6] 说明文案")
    d = WF.describe(WF.applicability({"book": {"form": "essay"}}))
    check("P6 describe 说明文体名", "散文" in d, d)
    check("P6 describe 说明七维只有几项适用", "适用" in d, d)
    check("P6 describe 说明不做深化/分卷/伏笔",
          "不做叙事深化段" in d and "不做分卷滚动" in d, d)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
