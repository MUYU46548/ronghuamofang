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


def main():
    case_scoring_and_reasons()
    case_missing_data_is_neutral()
    case_run_best_of_n_lands_winner()
    case_all_fail_keeps_text()
    case_apply_is_gui_only()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
