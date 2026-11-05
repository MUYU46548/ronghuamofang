# -*- coding: utf-8 -*-
"""阶段 5 结构指标：可自动算的“文学质量可疑信号”（零 token，纯确定性）。

## 为什么要有它

七维评审（`utils/review_ledger`）只验三件事：引句能否逐字命中原文、同章不重复、
有效字符 ≥10。**它不判断好坏** —— 这是设计（机器判不了“写得好不好”）。

但实测差距太刺眼：七维 8.14 分、4 批 `complete=True`，同一份文本的 1269 句对白里
只有 33 句问号，几乎全是单回合问答。读者一眼看出的问题，账本一个字都没说。

本模块补这一半：只算**确定性结构指标**，超线就标“需人工抽样”。
**不给文学分、不合成总分** —— 一个 8.14 混着流程分与文学分，作者就无法判断
“这书到底行不行、该看哪里”。机器只负责把可疑的章指出来，判断永远是作者的。

## 六项指标（定义都是确定性的，可单测）

| code | 名字 | 怎么算 | 方向 |
|---|---|---|---|
| `question_ratio` | 问号占对白 | 以问号结尾的对白句 / 对白句总数 | 低于线 → 对白全是陈述 |
| `single_turn_streak` | 连续单回合问答 | 连续“问→答（答不是问）”的最长组数 | 高于线 → 一问一答死板 |
| `speaker_spread` | 角色间平均句长差 | 各说话人平均句长的 max-min（每人 ≥3 句才参与，不足两人 = N/A） | 低于线 → 角色说话一个腔调 |
| `aphorism_density` | 格言式单句密度 | 独立成段、无引号、6~25 字、以句末标点收尾且无逗号顿号的段 / 非对白段 | 高于线 → 说教味 |
| `dialogue_cover` | 对话段占比 | 含对白的段落 / 总段落 | 低于线 → 通篇叙述 |
| `avg_para_len` | 平均段长 | 非空段落平均字数 | 高于线 → 大段密集难读 |

## 阈值在哪

**只在 `data/judge/caliber.json`（J16–J21）**，代码里一个数字都不写。缺判据 =
缺口径，如实返回 `missing_criteria`，不回落默认值（项目纪律，见 `utils/judge/caliber.py`）。

## 边界

- 不读模型、不写正文，只读 `data/chapters/raw/NN.md`。
- 触发**不阻断**流程：阶段 5 照常完成，只是报告里多一段“这几章请人工抽样”。
- 文体不适用：全文没有对白时，与对白相关的四项标 N/A（散文/书信体不会被判“对白少”）。
"""
import json
import re
from pathlib import Path

from utils.judge import caliber as cal

# 六项指标：code → 名字 / 单位 / 方向 / 对应判据 id。顺序即报告与界面顺序。
DIMENSIONS = [
    {"code": "question_ratio", "label": "问号占对白", "unit": "比例",
     "direction": "min", "criterion": "J16",
     "na_when_no_dialogue": True,
     "hint": "对白全是陈述句 —— 人物不追问、不试探，读起来像念台词"},
    {"code": "single_turn_streak", "label": "连续单回合问答", "unit": "组",
     "direction": "max", "criterion": "J17",
     "na_when_no_dialogue": True,
     "hint": "一问一答连续太多 —— 对话没有插话、沉默、答非所问"},
    {"code": "speaker_spread", "label": "角色间平均句长差", "unit": "字",
     "direction": "min", "criterion": "J18",
     "na_when_no_dialogue": True,
     "hint": "不同角色说话长度几乎一样 —— 声音没有区分度"},
    {"code": "aphorism_density", "label": "格言式单句密度", "unit": "比例",
     "direction": "max", "criterion": "J19",
     "na_when_no_dialogue": False,
     "hint": "独立成段的短警句太多 —— 说教味，像在给读者总结道理"},
    {"code": "dialogue_cover", "label": "对话段占比", "unit": "比例",
     "direction": "min", "criterion": "J20",
     "na_when_no_dialogue": True,
     "hint": "几乎没有对话段 —— 通篇叙述，节奏容易发闷"},
    {"code": "avg_para_len", "label": "平均段长", "unit": "字",
     "direction": "max", "criterion": "J21",
     "na_when_no_dialogue": False,
     "hint": "段落平均太长 —— 手机阅读吃力"},
]
CODES = [d["code"] for d in DIMENSIONS]
BY_CODE = {d["code"]: d for d in DIMENSIONS}

QUOTE_OPEN, QUOTE_CLOSE = "\u201c", "\u201d"          # “ ”（项目统一弯引号）
SENT_END = "。！？…"
# ⚠️ 必须**保留**句末标点：用 split 会把 ？ 吃掉，导致“问号占对白”恒为 0
# （2026-11-05 实测，第一版就是这么错的）。
SENT_RE = re.compile(r"[^。！？…]+[。！？…]*")
SPEAKER_VERBS = "说|道|问|答|喊|叫|开口|回答|笑道|低声说|轻声说|沉声说"
SPEAKER_TAIL_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,4})(?:低声|轻声|冷声|沉声|笑着|苦笑着|忽然|终于)?"
    r"(?:%s)[，,：:]?\s*$" % SPEAKER_VERBS)
PRONOUNS = {"他们", "我们", "你们", "两人", "三人", "众人", "大家", "有人",
            "那人", "这人", "老人", "女人", "男人", "孩子", "少年"}


def _speaker_in_head(head):
    """从引号前的文字里认说话人：先认“X 说/道”贴尾，再认段落开头那个名字。

    为什么要第二条：中文小说常写“陆九把烟按灭在鞋底，说：‘…’”，
    名字在段首、动词在段尾 —— 只认贴尾会把这些全漏掉。
    """
    head = (head or "").strip()
    if not head:
        return ""
    m = SPEAKER_TAIL_RE.search(head[-18:])
    if m and m.group(1) not in PRONOUNS:
        return m.group(1)
    if any(v in head for v in ("说", "道", "问", "答", "喊", "叫")):
        # 段首名字必须有边界：`陆九把烟按灭在鞋底，说：` 的段首 2~3 字后面
        # 仍是汉字，说明那是句子而不是名字 —— 宁可不认，也不认成“陆九把”。
        for n in (2, 3):
            cand = head[:n]
            if len(cand) != n or not re.fullmatch(r"[\u4e00-\u9fa5]+", cand):
                continue
            if len(head) > n and re.match(r"[\u4e00-\u9fa5]", head[n]):
                continue
            if cand not in PRONOUNS and cand[-1] not in "说道问答喊叫":
                return cand
    return ""


MIN_SPEAKER_TURNS = 3          # 少于 3 句的说话人不参与“句长差”（噪声太大）
APHORISM_MIN, APHORISM_MAX = 6, 25


def _char_count(s):
    """中文字数：去掉所有空白后的字符数（与项目其它统计口径一致）。"""
    return len(re.sub(r"\s+", "", s or ""))


def split_paragraphs(text):
    """按空行切段（单换行不切，符合 Markdown 习惯）。"""
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def _dialogue_sentences(para):
    """取一段里所有对白句（引号内文本再按句末标点切开，**标点保留**）。"""
    out = []
    for chunk in re.findall("%s([^%s]{1,400})%s" % (QUOTE_OPEN, QUOTE_CLOSE, QUOTE_CLOSE), para):
        for m in SENT_RE.finditer(chunk):
            piece = m.group(0).strip()
            if piece:
                out.append(piece)
    return out


def measure(text):
    """算一章的六项指标。返回 `{code: 数值 | None}`（None = 不适用/无数据）。"""
    paras = split_paragraphs(text)
    if not paras:
        return {c: None for c in CODES}

    turns = []            # [{q: bool, speaker: str, length: int}] —— 按出现顺序
    dialogue_paras = 0
    for para in paras:
        has_dialogue = QUOTE_OPEN in para and QUOTE_CLOSE in para
        if has_dialogue:
            dialogue_paras += 1
        head = para.split(QUOTE_OPEN)[0] if QUOTE_OPEN in para else ""
        speaker = _speaker_in_head(head)
        for s in _dialogue_sentences(para):
            q = s.rstrip().endswith(("？", "?"))
            turns.append({"q": q, "speaker": speaker, "length": _char_count(s)})

    n_para = len(paras)
    metrics = {}
    # 1) 问号占对白
    metrics["question_ratio"] = (
        round(sum(1 for t in turns if t["q"]) / len(turns), 4) if turns else None)
    # 2) 连续单回合问答（问 → 非问，成对推进）
    best = cur = 0
    i = 0
    while i < len(turns) - 1:
        if turns[i]["q"] and not turns[i + 1]["q"]:
            cur += 1
            best = max(best, cur)
            i += 2
        else:
            cur = 0
            i += 1
    metrics["single_turn_streak"] = best if turns else None
    # 3) 角色间平均句长差
    by_speaker = {}
    for t in turns:
        if t["speaker"]:
            by_speaker.setdefault(t["speaker"], []).append(t["length"])
    means = [sum(v) / len(v) for v in by_speaker.values() if len(v) >= MIN_SPEAKER_TURNS]
    metrics["speaker_spread"] = (round(max(means) - min(means), 2) if len(means) >= 2 else None)
    # 4) 格言式单句密度
    non_dialogue = [p for p in paras if QUOTE_OPEN not in p]
    aphis = [p for p in non_dialogue
             if APHORISM_MIN <= _char_count(p) <= APHORISM_MAX
             and p.rstrip().endswith(("。", "！", "？"))
             and not re.search(r"[，,；;：:、]", p)]
    metrics["aphorism_density"] = (
        round(len(aphis) / len(non_dialogue), 4) if non_dialogue else None)
    # 5) 对话段占比
    metrics["dialogue_cover"] = round(dialogue_paras / n_para, 4) if n_para else None
    # 6) 平均段长
    metrics["avg_para_len"] = round(sum(_char_count(p) for p in paras) / n_para, 2) if n_para else None
    # 全文没有对白（散文 / 书信 / 说明体）：与对白相关的四项判 N/A ——
    # 否则“对话段占比 0%”会把散文一律标成“需抽样”，那是误报不是提示。
    if not turns:
        for code in ("question_ratio", "single_turn_streak",
                     "speaker_spread", "dialogue_cover"):
            metrics[code] = None
    return metrics


def evaluate(metrics, limits):
    """按口径判断哪些指标越线。`limits` = {判据 id: 阈值}。

    返回 `(triggered, na)`：
    - `triggered` = [{code,label,value,limit,direction,hint}]
    - `na` = [{code,label,reason}] —— 不适用（无对白 / 说话人不足）或缺阈值
    """
    triggered, na = [], []
    no_dialogue = metrics.get("dialogue_cover") is None
    for d in DIMENSIONS:
        code = d["code"]
        v = metrics.get(code)
        limit = limits.get(d["criterion"])
        if v is None:
            reason = ("全文没有对白" if (no_dialogue and d["na_when_no_dialogue"])
                      else "本项无可用数据（如说话人不足两人）")
            na.append({"code": code, "label": d["label"], "reason": reason})
            continue
        if limit is None:
            na.append({"code": code, "label": d["label"],
                       "reason": "口径缺 %s（先升级口径表）" % d["criterion"]})
            continue
        over = (v < limit) if d["direction"] == "min" else (v > limit)
        if over:
            triggered.append({"code": code, "label": d["label"], "value": v,
                              "limit": limit, "direction": d["direction"],
                              "hint": d["hint"]})
    return triggered, na


def _dialogue_max(root):
    """读 `chapter.max_dialogue_ratio`（默认 0.35；0/非法 = 不限）。

    只读一次、失败回落默认 —— 阶段 5 不该因为一个配置读不到就整段指标消失。
    """
    try:
        from utils.config_io import load_config_yaml
        cfg = load_config_yaml(str(Path(root) / "config" / "system.yaml")) or {}
        raw = ((cfg.get("chapter") or {}).get("max_dialogue_ratio"))
        v = float(raw) if raw is not None else 0.35
        return v if 0 < v <= 1 else 0.0
    except Exception:                                          # noqa: BLE001
        return 0.35


def collect(root, chapter_dir=None, limit=None):
    """扫 `data/chapters/raw/` 逐章算指标。零 token、只读。

    `limit` 可传章节号列表（只看这几章）；默认全部。
    """
    root = Path(root)
    cdir = Path(chapter_dir) if chapter_dir else (root / "data" / "chapters" / "raw")
    doc = cal.load(root)
    if not doc:
        return {"ok": False, "error": "缺口径：data/judge/caliber.json 不存在或读不出来"}
    limits = cal.values(doc)
    missing = [c for c in ("J16", "J17", "J18", "J19", "J20", "J21") if c not in limits]
    # 对话段占比**上限**（2026-11-05，试跑第 26 条）：这是**产品约束**不是判据 ——
    # 与 J20（低于线=对话太少）方向相反；超了只把该章标进“需人工抽样”，
    # 不判合格、不拦阶段 5（篇幅配比是创作决策）。
    dmax = _dialogue_max(root)

    chapters, skipped = [], []
    files = sorted(cdir.glob("[0-9][0-9].md")) if cdir.exists() else []
    for p in files:
        try:
            no = int(p.stem)
        except ValueError:
            continue
        if limit and no not in limit:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            skipped.append({"chapter": no, "reason": "%s: %s" % (type(e).__name__, e)})
            continue
        m = measure(text)
        trig, na = evaluate(m, limits)
        cover = m.get("dialogue_cover")
        over = bool(dmax and cover is not None and cover > dmax)
        chapters.append({
            "chapter": no,
            "metrics": m,
            "triggered": trig,
            "na": na,
            # 超上限是**独立告警**，不并进 needs_sampling（那个字段的语义是
            # “判据 J16–J21 被触发”）；阶段 5 会单独列出来。
            "over_dialogue_max": over,
            "dialogue_max": dmax,
            "needs_sampling": bool(trig),
        })

    summary = {
        "chapters": len(chapters),
        "needs_sampling": [c["chapter"] for c in chapters if c["needs_sampling"]],
        "over_dialogue_max": [c["chapter"] for c in chapters if c.get("over_dialogue_max")],
        "dialogue_max": dmax,
        "triggered_count": {},
        "metrics_mean": {},
    }
    for d in DIMENSIONS:
        code = d["code"]
        summary["triggered_count"][code] = sum(
            1 for c in chapters if any(t["code"] == code for t in c["triggered"]))
        vals = [c["metrics"][code] for c in chapters if c["metrics"].get(code) is not None]
        summary["metrics_mean"][code] = round(sum(vals) / len(vals), 4) if vals else None

    return {
        "ok": True,
        "caliber_version": doc.get("version"),
        "dimensions": [dict(d) for d in DIMENSIONS],
        "missing_criteria": missing,
        "chapters": chapters,
        "summary": summary,
        "note": "只提示人工抽样，不判合格、不合成总分 —— 文学质量由作者判断",
        "warnings": skipped,
    }


def render_markdown(result):
    """人读版：一章一行 + 触发原因。"""
    lines = ["# 阶段 5 结构指标（文学质量抽样提示）", ""]
    lines.append("> 这些指标**只提示要不要人工抽样**，不判合格、不合成总分。")
    lines.append("> 阈值来自 `data/judge/caliber.json`（J16–J21），口径版本 v%s。"
                 % result.get("caliber_version"))
    if result.get("missing_criteria"):
        lines += ["", "⚠️ 口径表缺 %s —— 这几项本轮无法判断（先升级口径表）。"
                  % "、".join(result["missing_criteria"])]
    s = result.get("summary") or {}
    lines += ["", "全书：%d 章，需人工抽样 **%d 章**%s"
              % (s.get("chapters", 0), len(s.get("needs_sampling") or []),
                 ("（第 " + "、".join(str(n) for n in s["needs_sampling"]) + " 章）")
                 if s.get("needs_sampling") else ""), ""]
    if s.get("over_dialogue_max"):
        lines.append(
            "\n对话段占比超过上限（`chapter.max_dialogue_ratio` = %.0f%%）：第 %s 章 —— "
            "**只是提示，不判合格**；改不改是作者的决定。"
            % ((s.get("dialogue_max") or 0) * 100,
               "、".join(str(n) for n in s["over_dialogue_max"])))
    if s.get("metrics_mean"):
        parts = []
        for d in DIMENSIONS:
            v = (s["metrics_mean"] or {}).get(d["code"])
            parts.append("%s %s" % (d["label"], "—" if v is None else
                                    ("%.1f%%" % (v * 100) if d["unit"] == "比例" else v)))
        lines.append("均值：" + " · ".join(parts))
        lines.append("")
    lines.append("| 章 | " + " | ".join(d["label"] for d in DIMENSIONS) + " | 判定 |")
    lines.append("|---|" + "---|" * (len(DIMENSIONS) + 1))
    for c in result.get("chapters") or []:
        cells = []
        for d in DIMENSIONS:
            v = c["metrics"].get(d["code"])
            if v is None:
                cells.append("—")
            elif d["unit"] == "比例":
                cells.append("%.1f%%" % (v * 100))
            else:
                cells.append("%.1f" % v)
        verdict = ("**需人工抽样**：" + "；".join(
            "%s %s（%s线 %s）" % (t["label"],
                                 ("%.1f%%" % (t["value"] * 100)) if
                                 BY_CODE[t["code"]]["unit"] == "比例" else t["value"],
                                 "低于" if t["direction"] == "min" else "高于",
                                 ("%.1f%%" % (t["limit"] * 100)) if
                                 BY_CODE[t["code"]]["unit"] == "比例" else t["limit"])
            for t in c["triggered"])) if c["triggered"] else "—"
        lines.append("| %s | %s | %s |" % (c["chapter"], " | ".join(cells), verdict))
    lines.append("")
    return "\n".join(lines)


def write(base_dir, result):
    """落盘：`structure.json`（机读）+ `structure.md`（人读）。"""
    d = Path(base_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "structure.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "structure.md").write_text(render_markdown(result), encoding="utf-8")
    return d / "structure.json"
