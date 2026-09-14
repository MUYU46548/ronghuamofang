# -*- coding: utf-8 -*-
"""校对阶段（stage 5.5：润色后、交付 Word 前）。

两级校对：
1. 确定性校对（零 token，永远开启）
   - 标点规范：引号/书名号配对、省略号统一、破折号统一、句末标点、
     中英标点混用、连续标点、汉字间空格、不可见字符，
     **明鉴规则**（全角方括号 / 半角括号含中文）
   - 常见错字：高频易混词表（本库 + **明鉴 119 条词库**，合并后 151 条）
     + 的地得启发式 + 再/在 混淆
   - 格式一致性：全角数字混用、人名/地名别名混用（依据设定集）、章节标题格式
   - 章节节奏：字数方差、对话/叙述比例、段落长度分布
2. LLM 语义校对（可选，默认关，`--llm` 开启）
   - 提示词模板 prompts/stage5_proofread.md（提示词与代码分离）
   - 输出解析走 utils.validator.parse_llm_json（搬运自明鉴，容错去围栏）

报告：
  data/outline/proofread_report.json（机器可读，结构对齐 review_report 便于 GUI 复用）
  data/outline/proofread_report.md（人可读）

用法：
  python scripts/proofread.py                      # 确定性校对（默认 refined > checked > raw）
  python scripts/proofread.py --scope refined
  python scripts/proofread.py --llm                # 追加 LLM 语义校对
  python scripts/proofread.py --dry-run            # 只写 LLM 任务文件，不调用模型
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.file_io import read_text, write_text          # noqa: E402
from utils.verify_chapter import count_cn_words          # noqa: E402

# ---- 明鉴（MingJian）规则库：搬运层，见 utils/mingjian_rules.py ----
# 明鉴的 TYPO_DICT 覆盖率约为本库的两倍，且带 PUNCT_RULES（方括号/半角括号规范）。
# 导入失败时回退到内置词库，校对功能不因缺一个可选模块而失效。
try:
    from utils.mingjian_rules import (                    # noqa: E402
        TYPO_DICT_MINGJIAN, PUNCT_RULES_MINGJIAN, TYPO_EXCLUDE_MINGJIAN,
    )
except ImportError:                                       # 回退到内置
    TYPO_DICT_MINGJIAN = None
    PUNCT_RULES_MINGJIAN = None
    TYPO_EXCLUDE_MINGJIAN = frozenset()

# ---- LLM 输出容错解析（搬运自明鉴 utils/validator.parse_llm_json）----
try:
    from utils.validator import parse_llm_json            # noqa: E402
except ImportError:                                       # 回退到内置实现
    parse_llm_json = None

DEFAULT_REPORT = "data/outline/proofread_report.json"
SETTING_PATH = Path("data/setting/setting.json")
DEFAULT_TARGET = (1200, 3500)   # project.yaml 缺省时的章节字数区间

# ---------------------------------------------------------------- 规则表

# 高频易混词：错形 → 正确形。
# 收录原则：**高精度优先，宁漏不误**。只收「错形在现代汉语中不成立」的成对词；
# 像「分辩/分辨」「衷情/钟情」这类两形皆成立的语境词一律不收，避免误报。
CONFUSABLES = [
    ("做为", "作为"), ("既使", "即使"), ("按耐", "按捺"), ("急燥", "急躁"),
    ("寒喧", "寒暄"), ("迫不急待", "迫不及待"), ("一如继往", "一如既往"),
    ("再所不惜", "在所不惜"), ("按步就班", "按部就班"), ("飞扬拔扈", "飞扬跋扈"),
    ("针贬时弊", "针砭时弊"), ("脉膊", "脉搏"), ("松驰", "松弛"),
    ("精萃", "精粹"), ("渡假", "度假"), ("冒然", "贸然"), ("暮蔼", "暮霭"),
    ("严惩不怠", "严惩不贷"), ("走头无路", "走投无路"), ("自抱自弃", "自暴自弃"),
    ("优柔寡段", "优柔寡断"), ("貌合神篱", "貌合神离"), ("在次", "再次"),
    ("在接再厉", "再接再厉"), ("不再乎", "不在乎"), ("自已", "自己"),
    ("不能自拨", "不能自拔"), ("因该", "应该"), ("倍受", "备受"),
    ("漫延", "蔓延"), ("慢延", "蔓延"), ("焕散", "涣散"), ("决对", "绝对"),
    ("关怀倍至", "关怀备至"), ("报歉", "抱歉"), ("神彩奕奕", "神采奕奕"),
    ("插科打浑", "插科打诨"), ("草管人命", "草菅人命"), ("通霄", "通宵"),
    ("夜霄", "夜宵"), ("兰球", "篮球"), ("发人深醒", "发人深省"),
    ("义气用事", "意气用事"), ("断章取意", "断章取义"), ("和霭", "和蔼"),
    ("苍海桑田", "沧海桑田"), ("穿流不息", "川流不息"), ("相形见拙", "相形见绌"),
    ("怄心沥血", "呕心沥血"), ("如火如茶", "如火如荼"), ("沧海一栗", "沧海一粟"),
]
# 去重（同一错形只留一条，避免重复报）
_CONF_SEEN = set()
CONFUSABLES = [p for p in CONFUSABLES
               if p[0] != p[1] and not (p[0] in _CONF_SEEN or _CONF_SEEN.add(p[0]))]


def merge_confusables(local, extra=None, exclude=None):
    """合并词库：本库在前（项目自调、高精度优先），明鉴补本库没有的错形。

    - 同一错形只保留**首次出现**的映射（本库优先，不会被明鉴覆盖）；
    - exclude 命中的错形整条丢弃（明鉴里有少量合法异形词，见 TYPO_EXCLUDE_MINGJIAN）。
    """
    seen, out = set(), []
    pairs = list(local) + (sorted((extra or {}).items(), key=lambda kv: kv[0]))
    for wrong, right in pairs:
        if not wrong or wrong == right or wrong in seen:
            continue
        if exclude and wrong in exclude:
            continue
        seen.add(wrong)
        out.append((wrong, right))
    return out


CONFUSABLES = merge_confusables(CONFUSABLES, TYPO_DICT_MINGJIAN, TYPO_EXCLUDE_MINGJIAN)

# 的地得启发式：动词 + 的 + 补语 → 应为「得」
_VERB = "跑|说|来|走|做|想|唱|看|听|写|读|打|数|算|记|睡|笑|哭|喊|叫|爬|飞"
_COMPL = "快|慢|好|早|晚|多|少|高|低|远|近|清楚|明白|响亮|认真|仔细|干净|彻底|漂亮"
DE_AS_DE = re.compile("(%s)的(%s)" % (_VERB, _COMPL))
# 副词 + 的 + 动词 → 应为「地」
_ADV = ("认真|仔细|慢慢|轻轻|悄悄|默默|静静|缓缓|急急|匆匆|狠狠|死死|紧紧|"
        "大大|小小|飞快|悄悄|慌忙|从容|深情|冷冷|淡淡|依依|恋恋")
_VP = ("走|说|看|想|做|笑|哭|跑|站|坐|点|摇|抓|握|推|拉|拿|放|问|答|望|"
       "听|写|读|叹气|点头|摇头|转身|抬头|低头")
DE_AS_DI = re.compile("(%s)的(%s)" % (_ADV, _VP))

# 中文语境里的半角标点（前后紧邻汉字时判定为混用）
HALF_PUNCT = re.compile(r"[\u4e00-\u9fff][,;:!?][\u4e00-\u9fff]")
FULLWIDTH_DIGITS = re.compile(r"[０-９]+")
HALFWIDTH_DIGITS = re.compile(r"[0-9]+")
CJK_SPACE = re.compile(r"[\u4e00-\u9fff][ \t]+[\u4e00-\u9fff]")
ZERO_WIDTH = re.compile(r"[\u200b-\u200f\ufeff\u00ad]")
REPEAT_PUNCT = re.compile(r"([，。；：、,;:])\1+")
ELLIPSIS_WRONG = re.compile(r"\.{3,}|。{2,}")
DASH_SINGLE = re.compile(r"(?<!—)—(?!—)")
QUOTE_PAIRS = [("“", "”", "双引号"), ("‘", "’", "单引号"),
               ("「", "」", "直角引号"), ("『", "』", "直角双引号"),
               ("《", "》", "书名号"), ("〈", "〉", "尖括号")]
SENT_END = "。！？…\"'”’」』）)】》〉.!?~"
TITLE_RE = re.compile(r"^#{1,3}\s*第\s*([0-9一二三四五六七八九十百千零两]+)\s*章(.*)$", re.MULTILINE)


# ---------------------------------------------------------------- 工具

def _num_findings(chapters):
    """统计各严重度条数。"""
    c = Counter()
    for ch in chapters:
        for f in ch.get("findings", []):
            c[f.get("severity") or "info"] += 1
    return dict(c)


def _mk(chapter, fid, ftype, severity, detail, location="", suggestion=""):
    """构造一条 finding。

    字段刻意与 chapter_review 的 review_report 对齐（id/type/severity/detail/
    suggested_action），这样 GUI 审稿界面与 batch_refine 的决策链路可直接复用，
    不必为校对单独写一套前端。
    """
    return {
        "id": "%s_%s" % (ftype, fid),
        "chapter": chapter,
        "type": ftype,
        "severity": severity,
        "detail": detail,
        "location": location,
        "suggestion": suggestion,
        "suggested_action": suggestion,
    }


def _line_of(text, pos):
    """返回 pos 所在的 1-based 行号与该行摘要。"""
    line_no = text.count("\n", 0, pos) + 1
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end < 0:
        end = len(text)
    line = text[start:end].strip()
    return line_no, (line[:70] + ("…" if len(line) > 70 else ""))


def _snippet(text, pos, span=8):
    return text[max(0, pos - span):min(len(text), pos + span + 1)].replace("\n", "⏎")


# ---------------------------------------------------------------- 确定性检查

def check_punctuation(text, chapter_no, start_idx=100):
    """标点规范检查。返回 findings 列表。"""
    out = []
    idx = start_idx

    # 1) 引号 / 书名号配对
    for left, right, label in QUOTE_PAIRS:
        n_l, n_r = text.count(left), text.count(right)
        if n_l != n_r:
            idx += 1
            out.append(_mk(chapter_no, "q%d" % idx, "punct", "error",
                           "%s不配对：%s %d 个 / %s %d 个" % (label, left, n_l, right, n_r),
                           suggestion="补齐或删除落单的%s" % label))

    # 2) 省略号统一
    for m in ELLIPSIS_WRONG.finditer(text):
        idx += 1
        line_no, line = _line_of(text, m.start())
        out.append(_mk(chapter_no, "e%d" % idx, "punct", "warn",
                       "省略号写法不统一：「%s」" % m.group(0),
                       "第%d行 %s" % (line_no, line),
                       "中文省略号统一为「……」"))
        break  # 每章最多报一次

    # 3) 破折号统一
    m = DASH_SINGLE.search(text)
    if m:
        idx += 1
        line_no, line = _line_of(text, m.start())
        out.append(_mk(chapter_no, "d%d" % idx, "punct", "warn",
                       "破折号疑似单写：「%s」" % _snippet(text, m.start(), 6),
                       "第%d行 %s" % (line_no, line),
                       "中文破折号用「——」（两个全角破折号）"))

    # 4) 中英标点混用
    hits = [m for m in HALF_PUNCT.finditer(text)]
    if hits:
        idx += 1
        m = hits[0]
        line_no, line = _line_of(text, m.start())
        out.append(_mk(chapter_no, "h%d" % idx, "punct", "warn",
                       "中文语境混用半角标点，共 %d 处（如「%s」）" % (len(hits), m.group(0)),
                       "第%d行 %s" % (line_no, line),
                       "中文句内标点改为全角，或按统一规范处理"))

    # 5) 连续标点
    m = REPEAT_PUNCT.search(text)
    if m:
        idx += 1
        line_no, line = _line_of(text, m.start())
        out.append(_mk(chapter_no, "r%d" % idx, "punct", "info",
                       "出现连续标点：「%s」" % m.group(0),
                       "第%d行 %s" % (line_no, line),
                       "确认是刻意强调还是误输入"))

    # 6) 汉字间空格
    m = CJK_SPACE.search(text)
    if m:
        idx += 1
        line_no, line = _line_of(text, m.start())
        out.append(_mk(chapter_no, "s%d" % idx, "punct", "info",
                       "汉字之间出现空格：%s" % _snippet(text, m.start(), 6),
                       "第%d行 %s" % (line_no, line),
                       "中文正文内不留半角空格"))

    # 7) 不可见字符
    m = ZERO_WIDTH.search(text)
    if m:
        idx += 1
        line_no, _line = _line_of(text, m.start())
        out.append(_mk(chapter_no, "z%d" % idx, "punct", "warn",
                       "存在零宽字符 / BOM（位置：第%d行）" % line_no,
                       suggestion="清除不可见字符，避免影响排版"))

    # 8) 段落末标点缺失
    missing = []
    for i, para in enumerate(text.split("\n\n")):
        p = para.strip()
        if len(p) < 12 or p.startswith("#") or p.startswith("<!--") or p.startswith("---"):
            continue
        if p and p[-1] not in SENT_END:
            missing.append((i + 1, p[-20:]))
    if missing:
        idx += 1
        lineno, tail = missing[0]
        out.append(_mk(chapter_no, "m%d" % idx, "punct", "info",
                       "有 %d 个段落结尾缺标点（首个：段%d 结尾「…%s」）" % (len(missing), lineno, tail),
                       suggestion="叙述段以「。！」收尾，避免句子悬空"))

    # 9) 明鉴标点规范（全角方括号 / 半角括号含中文）
    #    与 1-8 同源：确定性、零 token，仅提示不自动改。
    for name, pattern, desc, _fix in (PUNCT_RULES_MINGJIAN or []):
        hits = list(re.finditer(pattern, text))
        if not hits:
            continue
        idx += 1
        m = hits[0]
        line_no, line = _line_of(text, m.start())
        out.append(_mk(chapter_no, "mj%d" % idx, "punct",
                       "warn" if name == "全角方括号" else "info",
                       "标点规范（%s）：%s，共 %d 处（如「%s」）"
                       % (name, desc, len(hits), m.group(0)),
                       "第%d行 %s" % (line_no, line),
                       "按规范调整标点；确认非刻意用法后再改"))
    return out


def check_confusables(text, chapter_no, start_idx=200):
    """常见错字检查。"""
    out = []
    idx = start_idx
    for wrong, right in CONFUSABLES:
        if wrong == right:
            continue
        m = text.find(wrong)
        if m < 0:
            continue
        idx += 1
        line_no, line = _line_of(text, m)
        out.append(_mk(chapter_no, "c%d" % idx, "typo", "error",
                       "疑似错字：「%s」应为「%s」" % (wrong, right),
                       "第%d行 %s" % (line_no, line),
                       "改为「%s」" % right))

    for regex, right, why in ((DE_AS_DE, "得", "动词 + 得 + 补语"),
                              (DE_AS_DI, "地", "副词 + 地 + 动词")):
        m = regex.search(text)
        if m:
            idx += 1
            line_no, line = _line_of(text, m.start())
            out.append(_mk(chapter_no, "c%d" % idx, "typo", "warn",
                           "「的/地/得」疑似误用：「%s」" % m.group(0),
                           "第%d行 %s" % (line_no, line),
                           "按「%s」应为「%s」" % (why, m.group(1) + right + m.group(2))))
    return out


def check_format(text, chapter_no, setting, start_idx=300):
    """格式一致性检查：数字全半角、别名混用、标题格式。"""
    out = []
    idx = start_idx

    # 1) 全半角数字混用
    full = FULLWIDTH_DIGITS.findall(text)
    half = HALFWIDTH_DIGITS.findall(text)
    if full and half:
        idx += 1
        out.append(_mk(chapter_no, "f%d" % idx, "format", "info",
                       "全角数字（%s）与半角数字（如 %s）混用" % (full[0], half[0]),
                       suggestion="全文数字统一为半角，正文更整齐"))

    # 2) 别名混用（依据设定集的 characters[].aliases）
    for ent in (setting.get("characters") or []):
        name = (ent.get("name") or "").strip()
        aliases = [a for a in (ent.get("aliases") or []) if a and a != name]
        if not name or not aliases:
            continue
        used = [name] if text.count(name) else []
        used += [a for a in aliases if text.count(a)]
        if len(set(used)) > 1:
            idx += 1
            out.append(_mk(chapter_no, "f%d" % idx, "format", "info",
                           "同一角色多种称法混用：%s（正式名「%s」）" % ("、".join(sorted(set(used))), name),
                           suggestion="确认是否为刻意的称呼变化，否则统一为「%s」" % name))

    # 3) 章节标题格式
    titles = TITLE_RE.findall(text)
    if not titles:
        idx += 1
        out.append(_mk(chapter_no, "f%d" % idx, "format", "warn",
                       "未找到规范章节标题（应为 `## 第N章 章节名`）",
                       suggestion="补上二级标题，Word 分章与目录依赖它"))
    elif len(titles) > 1:
        idx += 1
        out.append(_mk(chapter_no, "f%d" % idx, "format", "info",
                       "一章内出现 %d 个章节标题" % len(titles),
                       suggestion="确认是否误把分节写成了章标题"))
    return out


def chapter_rhythm(text):
    """单章节奏指标（确定性，零 LLM）。"""
    paras = [p.strip() for p in text.split("\n\n")
             if p.strip() and not p.strip().startswith("#") and not p.strip().startswith("<!--")]
    quoted = re.findall(r"[“「]([^”」]*)[”」]", text)
    sentences = [s for s in re.split(r"(?<=[。！？…])", text) if s.strip()]
    para_lens = [count_cn_words(p) for p in paras] or [0]
    sent_lens = [count_cn_words(s) for s in sentences] or [0]
    words = count_cn_words(text)
    dialogue_chars = sum(count_cn_words(q) for q in quoted)
    mean_p = sum(para_lens) / len(para_lens)
    var_p = sum((x - mean_p) ** 2 for x in para_lens) / len(para_lens)
    return {
        "word_count": words,
        "paragraphs": len(paras),
        "sentences": len(sentences),
        "avg_para_len": round(mean_p, 1),
        "avg_sentence_len": round(sum(sent_lens) / len(sent_lens), 1),
        "para_len_std": round(var_p ** 0.5, 1),
        "dialogue_ratio": round(dialogue_chars / words, 4) if words else 0.0,
        "dialogue_chars": dialogue_chars,
    }


def analyze_rhythm(metrics):
    """全书节奏：字数方差 / 离群章 / 对话比例分布。返回 (dict, findings)。"""
    findings = []
    if not metrics:
        return {"chapters": {}, "outliers": []}, findings
    counts = [m["word_count"] for m in metrics.values()]
    mean = sum(counts) / len(counts)
    var = sum((x - mean) ** 2 for x in counts) / len(counts)
    std = var ** 0.5
    outliers = []
    for n in sorted(metrics):
        wc = metrics[n]["word_count"]
        if mean > 0 and abs(wc - mean) / mean > 0.40:
            outliers.append({"n": n, "word_count": wc,
                             "delta_pct": round((wc - mean) / mean * 100, 1)})
    ratios = [m["dialogue_ratio"] for m in metrics.values()]
    rhythm = {
        "chapters": metrics,
        "word_count": {"mean": round(mean, 1), "std": round(std, 1),
                       "min": min(counts), "max": max(counts),
                       "cv": round(std / mean, 3) if mean else 0.0},
        "dialogue_ratio": {"mean": round(sum(ratios) / len(ratios), 4),
                           "min": round(min(ratios), 4), "max": round(max(ratios), 4)},
        "outliers": outliers,
    }
    if outliers:
        findings.append(_mk(None, "rhythm_outlier", "rhythm", "info",
                            "章节字数离群 %d 章（均值 %d，波动 ±%.0f%%）：%s"
                            % (len(outliers), round(mean), std / mean * 100 if mean else 0,
                               "、".join("第%d章 %d字(%+.1f%%)" % (o["n"], o["word_count"], o["delta_pct"])
                                        for o in outliers[:6])),
                            suggestion="读者节奏感依赖章节体量稳定性，可考虑合并或拆分"))
    if mean and std / mean > 0.30:
        findings.append(_mk(None, "rhythm_variance", "rhythm", "warn",
                            "章节字数波动较大（CV=%.2f，>0.30）" % (std / mean),
                            suggestion="统一章节体量或按卷调节奏"))
    if metrics and max(ratios) - min(ratios) > 0.35:
        findings.append(_mk(None, "rhythm_dialogue", "rhythm", "info",
                            "各章对话占比差异大（%.0f%% ~ %.0f%%）"
                            % (min(ratios) * 100, max(ratios) * 100),
                            suggestion="确认是否刻意安排，否则均衡叙述与对话"))
    return rhythm, findings


# ---------------------------------------------------------------- LLM 语义校对

def build_llm_prompt(chapters_text, setting_text):
    """构造 LLM 校对任务（提示词主体来自 prompts/stage5_proofread.md）。"""
    try:
        from utils.template_loader import load_template
        tmpl = load_template("stage5_proofread")
    except Exception:                                   # noqa: BLE001
        tmpl = ""
    if not tmpl:
        tmpl = (
            "你是资深中文校对。请找出下列章节中的**语义级**问题：错别字、"
            "事实/设定矛盾、人物称谓混乱、语句不通、标点误用。\n"
            "只输出 JSON，不要解释：\n"
            '{"findings":[{"chapter":1,"type":"typo|fact|name|grammar|punct",'
            '"severity":"error|warn|info","detail":"问题描述","location":"原文片段",'
            '"suggestion":"修改建议"}]}\n')
    parts = [tmpl, "\n\n===== 设定集（人名/地名权威来源）=====\n", setting_text[:4000], "\n"]
    parts.append("\n===== 待校对章节 =====\n")
    parts.append(chapters_text[:24000])
    return "".join(parts)


def run_llm_proofread(chapters_text, dry_run=False, client=None):
    """可选 LLM 语义校对。返回 (ok, findings or msg)。"""
    setting_text = ""
    if SETTING_PATH.exists():
        try:
            setting_text = read_text(SETTING_PATH)
        except Exception:                               # noqa: BLE001
            setting_text = ""
    prompt = build_llm_prompt(chapters_text, setting_text)
    task_dir = Path("data/state/tasks")
    task_dir.mkdir(parents=True, exist_ok=True)
    task_path = task_dir / "stage5_proofread_task.md"
    write_text(task_path, prompt)
    print("[proofread] LLM 任务文件: " + str(task_path))
    if dry_run:
        return True, []
    if client is None:
        print("[proofread] 未提供 client，跳过 LLM 校对")
        return True, []
    result = client.run_task(task_path)
    if result.get("exit_code") != 0:
        return False, "LLM 校对失败: " + str(result.get("stdout_tail", ""))[:200]
    data = _extract_json(result.get("stdout_tail", ""))
    if data is None:
        return False, "无法从 LLM 输出解析校对 JSON"
    raw = data.get("findings") if isinstance(data, dict) else data
    out = []
    for i, f in enumerate(raw or []):
        if not isinstance(f, dict):
            continue
        sug = f.get("suggestion") or ""
        out.append({
            "id": "llm_%d" % (i + 1),
            "chapter": f.get("chapter"),
            "type": f.get("type") or "other",
            "severity": f.get("severity") or "info",
            "detail": f.get("detail") or "",
            "location": f.get("location") or "",
            "suggestion": sug,
            "suggested_action": sug,
            "source": "llm",
        })
    return True, out


def _extract_json(text):
    """从 LLM 输出里提取第一个 JSON 对象/数组（容忍 ```json 围栏）。

    实现统一走 utils.validator.parse_llm_json（搬运自明鉴，去围栏 + 首块提取），
    此处仅保留函数名，避免调用点与其单测大改。模块缺失时回退内置正则实现。
    """
    if parse_llm_json is not None:
        return parse_llm_json(text)
    return _extract_json_builtin(text)


def _extract_json_builtin(text):
    """内置兜底实现（utils.validator 不可导入时使用）。"""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    for opener, closer in (("{", "}"), ("[", "]")):
        s = text.find(opener)
        e = text.rfind(closer)
        if s >= 0 and e > s:
            try:
                return json.loads(text[s:e + 1])
            except json.JSONDecodeError:
                continue
    return None


# ---------------------------------------------------------------- 主流程

def pick_scope_dir(scope):
    """按优先级选择目录：用户指定 > refined > checked > raw。"""
    if scope:
        d = Path("data/chapters/%s" % scope)
        return d if d.exists() else None
    for name in ("refined", "checked", "raw"):
        d = Path("data/chapters/%s" % name)
        if d.exists() and list(d.glob("*.md")):
            return d
    return None


def load_setting():
    if not SETTING_PATH.exists():
        return {}
    try:
        return json.loads(read_text(SETTING_PATH)) or {}
    except Exception:                                   # noqa: BLE001
        return {}


def load_target_range():
    """从 config/project.yaml / system.yaml 读取章节字数区间。"""
    try:
        import yaml
        cfg = yaml.safe_load(read_text("config/system.yaml")) or {}
        rng = (cfg.get("chapter") or {}).get("target_words") or []
        if len(rng) == 2:
            return int(rng[0]), int(rng[1])
    except Exception:                                   # noqa: BLE001
        pass
    return DEFAULT_TARGET


def run_proofread(scope=None, report_path=DEFAULT_REPORT, use_llm=False,
                  dry_run=False, client=None, log=print):
    """主校对流程。返回 (ok, message)。"""
    scope_dir = pick_scope_dir(scope)
    if scope_dir is None:
        return False, "无可用章节目录（scope=%s，请先跑 stage4/5/6）" % scope

    files = sorted(p for p in scope_dir.glob("*.md") if p.stem.isdigit())
    if not files:
        return False, "%s 下无 NN.md 章节" % scope_dir

    setting = load_setting()
    tmin, tmax = load_target_range()
    chapters, metrics = [], {}
    total = 0
    all_text_parts = []

    for f in files:
        n = int(f.stem)
        text = read_text(f)
        all_text_parts.append("### 第%d章\n%s" % (n, text))
        findings = []
        findings += check_punctuation(text, n)
        findings += check_confusables(text, n)
        findings += check_format(text, n, setting)
        m = chapter_rhythm(text)
        metrics[n] = m
        if m["word_count"] < tmin or m["word_count"] > tmax:
            findings.append(_mk(n, "len", "rhythm", "warn",
                                "章节字数 %d 超出目标区间 %d-%d"
                                % (m["word_count"], tmin, tmax),
                                suggestion="润色阶段允许 ±20%%，超出较多需人工判断"))
        total += len(findings)
        title_m = TITLE_RE.search(text)
        chapters.append({
            "n": n,
            "title": (title_m.group(0).lstrip("# ").strip() if title_m else "?"),
            "word_count": m["word_count"],
            "metrics": m,
            "findings": findings,
        })

    rhythm, rhythm_findings = analyze_rhythm(metrics)
    if rhythm_findings:
        chapters.insert(0, {"n": None, "title": "全书节奏", "word_count": 0,
                            "metrics": {}, "findings": rhythm_findings})
        total += len(rhythm_findings)

    llm_msg = ""
    if use_llm:
        ok, llm_findings = run_llm_proofread("\n\n".join(all_text_parts), dry_run, client)
        if not ok:
            llm_msg = "；LLM 校对未完成：" + str(llm_findings)
        elif llm_findings:
            by_ch = {}
            for f_ in llm_findings:
                try:
                    cn = int(f_.get("chapter"))
                except (TypeError, ValueError):
                    cn = None
                by_ch.setdefault(cn, []).append(f_)
            for cn, fs in by_ch.items():
                target = next((c for c in chapters if c["n"] == cn), None)
                if target is None:
                    chapters.append({"n": cn, "title": "LLM 校对", "word_count": 0,
                                     "metrics": {}, "findings": fs})
                else:
                    target["findings"].extend(fs)
                total += len(fs)
            llm_msg = "；LLM 语义校对补充 %d 条" % len(llm_findings)
        else:
            llm_msg = "；LLM 语义校对未发现问题"

    by_sev = _num_findings(chapters)
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "scope": scope_dir.name,
        "target_words": [tmin, tmax],
        "use_llm": bool(use_llm),
        "summary": {
            "chapters": len(metrics),
            "issues": total,
            "error": by_sev.get("error", 0),
            "warn": by_sev.get("warn", 0),
            "info": by_sev.get("info", 0),
        },
        "rhythm": rhythm,
        "chapters": chapters,
    }

    out = Path(report_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text(out, json.dumps(report, ensure_ascii=False, indent=2))
    md_path = out.with_suffix(".md")
    write_text(md_path, format_report(report))
    log("[proofread] 报告: %s / %s" % (out, md_path))
    msg = ("校对完成：%d 章，%d 条问题（错误 %d / 警告 %d / 提示 %d）"
           % (len(metrics), total, by_sev.get("error", 0),
              by_sev.get("warn", 0), by_sev.get("info", 0))) + llm_msg
    return True, msg


def format_report(report):
    """渲染人可读 Markdown 报告。"""
    s = report["summary"]
    L = ["# 校对报告（stage 5.5）", "",
         "- 生成时间：%s" % report["generated_at"],
         "- 校对对象：data/chapters/%s/" % report["scope"],
         "- 字数目标：%d-%d" % tuple(report["target_words"]),
         "- 问题统计：**%d** 条（错误 %d / 警告 %d / 提示 %d）"
         % (s["issues"], s["error"], s["warn"], s["info"]), ""]
    r = report.get("rhythm") or {}
    wc = r.get("word_count") or {}
    if wc:
        L += ["## 章节节奏", "",
              "| 指标 | 值 |", "|------|----|",
              "| 字数均值 | %s |" % wc.get("mean"),
              "| 字数标准差 | %s（CV=%s） |" % (wc.get("std"), wc.get("cv")),
              "| 字数区间 | %s ~ %s |" % (wc.get("min"), wc.get("max")),
              "| 对话占比 | %.1f%% ~ %.1f%%（均 %.1f%%） |"
              % ((r.get("dialogue_ratio") or {}).get("min", 0) * 100,
                 (r.get("dialogue_ratio") or {}).get("max", 0) * 100,
                 (r.get("dialogue_ratio") or {}).get("mean", 0) * 100),
              ""]
    icon = {"error": "🔴", "warn": "🟡", "info": "🔵"}
    L += ["## 问题清单", ""]
    for ch in report["chapters"]:
        if not ch["findings"]:
            continue
        L.append("### %s" % (ch["title"] if ch["n"] is None else "第%d章 %s" % (ch["n"], ch["title"])))
        L.append("")
        for f in ch["findings"]:
            L.append("- %s **[%s]** %s" % (icon.get(f["severity"], "⚪"), f["type"], f["detail"]))
            if f.get("location"):
                L.append("  - 位置：%s" % f["location"])
            if f.get("suggestion"):
                L.append("  - 建议：%s" % f["suggestion"])
        L.append("")
    clean = [c for c in report["chapters"] if not c["findings"]]
    if clean:
        L.append("### 无问题章节")
        L.append("")
        L.append("、".join("第%d章" % c["n"] for c in clean if c["n"]))
        L.append("")
    L.append("> 本报告完全确定性生成（标点/错字/格式/节奏），不含 LLM 语义判断"
             + ("；已叠加 LLM 语义校对结果。" if report.get("use_llm") else "。"))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="校对（stage 5.5，润色后交付前）")
    ap.add_argument("--scope", default=None, choices=[None, "raw", "checked", "refined"],
                    help="校对目录（默认 refined > checked > raw）")
    ap.add_argument("--report", default=DEFAULT_REPORT, help="报告输出路径（.json，同名 .md 一并生成）")
    ap.add_argument("--llm", action="store_true", help="追加 LLM 语义校对（消耗 token）")
    ap.add_argument("--dry-run", action="store_true", help="只生成 LLM 任务文件，不调用模型")
    args = ap.parse_args()

    client = None
    if args.llm and not args.dry_run:
        from utils.llm_client import make_client
        import yaml
        cfg = yaml.safe_load(read_text("config/system.yaml")) or {}
        client = make_client(cfg, "checker")

    ok, msg = run_proofread(scope=args.scope, report_path=args.report,
                            use_llm=args.llm, dry_run=args.dry_run, client=client)
    print(("[OK] " if ok else "[FAIL] ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
