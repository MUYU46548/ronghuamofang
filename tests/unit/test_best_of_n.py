# -*- coding: utf-8 -*-
"""Best-of-N 择优的回归锁（B2，2026-11-06）。

## 这个文件锁什么

“多写几版再挑”最容易做成两种假功能：

1. **只算总分不给理由** —— 用户不知道为什么推荐它（SP-1026 的老毛病，本次 screener 的判据就是
   每条扣分必须写得出是哪一项、差多少）；
2. **只留赢家** —— 输了的那几版被删掉，作者想“其实第 2 版更好”就无从比较。

判据：
- 三个候选（一好两烂）→ 排序正确，且理由里点得出具体维度与数值；
- 择优后**赢家写回正文**，三份候选都留在 `data/state/candidates/chNN/`，`result.json` 记分数与理由；
- 全部候选失败 → 正文**一个字都不动**（不许把半截稿当中选）；
- 采用候选的写端点与手改正文同族：非 GUI 来源必须 403。

用法：python tests/unit/test_best_of_n.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


from utils import candidate_score as cs                          # noqa: E402

GOOD = ("## 第1章 起风\n\n" + ("他推门进来的时候，屋檐上的水线正连成了串，"
        "地上积了一小滩暗色的水。她停下脚步，侧过脸朝窗外的天色看了一眼，"
        "随即收回视线，把手里的伞靠在门边。") * 3)
BAD_WORDS = ("## 第1章 起风\n\n" + "好。")           # 字数远低于下限 + 一句一段
REPEAT = ("## 第1章 起风\n\n" + ("同样的句子反复出现，同样的句子反复出现，"
          "同样的句子反复出现，同样的句子反复出现。\n\n") * 30)


def case_scoring_and_reasons():
    print("\n【1】三个候选（一好两烂）→ 排序正确 + 理由点得出具体项")
    rows = cs.score_candidates(
        [{"id": "cand1", "text": GOOD},
         {"id": "cand2", "text": BAD_WORDS},
         {"id": "cand3", "text": REPEAT}],
        target_words=[4000, 7200])
    check("好稿排第一", rows[0]["id"] == "cand1", [r["id"] for r in rows])
    check("三条都有理由（不是只给个数）",
          all(r["reasons"] for r in rows), [[r["id"], len(r["reasons"])] for r in rows])
    worst = [r for r in rows if r["id"] == "cand2"][0]
    check("烂稿的理由说得出“字数低于区间”",
          any("低于区间" in x for x in worst["reasons"]), worst["reasons"])
    rp = [r for r in rows if r["id"] == "cand3"][0]
    check("复读稿的理由说得出复读这一项",
          any("复读" in x for x in rp["reasons"]), rp["reasons"])
    check("分数是五维相加（可复算）",
          abs(sum(rows[0]["dims"].values()) - rows[0]["score"]) < 0.05,
          (sum(rows[0]["dims"].values()), rows[0]["score"]))
    check("同输入同输出（确定性）",
          [r["id"] for r in cs.score_candidates(
              [{"id": "cand1", "text": GOOD}, {"id": "cand2", "text": BAD_WORDS},
               {"id": "cand3", "text": REPEAT}], target_words=[4000, 7200])]
          == [r["id"] for r in rows])


def case_missing_data_is_neutral():
    print("\n【2】没区间 / 没词表 → 该项标“未评”，不默认满分也不凭空扣分")
    row = cs.score_text(GOOD, target_words=None, cliche_terms=None)
    check("没区间时字数项有说明", any("没有字数区间" in x for x in row["reasons"]),
          row["reasons"])
    check("没词表时病灶项标未评", any("未评" in x for x in row["reasons"]), row["reasons"])


def _mk_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_bon_"))
    (tmp / "data" / "chapters" / "refined").mkdir(parents=True)
    (tmp / "config").mkdir()
    (tmp / "data" / "chapters" / "refined" / "01.md").write_text(GOOD, encoding="utf-8")
    return tmp


def case_run_best_of_n_lands_winner():
    print("\n【3】择优：赢家写回正文 + 三份候选都留 + result.json 记分与理由")
    import utils.best_of_n as bon

    tmp = _mk_project()
    texts = [BAD_WORDS, REPEAT, GOOD]        # 故意让好稿排最后，验证“不是谁最后写谁赢”
    origin = bon.br.run_single_refine

    def fake_refine(cfg, proj, chapter, feedback, client=None, task_dir=None, dry_run=False):
        p = bon.br.pick_chapter_path(chapter)
        p.write_text(texts.pop(0), encoding="utf-8")
        return True, "ok", p

    bon.br.run_single_refine = fake_refine
    try:
        res = bon.run_best_of_n({}, {}, 1, "改一下", n=3, root=tmp)
    finally:
        bon.br.run_single_refine = origin

    check("择优成功", res.get("ok") is True, res.get("message"))
    check("选中的是最后的那个好稿（按分数，不按顺序）",
          (tmp / "data" / "chapters" / "refined" / "01.md").read_text(encoding="utf-8") == GOOD,
          res.get("chosen"))
    cdir = tmp / "data" / "state" / "candidates" / "ch01"
    check("三份候选都留在盘上", all((cdir / ("cand%d.md" % i)).exists() for i in (1, 2, 3)),
          sorted(p.name for p in cdir.glob("*.md")))
    check("原文也留了一份（择优前）", (cdir / "original.md").exists())
    meta = json.loads((cdir / "result.json").read_text(encoding="utf-8"))
    check("result.json 记了每个候选的分数", all("score" in c for c in meta["candidates"]),
          meta["candidates"])
    check("result.json 记了每个候选的理由",
          all(c.get("reasons") for c in meta["candidates"]))
    check("index.json 记了这章做过择优",
          (tmp / "data" / "state" / "candidates" / "index.json").exists())
    check("旧稿有备份（history/ch01_v*.md）",
          any((tmp / "data" / "chapters" / "history").glob("ch01_v*.md")),
          sorted(p.name for p in (tmp / "data" / "chapters" / "history").glob("*")))


def case_all_fail_keeps_text():
    print("\n【4】全部候选失败 → 正文一个字都不动")
    import utils.best_of_n as bon
    tmp = _mk_project()
    origin = bon.br.run_single_refine

    def fail_refine(cfg, proj, chapter, feedback, client=None, task_dir=None, dry_run=False):
        return False, "模型调用失败", None

    bon.br.run_single_refine = fail_refine
    try:
        res = bon.run_best_of_n({}, {}, 1, "改一下", n=2, root=tmp)
    finally:
        bon.br.run_single_refine = origin
    check("返回失败", res.get("ok") is False, res.get("message"))
    check("正文没被改成空稿/半截稿",
          (tmp / "data" / "chapters" / "refined" / "01.md").read_text(encoding="utf-8") == GOOD)


def case_apply_is_gui_only():
    print("\n【5】采用候选 = 与手改正文同族（非 GUI 来源必须 403）")
    from nf_api_domains import chapters as dom_ch

    class H:
        headers = {"X-Mofang-Source": "agent"}

    code, payload = dom_ch.handle_chapters_candidates_apply(H(), {"n": 1, "id": "cand1"})
    check("Agent 来源被拒（403）", code == 403, (code, payload))
    check("拒因说清是“只有作者能做”",
          "作" in str(payload) or "GUI" in str(payload) or "信任" in str(payload), payload)


def case_structure_evidence():
    """[5] 结构指标进择优结果（2026-11-17）：零 token、缺口径如实说缺、不 bootstrap。"""
    print("\n【5】结构指标：每版带六项证据；缺口径说缺，不往项目里写口径")
    import utils.best_of_n as bon

    APH = "人生就是这样。"                      # 7 字、独立成段、无对白、无逗号 → 格言式
    APHORISTIC = ("## 第1章 起风\n\n" + (APH + "\n\n") * 12)

    cfg_cal = {"version": 1, "criteria": [
        {"id": "J16", "value": 0.15}, {"id": "J17", "value": 2},
        {"id": "J18", "value": 5}, {"id": "J19", "value": 0.25},
        {"id": "J20", "value": 0.10}, {"id": "J21", "value": 220},
    ]}

    # A. 有口径：命中项被点名，needs_manual 只列那一版
    tmp = _mk_project()
    cal_dir = tmp / "data" / "judge"
    cal_dir.mkdir(parents=True)
    (cal_dir / "caliber.json").write_text(json.dumps(cfg_cal, ensure_ascii=False),
                                          encoding="utf-8")
    texts = [GOOD, APHORISTIC, REPEAT]
    origin = bon.br.run_single_refine

    def fake_refine(cfg, proj, chapter, feedback, client=None, task_dir=None, dry_run=False):
        p = bon.br.pick_chapter_path(chapter)
        p.write_text(texts.pop(0), encoding="utf-8")
        return True, "ok", p

    bon.br.run_single_refine = fake_refine
    try:
        res = bon.run_best_of_n({}, {}, 1, "改一下", n=3, root=tmp)
    finally:
        bon.br.run_single_refine = origin

    rows = res.get("candidates") or []
    ok_rows = [r for r in rows if r.get("status") == "ok"]
    check("A1 每版都带 structure", all(r.get("structure") for r in ok_rows),
          [(r.get("id"), bool(r.get("structure"))) for r in rows])
    check("A2 每版六项指标键齐全",
          all(set((r["structure"].get("metrics") or {})) ==
              {"question_ratio", "single_turn_streak", "speaker_spread",
               "aphorism_density", "dialogue_cover", "avg_para_len"} for r in ok_rows))
    hit = [r for r in ok_rows
           if "aphorism_density" in [t.get("code") for t in
                                     (r["structure"].get("triggered") or [])]]
    check("A3 格言体那版被点名为“需人工抽样”",
          len(hit) == 1 and res.get("needs_manual") == [hit[0]["id"]],
          (hit and hit[0]["id"], res.get("needs_manual")))
    check("A4 口径齐时不报缺", res.get("structure_criteria_missing") == [],
          res.get("structure_criteria_missing"))
    meta = json.loads((tmp / "data" / "state" / "candidates" / "ch01" / "result.json")
                      .read_text(encoding="utf-8"))
    check("A5 result.json 也落了结构证据（界面读得到）",
          all(c.get("structure") for c in meta["candidates"] if c.get("status") == "ok"))

    # B. 无口径：如实说缺，且**不** bootstrap 出文件（评分不该改项目）
    tmp2 = _mk_project()
    texts2 = [GOOD, APHORISTIC, REPEAT]
    origin = bon.br.run_single_refine

    def fake_refine2(cfg, proj, chapter, feedback, client=None, task_dir=None, dry_run=False):
        p = bon.br.pick_chapter_path(chapter)
        p.write_text(texts2.pop(0), encoding="utf-8")
        return True, "ok", p

    bon.br.run_single_refine = fake_refine2
    try:
        res2 = bon.run_best_of_n({}, {}, 1, "改一下", n=3, root=tmp2)
    finally:
        bon.br.run_single_refine = origin
    check("B1 缺口径 → 六条都列出（不回落默认值）",
          res2.get("structure_criteria_missing") ==
          ["J16", "J17", "J18", "J19", "J20", "J21"],
          res2.get("structure_criteria_missing"))
    check("B2 每个候选都说清“口径缺 X”",
          all(any("口径缺" in (n.get("reason") or "")
                  for n in (r.get("structure") or {}).get("na") or [])
              for r in res2.get("candidates") or [] if r.get("status") == "ok"))
    check("B3 没往项目里写口径文件（只读纪律）",
          not (tmp2 / "data" / "judge" / "caliber.json").exists())
    check("B4 缺口径时不会乱标“需人工抽样”", res2.get("needs_manual") == [],
          res2.get("needs_manual"))


def main():
    case_scoring_and_reasons()
    case_missing_data_is_neutral()
    case_run_best_of_n_lands_winner()
    case_all_fail_keeps_text()
    case_apply_is_gui_only()
    case_structure_evidence()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
