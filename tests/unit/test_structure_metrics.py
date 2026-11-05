# -*- coding: utf-8 -*-
"""阶段 5 结构指标自检（`utils.judge.structure`，零 token 纯确定性）。

试跑 P1-3 的现实：七维 8.14 分、4 批 complete=True，同一份文本 1269 句对白仅 33 句
问号，几乎全是单回合问答。读者一眼看出的问题，账本一个字都没说 —— 因为七维只验
“引句能否逐字命中原文”，不判断好坏。本用例守六件事：

1. 可疑文本真的会触发（并说清是哪一项、值多少、线在哪）；
2. 正常文本不误报（阈值不是摆设）；
3. **阈值只从口径表来** —— 把 J16 改严，同一份文本立刻触发（证明代码里没写死数字）；
4. 缺口径时如实标 N/A，不回落默认值；
5. 无对白文体（散文/书信）判 N/A，不被判“对白少”；
6. 同一个 8.14 不许再出现：报告只给指标与“需人工抽样”，不给文学分/总分。

专名纪律：夹具全部自造，不碰真实素材的专名。
用法：python tests/unit/test_structure_metrics.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils.judge import caliber  # noqa: E402
from utils.judge import structure as st  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


# 可疑：12 组纯“问→答”，答句一样长，另有两处极短独立段
BAD = ("雾从码头那头漫过来。\n\n"
       + "\n\n".join(['陆九说：“船到了吗？”\n\n阿七说：“到了。”' for _ in range(12)])
       + "\n\n天没有亮。\n")

# 正常：有追问、有长答、两个角色句长明显不同（甲 3 句短、乙 3 句长）
GOOD = """雾从码头那头漫过来，像谁把一整匹灰布抖开。

陆九说：“船到了吗？”

阿七说：“到了。昨天也到了，你没看见，那天你在河那头看人下棋，一直看到日头偏西才回来。”

陆九说：“吃水多深？”

阿七说：“不到三尺。回程的时候五尺有余，中间那两个时辰它去了哪儿，我账上没有记，也不打算替你编一个。”

陆九问：“他蹲了多久？”

阿七说：“他蹲到天亮。冻得直咳嗽，却一句怨言也没有，只在走的时候回头看了一眼那条船。”
"""

NO_DIALOGUE = """雾从码头那头漫过来，像谁把一整匹灰布抖开，又慢慢收拢。

镇子醒得很晚。屋檐下的水一滴一滴落在石阶上，把青苔砸出浅浅的坑。

他站在门口抽烟，烟头一明一暗，像在替谁数着时辰。
"""


def main():
    limits = caliber.values({"criteria": caliber.FACTORY})

    # ---------- 1. 可疑文本触发 ----------
    print("== 1. 可疑文本：单回合问答 / 句长同质 / 短段过多 ==")
    m_bad = st.measure(BAD)
    trig, na = st.evaluate(m_bad, limits)
    codes = {t["code"] for t in trig}
    print("   指标:", json.dumps(m_bad, ensure_ascii=False))
    check("连续单回合问答被抓到", "single_turn_streak" in codes, sorted(codes))
    check("连续组数 ≥ 10 组", m_bad["single_turn_streak"] >= 10, m_bad["single_turn_streak"])
    check("角色句长同质被抓到", "speaker_spread" in codes, m_bad["speaker_spread"])
    check("极短独立段过多被抓到", "aphorism_density" in codes, m_bad["aphorism_density"])
    check("触发项带值/线/方向（不是一句“不合格”）",
          all(t.get("value") is not None and t.get("limit") is not None
              and t.get("direction") in ("min", "max") for t in trig), trig[:2])

    # ---------- 2. 正常文本不误报 ----------
    print("== 2. 正常文本：不误报 ==")
    m_good = st.measure(GOOD)
    trig2, na2 = st.evaluate(m_good, limits)
    print("   指标:", json.dumps(m_good, ensure_ascii=False))
    check("正常文本不触发任何项", trig2 == [], trig2)
    check("两个角色句长差 ≥ 8 字（有区分度）",
          (m_good["speaker_spread"] or 0) >= 8, m_good["speaker_spread"])
    check("问号占对白 ≥ 15%", (m_good["question_ratio"] or 0) >= 0.15,
          m_good["question_ratio"])

    # ---------- 3. 阈值只在口径表里 ----------
    print("== 3. 阈值只从口径表来（代码里不许写死） ==")
    strict = dict(limits)
    strict["J16"] = 0.9                    # 问号占对白要 ≥90% 才算合格线
    trig3, _ = st.evaluate(m_good, strict)
    check("把 J16 改严 → 同一份文本立刻触发问号项",
          any(t["code"] == "question_ratio" for t in trig3),
          [t["code"] for t in trig3])

    print("== 4. 缺口径如实标 N/A，不回落默认值 ==")
    missing = {k: v for k, v in limits.items() if k != "J16"}
    trig4, na4 = st.evaluate(m_good, missing)
    check("缺 J16 → 该指标进 N/A 并说明“口径缺 J16”",
          any(x["code"] == "question_ratio" and "口径缺 J16" in x["reason"] for x in na4),
          [x for x in na4 if x["code"] == "question_ratio"])
    check("缺 J16 时不许凭空触发问号项",
          not any(t["code"] == "question_ratio" for t in trig4))

    # ---------- 5. 无对白文体 ----------
    print("== 5. 无对白：对白类指标判 N/A ==")
    m_nd = st.measure(NO_DIALOGUE)
    trig5, na5 = st.evaluate(m_nd, limits)
    na_codes = {x["code"] for x in na5}
    check("对白类四项判 N/A",
          {"question_ratio", "single_turn_streak", "speaker_spread",
           "dialogue_cover"} <= na_codes, sorted(na_codes))
    check("不因“没有对白”触发对话段占比", not any(t["code"] == "dialogue_cover" for t in trig5))
    check("段长仍然算得出来（不适用 ≠ 全废）", isinstance(m_nd["avg_para_len"], float),
          m_nd["avg_para_len"])

    # ---------- 6. 不出文学分 ----------
    print("== 6. 不给文学分、不合成总分 ==")
    check("指标里没有任何 score/grade 字段",
          not any(k in m_good for k in ("score", "grade", "overall", "mean")))
    md = st.render_markdown({"caliber_version": 1, "dimensions": st.DIMENSIONS,
                             "chapters": [{"chapter": 7, "metrics": m_bad, "na": [],
                                           "triggered": trig, "needs_sampling": True}],
                             "summary": {"chapters": 1, "needs_sampling": [7],
                                         "metrics_mean": {}, "triggered_count": {}},
                             "missing_criteria": []})
    check("报告写明“需人工抽样”并点名章节", "需人工抽样" in md and "| 7 |" in md, md[-160:])
    check("报告写明不给分", "不判合格、不合成总分" in md)

    # ---------- 7. 确定性 + 落盘 ----------
    print("== 7. 确定性 / collect / 落盘 ==")
    check("同一文本两次测量完全一致", st.measure(GOOD) == st.measure(GOOD))
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cdir = root / "data" / "chapters" / "raw"
        cdir.mkdir(parents=True)
        (cdir / "01.md").write_text(BAD, encoding="utf-8")
        (cdir / "02.md").write_text(GOOD, encoding="utf-8")
        (cdir / "03.md").write_text(NO_DIALOGUE, encoding="utf-8")
        (root / "data" / "judge").mkdir(parents=True)
        caliber.save_raw(root, {"version": 1, "fingerprint": caliber.fingerprint(caliber.FACTORY),
                                "criteria": caliber.FACTORY, "history": []})
        res = st.collect(root)
        print("   汇总:", json.dumps(res["summary"], ensure_ascii=False))
        check("扫到 3 章", res["summary"]["chapters"] == 3, res["summary"]["chapters"])
        check("只有第 1 章需人工抽样", res["summary"]["needs_sampling"] == [1],
              res["summary"]["needs_sampling"])
        check("缺判据列表为空（口径齐全）", res["missing_criteria"] == [],
              res["missing_criteria"])
        out = st.write(root / "data" / "review", res)
        check("落盘 structure.json", out.exists())
        check("落盘 structure.md", (root / "data" / "review" / "structure.md").exists())

        # 缺口径的老工作区：如实报缺，不静默用默认值
        dump = {"J16", "J17", "J18", "J19", "J20", "J21"}
        old = {"version": 1, "fingerprint": "x",
               "criteria": [c for c in caliber.FACTORY if c["id"] not in dump],
               "history": []}
        caliber.save_raw(root, old)
        res2 = st.collect(root)
        check("老口径表 → 明确报缺 J16-J21",
              res2["missing_criteria"] == ["J16", "J17", "J18", "J19", "J20", "J21"],
              res2["missing_criteria"])
        check("缺口径时仍返回章节指标（只是不判越线）",
              res2["summary"]["chapters"] == 3 and res2["summary"]["needs_sampling"] == [],
              res2["summary"]["needs_sampling"])

    print("\n%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
