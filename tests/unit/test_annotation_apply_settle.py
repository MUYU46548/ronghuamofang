# -*- coding: utf-8 -*-
"""采用候选之后，**手写批注**也要能收口（2026-11-20）—— 零 LLM、零网络。

## 为什么

`docs/读改链走查-20261117.md` 的洞 A 只补了一半：`recheck_metric_annotation` 处理
指标批注（`[code] 连排对白 3 处` 这种），达标自动 resolved、不达标留 addressed。
**手写批注**（作者自己写的一句意见）被刻意放过 —— “写得好不好”确实不能机械判定。
但放过之后它永远停在 `addressed`，界面按 `status != resolved` 归到“未收口”，
于是作者每改完一条还得回去手点一次；更糟的是 `addressed` 状态**连“标为已处理”按钮都没有**，
只剩删了重记这一条路。

这里补的**不是判定**，是“作者已经采纳了这一版”这个事实：
收口依据写 `resolved_by="apply"`（不冒充“达标”），并且可以重新打开。

## 判据（会红）

1. 手写批注挂了候选 cid，作者采用 → `resolved`，`resolved_by == "apply"`；
   （把实现改回“手写一律不收口”→ 第 1 组立刻红）
2. 指标批注不归这里管 → 返回 None、状态原样（别抢 recheck 的活）；
3. cid 没挂在任何批注上 → 返回 None，同章别的批注状态**一个都不许动**（不全局收口）；
4. 重开 → 回到 `open`，`resolved_by` 被清掉（否则界面还显示“采用候选时收口”）；
5. 作者手点收口 → `resolved_by == "author"`，与自动收口能分开；
6. 接线：`handle_paragraph_apply` 在 `recheck` 为空时调 settle，且回执带上 `settled`；
   `POST /chapters/annotations` 认识 `action="reopen"`；
7. 前端：已收口批注有“重新打开”按钮，`addressed` 也有“标为已处理”（死结不能留着）。

用法：python tests/unit/test_annotation_apply_settle.py
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

TEXT = """## 第3章 雨夜

“你来做什么？”

“看看你。”

“有什么好看的？”

窗外的水线连成了串，屋檐下积着一小滩暗色的水。
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def fresh(text=TEXT):
    tmp = Path(tempfile.mkdtemp(prefix="settle_"))
    p = tmp / "data" / "chapters" / "refined" / ("%02d.md" % CH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return tmp


def paras_of(tmp):
    return ann.split_paragraphs(
        (tmp / "data/chapters/refined" / ("%02d.md" % CH)).read_text(encoding="utf-8"))


def state(tmp):
    return {a["id"]: a for a in ann.list_for_chapter(tmp, CH).get("annotations") or []}


def main():
    print("=" * 62)
    print("  采用候选后手写批注收口（2026-11-20）")
    print("=" * 62)

    print("\n[1] 手写批注 + 作者采用了它生成的候选 → 收口（依据写 apply）")
    tmp = fresh()
    hand = ann.add(tmp, CH, "这段读着别扭，收一收", 1, paras_of(tmp))
    ann.mark_addressed(tmp, CH, hand["id"], cids=["c1"])
    res = dom.settle_manual_annotation(str(tmp), CH, "c1")
    check("1 返回 resolved:true", bool(res) and res.get("resolved") is True, res)
    check("1 依据是 apply（不是“达标”）", (res or {}).get("basis") == "apply", res)
    check("1 证据说清是按“你的采用”收口、且可重开",
          "采用" in (res or {}).get("evidence", "")
          and "重新打开" in (res or {}).get("evidence", ""), res)
    now = state(tmp)
    check("1 批注状态真变 resolved",
          (now.get(hand["id"]) or {}).get("status") == "resolved",
          (now.get(hand["id"]) or {}).get("status"))
    check("1 resolved_by 落盘为 apply",
          (now.get(hand["id"]) or {}).get("resolved_by") == "apply",
          (now.get(hand["id"]) or {}).get("resolved_by"))
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[2] 指标批注不归这里管（交给 recheck，别抢）")
    tmp = fresh()
    made = pm.to_annotations(CH, TEXT, root=str(tmp), codes=["dialogue_run"])
    check("2 夹具：造出一条指标批注", bool(made), made)
    if made:
        mid = made[0]["id"]
        ann.mark_addressed(tmp, CH, mid, cids=["c2"])
        res = dom.settle_manual_annotation(str(tmp), CH, "c2")
        check("2 指标批注返回 None", res is None, res)
        check("2 状态原样停在 addressed（等 recheck 判）",
              (state(tmp).get(mid) or {}).get("status") == "addressed",
              (state(tmp).get(mid) or {}).get("status"))
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[3] cid 没挂批注 → 不动任何一条（不全局收口）")
    tmp = fresh()
    a1 = ann.add(tmp, CH, "第一条意见", 1, paras_of(tmp))
    a2 = ann.add(tmp, CH, "第二条意见", 2, paras_of(tmp))
    for a in (a1, a2):
        ann.mark_addressed(tmp, CH, a["id"], cids=["other"])
    res = dom.settle_manual_annotation(str(tmp), CH, "no-such-cid")
    check("3 返回 None", res is None, res)
    now = state(tmp)
    check("3 两条都还停在 addressed",
          all((now.get(a["id"]) or {}).get("status") == "addressed" for a in (a1, a2)),
          [(now.get(a["id"]) or {}).get("status") for a in (a1, a2)])
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[4] 重开：resolved → open，resolved_by 清掉")
    tmp = fresh()
    hand = ann.add(tmp, CH, "只想改一半", 1, paras_of(tmp))
    ann.mark_addressed(tmp, CH, hand["id"], cids=["c4"])
    dom.settle_manual_annotation(str(tmp), CH, "c4")
    ok = ann.reopen(tmp, CH, hand["id"])
    now = state(tmp)
    check("4 reopen 返回 True", ok is True, ok)
    check("4 状态回到 open", (now.get(hand["id"]) or {}).get("status") == "open",
          (now.get(hand["id"]) or {}).get("status"))
    check("4 resolved_by 已清除（不再显示“采用候选时收口”）",
          "resolved_by" not in (now.get(hand["id"]) or {}),
          (now.get(hand["id"]) or {}).get("resolved_by"))
    check("4 留了重开痕迹", bool((now.get(hand["id"]) or {}).get("reopened_at")),
          now.get(hand["id"]))
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[5] 作者手点收口 → resolved_by=author（与自动收口分得开）")
    tmp = fresh()
    hand = ann.add(tmp, CH, "自己看过了", 1, paras_of(tmp))
    ann.resolve(tmp, CH, hand["id"])
    check("5 默认按作者",
          (state(tmp).get(hand["id"]) or {}).get("resolved_by") == "author",
          (state(tmp).get(hand["id"]) or {}).get("resolved_by"))
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[6] 接线（源码判据：改回去就红）")
    src = (ROOT / "scripts" / "nf_api_domains" / "refine.py").read_text(encoding="utf-8")
    check("6 apply 在 recheck 为空时调 settle",
          "if recheck is None:" in src and "settle_manual_annotation(" in src)
    check("6 回执带 settled 字段", 'payload["settled"] = settled' in src)
    chap = (ROOT / "scripts" / "nf_api_domains" / "chapters.py").read_text(encoding="utf-8")
    check("6 /chapters/annotations 认识 reopen",
          '"reopen"' in chap and "ann.reopen" in chap)
    vue = (ROOT / "console" / "src" / "ChapterEditor.vue").read_text(encoding="utf-8")
    check("6 界面有“重新打开”按钮", "reopenAnnotation" in vue and "重新打开" in vue)
    check("6 addressed 也能手点收口（死结不能留）",
          "a.status === 'open' || a.status === 'addressed'" in vue)
    check("6 已收口标出“采用候选时收口”", "采用候选时收口" in vue)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
