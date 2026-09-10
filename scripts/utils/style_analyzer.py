# -*- coding: utf-8 -*-
"""文风分析与风格指令生成（v2 深层特征版）。

从参考文本提取风格特征，生成润色/写作时的风格指令。

特征维度：
  基础：句长分布、修辞密度、对话占比、段落节奏、高频表达
  进阶：句式结构模式（关联词对）、节奏签名（句长序列波动）、连接词偏好
        （转折/因果/递进/并列）、情感基调、词汇丰富度（TTR/MATTR）
        开头/结尾模式、时间标记词、标点习惯、人称视角、语气词

设计约定：
  - 仅依赖 stdlib（re + collections），不引入第三方
  - extract_style_features(text) -> dict 签名不变，只新增键（旧键全部保留）
  - 空文本 / 过短文本（< MIN_TEXT_LEN）返回 {}，调用方按"无风格参考"处理
  - 所有比值计算走 _safe_div，杜绝除零

提示词模板与代码分离：本模块只产出"自然语言风格指令"，由 stage6_polish
等阶段注入 prompt，改动本文件即可调整文风迁移强度，无需改脚本。

  除风格指令外，本模块还提供两类可注入内容：
  - extract_style_samples()：从范文抽取代表性原文片段，作为 few-shot 示例
  - build_style_notes_section()：把用户手写的风格笔记包装成 prompt 小节
两者在"无内容"时均返回 ""，调用方保持原有逻辑不变。

生成侧还有一组"事后校验"接口（供 stage6 润色后调用）：
  - compute_style_drift()：对比范文特征与输出特征，给出逐维度偏差
  - format_drift_report()：把偏差结果渲染成 Markdown 小节
范文缺失 / 特征不足时返回空结果，调用方直接跳过，不阻塞主流程。
"""
import re
from collections import Counter

# 低于此长度认为样本不足，特征不可靠 → 返回 {}
MIN_TEXT_LEN = 100

# ---------------------------------------------------------------- 词表

# 关联词对（句式结构模式）：更具体的排在前面，匹配时按此顺序消费，避免重复计数
CORRELATIVE_PAIRS = [
    ("不是", "而是"), ("不是", "倒是"), ("与其", "不如"), ("虽然", "但是"),
    ("尽管", "还是"), ("越是", "越是"), ("不但", "而且"), ("不仅", "还"),
    ("无论", "都"), ("只有", "才"), ("只要", "就"), ("哪怕", "也"),
    ("即使", "也"), ("一边", "一边"), ("时而", "时而"), ("先是", "然后"),
    ("起初", "后来"), ("没有", "只有"), ("与其说", "不如说"),
    ("越", "越"), ("既", "又"), ("既", "也"),
]

# 连接词偏好：转折 / 因果 / 递进 / 并列
CONNECTIVES = {
    "转折": ["但是", "可是", "然而", "不过", "却", "只是", "反倒", "反而",
             "虽然", "尽管", "偏偏", "孰料", "谁知"],
    "因果": ["因为", "所以", "因此", "于是", "故而", "因而", "之所以",
             "既然", "致使", "缘何"],
    "递进": ["而且", "并且", "甚至", "更", "还", "况且", "乃至", "进而",
             "非但", "尤其"],
    "并列": ["同时", "一边", "一面", "另外", "或是", "或者", "以及", "既",
             "又", "也", "与此同时"],
}

# 时间标记词（叙事推进方式）
TEMPORAL_MARKERS = [
    "忽然", "突然", "骤然", "猛地", "陡然", "顿时", "霎时", "瞬间", "片刻",
    "须臾", "转瞬", "一瞬", "随后", "接着", "然后", "而后", "不久", "终于",
    "当时", "那时", "此刻", "此时", "现在", "曾经", "从前", "后来", "起初",
    "渐渐", "渐渐", "慢慢", "缓缓", "许久", "半晌", "良久", "一时", "正值",
    "清晨", "黄昏", "午后", "夜里", "夜半", "次日", "翌日", "三日后",
]

# 情感词表（轻量词典法，够用于"基调"判断）
POSITIVE_WORDS = [
    "欢喜", "喜悦", "欣喜", "愉快", "温暖", "温柔", "轻柔", "欢喜", "幸福",
    "希望", "光明", "灿烂", "明媚", "微笑", "笑声", "甜", "安心", "宁静",
    "舒展", "轻盈", "雀跃", "甜蜜", "喜欢", "爱", "美好", "感激", "庆幸",
]
NEGATIVE_WORDS = [
    "悲伤", "悲哀", "痛苦", "绝望", "孤独", "寂寞", "阴郁", "灰暗", "黑暗",
    "寒冷", "冰冷", "颤抖", "哭泣", "泪", "沉默", "疲惫", "疲倦", "失落",
    "后悔", "恐惧", "害怕", "愤怒", "恼怒", "怨恨", "苍凉", "荒凉", "冷清",
    "沉", "压抑", "窒息", "疼痛", "苍白", "恨", "死", "消失", "遗忘",
]

# 语气词（口语度）
MODAL_PARTICLES = ["吧", "呢", "啊", "呀", "哦", "嗯", "嘛", "啦", "罢", "呐", "噢", "唉"]

# 句尾收束字
ENDING_PARTICLES = list("了的着过吗呢吧啊呀嘛哦嗯啦罢")

METAPHOR_MARKERS = ['像', '仿佛', '如同', '好似', '宛如', '犹如', '似的', '一般']
PERSONIFICATION_MARKERS = ['低语', '沉默', '诉说', '倾听', '呼吸', '微笑', '叹息',
                           '注视', '拥抱', '颤抖', '呜咽', '沉睡']

# 人称视角
PERSON_WORDS = {
    "第一人称": ["我", "我们", "咱", "俺"],
    "第二人称": ["你", "你们"],
    "第三人称": ["他", "她", "它", "他们", "她们", "它们"],
}

_SENT_SPLIT = re.compile(r'[。！？；…]+|\n+')
_CN = re.compile(r'[\u4e00-\u9fff]')
_CN_WORD = re.compile(r'[\u4e00-\u9fff]{2,4}')
_QUOTED = re.compile(r'["“”「」『』]([^"“”「」『』]{1,200})["“”「」『』]')
# 无引号对白：说话动词（或提示动作）+ 冒号后的内容，如「头也不抬：去哪儿？」
_SPEECH_TAIL = re.compile(
    r'(?:说|道|问|答|喊|吼|叫|嘀咕|喃喃|嘟囔|低语|喘|笑|应|回|抬|望|看|指|摆|叹|皱|停)'
    r'[^：:\n]{0,8}[：:]([^\n]{1,200})')


# ---------------------------------------------------------------- 工具

def _safe_div(a, b, default=0.0):
    """安全除法：分母为 0 时返回默认值。"""
    try:
        b = float(b)
        if b == 0:
            return default
        return a / b
    except (TypeError, ValueError, ZeroDivisionError):
        return default


def _r(v, n=3):
    """四舍五入，避免 -0.0 / 浮点噪声。"""
    try:
        out = round(float(v), n)
        return 0.0 if out == 0 else out
    except (TypeError, ValueError):
        return 0.0


def _cn_only(text):
    return "".join(_CN.findall(text or ""))


def _density(count, text_len, per=1000):
    """每千字出现次数。"""
    return _r(count * per / text_len, 2) if text_len else 0.0


def _counter_top(counter, k=5, min_count=2):
    return [w for w, c in counter.most_common(40) if c >= min_count][:k]


# ---------------------------------------------------------------- 各维度分析

def _split_sentences(text):
    parts = _SENT_SPLIT.split(text)
    return [s.strip() for s in parts if s and s.strip()]


def _sentence_stats(sentences):
    lens = [len(s) for s in sentences]
    n = len(lens)
    if not n:
        return {}
    mean = sum(lens) / n
    var = sum((x - mean) ** 2 for x in lens) / n
    std = var ** 0.5
    cv = _safe_div(std, mean)
    # 交替率：相邻句在均值上下的翻转频率（高=长短交错，低=成串同调）
    if n >= 3:
        signs = [1 if x > mean else -1 for x in lens]
        alt = sum(1 for i in range(1, n) if signs[i] != signs[i - 1]) / (n - 1)
    else:
        alt = 0.0
    # 爆发：连续 >=3 句短句（<15字）的段数，用于识别"急促推进"
    bursts, run = 0, 0
    for x in lens:
        if x < 15:
            run += 1
        else:
            if run >= 3:
                bursts += 1
            run = 0
    if run >= 3:
        bursts += 1
    # 爆发指数（burstiness）：接近 1 = 忽长忽短；接近 -1 = 极度均匀
    burstiness = _safe_div(std - mean, std + mean)
    return {
        "count": n,
        "min": min(lens),
        "max": max(lens),
        "std": _r(std, 2),
        "cv": _r(cv, 3),
        "alternation": _r(alt, 3),
        "burstiness": _r(burstiness, 3),
        "short_bursts": bursts,
        "lens": lens,
    }


def _rhythm_label(stats):
    cv = stats.get("cv", 0)
    alt = stats.get("alternation", 0)
    if cv < 0.35:
        base = "节奏均匀平稳"
    elif cv < 0.7:
        base = "节奏中等起伏"
    else:
        base = "节奏强烈起伏"
    if alt >= 0.6:
        tail = "长短句高频交替（错落有致）"
    elif alt <= 0.35:
        tail = "同调句成串推进（一气呵成）"
    else:
        tail = "交替与成串混用"
    return base + "，" + tail


def _correlative_patterns(sentences):
    """关联词对（不是……而是…… / 越是……越是……）计数。"""
    counts = Counter()
    total = 0
    for sent in sentences:
        consumed = []
        for a, b in CORRELATIVE_PAIRS:  # 已按"更具体优先"排序
            pat = re.compile(re.escape(a) + r'.{0,40}?' + re.escape(b))
            for m in pat.finditer(sent):
                s, e = m.span()
                if any(not (e <= cs or s >= ce) for cs, ce in consumed):
                    continue  # 已被更具体的模式消费，跳过
                consumed.append((s, e))
                counts[a + "……" + b] += 1
                total += 1
    return [{"pattern": p, "count": c} for p, c in counts.most_common(8)], total


def _connectives(text, text_len):
    counts, detail = {}, {}
    for cat, words in CONNECTIVES.items():
        c = sum(text.count(w) for w in words)
        counts[cat] = c
        detail[cat] = _r(_density(c, text_len), 2)
    total = sum(counts.values()) or 1
    dominant = max(counts.items(), key=lambda kv: kv[1])[0] if counts else ""
    return {
        "counts": counts,
        "density_per_1k": detail,
        "dominant": dominant if counts.get(dominant) else "",
        "ratio": {k: _r(_safe_div(v, total), 3) for k, v in counts.items()},
    }


def _sentiment(text, text_len):
    pos = sum(text.count(w) for w in POSITIVE_WORDS)
    neg = sum(text.count(w) for w in NEGATIVE_WORDS)
    both = pos + neg
    polarity = _safe_div(pos - neg, both)
    if both == 0:
        tone = "中性"
    elif polarity > 0.3:
        tone = "积极明快"
    elif polarity < -0.3:
        tone = "沉郁低回"
    elif polarity > 0.1:
        tone = "略偏积极"
    elif polarity < -0.1:
        tone = "略偏消极"
    else:
        tone = "复杂克制"
    return {
        "positive": pos,
        "negative": neg,
        "neutral_hint": max(0, text_len // 500 - pos - neg),
        "polarity": _r(polarity, 3),
        "tone": tone,
        "density_per_1k": {
            "positive": _r(_density(pos, text_len), 2),
            "negative": _r(_density(neg, text_len), 2),
        },
    }


def _vocabulary(text):
    """词汇丰富度：字级 TTR + 二字组 MATTR + 一次性词比例。"""
    cn = _cn_only(text)
    if len(cn) < 10:
        return {}
    char_types = len(set(cn))
    ttr = _r(_safe_div(char_types, len(cn)), 4)

    bigrams = [cn[i:i + 2] for i in range(len(cn) - 1)]
    bg_total = len(bigrams)
    bg_types = len(set(bigrams))
    if bg_total < 20:
        return {"ttr": ttr, "bigram_ttr": _r(_safe_div(bg_types, bg_total), 4)}
    # MATTR：滑动窗口平均 TTR，抵消长度偏差
    win, step = 100, 50
    vals = []
    for i in range(0, max(1, bg_total - win + 1), step):
        chunk = bigrams[i:i + win]
        if len(chunk) >= 20:
            vals.append(len(set(chunk)) / len(chunk))
    mattr = _r(sum(vals) / len(vals), 4) if vals else _r(_safe_div(bg_types, bg_total), 4)
    bg_counts = Counter(bigrams)
    hapax = sum(1 for c in bg_counts.values() if c == 1)
    return {
        "ttr": ttr,
        "char_types": char_types,
        "bigram_ttr": _r(_safe_div(bg_types, bg_total), 4),
        "mattr": mattr,
        "hapax_ratio": _r(_safe_div(hapax, bg_types), 4),
        "bigram_types": bg_types,
    }


def _opening_ending(sentences, text):
    """开头/结尾模式。"""
    if not sentences:
        return {"opening": {}, "ending": {}}
    all_conn = [w for ws in CONNECTIVES.values() for w in ws]
    openers = Counter()
    o_conn = o_temp = o_pron = o_quote = 0
    for s in sentences:
        head = s[:2]
        openers[head] += 1
        if any(s.startswith(w) for w in all_conn):
            o_conn += 1
        if any(s.startswith(w) for w in TEMPORAL_MARKERS):
            o_temp += 1
        if any(s.startswith(w) for ws in PERSON_WORDS.values() for w in ws):
            o_pron += 1
        if s[:1] in ('"', '“', '「', '『'):
            o_quote += 1
    n = len(sentences)
    opening = {
        "connective_start_ratio": _r(_safe_div(o_conn, n), 3),
        "temporal_start_ratio": _r(_safe_div(o_temp, n), 3),
        "pronoun_start_ratio": _r(_safe_div(o_pron, n), 3),
        "quote_start_ratio": _r(_safe_div(o_quote, n), 3),
        "top_openers": _counter_top(openers, 5, 2),
    }

    e_particle = sum(1 for s in sentences if s and s[-1] in ENDING_PARTICLES)
    q = text.count("？") + text.count("?")
    ex = text.count("！") + text.count("!")
    ell = text.count("……")
    tails = Counter(s[-2:] for s in sentences if len(s) >= 2)
    ending = {
        "particle_end_ratio": _r(_safe_div(e_particle, n), 3),
        "question_ratio": _r(_safe_div(q, n), 3),
        "exclaim_ratio": _r(_safe_div(ex, n), 3),
        "ellipsis_count": ell,
        "top_endings": _counter_top(tails, 5, 2),
    }
    return {"opening": opening, "ending": ending}


def _temporal(text, text_len):
    counter = Counter({w: text.count(w) for w in TEMPORAL_MARKERS if text.count(w)})
    total = sum(counter.values())
    return {
        "count": total,
        "density_per_1k": _r(_density(total, text_len), 2),
        "top": [w for w, _ in counter.most_common(6)],
    }


def _punctuation(text, sentences, text_len):
    n = len(sentences) or 1
    return {
        "comma_per_sentence": _r(_safe_div(text.count("，") + text.count(","), n), 2),
        "semicolon_density": _r(_density(text.count("；"), text_len), 2),
        "dash_density": _r(_density(text.count("——") + text.count("—"), text_len), 2),
        "ellipsis_density": _r(_density(text.count("……"), text_len), 2),
        "pause_mark_density": _r(_density(text.count("、"), text_len), 2),
        "quote_density": _r(_density(text.count('"') + text.count('“'), text_len), 2),
    }


def _person(text):
    counts = {k: sum(text.count(w) for w in ws) for k, ws in PERSON_WORDS.items()}
    total = sum(counts.values())
    dominant = max(counts.items(), key=lambda kv: kv[1])[0] if total else ""
    return {
        "counts": counts,
        "ratio": {k: _r(_safe_div(v, total), 3) for k, v in counts.items()},
        "dominant": dominant if counts.get(dominant) else "",
    }


def _modality(text, text_len):
    counter = Counter({p: text.count(p) for p in MODAL_PARTICLES if text.count(p)})
    total = sum(counter.values())
    return {
        "count": total,
        "density_per_1k": _r(_density(total, text_len), 2),
        "top": [w for w, _ in counter.most_common(5)],
    }


def _speech(text, quoted_chars, text_len):
    """对白占比：引号内 + 说话动词引导的无引号对白。

    dialogue_ratio（旧键）只统计引号内字符；本函数补充"吼道：……"这类
    无引号对白，使口语型文风的对话占比不被低估。
    """
    tails = _SPEECH_TAIL.findall(text)
    spoken_chars = quoted_chars + sum(len(t.strip()) for t in tails)
    return {
        "quoted_ratio": _r(_safe_div(quoted_chars, text_len), 3),
        "spoken_ratio": _r(_safe_div(spoken_chars, text_len), 3),
        "unquoted_lines": len(tails),
    }


# ---------------------------------------------------------------- 主入口

def extract_style_features(text):
    """从参考文本提取风格特征字典。

    返回 {} 表示样本不足（空文本或少于 MIN_TEXT_LEN 字），调用方按
    "无风格参考"处理——保持旧版契约不变。
    """
    if not text or not isinstance(text, str):
        return {}
    text = text.strip()
    if len(text) < MIN_TEXT_LEN:
        return {}

    text_len = len(text)

    # 1. 句长分布
    sentences = _split_sentences(text)
    sent_lengths = [len(s) for s in sentences]
    n_sent = len(sentences)
    avg_len = _safe_div(sum(sent_lengths), n_sent)
    short_ratio = _safe_div(sum(1 for l in sent_lengths if l < 15), n_sent)
    long_ratio = _safe_div(sum(1 for l in sent_lengths if l > 40), n_sent)

    # 2. 高频表达（2-4 字词组，出现 3 次以上）
    words = _CN_WORD.findall(text)
    word_counts = Counter(words)
    common_phrases = [w for w, c in word_counts.most_common(20) if c >= 3]

    # 3. 修辞特征
    metaphor_count = sum(text.count(m) for m in METAPHOR_MARKERS)
    personification_count = sum(text.count(m) for m in PERSONIFICATION_MARKERS)

    # 4. 对话模式
    dialogue = _QUOTED.findall(text)
    dialogue_chars = sum(len(d) for d in dialogue)
    dialogue_ratio = _safe_div(dialogue_chars, text_len)

    # 5. 段落节奏
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    avg_para_len = _safe_div(sum(len(p) for p in paragraphs), len(paragraphs))

    # 6. 深层特征
    stats = _sentence_stats(sentences)
    corr, corr_total = _correlative_patterns(sentences)
    oe = _opening_ending(sentences, text)

    return {
        # ---- 旧键（向后兼容，勿改语义）----
        "avg_sentence_len": _r(avg_len, 1),
        "short_sentence_ratio": _r(short_ratio, 2),
        "long_sentence_ratio": _r(long_ratio, 2),
        "common_phrases": common_phrases[:10],
        "metaphor_density": _r(_safe_div(metaphor_count, n_sent)),
        "personification_density": _r(_safe_div(personification_count, n_sent)),
        "dialogue_ratio": _r(dialogue_ratio, 2),
        "avg_paragraph_len": _r(avg_para_len, 1),
        # ---- 新增：样本信息 ----
        "text_length": text_len,
        "sentence_count": n_sent,
        "paragraph_count": len(paragraphs),
        # ---- 新增：节奏签名 ----
        "sentence_len_std": stats.get("std", 0.0),
        "sentence_len_cv": stats.get("cv", 0.0),
        "sentence_len_min": stats.get("min", 0),
        "sentence_len_max": stats.get("max", 0),
        "rhythm_alternation": stats.get("alternation", 0.0),
        "rhythm_burstiness": stats.get("burstiness", 0.0),
        "short_burst_count": stats.get("short_bursts", 0),
        "rhythm_signature": _rhythm_label(stats) if stats else "",
        # ---- 新增：句式结构模式 ----
        "correlative_patterns": corr,
        "correlative_density": _r(_safe_div(corr_total, n_sent)),
        # ---- 新增：连接词偏好 ----
        "connectives": _connectives(text, text_len),
        # ---- 新增：情感基调 ----
        "sentiment": _sentiment(text, text_len),
        # ---- 新增：词汇丰富度 ----
        "vocabulary": _vocabulary(text),
        # ---- 新增：开头/结尾模式 ----
        "opening_patterns": oe.get("opening", {}),
        "ending_patterns": oe.get("ending", {}),
        # ---- 新增：时间标记词 ----
        "temporal_markers": _temporal(text, text_len),
        # ---- 新增：标点习惯 ----
        "punctuation": _punctuation(text, sentences, text_len),
        # ---- 新增：人称视角 ----
        "person": _person(text),
        # ---- 新增：语气词 ----
        "modality": _modality(text, text_len),
        # ---- 新增：对白占比（含无引号对白）----
        "speech": _speech(text, dialogue_chars, text_len),
    }


# ---------------------------------------------------------------- 风格偏差对比

# 默认偏差阈值：相对偏差超过 30% 才判定为"漂移"
DEFAULT_DRIFT_THRESHOLD = 0.3


def _fmt_ratio(v):
    """0.35 -> 35%"""
    return f"{float(v) * 100:.0f}%"


def _fmt_num2(v):
    return f"{float(v):.2f}"


def _fmt_num3(v):
    return f"{float(v):.3f}"


def _fmt_sentence_len(v):
    return f"平均{float(v):.0f}字/句"


def _fmt_paragraph_len(v):
    return f"平均{float(v):.0f}字/段"


def _fmt_density(v):
    return f"{float(v):.1f}/千字"


def _fmt_comma(v):
    return f"{float(v):.1f}个/句"


# 参与对比的维度：(特征路径, 中文名, 数值显示格式)
# 嵌套结构（如 connectives.counts）只取到数值叶子节点，不递归整棵子树
DRIFT_DIMENSIONS = (
    ("avg_sentence_len", "句式长度", _fmt_sentence_len),
    ("short_sentence_ratio", "短句占比", _fmt_ratio),
    ("long_sentence_ratio", "长句占比", _fmt_ratio),
    ("dialogue_ratio", "对话占比", _fmt_ratio),
    ("metaphor_density", "比喻密度", _fmt_num2),
    ("personification_density", "拟人密度", _fmt_num2),
    ("avg_paragraph_len", "段落长度", _fmt_paragraph_len),
    ("sentence_len_cv", "节奏起伏", _fmt_num2),
    ("vocabulary.mattr", "词汇丰富度", _fmt_num3),
    ("modality.density_per_1k", "语气词密度", _fmt_density),
    ("punctuation.comma_per_sentence", "逗号密度", _fmt_comma),
)


def _get_numeric(features, path):
    """按 'a.b.c' 路径取值，只接受数值（bool 不算）；缺失返回 None。"""
    cur = features
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        return None
    return float(cur)


def _signed_pct(v):
    """44.44 -> +44%；-2.5 -> -2.5%"""
    v = float(v)
    return f"{v:+.0f}%" if abs(v) >= 10 else f"{v:+.1f}%"


def compute_style_drift(features_ref, features_output, threshold=0.3):
    """对比范文特征与输出特征，返回偏差字典。

    参数：
      features_ref     extract_style_features(范文) 的结果
      features_output  extract_style_features(待检文本) 的结果
      threshold        相对偏差阈值（默认 0.3 = 30%），超过才计入 drifts

    偏差算法：数值型/比例型特征统一用相对偏差 (output - ref) / |ref|，
    drift_pct 为百分比数值并保留 2 位小数（44.44 表示 +44.44%）。
    ref 为 0 或任一侧缺失的维度直接跳过，不参与统计。

    Returns:
      {"drifts": [{"dimension": str, "ref": float, "output": float,
                   "drift_pct": float, "ref_text": str, "output_text": str}],
       "matches": [...同结构...], "summary": str, "drift_count": int,
       "compared": int, "threshold": float}
      无偏差时 drifts=[]，summary 说明"风格匹配良好"；
      特征不足时额外置 skipped=True，summary 说明跳过原因。
    """
    result = {"drifts": [], "matches": [], "summary": "",
              "drift_count": 0, "compared": 0, "threshold": 0.3}
    if not features_ref or not features_output:
        result["summary"] = "特征不足，未进行风格偏差分析"
        result["skipped"] = True
        return result
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = DEFAULT_DRIFT_THRESHOLD
    result["threshold"] = _r(threshold, 3)

    drifts, matches = [], []
    for path, label, fmt in DRIFT_DIMENSIONS:
        ref = _get_numeric(features_ref, path)
        out = _get_numeric(features_output, path)
        if ref is None or out is None or ref == 0:
            continue
        rel = (out - ref) / abs(ref)
        item = {
            "dimension": label,
            "ref": _r(ref, 4),
            "output": _r(out, 4),
            "drift_pct": _r(rel * 100, 2),
            "ref_text": fmt(ref),
            "output_text": fmt(out),
        }
        (drifts if abs(rel) > threshold else matches).append(item)

    drifts.sort(key=lambda d: -abs(d["drift_pct"]))
    result["drifts"] = drifts
    result["matches"] = matches
    result["drift_count"] = len(drifts)
    result["compared"] = len(drifts) + len(matches)
    if drifts:
        result["summary"] = f"{len(drifts)} 项漂移，整体风格偏离范文。"
    elif result["compared"]:
        result["summary"] = (f"风格匹配良好（比较 {result['compared']} 项，"
                             f"均无超过 {threshold * 100:.0f}% 的偏差）")
    else:
        result["summary"] = "可对比维度不足，未判定风格偏差"
        result["skipped"] = True
    return result


def format_drift_report(result, title="", max_matches=3):
    """把 compute_style_drift() 的结果渲染成 Markdown 小节。

    title 形如"第3章"，渲染为 "## 风格偏差报告（第3章）"。
    跳过/无内容时返回 ""，调用方直接忽略。
    """
    if not result or result.get("skipped"):
        return ""
    head = f"## 风格偏差报告（{title}）" if title else "## 风格偏差报告"
    lines = [head]
    drifts = result.get("drifts") or []
    if not drifts:
        lines.append(f"- ✓ {result.get('summary', '风格匹配良好')}")
        return "\n".join(lines) + "\n"
    for d in drifts:
        lines.append(f"- ⚠️ {d['dimension']}偏差 {_signed_pct(d['drift_pct'])}"
                     f"（范文{d['ref_text']}，本章{d['output_text']}）")
    for m in (result.get("matches") or [])[:max_matches]:
        lines.append(f"- ✓ {m['dimension']}匹配"
                     f"（范文{m['ref_text']}，本章{m['output_text']}）")
    lines.append(f"总结：{result.get('summary', '')}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 指令生成

def build_style_instruction(features):
    """根据特征生成风格指令文本，注入 prompt。"""
    if not features:
        return ""

    def g(key, default=None):
        v = features.get(key, default)
        return default if v is None else v

    L = []
    L.append("\n## 风格模仿要求")
    L.append("请严格模仿以下文风特征进行创作/润色（下列数据来自对参考范文的量化分析）：\n")

    # ============ 一、句式与节奏 ============
    L.append("### 一、句式与节奏")
    avg = g("avg_sentence_len", 0)
    if avg > 0:
        mn, mx = g("sentence_len_min", 0), g("sentence_len_max", 0)
        span = f"（最短 {mn} 字 / 最长 {mx} 字）" if mx else ""
        L.append(f"- **句长**：平均 {avg} 字/句{span}；"
                 f"短句（<15字）占 {g('short_sentence_ratio', 0)*100:.0f}%，"
                 f"长句（>40字）占 {g('long_sentence_ratio', 0)*100:.0f}%")
        if g("short_sentence_ratio", 0) > 0.4:
            L.append("  - 短句占比高，请保留简洁明快、一刀见骨的节奏")
        if g("long_sentence_ratio", 0) > 0.3:
            L.append("  - 长句占比高，请保留铺陈渲染、气息绵长的风格")

    sig = g("rhythm_signature", "")
    cv = g("sentence_len_cv", 0)
    if sig and cv:
        L.append(f"- **节奏签名**：{sig}（句长变异系数 {cv}，"
                 f"交替率 {g('rhythm_alternation', 0)}）")
        if cv >= 0.7:
            L.append("  - 请刻意制造长短对比：长句铺陈后紧跟短句收束，形成呼吸感")
        elif cv < 0.35:
            L.append("  - 请保持句长稳定，避免忽长忽短的突兀变化")
        bursts = g("short_burst_count", 0)
        if bursts:
            L.append(f"  - 范文中有 {bursts} 处“连续 3 句以上短句”的急促推进，请在高潮处复现")

    corr = g("correlative_patterns", [])
    if corr:
        shown = "、".join(f"{c['pattern']}（{c['count']} 次）" for c in corr[:4])
        L.append(f"- **句式结构**：偏好关联词对——{shown}")
        L.append("  - 请复现这类“前后呼应”的句式（如“不是……而是……”“越是……越是……”）")

    # ============ 二、连接与推进 ============
    L.append("\n### 二、连接与推进")
    conn = g("connectives") or {}
    counts = conn.get("counts") or {}
    if counts and sum(counts.values()):
        detail = " / ".join(f"{k} {v}" for k, v in counts.items())
        dom = conn.get("dominant", "")
        L.append(f"- **连接词偏好**：{detail}")
        if dom:
            L.append(f"  - 以「{dom}」关系为主，句间推进请沿用这一逻辑重心")
        ratio = conn.get("ratio") or {}
        if ratio.get("转折", 0) >= 0.4:
            L.append("  - 转折密集：叙事多靠“反转”推进，请保持情节的逆向张力")
        if ratio.get("因果", 0) >= 0.4:
            L.append("  - 因果密集：叙事讲究前因后果，请保持逻辑链条的清晰")
        if ratio.get("递进", 0) >= 0.3:
            L.append("  - 递进密集：情绪层层加码，请保持“再进一层”的堆叠感")
        if ratio.get("并列", 0) >= 0.4:
            L.append("  - 并列密集：多用平行铺排，请保持对仗/罗列的节奏")

    tm = g("temporal_markers") or {}
    if tm.get("count"):
        top = "、".join(tm.get("top", [])[:4])
        L.append(f"- **时间推进**：时间标记词密度 {tm.get('density_per_1k', 0)}/千字"
                 + (f"，常用：{top}" if top else ""))

    # ============ 三、情感与语气 ============
    L.append("\n### 三、情感与语气")
    sent = g("sentiment") or {}
    if sent:
        L.append(f"- **情感基调**：{sent.get('tone', '中性')}"
                 f"（积极 {sent.get('positive', 0)} / 消极 {sent.get('negative', 0)}，"
                 f"极性 {sent.get('polarity', 0)}）")
        tone = sent.get("tone", "")
        if tone in ("沉郁低回", "略偏消极"):
            L.append("  - 请用词克制、留白多于直说，避免突然明亮的转折")
        elif tone in ("积极明快", "略偏积极"):
            L.append("  - 请保持明亮温润的用色，避免无谓的阴郁铺陈")
        else:
            L.append("  - 请保持情绪的中立克制，让事实自己说话")

    mod = g("modality") or {}
    if mod.get("count"):
        top = "、".join(mod.get("top", [])[:4])
        d = mod.get("density_per_1k", 0)
        level = "口语感强" if d >= 4 else ("口语感中等" if d >= 1.5 else "书面感强")
        L.append(f"- **语气词**：{top}（密度 {d}/千字，{level}）")

    # ============ 四、用词与修辞 ============
    L.append("\n### 四、用词与修辞")
    vocab = g("vocabulary") or {}
    if vocab:
        mattr = vocab.get("mattr", 0)
        level = "用词丰富" if mattr >= 0.75 else ("用词中等" if mattr >= 0.6 else "用词集中、偏口语化")
        L.append(f"- **词汇丰富度**：字级 TTR {vocab.get('ttr', 0)}，"
                 f"二字组 MATTR {mattr}（{level}）")
        if vocab.get("hapax_ratio"):
            L.append(f"  - 一次性搭配占比 {vocab['hapax_ratio']}，"
                     f"请避免高频重复同一组词")

    phrases = g("common_phrases", [])
    if phrases:
        L.append(f"- **标志性表达**：{'、'.join(phrases[:8])}")

    meta = g("metaphor_density", 0)
    pers = g("personification_density", 0)
    if meta > 0.1:
        L.append(f"- **修辞**：高频使用比喻（密度 {meta}），请保持具象化的意象表达")
    if pers > 0.05:
        L.append(f"- **修辞**：高频使用拟人（密度 {pers}），请保持万物有灵的视角")

    # ============ 五、视角、对话与句法外观 ============
    L.append("\n### 五、视角、对话与句法外观")
    person = g("person") or {}
    if person.get("dominant"):
        L.append(f"- **叙述视角**：{person['dominant']}"
                 f"（{' / '.join(f'{k} {v}' for k, v in (person.get('ratio') or {}).items())}）")

    dialogue = g("dialogue_ratio", 0)
    speech = g("speech") or {}
    spoken = speech.get("spoken_ratio", dialogue)
    if spoken > 0.3:
        extra = ""
        if speech.get("unquoted_lines"):
            extra = f"（其中 {speech['unquoted_lines']} 处为无引号对白，多用“某某说：……”式引导）"
        L.append(f"- **对话**：对白占比 {spoken*100:.0f}%{extra}，请以对话推动叙事")
    elif spoken < 0.1:
        L.append(f"- **对话**：对白占比低（{spoken*100:.0f}%），请保持内敛克制的叙事")
    elif speech.get("quoted_ratio", 0) != spoken:
        L.append(f"- **对话**：引号对白 {speech.get('quoted_ratio', 0)*100:.0f}%，"
                 f"含引导式对白共 {spoken*100:.0f}%，请保持这一配比")

    op = g("opening_patterns") or {}
    if op:
        bits = []
        if op.get("pronoun_start_ratio", 0) >= 0.25:
            bits.append(f"人称开头 {op['pronoun_start_ratio']*100:.0f}%")
        if op.get("connective_start_ratio", 0) >= 0.1:
            bits.append(f"连接词开头 {op['connective_start_ratio']*100:.0f}%")
        if op.get("temporal_start_ratio", 0) >= 0.08:
            bits.append(f"时间/场景状语开头 {op['temporal_start_ratio']*100:.0f}%")
        tops = op.get("top_openers") or []
        L.append("- **句首模式**：" + ("；".join(bits) if bits else "无明显倾向")
                 + (f"；高频句首词：{'、'.join(tops[:4])}" if tops else ""))

    ed = g("ending_patterns") or {}
    if ed:
        bits = [f"以“了/的/着”等助词收束 {ed.get('particle_end_ratio', 0)*100:.0f}%"]
        if ed.get("question_ratio", 0) >= 0.03:
            bits.append(f"问句 {ed['question_ratio']*100:.0f}%")
        if ed.get("exclaim_ratio", 0) >= 0.03:
            bits.append(f"感叹句 {ed['exclaim_ratio']*100:.0f}%")
        if ed.get("ellipsis_count", 0):
            bits.append(f"省略号 {ed['ellipsis_count']} 处（留白式收尾）")
        tops = ed.get("top_endings") or []
        L.append("- **句尾模式**：" + "；".join(bits)
                 + (f"；高频收尾：{'、'.join(tops[:4])}" if tops else ""))

    punc = g("punctuation") or {}
    if punc:
        bits = [f"逗号 {punc.get('comma_per_sentence', 0)} 个/句"]
        for label, key in (("破折号", "dash_density"), ("省略号", "ellipsis_density"),
                           ("顿号", "pause_mark_density"), ("分号", "semicolon_density")):
            v = punc.get(key, 0)
            if v >= 0.5:
                bits.append(f"{label} {v}/千字")
        L.append("- **标点习惯**：" + "；".join(bits))

    # ============ 六、段落 ============
    para_len = g("avg_paragraph_len", 0)
    if para_len > 0:
        L.append(f"\n### 六、段落\n- **段落**：平均段长 {para_len} 字"
                 f"（共 {g('paragraph_count', 0)} 段）")
        if para_len < 100:
            L.append("  - 段落短小精悍，节奏明快")
        elif para_len > 200:
            L.append("  - 段落铺陈舒展，节奏沉稳")

    L.append("\n请在保持情节和人物关系不变的前提下，严格遵循以上风格特征。")
    return "\n".join(L)


def load_style_reference(path):
    """从文件加载参考文本并返回风格指令。"""
    from utils.file_io import read_text
    try:
        text = read_text(path)
    except Exception:
        return ""
    features = extract_style_features(text)
    return build_style_instruction(features)


# ---------------------------------------------------------------- 范文片段（few-shot）

# 片段选取参数
SAMPLE_MIN_LEN = 40           # 短于此字数的段落不作为候选
SAMPLE_MAX_LEN = 260          # 片段截取上限（超长段按句边界截断）
SAMPLE_TOTAL_BUDGET = 900     # 注入总字数上限，防任务文件膨胀
SAMPLE_SIM_THRESHOLD = 0.30   # 与当前正文的字面重叠上限（超过则丢弃该候选）
SAMPLE_DEDUP_THRESHOLD = 0.50 # 片段之间的字面重叠上限（避免挑到雷同段落）
DEFAULT_SAMPLE_COUNT = 3      # 默认片段数

_CLIP_PUNCS = ("。", "！", "？", "…", "；")


_PARA_BREAK = re.compile(r"\n[ \t]*\n+")   # 空行分段（首选）
_LINE_BREAK = re.compile(r"\n+")           # 无空行时退化为按行分段


def _split_paragraphs(text):
    """切分段落并保留原文字符偏移，返回 [(start, end, text), ...]。

    优先按空行分段——对白块常由若干单行组成（"甲说：……\\n乙说：……"），
    若按单换行切会把它们拆成过短的碎块，反而丢掉最能体现语感的部分。
    全文没有空行时才退化为按行切分。
    """
    pattern = _PARA_BREAK if _PARA_BREAK.search(text) else _LINE_BREAK
    out = []
    pos = 0
    for m in pattern.finditer(text):
        seg = text[pos:m.start()]
        if seg.strip():
            out.append((pos, m.start(), seg.strip()))
        pos = m.end()
    tail = text[pos:]
    if tail.strip():
        out.append((pos, len(text), tail.strip()))
    return out


def _ngrams(text, n=3):
    """汉字 n-gram 集合（去标点/空白），用于字面重叠比较。"""
    cn = _cn_only(text)
    if len(cn) < n:
        return set()
    return {cn[i:i + n] for i in range(len(cn) - n + 1)}


def _overlap(a_grams, b_grams):
    """a 相对 b 的重叠率 = |a∩b| / |a|。

    纯字面比较，不做语义匹配。取值 0~1，越大说明 a 的内容越多地出现在 b 中。
    """
    if not a_grams or not b_grams:
        return 0.0
    return _safe_div(len(a_grams & b_grams), len(a_grams))


def _clip(text, limit):
    """截断到 limit 字以内，尽量落在句末标点处。"""
    if len(text) <= limit:
        return text, len(text)
    cut = -1
    for p in _CLIP_PUNCS:
        cut = max(cut, text.rfind(p, 0, limit))
    if cut >= limit * 0.5:
        return text[:cut + 1].strip(), cut + 1
    return text[:limit].rstrip() + "……", limit


def _sample_score(para):
    """给候选段落打"代表性"分：长度适中、含对白/修辞/句式变化、中文占比高。"""
    n = len(para)
    if n < SAMPLE_MIN_LEN:
        return 0.0
    score = 1.0
    if 80 <= n <= 200:                       # 长度最讨喜
        score += 1.0
    elif n <= SAMPLE_MAX_LEN:
        score += 0.4
    else:
        score += 0.1
    if _QUOTED.search(para) or _SPEECH_TAIL.search(para):
        score += 0.8                          # 对白最能体现语感
    if any(m in para for m in METAPHOR_MARKERS):
        score += 0.4
    if any(a in para and b in para for a, b in CORRELATIVE_PAIRS[:8]):
        score += 0.4
    sents = _split_sentences(para)
    if len(sents) >= 3:
        st = _sentence_stats(sents)
        score += min(st.get("cv", 0), 1.0) * 0.6   # 节奏有起伏更好
    if len(_cn_only(para)) < n * 0.5:
        score -= 1.5                          # 目录行/符号行/非中文为主
    if "第" in para and "章" in para and n < 30:
        score -= 1.0                          # 章节标题行
    return score


def select_style_samples(ref_text, current_text="", max_samples=DEFAULT_SAMPLE_COUNT):
    """从范文正文中挑选代表性片段。

    参数：
      ref_text     范文全文
      current_text 当前章节/大纲正文（用于避开高度相似的段落）
      max_samples  最多返回几个片段

    返回 [(location_label, snippet_text), ...]；无合格片段时返回 []。
    片段按原文出现顺序排列（不按分数），便于保持叙事先后。
    """
    if not ref_text or not isinstance(ref_text, str):
        return []
    try:
        max_samples = max(1, int(max_samples))
    except (TypeError, ValueError):
        max_samples = DEFAULT_SAMPLE_COUNT

    cur_grams = _ngrams(current_text or "")
    picked = []
    picked_grams = []
    used_chars = 0

    scored = []
    for idx, (start, end, para) in enumerate(_split_paragraphs(ref_text), 1):
        score = _sample_score(para)
        if score <= 0:
            continue
        scored.append((score, idx, start, para))
    scored.sort(key=lambda x: -x[0])

    for score, idx, start, para in scored:
        body, consumed = _clip(para, SAMPLE_MAX_LEN)
        if len(body) < SAMPLE_MIN_LEN:
            continue
        grams = _ngrams(body)
        # 1) 与当前正文高度相似 → 跳过（避免把"待写内容"本身当范文）
        if _overlap(grams, cur_grams) > SAMPLE_SIM_THRESHOLD:
            continue
        # 2) 与已选片段雷同 → 跳过
        if any(_overlap(grams, g) > SAMPLE_DEDUP_THRESHOLD for g in picked_grams):
            continue
        # 3) 总字数预算
        if used_chars + len(body) > SAMPLE_TOTAL_BUDGET:
            continue
        picked.append((idx, start, consumed, body))
        picked_grams.append(grams)
        used_chars += len(body)
        if len(picked) >= max_samples:
            break

    # 按原文出现顺序输出（便于保持叙事先后）
    picked.sort(key=lambda x: x[0])
    return [(f"第 {idx} 段，字符 {start}-{start + consumed}", body)
            for idx, start, consumed, body in picked]


def format_style_samples(picks, source=""):
    """把片段列表渲染成可注入 prompt 的 Markdown 小节。"""
    if not picks:
        return ""
    L = ["\n## 范文片段（few-shot 示例）",
         "下面是从参考范文中抽取的代表性原文，代表其典型节奏、用词与句式。"
         "请对照其语感进行创作/润色，并**严格遵守**："]
    L.append("- 只学语感（节奏、句式、用词分寸），**严禁照搬情节、人物、意象或原句**；")
    L.append("- 片段中若出现与本章设定冲突的内容，一律以大纲与设定集为准。\n")
    for i, (label, body) in enumerate(picks, 1):
        src = f"来源：{source}" if source else "来源：参考范文"
        L.append(f"### 片段 {i}　{src}（{label}）")
        L.append("```text")
        L.append(body)
        L.append("```\n")
    return "\n".join(L).rstrip() + "\n"


def extract_style_samples(path, current_text="", max_samples=DEFAULT_SAMPLE_COUNT, source=None):
    """从范文文件提取代表性片段，返回可直接注入 prompt 的小节文本。

    与 load_style_reference() 配套：前者给"量化风格指令"，本函数给"原文语感示例"。
    范文为空/不可读/无合格片段时返回 ""，调用方保持现有逻辑不变。
    """
    if not path:
        return ""
    from utils.file_io import read_text
    try:
        text = read_text(path)
    except Exception:
        return ""
    if not text or not text.strip():
        return ""
    picks = select_style_samples(text, current_text=current_text, max_samples=max_samples)
    return format_style_samples(picks, source=source or str(path))


# ---------------------------------------------------------------- 用户风格笔记

def build_style_notes_section(notes):
    """把用户手写的风格笔记包装成 prompt 小节；空笔记返回 ""。"""
    if not notes or not str(notes).strip():
        return ""
    body = str(notes).strip()
    L = ["\n## 用户风格笔记（最高优先级）",
         "以下为作者手动补充的风格要求，与上述自动分析结论**叠加生效**。",
         "若与自动分析、模板默认文风或范文片段冲突，一律以本节为准：\n",
         body, ""]
    return "\n".join(L)
