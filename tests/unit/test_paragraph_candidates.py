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


def rec(cid, after, ch=1, pi=1):
    return {"chapter": ch, "index": pi, "cid": cid, "before": "原文。", "after": after,
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


def case_apply_by_cid():
    print("\n[C] 采用指定那条；采用后整段候选清空")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        chap = root / "data" / "chapters" / "refined" / "01.md"
        chap.parent.mkdir(parents=True)
        chap.write_text("## 第1章\n\n原文一。\n\n原文二。\n", encoding="utf-8")
        (root / "data" / "state").mkdir(parents=True, exist_ok=True)
        seed(root, [rec("c1", "旧改法。"), rec("c2", "新改法。")])

        ok, msg = rp.apply_pending(1, 1, root=root, cid="c1")
        text = chap.read_text(encoding="utf-8")
        check("C1 采用成功", ok, msg)
        check("C2 用的是 c1 那条（不是最新的 c2）", "旧改法" in text and "新改法" not in text,
              text)
        check("C3 采用后这一段候选全清（剩下的是基于旧正文的，留着会点错）",
              rp.get_candidates(1, 1, root) == [])

        # 不传 cid = 老行为：最新那条
        seed(root, [rec("c1", "旧改法。", pi=0), rec("c2", "又一条。", pi=0)])
        ok2, _m = rp.apply_pending(1, 0, root=root)
        check("C4 不传 cid 时仍用最新那条（向后兼容）",
              ok2 and "又一条" in chap.read_text(encoding="utf-8"), chap.read_text(encoding="utf-8"))


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


for fn in (case_multi, case_cap, case_apply_by_cid, case_drop_one, case_api_shape):
    fn()

(Path(sys.argv[0]).parent / "").exists()  # noqa: B018  (保持与其它用例同样的收尾形态)
json.dumps({})                             # noqa: B018
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
