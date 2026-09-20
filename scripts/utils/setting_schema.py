# -*- coding: utf-8 -*-
"""设定集条目 schema 归一（**唯一事实来源**）。

## 为什么需要这个模块

`data/setting/setting.json` 历史上由**两个生产者**写入，字段集互不相同：

| 生产者 | 角色条目字段 | 何时产生 |
|---|---|---|
| `prompts/stage1_materials.md` + `fake_client._write_setting` | `id / name / role / traits / relations / source` | **真实流水线**（stage1 归并） |
| `obsidian_integrate.build_setting_from_vault` | `name / path / tags / type / snippet / source` | vault 联动路径 |

而消费者（`stage4_writing._extract_character_cards_for_chapter`）此前**只认 vault 那套**，
于是真实流水线下注入给写作模型的任务里，角色卡只剩「名字 + 一行裸 dict」——
`role`（身份）与 `traits`（性格）全丢，而 `prompts/stage4_writing.md` 恰恰要求
「角色言行符合设定集 traits 与 relations」。且 `locked` 在两个生产者那里都**从不置真**，
所以「locked 条目不可违逆」在直连流水线上一直是**空约束**。

本模块把「两边字段如何对齐」收敛为**一处**实现：消费者不再挑食，
生产者也无需改（旧文件继续可读）。

## 字段对照

| 语义 | stage1 schema | vault schema | 归一后 |
|---|---|---|---|
| 身份/定位 | `role` | `type` | `kind` |
| 性格/特征 | `traits`（list[str]） | `tags`（list[str]） | `traits` |
| 简介 | —（可由 role 兜底） | `snippet` | `snippet` |
| 能力/特殊设定 | `abilities` | `tags` 中的能力词 | `abilities` |
| 关系 | `relations`（list[dict] 或 list[str]） | `relations` | `relations` |
| 硬约束 | `locked`（历史恒假） | `locked`（vault frontmatter） | `locked` |

**注意**：归一**不做语义推断**（不猜谁是主角）。它只把两个 schema 的
同义字段对齐，并如实区分「有值 / 无值」——判断留给 `material_review`。
"""
import re

# stage1 schema → 内部语义
_ROLE_KEYS = ("role", "type", "kind", "identity", "定位")
_TRAIT_KEYS = ("traits", "tags", "特征", "性格")
_ABILITY_KEYS = ("abilities", "skills", "ability", "能力")
_SNIPPET_KEYS = ("snippet", "summary", "desc", "description", "简介")
_RELATION_KEYS = ("relations", "relation", "relations_src", "关系")
_LOCKED_KEYS = ("locked", "lock", "immutable")

# 能力词表（与 material_review.ABILITY_WORDS 同源，避免两处漂移）
ABILITY_WORDS = (
    "能力", "使用", "掌控", "牵引", "冰封", "战斗", "剑", "法术", "魔法",
    "治疗", "侦查", "操控", "之力", "通晓", "擅长",
)


def _as_list(value):
    """把任意值收敛为 list[str]（去空、转 str、保序去重）。"""
    if value is None or value is False:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out, seen = [], set()
    for it in items:
        s = str(it).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _raw_list(value):
    """把任意值收敛为 list（**保留元素原始类型**）。

    与 `_as_list` 的区别：`_as_list` 会把 dict 转成 str，而关系项
    `{"to": "暮雨", "type": "同事"}` 必须先当 dict 处理才能取出字段名。
    所以关系归一用这个，纯文本类字段才用 `_as_list`。
    """
    if value is None or value is False:
        return []
    if isinstance(value, (list, tuple, set)):
        return [it for it in value if it not in (None, "", [], {})]
    return [value]


def _first(entry, keys):
    """按优先级取第一个「非空」值。"""
    for k in keys:
        if k in entry and entry[k] not in (None, "", [], {}, False):
            return entry[k]
    return None


def _rel_to_text(rel):
    """关系项 → 可读文本。

    stage1 schema 用 `{"to": "暮雨", "type": "同事"}`，
    vault / 手写常用 `"暮雨（同事）"` 或 `"暮雨"`。两种都要能读。
    """
    if isinstance(rel, dict):
        other = _first(rel, ("to", "target", "name", "who", "对象", "name_en"))
        kind = _first(rel, ("type", "kind", "rel", "关系"))
        if other and kind:
            return "%s（%s）" % (str(other).strip(), str(kind).strip())
        if other:
            return str(other).strip()
        # 没有对象名 → 退回整条的可读形态，别扔
        return " / ".join("%s:%s" % (k, v) for k, v in rel.items() if v)
    return str(rel).strip()


def _truthy_locked(entry):
    """`locked` 的真实语义：只认布尔真或显式真值字符串。

    历史坑：两个生产者**都从不置真**，所以任何 `locked` 判据以前都是空转。
    这里不发明新语义，只把「写成字符串 'false'」这类假值正确识别为 False。
    """
    for k in _LOCKED_KEYS:
        if k not in entry:
            continue
        v = entry[k]
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "yes", "1", "是", "锁定")
        return bool(v)
    return False


# vault 路径的一级/二级目录名（ROSA 目录规范）
_VAULT_ROOT = "03 设定"
_VAULT_CHARACTER_DIR = "01 人物"


def path_kind(entry):
    """用 vault `path` 判实体类型。返回 True / False / **None（不表态）**。

    ## 为什么这是最强的判据

    真实归档设定集（480 条，`source: "rosa"`）里 `type` 字段**全为空字符串**、
    `tags` 全为 `[]` —— 也就是说「靠显式字段判类型」对这批数据完全不生效。
    唯一可靠的信号是 `path`：ROSA 的目录结构本身就是类型标注。

    实测统计（`history/20260917_191317_stage2_done`）：

    | path 形态 | 条数 | 真值 |
    |---|---|---|
    | `03 设定\\01 人物\\<子类目>\\<名字>.md` | **168** | 全部是人物 |
    | `03 设定\\01 人物\\碎片角色列表.md` | 2 | 索引页，**不是**人物 |
    | `03 设定\\<其他类目>\\...` | 310 | 全部不是人物 |

    判据：**`01 人物` 之后还有子目录的才是人物**。
    这条在 480 条上零误判，比 name 后缀词表可靠得多
    （词表永远追不上「岳」「汐」「宁」这类人名尾字）。

    非 ROSA 结构（stage1 直连流水线不写 `path`）→ 返回 None，
    交回 `is_character` 继续用其它判据。**这个函数不猜。**
    """
    if not isinstance(entry, dict):
        return None
    raw = entry.get("path")
    if not raw:
        return None
    seg = [s for s in str(raw).replace("/", "\\").split("\\") if s]
    if len(seg) < 2 or seg[0] != _VAULT_ROOT:
        return None
    if seg[1] != _VAULT_CHARACTER_DIR:
        return False
    # `01 人物\<子类目>\<名字>.md` → 是人物
    # `01 人物\<名字>.md`（索引页如「碎片角色列表」）→ 交回名字判据
    return True if len(seg) >= 4 else None


# 非人物名词后缀。**只在 name 上匹配**，判据见 is_character 文档。
# 词表覆盖的是「机构/法规/文明/场所/装备」这类后缀，
# 从真实数据 310 条非人物 path 条目的命名中归纳，并保留泛化项。
NON_CHARACTER = (
    # 法规文书
    "法", "条例", "协定", "章程", "协议", "法案", "规定", "制度", "体系",
    "总纲", "总览", "编制", "原理", "分类", "序列",
    # 计划与统计
    "统计", "计划", "草案", "报告", "档案",
    # 装备器械
    "装备", "机型", "战机", "战舰", "武器", "导弹", "装置", "屏障", "基站",
    "平台", "设备", "胶囊", "系统", "器", "枪", "剑",
    # 组织机构
    "司", "厅", "院", "会", "部", "军", "局", "署", "堂", "阁",
    "教廷", "委员会", "帝国", "共和国", "王国", "联邦",
    # 空间
    "星域", "星系", "星球", "城邦", "之城", "大陆", "世界", "空间站",
    "镇", "城", "村", "岛", "关", "山", "江", "河", "殿", "塔", "楼",
    "宫", "府", "厅堂", "场", "馆", "室", "院区", "设施", "据点", "基地",
    "营地", "庇护所", "墓", "山谷", "广场", "中心", "工厂", "学校", "中学",
    # 文明种族文化
    "文明", "种族", "文化", "节日", "历法", "礼仪", "习俗", "观念", "模式",
    "形态", "构成", "战术", "战", "战役", "灾变", "异变", "事件", "之盟",
    "之役", "之战", "之死", "仪式", "祭", "祭祀",
    # 索引页 / 说明页
    "说明", "列表", "总表", "索引", "词条", "总录",
    # 生物怪物（群落性条目）
    "生物", "怪物", "兽", "妖兽", "妖精", "神",
    # 场景
    "世界",
)


def is_character(entry, kind_hint=None):
    """判断一个条目是否应当被当作「角色/人物」对待。

    **这是修正 material_review「480 个碎片角色」问题的关键判据**：
    归档报告里「绒花帝国魔法管制法」「茶叶统计」「AX-B-16型隐形空天战机」
    被按角色维度打分，是因为只看「在不在 characters 数组里」。

    判据顺序（**确定性优先，误判代价不对称**）：

    1. **显式字段**：`card_type` / `kind_class` / `entity_type` 明说类型 → 直接采信。
    2. **vault path 权威**：`path_kind()` 有结论就用它。实测 480 条零误判。
    3. **id 编号惯例**：stage1 给角色分配 `c001` 这类编号 → 是。**真实流水线的最强信号**。
    4. **名字判据**：在 **name** 上（不是 role 上！）找非人物名词。
       角色名是短专名（露汐/暮雨），设定条目名会带机构后缀（净源司）
       或物件后缀（魔法管制法）。
       **只在 name 上判断是刻意的**：真实数据里「露汐」的 role 是
       「绒花科学院研究员…」，里面有「院」；在 role 上匹配会把她误杀。
    5. **kind_hint 兜底**：显式声明这个条目来自 `characters` 数组。
       ⚠️ **它必须在 name 判据之后**。这里是本模块踩过的坑：
       曾经把兜底放在 name 判据**之前**，结果 480 条**全部**判为角色
       （`kind_hint="characters"` 一票通过，词表从未被执行），
       体检照样报「480 个碎片角色」——**修复看起来生效了，实际是空转**。
       现在只把 2 条救回（`碎片角色列表` 这类无后缀的索引页，靠「列表」判掉），
       其余 168 条真角色由 vault path 命中。

    误判方向的不对称性：把非人物条目当角色 → `material_review` 白跑一趟（浪费）；
    把真角色当非人物 → 角色卡在写作时消失（**静默降级**）。
    所以最后兜底仍返回 True（`kind_hint` 声明过就算），宁可多算。
    """
    if not isinstance(entry, dict):
        return False

    # ---- 1 显式字段优先 ----
    for k in ("card_type", "kind_class", "entity_type"):
        v = str(entry.get(k, "") or "").strip()
        if not v:
            continue
        if v == "角色" or "角色" in v or "人物" in v:
            return True
        return False

    # ---- 2 vault path 权威 ----
    vp = path_kind(entry)
    if vp is not None:
        return vp

    # ---- 3 id 编号惯例（stage1 真实流水线的最强信号）----
    cid = str(entry.get("id", "") or "")
    if re.match(r"^(c|ch|char)\d*$", cid, re.I):
        return True

    # ---- 4 名字判据（在 name 上，不在 role 上）----
    name = str(entry.get("name", "") or "").strip()

    # ⚠️ 顺序：**非人物后缀优先**。
    # 「碎片角色列表」同时命中「角色」（人物词）与「列表」（非人物词）——
    # 若先测人物词就会一票判真，索引页又被当成角色。
    # 「列表 / 说明 / 总纲」这类后缀比「角色」更具体，所以先测它。
    if any(name.endswith(w) for w in NON_CHARACTER):
        return False

    CHARACTER_HINTS = ("角色", "人物", "主角", "配角", "龙套")
    if any(w in name for w in CHARACTER_HINTS):
        return True

    raw_kind = ""
    for k in _ROLE_KEYS:
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            raw_kind = v.strip()
            break
        if isinstance(v, list) and v:
            raw_kind = " ".join(str(x) for x in v)
            break
    if any(w in raw_kind for w in CHARACTER_HINTS):
        return True

    # ---- 5 兜底：调用方声明了它来自角色数组 → 算角色（不静默丢）----
    if kind_hint in ("characters", "character", "char"):
        return True
    return False


def normalize_character(entry, kind_hint="characters"):
    """一个角色条目 → 统一内部形态 dict。

    返回键（消费者只用这些，不再直接读原始字段）：
      name / id / kind / traits / abilities / relations / snippet / locked
      / source / raw（原始条目，供调试与写回）
    """
    if not isinstance(entry, dict):
        return None
    name = str(_first(entry, ("name", "名称", "title")) or "").strip()
    if not name:
        return None

    kind = _first(entry, _ROLE_KEYS)
    if isinstance(kind, list):
        kind = "、".join(str(x) for x in kind)
    kind = str(kind or "").strip()

    traits = _as_list(_first(entry, _TRAIT_KEYS))
    abilities = _as_list(_first(entry, _ABILITY_KEYS))
    snippet = _first(entry, _SNIPPET_KEYS)
    snippet = str(snippet).strip() if snippet else ""

    rels = _raw_list(_first(entry, _RELATION_KEYS))
    relations = [t for t in (_rel_to_text(r) for r in rels) if t]

    return {
        "name": name,
        "id": str(entry.get("id", "") or "").strip(),
        "kind": kind,
        "traits": traits,
        "abilities": abilities,
        "relations": relations,
        "snippet": snippet,
        "locked": _truthy_locked(entry),
        "source": str(entry.get("source", "") or "").strip(),
        "is_character": is_character(entry, kind_hint),
        "raw": entry,
    }


def normalize_setting(setting):
    """整个设定集 → 归一后的角色列表 + 统计。

    返回 dict：
      characters: [normalize_character 的结果]
      raw_count / character_count / non_character_count / alias_groups
      / locked_count / duplicate_count

    ## 去重（2026-09-20 新增）

    vault 路径产出的设定集**每条目重复两次**：`build_setting_from_vault`
    把 `world.concepts` 与 `world.all` 一起展开，而这两个列表装的是同一批数据
    （实测：480 条 characters 里只有 394 个唯一 name，86 组完全重复）。

    后果：`material_review` 的结论清单出现
    「刘锋炎、锋炎（自然罗盘）…刘锋炎、锋炎（自然罗盘）」这种回音，
    角色卡注入时也会把同一个人写两遍。

    **判等键是 `(name, path)` 而不是 `name`** —— 同名不同物是合法的
    （真实数据里「月兔」是种族、「月兔之城」是地点；星系和人物也可能同名）。
    所以只丢「名字与路径都一致」的条目，那一定是同一条被写了两遍。
    """
    setting = setting if isinstance(setting, dict) else {}
    chars = setting.get("characters", [])
    if not isinstance(chars, list):
        chars = []
    out = []
    seen = set()
    dup = 0
    for c in chars:
        nc = normalize_character(c, kind_hint="characters")
        if nc:
            # 判等键含 path：同名不同路径不去重（可能是合理的两个实体）
            k = (nc["name"], str(nc["raw"].get("path", "") or ""))
            if k in seen:
                dup += 1
                continue
            seen.add(k)
            out.append(nc)

    # 别名分组：`暮雨` 与 `暮雨（神格）` → 同根名
    alias = {}
    for nc in out:
        alias.setdefault(base_name(nc["name"]), []).append(nc["name"])
    alias_groups = {k: v for k, v in alias.items() if len(v) > 1}

    return {
        "characters": out,
        "raw_count": len(chars),
        "character_count": sum(1 for nc in out if nc["is_character"]),
        "non_character_count": sum(1 for nc in out if not nc["is_character"]),
        "alias_groups": alias_groups,
        "locked_count": sum(1 for nc in out if nc["locked"]),
        "duplicate_count": dup,
    }


def base_name(name):
    """取别名根名：去掉括号/中括号后缀与常见变体后缀。

    「暮雨（神格）」→「暮雨」；「露汐 · 某形态」→「露汐」。
    """
    s = str(name or "").strip()
    s = re.sub(r"[（(\[【].*?[)）\]】]", "", s).strip()
    s = re.sub(r"[\s·・]+.*$", "", s).strip()
    return s or str(name or "").strip()


def build_character_card(nc, warn_locked=True):
    """归一后的角色 → 注入写作任务的角色卡文本。

    这里**不省略任何有值的字段**：`kind`（身份）与 `traits`（性格）必须落地，
    这正是修复「真实流水线下 traits 全丢」的关键。
    """
    parts = ["**%s**" % nc["name"]]
    if nc.get("kind"):
        parts.append("身份：%s" % nc["kind"])
    if nc.get("traits"):
        parts.append("性格：%s" % "、".join(nc["traits"]))
    if nc.get("abilities"):
        parts.append("能力：%s" % "、".join(nc["abilities"]))
    if nc.get("relations"):
        parts.append("关系：%s" % "、".join(nc["relations"]))
    if nc.get("snippet"):
        parts.append("简介：%s" % nc["snippet"][:200].replace("\n", " "))
    if nc.get("locked") and warn_locked:
        parts.append("⚠ locked 条目，绝对不可违逆")
    return "\n".join(parts)
