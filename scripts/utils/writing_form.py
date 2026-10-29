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


# ---------------------------------------------------------------- 写法指导

def style_files_exist(form):
    """该文体的写法指导文件是否**真的存在**（不看内容非空）。

    ⚠️ 不能用 `style_blocks(form)[0] != ""` 判断（2026-10-20 实测踩到）：
    `style_blocks` 对未接的文体**回落叙事**并返回叙事正文 → 非空 →
    界面于是显示「已接入」，用户以为书信体有书信体的提示词。回落是回落，接入是接入。
    """
    from pathlib import Path as _P
    code = form if form in FORMS else DEFAULT_FORM
    for kind in ("style", "para"):
        if not (_P("prompts/style") / ("%s_%s.md" % (code, kind))).exists():
            return False
    return True


def style_blocks(form, chosen=None):
    """读「文风规则」「段落规则」两段 → (style, para, warning)。

    **2026-10-29 拆锁（R22 / AC-22）：默认不注入任何风格规格。**
    为什么：`prompts/style/narrative_style.md` 那七条（不渲染情绪 / 不解释心理 /
    严格客观叙事……）此前是**所有叙事项目必注入**的默认值 —— 作者从没选过，
    系统却替他定了腔调。10-28 复盘认定这套规格正是"伪具体"的配方。
    现在只有 `book.style_rule` 显式填了文体代码才注入（文件仍在，可选，不默认）。

    `chosen` 为空 → 返回空串（stage4 的 `{{style_rules}}` 渲染为空，AC-22-1）。
    """
    from pathlib import Path as _P
    from utils.file_io import read_text

    if not chosen:
        return "", "", ""

    code = chosen if chosen in FORMS else DEFAULT_FORM
    warn = ""

    def _read(c):
        out = {}
        for kind in ("style", "para"):
            pth = _P("prompts/style") / ("%s_%s.md" % (c, kind))
            out[kind] = read_text(pth).strip() if pth.exists() else None
        return out

    got = _read(code)
    if code != DEFAULT_FORM and (got["style"] is None or got["para"] is None):
        warn = ("文体「%s」的写法指导尚未接入（缺 prompts/style/%s_style.md 或 _para.md）"
                "—— 本轮暂用「%s」的文风块" % (form_spec(code)["label"], code,
                                              form_spec(DEFAULT_FORM)["label"]))
        code = DEFAULT_FORM
        got = _read(code)
    if got["style"] is None or got["para"] is None:
        return "", "", (warn or "写法指导文件缺失：prompts/style/%s_{style,para}.md" % code)
    return got["style"], got["para"], warn

# ---------------------------------------------------------------- 评审提示词裁剪
# 2026-10-20：stage5 的提示词原来硬写「七维 + 审美五问」，散文项目照样被要求
# 逐条举证七维、还要给"对话区分度"作证。校验侧虽然标 N/A 不计分，但**token 已经花掉**，
# 账本里还会留下模型为凑数硬编的证据。这里按文体把提示词本身裁掉。

# 维度的「含义」说明（只用于提示词文案；判据真源仍是 review_ledger.DIMENSIONS）
_DIM_HINT = {
    "plot": ("情节连贯性", "主线支线是否咬合、转折是否有铺垫"),
    "timeline": ("时序一致性", "时间线是否前后矛盾"),
    "space": ("空间合理性", "地理与场景转换是否说得通"),
    "reference": ("名词指代清晰", "指代是否产生歧义（他/她/它、绰号、代称）"),
    "relation": ("人物关系自洽", "互动是否符合既有关系"),
    "persona": ("人设统一", "行为与背景设定是否一致"),
    "commonsense": ("常识与科学", "是否违背常识或本书世界观内的规则"),
}
_Q_HINT = {
    "prose": ("描写质感", "有没有具体的感官细节，而不是形容词堆砌"),
    "technique": ("叙事手法", "视角、节奏、信息给出的方式有没有讲究"),
    "dialogue": ("对话区分度", "不同角色说话，能不能听出是谁"),
    "diction": ("用词质量", "有没有用词重复、口头禅式表达、AI 腔"),
    "impact": ("情感打动力", "读完这一段，读者会不会被推动一下"),
}


def review_scope(app, path_review):
    """生成 stage5 的「评审账本」整块提示词（按文体裁剪维度与问题）。

    app 来自 `applicability()`；path_review 是账本文件绝对路径。
    返回的文本可直接替换模板里的 `{{review_scope}}`。
    """
    dims = [c for c in _DIM_HINT if c in (app.get("review_dims") or [])]
    qs = [c for c in _Q_HINT if c in (app.get("aesthetic") or [])]
    label = app.get("label") or form_spec(DEFAULT_FORM)["label"]
    n_all_d, n_all_q = len(_DIM_HINT), len(_Q_HINT)

    # 标题：叙事小说沿用「七维评审账本」这个既有名字；其它文体不说谎（散文只有 1 维）
    title = ("## 七维评审账本（**必须**，结构化）" if len(dims) == n_all_d
             else "## 评审账本（本文体 %d 维，**必须**，结构化）" % len(dims))
    lines = [title, ""]
    if len(dims) < n_all_d or len(qs) < n_all_q:
        lines += ["本文体是**%s**。下面列出的维度/问题就是本次**全部**要评的，"
                  "没列出来的（%s）**不要输出** —— 输出了也不计入。"
                  % (label,
                     "、".join(_DIM_HINT[c][0] for c in _DIM_HINT if c not in dims) or "无")]
        lines.append("")

    lines += ["对**每一章**逐维打分（0-10 整数），每维**必须**从本章原文里摘一句原句作举证：", ""]
    lines += ["本次评 **%d** 维：如果本文体不是叙事小说，有些维度根本无从谈起"
              "（散文没有主线情节、没有人物关系），那就**不要**硬凑。" % len(dims), ""]
    lines += ["| dim | 维度 | 含义 |", "|---|---|---|"]
    for c in dims:
        lab, hint = _DIM_HINT[c]
        lines.append("| `%s` | %s | %s |" % (c, lab, hint))
    lines += ["", "举证纪律（**这些是硬要求，不是建议**）：", "",
              "- `quote` 必须是本章原文里**逐字存在**的一句（可以截取一句，不要改写、不要拼接两处、",
              "  不要翻译成更通顺的说法）。校验程序会拿本章全文逐字核对。",
              "- 太短的引用不算（有效字符少于 8 个，例如「他点了点头」定位不到任何一处）。",
              "- 单条引用不要超过 400 字——要引**句子**，不是整段。",
              "- 一维只引一句就够：引**最能说明这一维问题或无恙**的那句。没把握就打低分，",
              "  **不要编句子**——编的句子对不上原文，这一维**不计分**（不是扣分，是整维作废）。",
              "- 本次 %d 维缺一不可，不要合并、不要加维度、不要用中文维度名代替 `dim` 代码。"
              % len(dims), ""]

    lines += ["## 审美五问（**必须**，另开一块，不塞进上面的逻辑维度）", "",
              "逻辑维度管「有没有写错」，五问管「写得好看吗」。"
              "对**每一章**各问一句，各引一句原文：", ""]
    if len(qs) < n_all_q:
        lines += ["本文体只评 **%d** 问（%s 不适用，不要输出）。" % (
            len(qs), "、".join(_Q_HINT[c][0] for c in _Q_HINT if c not in qs)), ""]
    lines += ["| q | 问什么 | 怎么才算答好 |", "|---|---|---|"]
    for c in qs:
        lab, hint = _Q_HINT[c]
        lines.append("| `%s` | %s | %s |" % (c, lab, hint))
    lines += ["", "举证纪律（比逻辑维度更严）：", "",
              "- **一问一句，不许复用**：同一句原文不能给两问作证。重复的那一问**整问作废**",
              "  （校验程序会按句子比对，第二问起判未举证）。",
              "- 分数 0-10 整数；太短的引用不算（有效字符少于 8 个）；单条不超过 400 字。",
              "- %d 问**缺一即整块作废** —— 写不出来就给低分，别瞎编。" % len(qs), ""]

    # JSON 示例按**实际维度**生成：示例里出现七维，模型就会照着写七维
    sample = {
        "chapters": [{
            "chapter": 1,
            "summary": "一句话总评",
            "dims": [{"dim": c, "score": 8, "quote": "本章原文里的一句", "note": "为什么给这个分"}
                     for c in dims],
            "aesthetic": [{"q": c, "score": 8,
                           "quote": "一句与上面都不重复的原文", "note": "为什么给这个分"}
                          for c in qs],
        }]
    }
    import json as _json
    lines += ["输出格式（**必须**写出这个 FILE 块，路径照抄）：", "",
              "```", "===FILE: %s===" % path_review,
              _json.dumps(sample, ensure_ascii=False, separators=(",", ":")),
              "===END===", "```", "",
              "**注意**：`aesthetic` 里的 quote 必须**两两不同**；同一句不能既给逻辑维度又给审美、"
              "也不能给两问。校验程序会逐句比对，撞了就整问作废。", ""]
    return "\n".join(lines)

