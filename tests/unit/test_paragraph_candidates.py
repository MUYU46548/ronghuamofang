# -*- coding: utf-8 -*-
"""多候选（L2 第四件）的判据 —— 零 token、临时工作区。

## 口径（为什么不是“一次调用出两条”）

功能建议写的是“默认 2 条”，但那等于每次点击翻倍花钱。这里按成本口径做成
**点一次出一条、最多留 3 条并排比**：多一条的代价是作者自己再点一次。
所以判据不是“一次返回几条”，而是：

  A 同一段能留住多条候选（旧→新），`get_pending` 仍给最新那条（老调用方不改）
  B 上限 3 条，超了淘汰最旧（不是无界增长）
  C 采用指定 cid 的那一条（不是永远用最新），采用后这一段候选全清
    （正文已变，剩下的都是基于旧正文改的，留着会点错）
  D 放弃带 cid 时只丢那一条，别的候选还在
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import refine_paragraph as rp  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if extra else ""))


def _para_now(ch, pi, root=None):
    """盘上这一段的真实正文（候选里的 `before` 就该是它 —— 冲突判据比的是这玩意）。"""
    path = rp._pick_chapter_path(ch, root)
    if path is None:
        return "原文。"
    try:
        paras = rp.split_paragraphs(path.read_text(encoding="utf-8"))
    except OSError:
        return "原文。"
    return paras[pi] if pi < len(paras) else "原文。"


def rec(cid, after, ch=1, pi=1, before=None):
    return {"chapter": ch, "index": pi, "cid": cid,
            "before": _para_now(ch, pi) if before is None else before, "after": after,
            "feedback": "润色", "at": "2026-11-13T00:00:0%s" % cid[-1],
            "context": [], "version": 1, "backup": ""}


def seed(root, items):
    (root / "data" / "state").mkdir(parents=True, exist_ok=True)
    rp._save_pending(items, root)


def case_multi():
    print("\n[A] 留多条 + 最新那条仍是 get_pending")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed(root, [rec("c1", "改法一。"), rec("c2", "改法二。"), rec("c3", "改法三。")])
        cands = rp.get_candidates(1, 1, root)
        check("A1 三条都在，顺序是旧→新", [c["cid"] for c in cands] == ["c1", "c2", "c3"],
              [c["cid"] for c in cands])
        check("A2 get_pending 给最新那条（老接口语义不变）",
              (rp.get_pending(1, 1, root) or {}).get("cid") == "c3")
        check("A3 别的段落不受影响", rp.get_candidates(1, 2, root) == [])


def case_cap():
    print("\n[B] 上限 3 条，超了淘汰最旧")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed(root, [])
        items = []
        for i in range(1, 5):
            items.append(rec("c%d" % i, "改法%d。" % i))
            items = rp._trim_candidates(items)
        check("B1 留 3 条", len(items) == 3, len(items))
        check("B2 淘汰的是最旧那条（c1 没了，c4 在）",
              [c["cid"] for c in items] == ["c2", "c3", "c4"], [c["cid"] for c in items])
        check("B3 常量就是 3（界面文案与后端同源）", rp.MAX_CANDIDATES == 3)


def case_parse_candidates():
    print()
    print("[D2] 一次调用出多候选（P0-1）：切分 / 容错 / 截断")
    a = "候选甲这一版只修了标点，其余照旧，读起来和原文最接近。" * 2
    b = "候选乙这一版换了句式，把动作提到前面，节奏比原来快一点。" * 2
    c = "候选丙这一版换了个切入角度，先写声音再写人，仍然接得上前一段。" * 2
    NL = chr(10)
    check("P1 三段按 ===CAND=== 切开并保持顺序",
          rp.parse_candidates(a + NL + "===CAND===" + NL + b + NL + "===CAND===" + NL + c)
          == [a, b, c])
    check("P2 只回一条也算成功（多候选是加分项，不是硬前置）",
          len(rp.parse_candidates(a)) == 1)
    check("P3 空文件 + fallback → 用整章里那一段（老行为不丢）",
          rp.parse_candidates("", fallback="原段落") == ["原段落"])
    check("P4 超过 3 条只留前 3 条",
          len(rp.parse_candidates("===CAND===".join([a, b, c, a, b]))) == 3)
    check("P5 分隔符带前后空格也认",
          len(rp.parse_candidates(a + NL + "  ===CAND===  " + NL + b)) == 2)
    check("P6 太短的碎片不算候选（模型偶尔只吐一句）",
          rp.parse_candidates("甲。" + NL + "===CAND===" + NL + "短") == [])


def case_apply_by_cid():
    print("\n[C] 采用指定那条；采用后整段候选清空")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        chap = root / "data" / "chapters" / "refined" / "01.md"
        chap.parent.mkdir(parents=True)
        chap.write_text("## 第1章\n\n原文一。\n\n原文二。\n", encoding="utf-8")
        (root / "data" / "state").mkdir(parents=True, exist_ok=True)
        # before 写真实正文：冲突判据比的就是它（写死一句假原文会被判成“这段变过”）
        seed(root, [rec("c1", "旧改法。", before="原文一。"),
                    rec("c2", "新改法。", before="原文一。")])

        ok, msg = rp.apply_pending(1, 1, root=root, cid="c1")
        text = chap.read_text(encoding="utf-8")
        check("C1 采用成功", ok, msg)
        check("C2 用的是 c1 那条（不是最新的 c2）", "旧改法" in text and "新改法" not in text,
              text)
        check("C3 采用后这一段候选全清（剩下的是基于旧正文的，留着会点错）",
              rp.get_candidates(1, 1, root) == [])

        # 不传 cid = 老行为：最新那条
        seed(root, [rec("c1", "旧改法。", pi=0, before="## 第1章"),
                    rec("c2", "又一条。", pi=0, before="## 第1章")])
        ok2, _m = rp.apply_pending(1, 0, root=root)
        check("C4 不传 cid 时仍用最新那条（向后兼容）",
              ok2 and "又一条" in chap.read_text(encoding="utf-8"), chap.read_text(encoding="utf-8"))


def case_conflict_no_overwrite():
    """[F] 候选出来之后这一段被改过 → 不覆盖；作者明确 force 才覆盖（Papilot ⑧）。"""
    print("\n[F] 冲突摊开、不自动覆盖")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        chap = root / "data" / "chapters" / "refined" / "01.md"
        chap.parent.mkdir(parents=True)
        chap.write_text("## 第1章\n\n他推门进来，屋里没人。\n", encoding="utf-8")
        (root / "data" / "state").mkdir(parents=True, exist_ok=True)
        seed(root, [rec("c1", "他推开门，屋里空着。", pi=1,
                        before="他推门进来，屋里没人。")])

        # 作者在这中间自己改了这一段（不是标点，是字变了）
        chap.write_text("## 第1章\n\n他推门进来，屋里坐着一个人。\n", encoding="utf-8")
        conf = rp.paragraph_conflict(1, 1, rp.get_pending(1, 1, root), root=root)
        check("F1 判得出冲突，并摊开三份证据",
              bool(conf) and conf.get("current") != conf.get("before")
              and "变过" in conf.get("reason", ""), conf)

        ok, msg = rp.apply_pending(1, 1, root=root, cid="c1")
        text = chap.read_text(encoding="utf-8")
        check("F2 不给 force → 拒绝采用（正文一个字没动）",
              ok is False and "坐着一个人" in text and "空着" not in text, msg)

        ok2, msg2 = rp.apply_pending(1, 1, root=root, cid="c1", force=True)
        text2 = chap.read_text(encoding="utf-8")
        check("F3 作者明确 force → 才覆盖（覆盖是他的决定，不是程序的）",
              ok2 and "屋里空着" in text2, msg2)

    # 只补了标点 → 不算冲突（否则作者会被“什么都在报冲突”烦死）
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        chap = root / "data" / "chapters" / "refined" / "01.md"
        chap.parent.mkdir(parents=True)
        chap.write_text("## 第1章\n\n他说，走吧。\n", encoding="utf-8")
        (root / "data" / "state").mkdir(parents=True, exist_ok=True)
        seed(root, [rec("c9", "他说：“走吧。”", pi=1, before="他说,走吧.")])
        conf2 = rp.paragraph_conflict(1, 1, rp.get_pending(1, 1, root), root=root)
        check("F4 只差标点/空白 → 不算冲突（不拦正常的采用）", conf2 is None, conf2)


def case_drop_one():
    print("\n[D] 放弃带 cid = 只丢那一条")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seed(root, [rec("c1", "一。"), rec("c2", "二。"), rec("c3", "三。")])
        rp.drop_pending(1, 1, root=root, cid="c2")
        left = [c["cid"] for c in rp.get_candidates(1, 1, root)]
        check("D1 只少了 c2", left == ["c1", "c3"], left)
        rp.drop_pending(1, 1, root=root)
        check("D2 不带 cid = 清空这一段（老行为）", rp.get_candidates(1, 1, root) == [])


def case_api_shape():
    print("\n[E] 接口契约：pending 同时给 rec 与 candidates")
    src = (REPO / "scripts" / "nf_api_domains" / "refine.py").read_text(encoding="utf-8")
    check("E1 读端点回 candidates（旧→新）", '"candidates":' in src and "get_candidates" in src)
    check("E2 采用端点收 cid", 'cid=(body.get("cid") or None)' in src)
    ce = (REPO / "console" / "src" / "ChapterEditor.vue").read_text(encoding="utf-8")
    check("E3 界面能选候选（不是只用最新）", "pickCid" in ce and "ce-cand-tab" in ce)
    check("E4 界面采用时带上选中的 cid", "cid: activeCid.value" in ce)


def case_intent():
    """[G] 模型先复述“我理解为要改什么”（2026-11-17，P1-6）。

    为什么要有：以前候选卡只有 diff，模型听没听懂意见全靠作者自己对着字看。
    判据分三层：① 能切出来且不污染候选；② 每条候选记录都带上；
    ③ 没写就显式标“未复述”，不许静默空白。
    """
    print("\n[G] 模型先复述理解（P1-6）")
    NL = chr(10)
    a = "把结尾的总结句删掉，改成她推门的动作。" * 2
    b = "结尾改成一个没说出口的动作，接下一段的时间。" * 2
    c = "换一种收束方式，动作在前，情绪不点明。" * 2
    good = ("===INTENT===" + NL + "我理解为要改：删掉结尾总结句，改成动作" + NL
            + "===CAND===" + NL + a + NL + "===CAND===" + NL + b
            + NL + "===CAND===" + NL + c)
    intent, rest = rp.split_intent(good)
    check("G1 intent 切得出来", intent == "我理解为要改：删掉结尾总结句，改成动作", intent)
    check("G2 intent 不进候选正文", rp.parse_candidates(good) == [a, b, c])
    check("G3 rest 里没有 intent 标记", "===INTENT===" not in rest)

    old = a + NL + "===CAND===" + NL + b
    check("G4 老格式（没 intent）照常解析", rp.split_intent(old)[0] == ""
          and rp.parse_candidates(old) == [a, b])

    late = a + NL + "===CAND===" + NL + b + NL + "===INTENT===" + NL + "说明"
    check("G5 标记写在候选之后 → 不当 intent（否则会把候选吞掉）",
          rp.split_intent(late)[0] == "" and len(rp.parse_candidates(late)) == 2)

    # 落进候选记录：每一条都带同一个 intent；模型没写就是空串（界面标“未复述”）。
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "data" / "state").mkdir(parents=True, exist_ok=True)
        orig = rp.run_paragraph_refine

        def _fake_intent(*_a, **_k):
            return (True, "ok", None, 1, {"index": 0, "before": "改前。", "after": a,
                                          "candidates": [a, b, c],
                                          "intent": "我理解为要改：精简", "context": []})

        def _fake_blank(*_a, **_k):
            return (True, "ok", None, 2, {"index": 0, "before": "改前。", "after": a,
                                          "candidates": [a, b, c], "context": []})
        try:
            rp.run_paragraph_refine = _fake_intent
            ok, _msg, rec = rp.preview_paragraph({}, {}, 1, 0, "精简", root=root)
            items = rp.get_candidates(1, 0, root)
            check("G6 每条候选记录都带 intent",
                  ok and len(items) == 3 and all(x.get("intent") == "我理解为要改：精简" for x in items),
                  [x.get("intent") for x in items])
            check("G7 接口返回的那条也带", (rec or {}).get("intent") == "我理解为要改：精简")
            rp.run_paragraph_refine = _fake_blank
            ok2, _m2, rec2 = rp.preview_paragraph({}, {}, 1, 0, "精简", root=root)
            check("G8 模型没复述 → 记空串（不是缺字段，界面据此标“未复述”）",
                  ok2 and rec2 is not None and rec2.get("intent") == "",
                  (rec2 or {}).get("intent"))
        finally:
            rp.run_paragraph_refine = orig

    # 提示词与界面：两边都得在位（改回去立刻红）。
    pr = (REPO / "prompts" / "stage4_refine_paragraph.md").read_text(encoding="utf-8")
    check("G9 提示词要求先写 ===INTENT===", "===INTENT===" in pr)
    ce = (REPO / "console" / "src" / "ChapterEditor.vue").read_text(encoding="utf-8")
    check("G10 候选卡显示“模型的理解”", "模型的理解" in ce and "ce-intent-text" in ce)
    check("G11 没复述时显式标注（不许静默空白）", "未复述" in ce)
    check("G12 批注按未收口/已收口分组", "annGroups" in ce and "未收口" in ce and "已收口" in ce)
    check("G13 批注能一键跳到段落", "jumpToAnnotation" in ce)

    # 两个入口语义显式不同（P1-7）：精修页签直接写盘，章节编辑器先候选后采用。
    prp = (REPO / "console" / "src" / "ParagraphRefinePanel.vue").read_text(encoding="utf-8")
    check("G14 精修页签写明“直接改正文 / 立刻生效”",
          "直接改正文" in prp and "点下就生效" in prp)
    check("G15 精修页签指向候选流程（语义不同要说清）",
          "先出候选" in prp and "章节" in prp and "点采用才进正文" in prp)
    api_src = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    _act = api_src.split("def act_refine_paragraph")[1].split("\ndef ")[0]
    check("G16 /refine/paragraph 仍是直接落盘（与面板文案一致）",
          "run_paragraph_refine" in _act and "preview=True" not in _act)


for fn in (case_multi, case_cap, case_parse_candidates, case_apply_by_cid,
            case_drop_one, case_intent, case_api_shape):
    fn()

(Path(sys.argv[0]).parent / "").exists()  # noqa: B018  (保持与其它用例同样的收尾形态)
json.dumps({})                             # noqa: B018
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
