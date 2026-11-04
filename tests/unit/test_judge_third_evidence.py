# -*- coding: utf-8 -*-
"""M4=B 第三条证据（模型先出、作者终裁）的确定性判据 —— 全程离线、零 token。

对应 `docs/questionnaires/20261104-宪法层-r2.md` 的 M4 验收线：
- **判据清单标明产出方**（每条：谁产的 / 谁终裁），老表能显式补登记；
- **模型越层代裁被拒**（写档位/终裁/来源一律 400，一条都不落盘）；
- **证据必须能核对**（编造引文、段号错、无理由 —— 整批拒收）；
- 终裁只认作者动作（采纳/推翻/驳回都留痕，不许覆盖已终裁的），
  且所有写入仍只落 `records.json`（F13）。

“真跑一次模型验合格产出”不在这里 —— 那是要花钱的活，见
`scripts/judge_feelings.py --chapter N`（`--dry-run` 零调用）。

用法：python tests/unit/test_judge_third_evidence.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.judge import caliber as C                        # noqa: E402
from utils.judge import feelings as F                       # noqa: E402
from utils.judge import records as R                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


BODY = """## 第3章 夜雨

他把伞收了，站在屋檐下，看雨水顺着瓦沿一线一线落下来。

“你回来晚了。”她说。他没有答话，只把手里那袋热的东西往她怀里一塞。
"""

GOOD = [
    {"paragraph_index": 1,
     "sentence": "他把伞收了，站在屋檐下，看雨水顺着瓦沿一线一线落下来。",
     "feeling": "打中了", "reason": "用一个动作把等待写实了"},
    {"paragraph_index": 2, "sentence": "“你回来晚了。”她说。",
     "feeling": "没打中", "reason": "对话标签太直白，没有情绪"},
]


def tmp_root():
    d = Path(tempfile.mkdtemp(prefix="nf_m4_"))
    (d / "data" / "chapters" / "raw").mkdir(parents=True)
    (d / "data" / "chapters" / "raw" / "03.md").write_text(BODY, encoding="utf-8")
    return d


def old_caliber(root):
    """模拟存量工作区：v1 的 14 条，既没有 producer 也没有 J15。"""
    doc = C.load(str(root))
    keep = ("id", "name", "kind", "value", "consumer", "note")
    doc["criteria"] = [{k: v for k, v in c.items() if k in keep}
                       for c in doc["criteria"] if c["id"] != "J15"]
    doc["version"] = 1
    doc["history"] = []
    C.save_raw(str(root), doc)
    return doc


def judge_dir_files(root):
    p = Path(root) / "data" / "judge"
    return sorted(x.name for x in p.glob("*")) if p.exists() else []


def main():
    print("=== A. 判据清单标明产出方（M4 验收①） ===")
    ids = [c["id"] for c in C.FACTORY]
    check("出厂表每条都有 producer", all(c.get("producer") in C.PRODUCERS for c in C.FACTORY),
          [c["id"] for c in C.FACTORY if c.get("producer") not in C.PRODUCERS])
    check("出厂表每条都有 settled_by",
          all(c.get("settled_by") in C.SETTLERS for c in C.FACTORY),
          [c["id"] for c in C.FACTORY if c.get("settled_by") not in C.SETTLERS])
    by_id = {c["id"]: c for c in C.FACTORY}
    check("J12（事件数）产出方 = 作者",
          by_id["J12"]["producer"] == "author" and by_id["J12"]["settled_by"] == "author",
          by_id["J12"])
    check("J15（第三条证据）产出方 = 模型、终裁 = 作者",
          by_id["J15"]["producer"] == "model" and by_id["J15"]["settled_by"] == "author",
          by_id["J15"])
    check("J15 的消费方是 model.feelings", by_id["J15"]["consumer"] == "model.feelings",
          by_id["J15"]["consumer"])
    check("出厂表 15 条（14 + 第三条证据）", len(C.FACTORY) == 15, len(C.FACTORY))

    root = tmp_root()
    doc = C.load(str(root))
    desc = {x["id"]: x for x in C.describe(doc)}
    check("describe 带 producer_name", desc["J01"]["producer_name"] == C.PRODUCERS["rules"],
          desc["J01"]["producer_name"])
    check("describe 带 settled_by_name", "作者" in desc["J12"]["settled_by_name"],
          desc["J12"]["settled_by_name"])
    check("模型类判据的值不可在界面直接改（editable=False）",
          desc["J15"]["editable"] is False and desc["J01"]["editable"] is True,
          (desc["J15"]["editable"], desc["J01"]["editable"]))
    check("third_evidence() 只返回模型产的判据",
          [x["id"] for x in C.third_evidence(doc)] == ["J15"],
          [x["id"] for x in C.third_evidence(doc)])
    check("新表不需要补登记", C.needs_upgrade(doc)["needed"] is False,
          C.needs_upgrade(doc))

    print("\n=== B. 老表：缺产出方必须显式补登记，不许默认顶上 ===")
    root2 = tmp_root()
    old_caliber(root2)
    old = C.load(str(root2))
    need = C.needs_upgrade(old)
    check("老表 needs_upgrade=True", need["needed"] is True, need)
    check("老表缺 J15", "J15" in need["missing_factory_ids"], need["missing_factory_ids"])
    check("老表 14 条都缺产出方", len(need["criteria_without_producer"]) == 14,
          len(need["criteria_without_producer"]))
    odesc = {x["id"]: x for x in C.describe(old)}
    check("缺登记就显示“未登记产出方”（不拿出厂表顶替）",
          odesc["J01"]["producer_name"] == "（未登记产出方）", odesc["J01"]["producer_name"])
    # 改过阈值的表：补登记不许覆盖作者改过的值
    C.update(str(root2), {"J10": 0.3})
    before = C.values(C.load(str(root2)))
    ok, up = C.upgrade(str(root2))
    after = C.values(C.load(str(root2)))
    check("upgrade 成功", ok and up["changed"] is True, up.get("message"))
    check("upgrade 产新版本（版本 +1）", up["version"] >= 3, up["version"])
    check("upgrade 的 filled 覆盖 14 条 × 2 字段", len(up["filled"]) == 28, len(up["filled"]))
    check("upgrade 不覆盖作者改过的阈值", before["J10"] == after["J10"] == 0.3,
          (before["J10"], after["J10"]))
    check("upgrade 后 J15 补上", "J15" in after, sorted(after))
    check("upgrade 后不再需要补登记", C.needs_upgrade(C.load(str(root2)))["needed"] is False)
    ok2, up2 = C.upgrade(str(root2))
    check("upgrade 幂等（第二次 changed=False）", ok2 and up2["changed"] is False, up2.get("message"))
    hist_versions = [h.get("version") for h in C.load(str(root2)).get("history") or []]
    check("旧版进 history（可回退）", len(hist_versions) >= 2, hist_versions)

    print("\n=== C. 越层：模型只能出证据，不能出结论 ===")
    root3 = tmp_root()
    ok, bad = F.assert_no_overreach({"chapter": 3, "items": [], "verdict": "可开始逐字改"})
    check("顶层带 verdict → 越层", ok is False and "verdict" in bad[0], bad)
    ok, bad = F.assert_no_overreach({"chapter": 3, "items": [], "status": "accepted"})
    check("顶层带 status → 越层", ok is False, bad)
    ok, bad = F.assert_no_overreach({"chapter": 3, "items": [], "note": "x"})
    check("白名单字段不误报", ok is True, bad)
    overreach_cases = [
        ("verdict", "需重写本章"), ("status", "accepted"), ("settled_by", "model"),
        ("source", "author"), ("book_reject", {"reason": "不行"}), ("quality", 9),
    ]
    for key, val in overreach_cases:
        item = dict(GOOD[0], **{key: val})
        ok, res = F.propose(str(root3), 3, [item], model="m")
        check("条目带 %s → 拒收" % key, ok is False and "越层" in str(res.get("error")),
              str(res.get("error"))[:80])
    check("越层被拒后盘上一个提案都没有",
          R.load(str(root3)).get("proposals") is None, R.load(str(root3)).get("proposals"))
    ok, res = F.propose(str(root3), 3, [dict(GOOD[0], extra="x")], model="m")
    check("未知字段 → 拒收（不静默忽略）", ok is False and "未知字段" in str(res.get("error")),
          str(res.get("error"))[:80])

    print("\n=== D. 证据必须能核对（编造 / 段号 / 理由） ===")
    cases = [
        ("编造引文", [dict(GOOD[0], sentence="他从来没有回过头。")], "定位不到"),
        ("段号错", [dict(GOOD[0], paragraph_index=2)], "段落号对不上"),
        ("理由太短", [dict(GOOD[0], reason="好")], "理由太短"),
        ("感受不在三选一", [dict(GOOD[0], feeling="很棒")], "感受只能是"),
        ("理由为空", [dict(GOOD[0], reason="   ")], "理由太短"),
    ]
    for name, items, needle in cases:
        ok, res = F.propose(str(root3), 3, items, model="m")
        check("%s → 拒收" % name, ok is False and needle in str(res.get("error")),
              str(res.get("error"))[:90])
    ok, res = F.propose(str(root3), 3, [GOOD[0]] * 31, model="m")
    check("一次 >30 条 → 拒收", ok is False and "最多" in str(res.get("error")),
          str(res.get("error"))[:60])
    ok, res = F.propose(str(root3), 99, GOOD, model="m")
    check("没有这章的 raw → 拒收", ok is False and "没有第 99 章" in str(res.get("error")),
          str(res.get("error"))[:60])

    print("\n=== E. 提案 → 作者终裁（模型提案不自动变成作者结论） ===")
    root4 = tmp_root()
    ok, res = F.propose(str(root4), 3, GOOD, model="deepseek-x")
    check("合法提案落盘 2 条", ok and len(res["added"]) == 2, res)
    snap = F.snapshot(str(root4), 3)
    check("候选状态全部是 pending", snap["counts"]["pending"] == 2, snap["counts"])
    check("模型提案**不会**自动变成作者裁定", snap["author_verdicts"] == 0,
          snap["author_verdicts"])
    check("跨句引文（“你回来晚了。”她说。）被接受（退化定位，仍逐字核对）",
          any(p["sentence"] == GOOD[1]["sentence"] for p in snap["pending"]),
          [p["sentence"] for p in snap["pending"]])
    ok, res = F.propose(str(root4), 3, GOOD, model="deepseek-x")
    check("重复提案不重复落盘", ok and res["added"] == [] and res["duplicated"] == 2, res)

    ids = [p["id"] for p in snap["pending"]]
    ok, res = F.settle(str(root4), 3, [{"id": ids[0], "action": "reject", "reason": ""}])
    check("驳回缺理由 → 拒", ok is False and "理由" in str(res.get("error")),
          str(res.get("error"))[:80])
    ok, res = F.settle(str(root4), 3, [{"id": ids[0], "action": "override", "feeling": "打中了"}])
    check("推翻缺理由 → 拒", ok is False, str(res.get("error"))[:80])
    ok, res = F.settle(str(root4), 3, [{"id": ids[0], "action": "delete"}])
    check("非法动作 → 拒", ok is False and "accept/override/reject" in str(res.get("error")),
          str(res.get("error"))[:80])

    ok, res = F.settle(str(root4), 3, [
        {"id": ids[0], "action": "accept"},
        {"id": ids[1], "action": "override", "feeling": "拿不准",
         "reason": "读着其实还行，只是太短"},
    ])
    check("采纳 + 推翻一次成功", ok and res["summary"] == {"accept": 1, "override": 1, "reject": 0},
          res)
    sents = R.sentence_verdicts(str(root4), 3)
    check("作者裁定落 2 条（source=author）",
          len(sents) == 2 and all(s["source"] == "author" for s in sents), sents)
    check("裁定带 from_proposal（分得清哪些来自模型）",
          all(s.get("from_proposal") for s in sents), sents)
    check("采纳的感受取模型的、推翻的感受取作者的",
          sents[0]["feeling"] == "打中了" and sents[1]["feeling"] == "拿不准",
          [s["feeling"] for s in sents])
    snap = F.snapshot(str(root4), 3)
    check("终裁后没有待裁", snap["counts"]["pending"] == 0, snap["counts"])
    check("报告摘要能数出来源", F.summary(str(root4))["author_from_model"] == 2,
          F.summary(str(root4))["author_from_model"])
    ok, res = F.settle(str(root4), 3, [{"id": ids[0], "action": "accept"}])
    check("已终裁再裁 → 拒（不许悄悄覆盖）", ok is False and "已终裁过" in str(res.get("error")),
          str(res.get("error"))[:80])

    print("\n=== F. 正文变了 → 旧提案不能用于终裁 ===")
    root5 = tmp_root()
    F.propose(str(root5), 3, [GOOD[0]], model="m")
    pid = F.snapshot(str(root5), 3)["pending"][0]["id"]
    (root5 / "data" / "chapters" / "raw" / "03.md").write_text(
        "## 第3章 夜雨\n\n他走了。\n", encoding="utf-8")
    ok, res = F.settle(str(root5), 3, [{"id": pid, "action": "accept"}])
    check("正文改动后终裁被拒", ok is False and "正文已经变了" in str(res.get("error")),
          str(res.get("error"))[:80])

    print("\n=== G. 单一出口：仍只写 records.json（F13） ===")
    files = judge_dir_files(root4)
    check("data/judge 下没有另开 feelings.json", "feelings.json" not in files, files)
    check("records.json 里有 proposals.feelings",
          "proposals" in json.loads((Path(root4) / R.RECORDS_REL).read_text(encoding="utf-8")),
          files)

    print("\n=== H. 接线：路由与 Agent 守卫 ===")
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    dom_src = (ROOT / "scripts" / "nf_api_domains" / "judge.py").read_text(encoding="utf-8")
    check("nf_api 分发器接了 /judge/feelings/propose", '"/judge/feelings/propose"' in api_src)
    check("nf_api 分发器接了 /judge/feelings/settle", '"/judge/feelings/settle"' in api_src)
    check("nf_api 分发器接了 /judge/caliber/upgrade", '"/judge/caliber/upgrade"' in api_src)
    check("域模块 ROUTES 登记三条新端点",
          all(('"%s"' % p) in dom_src for p in
              ("/judge/feelings", "/judge/feelings/propose", "/judge/feelings/settle",
               "/judge/caliber/upgrade")))
    from utils import agent_guard as AG
    check("Agent 模式下 /judge/feelings/settle 被拒（终裁是作者的动作）",
          AG.is_forbidden_in_agent_mode("/judge/feelings/settle"))
    check("Agent 模式下 /judge/feelings/propose 不拦（提案正是模型该干的）",
          not AG.is_forbidden_in_agent_mode("/judge/feelings/propose"))
    check("Agent 模式下读侧 /judge/feelings 不拦",
          not AG.is_forbidden_in_agent_mode("/judge/feelings"))

    print("\n=== I. 提示词与 CLI（零 token 预演） ===")
    prompt = ROOT / "prompts" / "judge_feelings.md"
    ptext = prompt.read_text(encoding="utf-8") if prompt.exists() else ""
    check("prompts/judge_feelings.md 存在", prompt.exists())
    check("提示词禁止评档位/分数", "不许评档位" in ptext)
    check("提示词要求逐字复制原句", "逐字复制" in ptext)
    check("提示词写明三选一感受", "拿不准" in ptext and "打中了" in ptext)

    root6 = tmp_root()
    (root6 / "prompts").mkdir()
    (root6 / "prompts" / "judge_feelings.md").write_text(ptext, encoding="utf-8")
    script = str(ROOT / "scripts" / "judge_feelings.py")
    r = subprocess.run([sys.executable, script, "--chapter", "3", "--dry-run"],
                       cwd=str(root6), capture_output=True, text=True, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    check("judge_feelings --dry-run 退出码 0", r.returncode == 0, out[-160:])
    check("dry-run 打出提示词且明说零调用",
          "零调用" in out and "第3章" in out.replace(" ", ""), out[-160:])
    check("dry-run 一个字节都没写（没有 records.json）",
          not (root6 / R.RECORDS_REL).exists())

    print("\n" + "=" * 66)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for n in FAIL:
        print("    -", n)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
