# -*- coding: utf-8 -*-
"""设定集 schema 兼容回归（零 token，临时目录，真实 data/ 零触碰）。

## 这个套件为什么存在

`stage4_writing._extract_character_cards_for_chapter()` 历史上只认
**Obsidian vault schema**（`type/tags/snippet/locked`），而真实流水线
（`prompts/stage1_materials.md` + `fake_client._write_setting`）产出的是
**stage1 schema**（`role/traits/relations`）。后果：注入给写作模型的任务里
角色卡只剩「名字 + 一行裸 dict」，`role` 与 `traits` 全丢 —— 而
`prompts/stage4_writing.md` 恰恰要求「角色言行符合设定集 traits 与 relations」。

**为什么既有测试全绿**：唯一覆盖该函数的 `tests/e2e/test_all_tasks.py:71`
用的夹具（`{"name","type","tags","snippet"}`）恰好是 vault schema，
测的是生产中不走的那条路。本套件专门用 **stage1 schema** 建夹具，
把那条路补上（否则修了也还是绿）。

断言分五节：A 两种 schema 都能读全 / B 大纲解析 / C 别名与 id 匹配 /
D 实体类型判定（治「480 个碎片角色」）/ E 反向判据（负例必须为空）。
"""
import json
import shutil
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


# ---------------------------------------------------------------- 夹具

def stage1_setting():
    """**真实流水线**产出的 schema（prompts/stage1_materials.md 声明）。"""
    return {
        "characters": [
            {"id": "c001", "name": "露汐",
             "role": "绒花科学院研究员，冷淡理性，对人体实验有负罪感",
             "traits": ["冷静", "克制", "负罪感深重"],
             "relations": [{"to": "暮雨", "type": "同事"}],
             "source": "露汐_旋转矩阵秘闻录.md"},
            {"id": "c002", "name": "暮雨",
             "role": "科学院院长，隐藏身份为神格残片",
             "traits": ["沉默", "护短", "疲惫"],
             "relations": [{"to": "露汐", "type": "上级"}],
             "source": "暮雨_旋转矩阵秘闻录.md"},
            # 非人物条目混在 characters 里（归档数据的真实形态）
            {"id": "w001", "name": "绒花帝国魔法管制法",
             "role": "帝国颁布的魔法管制法规",
             "traits": ["强制备案"], "relations": [], "source": "法典.md"},
            {"id": "w002", "name": "AX-B-16型隐形空天战机",
             "role": "圣卫军装备的隐形战机",
             "traits": ["隐身"], "relations": [], "source": "装备.md"},
        ],
        "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
        "plot_fragments": [], "timeline": [],
    }


def vault_setting():
    """vault 联动产出的 schema（obsidian_integrate.build_setting_from_vault）。"""
    return {
        "characters": [
            {"name": "露汐", "path": "角色/露汐.md", "tags": ["人类", "研究员"],
             "type": "主角", "snippet": "冷静克制的研究员", "locked": True,
             "relations": ["暮雨"], "source": "vault"},
        ],
        "world": {"locations": [], "factions": [], "concepts": [], "all": []},
        "plot_fragments": [], "timeline": [],
    }


def write_fixtures(tmp, setting, outline_text):
    sp = tmp / "setting.json"
    op = tmp / "01.md"
    sp.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")
    op.write_text(outline_text, encoding="utf-8")
    return sp, op


def main():
    global PASS, FAIL
    import stage4_writing as s4
    import utils.setting_schema as ss

    tmp = Path(tempfile.mkdtemp(prefix="nf_setting_schema_"))
    try:
        # ============================================================ A
        print("\n[A] 两种 schema 都能读全（本轮修复的核心）")
        sp, op = write_fixtures(tmp, stage1_setting(),
                                "## 第1章 夜班\n涉及角色：露汐、暮雨\n")
        cards = s4._extract_character_cards_for_chapter(str(sp), str(op))
        ok("露汐" in cards and "暮雨" in cards, "stage1 schema 匹配到两个角色")
        ok("身份：绒花科学院研究员" in cards,
           "**stage1 schema 的 role 落地了**（修前此处为裸 dict，role 全丢）")
        ok("性格：冷静、克制、负罪感深重" in cards,
           "**stage1 schema 的 traits 落地了**（修前 trait 全丢）")
        ok("关系：暮雨（同事）" in cards,
           "stage1 的关系 dict 被渲染为可读文本（非裸 dict）")
        ok("{'to'" not in cards, "角色卡里不含裸 dict 字面量")

        sp, op = write_fixtures(tmp, vault_setting(),
                                "## 第1章 夜班\n涉及角色：露汐\n")
        cards_v = s4._extract_character_cards_for_chapter(str(sp), str(op))
        ok("身份：主角" in cards_v, "vault schema 的 type → 身份")
        ok("性格：人类、研究员" in cards_v, "vault schema 的 tags → 性格")
        ok("简介：冷静克制的研究员" in cards_v, "vault schema 的 snippet → 简介")
        ok("locked 条目，绝对不可违逆" in cards_v, "vault schema 的 locked 被识别")

        # ============================================================ B
        print("\n[B] 大纲「涉及角色」解析（容忍 id 前缀与括号描述）")
        names = s4._involved_names("涉及角色：c001 露汐（写病历）、小林（夜班护士）、暮雨\n")
        ok("露汐" in names, "从 `c001 露汐（写病历）` 拆出「露汐」")
        ok("c001" in names, "同时保留 ASCII id 候选")
        ok("暮雨" in names, "无描述项直接取出")
        ok("小林" in names, "纯中文 + 描述项可取出")
        ok(s4._involved_names("## 第1章\n（无涉及角色行）") == [],
           "无「涉及角色」行 → 空列表（不误配）")

        # ============================================================ C
        print("\n[C] 别名 / id 匹配（防「恒为空」）")
        ok(ss.base_name("暮雨（神格）") == "暮雨", "base_name 剥括号后缀")
        ok(ss.base_name("露汐 · 某形态") == "露汐", "base_name 剥点号后缀")
        ok(ss.base_name("露汐") == "露汐", "base_name 对无后缀名不变")
        setting_alias = {"characters": [{"id": "c1", "name": "暮雨（神格）",
                                        "role": "神格残片", "traits": ["沉默"]}]}
        sp, op = write_fixtures(tmp, setting_alias, "## 第1章\n涉及角色：暮雨\n")
        ok("暮雨（神格）" in s4._extract_character_cards_for_chapter(str(sp), str(op)),
           "大纲写「暮雨」能命中设定集「暮雨（神格）」（别名根名匹配）")
        setting_id = {"characters": [{"id": "luxi", "name": "露汐", "role": "研究员"}]}
        sp, op = write_fixtures(tmp, setting_id, "## 第1章\n涉及角色：luxi 露汐\n")
        ok("露汐" in s4._extract_character_cards_for_chapter(str(sp), str(op)),
           "按 id + 名匹配成功")

        # ============================================================ D
        print("\n[D] 实体类型判定（治 material_review「480 个碎片角色」）")
        ok(ss.is_character({"name": "露汐", "role": "主角"}), "role=主角 → 是角色")
        ok(ss.is_character({"id": "c001", "name": "露汐", "role": "研究员"}),
           "id 以 c 开头 → 是角色")
        ok(not ss.is_character({"name": "绒花帝国魔法管制法", "role": "魔法管制法规"}),
           "**「绒花帝国魔法管制法」不再被当作角色**")
        ok(not ss.is_character({"name": "AX-B-16型隐形空天战机", "role": "圣帝军装备的隐形战机"}),
           "**「AX-B-16型隐形空天战机」不再被当作角色**")
        ok(not ss.is_character({"name": "茶叶统计", "role": "帝国的茶叶统计表"}),
           "「茶叶统计」不再被当作角色")
        ok(not ss.is_character({"name": "月兔之城（城邦）", "role": "月兔族聚居城邦"}),
           "「月兔之城（城邦）」不再被当作角色")
        ok(not ss.is_character({"name": "净源司", "role": "帝国净源司"}),
           "「净源司」不再被当作角色")
        ok(not ss.is_character({"name": "魔法使自律委员会", "role": "魔法使自律委员会"}),
           "「魔法使自律委员会」不再被当作角色")
        ok(ss.is_character({"name": "露汐", "type": "角色卡"}),
           "显式 card_type=角色 → 是角色")
        ok(ss.is_character({"name": "某无字段条目"}, kind_hint="characters"),
           "无任何类型字段时退回容器判据（不静默丢条目）")

        st = ss.normalize_setting(stage1_setting())
        ok(st["character_count"] == 2, "归一统计：2 个真角色")
        ok(st["non_character_count"] == 2, "归一统计：2 个非人物条目")
        ok(st["alias_groups"] == {}, "无别名的设定集 alias_groups 为空")

        st_a = ss.normalize_setting({"characters": [
            {"name": "暮雨", "role": "院长"}, {"name": "暮雨（神格）", "role": "神格"}]})
        ok(st_a["alias_groups"].get("暮雨") == ["暮雨", "暮雨（神格）"],
           "别名分组识别「暮雨 / 暮雨（神格）」")

        # ============================================================ E
        print("\n[E] 反向判据：负例必须为空 / 不得误报")
        ok(not ss.is_character("我不是 dict"), "非 dict 输入不抛异常且判否")
        ok(ss.normalize_character({"role": "无名"}) is None, "无 name 的条目归一为 None")
        ok(ss._truthy_locked({"locked": "false"}) is False,
           "locked 写成字符串 'false' → 判否（不误报硬约束）")
        ok(ss._truthy_locked({"locked": True}) is True, "locked=True → 判真")
        ok(ss._truthy_locked({}) is False, "无 locked 字段 → 判否")
        sp, op = write_fixtures(tmp, stage1_setting(), "## 第1章\n涉及角色：王大明\n")
        ok(s4._extract_character_cards_for_chapter(str(sp), str(op)) == "",
           "大纲提到的角色不在设定集 → 返回空串（不编造角色卡）")
        sp, op = write_fixtures(tmp, {"characters": []}, "## 第1章\n涉及角色：露汐\n")
        ok(s4._extract_character_cards_for_chapter(str(sp), str(op)) == "",
           "设定集无角色 → 返回空串")

        # ============================================================ F
        print("\n[F] 真实归档数据回归（ROSA vault 快照，只读）")
        # 这批数据是本判据的**唯一来源**：480 条，type 全空、locked 全假、
        # tags 全空 —— 「显式字段判类型」对它完全不生效，只能靠 path 与名字。
        # 另有一个必须记住的事实：**每条目在数组里出现两次**
        # （build_setting_from_vault 把 concepts 与 all 一起展开，85 组完全重复）。
        snap = ROOT / "history" / "20260917_191317_stage2_done" / "data" / "setting" / "setting.json"
        if not snap.exists():
            print("  [SKIP] 未找到 ROSA 快照（换机器/清理 history 后跳过，不算失败）")
        else:
            real = json.loads(snap.read_text(encoding="utf-8"))
            real_norm = ss.normalize_setting(real)
            n_dedup = len(real_norm["characters"])
            n_char = real_norm["character_count"]
            n_non = real_norm["non_character_count"]

            ok(len(real["characters"]) == 480, "快照含 480 条原始 characters")
            ok(real_norm["duplicate_count"] > 0,
               f"**检出重复条目（实测 {real_norm['duplicate_count']} 组）**："
               "vault 把 concepts 与 all 一起展开导致每条写两遍")
            ok(n_dedup == real_norm["raw_count"] - real_norm["duplicate_count"],
               "去重后条目数 = 原始 - 重复")
            ok(n_char + n_non == n_dedup, "人物 + 非人物 = 去重后总数（无条目被丢）")

            # 修前：character_count=480 / non_character_count=0，且角色数被重复计数翻倍
            ok(n_non >= 300,
               f"**非人物被正确分出（实测 {n_non} 条，修前为 0）**")
            ok(70 <= n_char <= 100,
               f"**真角色收敛到 {n_char} 条**（修前为 480，含 85 组重复）")

            # 抽取真值样本：这些人/物必须各归其位
            by_name = {}
            for c in real_norm["characters"]:
                by_name.setdefault(c["name"], c)
            for who in ("露汐", "暮雨", "凤凰", "塔罗斯", "月神", "风灵", "泽代"):
                ok(by_name.get(who, {}).get("is_character") is True,
                   f"真角色「{who}」判为角色")
            for what in ("绒花帝国魔法管制法", "AX-B-16型隐形空天战机", "茶叶统计",
                         "月兔之城", "净源司", "幻境文明", "月兔", "神明",
                         "伏夜提加", "玄荆", "余白", "净蚀者", "花之骑士"):
                ok(by_name.get(what, {}).get("is_character") is False,
                   f"非人物「{what}」判为非角色")

            # 索引页：path 无子目录 → 退回名字判据 → 「列表」拦下
            ok(by_name.get("碎片角色列表", {}).get("is_character") is False,
               "索引页「碎片角色列表」不被当作角色（path 判据留的缺口由词表补上）")

            # 反向：kind_hint 不得先于名字判据生效 —— 这条守着「修复空转」这个坑
            ok(ss.is_character({"name": "绒花帝国魔法管制法"}, kind_hint="characters") is False,
               "**kind_hint=characters 不得覆盖名字判据**（修前正是它让词表空转）")

            # 去重不得误伤：同名不同 path 应各留一条
            two = ss.normalize_setting({"characters": [
                {"name": "月兔", "path": "03 设定\\08 种族\\月兔.md"},
                {"name": "月兔", "path": "03 设定\\14 秩序与规则\\月兔.md"},
            ]})
            ok(len(two["characters"]) == 2,
               "同名不同 path → 各留一条（去重键含 path，不误伤同名异实体）")
            same = ss.normalize_setting({"characters": [
                {"name": "露汐", "path": "03 设定\\01 人物\\02 新作人物\\露汐.md"},
                {"name": "露汐", "path": "03 设定\\01 人物\\02 新作人物\\露汐.md"},
            ]})
            ok(len(same["characters"]) == 1 and same["duplicate_count"] == 1,
               "同名同 path → 判定为重复，只留一条")

        print("\n" + "=" * 60)
        print("合计: %d 通过 / %d 失败" % (PASS, FAIL))
        print("=" * 60)
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
