# -*- coding: utf-8 -*-
"""P0 判定规则：F01 体检 / F02 伪具体功能 / F03 伪具体可替换 / F04 章纲密度 / F05 未落地。

设计纪律（写在这些规则之前，改规则前先读）：
- **只产依据，不产档位**。档位由 `verdict.decide` 统一映射，规则自己不许说"合格/不合格"。
- **数字全部来自口径登记表**（`caliber.values`），代码里不写死阈值。
- **每条命中都要能定位**：段号 + 原句；定位不到就不许进缺陷单。
- 零模型、零网络、纯函数：同输入必然同输出（否则 F14 回测台不可复现）。
"""
from __future__ import annotations

import re

from . import text as T

# 物理动作动词（用于"纯动作句"召回与章纲事件分类）
ACTION_VERBS = (
    "推|拉|压|抬|按|拧|翻|合|扣|盖|拿|放|塞|抽|递|接|握|捏|攥|擦|抹|扫|拖|扛|"
    "走|跑|站|坐|蹲|转身|回头|低头|抬头|起身|停下|点头|摇头|伸手|收手|敲门|关门|开门|"
    "写|划|记|抄|数|点|翻看|合上|摊开|展开|折|撕|折好|放进|取出|装上|卸下|盖上"
)
ACTION_RE = re.compile("(?:%s)" % ACTION_VERBS)

# 功能词：出现即说明这句在承担推进/揭示/情绪/关系中的至少一项
FUNCTION_WORDS = (
    "知道|明白|意识到|发现|想起|记得|忘了|决定|打算|想要|希望|害怕|担心|恨|爱|欠|"
    "答应|拒绝|怀疑|察觉|以为|误会|承认|解释|问|说|喊|叫|笑|哭|沉默|看着|盯着|"
    "因为|所以|但是|如果|除非|其实|原来|终于|第一次|再也没|从此"
)
FUNCTION_RE = re.compile("(?:%s)" % FUNCTION_WORDS)

# 状态变化词（章纲四类：处境 / 信息 / 关系 / 目标）——出现即视为"事件边界"
STATE_WORDS = (
    "得知|发现|明白|意识到|查出|查明|听见|听说|收到|看到|认出|误会|和解|决裂|翻脸|"
    "答应|拒绝|同意|反对|决定|改变主意|放弃|立誓|承诺|威胁|交换|欠下|还清|升职|被开除|"
    "处境|身份|暴露|抓住|逃走|救下|杀死|失踪|结婚|离婚"
)
STATE_RE = re.compile("(?:%s)" % STATE_WORDS)

# 自造计量单位 / 可无限生成刻度（F02/F03 的共同底座）
NUM = r"[0-9零一二三四五六七八九十百千万两半]+"
UNIT = r"次|回|遍|息|步|寸|分|刻|指|拳|口气|声"
UNIT_RE = re.compile(r"(?P<num>%s)\s*(?P<unit>%s)" % (NUM, UNIT))
INNER_TAIL = r"之内|以内|内"
REMAIN_TAIL = r"余量|余地|力气|工夫|余力"

SELF_MADE_RE = re.compile(
    r"(?P<num>%s)\s*(?P<unit>%s)\s*[^，。！？；：\n]{0,10}?(?:(?P<inner>%s)|(?P<rem>%s))"
    % (NUM, UNIT, INNER_TAIL, REMAIN_TAIL))
LEAVE_RE = re.compile(
    r"(?P<verb>留|剩|余)\s*(?P<num>%s)\s*(?P<unit>%s)\s*(?P<tail>%s)"
    % (NUM, UNIT, REMAIN_TAIL))

# 对话标签：「他说」「木野说」「她问」
TAG_RE = re.compile(r"([\u4e00-\u9fff]{0,3})(说|问|答|道|喊|叫)(?=[：:，。！？「“]|$)")


def _entry(rule_id, name, chapter, pi, sent, count, why, fix, status=None):
    e = {
        "id": "%s-%s-%s" % (rule_id, chapter, pi),
        "chapter": chapter,
        "paragraph_index": int(pi),
        "sentence": sent,
        "rule_id": rule_id,
        "rule_name": name,
        "count": int(count),
        "why": why,
        "fix_type": fix,
    }
    if status:
        e["status"] = status
    return e


# --------------------------------------------------------------------- F01
def body_metrics(body, chapter=0, cal=None):
    """F01 确定性正文体检：字数/段/句/句长/复读/对话标签 + 可定位缺陷。"""
    cal = cal or {}
    min_words = int(cal.get("J01", 5000))
    max_words = int(cal.get("J02", 9000))
    long_chars = int(cal.get("J03", 60))
    repeat_min = int(cal.get("J04", 3))
    tag_min = int(cal.get("J05", 10))

    sents = list(T.iter_sentences(body))
    lens = [len(re.sub(r"\s+", "", s)) for _pi, _si, s in sents]
    words = T.count_cn_words(body)
    paras = T.paragraph_count(body)

    seen = {}
    for pi, si, s in sents:
        seen.setdefault(s, []).append((pi, si))
    repeated = {s: v for s, v in seen.items() if len(v) >= repeat_min}

    tags = {}
    for pi, si, s in sents:
        for m in TAG_RE.finditer(s):
            tags.setdefault(m.group(2), []).append((pi, m.group(1) or ""))

    defects = []
    for pi, si, s in sents:
        if long_chars > 0 and len(s) > long_chars:
            defects.append(_entry(
                "J03", "长句上限", chapter, pi, s, 1,
                "这句 %d 字，超过口径 %d 字 —— 一句里塞了多个动作/多段信息" % (len(s), long_chars),
                "拆句"))
    for s, locs in repeated.items():
        pi = locs[0][0]
        defects.append(_entry(
            "J04", "复读句", chapter, pi, s, len(locs),
            "同一句重复 %d 次 —— 读者第二次就跳过，等于没写" % len(locs),
            "删重复/换说法"))
    for tag, locs in tags.items():
        if len(locs) >= tag_min and len(locs) > 0:
            pi = locs[0][0]
            defects.append(_entry(
                "J05", "对话标签复现", chapter, pi, "（%s）共 %d 处" % (tag, len(locs)),
                len(locs),
                "「%s」出现 %d 次 —— 标签复现是机械感的主要来源" % (tag, len(locs)),
                "换标签/删标签"))

    return {
        "words": words,
        "paragraphs": paras,
        "sentences": len(sents),
        "avg_sentence_len": round(sum(lens) / len(lens), 1) if lens else 0.0,
        "max_sentence_len": max(lens) if lens else 0,
        "dialogue_tags": {k: len(v) for k, v in tags.items()},
        "repeated_sentences": {T.snippet(k, 24): len(v) for k, v in repeated.items()},
        "below_min_words": words < min_words,
        "above_max_words": words > max_words,
        "defects": defects,
    }


# ------------------------------------------------------------------ F02/F03
def self_made_units(sentence):
    """自造计量单位 / 可无限生成刻度：返回 [(num, unit, tail)]（同一处只算一次）。"""
    seen, out = set(), []
    for m in SELF_MADE_RE.finditer(sentence or ""):
        key = (m.group("num"), m.group("unit"), m.group("inner") or m.group("rem"))
        if key not in seen:
            seen.add(key)
            out.append(key)
    for m in LEAVE_RE.finditer(sentence or ""):
        key = (m.group("num"), m.group("unit"), m.group("tail"))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


DETAIL_RE = re.compile(
    r"笔尖|笔槽|笔|纸|手指|指尖|袖|领口|鞋|靴|伞|门|墙|桌面|桌|椅|碗|杯|灯|窗|"
    r"台阶|石|烟|灰|指甲|掌|肩|背|膝|牙")


def _is_pure_action(sent):
    """纯动作句：没有对话、没有功能词，且"两个动作"或"一个动作 + 器物细节"。

    为什么留第二个条件：本次实测句「笔尖朝上，放回笔槽」只有一个动作动词，
    但它是典型的"器物特写冒充质感"——按两个动作卡会漏掉它（AC-09-1 明确要求命中）。
    """
    s = sent or ""
    if "「" in s or "『" in s or "“" in s:
        return False
    if FUNCTION_RE.search(s):
        return False
    if len(s) < 6 or len(s) > 40:
        return False
    acts = len(ACTION_RE.findall(s))
    return acts >= 2 or (acts >= 1 and bool(DETAIL_RE.search(s)))


def fake_concrete(body, chapter=0, cal=None):
    """F02 伪具体·功能标准：只**召回候选**（自造刻度 / 纯动作句），是否成立由作者裁定。"""
    cal = cal or {}
    strategy = str(cal.get("J06", "unit_or_pure_action"))
    out = []
    for pi, si, s in T.iter_sentences(body):
        units = self_made_units(s)
        if units:
            num, unit, tail = units[0]
            out.append(_entry(
                "J02", "伪具体·自造刻度", chapter, pi, s, len(units),
                "「%s%s%s」是自造计量单位：换个数字照样成立，本身不推进任何事" % (num, unit, tail),
                "换说法/删", status="待裁定"))
            continue
        if strategy == "unit_or_pure_action" and _is_pure_action(s):
            out.append(_entry(
                "J02", "伪具体·纯动作句", chapter, pi, s, 1,
                "这句只有动作，不改变处境/不揭示信息/不产生情绪/不动关系 —— 像不像「填满」由你判",
                "删/补功能", status="待裁定"))
    return out


def skeleton_of(sentence):
    """抽句式骨架：把数字换成 N，保留单位与尾巴。"""
    s = sentence or ""
    num = None
    for m in NUM_RE.finditer(s):
        num = m
        break
    if not num:
        return None
    unit = re.search(r"(?:%s)" % UNIT, s[: num.end() + 6])
    tail = None
    for cand in (INNER_TAIL, REMAIN_TAIL):
        if re.search(cand, s):
            tail = cand
            break
    if not unit or not tail:
        return None
    head = s[: num.start()]
    after = s[num.end():]
    unit_txt = re.match(r"\s*(?:%s)" % UNIT, after)
    if not unit_txt:
        return None
    rest = after[unit_txt.end():]
    return {"head": head.strip(), "unit": unit_txt.group(0).strip(),
            "tail": re.search(tail, rest).group(0),
            "pattern": (head + "N" + unit_txt.group(0) + rest).strip()}


NUM_RE = re.compile(NUM)


def skeleton_hits(body, chapter=0, cal=None):
    """F03 伪具体·可替换标准：骨架抽取 + 命中位置（换数字仍成立才算）。"""
    cal = cal or {}
    out = []
    for pi, si, s in T.iter_sentences(body):
        sk = skeleton_of(s)
        if not sk:
            continue
        # 替换测试：换一个同类数值后骨架仍要成立（否则说明只是词表）
        repl = _replace_num(s)
        if repl is None or skeleton_of(repl) is None:
            continue
        out.append(_entry(
            "J03b", "伪具体·可替换骨架", chapter, pi, s, 1,
            "骨架「%s」：任何数字都能填进去（如「%s」）—— 这类句子可无限生成"
            % (sk["pattern"].replace("N", "N"), repl),
            "换说法/删"))
    return out


def _replace_num(sentence):
    """把第一个数字换成另一个同类值（AC-10-2 的替换测试，确定性）。"""
    m = NUM_RE.search(sentence or "")
    if not m:
        return None
    table = {"一": "七", "二": "九", "三": "十", "四": "六", "五": "八",
             "十": "三", "七": "一", "九": "二", "六": "四", "八": "五",
             "两": "五", "半": "一"}
    ch = m.group(0)
    rep = "".join(table.get(c, c) for c in ch) if not ch.isdigit() else str(int(ch) + 3)
    return sentence[:m.start()] + rep + sentence[m.end():]


# --------------------------------------------------------------------- F04
def outline_events(outline_text):
    """从章节大纲解析「核心事件」列表（复用 chapter_blueprint 的格式）。"""
    try:
        from utils.chapter_blueprint import parse_chapter
        d = parse_chapter(outline_text or "")
        return [str(e).strip() for e in d.get("events") or [] if str(e).strip()]
    except Exception:                                          # noqa: BLE001
        return [str(e).strip() for e in parse_events_fallback(outline_text)
                if str(e).strip()]


def parse_events_fallback(text):
    m = re.search(r"-\s*核心事件[：:]\s*\n((?:\s*\d+[.、].+\n?)*)", text or "")
    if not m:
        return []
    return [re.sub(r"^\s*\d+[.、]\s*", "", ln).strip() for ln in m.group(1).splitlines()
            if ln.strip()]


def _bigrams(text):
    s = re.sub(r"[^\u4e00-\u9fff]", "", text or "")
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def _same_scene(a, b):
    """两条事件是否同一场景/同一班次：既没换地点、也没换场景。

    v1 判据（写在这里，改它要动口径版本）：事件里出现"换场动词"才算换场景；
    否则默认同场景（`走进/来到/回到/离开/出门/上车/下楼` 这几类）。
    """
    if SCENE_CHANGE_RE.search(a or "") or SCENE_CHANGE_RE.search(b or ""):
        return False
    return True


_STOP_BI = set("""
一个 个人 什么 这个 那个 自己 他们 我们 你们 已经 还是 就是 不是 没有 可以
""".split())

# 换场景/换班次的动作（出现即视为事件边界，不再并进上一条的连续动作组）
SCENE_CHANGE_RE = re.compile(
    r"走进|走出|来到|回到|离开|出门|进门|上车|下车|上楼|下楼|赶往|回到档案室|"
    r"第二天|次日|次日清晨|当天下午|与此同时|另一边")


def _has_state(event):
    return bool(STATE_RE.search(event or ""))


def outline_density(events, cal=None):
    """F04 章纲粒度过粗：同场景 + 时间连续 + 不改变四类状态 → 连续动作被拆成多条。

    判定是**建议**（AC-11-3）：只报"章纲密度虚高"，不据此自动判不合格。
    """
    cal = cal or {}
    need = int(cal.get("J08", 3))
    if not events:
        return {"flagged": False, "reason": "无章纲", "groups": []}
    groups, cur = [], []
    for ev in events:
        if cur and not _has_state(ev) and _same_scene(cur[-1], ev):
            cur.append(ev)
        else:
            if cur:
                groups.append(cur)
            cur = [ev]
    if cur:
        groups.append(cur)
    big = [g for g in groups if len(g) >= need]
    if not big:
        return {"flagged": False,
                "reason": "章纲 %d 条，最大同场景连续动作组 %d 条（阈值 %d）"
                          % (len(events), max([len(g) for g in groups] or [0]), need),
                "groups": groups}
    g = max(big, key=len)
    return {
        "flagged": True,
        "count": len(g),
        "events": g,
        "reason": "章纲把同一场戏的 %d 个动作算成了 %d 件事（%s）"
                  % (len(g), len(g), "→".join(T.snippet(x, 10) for x in g)),
        "groups": groups,
    }


# --------------------------------------------------------------------- F05
def _body_beats(body):
    """正文里的"事件拍"：一段里出现 ≥2 个动作，就算一拍（对话段也算）。"""
    beats = []
    for pi, para in enumerate(T.paragraphs(body), 1):
        if len(ACTION_RE.findall(para)) >= 2:
            first = T.split_sentences(para)[0] if T.split_sentences(para) else para[:30]
            beats.append({"paragraph_index": pi, "text": para, "sentence": first})
    return beats


def outline_landing(events, body, cal=None):
    """F05 正文与章纲对账：两个方向都报（正文缺项 / 章纲缺项），字段分开。"""
    cal = cal or {}
    min_rate = float(cal.get("J09", 0.5))
    if not events:
        return {"ok": False, "reason": "无章纲", "missing_in_body": [],
                "extra_in_body": [], "rate": None}
    body_bi = _bigrams(body)
    landed = []
    for ev in events:
        eb = _bigrams(ev)
        if not eb:
            continue
        rate = len(eb & body_bi) / float(len(eb))
        landed.append({"event": ev, "rate": round(rate, 3), "landed": rate >= min_rate})
    missing = [x for x in landed if not x["landed"]]
    # 章纲缺项：正文里像"事件"的段落，与任何章纲事件都对不上
    extra = []
    for beat in _body_beats(body):
        bb = _bigrams(beat["text"])
        best = 0.0
        for ev in events:
            eb = _bigrams(ev)
            if not bb or not eb:
                continue
            best = max(best, len(bb & eb) / float(min(len(bb), len(eb))))
        if best < min_rate:
            extra.append(beat)
    rate = (sum(1 for x in landed if x["landed"]) / float(len(landed))) if landed else None
    return {"ok": True, "rate": round(rate, 3) if rate is not None else None,
            "events": landed, "missing_in_body": missing, "extra_in_body": extra,
            "flagged": bool(missing or extra)}


def outline_landing_defects(res, chapter=0):
    """把 F05 对账结果翻成缺陷单条目（章级；段号能定位就带段号）。"""
    out = []
    for item in (res.get("missing_in_body") or []):
        out.append(_entry(
            "J05b", "正文未落地章纲", chapter, 0, item["event"], 1,
            "章纲写了「%s」，正文里找不到对应内容（匹配率 %.0f%%）"
            % (item["event"], item["rate"] * 100),
            "补事件/删章纲条目"))
    for beat in (res.get("extra_in_body") or []):
        out.append(_entry(
            "J05c", "章纲缺项（正文多出事件）", chapter, beat["paragraph_index"],
            T.snippet(beat["sentence"], 40), 1,
            "第 %d 段这段戏在章纲里没有对应条目 —— 章纲漏了一条事件"
            % beat["paragraph_index"],
            "补章纲/删正文"))
    return out


def analyze_chapter(body, outline_text=None, chapter=0, cal=None):
    """跑完全部规则，返回结构化结果（不含档位 —— 档位归 verdict）。"""
    cal = cal or {}
    metrics = body_metrics(body, chapter, cal)
    cands = fake_concrete(body, chapter, cal)
    skel = skeleton_hits(body, chapter, cal)
    events = outline_events(outline_text) if outline_text else []
    density = outline_density(events, cal)
    landing = outline_landing(events, body, cal) if events else {
        "ok": False, "reason": "无章纲", "missing_in_body": [], "extra_in_body": [],
        "rate": None, "flagged": False}
    landing_defects = outline_landing_defects(landing, chapter) if events else []

    defects = list(metrics["defects"]) + list(cands) + list(skel) + list(landing_defects)
    return {
        "chapter": chapter,
        "metrics": metrics,
        "fake_concrete": cands,
        "skeletons": skel,
        "outline": {"events": events, "density": density, "landing": landing},
        "defects": defects,
        "counts": {
            "F01": len(metrics["defects"]),
            "F02": len(cands),
            "F03": len(skel),
            "F04": 1 if density.get("flagged") else 0,
            "F05": len(landing_defects),
            "total": len(defects) + (1 if density.get("flagged") else 0),
        },
    }
