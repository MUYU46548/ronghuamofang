# -*- coding: utf-8 -*-
"""stage5 改稿裁定闸的回归锁（2026-11-04，N1=B）。

## 锁什么

1. 只有**改过的段**才产生裁定项（没改的地方不逼作者点头）。
2. 未全部裁定 → 拒绝合成（不能"点一半就落成品"）。
3. 全部不采用 → 合成结果与 raw 完全一致（拒绝也是合法结果）。
4. 裁定后 raw 变了 → 判 stale，拒绝拿旧裁定套新稿。
5. 模型没改任何东西 → 0 项，合成结果等于 raw。

用法：python tests/unit/test_stage5_gate.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import stage5_gate as G  # noqa: E402

PASS, FAIL = [], []

RAW = """第一段：雨落在青石板上。

第二段：他收伞进门。

第三段：屋里有人低声说话。

第四段：桌上摆着一碗凉透的面。"""

PROP = """第一段：雨落在青石板上。

第二段：他收起伞，抖了抖水才进门。

（新增）门外有车驶过，灯扫过窗纸。

第三段：屋里有人低声说话。"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  → " + str(detail)) if detail and not cond else ""))


def setup(tmp: Path):
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True)
    (tmp / "data" / "chapters" / "checked_pending").mkdir(parents=True)
    (tmp / "data" / "chapters" / "checked").mkdir(parents=True)
    (tmp / "data" / "review" / "stage5").mkdir(parents=True)
    (tmp / "data" / "chapters" / "raw" / "03.md").write_text(RAW, encoding="utf-8")
    (tmp / "data" / "chapters" / "checked_pending" / "03.md").write_text(PROP, encoding="utf-8")


def _test_format_fold():
    """P1-2（2026-11-15）：只改标点/空白的项并入最近的实质改动。

    为什么单独锁：这条规则的正向效果（少点几次按钮）和反向风险（**悄悄丢掉**
    模型对某处的改法、或让作者为标点单独点一次）都不在别处可见。断言固定四件：
    挂靠对象正确、计数据实质项算、取舍跟着挂靠项走、没有挂靠处时不乱并。
    """
    print("== 9. 只改标点/空白的项并入最近的实质改动（P1-2）==")

    print("-- 9.1 就近挂靠（不是等距时取更近的那个）--")
    raw = "\n\n".join(["甲一。", "甲二。", "甲三。", "甲四。", "甲五。", "甲六。", "甲七。"])
    prop = "\n\n".join(["甲一改了。", "甲二也改了。", "甲三。", "甲四！", "甲五。", "甲六。",
                        "甲七，结尾补一句。"])
    items, _ = G._plan(raw, prop)
    kinds = [(it["id"], bool(it.get("folded_into"))) for it in items]
    check("4 项，其中 1 项被并入", len(items) == 4 and sum(f for _, f in kinds) == 1, kinds)
    folded = [it for it in items if it.get("folded_into")]
    check("只改标点的那一项挂到前一处实质改动（第 2 处）",
          bool(folded) and folded[0]["folded_into"] == 2,
          [(it["id"], it.get("folded_into")) for it in folded])
    check("挂靠项的 folds 里能查到它",
          sorted(items[1].get("folds") or []) == [it["id"] for it in folded],
          items[1].get("folds"))
    check("待裁定的项只有实质改动（3 项）", len(G.actionable(items)) == 3,
          [it["id"] for it in G.actionable(items)])

    print("-- 9.2 等距时挂前面那一处（作者是从上往下看的）--")
    raw2 = "\n\n".join(["他推门进来，屋里没人。", "桌上摆着一碗面。", "窗外的雨还没停。"])
    prop2 = "\n\n".join(["他推开门进来，屋里没有人。", "桌上摆着一碗面！", "窗外的雨还没停，屋檐在滴水。"])
    items2, _ = G._plan(raw2, prop2)
    check("前后各一处实质改动时，标点项挂到前一处",
          len(items2) == 3 and items2[1].get("folded_into") == items2[0]["id"],
          [(it["id"], it.get("folded_into")) for it in items2])

    print("-- 9.3 取舍跟着挂靠项走（采用 / 不采用都验）--")
    tmp = Path(tempfile.mkdtemp(prefix="s5fold_"))
    try:
        for d in ("data/chapters/raw", "data/chapters/checked_pending",
                  "data/chapters/checked", "data/review/stage5"):
            (tmp / d).mkdir(parents=True)
        (tmp / "data/chapters/raw/07.md").write_text(raw2, encoding="utf-8")
        (tmp / "data/chapters/checked_pending/07.md").write_text(prop2, encoding="utf-8")
        meta = G.write_proposal(7, root=tmp)
        host = [it for it in meta["items"] if not it.get("folded_into")][0]["id"]
        punct = [it for it in meta["items"] if it.get("folded_into")]
        check("落盘的 meta 带 folded_into", len(punct) == 1, meta["items"])

        ok, msg, _ = G.decide(7, {punct[0]["id"]: True}, root=tmp)
        check("直接给并入项下裁定被拒，并说清该定哪一处",
              ok is False and ("并入第 %d 处" % host) in msg, msg)

        others = {it["id"]: False for it in G.actionable(meta) if it["id"] != host}
        dec = dict(others)
        dec[host] = True
        G.decide(7, dec, root=tmp)
        ok, msg, p = G.apply_chapter(7, root=tmp)
        got = p.read_text(encoding="utf-8") if p else ""
        check("采用挂靠项 → 那处标点改法一起进成品",
              ok and "桌上摆着一碗面！" in got, got[:120])

        # 反证：不采用挂靠项 → 标点改法也不能溜进成品
        (tmp / "data/review/stage5/07.json").unlink()
        (tmp / "data/chapters/checked/07.md").unlink()
        meta = G.write_proposal(7, root=tmp)
        host = [it for it in meta["items"] if not it.get("folded_into")][0]["id"]
        dec = {it["id"]: True for it in G.actionable(meta) if it["id"] != host}
        dec[host] = False
        G.decide(7, dec, root=tmp)
        ok, msg, p = G.apply_chapter(7, root=tmp)
        got = p.read_text(encoding="utf-8") if p else ""
        check("不采用挂靠项 → 标点改法也不进成品（程序没自作主张）",
              ok and "桌上摆着一碗面！" not in got and "桌上摆着一碗面。" in got, got[:120])

        print("-- 9.4 计数据实质项算（并入的不占一格、也不算未定）--")
        rows = G.list_all(root=tmp)
        row = rows[0]
        check("list_all 的 items 只数实质项",
              row["items"] == len(G.actionable(G.load(7, root=tmp))), row)
        check("并入的项单列成 folded 计数，不混进未定清单",
              row["folded"] == 1 and row["pending"] == [], row)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-- 9.5 没有可挂靠的地方时不许乱并 --")
    raw3 = "桌上摆着一碗面。\n\n窗外的雨还没停。"
    prop3 = "桌上摆着一碗面！\n\n窗外的雨还没停？"
    items3, _ = G._plan(raw3, prop3)
    check("全章只有标点改动 → 两项都留给人定（不悄悄丢掉）",
          len(items3) == 2 and not any(it.get("folded_into") for it in items3),
          items3)

    print("-- 9.6 段落拆并仍单独成项（边界，如实声明）--")
    raw4 = "他一口气说了很长的一段话，中间的停顿都省了。"
    prop4 = "他一口气说了很长的一段话。\n\n中间的停顿都省了。"
    items4, _ = G._plan(raw4, prop4)
    check("一段拆两段 → 两项都是实质改动",
          len(items4) == 2 and not any(it.get("folded_into") for it in items4),
          items4)


def _test_layout():
    """P2-1（2026-11-15）：阅读态版面 —— 两稿各排一遍、改过的段带 item 号。

    为什么单独锁：这一层是“不读 diff、在成书的版面上改”的数据面。
    它错了不会报错，只会**静默错位**：高亮错行、点一处跳到另一处、
    新增段在原文栏凭空出现 —— 作者看着正常，改的是别的段。
    """
    print("== 10. 阅读态版面（P2-1）==")
    raw = "\n\n".join(["甲段原样。", "乙段原样。", "丙段原样。", "丁段原样。"])
    prop = "\n\n".join(["甲段原样。", "乙段改了。", "丙段原样。", "新增的一段。"])

    items, _ = G._plan(raw, prop)
    meta = {"items": items, "blocks": G._plan(raw, prop)[1]}
    lay = G.layout_plan(raw, prop, meta)

    check("两栏都能拼回各自的原文（段落一个不漏、顺序不变）",
          [x["text"] for x in lay["raw"]] == G.paragraphs(raw)
          and [x["text"] for x in lay["proposed"]] == G.paragraphs(prop),
          ([x["text"] for x in lay["raw"]], [x["text"] for x in lay["proposed"]]))
    changed_raw = [x for x in lay["raw"] if x["item"] is not None]
    changed_prop = [x for x in lay["proposed"] if x["item"] is not None]
    check("原文栏里改过的段：乙段（改写）+ 丁段（删除）",
          [x["text"] for x in changed_raw] == ["乙段原样。", "丁段原样。"], changed_raw)
    check("改后栏里改过的段：乙段（改写）+ 新增段（插入）",
          [x["text"] for x in changed_prop] == ["乙段改了。", "新增的一段。"], changed_prop)
    check("没改的段 item 为 None（高亮只落在改动上）",
          [x["text"] for x in lay["raw"] if x["item"] is None] == ["甲段原样。", "丙段原样。"],
          lay["raw"])
    ids = {x["item"] for x in lay["raw"] if x["item"] is not None}
    check("栏里的 item 号都是本章真有的号", ids <= {it["id"] for it in items}, ids)
    check("删除项在改后栏没有段落、新增项在原文栏没有段落（事实如此，不是缺数据）",
          len([x for x in lay["proposed"] if x["item"] == 4]) == 0
          and len([x for x in lay["raw"] if x["item"] == 3]) == 0,
          (lay["raw"], lay["proposed"]))

    tmp = Path(tempfile.mkdtemp(prefix="s5lay_"))
    try:
        for d in ("data/chapters/raw", "data/chapters/checked_pending",
                  "data/chapters/checked", "data/review/stage5"):
            (tmp / d).mkdir(parents=True)
        check("两稿缺一 → 返回 None（前端据此说“读不到”，不编版面）",
              G.layout_for(9, meta, root=tmp) is None)
        (tmp / "data/chapters/raw/09.md").write_text(raw, encoding="utf-8")
        (tmp / "data/chapters/checked_pending/09.md").write_text(prop, encoding="utf-8")
        lay2 = G.layout_for(9, meta, root=tmp)
        check("两稿都在 → 版面可以排出来",
              bool(lay2) and [x["text"] for x in lay2["proposed"]] == G.paragraphs(prop),
              lay2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="s5gate_"))
    try:
        setup(tmp)

        print("== 1. 只对改过的段出裁定项 ==")
        meta = G.write_proposal(3, root=tmp)
        check("4 段 → 3 个变化项", len(meta["items"]) == 3, len(meta["items"]))
        kinds = [it["kind"] for it in meta["items"]]
        check("类型是 replace/insert/delete",
              sorted(kinds) == ["delete", "insert", "replace"], kinds)
        check("初始全部未定", all(it["accepted"] is None for it in meta["items"]), None)

        print("== 2. 未全部裁定 → 拒绝合成 ==")
        rid = meta["items"][0]["id"]
        ok, msg, _ = G.decide(3, {rid: True}, root=tmp)
        check("记一项后仍有未定", ok and "还剩" in msg, msg)
        ok, msg, _ = G.apply_chapter(3, root=tmp)
        check("合成被拒绝", ok is False and "未裁定" in msg, msg)
        check("checked 未生成", not (tmp / "data" / "chapters" / "checked" / "03.md").exists(), None)

        print("== 3. 全部采用 → 合成模型稿 ==")
        ids = [it["id"] for it in meta["items"]]
        G.decide(3, {i: True for i in ids}, root=tmp)
        ok, msg, p = G.apply_chapter(3, root=tmp)
        got = p.read_text(encoding="utf-8")
        check("合成成功", ok, msg)
        check("采用后包含新增段", "门外有车驶过" in got, got[:80])
        check("采用后删掉第四段", "凉透的面" not in got, got[:80])
        check("采用后保留改过的第二段", "抖了抖水" in got, got[:80])

        print("== 4. 全部不采用 → 合成结果等于 raw ==")
        (tmp / "data" / "chapters" / "checked_pending" / "03.md").write_text(PROP, encoding="utf-8")
        (tmp / "data" / "chapters" / "raw" / "03.md").write_text(RAW, encoding="utf-8")
        meta2 = G.write_proposal(3, root=tmp)
        G.decide(3, {it["id"]: False for it in meta2["items"]}, root=tmp)
        ok, msg, p2 = G.apply_chapter(3, root=tmp)
        check("拒绝后仍可合成", ok, msg)
        check("拒绝 = raw 原文", p2.read_text(encoding="utf-8") == RAW, p2.read_text(encoding="utf-8")[:60])

        print("== 5. 裁定后 raw 变了 → stale ==")
        (tmp / "data" / "chapters" / "raw" / "03.md").write_text(RAW, encoding="utf-8")
        meta3 = G.write_proposal(3, root=tmp)
        G.decide(3, {it["id"]: True for it in meta3["items"]}, root=tmp)
        (tmp / "data" / "chapters" / "raw" / "03.md").write_text(RAW + "\n\n第五段：作者自己补的。",
                                                                encoding="utf-8")
        ok, msg, _ = G.apply_chapter(3, root=tmp)
        check("stale 拒绝合成", ok is False and "stale" in msg.replace("stale", "stale"), msg)
        check("状态被标 stale", G.load(3, root=tmp)["status"] == "stale", G.load(3, root=tmp)["status"])

        print("== 6. 模型没改 → 0 项，合成等于 raw ==")
        (tmp / "data" / "chapters" / "raw" / "03.md").write_text(RAW, encoding="utf-8")
        (tmp / "data" / "chapters" / "checked_pending" / "03.md").write_text(RAW, encoding="utf-8")
        meta4 = G.write_proposal(3, root=tmp)
        check("0 个裁定项", meta4["items"] == [], meta4["items"])
        ok, msg, p4 = G.apply_chapter(3, root=tmp)
        check("无改稿也能合成", ok and p4.read_text(encoding="utf-8") == RAW, msg)

        print("== 7. 列表与待裁定 ==")
        rows = G.list_all(root=tmp)
        check("list_all 返回第 3 章", [r["chapter"] for r in rows] == [3], rows)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _test_format_fold()
    _test_layout()

    print("== 8. API 层：清单 / 详情 / 裁定 / 合成 ==")
    tmp2 = Path(tempfile.mkdtemp(prefix="s5api_"))
    try:
        for d in ("data/chapters/raw", "data/chapters/checked_pending",
                  "data/chapters/checked", "data/review/stage5"):
            (tmp2 / d).mkdir(parents=True)
        (tmp2 / "data/chapters/raw/01.md").write_text(RAW, encoding="utf-8")
        (tmp2 / "data/chapters/checked_pending/01.md").write_text(PROP, encoding="utf-8")
        import nf_api
        import nf_api_domains.stage5 as S5
        nf_api.ROOT = tmp2
        G.write_proposal(1, root=tmp2)

        st, d = S5.handle_stage5_list(None)
        check("list 200 且待裁定含第 1 章", st == 200 and d["pending"] == [1], d)

        class H:
            path = "/review/stage5/chapter?n=1"

        st, d = S5.handle_stage5_chapter(H())
        check("chapter 200 且未勾不可合成", st == 200 and d["can_apply"] is False, st)
        ids = [it["id"] for it in d["items"]]

        st, d = S5.handle_stage5_apply(None, {"chapter": 1})
        check("未勾完 apply 被拒（400）", st == 400, d)

        st, d = S5.handle_stage5_decide(None, {"chapter": 1,
                                               "decisions": {i: True for i in ids}})
        check("全部裁定 200", st == 200, d)
        st, d = S5.handle_stage5_apply(None, {"chapter": 1})
        check("裁定完 apply 200", st == 200, d)
        check("checked 已生成",
              (tmp2 / "data/chapters/checked/01.md").exists(), None)
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
