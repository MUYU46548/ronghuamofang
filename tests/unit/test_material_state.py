# -*- coding: utf-8 -*-
"""素材库状态机（A3）回归 —— 确定性冲突分级 / 墓碑 / canon 冻结。

## 为什么需要它

A3 要解决的三件事**都是"没有状态"造成的静默问题**：

| 编号 | 问题 | 钉法 |
|---|---|---|
| W1 | 素材卡解析（`# 角色：X` + `## 基本信息`）—— 解析不对，后面全错 | 含括号注释的标题归一、非卡片文件必须返回 None |
| W2 | 同角色两张卡矛盾时提示词层只有「取更详细/更新的」，模型自由心证 | `review`（机器裁不了 → 报人）判据 |
| W3 | 信息量有包含关系时不该打扰人 | `auto` 判据（子串包含 / 英文名·别名等低风险字段 / "待填充"不算值） |
| W4 | 被否决的卡没有墓碑 → 下一轮复活 | 墓碑 CRUD + 损坏文件**不静默** |
| W5 | **不给 LLM 自报置信度的机会** —— 数字要可复现 | 全流程零 token，纯函数 |
| W6 | 大纲阶段读活稿 → 改素材悄悄扰动已定稿大纲 | canon 冻结 + stage2 真的读 canon |
| W7 | 空结果 ≠ 一致 | 设定集不存在时 `checked=False` + reason |

用法：python tests/unit/test_material_state.py（零 LLM、零网络）
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


CARD_TMPL = """# {kind}：{title}

> 来源：测试库 `设定/{kind}/{name}.md`（审定版）

## 基本信息

{fields}
## 正文

{body}
"""


def write_card(d, fname, kind, title, fields, body="内容。", name=None):
    p = Path(d) / fname
    p.write_text(CARD_TMPL.format(
        kind=kind, title=title, name=name or title,
        fields="".join(f"- {k}：{v}\n" for k, v in fields.items()),
        body=body), encoding="utf-8")
    return p


def make_materials(d, cards):
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    for c in cards:
        write_card(d, c["file"], c.get("kind", "角色"), c["title"],
                   c.get("fields", {}), c.get("body", "内容。"))
    return d


# ====================================================================== W1

def case_parse_card():
    print("\n[W1] 素材卡解析")
    from utils.material_state import parse_card, collect_cards
    with tempfile.TemporaryDirectory() as td:
        p = write_card(td, "甲_角色卡.md", "角色", "甲（黑衣）",
                       {"正式名称": "甲", "种族": "人类", "别名": "无"})
        c = parse_card(p)
        check("W1 能识别 `# 角色：名字`", bool(c) and c["kind"] == "角色", f"→ {c}")
        check("W1 标题里的括号注释不算名字的一部分（甲（黑衣）→ 甲）",
              c and c["name"] == "甲", f"→ {c and c['name']}")
        check("W1 `## 基本信息` 字段被抽取",
              c and c["fields"].get("种族") == "人类", f"→ {c and c['fields']}")
        check("W1 来源被记录（冲突时要能指出出处）",
              c and "测试库" in c["source"], f"→ {c and c['source']}")

        (Path(td) / "稿_某秘闻录.md").write_text(
            "## 震惊！某人的深夜食堂\n\n正文。\n", encoding="utf-8")
        (Path(td) / "README.md").write_text("# 素材目录\n", encoding="utf-8")
        got = collect_cards(td)
        check("W1 非卡片文件（稿件/README）不被当卡", len(got) == 1,
              f"→ {[g['name'] for g in got]}")


# ====================================================================== W2/W3

def case_conflicts_severity():
    print("\n[W2/W3] 分歧分级：review（报人）vs auto（自动归并）")
    from utils.material_state import collect_cards, detect_conflicts
    with tempfile.TemporaryDirectory() as td:
        make_materials(td, [
            {"file": "甲_角色卡.md", "title": "甲",
             "fields": {"正式名称": "甲", "能力": "控火", "英文名": "A"}},
            {"file": "甲_角色卡_旧.md", "title": "甲",
             "fields": {"正式名称": "甲", "能力": "控水", "英文名": "Alpha",
                        "驻地或据点": "待填充"}},
        ])
        cards = collect_cards(td)
        conf = detect_conflicts(cards)
        by_field = {c["field"]: c for c in conf}
        check("W2 「能力」两值都不为空且不同 → review（机器裁不了，报人）",
              by_field.get("能力", {}).get("severity") == "review", f"→ {by_field.get('能力')}")
        check("W3 英文名属低风险字段 → auto（不打扰人）",
              by_field.get("英文名", {}).get("severity") == "auto", f"→ {by_field.get('英文名')}")
        check("W3 「待填充」不算有值，不产生冲突",
              "驻地或据点" not in by_field, f"→ {by_field.get('驻地或据点')}")
        vals = by_field.get("能力", {}).get("values", [])
        check("W2 冲突条目带**双方出处**（不能只说「有冲突」）",
              len(vals) == 2 and all(v["file"] for v in vals), f"→ {vals}")

    with tempfile.TemporaryDirectory() as td:
        make_materials(td, [
            {"file": "乙_角色卡.md", "title": "乙", "fields": {"种族": "神明"}},
            {"file": "乙_角色卡_全.md", "title": "乙", "fields": {"种族": "神明（月面结界维持者）"}},
        ])
        conf = detect_conflicts(collect_cards(td))
        sf = {c["field"]: c for c in conf}.get("种族", {})
        check("W3 一方是另一方的子串（信息量有包含关系）→ auto",
              sf.get("severity") == "auto", f"→ {sf}")

    with tempfile.TemporaryDirectory() as td:
        make_materials(td, [
            {"file": "丙_角色卡.md", "title": "丙", "fields": {"性别": "男"}},
            {"file": "丁_角色卡.md", "title": "丁", "fields": {"性别": "女"}},
        ])
        check("W3 不同名字之间不判冲突（同名才比）",
              detect_conflicts(collect_cards(td)) == [])


# ====================================================================== W4

def case_tombstones():
    print("\n[W4] 墓碑 CRUD")
    from utils import material_state as ms
    with tempfile.TemporaryDirectory() as td:
        tp = str(Path(td) / "tombstones.json")
        check("W4 初始为空", ms.tombstone_names(ms.load_tombstones(tp)) == set())
        ok, msg = ms.add_tombstone("戊（旧设）", "与正典冲突，已废弃", path=tp)
        check("W4 登记墓碑成功且名字归一（戊（旧设）→ 戊）",
              ok and ms.tombstone_names(ms.load_tombstones(tp)) == {"戊"}, f"→ {msg}")
        ok2, msg2 = ms.add_tombstone("戊", "换了个理由", path=tp)
        data = ms.load_tombstones(tp)
        check("W4 重复登记=更新而不是新增",
              ok2 and len(data["entries"]) == 1
              and data["entries"]["戊"]["reason"] == "换了个理由", f"→ {msg2}")
        check("W4 理由为空也要有占位（不许空着当「没问题」）",
              ms.add_tombstone("己", "", path=tp)[0]
              and ms.load_tombstones(tp)["entries"]["己"]["reason"])
        ok3, _ = ms.remove_tombstone("戊", path=tp)
        check("W4 撤销墓碑", ok3 and "戊" not in ms.tombstone_names(ms.load_tombstones(tp)))
        check("W4 撤销不存在的墓碑要明确回报失败",
              ms.remove_tombstone("查无此人", path=tp)[0] is False)

        # 损坏文件：必须打 WARN 且安全回退（本仓最忌静默）
        Path(tp).write_text("{ 坏 JSON", encoding="utf-8")
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            data = ms.load_tombstones(tp)
        check("W4 墓碑文件损坏 → 打 WARN 且按空处理（不静默、不炸）",
              "WARN" in buf.getvalue() and data["entries"] == {}, f"→ {buf.getvalue()[:80]}")


# ====================================================================== W5/W6

def case_audit_and_status():
    print("\n[W5/W6] 审计：状态标注 + 只改该改的地方")
    from utils import material_state as ms
    with tempfile.TemporaryDirectory() as td:
        md = make_materials(Path(td) / "raw", [
            {"file": "甲_角色卡.md", "title": "甲", "fields": {"能力": "控火"}},
            {"file": "甲_角色卡2.md", "title": "甲", "fields": {"能力": "控水"}},
            {"file": "乙_角色卡.md", "title": "乙", "fields": {"能力": "控风"}},
        ])
        sp = Path(td) / "setting.json"
        sp.write_text(json.dumps({
            "characters": [{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
            "world": {"locations": [{"name": "甲"}]},
            "plot_fragments": [{"id": "f1", "status": "unused"}],
            "timeline": [{"event": "x"}],
        }, ensure_ascii=False), encoding="utf-8")
        tp = str(Path(td) / "tomb.json")
        ms.add_tombstone("丙", "已废弃", path=tp)

        rep = ms.audit(str(sp), str(md), tp)
        check("W5 审计生效", rep["checked"] is True, f"→ {rep['reason']}")
        check("W5 有 review 分歧 → uncertain", rep["by_name"]["甲"]["status"] == "uncertain",
              f"→ {rep['by_name'].get('甲')}")
        check("W5 墓碑命中 → rejected", rep["by_name"]["丙"]["status"] == "rejected",
              f"→ {rep['by_name'].get('丙')}")
        check("W5 无分歧无墓碑 → adopted", rep["by_name"]["乙"]["status"] == "adopted",
              f"→ {rep['by_name'].get('乙')}")
        check("W5 counts 与逐条结论一致",
              rep["counts"] == {"adopted": 1, "uncertain": 1, "rejected": 1},
              f"→ {rep['counts']}")

        setting = json.loads(sp.read_text(encoding="utf-8"))
        marked = ms.apply_status(setting, rep)
        names = {c["name"]: c for c in marked["characters"]}
        check("W6 条目被打上 status + status_reason",
              names["甲"].get("status") == "uncertain" and names["甲"].get("status_reason"),
              f"→ {names['甲']}")
        check("W6 world 里的同名条目也被标注",
              marked["world"]["locations"][0].get("status") == "uncertain")
        check("W6 **不碰** plot_fragments / timeline（1a 机制自己管）",
              marked["plot_fragments"] == setting["plot_fragments"]
              and marked["timeline"] == setting["timeline"])
        check("W6 原始 setting 未被就地修改（纯函数）",
              "status" not in setting["characters"][0])
        check("W6 _meta 记了本次审计（可追溯）",
              (marked["_meta"].get("status_audit") or {}).get("counts"))

    # W7：空结果 ≠ 通过
    with tempfile.TemporaryDirectory() as td:
        md = make_materials(Path(td) / "raw", [])
        rep = ms.audit(str(Path(td) / "nonexistent.json"), str(md),
                       str(Path(td) / "tomb.json"))
        check("W7 设定集不存在 → checked=False 且给出原因（不冒充「通过」）",
              rep["checked"] is False and bool(rep["reason"]), f"→ {rep['reason']}")


# ====================================================================== W8

def case_canon():
    print("\n[W8] canon 冻结：大纲阶段只读快照")
    from utils import material_state as ms
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td) / "setting.json"
        cp = str(Path(td) / "canon.json")
        sp.write_text(json.dumps({"characters": [{"name": "甲"}], "_meta": {"version": 1}},
                                 ensure_ascii=False), encoding="utf-8")
        check("W8 无快照时退回活稿",
              ms.canon_or_setting(str(sp), cp) == str(sp))
        ok, msg = ms.freeze_canon(str(sp), cp)
        check("W8 冻结成功", ok and Path(cp).exists(), f"→ {msg}")
        check("W8 有快照后大纲读 canon",
              ms.canon_or_setting(str(sp), cp) == cp, f"→ {ms.canon_or_setting(str(sp), cp)}")
        frozen = json.loads(Path(cp).read_text(encoding="utf-8"))
        check("W8 快照带冻结时间（可追溯）",
              (frozen["_meta"].get("canon_frozen_at") or ""), f"→ {frozen['_meta']}")
        check("W8 canon_state 可查", ms.canon_state(cp)["exists"] is True)
        check("W8 撤销后退回活稿",
              ms.drop_canon(cp) and ms.canon_or_setting(str(sp), cp) == str(sp))
        ok2, msg2 = ms.freeze_canon(str(Path(td) / "nope.json"), cp)
        check("W8 设定集不存在时冻结要明确失败", ok2 is False, f"→ {msg2}")


# ====================================================================== W9

def case_prompt_section():
    print("\n[W9] 墓碑要真的进提示词（否则模型不知道不能复活）")
    from utils import material_state as ms
    with tempfile.TemporaryDirectory() as td:
        tp = str(Path(td) / "tomb.json")
        ap = str(Path(td) / "audit.json")
        check("W9 无墓碑无历史 → 不产生空小节（不污染提示词）",
              ms.status_context_section(tp, ap) == "")
        ms.add_tombstone("庚", "作者明确废弃", path=tp)
        sec = ms.status_context_section(tp, ap)
        check("W9 有墓碑 → 含「不得复活」与条目名",
              "不得复活" in sec and "庚" in sec and "作者明确废弃" in sec,
              f"→ {sec[:90]}")
        check("W9 旧入口 tombstones_prompt_section 仍可用（兼容）",
              "庚" in ms.tombstones_prompt_section(tp))


# ====================================================================== W11

def case_round_inheritance():
    print("\n[W11] 多回合继承：上一轮待裁决分歧必须进本轮提示词（开工单 §四.c）")
    from utils import material_state as ms
    with tempfile.TemporaryDirectory() as td:
        ap = Path(td) / "merge_audit.json"
        tp = str(Path(td) / "tomb.json")
        ap.write_text(json.dumps({
            "audited_at": "2026-10-03T09:00:00",
            "counts": {"adopted": 3, "uncertain": 1, "rejected": 1},
            "review_conflicts": [{
                "name": "甲", "field": "能力", "severity": "review",
                "values": [{"value": "控火", "file": "甲_角色卡.md", "source": "库A"},
                           {"value": "控水", "file": "甲_角色卡2.md", "source": "库B"}],
            }],
        }, ensure_ascii=False), encoding="utf-8")

        sec = ms.status_context_section(tp, str(ap))
        check("W11 上一轮分歧进提示词（带双方出处）",
              "甲" in sec and "能力" in sec and "控火" in sec
              and "甲_角色卡.md" in sec and "甲_角色卡2.md" in sec, f"→ {sec[:160]}")
        check("W11 明确标注「继承、不得当成已定」", "继承" in sec and "未裁决" in sec,
              f"→ {sec[:160]}")
        check("W11 带上轮计数（多回合可读）",
              "上一轮结论" in sec and "uncertain" in sec, f"→ {sec[:160]}")

        # 历史丢失 → 只剩墓碑；不得因此炸掉
        sec2 = ms.status_context_section(tp, str(Path(td) / "nope.json"))
        check("W11 历史文件缺失时安全降级（不炸、不产生假历史）",
              sec2 == "", f"→ {sec2!r}")

        # 审计文件损坏 → WARN 且按无历史处理
        bad = Path(td) / "bad.json"
        bad.write_text("{ 坏", encoding="utf-8")
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = ms.load_audit(str(bad))
        check("W11 审计文件损坏 → WARN 且返回 None（不静默）",
              got is None and "WARN" in buf.getvalue(), f"→ {buf.getvalue()[:80]}")

        # 单源护栏：提示词模板里只有一个状态占位，不许残留旧名
        from utils.file_io import read_text
        tpl = read_text("prompts/stage1_materials.md")
        check("W11 模板用 {{status_context}} 且无残留 {{tombstones}}（单源）",
              "{{status_context}}" in tpl and "{{tombstones}}" not in tpl)


# ====================================================================== W12

def case_canon_freezes_stage2_input():
    print("\n[W12] canon 冻结生效：改活稿后 stage2 输入**逐字不变**（开工单 §四.b）")
    from utils import material_state as ms
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td) / "setting.json"
        cp = str(Path(td) / "canon.json")
        v1 = {"characters": [{"name": "甲", "role": "旧身份"}],
              "world": {}, "plot_fragments": [], "timeline": []}
        sp.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
        ms.freeze_canon(str(sp), cp)
        frozen_text = Path(cp).read_text(encoding="utf-8")

        old_canon, old_setting = ms.CANON_PATH, ms.SETTING_PATH
        ms.CANON_PATH, ms.SETTING_PATH = cp, str(sp)
        try:
            import importlib
            import stage2_outline
            importlib.reload(stage2_outline)
            body1 = stage2_outline.build_task({}, {"book": {"name": "测试书"}})

            # 改动活稿（模拟用户改素材/手改设定集）
            v2 = {"characters": [{"name": "甲", "role": "新身份"},
                                 {"name": "乙", "role": "新增角色"}],
                  "world": {}, "plot_fragments": [], "timeline": []}
            sp.write_text(json.dumps(v2, ensure_ascii=False), encoding="utf-8")

            body2 = stage2_outline.build_task({}, {"book": {"name": "测试书"}})
            check("W12 改活稿后 stage2 任务文本**逐字不变**", body1 == body2,
                  "→ 输入被活稿影响了")
            check("W12 canon 快照内容本身未被改写",
                  Path(cp).read_text(encoding="utf-8") == frozen_text)
            check("W12 任务里指向的是 canon 而不是 setting.json",
                  "canon.json" in body1, f"→ 未指向 canon")
        finally:
            ms.CANON_PATH, ms.SETTING_PATH = old_canon, old_setting
            import importlib
            import stage2_outline
            importlib.reload(stage2_outline)


def case_stage2_falls_back_without_canon():
    print("\n[W13] 无 canon 时 stage2 退回活稿（不破坏既有流程）")
    from utils import material_state as ms
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td) / "setting.json"
        sp.write_text(json.dumps({"characters": [{"name": "甲"}]}, ensure_ascii=False),
                      encoding="utf-8")
        cp = str(Path(td) / "canon.json")
        old_canon, old_setting = ms.CANON_PATH, ms.SETTING_PATH
        ms.CANON_PATH, ms.SETTING_PATH = cp, str(sp)
        try:
            import importlib
            import stage2_outline
            importlib.reload(stage2_outline)
            body = stage2_outline.build_task({}, {"book": {"name": "测试书"}})
            check("W13 无快照 → 退回 setting.json",
                  "canon.json" not in body and "setting.json" in body)
        finally:
            ms.CANON_PATH, ms.SETTING_PATH = old_canon, old_setting
            import importlib
            import stage2_outline
            importlib.reload(stage2_outline)


def main():
    print("=" * 70)
    print("素材库状态机（A3）回归")
    print("=" * 70)
    case_parse_card()
    case_conflicts_severity()
    case_tombstones()
    case_audit_and_status()
    case_canon()
    case_prompt_section()
    case_round_inheritance()
    case_canon_freezes_stage2_input()
    case_stage2_falls_back_without_canon()
    print("\n" + "=" * 70)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
