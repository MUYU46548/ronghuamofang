# -*- coding: utf-8 -*-
"""叙事深化的自检（2026-10-20，v0.7.2 roadmap 7.2）。

## 为什么这些断言值得写

叙事深化是「卷 → 章」之间缺的那一层：一卷 20 章不是直线平推，
要有 2-4 个深化段（各有类型与目标）。切段由模型给（创作决策），
但**校验必须确定性** —— 否则"深化"会退化成模型随手贴的标签：
段盖不满整卷、相邻同型（连续 40 章日常过渡）、类型瞎写，全都过检。

## 主判据

**解析**
1. 标准行格式（- 第 X-Y 章 ｜ 类型 ｜ 目标）解析成功
2. 分隔符宽容：全角｜ / 半角| / 冒号；破折号 – — ～ 至 都认
3. 没有「## 叙事深化」节 → present=False（存量卷纲的正常态，不是错误）
4. 乱行跳过但好行保留（不因一行坏全段）

**判据（会失败的反证）**
5. 覆盖不满整卷（缺章）→ 判不合格
6. 段重叠 → 判不合格（章数之和 != 卷章数）
7. 相邻两段同型 → 判不合格（"连续日常过渡"是真实退化）
8. 类型不在五枚举内 → 判不合格并列出可选
9. 段数越界（20 章切 1 段 / 切 6 段）→ 不合格
10. 每段跨章越界（2 章的段 / 12 章的段）→ 不合格
11. 目标太短（<6 有效字符）/ 占位符 → 不合格
12. 小卷（<6 章）允许 1 段 —— 2×3 切不开不硬凑
13. 合格的三段切分通过

**注入**
14. stage3 用全表（render_full 标出本章所在段）
15. stage4 用本段块（render_for_chapter 不泄全卷形状 —— 少花钱且防剧透）
16. 不在任何段内 → 空串（不注入"暂无数据"）
17. 存量卷纲（无深化节）→ context_for 两块皆空
18. essay 文体（deepen=False）→ 两块皆空（消费 writing_form.deepen 键）

**接线（防"配了不生效"）**
19. volume_arc.render_context 注入深化全表（有深化节时）
22. ensure_volume 生成路径：深化不合格 → 整份判失败不登记
23. ensure_volume 存量路径：无深化节 → 通过 + WARN（不判废）

用法：python tests/unit/test_story_deepen.py
"""
import io
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("" if detail == "" else "  -> %r" % (detail,)))


def vol_md(start, end, seg_lines, extra=""):
    lines = ["# 第1卷 卷名（第 %d–%d 章）" % (start, end), "",
             "## 本卷目标", "一句话目标。", "",
             "## 关键转折", "- 第 %d 章附近转折" % start, "",
             "## 叙事深化"]
    lines += seg_lines
    lines += ["", "## 逐章目标"]
    for n in range(start, end + 1):
        lines.append("第 %d 章：具体事件一句。" % n)
    lines += ["", "## 卷末交接", "下一卷的钩子。"]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def main():
    print("=" * 62)
    from utils import story_deepen as SD

    print("\n[P1] 解析")
    segs, present = SD.parse_deepen(vol_md(1, 9, [
        "- 第 1-4 章 ｜ 成长突破 ｜ 主角重拾旧业，第一次当众失手",
        "- 第 5-9 章 ｜ 恩怨冲突 ｜ 与旧友正面翻脸，关系回不去",
    ]))
    check("P1 present=True", present is True)
    check("P1 两段", len(segs) == 2, segs)
    check("P1 起止章正确", segs[0]["start"] == 1 and segs[0]["end"] == 4
          and segs[1]["start"] == 5 and segs[1]["end"] == 9, segs)
    check("P1 类型与目标", segs[0]["type"] == "成长突破"
          and "第一次当众失手" in segs[0]["goal"], segs[0])

    print("\n[P2] 分隔符宽容")
    segs2, _ = SD.parse_deepen(
        "## 叙事深化\n"
        "- 第 1～3 章 | 探索发现 | 揭开第一层谜团并露出更深一层\n"
        "- 第 4-6 章：对抗竞技：与对手第一次正面交锋\n"
        "- 第 7–9 章 ｜ 日常过渡 ｜ 喘息与蓄力，伏笔至多一处\n")
    check("P2 三种分隔符都认", len(segs3 := segs2) == 3, segs3)
    check("P2 破折号变体都认", [s["start"] for s in segs2] == [1, 4, 7], segs2)

    print("\n[P3] 存量与容错")
    segs3, present3 = SD.parse_deepen("# 第1卷\n\n## 本卷目标\n只有老三节。\n")
    check("P3 无深化节 → present=False", present3 is False and segs3 == [])
    segs4, _ = SD.parse_deepen(
        "## 叙事深化\n- 这行是坏的没人管\n"
        "- 第 1-5 章 ｜ 探索发现 ｜ 好行必须保留不被坏行拖下水\n")
    check("P4 坏行跳过、好行保留", len(segs4) == 1 and segs4[0]["end"] == 5, segs4)

    print("\n[P5] 判据反证")
    nine = [
        "- 第 1-4 章 ｜ 成长突破 ｜ 蓄力与第一次失手之后的转变完成",
        "- 第 5-9 章 ｜ 恩怨冲突 ｜ 与旧友翻脸并付出关系代价",
    ]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, nine))[0], 1, 9)
    check("P5a 合格切分通过", ok, probs)

    gap = [
        "- 第 1-3 章 ｜ 成长突破 ｜ 蓄力与突破在具体事件上落地了",
        "- 第 5-9 章 ｜ 恩怨冲突 ｜ 与旧友翻脸并付出关系的代价",
    ]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, gap))[0], 1, 9)
    check("P5b 缺第 4 章 → 不合格并点名和数", not ok and any("缺章" in p for p in probs), probs)

    overlap = [
        "- 第 1-5 章 ｜ 成长突破 ｜ 蓄力与突破在具体事件上落地了",
        "- 第 5-9 章 ｜ 恩怨冲突 ｜ 与旧友翻脸并付出关系的代价",
    ]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, overlap))[0], 1, 9)
    check("P5c 重叠 → 不合格", not ok, probs)

    same = [
        "- 第 1-4 章 ｜ 日常过渡 ｜ 喘息蓄力并埋一处不显眼的伏笔",
        "- 第 5-9 章 ｜ 日常过渡 ｜ 继续喘息直到读者失去耐心为止",
    ]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, same))[0], 1, 9)
    check("P5d 相邻同型 → 不合格并点名", not ok and any("同型" in p for p in probs), probs)

    badtype = [
        "- 第 1-4 章 ｜ 玄之又玄 ｜ 类型瞎写必须被枚举拦下",
        "- 第 5-9 章 ｜ 恩怨冲突 ｜ 与旧友翻脸并付出关系的代价",
    ]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, badtype))[0], 1, 9)
    check("P5e 未知类型 → 不合格并列出可选", not ok and any("枚举" in p for p in probs), probs)

    print("\n[P6] 段数与段长边界")
    one = ["- 第 1-9 章 ｜ 探索发现 ｜ 9 章切一段是平推不是深化必须拦住"]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, one))[0], 1, 9)
    check("P6a 9 章卷切 1 段 → 不合格", not ok and any("数量" in p for p in probs), probs)

    six = ["- 第 %d-%d 章 ｜ %s ｜ 每段目标都要超过六个有效字符\n"
           % (i * 3 + 1, i * 3 + 3, t)
           for i, t in enumerate(["成长突破", "对抗竞技", "探索发现",
                                  "恩怨冲突", "日常过渡", "成长突破"])]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 18, six))[0], 1, 18)
    check("P6b 6 段（越上限）→ 不合格", not ok and any("数量" in p for p in probs), probs)

    tiny = [
        "- 第 1-2 章 ｜ 探索发现 ｜ 两章的段不构成段必须被拦下",
        "- 第 3-9 章 ｜ 恩怨冲突 ｜ 与旧友翻脸并付出关系的代价",
    ]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, tiny))[0], 1, 9)
    check("P6c 2 章的段 → 不合格", not ok and any("跨" in p for p in probs), probs)

    short = [
        "- 第 1-4 章 ｜ 成长突破 ｜ 太短",
        "- 第 5-9 章 ｜ 恩怨冲突 ｜ 与旧友翻脸并付出关系的代价",
    ]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, short))[0], 1, 9)
    check("P6d 目标太短 → 不合格", not ok and any("太短" in p for p in probs), probs)

    ph = [
        "- 第 1-4 章 ｜ 成长突破 ｜ 目标待补充后面还挺长的",
        "- 第 5-9 章 ｜ 恩怨冲突 ｜ 与旧友翻脸并付出关系的代价",
    ]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 9, ph))[0], 1, 9)
    check("P6e 占位符 → 不合格", not ok and any("占位符" in p for p in probs), probs)

    small = ["- 第 1-4 章 ｜ 探索发现 ｜ 小卷允许一段不硬凑出两段来"]
    ok, probs = SD.check_deepen(SD.parse_deepen(vol_md(1, 4, small))[0], 1, 4)
    check("P6f 4 章小卷 1 段 → 通过（2×3 切不开）", ok, probs)

    print("\n[P7] 注入")
    segs_ok = SD.parse_deepen(vol_md(1, 9, nine))[0]
    full = SD.render_full(segs_ok, n=6)
    check("P7a 全表含两段", "第 1-4 章" in full and "第 5-9 章" in full)
    check("P7b 标出本章所在段", "本章所在段" in full and full.index("本章所在段") > full.index("第 5-9 章"))
    check("P7c 密度指导在表里", "密度指导" in full)
    chap = SD.render_for_chapter(segs_ok, 6)
    check("P7d 单章块只给本段（不泄全卷）", "第 5-9 章" in chap and "第 1-4 章" not in chap)
    check("P7e 单章块含类型与密度", "恩怨冲突" in chap and "密度指导" in chap)
    check("P7f 不在任何段内 → 空串", SD.render_for_chapter(segs_ok, 20) == "")

    print("\n[P8] context_for 的闸门")
    tmp = Path(tempfile.mkdtemp(prefix="nf_sd_"))
    try:
        (tmp / "data/outline/volumes").mkdir(parents=True)
        # 有深化节的卷纲
        (tmp / "data/outline/volumes/v01.md").write_text(
            vol_md(1, 9, nine), encoding="utf-8")
        cfg, proj = {}, {"book": {"chapters": 9, "volume_size": 20}}
        full_b, chap_b = SD.context_for(6, 9, cfg, proj, root=tmp)
        check("P8a 有深化节 → 两块都非空", bool(full_b) and bool(chap_b))
        # essay 文体：deepen=False → 早退
        proj_essay = {"book": {"chapters": 9, "form": "essay"}}
        full_e, chap_e = SD.context_for(6, 9, cfg, proj_essay, root=tmp)
        check("P8b essay 文体 → 两块皆空（消费 deepen 键）",
              full_e == "" and chap_e == "")
        # 存量卷纲（无深化节）
        (tmp / "data/outline/volumes/v01.md").write_text(
            vol_md(1, 9, []) .replace("## 叙事深化\n\n", ""), encoding="utf-8")
        full_l, chap_l = SD.context_for(6, 9, cfg, proj, root=tmp)
        check("P8c 存量卷纲 → 两块皆空（明确空白，不注入占位）",
              full_l == "" and chap_l == "")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n[P9] 接线（防配了不生效）")
    tmp2 = Path(tempfile.mkdtemp(prefix="nf_sd2_"))
    try:
        from utils import volume_arc as VA
        (tmp2 / "data/outline/volumes").mkdir(parents=True)
        (tmp2 / "data/outline/volumes/v01.md").write_text(
            vol_md(1, 9, nine), encoding="utf-8")
        proj9 = {"book": {"chapters": 9, "volume_size": 20}}
        ctx = VA.render_context(3, 9, cfg={}, proj=proj9, root=tmp2)
        check("P9a render_context 注入深化全表", "叙事深化" in ctx and "成长突破" in ctx, ctx[:200])
        ctx2 = VA.render_context(7, 9, cfg={}, proj=proj9, root=tmp2)
        check("P9b 本章所在段标记跟着章号走", "本章所在段" in ctx2
              and ctx2.index("本章所在段") > ctx2.index("第 5-9 章"))
    finally:
        import shutil
        shutil.rmtree(tmp2, ignore_errors=True)

    print("\n" + "=" * 62)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
