# -*- coding: utf-8 -*-
"""分布型文风指标：量“读着像 AI”的结构证据，而不是数词表（R2，2026-11-16）。

## 为什么单靠词表不算体检

OB37 施工回执 §四.1 的第一条：`prose_compliance_check.py` 报“AI 腔 0”，
而作者不信 —— 因为它只查 5 条固定说法（不是…是、在这一刻、仿佛整个世界都、
五味杂陈、从此以后）。那本书真正的病灶是分布型的三件事：

- 四百处“连排对白”（连续 ≥3 段以引号开头）—— 问答阶梯；
- 一半对白不到 12 个汉字 —— 谁也不好好说话；
- 近半数段落拿短句收尾 —— 段末金句病。

这三件都**不在词表里**。本书最后是靠自写的 `check_rhythm.py` 把数字打出来、
再按数字逐段手术才压下去的（411 处 → 71 处；≤12 字对白 62% → 26%；
段末短句 45% → 21%）。本模块把那四个数变成墨坊的一等产物。

## 四个核心数（口径照抄 OB37 的脚本，不重新发明）

| 指标 | 定义 |
|---|---|
| `dialogue_runs` | 连续 ≥3 段以““”开头的段落，算一处 |
| `short_dialogue_ratio` | ≤12 汉字的对白段 / 全部对白段 |
| `end_short_ratio` | 段末句 ≤12 汉字的段落 / 有句子的段落 |
| `say_ask_per_10k` | 正文里“说”“问”出现次数 ÷ 汉字数 × 10000 |

另外附带句级分布（均句长、句长标准差、≤8 字句占比、恰好 3 句段占比），
它们便宜且能解释上面四个数为什么长这样。

## 纪律

- **只量不写**（与 `utils/ai_voice.py` 同一条纪律）：要改由作者点批注（R3）。
- 数字与定位**同源**：`locate()` 给的不是"第 37 段起"，而是带段号+引文的命中项，
  可以直接变成批注（`to_annotations`），批注又能直接喂已有的段落级改写入口。
"""
from __future__ import annotations

import json
import re
import statistics as st
import time
from pathlib import Path

from utils.word_count import count_cn_words

SENT_RE = re.compile(r"[^。！？…]*[。！？…]+")
DLG_HEAD = "“"
SHORT_DIALOGUE = 12          # ≤12 汉字的对白算"一句半口气"
SHORT_END = 12               # ≤12 汉字的段末句算"金句病"
RUN_MIN = 3                  # 连排 ≥3 段算一处问答阶梯

#: 指标的默认红线（作者可改，改的是 `config/system.yaml` 的 `prose.thresholds`）。
#: 数值取自 OB37 润色后的实测水位，**不是普适真理** —— 紧张段落短句多是正当的。
DEFAULT_THRESHOLDS = {
    "dialogue_runs_per_chapter": 8,
    "short_dialogue_ratio": 0.50,
    "end_short_ratio": 0.25,
    "say_ask_per_10k": 180,
}


def strip_body(text):
    """去掉章标题以外的结构行与“本章自查”尾注，得到可量的正文。

    OB37 的脚本用 `split("## 本章自查")[0]` 去掉模型自评；这里同样处理 ——
    自评里也写着"这段对白太多"，数进去等于让报告自己污染自己。
    """
    raw = str(text or "").split("## 本章自查")[0]
    keep = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith(("#", ">", "---")):
            continue
        keep.append(line)
    return "\n".join(keep)


def paragraphs(text):
    return [p.strip() for p in strip_body(text).split("\n\n") if p.strip()]


def _sents(paragraph):
    return [s for s in SENT_RE.findall(paragraph) if count_cn_words(s) > 0]


def dialogue_runs(paras):
    """连排对白：返回 `[(段数, 起始段号 0 起, 首句摘要)]`。"""
    flags = [p.startswith(DLG_HEAD) for p in paras]
    out, i = [], 0
    while i < len(paras):
        if flags[i]:
            j = i
            while j < len(paras) and flags[j]:
                j += 1
            if j - i >= RUN_MIN:
                out.append((j - i, i, paras[i][:24]))
            i = j
        else:
            i += 1
    return out


def analyze(text):
    """量一段正文，返回指标 dict（零 token、纯函数）。"""
    paras = paragraphs(text)
    sents = [s for p in paras for s in _sents(p)]
    slen = [count_cn_words(s) for s in sents]
    dlg = [p for p in paras if p.startswith(DLG_HEAD)]
    dlen = [count_cn_words(p) for p in dlg]
    endlen = []
    for p in paras:
        ss = _sents(p)
        if ss:
            endlen.append(count_cn_words(ss[-1]))
    han = count_cn_words(strip_body(text))
    runs = dialogue_runs(paras)

    def pct(num, den):
        return round(num / den, 3) if den else 0.0

    return {
        "hanzi": han,
        "paragraphs": len(paras),
        "sentences": len(sents),
        "avg_sentence": round(st.mean(slen), 1) if slen else 0.0,
        "sd_sentence": round(st.pstdev(slen), 1) if len(slen) > 1 else 0.0,
        "short_sentence_ratio": pct(sum(1 for x in slen if x <= 8), len(slen)),
        "three_sentence_ratio": pct(
            sum(1 for p in paras if len(_sents(p)) == 3), len(paras)),
        # 碎段：≤20 汉字的段落。OB37 的"语体轮"判据就是压这个比例（v3 之前一堆碎段）
        "short_paragraph_ratio": pct(
            sum(1 for p in paras if count_cn_words(p) <= 20), len(paras)),
        "dialogue_paragraphs": len(dlg),
        "dialogue_runs": len(runs),
        "dialogue_run_paragraphs": sum(r[0] for r in runs),
        "short_dialogue_ratio": pct(sum(1 for x in dlen if x <= SHORT_DIALOGUE), len(dlen)),
        "avg_dialogue": round(st.mean(dlen), 1) if dlen else 0.0,
        "end_short_ratio": pct(sum(1 for x in endlen if x <= SHORT_END), len(endlen)),
        "avg_end_sentence": round(st.mean(endlen), 1) if endlen else 0.0,
        "say_ask_per_10k": int(round(
            (str(strip_body(text)).count("说") + str(strip_body(text)).count("问"))
            / han * 10000)) if han else 0,
    }


# ---------------------------------------------------------------- 定位（R3 的前半）
def locate(text):
    """把命中的结构问题**定位到段**，返回可直接变批注的命中项。

    只报三类能机械定位的（对白连排 / 短对白 / 短段末），每条带：
    `code`、`para_index`（0 起，与段落精修同一套段号）、`quote`、`why`、`action`。
    """
    paras = paragraphs(text)
    hits = []
    for length, start, head in dialogue_runs(paras):
        hits.append({
            "code": "dialogue_run",
            "para_index": start,
            "span": [start, start + length - 1],
            "quote": head,
            "why": "连续 %d 段起于引号 —— 问答阶梯：读者读不到动作与场景" % length,
            "action": "把其中几轮问答并进叙述，只留最关键的一问一答",
        })
    for i, p in enumerate(paras):
        if not p.startswith(DLG_HEAD):
            continue
        n = count_cn_words(p)
        if n <= SHORT_DIALOGUE:
            hits.append({
                "code": "short_dialogue",
                "para_index": i,
                "quote": p[:40],
                "why": "这一句对白只有 %d 个汉字 —— 短对白连着来就是“谁也不好好说话”" % n,
                "action": "给它加动作/停顿/潜台词，或与相邻叙述合并",
            })
    for i, p in enumerate(paras):
        ss = _sents(p)
        if not ss:
            continue
        last = ss[-1]
        if count_cn_words(last) <= SHORT_END:
            hits.append({
                "code": "short_end",
                "para_index": i,
                "quote": last.strip()[:40],
                "why": "段末只有 %d 个汉字 —— 段末金句病（每一段都想收个漂亮的尾）"
                       % count_cn_words(last),
                "action": "让这一段以具体动作或物件收尾，不要以格言收尾",
            })
    return hits


def gate(report, thresholds=None):
    """按阈值判“这一章算不算过”。返回 `{ok, fails: [{code, value, limit}]}`。

    **只报不挡流程**：过不过由作者定（回执 §八：短句留不留是人的事）。
    R3 的闭环就是"照着 fails 去改，改完数字必须变"。
    """
    th = dict(DEFAULT_THRESHOLDS)
    th.update(thresholds or {})
    style = (report or {}).get("style") or report or {}
    fails = []
    pairs = (
        ("dialogue_runs_per_chapter", "dialogue_runs", ">="),
        ("short_dialogue_ratio", "short_dialogue_ratio", ">="),
        ("end_short_ratio", "end_short_ratio", ">="),
        ("say_ask_per_10k", "say_ask_per_10k", ">="),
    )
    for limit_key, value_key, _op in pairs:
        limit = th.get(limit_key)
        val = style.get(value_key)
        if limit is None or val is None:
            continue
        if float(val) >= float(limit):
            fails.append({"code": value_key, "value": val, "limit": limit})
    return {"ok": not fails, "fails": fails, "thresholds": th}


# ---------------------------------------------------------------- 落盘 / 汇总
def metrics_path(chapter, root="."):
    return Path(root) / "data" / "chapters" / ("%02d" % int(chapter)) / "metrics.json"


def build_report(chapter, text, thresholds=None, sections_report=None):
    style = analyze(text)
    rep = {"chapter": int(chapter), "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "hanzi": style["hanzi"], "style": style}
    if sections_report is not None:
        rep["sections"] = sections_report.get("sections") or []
        rep["section_verdict"] = sections_report.get("verdict")
    rep["gate"] = gate(rep, thresholds)
    return rep


def write_report(report, root="."):
    p = metrics_path(report.get("chapter"), root)
    p.parent.mkdir(parents=True, exist_ok=True)
    from utils.file_io import write_text
    write_text(p, json.dumps(report, ensure_ascii=False, indent=2))
    return p


def read_report(chapter, root="."):
    try:
        return json.loads(metrics_path(chapter, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def measure_and_save(chapter, text, cfg=None, root="."):
    """量 + 落盘（`data/chapters/NN/metrics.json`）。顺带把节级报告并进来。

    写盘失败只留痕 —— 指标是"给人看的证据"，不该让它把写作链路弄挂。
    """
    thresholds = ((cfg or {}).get("prose") or {}).get("thresholds") or {}
    try:
        from utils import sections as _sec
        sec_rep = _sec.measure(chapter, text, _outline_text(chapter, root), cfg)
    except Exception:                                          # noqa: BLE001
        sec_rep = None
    rep = build_report(chapter, text, thresholds, sec_rep)
    try:
        write_report(rep, root)
    except Exception as e:                                     # noqa: BLE001
        print("[prose_metrics] ⚠ 指标写盘失败（章节不受影响）：%s" % e)
    return rep


def _outline_text(chapter, root="."):
    p = Path(root) / "data" / "outline" / "chapters" / ("%02d.md" % int(chapter))
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------- 变批注（R3 的后半）
def to_annotations(chapter, text, root=".", codes=None, limit=40):
    """把命中项变成**段落批注**（已有的批注系统，带批注 = 带"该改这一段"的句柄）。

    为什么走批注而不是直接改：`utils/ai_voice.py` 的纪律是"只报不写回"，
    在已定稿阶段是对的做法。批注把"报"接到"改"上，但**改不改仍由作者点** ——
    `POST /chapters/paragraph/preview {annotation_id}` 就是这条闭环的落点。
    """
    from utils import annotations as ann
    from refine_paragraph import split_paragraphs
    paras = split_paragraphs(strip_body(text))
    hits = [h for h in locate(text) if not codes or h["code"] in codes]
    made = []
    for h in hits[:limit]:
        idx = int(h.get("para_index") or 0)
        if idx < 0 or idx >= len(paras):
            continue
        try:
            item = ann.add(root, chapter, "[%s] %s 处置：%s"
                           % (h["code"], h["why"], h["action"]), idx, paras, layer="refined")
        except (ValueError, OSError) as e:
            print("[prose_metrics] ⚠ 批注写入失败（第 %d 段）：%s" % (idx, e))
            continue
        made.append({"id": item["id"], "code": h["code"], "para_index": idx,
                     "quote": h.get("quote"), "why": h["why"], "action": h["action"]})
    return made


def summary_line(report):
    """一行话（CLI / 界面提示用），四个人能看懂的数。"""
    s = (report or {}).get("style") or {}
    return ("连排对白 %s 处 · ≤12 字对白 %s%% · 段末短句 %s%% · 说/问每万字 %s"
            % (s.get("dialogue_runs"), round((s.get("short_dialogue_ratio") or 0) * 100),
               round((s.get("end_short_ratio") or 0) * 100), s.get("say_ask_per_10k")))
