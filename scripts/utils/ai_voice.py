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


def _paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


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
    return {
        "ok": True,
        "chars": len(text),
        "hits": [dict(h, count=min(h["count"], THRESHOLDS["max_hits_per_feature"]))
                 for h in hits],
        "feature_count": kinds,
        "suspect": kinds >= THRESHOLDS["suspect_feature_count"],
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


def report_markdown(res):
    """给任务书/讨论用的一小段人话（没有命中就明说没有）。"""
    if not res.get("ok"):
        return "（扫描失败：%s）" % res.get("error")
    if not res.get("hits"):
        return "机器扫了一遍，没有命中五类特征里任何一类 —— 但这不等于“没有 AI 腔”，作者的眼睛仍是第一位。"
    lines = ["机器扫到 %d 类疑似特征（**证据，不是结论**）：" % res["feature_count"], ""]
    for h in res["hits"]:
        ex = "、".join("“%s”" % s for s in h["samples"][:3])
        lines.append("- **%s** ×%d：%s" % (h["label"], h["count"], ex))
        lines.append("  - 怎么改：%s" % h["hint"])
    lines += ["", "以上只说明“有这些形状”，是不是 AI 腔由你判断。"]
    return "\n".join(lines)
