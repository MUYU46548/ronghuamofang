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




# ====================================================================== P7
def case_essay_wiring():
    """写法指导真的按文体换了（不是只有判据口径）。"""
    import os
    import json
    import shutil
    import tempfile

    print("\n[P7] 写法指导按文体切换（stage4 文风块）")
    st_n, pa_n, w_n = WF.style_blocks("narrative")
    st_e, pa_e, w_e = WF.style_blocks("essay")
    check("P7 叙事文风块含「客观叙事」", "客观叙事" in st_n, st_n[:60])
    check("P7 散文文风块**不含**叙事口吻", "客观叙事" not in st_e, st_e[:80])
    check("P7 散文文风块写的是观察与感受", "观察与感受" in st_e, st_e[:80])
    check("P7 散文段落块允许一句一段", "一句一段是合法的" in pa_e, pa_e[:80])
    check("P7 叙事段落块要求按情节单元", "情节单元" in pa_n, pa_n[:80])
    check("P7 两者都不为空且 no warning", not w_n and not w_e and st_e and pa_e)
    st_l, pa_l, w_l = WF.style_blocks("letter")
    check("P7 未接的文体回落叙事并告警",
          st_l == st_n and "尚未接入" in w_l, (len(st_l), w_l[:40]))

    # 真跑一遍 stage4 的任务构造（essay vs narrative）
    tmp = Path(tempfile.mkdtemp(prefix="nf_style_"))
    cwd = os.getcwd()
    try:
        shutil.copytree(ROOT / "prompts", tmp / "prompts")
        (tmp / "config").mkdir()
        (tmp / "config" / "project.yaml").write_text(
            json.dumps({"book": {"name": "文体测试", "chapters": 4, "form": "essay"}}),
            encoding="utf-8")
        shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
        (tmp / "data" / "setting").mkdir(parents=True)
        (tmp / "data" / "setting" / "setting.json").write_text(json.dumps({
            "characters": [{"name": "苏芷", "role": "整理员", "traits": ["细心"]}],
            "world": {}, "plot_fragments": [], "timeline": [],
        }, ensure_ascii=False), encoding="utf-8")
        (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
        (tmp / "data" / "outline" / "chapters" / "01.md").write_text(
            "## 第1章 旧屋\n- 核心事件：她把旧屋的窗推开\n- 涉及角色：苏芷\n- 功能：开篇\n",
            encoding="utf-8")
        (tmp / "data" / "summaries").mkdir(parents=True)
        (tmp / "data" / "summaries" / "rolling.md").write_text(
            "# 滚动摘要\n## 全书摘要\n\n## 近期章节\n", encoding="utf-8")
        (tmp / "materials").mkdir()
        os.chdir(tmp)
        import yaml
        cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
        proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
        import stage4_writing as S4
        body = S4.build_chapter_task(cfg, proj, 1, "data/outline/chapters/01.md",
                                     "data/setting/setting.json",
                                     "data/summaries/rolling.md", [])
        check("P7 essay 项目的任务文本用散文文风",
              "观察与感受" in body and "客观叙事" not in body,
              [l for l in body.splitlines() if "客观" in l or "观察" in l][:3])
        check("P7 任务文本无未替换占位符",
              "{{style_rules}}" not in body and "{{para_rules}}" not in body)

        (tmp / "config" / "project.yaml").write_text(
            json.dumps({"book": {"name": "文体测试", "chapters": 4, "form": "narrative"}}),
            encoding="utf-8")
        proj2 = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
        body2 = S4.build_chapter_task(cfg, proj2, 1, "data/outline/chapters/01.md",
                                      "data/setting/setting.json",
                                      "data/summaries/rolling.md", [])
        check("P7 narrative 项目的任务文本用叙事文风", "客观叙事" in body2)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[P8] 三处判据按文体放宽")
    # 单段长文：叙事判退化，散文不判
    one_para = "他推开门。风把灯吹得忽明忽暗。院子里没有人，只有一条老狗趴在墙根。" * 20
    d1, _ = __import__("utils.verify_chapter", fromlist=["x"]).check_degenerate(one_para, para_structure=True)
    d2, _ = __import__("utils.verify_chapter", fromlist=["x"]).check_degenerate(one_para, para_structure=False)
    check("P8 叙事：单段长文被判退化", any("单段" in x or "段落过少" in x for x in d1), d1)
    check("P8 散文：同样文本不因段落结构判退化",
          not any("单段" in x or "段落过少" in x for x in d2), d2)

    import quality_checklist as QC
    txt = "她把窗推开，风进来，桌上的纸动了一下。" * 30
    setting = Path(tempfile.mkdtemp()) / "s.json"
    import json as _json
    setting.write_text(_json.dumps({"characters": [{"name": n} for n in ("甲", "乙", "丙")]},
                                   ensure_ascii=False), encoding="utf-8")
    q1 = QC.check_consistency(txt, 1, setting, require_character=True)
    q2 = QC.check_consistency(txt, 1, setting, require_character=False)
    check("P8 叙事：未提角色会报", any("未提及" in x for x in q1), q1)
    check("P8 散文：不报「未提及角色」", not any("未提及" in x for x in q2), q2)

    # stage3 strict：散文不要求小目标/钩子
    import stage3_chapter_outline as S3
    tmp2 = Path(tempfile.mkdtemp())
    essay_outline = tmp2 / "01.md"
    essay_outline.write_text(
        "## 第1章 旧屋\n- 核心事件：她把窗推开，看见院子里的老狗\n"
        "- 涉及角色：（无）\n- 功能：开篇\n", encoding="utf-8")
    ok_n, err_n = S3.check_chapter_outline(essay_outline, strict=True)
    ok_e, err_e = S3.check_chapter_outline(essay_outline, strict=True, new_fields=[])
    check("P8 叙事 strict：缺小目标/钩子判不合格", not ok_n, err_n)
    check("P8 散文 strict：同样文本通过（不要求这两项）", ok_e, err_e)
    shutil.rmtree(tmp2, ignore_errors=True)


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

    case_essay_wiring()

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
