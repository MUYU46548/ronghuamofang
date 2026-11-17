# -*- coding: utf-8 -*-
"""采用候选之后的"重算"闭环（P1-A3，2026-11-17）—— 零 LLM、零网络。

## 为什么

`docs/读改链走查-20261117.md` 问题 A：批注写着"连排对白太多"，改完**没人再算一次那个指标**，
于是"要不要再改"只能靠作者读 diff 猜；批注状态停在 `addressed`，还得逐条手点 resolved。
这里把闭环钉成判据：`recheck_metric_annotation()` 采用后立刻重算同一个 code ——
达标自动 `resolved`，不达标保持 `addressed` 并交回越线证据。

## 判据（会红）

1. 改完了、这一段的 `dialogue_run` 不再命中 → `resolved:true` 且批注状态真变 `resolved`；
2. **没改好就不许自动收口** → 仍命中时 `resolved:false`、状态仍是 `addressed`、证据含"仍越线"
   （把实现改成"恒 resolved"这一条立刻红）；
3. 只判**本条批注所在段**：同章别处仍越线不影响本条结论（`remaining>0` 但 resolved）；
4. 手写批注（note 不是 `[code] …`）**不动** —— 达标与否是作者的判断；
5. 没有候选 cid 关联时返回 None（不猜、不全局收口）。

用法：python tests/unit/test_paragraph_apply_recheck.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import annotations as ann          # noqa: E402
from utils import prose_metrics as pm         # noqa: E402
from nf_api_domains import refine as dom       # noqa: E402

PASS, FAIL = [], []
CH = 3

#: 三段连排对白（连续 ≥3 段以引号开头 = 一处 dialogue_run，段号 0 起）
BAD = """## 第3章 雨夜

“你来做什么？”

“看看你。”

“有什么好看的？”

窗外的水线连成了串，屋檐下积着一小滩暗色的水。
"""

#: 同一段改好：对白只剩一段，且并进叙述（不再连排）
GOOD = """## 第3章 雨夜

“你来做什么？”他站在门口，没有进来的意思，伞上的水还在往下滴。

窗外的水线连成了串，屋檐下积着一小滩暗色的水，她没起身，只把杯子往桌里推了推。
"""

#: 段落全改掉（引文已不在正文里）
GONE = """## 第3章 雨夜

雨敲在窗上。她把杯子往桌里推了推，杯底和木头蹭出一声，屋里静得能听见。

他站在门口，伞上的水顺着门缝淌进来，在地板上积了一小片深色。
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def write_chapter(root, text):
    p = Path(root) / "data" / "chapters" / "refined" / ("%02d.md" % CH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def fresh(text):
    tmp = Path(tempfile.mkdtemp(prefix="recheck_"))
    write_chapter(tmp, text)
    return tmp


def metric_annotation(tmp, text):
    """造一条真批注（走生产同一条 `to_annotations`）。返回 (aid, item)。"""
    made = pm.to_annotations(CH, text, root=str(tmp), codes=["dialogue_run"])
    if not made:
        raise RuntimeError("没造出 dialogue_run 批注（夹具失效）")
    return made[0]["id"], made[0]


def main():
    print("=" * 62)
    print("  采用后重算批注指标（P1-A3）")
    print("=" * 62)

    print("\n[0] 夹具自证：BAD 里确实有一处 dialogue_run，GOOD/GONE 里没有")
    bad_hits = [h for h in pm.locate(BAD) if h["code"] == "dialogue_run"]
    good_hits = [h for h in pm.locate(GOOD) if h["code"] == "dialogue_run"]
    gone_hits = [h for h in pm.locate(GONE) if h["code"] == "dialogue_run"]
    check("0 BAD 命中 1 处连排对白", len(bad_hits) == 1, bad_hits)
    check("0 GOOD 已回到线内", not good_hits, good_hits)
    check("0 GONE 也已回到线内", not gone_hits, gone_hits)

    print("\n[1] 改好了 → 自动 resolved（真写进批注库）")
    tmp = fresh(BAD)
    aid, item = metric_annotation(tmp, BAD)
    check("1 批注是 open 且 note 带指标 code",
          item["code"] == "dialogue_run", item)
    ann.mark_addressed(tmp, CH, aid, cids=["c1"])
    write_chapter(tmp, GOOD)
    res = dom.recheck_metric_annotation(str(tmp), CH, "c1")
    check("1 重算返回 resolved:true", bool(res) and res.get("resolved") is True, res)
    check("1 证据说清回到线内", "回到线内" in (res or {}).get("evidence", ""), res)
    now = {a["id"]: a for a in ann.list_for_chapter(tmp, CH).get("annotations") or []}
    check("1 批注状态真变 resolved", (now.get(aid) or {}).get("status") == "resolved",
          (now.get(aid) or {}).get("status"))
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[2] 没改好 → 不许自动收口（反例：改成恒 resolved 这里就红）")
    tmp = fresh(BAD)
    aid, _ = metric_annotation(tmp, BAD)
    ann.mark_addressed(tmp, CH, aid, cids=["c2"])
    res = dom.recheck_metric_annotation(str(tmp), CH, "c2")     # 正文一个字没改
    check("2 resolved:false", bool(res) and res.get("resolved") is False, res)
    check("2 证据说清仍越线", "仍越线" in (res or {}).get("evidence", ""), res)
    check("2 remaining 给出章内仍在的处数", (res or {}).get("remaining") == 1, res)
    now = {a["id"]: a for a in ann.list_for_chapter(tmp, CH).get("annotations") or []}
    check("2 批注仍停在 addressed（没有替作者拍板）",
          (now.get(aid) or {}).get("status") == "addressed",
          (now.get(aid) or {}).get("status"))
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[3] 原句已不在 → 判达标（引文消失 = 这一段被重写了）")
    tmp = fresh(BAD)
    aid, _ = metric_annotation(tmp, BAD)
    ann.mark_addressed(tmp, CH, aid, cids=["c3"])
    write_chapter(tmp, GONE)
    res = dom.recheck_metric_annotation(str(tmp), CH, "c3")
    check("3 resolved:true", bool(res) and res.get("resolved") is True, res)
    check("3 证据说明原句已不在", "原句已不在" in (res or {}).get("evidence", ""), res)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[4] 手写批注不动（达标与否是作者的判断）")
    tmp = fresh(BAD)
    text = (tmp / "data/chapters/refined" / ("%02d.md" % CH)).read_text(encoding="utf-8")
    paras = ann.split_paragraphs(text)
    hand = ann.add(tmp, CH, "这段读着别扭，作者觉得该重写", 1, paras)
    ann.mark_addressed(tmp, CH, hand["id"], cids=["c4"])
    res = dom.recheck_metric_annotation(str(tmp), CH, "c4")
    check("4 手写批注返回 None（不机械判）", res is None, res)
    now = {a["id"]: a for a in ann.list_for_chapter(tmp, CH).get("annotations") or []}
    check("4 手写批注状态原样", (now.get(hand["id"]) or {}).get("status") == "addressed",
          (now.get(hand["id"]) or {}).get("status"))
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[5] 没有 cid 关联 → None（不猜、不全局收口）")
    tmp = fresh(BAD)
    metric_annotation(tmp, BAD)
    res = dom.recheck_metric_annotation(str(tmp), CH, "不存在的cid")
    check("5 返回 None", res is None, res)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[6] 只判本条所在段：别处仍越线也算达标")
    tmp = fresh(BAD)
    # 两处连排：在 BAD 后面再接一组三段对白
    two = BAD + """
“你先坐。”

“不坐了。”

“随你。”
"""
    write_chapter(tmp, two)
    made = pm.to_annotations(CH, two, root=str(tmp), codes=["dialogue_run"])
    check("6 夹具：两处连排", len(made) == 2, [m["para_index"] for m in made])
    if len(made) == 2:
        first = made[0]
        ann.mark_addressed(tmp, CH, first["id"], cids=["c6"])
        # 只改第一处（把前三段对白并成叙述），第二处原样留着
        write_chapter(tmp, GOOD + """
“你先坐。”

“不坐了。”

“随你。”
""")
        res = dom.recheck_metric_annotation(str(tmp), CH, "c6")
        check("6 本条达标 → resolved", (res or {}).get("resolved") is True, res)
        check("6 但章内仍有越线（remaining>0）", ((res or {}).get("remaining") or 0) > 0, res)
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
