# -*- coding: utf-8 -*-
"""节级产物 + 分布型指标 + 指标→批注（R1/R2/R3，2026-11-16）。

## 为什么这三条要一起测

它们是同一条链的三个环节，任何一环断了，"节"就还是纸上的字：

    大纲写节列表（R1 解析）
      → 正文按 `### 节 N｜名` 承载（R1 切分 + 逐节字数 + 速记体判据）
      → 四个分布型指标量出来（R2）
      → 命中项能定位到段、能变成批注（R3 前半）

## 判据（每条都能变红）

- R1a 两种大纲写法都能解析出节，且缺目标时按章目标补（标 derived）
- R1b 正文按 `### 节` 切段，汉字/偏移算对
- R1c 一节明显没写够 → `verdict=thin` 且 `ok=False`（速记体，不许标完成）
- R1d 声明了节但正文一个节标题都没有 → `missing_headings`
- R1e 大纲没节 → `no_sections` 且 `ok=True`（老工作区零回归）
- R1f `complete()` 与报告一致（断点续跑用同一判据）
- R2a 四个核心数与定义相符（连排/短对白/段末短句/说问密度）
- R3a `locate()` 给的是段号而不是"某处"
- R3b `to_annotations()` 真写进批注库，且批注带 code
- R3c 路由既在 ROUTES 里、也在 nf_api 分发器里（否则真机 404）
- R1g 提示词模板真的带上了 `{{sections_block}}`（含出厂副本）

用法：python tests/unit/test_sections_and_metrics.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api                                                # noqa: E402
from utils import prose_metrics as pm                        # noqa: E402
from utils import sections as sec                            # noqa: E402
from nf_api_domains import metrics as dom                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def filler(n, seed="他把伞靠在门边，雨水顺着伞骨往下淌。"):
    """凑 n 个汉字左右的叙述段（用于造"写够了/没写够"的节）。"""
    out = []
    total = 0
    while total < n:
        out.append(seed)
        total += len(seed)
    return "\n\n".join(out)


SECTION1 = "\n\n".join([
    "他推门进来的时候，屋檐上的水线正连成了串。",
    "“你回来得比信上写的晚了三天。”",
    "“路上塌方。”",
    "“塌方挡了你三天？”",
    "“挡了。”",
    filler(400),
])
SECTION2 = "他把名单摊在桌上，看了很久。\n\n“就这些？”她问。"

BODY = "\n\n".join([
    "## 第 3 章 雨夜",
    "### 节 1｜雨夜入门\n\n" + SECTION1,
    "### 节 2｜名单\n\n" + SECTION2,
    "<!-- quality: 7/10 -->",
    "<!-- summary: 他回来了，名单对不上。 -->",
])

OUTLINE_INLINE = """## 第3章 雨夜
- 核心事件：他回家 → 名单对不上 → 决定去水站
- 涉及角色：他、她
- 节列表：
  - 节 1｜雨夜入门｜目标 600｜地点 门厅｜在场 他、她｜节拍 进门→问答→看名单
  - 节 2｜名单｜目标 900
"""

OUTLINE_BLOCKS = """## 第3章 雨夜
### 节 1｜雨夜入门
- 目标字数：600
- 地点：门厅
- 在场：他、她

### 节 2｜名单
- 目标字数：900
- 节拍：名单对不上
"""

OUTLINE_NO_SECTION = """## 第3章 雨夜
- 核心事件：他回家
- 涉及角色：他
"""


def main():
    print("=" * 66)
    print("节级 / 指标 / 批注（R1/R2/R3）")
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw" / "03.md").write_text(BODY, encoding="utf-8")
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "outline" / "chapters" / "03.md").write_text(OUTLINE_INLINE, encoding="utf-8")
    orig_root = nf_api.ROOT
    nf_api.ROOT = tmp
    try:
        print("\n【1】大纲里的节列表（两种写法）")
        a = sec.parse_outline_sections(OUTLINE_INLINE)
        b = sec.parse_outline_sections(OUTLINE_BLOCKS)
        check("R1a 单行写法解析出 2 节", len(a) == 2, a)
        check("R1a 块写法解析出 2 节", len(b) == 2, b)
        check("R1a 名称对", a[0]["name"] == "雨夜入门" and b[0]["name"] == "雨夜入门", a)
        check("R1a 目标字数解析对了", a[0]["target"] == 600 and b[1]["target"] == 900, (a, b))
        check("R1a 地点/在场解析对了", a[0]["place"] == "门厅" and "他" in a[0]["present"], a[0])
        check("R1a 没有节列表 → 空列表", sec.parse_outline_sections(OUTLINE_NO_SECTION) == [])
        filled = sec.fill_targets([{"n": 1, "name": "x", "target": None}], 4000, 7200)
        check("R1a 缺目标按章目标补（标 derived）",
              filled[0]["target"] > 0 and filled[0]["target_source"] == "derived", filled)

        print("\n【2】正文按节切")
        parts = sec.split_body_sections(BODY)
        check("R1b 切出 2 节", len(parts) == 2, parts)
        check("R1b 节 1 汉字数 > 节 2", parts[0]["hanzi"] > parts[1]["hanzi"], parts)
        check("R1b 偏移递增", parts[0]["offset"] < parts[1]["offset"], [p["offset"] for p in parts])
        check("R1b 节标题不进正文", "节 1｜" not in parts[0]["text"])

        print("\n【3】速记体判据")
        rep = sec.measure(3, BODY, OUTLINE_INLINE, {"chapter": {"target_words": [4000, 7200]}})
        check("R1c 声明了节", rep["declared"] is True)
        check("R1c 判 thin（节 2 只有 %d 字 / 目标 900）" % rep["sections"][1]["hanzi"],
              rep["verdict"] == "thin" and rep["ok"] is False, rep)
        check("R1c 说清是哪一节短", 2 in (rep.get("short") or []), rep.get("short"))
        rep2 = sec.measure(3, "## 第 3 章 雨夜\n\n他把伞靠在门边。\n", OUTLINE_INLINE, {})
        check("R1d 正文没有节标题 → missing_headings",
              rep2["verdict"] == "missing_headings" and rep2["ok"] is False, rep2)
        rep3 = sec.measure(3, BODY, OUTLINE_NO_SECTION, {})
        check("R1e 大纲没节 → no_sections 且 ok",
              rep3["verdict"] == "no_sections" and rep3["ok"] is True, rep3)
        check("R1f complete() 与报告一致",
              sec.complete(3, tmp / "data" / "chapters" / "raw" / "03.md",
                           tmp / "data" / "outline" / "chapters" / "03.md", {}) is False)

        saved = sec.measure_and_save(3, BODY, OUTLINE_INLINE,
                                     {"chapter": {"target_words": [4000, 7200]}}, root=str(tmp))
        sp = tmp / "data" / "chapters" / "03" / "sections.json"
        check("R1 落盘 data/chapters/03/sections.json", sp.exists(), sp)
        on_disk = json.loads(sp.read_text(encoding="utf-8"))
        check("R1 落盘内容与内存一致", on_disk["verdict"] == saved["verdict"], on_disk.get("verdict"))

        print("\n【4】分布型指标（R2）")
        st = pm.analyze(BODY)
        check("R2a 连排对白 ≥1 处（3 段连续引号）", st["dialogue_runs"] >= 1, st["dialogue_runs"])
        check("R2a 对白段数 ≥3", st["dialogue_paragraphs"] >= 3, st["dialogue_paragraphs"])
        check("R2a 短对白占比 > 0", st["short_dialogue_ratio"] > 0, st["short_dialogue_ratio"])
        check("R2a 说/问每万字 > 0", st["say_ask_per_10k"] > 0, st["say_ask_per_10k"])
        check("R2a 段末短句比 > 0", st["end_short_ratio"] > 0, st["end_short_ratio"])
        check("R2a 碎段比有值", "short_paragraph_ratio" in st, st.keys())
        check("R2a 汉字数与唯一口径一致",
              st["hanzi"] == __import__("utils.word_count", fromlist=["x"]).count_cn_words(
                  pm.strip_body(BODY)), st["hanzi"])

        rep_m = pm.measure_and_save(3, BODY, {}, root=str(tmp))
        mp = tmp / "data" / "chapters" / "03" / "metrics.json"
        check("R2 落盘 metrics.json", mp.exists(), mp)
        check("R2 指标里并进了节级数据",
              (json.loads(mp.read_text(encoding="utf-8")).get("sections") or []) != [])
        gate = pm.gate(rep_m, {"dialogue_runs_per_chapter": 0, "short_dialogue_ratio": 0,
                               "end_short_ratio": 0, "say_ask_per_10k": 0})
        check("R2 阈值判定能变红（全部 0 红线时必不过）", gate["ok"] is False, gate)

        print("\n【5】指标 → 定位 → 批注（R3）")
        hits = pm.locate(BODY)
        check("R3a 有命中项", bool(hits), hits[:2])
        check("R3a 命中项带段号", all(isinstance(h.get("para_index"), int) for h in hits))
        check("R3a 连排对白被点名", any(h["code"] == "dialogue_run" for h in hits),
              [h["code"] for h in hits])
        made = pm.to_annotations(3, BODY, root=str(tmp))
        check("R3b 批注真写进去了", len(made) > 0, made[:2])
        ann_file = tmp / "data" / "state" / "annotations" / "ch03.json"
        check("R3b 批注库文件存在", ann_file.exists(), ann_file)
        items = json.loads(ann_file.read_text(encoding="utf-8"))["annotations"]
        check("R3b 批注说明里带 code", all("[dialogue_run]" in a["note"]
                                          or "[short_" in a["note"] for a in items), items[0]["note"])

        print("\n【5b】AI 腔体检验收判据：报告里必须带四个分布型数")
        from utils import ai_voice
        scan = ai_voice.scan_book(tmp, 3)
        ch0 = (scan.get("chapters") or [{}])[0]
        style = ch0.get("style") or {}
        check("R2b /review/unreadable 的章级结果带 style", bool(style), list(ch0))
        check("R2b 四个数都在（不是只报词表）",
              all(k in style for k in ("dialogue_runs", "short_dialogue_ratio",
                                       "end_short_ratio", "say_ask_per_10k")), style)

        print("\n【5c】self 引擎与真跑同一条判据（字数够但节太短 → 仍算没写完）")
        import os
        cwd = os.getcwd()
        try:
            os.chdir(str(tmp))
            (tmp / "data" / "outline" / "chapters").mkdir(parents=True, exist_ok=True)
            (tmp / "data" / "outline" / "chapters" / "01.md").write_text(
                "## 第1章 试\n- 节列表：\n  - 节 1｜A｜目标 100\n  - 节 2｜B｜目标 100\n",
                encoding="utf-8")
            # 字数达标（区间故意放宽），但第二节一个字都没有 → 速记体
            (tmp / "data" / "chapters" / "raw" / "01.md").write_text(
                "## 第 1 章 试\n\n### 节 1｜A\n\n" + "他把伞靠在门边。" * 40 + "\n\n"
                "### 节 2｜B\n\n", encoding="utf-8")
            from stage4_writing import _self_pending
            _total, pend = _self_pending({"chapter": {"target_words": [10, 100000]}},
                                         {"book": {"chapters": 1}})
            check("R1h self 把速记体章节留在待写里", pend == [1], pend)
        finally:
            os.chdir(cwd)

        print("\n【6】路由与模板接线")
        api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        for method, path, fn in (("GET", "/chapters/sections", "handle_chapters_sections"),
                                 ("GET", "/chapters/metrics", "handle_chapters_metrics"),
                                 ("POST", "/chapters/metrics/annotate", "handle_chapters_metrics_annotate")):
            check("R3c ROUTES 有 %s %s" % (method, path),
                  any(m == method and p == path for m, p, _ in dom.ROUTES))
            check("R3c 分发器有 %s" % fn, ("dom_metrics." + fn) in api_src)
        tpl = (ROOT / "prompts" / "stage4_writing.md").read_text(encoding="utf-8")
        fac = ROOT / "prompts" / "factory" / "stage4_writing.md"
        check("R1g 写作模板带 {{sections_block}}", "{{sections_block}}" in tpl)
        check("R1g 出厂副本也带（发版后还原不会丢）",
              fac.exists() and "{{sections_block}}" in fac.read_text(encoding="utf-8"))
        blk = sec.render_prompt_block(sec.fill_targets(sec.parse_outline_sections(OUTLINE_INLINE)))
        check("R1g 渲染块里有节标题与目标", "### 节 1｜雨夜入门" in blk and "个汉字" in blk, blk[:120])
        check("R1g 没节时渲染成空串", sec.render_prompt_block([]) == "")
    finally:
        nf_api.ROOT = orig_root

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
