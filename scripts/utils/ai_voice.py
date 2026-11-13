# -*- coding: utf-8 -*-
"""AI 腔确定性扫描（零 token，只报告不改写）。

## 为什么要有这一层

“去 AI 腔”此前只是一句写进任务书的提示词（`rewrite_studio` 的 dehumanize 模式）：
模型照不照做，没有任何依据可查；作者也只能凭感觉说“这段像 AI 写的”。
2026-11-07 作者点名“去 AI 腔你自己造样本” —— 样本不是拿来看的，是用来**校准检测**的。

## 五个特征（阈值由 `examples/samples/ai_voice/` 的对照样本校准）

| code | 查什么 | 为什么它在 |
|---|---|---|
| `explain_narration` | “她感到 / 他意识到 / 我明白”这类**解释性旁白** | 好的写法让动作与对话自己说话，旁白是把结论塞给读者 |
| `universal_adjective` | 莫名 / 一丝 / 淡淡 / 微微 / 悄然 / 格外 一类**万能修饰** | 它们可以贴在任何名词前，等于什么都没写 |
| `symmetry` | “不是……而是……” / “并非……而是……” | 对称句式连用是典型的模型腔 |
| `cliche_phrase` | “在这个……时代 / 值得注意的是 / 总而言之 / 生活就是这样” | 模板套话，换任何一本书都成立 |
| `aphorism_ending` | 段末一句短句，且落在抽象名词上（人生 / 成长 / 命运 / 意义） | “每段都收在一个金句上”是模型最稳定的习惯 |

## 纪律（与同质化检测同一条）

- **只报告，不改写、不阻断、不算分**：误报的成本是作者多看一眼，漏报的成本是烂稿静默通过。
- 命中是**证据**，不是结论：报告里给原文片段，作者自己判断打没打中。
- 阈值只在 `THRESHOLDS` 一处；改阈值先跑 `tests/unit/test_ai_voice.py`（对照样本必须仍然分开）。
"""
from __future__ import annotations

import re
from pathlib import Path

# 判定线：命中 ≥2 类才算“疑似”（单类命中在正常中文里也可能出现：
# 一个人物真的“意识到”某件事并不等于 AI 腔）。
THRESHOLDS = {"suspect_feature_count": 2, "max_hits_per_feature": 30}

FEATURES = [
    {"code": "explain_narration", "label": "解释性旁白",
     "hint": "把“她感到/他意识到”换成动作或对话，让读者自己得出",
     "re": re.compile(r"[他她它我][感到觉]?(到)?(一阵|一种|一丝|几分)?"
                      r"(感到|意识到|明白过来|明白了|懂得了|发现原?来?|觉得)")},
    {"code": "universal_adjective", "label": "万能修饰词",
     "hint": "莫名 / 一丝 / 淡淡 / 微微 这类词可以贴在任何名词前，等于没写",
     "re": re.compile(r"(莫名的?|一丝|一抹|淡淡的?|微微|缓缓地?|静静地?|深深地?|"
                      r"悄悄地?|轻轻地?|悄然|格外|不禁|不由得)")},
    {"code": "symmetry", "label": "对称排比句",
     "hint": "“不是……而是……”连用是模型腔；拆成两句具体的话",
     "re": re.compile(r"(不是[^。！？\n]{1,24}而是|并非[^。！？\n]{1,24}而是|"
                      r"既[^。！？\n]{1,16}又[^。！？\n]{1,16})")},
    {"code": "cliche_phrase", "label": "模板套话",
     "hint": "换任何一本书都成立的句子，删掉不影响任何东西",
     "re": re.compile(r"(在这个[^。！？\n]{0,12}的时代|值得注意的是|总而言之|"
                      r"不难发现|让我们|某种意义上|生活就是这样|注定是不平凡的|"
                      r"由此可见|综上所述)")},
]

# 段末金句：短句 + 抽象名词。单独一条规则（需要看段落边界，不是整篇正则）。
APHORISM = re.compile(r"(人生|命运|生活|成长|勇气|孤独|时间|意义|真相|自由|爱|"
                      r"世界|选择|告别|开始|结束)")
SENT_SPLIT = re.compile(r"[。！？!?…]+")
MAX_APHORISM_LEN = 24

# —— 句法层：读不通的句子（2026-11-14，用作者给的三条真例子校准）——
#
# 与腔调层的五类**分开算**：腔调是“像 AI 写的”，读不通是“一句也没读懂”。
# 两者混进同一个数字，作者不知道该改文风还是改句子，所以这一组**不进 suspect 判定线**。
#
# 能力边界（如实写在这里，别让下一个人以为它万能）：
# 只抓**结构缺件**（没头没尾的碎句 / 引介语后面没有主句）。语义讲不通
# （例：“两条腿都已有主”）要读懂上下文才判得了，确定性规则不判 ——
# 那条落在判定器的“极短独立段”与人工抽样里，不在这里假装能测。
ANSWERS = {"嗯", "是", "好", "行", "不", "对", "啊", "哦", "喂", "唉", "呃"}
FRAGMENT_MAX = 2          # 单字/两字独立成句才可疑（更长的省略句在中文里是常态）
CJK_ONLY = re.compile(r"^[\u4e00-\u9fff]+$")
ANY_QUOTE = re.compile(r"[“”‘’\"]")
DANGLING_RE = re.compile(r"^(包括|除了|除去|至于|关于|对于|就)[^，,。！？]{0,20}[，,]")
DANGLING_TAIL_MAX = 10
SUBJECT_WORDS = re.compile(r"(他|她|它|我|你|们|谁|人|咱)")
LEADING_ADVERB = "也都就才便还"
UNREADABLE_LABELS = {
    "fragment_sentence": ("碎句（没头没尾）",
                          "这句单独成句，但读不出谁做了什么；补出主语和动作，或并回上一句"),
    "dangling_connector": ("半句话（引介语之后没有主句）",
                           "“包括 / 除了 / 对于……”后面要跟一个完整的主句，别让它悬着"),
}


def _paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def scan_unreadable(text):
    """句法层：结构缺件的候选句。**只报句子，不改写、不进 suspect 判定线。**

    ## 三条护栏（每条都对应一个误报源）

    1. **只看 ≥2 句的段落**：一句成段的是标题、署名、刻意的强调，不是“读不通”。
       （书信体的落款“妹”就是一句成段 —— 不加这条，样本会当场误报。）
    2. **跳过带引号的句子**：对白里人物只答一个字是人物的事，不是作者行文的问题。
    3. **半句话要求“后半句以也/都/就/才/便/还开头、无主语、无完成态”**：
       “至于钱，以后再说”这类正常省略句被挡在外面。
    """
    found = {"fragment_sentence": [], "dangling_connector": []}
    for p in _paragraphs(text or ""):
        sents = [s.strip() for s in re.split(r"[。！？!?…\n]", p) if s.strip()]
        # 段内必须有真正的句末标点：署名 / 日期 / 地址块是靠换行排版的短行
        # （书信落款“妹 / 十一月四日夜”），把它们当碎句是纯误报。
        if len(sents) < 2 or not re.search(r"[。！？!?…]", p):
            continue
        for s in sents:
            if ANY_QUOTE.search(s):
                continue
            if len(s) <= FRAGMENT_MAX and CJK_ONLY.match(s) and s not in ANSWERS:
                found["fragment_sentence"].append(s)
                continue
            m = DANGLING_RE.match(s)
            if not m:
                continue
            tail = s[m.end():].strip()
            if (0 < len(tail) <= DANGLING_TAIL_MAX and tail[0] in LEADING_ADVERB
                    and not SUBJECT_WORDS.search(tail) and "了" not in tail):
                found["dangling_connector"].append(s)
    return [{"code": code, "label": UNREADABLE_LABELS[code][0], "count": len(items),
             "samples": items[:5], "hint": UNREADABLE_LABELS[code][1]}
            for code, items in found.items() if items]


def scan_text(text):
    """扫一段正文。返回命中清单与“疑似”结论（只有报告，不含改写建议的正文）。"""
    text = text or ""
    hits = []
    for f in FEATURES:
        found = [m.group(0) for m in f["re"].finditer(text)]
        if found:
            hits.append({"code": f["code"], "label": f["label"],
                         "count": len(found), "samples": found[:5],
                         "hint": f["hint"]})
    # 段末金句：只看每段最后一句，且必须短
    aph = []
    for p in _paragraphs(text):
        sents = [s.strip() for s in SENT_SPLIT.split(p) if s.strip()]
        if not sents:
            continue
        last = sents[-1]
        if len(last) <= MAX_APHORISM_LEN and APHORISM.search(last):
            aph.append(last)
    if aph:
        hits.append({"code": "aphorism_ending", "label": "段末金句",
                     "count": len(aph), "samples": aph[:5],
                     "hint": "每段都收在一个道理上；让画面自己停住，不必总结"})
    kinds = len([h for h in hits if h["count"]])
    unreadable = scan_unreadable(text)
    return {
        "ok": True,
        "chars": len(text),
        "hits": [dict(h, count=min(h["count"], THRESHOLDS["max_hits_per_feature"]))
                 for h in hits],
        "feature_count": kinds,
        "suspect": kinds >= THRESHOLDS["suspect_feature_count"],
        "unreadable": unreadable,
        "unreadable_count": len(unreadable),
        "verdict": ("疑似 AI 腔：命中 %d 类特征（这是证据，不是结论）" % kinds
                    if kinds >= THRESHOLDS["suspect_feature_count"]
                    else "未达到“疑似”线（命中 %d 类）" % kinds),
    }


def scan_file(path):
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "文件不存在：%s" % p}
    return dict(scan_text(p.read_text(encoding="utf-8", errors="ignore")),
                path=str(p))


def scan_book(root, chapter=None):
    """全书（或单章）扫句法层。零 token 只读，**不写盘、不改稿**。

    取正文的层按 `refined > checked > raw`（直接复用 `utils.cliche_scan.chapter_path`：
    同一个问题只许一份判据，别在这里再写一遍优先级）。
    章节集合取三层并集 —— 只看一层会在"只剩 refined"的工作区里零循环，报出假绿。
    """
    from utils.cliche_scan import chapter_path
    root = Path(root)
    if chapter:
        nums = [int(chapter)]
    else:
        found = set()
        for rel in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
            d = root / rel
            if d.is_dir():
                found.update(int(f.stem) for f in d.glob("*.md") if f.stem.isdigit())
        nums = sorted(found)
    chapters = []
    for n in nums:
        p = chapter_path(n, root)
        if p is None:
            chapters.append({"chapter": n, "error": "这一章三层正文都没有"})
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        hits = scan_unreadable(text)
        chapters.append({
            "chapter": n,
            "file": str(p.relative_to(root)).replace("\\", "/"),
            "layer": p.parent.name,
            "chars": len(text),
            "hits": hits,
            "count": sum(h["count"] for h in hits),
        })
    return {"ok": True, "chapters": chapters,
            "total": sum(c.get("count") or 0 for c in chapters)}


def report_markdown(res):
    """给任务书/讨论用的一小段人话（没有命中就明说没有）。"""
    if not res.get("ok"):
        return "（扫描失败：%s）" % res.get("error")
    if not res.get("hits"):
        lines = ["机器扫了一遍，没有命中五类特征里任何一类 —— "
                 "但这不等于“没有 AI 腔”，作者的眼睛仍是第一位。"]
    else:
        lines = ["机器扫到 %d 类疑似特征（**证据，不是结论**）：" % res["feature_count"], ""]
        for h in res["hits"]:
            ex = "、".join("“%s”" % s for s in h["samples"][:3])
            lines.append("- **%s** ×%d：%s" % (h["label"], h["count"], ex))
            lines.append("  - 怎么改：%s" % h["hint"])
        lines += ["", "以上只说明“有这些形状”，是不是 AI 腔由你判断。"]
    unread = res.get("unreadable") or []
    if unread:
        # 句法层单独一段：它回答的是“这句读得通吗”，与上面“像不像 AI”不是一件事。
        lines += ["", "**另外，句法层扫到读不通的候选句**（不进上面的判定线，只看结构）：", ""]
        for h in unread:
            ex = "、".join("“%s”" % s for s in h["samples"][:3])
            lines.append("- %s ×%d：%s" % (h["label"], h["count"], ex))
            lines.append("  - 怎么改：%s" % h["hint"])
        lines += ["", "这一组只抓“结构缺件”（没头没尾、半句话）。语义上讲不通的句子"
                      "（要读懂上下文才知道缺什么）机器判不了，仍需你自己看。"]
    return "\n".join(lines)
