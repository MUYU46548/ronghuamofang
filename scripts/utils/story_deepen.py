# -*- coding: utf-8 -*-
"""叙事深化（2026-10-20，v0.7.2）—— 卷内的节奏形状。

## 为什么需要它

旧流程是「卷 → 章」两层：卷纲给 20 章逐章目标，然后一章一章平推。
一卷 20 章不是一条直线 —— 中间要有 2-4 个**深化段**，每段有自己的目的
与节奏形状（蓄力、爆发、喘息、再蓄力）。没有这一层，"滚动规划"只管了
**写什么**，没管**怎么铺开**：模型会把高潮章均匀撒在卷里，读起来平。

## 职责边界（只做三件事）

1. **解析**：卷纲「## 叙事深化」节 → 段列表（起止章 + 类型 + 目标）。
   切段本身是**创作决策**，由规划模型在卷纲的同一次调用里完成（不加调用）。
2. **判据**：确定性校验（覆盖无缺无重 / 每段 3-8 章 / 类型在枚举内 /
   相邻不同型）。不合格就报，不修不猜。
3. **注入**：把「本章属于哪一段、该段什么形状」渲染成 stage3/stage4 的
   注入块。**密度指导写成数据**（SEGMENT_TYPES），不是散在提示词里。

## 存量宽容（与章大纲六字段同纪律）

老卷纲没有「## 叙事深化」节：不判废、不重生成、不注入（注入块为空）。
只有**新生成**的卷纲走严格校验（`volume_plan.ensure_volume` 的生成路径）。
用户要补切段，对旧卷 `--force` 重规划即可。

## 文体闸门

`writing_form` 的 `deepen` 键在这里**被真正消费**：essay / episodic 的
spec 里 deepen=False → 本模块全程早退（散文集没有"卷内节奏"这回事）。
（2026-10-20 自查教训：配了键没有消费方 = 假功能，比没配更糟。）
"""
import re

# 五种深化类型 —— **写成数据不是写成提示词**。hint 是给写作模型的密度指导，
# 注入时按本段类型取对应一行；改密度口径只改这里，不动提示词模板。
SEGMENT_TYPES = {
    "成长突破": {
        "desc": "蓄力到爆发：能力/认知上一个台阶",
        "hint": "铺垫 2-3 章（受挫、积累），突破点 1-2 处落在具体事件上；不许靠独白顿悟",
    },
    "对抗竞技": {
        "desc": "有明确对手的拉锯：胜负交替",
        "hint": "冲突点 2-3 处/章；胜负交替不许一边倒；每次失利要留下后果",
    },
    "探索发现": {
        "desc": "线索驱动：未知逐层揭开",
        "hint": "新信息 1-2 条/章；每章至少揭开一层又露出更深一层；不许一次说尽",
    },
    "恩怨冲突": {
        "desc": "关系破裂与对峙：情绪升级",
        "hint": "对峙 1-2 处/章；情绪逐级升级但不重复发火；每次对峙都改变关系格局",
    },
    "日常过渡": {
        "desc": "喘息与蓄力：生活细节推进人物",
        "hint": "允许 0 冲突；用生活细节蓄力，伏笔至多 1 处；不得连续两段同型",
    },
}

# 段的章数边界。MIN 起 3：一段 1-2 章不构成"段"，等于没切。
MIN_SEG_CHAPTERS = 3
MAX_SEG_CHAPTERS = 8
MIN_SEGMENTS = 2
MAX_SEGMENTS = 4
# 少于这么多章的卷切不出 2 段（2×3=6）——允许 1 段或不切，不硬凑。
SMALL_VOLUME_CHAPTERS = 6
# 目标字段下限（有效字符），与章大纲六字段的 MIN_FIELD_CHARS 同口径。
MIN_GOAL_CHARS = 6
PLACEHOLDERS = ("待补充", "TODO", "XXX", "（略）", "(略)", "待定")

# 行格式：- 第 X-Y 章 ｜ 类型 ｜ 目标。分隔符宽容（｜ | : ：），破折号宽容（- – — ～ 至）。
# ⚠️ 冒号必须全半角都认 —— 模型写 `第 4-6 章：类型：目标` 是高频变体，
# 只认「｜」会把整段判成解析失败（2026-10-20 实测 P2 抓到）。
_SEG_RE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?第[ \t]*(\d+)[ \t]*[-–—～~至][ \t]*(\d+)[ \t]*章"
    r"[ \t]*[｜|：:][ \t]*(\S+?)[ \t]*[｜|：:][ \t]*(.+?)\s*$"
)
_SECTION_RE = re.compile(r"^##\s*叙事深化\s*$", re.M)


def applicable(proj):
    """本文体是否做叙事深化（消费 writing_form 的 deepen 键）。读不到按 True（默认叙事）。"""
    try:
        from utils import writing_form as wf
        return bool(wf.applicability(proj).get("deepen", True))
    except Exception:                                       # noqa: BLE001
        return True


def parse_deepen(text):
    """从卷纲文本解析深化段。返回 (segments, present)。

    `present=False` 表示压根没有「## 叙事深化」节（存量卷纲的正常态，不是错误）。
    解析失败的行跳过 —— 校验阶段会因覆盖不全报出来，不在这儿吞掉。
    """
    t = text or ""
    m = re.search(r"^##\s*叙事深化\s*$(.*?)(?=^##\s|\Z)", t, re.M | re.S)
    if not m:
        return [], False
    segs = []
    for ln in m.group(1).splitlines():
        sm = _SEG_RE.match(ln)
        if not sm:
            continue
        segs.append({
            "start": int(sm.group(1)),
            "end": int(sm.group(2)),
            "type": sm.group(3).strip(),
            "goal": sm.group(4).strip(),
        })
    segs.sort(key=lambda s: s["start"])
    return segs, True


def check_deepen(segments, start, end):
    """确定性判据。返回 (ok, problems)。

    - 段数：卷章数 >= 6 时 2-4 段；不足 6 章允许 1 段（2×3 切不开，不硬凑）
    - 覆盖：正好盖满 [start, end]，不缺章、不重叠（各段章数之和 == 卷章数）
    - 每段 3-8 章（1 段的小卷放宽到 1-8）
    - 类型必须在 SEGMENT_TYPES 枚举内
    - 相邻两段不得同型（"连续 40 章日常过渡"是真实会出现的退化）
    - 目标非空（>= 6 有效字符）、无占位符
    """
    problems = []
    if not segments:
        return False, ["深化段为空"]
    total = int(end) - int(start) + 1
    n_min = 1 if total < SMALL_VOLUME_CHAPTERS else MIN_SEGMENTS
    if not (n_min <= len(segments) <= MAX_SEGMENTS):
        problems.append("深化段数量 %d 不在 %d-%d 段之间"
                        % (len(segments), n_min, MAX_SEGMENTS))

    # 覆盖：排序后首尾相接
    if segments[0]["start"] != int(start):
        problems.append("第一段从第 %d 章开始，应从第 %d 章" % (segments[0]["start"], start))
    if segments[-1]["end"] != int(end):
        problems.append("最后一段到第 %d 章结束，应到第 %d 章" % (segments[-1]["end"], end))
    covered = 0
    for i, s in enumerate(segments):
        if s["end"] < s["start"]:
            problems.append("第 %d 段起止倒挂（%d-%d 章）" % (i + 1, s["start"], s["end"]))
            continue
        covered += s["end"] - s["start"] + 1
        if i > 0 and s["start"] != segments[i - 1]["end"] + 1:
            problems.append("第 %d 段（第 %d 章起）与上一段不相接（上一段止于第 %d 章）"
                            % (i + 1, s["start"], segments[i - 1]["end"]))
    if covered != total:
        problems.append("各段章数之和 %d != 卷章数 %d（缺章或重叠）" % (covered, total))

    lo = 1 if total < SMALL_VOLUME_CHAPTERS else MIN_SEG_CHAPTERS
    for i, s in enumerate(segments):
        span = s["end"] - s["start"] + 1 if s["end"] >= s["start"] else 0
        if span and not (lo <= span <= MAX_SEG_CHAPTERS):
            problems.append("第 %d 段跨 %d 章，应在 %d-%d 章之间"
                            % (i + 1, span, lo, MAX_SEG_CHAPTERS))
        if s["type"] not in SEGMENT_TYPES:
            problems.append("第 %d 段类型「%s」不在枚举内（可选：%s）"
                            % (i + 1, s["type"], "、".join(SEGMENT_TYPES)))
        goal = (s.get("goal") or "").strip()
        if len(goal) < MIN_GOAL_CHARS:
            problems.append("第 %d 段目标太短或为空（少于 %d 有效字符）" % (i + 1, MIN_GOAL_CHARS))
        for w in PLACEHOLDERS:
            if w in goal:
                problems.append("第 %d 段目标含占位符「%s」" % (i + 1, w))
                break
        if i > 0 and s["type"] == segments[i - 1]["type"]:
            problems.append("第 %d 段与第 %d 段同型（%s）—— 相邻两段不得同型"
                            % (i, i + 1, s["type"]))
    return (not problems), problems


def segment_of(segments, n):
    """第 n 章属于哪一段。不在任何段内返回 None。"""
    for s in segments or []:
        if s["start"] <= n <= s["end"]:
            return s
    return None


def render_full(segments, n=None):
    """渲染完整段表（stage3 用：整批章大纲生成时要看到全卷形状）。

    n 给出时把该章所在段标出来。没有段（存量卷纲）返回空串 —— 调用方
    据此决定不注入，绝不注入"暂无数据"占位。
    """
    if not segments:
        return ""
    cur = segment_of(segments, n) if n is not None else None
    lines = ["**叙事深化（本卷切分）**：一卷不是直线平推，各段形状如下 ——"]
    for i, s in enumerate(segments):
        mark = " ← **本章所在段**" if (cur is s) else ""
        lines.append("- 第 %d-%d 章 ｜ %s ｜ %s%s"
                     % (s["start"], s["end"], s["type"], s["goal"], mark))
        lines.append("  - 密度指导：%s" % SEGMENT_TYPES.get(s["type"], {}).get("hint", ""))
    lines.append("（本章的情节密度与节奏以所在段为准：段与段之间是形状切换，"
                 "不许把下一段的爆发提前到本段。）")
    return "\n".join(lines)


def render_for_chapter(segments, n):
    """渲染单章注入块（stage4 写作用：只给本章所在段，不给全卷形状 —— 少花钱）。"""
    s = segment_of(segments, n)
    if not s:
        return ""
    idx = segments.index(s) + 1
    return ("## 叙事深化（本章节奏形状）\n"
            "本章属于第 %d 段（第 %d-%d 章，共 %d 段），类型：**%s** —— %s。\n"
            "本段目标：%s\n"
            "密度指导：%s\n"
            "（段与段之间是形状切换：不许把别段的爆发提前，也不许在爆发段里平推。）"
            % (idx, s["start"], s["end"], len(segments), s["type"],
               SEGMENT_TYPES.get(s["type"], {}).get("desc", ""), s["goal"],
               SEGMENT_TYPES.get(s["type"], {}).get("hint", "")))


def load_segments(volume_path):
    """读卷纲文件 → (segments, present)。文件不存在返回 ([], False)。"""
    from pathlib import Path
    from utils.file_io import read_text
    p = Path(volume_path)
    if not p.exists():
        return [], False
    try:
        return parse_deepen(read_text(p))
    except OSError:
        return [], False


def context_for(n, total, cfg=None, proj=None, root="."):
    """stage3/stage4 的注入入口：本章的深化上下文。返回 (full_block, chapter_block)。

    - 文体不做深化（essay/episodic）→ ("", "")
    - 卷纲不存在或没有深化节（存量）→ ("", "") —— 明确的空白，不注入占位
    - 其余 → (整批用全表, 单章用本段块)
    """
    if not applicable(proj):
        return "", ""
    from utils import volume_arc as va
    size = va.volume_size(cfg, proj)
    v = va.volume_of(n, size)
    segs, present = load_segments(va.volume_path(v, root))
    if not present or not segs:
        return "", ""
    return render_full(segs, n), render_for_chapter(segs, n)
