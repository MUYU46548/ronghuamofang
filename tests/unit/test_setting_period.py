# -*- coding: utf-8 -*-
"""设定条目的时期 / 演变（问题总账第 8 条）—— 零 token、临时目录、真实 data/ 零触碰。

## 这个套件为什么存在

`setting_schema` 的条目字段一直只有 name/role/snippet/abilities/relations/locked/tags/sources，
**没有时间维度**。于是"早期外观可见、后期收进皮下"这类演变型设定两种写法都会出错：

| 写法 | 以前的结果 |
|---|---|
| 两个时期写进同一句话 | `material_state.detect_conflicts` 按字面判成矛盾 —— 写对了还挨红 |
| 拆成两个不同名条目 | 永不互相比对 —— 错的那条也看不见 |

现在：条目可写可选 `period`（单期）/ `phases`（分期状态），
冲突只**同期内**比较、跨期差异进"演变"清单（不判红，漏标的标 suspect 报人），
写作注入按本章时期选一版。**没写时期的旧数据行为逐字不变**（本条是兼容性红线）。

断言分五节：A schema 归一（多种写法）/ B 角色卡注入 / C 冲突与演变 /
D 版本对照认得出"改了时期" / E 反向判据（没时期就必须一个字都不变）。

用法：python tests/unit/test_setting_period.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS = FAIL = 0


def ok(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + label)
    else:
        FAIL += 1
        print("  [FAIL] " + label)


CARD_TMPL = """# {kind}：{title}

> 来源：测试库 `设定/{kind}/{title}.md`（审定版）

## 基本信息

{fields}
## 正文

内容。
"""


def write_card(d, fname, fields, title="甲", kind="角色"):
    p = Path(d) / fname
    p.write_text(CARD_TMPL.format(
        kind=kind, title=title,
        fields="".join("- %s：%s\n" % (k, v) for k, v in fields.items())), encoding="utf-8")
    return p


# ====================================================================== A
print("\n[A] schema 归一：时期 / 演变的多种写法")


def case_normalize():
    from utils.setting_schema import normalize_period, normalize_character

    cases = [
        ({"period": "早期"}, "早期", 0, "period 单值"),
        ({"时期": "战后"}, "战后", 0, "中文键“时期”"),
        ({"era": "卷一"}, "卷一", 0, "era 英文键"),
        ({"period": {"from": "早期", "to": "后期"}}, "早期→后期", 0, "period 写成区间"),
        ({"period": {"label": "中期"}}, "中期", 0, "period 写成对象"),
        ({"phases": [{"era": "早期", "note": "外观可见"},
                     {"era": "后期", "note": "收进皮下"}]}, "", 2, "phases 对象数组"),
        ({"phases": ["早期：外观可见", "后期：收进皮下"]}, "", 2, "phases 字符串数组（冒号前是时期）"),
        ({"period": ["早期", "后期"]}, "", 2, "错把 phases 写进 period 也认"),
        ({"演变": [{"era": "早期", "外观": "可见"}]}, "", 1, "中文键“演变”+ 扁平字段"),
    ]
    for entry, label, n_phases, name in cases:
        p = normalize_period(entry)
        ok(p["label"] == label and len(p["phases"]) == n_phases,
           "A %s → label=%r phases=%d" % (name, p["label"], len(p["phases"])))

    flat = normalize_period({"phases": [{"era": "早期", "外观": "可见",
                                         "识别方式": "专用设备"}]})
    ok(flat["phases"] and flat["phases"][0]["fields"].get("外观") == "可见"
       and flat["phases"][0]["fields"].get("识别方式") == "专用设备",
       "A phase 里的扁平字段被收进 fields（模型常这么写）")

    # 旧条目：一个时期字段都没有 → 归一出空，且不报错
    none = normalize_period({"name": "甲", "role": "巡道工"})
    ok(none == {"label": "", "phases": []}, "A 没写时期 → label='' / phases=[]")

    nc = normalize_character({"name": "甲", "role": "巡道工",
                              "period": "卷一",
                              "phases": [{"era": "早期", "note": "可见"}]})
    ok(nc["period"] == "卷一" and len(nc["phases"]) == 1,
       "A normalize_character 带出 period / phases")

    legacy = normalize_character({"name": "乙", "role": "掌柜"})
    ok(legacy["period"] == "" and legacy["phases"] == [],
       "A 旧条目归一后 period/phases 为空（下游行为不变）")


# ====================================================================== B
print("\n[B] 写作注入：角色卡按时期分点，并写清选用规则")


def case_inject():
    from utils.setting_schema import normalize_character, build_character_card

    nc = normalize_character({
        "name": "甲", "role": "巡道工", "traits": ["沉默"],
        "phases": [{"era": "早期", "note": "肩上标识外观可见"},
                   {"era": "后期", "note": "标识收进皮下，需专用设备识别"}],
    })
    card = build_character_card(nc)
    ok("演变" in card and "早期" in card and "后期" in card,
       "B 角色卡列出两期状态")
    ok("只写本章所处时期" in card, "B 卡里写清“只写本章所处时期的那一版”")

    card2 = build_character_card(nc, chapter_text="本章已是战后后期，标识只在设备上出现。")
    ok("← 本章大纲提到这个时期" in card2 and "后期" in card2,
       "B 大纲提到某一期时，那一期被标成本章依据")
    ok("本章大纲没写具体时期" not in card2,
       "B 命中某一期后不再泛泛提示（别多说一句）")

    card3 = build_character_card(nc, chapter_text="本章没有任何时期字样。")
    ok("本章大纲没写具体时期" in card3,
       "B 大纲没提时期 → 明确说“按与本章描写相符的那一期写”")

    plain = build_character_card(normalize_character({"name": "乙", "role": "掌柜"}))
    ok("演变" not in plain and "时期：" not in plain,
       "B 没时期的条目注入文本一个字都不多（旧数据不变）")

    single = build_character_card(normalize_character({"name": "丙", "period": "战后"}))
    ok("时期：战后" in single and "只在这一时期成立" in single,
       "B 单期条目写清“只在这一时期成立”")


# ====================================================================== C
print("\n[C] 冲突判定：同期内才比，跨期是演变不是矛盾")


def case_conflicts():
    from utils.material_state import (collect_cards, detect_conflicts,
                                      detect_evolutions, card_period, audit)

    with tempfile.TemporaryDirectory() as td:
        write_card(td, "甲_早期.md", {"时期": "早期", "能力": "标识可见"})
        write_card(td, "甲_后期.md", {"时期": "后期", "能力": "标识收进皮下"})
        cards = collect_cards(td)
        ok(len(cards) == 2 and {card_period(c) for c in cards} == {"早期", "后期"},
           "C 卡上的“时期”被读出来（两张卡的时期都认）")
        ok(detect_conflicts(cards) == [],
           "C 同一条目两期各一个值 → 冲突 0（这是演变，不是矛盾）")
        evo = detect_evolutions(cards)
        ok(len(evo) == 1 and evo[0]["field"] == "能力" and evo[0]["suspect"] is False,
           "C 跨期差异进“演变”清单，且不标 suspect")

    with tempfile.TemporaryDirectory() as td:
        write_card(td, "乙_甲.md", {"时期": "早期", "能力": "控火"})
        write_card(td, "乙_乙.md", {"时期": "早期", "能力": "控水"})
        conf = detect_conflicts(collect_cards(td))
        ok(len(conf) == 1 and conf[0]["severity"] == "review",
           "C 同一时期两张卡两个值 → 冲突 1 处、review")
        ok(conf[0].get("period") == "早期",
           "C 冲突条目写明发生在哪一期（不写等于让人自己猜）")

    with tempfile.TemporaryDirectory() as td:
        write_card(td, "丙_旧.md", {"能力": "控火"})
        write_card(td, "丙_新.md", {"能力": "控水"})
        ok(len(detect_conflicts(collect_cards(td))) == 1,
           "C 回归：两张卡都没写时期 → 与改动前一样判冲突")

    with tempfile.TemporaryDirectory() as td:
        write_card(td, "丁_早期.md", {"时期": "早期", "能力": "控火"})
        write_card(td, "丁_无期.md", {"能力": "控水"})
        cards = collect_cards(td)
        ok(detect_conflicts(cards) == [],
           "C 一张有时期一张没时期 → 不当矛盾（同期桶内各只有一张）")
        evo = detect_evolutions(cards)
        ok(len(evo) == 1 and evo[0]["suspect"] is True and "没写时期" in evo[0]["note"],
           "C 但演变条目标 suspect，并说清是哪张卡没标时期（报人，不判红）")

    with tempfile.TemporaryDirectory() as td:
        write_card(td, "戊_a.md", {"时期": "早期"})
        write_card(td, "戊_b.md", {"时期": "后期"})
        ok(detect_conflicts(collect_cards(td)) == [],
           "C “时期”字段本身不参与冲突比对（早期 vs 后期不是矛盾）")

    # audit 的结果里必须带演变，否则 CLI/GUI 读不到等于没做
    with tempfile.TemporaryDirectory() as td:
        write_card(td, "辛_a.md", {"时期": "早期", "能力": "控火"})
        write_card(td, "辛_b.md", {"时期": "后期", "能力": "控水"})
        rep = audit(setting_path=str(Path(td) / "nope.json"), materials_dir=td)
        ok(rep.get("evolution_count") == 1 and len(rep.get("evolutions") or []) == 1,
           "C audit 里带出演变清单（CLI/GUI 才读得到）")
    with tempfile.TemporaryDirectory() as td:
        write_card(td, "壬_a.md", {"时期": "早期", "能力": "控火"})
        write_card(td, "壬_b.md", {"时期": "早期", "能力": "控火"})
        rep = audit(setting_path=str(Path(td) / "nope.json"), materials_dir=td)
        ok(rep.get("evolutions") == [] and rep.get("evolution_count") == 0,
           "C 没有跨期差异时 evolutions 为空列表（不是缺字段）")


# ====================================================================== D
print("\n[D] 版本对照与溯源：改了时期要看得见")


def case_versions():
    from utils.setting_versions import _field_rows
    from utils.setting_provenance import _field_value

    rows = _field_rows("character", {"name": "甲", "period": "早期"},
                       {"name": "甲", "period": "后期"})
    ok(any(r["field"] == "period" and r["label"] == "时期" for r in rows),
       "D 版本对照列出“时期”变化")
    rows2 = _field_rows("character", {"name": "甲"},
                        {"name": "甲", "phases": [{"era": "早期", "note": "可见"}]})
    ok(any(r["field"] == "period" and r["to"] for r in rows2),
       "D 从“没写”改成“写了 phases”也算一次变化")
    ok(_field_rows("character", {"name": "甲"}, {"name": "甲"}) == [],
       "D 两版都没时期 → 不产生多余行")
    ok(_field_value({"phases": [{"era": "早期", "note": "可见"}]}, "period") == "早期：可见",
       "D 溯源读 period 走同一份取数实现（不读裸键）")


# ====================================================================== E
print("\n[E] 反向判据：旧数据（无时期）行为必须逐字不变")


def case_backward_compat():
    from utils import material_state as ms
    from utils.setting_schema import build_character_card, normalize_character

    with tempfile.TemporaryDirectory() as td:
        write_card(td, "己_a.md", {"能力": "控火", "英文名": "A"})
        write_card(td, "己_b.md", {"能力": "控水", "英文名": "Alpha"})
        conf = ms.detect_conflicts(ms.collect_cards(td))
        by_field = {c["field"]: c for c in conf}
        ok(by_field.get("能力", {}).get("severity") == "review",
           "E 无时期：能力两个值 → review（原判据不动）")
        ok(by_field.get("英文名", {}).get("severity") == "auto",
           "E 无时期：低风险字段 → auto（原判据不动）")
        ok(all("period" not in c for c in conf),
           "E 无时期时不往结果里塞空 period 键（形状不变）")
        ok(ms.detect_evolutions(ms.collect_cards(td)) == [],
           "E 无时期时不会凭空报“演变”")

    nc = normalize_character({"name": "庚", "role": "掌柜", "traits": ["沉默"]})
    card = build_character_card(nc, warn_locked=False)
    ok(card == "**庚**\n身份：掌柜\n性格：沉默",
       "E 无时时期卡片文本与改动前逐字一致 → %r" % card)


for fn in (case_normalize, case_inject, case_conflicts, case_versions, case_backward_compat):
    fn()

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
