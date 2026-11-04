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
