# -*- coding: utf-8 -*-
"""判定引擎（P0）的确定性判据 —— 全程离线、零 token。

对照 `docs/product-20261028/02-产品定义.md` §四 P0 验收线：
F01 体检 / F02 伪具体功能 / F03 可替换 / F03b 勾选闸门 / F04 章纲密度 /
F05 未落地 / F06 三档 / F07 缺陷单计数 / F08 口径版本 / F13 单一出口 / F14 回测台。

用法：python tests/unit/test_judge_engine.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.judge import backtest as B                       # noqa: E402
from utils.judge import caliber as C                        # noqa: E402
from utils.judge import destroy as D                        # noqa: E402
from utils.judge import records as R                        # noqa: E402
from utils.judge import rules as RU                         # noqa: E402
from utils.judge import text as T                           # noqa: E402
from utils.judge import verdict as V                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


# ---------------------------------------------------------------- 夹具
OUTLINE = """## 第1章 夜雨
- 核心事件：
  1. 他在档案室盖章
  2. 他核对回执编号
  3. 他在日志上划改
  4. 他发现一张字条
- 涉及角色：沈砚
- 功能：推进
- 衔接：承接上一章；钩子：字条上只有半个名字
"""

BODY = """## 第1章 夜雨

他在档案室盖章。他把回执压平，拿起印章，在纸角上按下去。

他核对回执上的编号。他一行一行往下看，看到第三行停住。

他在日志上划改。他把笔尖朝上，放回笔槽。

他抽出一张字条。他把字条摊在桌上，压在回执底下。
"""

CAL = C.values(C.bootstrap(tempfile.mkdtemp(prefix="nf_cal_")))


def main():
    print("=== A. 文本层：分段 / 分句 / 定位 ===")
    paras = T.paragraphs(BODY)
    check("段号从 1 起且跳过标题", len(paras) == 4 and "第1章" not in paras[0][:6],
          paras[0][:12])
    sents = [s for _p, _s, s in T.iter_sentences(BODY)]
    check("分句保留句末标点", sents and sents[0].endswith("。"), sents[0])
    loc = T.locate(BODY, "他把笔尖朝上，放回笔槽。")
    check("定位到段号+句序（第 3 段）", loc == (3, 2), loc)
    check("引文校验：编造的句子定位不到", T.locate(BODY, "他推开门走了。") is None)
    check("字数口径与非引擎一致（去空白与符号）", T.count_cn_words(BODY) > 0)

    print("\n=== B. F01 体检（AC：普通句 0 条；长句/复读可定位） ===")
    plain = "他坐下，把茶推过去。「喝吗？」她摇头。"
    m0 = RU.body_metrics(plain, 1, CAL)
    check("普通文本 F01 报 0 条缺陷", len(m0["defects"]) == 0, m0["defects"])
    long_body = "他把整段话一口气说完" * 9 + "。"
    m1 = RU.body_metrics(long_body, 1, CAL)
    check("超长句记一条 J03 且带段号", any(d["rule_id"] == "J03" and d["paragraph_index"] == 1
                                             for d in m1["defects"]), m1["defects"])
    rep = "他没有说话。"
    m2 = RU.body_metrics("他站着。" + rep + rep + rep, 1, CAL)
    j04 = [d for d in m2["defects"] if d["rule_id"] == "J04"]
    check("复读句记一条且 count=3", len(j04) == 1 and j04[0]["count"] == 3,
          [(d["rule_id"], d["count"]) for d in m2["defects"]])

    print("\n=== C. F02/F03 伪具体（AC-09-1 / AC-10-1 / 替换测试） ===")
    three = ("十次呼吸之内落不了章。留三次余量。笔尖朝上，放回笔槽。")
    fc = RU.fake_concrete(three, 1, CAL)
    hit_sents = [x["sentence"] for x in fc]
    check("三句全部召回（自造刻度 ×2 + 器物特写 ×1）", len(fc) >= 3, hit_sents)
    check("召回条目全部带段号与原句",
          all(x["paragraph_index"] >= 1 and x["sentence"] for x in fc))
    check("未裁定前状态是「待裁定」，不记为合格",
          all(x.get("status") == "待裁定" for x in fc),
          [x.get("status") for x in fc])
    sk = RU.skeleton_hits("十次呼吸之内落不了章。留三次余量。", 1, CAL)
    check("骨架命中 2 条", len(sk) == 2, len(sk))
    sk0 = RU.skeleton_hits("他推开门，把伞靠在墙边。", 1, CAL)
    check("普通句骨架命中 0（不误报）", len(sk0) == 0, sk0)
    repl = RU._replace_num("十次呼吸之内落不了章")
    check("换数字后骨架仍成立（不是词表）",
          RU.skeleton_of(repl) is not None and repl != "十次呼吸之内落不了章", repl)

    print("\n=== D. F04/F05 章纲（AC-11 / AC-12 / 约束 2） ===")
    ev4 = RU.outline_events(OUTLINE)
    check("章纲解析出 4 条事件", len(ev4) == 4, ev4)
    dens = RU.outline_density(ev4, CAL)
    check("原稿：章纲密度虚高（3 条连续动作）", dens["flagged"] is True, dens.get("reason"))
    ev_del = [ev4[0], ev4[1], ev4[3]]          # 删掉「日志划改」这条连续动作
    dens3 = RU.outline_density(ev_del, CAL)
    check("删掉一条事件后不再报密度虚高", dens3["flagged"] is False, dens3.get("reason"))
    land_ok = RU.outline_landing(ev4, BODY, CAL)
    check("原稿：F05 不报（正文落地 4 条）",
          not land_ok["missing_in_body"] and not land_ok["extra_in_body"], land_ok)
    land_gap = RU.outline_landing(ev_del, BODY, CAL)
    check("删事件版本：报「章纲缺项（正文多出事件）」",
          bool(land_gap["extra_in_body"]), [b["paragraph_index"] for b in land_gap["extra_in_body"]])
    body_missing = BODY.replace("他抽出一张字条。他把字条摊在桌上，压在回执底下。", "")
    land_miss = RU.outline_landing(ev4, body_missing, CAL)
    check("正文没写章纲事件：报「正文未落地章纲」",
          bool(land_miss["missing_in_body"]), [x["event"] for x in land_miss["missing_in_body"]])
    check("删事件版本报「章纲缺项」在第 3 段",
          any(b["paragraph_index"] == 3 for b in land_gap["extra_in_body"]),
          [b["paragraph_index"] for b in land_gap["extra_in_body"]])
    d_def = RU.outline_landing_defects(land_gap, 1)
    check("F05 缺陷条目是独立规则 id（不与 F04 混）",
          d_def and d_def[0]["rule_id"] in ("J05b", "J05c"), d_def[:1])

    print("\n=== E. R15 三档结论（AC-15） ===")
    res = RU.analyze_chapter(BODY, OUTLINE, 1, CAL)
    v_none = V.decide(res, None, None, CAL)
    check("没勾事件 → 结论为 null + 待作者勾选",
          v_none["verdict"] is None and v_none["status"] == "待作者勾选", v_none["status"])
    v1 = V.decide(res, None, {"events": 1, "chosen": "信息占有"}, CAL)
    check("作者认定 1 件事 → 需重写本章", v1["verdict"] == V.VERDICT_REWRITE, v1["verdict"])
    check("需重写本章的依据不是字数",
          all("字数" not in r["text"] for r in v1["reasons"]), v1["reasons"])
    v4 = V.decide(res, None, {"events": 4, "chosen": "信息占有"}, CAL)
    check("缺陷段占比 ≥20% → 需定点重写且给段号",
          v4["verdict"] == V.VERDICT_SPOT and v4["paragraph_set"], v4["paragraph_set"])
    check("结论里没有分数/quality 字段",
          not any(k in v4 for k in ("score", "quality", "total")), list(v4))
    dbl = {"compressed_twice": True, "words": {"raw": 5967, "checked": 2186, "refined": 2183},
           "shrink": {"raw->checked": 0.634}}
    vb = V.decide(res, dbl, {"events": 3, "chosen": "关系"}, CAL)
    check("三稿被压缩两次 → 不可判定 B，不给档位",
          vb["verdict"] == V.VERDICT_UNDECIDED and vb["reason_code"] == "B", vb["status"])
    check("B 的理由带 5967→2186→2183 证据",
          "5967" in vb["reasons"][0]["text"] and "2183" in vb["reasons"][0]["text"],
          vb["reasons"][0]["text"])

    print("\n=== F. F07 缺陷单计数一致 ===")
    c = res["counts"]
    manual = len([d for d in res["defects"] if d["rule_id"].startswith(("J03", "J04", "J05"))])
    check("F01 计数 = J03/J04/J05 条目数", c["F01"] == manual, (c["F01"], manual))
    check("缺陷单条数 = F01+F02+F03+F05",
          len(res["defects"]) == c["F01"] + c["F02"] + c["F03"] + c["F05"],
          (len(res["defects"]), c))
    check("F04 单独计数且不进缺陷单（章级建议）",
          c["total"] == len(res["defects"]) + (1 if res["outline"]["density"]["flagged"] else 0), c)

    print("\n=== G. R02 三稿对照（销毁检测 + 缺版语义） ===")
    raw = "第%d句，他把回执压平。" * 40
    checked = "第%d句，他把回执压平。" * 12
    refined = "第%d句，他把回执压平。" * 11
    comp = D.compare(raw, checked, refined, CAL)
    check("raw→checked 缩减 70% → 判销毁", comp["destroyed"] is True, comp["shrink"])
    check("两次压缩 → compressed_twice（判定落 B 的依据）", comp["compressed_twice"] is True,
          comp["shrink"])
    check("删除句带段号", comp["deleted"] and comp["deleted"][0]["paragraph_index"] >= 1,
          comp["deleted"][:1])
    miss = D.compare(raw, None, None, CAL)
    check("缺版标「该版不存在」而不是记 0",
          miss["present"]["checked"] is False and miss["words"]["checked"] is None, miss["present"])
    empty = D.compare(None, None, None, CAL)
    check("三版全缺 → 不可判定：无正文", empty["ok"] is False and "无正文" in empty["reason"])

    print("\n=== H. 口径版本（R04/F08）与记录单一出口（F13/F03b/F12） ===")
    tmp = Path(tempfile.mkdtemp(prefix="nf_judge_"))
    try:
        cal1 = C.load(tmp)
        v1fp = cal1["fingerprint"]
        ok, d1 = C.update(tmp, {"J10": 0.20})
        check("不改参数重复保存 → 版本不变", d1.get("changed") is False, d1)
        ok, d2 = C.update(tmp, {"J10": 0.30})
        check("改阈值 → 版本 +1 且指纹变",
              d2["version"] == cal1["version"] + 1 and d2["fingerprint"] != v1fp, d2.get("version"))
        check("旧版本进 history 且只读", C.load(tmp)["history"][0]["version"] == cal1["version"])
        bad = C.load(tmp)
        bad["criteria"].append({"id": "J99", "name": "没有消费方", "kind": "阈值",
                                "value": 1, "consumer": "nobody"})
        C.save_raw(tmp, bad)
        ok_ready, why = C.require_ready(C.load(tmp))
        check("空判据（无消费方）→ 不许判定", ok_ready is False and "空判据" in why, why)

        # F03b / F12
        ev = R.set_events(tmp, 3, [0, 1], {"0": "关系", "1": "目标"}, "第 12 段")
        check("勾选落盘：events/chosen 齐备",
              ev["events"] == 2 and ev["chosen"] == "关系", ev)
        ok_s, _ = R.set_sentence(tmp, 3, 5, "他把笔尖朝上，放回笔槽。", "打中了", "")
        check("裁定理由为空 → 拒绝落库", ok_s is False)
        ok_s2, rec = R.set_sentence(tmp, 3, 5, "他把笔尖朝上，放回笔槽。", "打中了", "读到这儿停了")
        check("带理由的裁定落库且带章/段/句",
              ok_s2 and rec["chapter"] == 3 and rec["paragraph_index"] == 5, rec)
        check("记录只落 records.json 一个文件",
              (tmp / "data" / "judge" / "records.json").exists()
              and not (tmp / "data" / "judge" / "records2.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n=== I. F14 回测台（10 章 / 只对 raw / 零 token） ===")
    tmp = Path(tempfile.mkdtemp(prefix="nf_backtest_"))
    try:
        raw_dir = tmp / "data" / "chapters" / "raw"
        out_dir = tmp / "data" / "outline" / "chapters"
        raw_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)
        for n in range(1, 11):
            (raw_dir / ("%02d.md" % n)).write_text(BODY, encoding="utf-8")
            (out_dir / ("%02d.md" % n)).write_text(
                OUTLINE.replace("第1章", "第%d章" % n), encoding="utf-8")
        rep = B.run(tmp, book_name="回测夹具书")
        check("回测跑通且覆盖 10 章", rep.get("ok") and rep["summary"]["total"] == 10,
              rep.get("error"))
        check("总账 0 个「可以开始逐字改」（基线 0/10）", rep["summary"]["ok"] == 0,
              rep["summary"])
        check("报告头带固定串①和②",
              B.NOTICE_BACKTEST in rep["notices"] and B.NOTICE_NO_ARTIFACT in rep["notices"],
              rep["notices"])
        check("口径版本与指纹进报告头",
              rep["caliber"]["version"] and rep["caliber"]["fingerprint"], rep["caliber"])
        m = rep["matrix"][0]["cells"]
        check("判据矩阵无空格（数值或显式不适用）",
              all(m.get(k) is not None and m.get(k) != "" for k in B.MATRIX_ROWS),
              [k for k in B.MATRIX_ROWS if m.get(k) in (None, "")])
        check("回测只读 raw（checked/refined 不存在时标缺稿语义）",
              rep["chapters"][0]["destroy"]["shrink"].get("raw->checked") is None,
              rep["chapters"][0]["destroy"])
        after = {p: p.stat().st_mtime for p in (tmp / "data").rglob("*.json")}
        B.run(tmp, book_name="回测夹具书")
        after2 = {p: p.stat().st_mtime for p in (tmp / "data").rglob("*.json")}
        changed = [str(p) for p in after2 if after.get(p) != after2[p]]
        check("跑一次只动 records.json（F13 单一出口）",
              changed and all(x.endswith("records.json") for x in changed), changed)
        src = "\n".join((ROOT / "scripts" / "utils" / "judge" / f).read_text(encoding="utf-8")
                        for f in ("rules.py", "verdict.py", "backtest.py", "destroy.py",
                                  "records.py", "caliber.py"))
        import re as _re
        model_imports = _re.findall(r"^\s*(?:from|import)\s+[\w.]*llm_client", src, _re.M)
        check("引擎源码零模型依赖（不 import llm_client / 不调 make_client）",
              not model_imports and "make_client(" not in src, model_imports)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for n in FAIL:
        print("    -", n)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
