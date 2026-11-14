# -*- coding: utf-8 -*-
"""人工分歧 + 裁决账本 判据（2026-11-14）。

守四件事：
  1. **摆事实与签字分开**：清单谁都能写，裁决必须带理由、只能追加；
  2. **只记账不改设定集**：裁决跑完，setting.json 一个字节都不许变；
  3. **校验从严**：未知 id / 不在清单里的值 / 空理由，一律拒绝并说清可选什么；
  4. **合并可见**：`/setting/conflicts` 里人工条目与自动检出同形（前端一套渲染）。

用法：python tests/unit/test_manual_conflicts.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import manual_conflicts as mc  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if extra else ""))


def build(root: Path) -> None:
    (root / "data" / "setting").mkdir(parents=True, exist_ok=True)
    (root / "materials" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "data" / "setting" / "setting.json").write_text(json.dumps({
        "characters": [{"id": "c001", "name": "柯砚", "role": "主角"}],
        "world": {}, "plot_fragments": [], "timeline": [],
    }, ensure_ascii=False), encoding="utf-8")
    mc.save(root, {"version": 1, "conflicts": [
        {"id": "A1", "name": "柯砚", "field": "姓名", "question": "主角姓什么？",
         "current": "柯砚", "suggestion": "柯砚",
         "values": [{"value": "岑砚", "file": "a.md", "excerpt": "甲"},
                    {"value": "柯砚", "file": "b.md", "excerpt": "乙"}]},
        {"id": "B1", "name": "糜生", "field": "年龄", "question": "多大？",
         "values": [{"value": "四十七岁", "file": "c.md", "excerpt": ""},
                    {"value": "42 岁", "file": "d.md", "excerpt": ""}]},
    ]})


def case_list_and_shape():
    print("\n[A] 清单：空 = 空表；有条目时字段齐全")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("A1 没有文件时是空表（不是报错）", mc.load(root)["conflicts"] == [])
        build(root)
        items = mc.as_conflicts(root)
        check("A2 两条都出来", len(items) == 2, len(items))
        check("A3 每条带 source=manual（前端靠它分开渲染）",
              all(c["source"] == "manual" for c in items))
        check("A4 两方版本都带出处与原文",
              all(len(c["values"]) == 2 and all(v["file"] for v in c["values"])
                  for c in items))
        st = mc.stats(root)
        check("A5 未裁统计正确", st == {"total": 2, "pending": 2, "decided": 0}, st)


def case_resolve():
    print("\n[B] 裁决：带理由、只追加、不动设定集")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root)
        before = (root / "data" / "setting" / "setting.json").read_bytes()

        ok, pay = mc.resolve(root, "A1", "柯砚", "4-4 稿给了改姓的制度理由")
        check("B1 正常裁决成功", ok is True, pay)
        check("B2 账本落一条", len(mc.load(root) and mc.load_ledger(root)["entries"]) == 1)
        items = {c["id"]: c for c in mc.as_conflicts(root)}
        check("B3 条目带 decided（含理由与时间）",
              items["A1"]["decided"]["chosen"] == "柯砚"
              and items["A1"]["decided"]["reason"].startswith("4-4"), items["A1"]["decided"])
        check("B4 未裁数减一", mc.stats(root)["pending"] == 1, mc.stats(root))
        check("B5 setting.json 一个字节都没变",
              (root / "data" / "setting" / "setting.json").read_bytes() == before)

        ok2, _ = mc.resolve(root, "A1", "岑砚", "改主意了")
        led = mc.load_ledger(root)["entries"]
        check("B6 同一 id 再裁一次 = 追加一条（不改历史）", ok2 and len(led) == 2, len(led))
        check("B7 latest 取最后一条", mc.ledger_latest(root)["A1"]["chosen"] == "岑砚",
              mc.ledger_latest(root)["A1"])
        ok3, _ = mc.resolve(root, "B1", mc.KEEP, "确认现状即可")
        check("B8 __keep__ 是合法表态", ok3 is True)
        ok4, _ = mc.resolve(root, "B1", mc.REJECT, "两个说法都不采用")
        check("B9 __reject__ 是合法表态", ok4 is True)
        check("B10 没有 .tmp 残留（原子写）",
              not list((root / "data" / "setting").glob("*.tmp")),
              list((root / "data" / "setting").iterdir()))


def case_reject_bad_input():
    print("\n[C] 坏输入：拒绝并说清可选什么")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build(root)
        ok, pay = mc.resolve(root, "ZZ", "柯砚", "随便")
        check("C1 未知 id 被拒", ok is False and "没有这条分歧" in pay["error"], pay.get("error"))
        ok, pay = mc.resolve(root, "A1", "张三", "随便")
        check("C2 清单外的值被拒，并列出可选值",
              ok is False and "岑砚" in (pay.get("allowed") or []), pay)
        ok, pay = mc.resolve(root, "A1", "柯砚", "   ")
        check("C3 空理由被拒", ok is False and "理由" in pay["error"], pay.get("error"))
        check("C4 被拒的调用不写账本", mc.load_ledger(root)["entries"] == [])


def case_merged_into_conflicts():
    print("\n[D] 合并：/setting/conflicts 里人工条目与自动检出同形")
    import nf_api
    from nf_api_domains import setting_state as ss

    class H:
        def _query(self):
            return {}

    old = nf_api.ROOT
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            build(root)
            nf_api.ROOT = root
            code, d = ss.handle_setting_conflicts(H())
            items = d.get("review_conflicts") or []
            names = [c.get("name") for c in items]
            check("D1 200 且人工条目出现", code == 200 and "柯砚" in names, names)
            manual = [c for c in items if c.get("source") == "manual"]
            check("D2 manual 条目带 id 与 question",
                  all(c.get("id") and c.get("question") for c in manual),
                  [(c.get("id"), c.get("question")) for c in manual])
            check("D3 manual_stats 一并回给界面",
                  (d.get("manual_stats") or {}).get("pending") == 2, d.get("manual_stats"))
            mc.resolve(root, "A1", "柯砚", "测试")
            code, d = ss.handle_setting_conflicts(H())
            check("D4 裁过的条目仍列出（带 decided），但不再计入待裁",
                  (d.get("manual_stats") or {}).get("pending") == 1, d.get("manual_stats"))
    finally:
        nf_api.ROOT = old


for fn in (case_list_and_shape, case_resolve, case_reject_bad_input, case_merged_into_conflicts):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
