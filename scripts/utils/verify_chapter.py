# -*- coding: utf-8 -*-
"""章节校验：标题格式 / 字数 / 段落空行 / 占位符 / 复杂元素 / 质量自评分
/ **退化检测（非空 + 非退化硬断言）**。

对应架构文档 v2 4.1 阶段4校验点与 4.7 自评分解析。

## 退化为什要单独一层判据（2026-09-20 补，审计行动项 10）

字数检查只能抓「产出太空」。**当模型产出足够长却全是垃圾时，原判据全部漏过** ——
实测（把退化样本字数调进 `[2000, 3000]` 区间后跑旧 `check_chapter`）：

| 退化形态 | 触发场景 | 旧判据 |
|---|---|---|
| 复读填充 | 模型卡住，同一句重复填满字数 | **漏** |
| 思考残片 | `reasoning_content` 兜底提取出的思考文本被当正文 | **漏** |
| 元话语/拒答 | 模型说「作为AI我无法…」 | **漏** |
| 无段落换行 | 整章一坨，无段落结构 | **漏** |
| 标点灌水 | 只有标题 + 纯标点 | **漏** |
| 模板骨架 | 「【场景描写】【角色A】」未填充 | **漏** |

其中「思考残片」与本项目已知的思考模式兼容问题直接相关：
`llm_client` 的第三层防线是「全部失败才从 `reasoning_content` 兜底提取」——
**那层兜底本身就是在把思考当正文**。此处判据为它补上最后一道网。
"""
import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from utils.file_io import read_text

TITLE_RE = re.compile(r"^##\s*第\s*(\d+)\s*章.*$", re.MULTILINE)
QUALITY_RE = re.compile(r"<!--\s*quality:\s*(\d{1,2})\s*/\s*10\s*-->", re.IGNORECASE)
# 复杂元素（v2 4.6 降级策略要求生成阶段禁止出现）
COMPLEX_PATTERNS = [
    (re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE), "Markdown 表格"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), "Markdown 图片"),
    (re.compile(r"^```"), "代码块"),
]
PLACEHOLDERS = ("XXX", "TODO", "待补充", "此处插入", "{{", "}}")

# ---------------------------------------------------------------- 退化判据

# 元话语/拒答：模型没在写小说，而是在跟用户说话或拒绝。
# 只在**正文占比很高**时才判否（避免正文里角色说「作为AI」被误杀）。
META_PATTERNS = [
    r"作为\s*(一个)?\s*(AI|人工智能|语言模型|助手|智能助手)",
    r"我(无法|不能|不便)(完成|满足|提供|继续)",
    r"很抱歉[，,]?\s*我",
    r"^\s*(好的|收到|明白)[，,]?\s*(我(将|会|来)|让我)",
    r"如果您(需要|希望|想要)",
    r"(本章|这段)(内容)?(需要|将由|建议)(您|作者)(自行|来)",
    r"以下是(我|为)(您|你)(生成|写)的",
]

# 思考残片：模型的自述式规划语言。
# 这些词在**正常小说正文**里极少高频出现，但在 reasoning 里是标志词。
REASONING_PATTERNS = [
    r"让我(想想|思考|分析|梳理|确认)",
    r"我(应该|需要|可以|要)(先|再|然后|考虑|注意)",
    r"首先(我)?(需要|要|来)",
    r"接下来(我)?(需要|要|考虑|写)",
    r"这一步(需要|要)",
    r"这一章(需要|应该|大概)",
    r"^(嗯|好的)[，,、]",
    r"用户(要求|希望|想要)",
    r"按照(要求|大纲|设定)[，,]?(我)",
    r"考虑(到)?(一下)?(节奏|篇幅|字数)",
]

# 模板骨架：未填充的结构性占位
SKELETON_PATTERNS = [
    r"【[^】]{0,12}】",              # 【场景描写】
    r"\{[^}]{0,12}\}",              # {角色A}
    r"\[[^\]]{0,12}\]",             # [对话内容]
    r"<[^>]{0,12}>",                # <情节>
]

# ---- 段落结构判据（定标依据见下）----
#
# ⚠️ 定标过程（2026-09-20，用 19 个真实章节文件实测）：
# 第一版用「平均段长 ≥30 字」，结果 **7/19 命中，其中 5 个是误报** ——
# 被判退化的样本均长 26.3 / 27.1 / 27.8 / 29.1，全是健康的对话密集章节：
#
#     4 | "坐。"
#     6 | 暮雨没碰茶。
#     7 | "去了多久？"
#
# 中文小说**单句成段**是标准写法，不是退化。均长 19.2~47.6 全部合法。
#
# 第二版试过「段长变异系数 CV ≥0.25」，实测**真实健康章节 CV = 0.64~1.31
# （中位 0.80）** —— 阈值离真实分布太远，等于永不触发的空转判据。
# 复读场景由 n-gram 判据精确覆盖（真实章节重复率仅 0.002~0.004，
# 复读样本 60%+，区分度极高），CV 属冗余且对样本长度敏感，故**不采用**。
#
# 保留两条结构判据，它们直接刻画「没有段落结构」这个本质：
MIN_PARAGRAPHS = 6            # 一章至少这么多个非空、非标题段落
MAX_SINGLE_PARA_RATIO = 0.5   # 单段不得超过正文 50%（防「一坨」；实测健康值 ≤0.10）

# 复读：n-gram 重复率上限
NGRAM = 12
MAX_NGRAM_REPEAT_RATIO = 0.35  # 重复的 12-gram 占所有 12-gram 的比例上限
MIN_NGRAM_SAMPLES = 60         # 样本太少（短章）不做复读判定，避免误报

# 比率型判据（元话语/思考残片/骨架/标点）统一的最小正文体量门槛。
# 低于此值时不判——短文本上「1 段里有 1 段命中」就是 100%，毫无统计意义。
MIN_RATIO_BODY_CHARS = 600


def _body_lines(text):
    """去掉标题行、质量注释、空行 → 正文段落列表。"""
    body = QUALITY_RE.sub("", text)
    out = []
    for ln in body.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or set(s) <= set("-—=*_ "):
            continue
        out.append(s)
    return out


def _ratio_of_matches(text, patterns):
    """返回「命中行数 / 正文段落数」。按行判，避免长文本里偶发一次就触发。"""
    body = _body_lines(text)
    if not body:
        return 0.0, []
    hits = []
    for ln in body:
        for pat in patterns:
            if re.search(pat, ln):
                hits.append(ln[:60])
                break
    return (len(hits) / len(body)), hits


def check_degenerate(text):
    """退化检测：返回 (问题列表, 指标 dict)。问题列表为空 = 健康。

    判据取「**宽松触发**」而非「严苛触发」——原因是这两类误判的代价不对称：
    把好章判成退化 → 触发重写，浪费 token，但用户能看到并纠正；
    把退化章放行 → 空白/垃圾进入下游润色与交付 Word（**静默失败**）。
    所以宁可多拦，但每条判据都必须有明确的、可解释的形态特征。
    """
    problems = []
    metrics = {}
    body = _body_lines(text)
    metrics["paragraphs"] = len(body)
    if not body:
        return ["正文为空（无任何非标题段落）"], metrics

    total = sum(len(x) for x in body)
    metrics["body_chars"] = total

    # ---- 1 段落结构 ----
    if len(body) < MIN_PARAGRAPHS:
        problems.append(f"段落过少（{len(body)} 段，正常章节应 ≥{MIN_PARAGRAPHS} 段）")
    longest = max(len(x) for x in body)
    metrics["longest_para"] = longest
    if total and longest / total > MAX_SINGLE_PARA_RATIO:
        problems.append(f"单一超长段落占正文 {longest / total:.0%}"
                        f"（须 ≤{MAX_SINGLE_PARA_RATIO:.0%}，疑似丢换行）")
    if len(body):
        metrics["avg_para_len"] = round(total / len(body), 1)

    # 比率型判据统一门槛：正文太短时比率无统计意义（1/1 就是 100%）
    if total < MIN_RATIO_BODY_CHARS:
        metrics["ratio_skipped"] = True
        return problems, metrics

    # ---- 2 元话语 / 拒答 ----
    r_meta, meta_hits = _ratio_of_matches(text, META_PATTERNS)
    metrics["meta_ratio"] = round(r_meta, 3)
    if r_meta >= 0.25:
        problems.append(f"元话语/拒答占比 {r_meta:.0%}（模型在对话而非写小说）：{meta_hits[:2]}")

    # ---- 3 思考残片 ----
    r_think, think_hits = _ratio_of_matches(text, REASONING_PATTERNS)
    metrics["reasoning_ratio"] = round(r_think, 3)
    if r_think >= 0.25:
        problems.append(f"思考残片占比 {r_think:.0%}（reasoning 被当正文）：{think_hits[:2]}")

    # ---- 4 模板骨架 ----
    r_skel, skel_hits = _ratio_of_matches(text, SKELETON_PATTERNS)
    metrics["skeleton_ratio"] = round(r_skel, 3)
    if r_skel >= 0.20:
        problems.append(f"模板骨架未填充占比 {r_skel:.0%}：{skel_hits[:2]}")

    # ---- 5 复读填充（n-gram 重复率）----
    flat = re.sub(r"\s+", "", "".join(body))
    if len(flat) >= NGRAM * MIN_NGRAM_SAMPLES:
        grams = [flat[i:i + NGRAM] for i in range(len(flat) - NGRAM + 1)]
        uniq = len(set(grams))
        dup_ratio = 1 - uniq / len(grams)
        metrics["ngram_repeat"] = round(dup_ratio, 3)
        if dup_ratio > MAX_NGRAM_REPEAT_RATIO:
            problems.append(f"复读：{NGRAM}-gram 重复率 {dup_ratio:.0%}"
                            f"（上限 {MAX_NGRAM_REPEAT_RATIO:.0%}，模型在重复填充）")

    # ---- 6 标点/字符灌水 ----
    printable = re.sub(r"\s+", "", flat)
    if printable:
        # 去标点后剩多少
        content = re.sub(r"[。，、！？；：""''《》（）—…·,.!?;:\"'()\[\]<>~\-*#]+", "", printable)
        content_ratio = len(content) / len(printable)
        metrics["content_ratio"] = round(content_ratio, 3)
        if content_ratio < 0.5:
            problems.append(f"有效字符占比仅 {content_ratio:.0%}（疑似标点灌水）")

    return problems, metrics


@dataclass
class ChapterCheck:
    path: str
    ok: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    word_count: int = 0
    chapter_no: int = None
    title: str = None
    quality: int = None
    degenerate: list = field(default_factory=list)
    degen_metrics: dict = field(default_factory=dict)

    def summary(self):
        extra = (f" 退化={len(self.degenerate)}" if self.degenerate else "")
        return (f"[{'PASS' if self.ok else 'FAIL'}] 第{self.chapter_no or '?'}章 "
                f"{self.title or ''} 字数={self.word_count} quality={self.quality or '-'}"
                + extra
                + (f" 错误: {len(self.errors)}" if self.errors else ""))


def count_cn_words(text):
    """粗略字数统计：去除空白与 Markdown 符号后的字符数（含中英文）。"""
    stripped = re.sub(r"[#*`>_|\[\]()!\-]", "", text)
    stripped = re.sub(r"\s+", "", stripped)
    return len(stripped)


def is_chapter_complete(path, target_min=1200, target_max=3500):
    """检查章节是否真正完成（非空、无截断、字数达标、无占位符、标题格式正确、**非退化**）。

    与 check_chapter() 互补：check_chapter 返回详细报告，
    is_chapter_complete 只做布尔判定，供断点续跑筛选使用。

    ⚠️ 退化判据的**严重项**也计入：否则断点续跑会把一坨复读文本
    当成「已完成」跳过，于是永远不重写（静默失败）。
    """
    p = Path(path)
    if not p.exists():
        return False
    try:
        text = read_text(path).strip()
    except Exception:
        return False
    if not text:
        return False
    # 检查截断标记
    if text.endswith("...") or text.endswith("……"):
        return False
    # 检查字数
    words = count_cn_words(text)
    if words < target_min or words > target_max:
        return False
    # 检查占位符
    for ph in PLACEHOLDERS:
        if ph in text:
            return False
    # 检查标题格式
    if not TITLE_RE.search(text):
        return False
    # 退化检测：严重项直接判未完成（避免断点续跑跳过垃圾章）
    degen, _ = check_degenerate(text)
    if degen:
        return False
    return True


def check_chapter(path, min_words=2000, max_words=3000):
    """校验单章文件，返回 ChapterCheck。"""
    text = read_text(path)
    result = ChapterCheck(path=str(path))

    title_m = TITLE_RE.search(text)
    if not title_m:
        result.errors.append("缺少标题：需为 `## 第X章 章节名`")
    else:
        result.chapter_no = int(title_m.group(1))
        result.title = title_m.group(0).lstrip("# ")

    result.word_count = count_cn_words(text)
    if not (min_words <= result.word_count <= max_words):
        result.errors.append(f"字数 {result.word_count} 不在 [{min_words}, {max_words}] 区间")

    # 段落空行检查：正文连续两段之间需空行
    body = QUALITY_RE.sub("", text)
    lines = [ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    for i in range(1, len(lines)):
        if lines[i].strip() and lines[i - 1].strip():
            result.warnings.append(f"第 {i + 1} 行与上一段之间缺空行")
            break  # 只报一次

    for pat, name in COMPLEX_PATTERNS:
        if pat.search(text):
            result.errors.append(f"含复杂元素：{name}（应降级为文本描述，见 v2 4.6）")

    for ph in PLACEHOLDERS:
        if ph in text:
            result.errors.append(f"含占位符：{ph}")

    qm = QUALITY_RE.search(text)
    if qm:
        result.quality = int(qm.group(1))
    else:
        result.warnings.append("缺少质量自评分注释 <!-- quality: X/10 -->")

    # 退化检测：字数达标但内容为垃圾时，上面的判据全部漏过（审计行动项 10）
    degen, degen_metrics = check_degenerate(text)
    result.degenerate = list(degen)
    result.degen_metrics = degen_metrics
    for p in degen:
        result.errors.append(f"退化：{p}")

    result.ok = not result.errors
    return result


def main():
    parser = argparse.ArgumentParser(description="NovelForge 章节校验")
    parser.add_argument("target", help="单章文件或章节目录")
    parser.add_argument("--min", type=int, default=2000)
    parser.add_argument("--max", type=int, default=3000)
    args = parser.parse_args()

    target = Path(args.target)
    files = [target] if target.is_file() else sorted(target.glob("*.md"))
    results = [check_chapter(f, args.min, args.max) for f in files]
    for r in results:
        print(r.summary())
    print(f"\n合计: {sum(r.ok for r in results)}/{len(results)} 通过")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
