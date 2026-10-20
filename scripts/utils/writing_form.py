# -*- coding: utf-8 -*-
"""文体（form）—— 决定哪些判据适用。

## 为什么需要这个（2026-10-20，用户当场质疑）

> 「如果根本不是传统叙事小说，会不会炸」

会。整条流水线默认**传统叙事小说**，而且是硬编码假设，不是可调参数：

| 判据 | 对非叙事文体 |
|---|---|
| 七维评审（情节/时序/空间/指代/关系/人设/常识） | 散文集没有情节与人物关系 → 永远"不完整" |
| 审美五问里的"对话区分度" | 散文/诗可能整篇没有对话 |
| 章大纲「小目标」「悬念钩子」 | 散文、随笔不需要"这一章主角想做成什么" |
| 叙事深化段 / 分卷滚动 / 伏笔台账 | 散文集按主题分组，不是"卷"，也没有伏笔 |
| `verify_chapter` 段落结构（段数 <6 判退化） | 一首诗、一封短信会被判"退化" |
| `quality_checklist` 未提及角色名即报错 | 散文、纪实根本没有角色 |
| stage4 文风要求（客观叙事、不渲情、不铺陈） | 直接违反抒情/意识流/书信体的写法 |

## 这一层的职责（只做两件事）

1. **声明文体**：`config/project.yaml` 的 `book.form`，默认 `narrative`（行为与改动前一致）。
2. **说明哪些判据不适用**：`applicability(form)` 返回各判据的适用列表，
   下游据此把不适用项标成 **N/A（不计分、不算缺失、不进均值）**，而不是硬给 0 分或满分。

**边界**：本模块只是一张表。真正"按文体换提示词"（stage4 文风块、stage3 字段集）
还没接 —— 那要先有真实样本校准，不能猜。没接之前，非叙事文体**只能拿到正确的判据口径**，
拿不到对应的写法指导（summary 里会明确写「本文件体的写法指导尚未接入」）。

## 未知/未支持的文体

**不静默回落**：`resolve()` 返回 `(form, warning)`，调用方必须把 warning 打出来。
静默按叙事跑，是这套系统里最危险的一种失败 —— 用户以为"支持散文"，跑到一半全红。
"""
FORMS = {
    "narrative": {
        "label": "叙事小说",
        "desc": "有主线、有冲突、连续推进（默认）",
        "review_dims": ["plot", "timeline", "space", "reference", "relation",
                        "persona", "commonsense"],
        "aesthetic": ["prose", "technique", "dialogue", "diction", "impact"],
        "chapter_new_fields": ["小目标", "悬念钩子"],
        "deepen": True,
        "foreshadow": True,
        "volume": True,
        "must_mention_character": True,
        "para_structure": True,
        "style_prompt": "stage4_style_narrative.md",
    },
    "letter": {
        "label": "书信 / 日记体",
        "desc": "以信件或日记推进；时间跨度可跳跃，语气比情节重要",
        "review_dims": ["plot", "timeline", "reference", "relation", "persona", "commonsense"],
        "aesthetic": ["prose", "technique", "dialogue", "diction", "impact"],
        "chapter_new_fields": ["悬念钩子"],       # 不是行动章，不要求"小目标"
        "deepen": True,
        "foreshadow": True,
        "volume": True,
        "must_mention_character": True,
        "para_structure": True,
        "style_prompt": "stage4_style_letter.md",
    },
    "episodic": {
        "label": "单元 / 日常流",
        "desc": "每章一件独立小事，弱主线；靠氛围与人物日常撑住",
        "review_dims": ["plot", "space", "reference", "relation", "persona", "commonsense"],
        "aesthetic": ["prose", "technique", "dialogue", "diction", "impact"],
        "chapter_new_fields": ["悬念钩子"],
        "deepen": False,
        "foreshadow": False,
        "volume": False,
        "must_mention_character": True,
        "para_structure": True,
        "style_prompt": "stage4_style_episodic.md",
    },
    "essay": {
        "label": "散文 / 随笔集",
        "desc": "篇与篇独立；重感受与思辨，没有主线人物",
        "review_dims": ["commonsense"],            # 只剩"事实与常识"这一条对散文成立
        "aesthetic": ["prose", "technique", "diction", "impact"],   # 对话区分度 N/A
        "chapter_new_fields": [],
        "deepen": False,
        "foreshadow": False,
        "volume": False,
        "must_mention_character": False,
        "para_structure": False,                   # 散文允许极短篇、单段长文
        "style_prompt": "stage4_style_essay.md",
    },
}
DEFAULT_FORM = "narrative"
# 允许被 form_overrides 覆盖的键（写错键必须报错，不能静默忽略）
OVERRIDABLE = ("review_dims", "aesthetic", "chapter_new_fields", "deepen",
               "foreshadow", "volume", "must_mention_character", "para_structure")


def resolve(proj):
    """读 `book.form`。返回 (form_code, warning)。

    未声明 → narrative（老项目零迁移）。
    写了但不认识 → **回落 narrative 并给出 warning**（调用方必须打印）。
    """
    book = (proj or {}).get("book") or {}
    raw = str(book.get("form") or "").strip().lower()
    if not raw:
        return DEFAULT_FORM, ""
    if raw in FORMS:
        return raw, ""
    return DEFAULT_FORM, ("未知文体 book.form=%s（支持：%s）—— 已按 %s 处理；"
                          "要么改成支持的值，要么用 book.form_overrides 自定义判据适用范围"
                          % (raw, "、".join(FORMS), DEFAULT_FORM))


def form_spec(form):
    return FORMS.get(str(form or "").strip().lower()) or FORMS[DEFAULT_FORM]


def applicability(proj=None, form=None):
    """返回本项目的判据适用表（含 form_overrides）。

    overrides 只能是 `OVERRIDABLE` 里的键，且值必须是合法列表/布尔 —— 写错就抛
    `ValueError`（带可行动提示），**不静默忽略**：静默忽略等于用户以为改了其实没改。
    """
    if form:
        code, warning = (form, "") if form in FORMS else (DEFAULT_FORM,
                                                          "未知文体 %s，按 %s 处理" % (form, DEFAULT_FORM))
    else:
        code, warning = resolve(proj)
    spec = dict(form_spec(code))
    overrides = ((proj or {}).get("book") or {}).get("form_overrides") or {}
    if overrides and not isinstance(overrides, dict):
        raise ValueError("book.form_overrides 必须是映射（键：%s）" % "、".join(OVERRIDABLE))
    applied = []
    for k, v in (overrides or {}).items():
        if k not in OVERRIDABLE:
            raise ValueError("book.form_overrides 里的 `%s` 不是可覆盖项（可选：%s）"
                             % (k, "、".join(OVERRIDABLE)))
        if k.endswith("s") or k in ("review_dims", "aesthetic", "chapter_new_fields"):
            if not isinstance(v, list):
                raise ValueError("book.form_overrides.%s 必须是列表" % k)
            unknown = [x for x in v if x not in _known_codes(k)]
            if unknown:
                raise ValueError("book.form_overrides.%s 含不认识的值：%s（可选：%s）"
                                 % (k, unknown, "、".join(_known_codes(k))))
        elif not isinstance(v, bool):
            raise ValueError("book.form_overrides.%s 必须是 true/false" % k)
        spec[k] = v
        applied.append(k)
    spec["code"] = code
    spec["warning"] = warning
    spec["overrides_applied"] = applied
    # 尚未接入的部分要说出来（不假装支持）
    spec["style_prompt_wired"] = False
    return spec


def _known_codes(key):
    """各列表项允许的值集合（单一来源仍是 review_ledger 与 stage3）。"""
    if key == "review_dims":
        from utils import review_ledger as rl
        return rl.DIM_CODES
    if key == "aesthetic":
        from utils import review_ledger as rl
        return rl.AESTHETIC_CODES
    if key == "chapter_new_fields":
        from stage3_chapter_outline import NEW_FIELDS
        return NEW_FIELDS
    return []


def describe(spec):
    """一行人话：给阶段入口打印用。"""
    code = spec.get("code", DEFAULT_FORM)
    if code == DEFAULT_FORM and not spec.get("warning"):
        return "文体：%s（默认）" % spec["label"]
    dims = spec.get("review_dims") or []
    n_all = 7
    parts = ["文体：%s（%s）" % (spec["label"], spec["desc"])]
    if len(dims) < n_all:
        parts.append("七维里只有 %d 项适用（不适用项按 N/A 处理，不计分）" % len(dims))
    if not spec.get("deepen"):
        parts.append("不做叙事深化段")
    if not spec.get("volume"):
        parts.append("不做分卷滚动")
    if not spec.get("foreshadow"):
        parts.append("不做伏笔台账")
    return "；".join(parts)
