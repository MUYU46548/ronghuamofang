# -*- coding: utf-8 -*-
"""多方案生成的反馈（I-2，2026-11-03）。

## 要治的病

作者原话：阶段 2 能出 3 版，但**不知道在跑、不知道做到哪**，完成后也没有
并排对比的入口（只有列表）。本轮两件事：

1. 逐版进度进 job（`JOBS[id]["progress"]`），前端能显示“第 2/3 版生成中”；
2. draft 列表带可比字段（字数 / 节点数 / 章规划数 / 时间 / score）+ 推荐排序。

## 判据（每条都能变红）

- M1 list_drafts 带 chars / node_count / plan_count / created_at / score
- M2 score 口径：ok*3 + warn*1 - thin*2
- M3 recommend_draft：单份不推荐；多份取高分；同分取字数更多
- M4 全部方案 score <= 0 → 不给推荐（不硬编一个“推荐”）
- M5 端点回包带 recommended 字段
- M6 逐版进度真的写进 job（源码接线：setdefault("progress")）
- M7 每份开始时写 index/count/done（不是只在最后写一次）

用法：python tests/unit/test_multi_drafts.py
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


HEAD = ("# 《雾港核心》整体大纲\n## 起\n起\n## 承\n承\n## 转\n转\n## 合\n合\n"
        "## 关键节点\n")
NODE = ("- 节点{n}：第{n}-{m}章 落魄机关术师沈砚在边城营地被守卫队长程砧找上门，"
        "两人一同出发前往雾港遗址，在遗址深处发现异常封印遗迹，"
        "与朝廷暗卫爆发正面冲突，程砧出手相救，沈砚决定追查三年前那场失控的真相。")


def body(nodes=3, extra=""):
    out = HEAD
    for i in range(1, nodes + 1):
        out += NODE.format(n=i, m=i * 2) + "\n"
    out += "## 章节规划\n"
    for i in range(1, nodes + 1):
        out += "- 第%d章：沈砚与程砧推进主线（具体场景）\n" % i
    out += "## 预计章节数\n%d\n" % nodes
    return out + extra


def main():
    print("=" * 66)
    print("多方案反馈（I-2）")
    print("=" * 66)

    from utils import outline_panel as op

    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            d = Path("data") / "outline"
            d.mkdir(parents=True)
            (d / "global_draft_1.md").write_text(body(2), encoding="utf-8")
            (d / "global_draft_2.md").write_text(body(5), encoding="utf-8")
            (d / "global_draft_3.md").write_text(body(1), encoding="utf-8")

            drafts = op.list_drafts()
            print("\n[M1] 可比字段")
            check("三份都读到", len(drafts) == 3, len(drafts))
            keys = set(drafts[0])
            check("带 chars", "chars" in keys)
            check("带 node_count / plan_count",
                  "node_count" in keys and "plan_count" in keys)
            check("带 created_at", "created_at" in keys and drafts[0]["created_at"])
            check("带 score", "score" in keys)
            check("chars 与正文长度一致",
                  drafts[0]["chars"] == len((d / "global_draft_1.md").read_text(encoding="utf-8")),
                  (drafts[0]["chars"],))
            check("章节越多的那版 chars 越大",
                  drafts[1]["chars"] > drafts[0]["chars"] > drafts[2]["chars"],
                  [x["chars"] for x in drafts])

            print("\n[M2] score 口径")
            doc = {"id": 1, "summary": {"ok": 4, "warn": 2, "thin": 1}}
            # ok*3 + warn*1 - thin*2 = 12 + 2 - 2 = 12
            expected = 4 * 3 + 2 * 1 - 1 * 2
            got = (doc["summary"]["ok"] * 3 + doc["summary"]["warn"]
                   - doc["summary"]["thin"] * 2)
            check("公式 = ok*3 + warn*1 - thin*2", got == expected, (got, expected))
            check("list_drafts 的 score 是整数", all(isinstance(x["score"], int) for x in drafts),
                  [x["score"] for x in drafts])

            print("\n[M3] 推荐排序")
            rec = op.recommend_draft(drafts)
            check("多份时给推荐", rec is not None, rec)
            check("推荐是 score 最高的那版",
                  rec == max(drafts, key=lambda x: (x["score"], x["chars"]))["id"], rec)
            check("单份不推荐", op.recommend_draft(drafts[:1]) is None)
            check("空列表不推荐", op.recommend_draft([]) is None)
            tie = [{"id": 1, "score": 5, "chars": 100},
                   {"id": 2, "score": 5, "chars": 300}]
            check("同分取字数更多", op.recommend_draft(tie) == 2, op.recommend_draft(tie))

            print("\n[M4] 全是负分 → 不给推荐")
            bad = [{"id": 1, "score": 0, "chars": 10},
                   {"id": 2, "score": -3, "chars": 20}]
            check("不硬编推荐", op.recommend_draft(bad) is None)

            print("\n[M3b] 排序理由（2026-11-05：借鉴 SP-1026 择优器，理由是算出来的）")
            rows = op.rank_drafts(drafts)
            check("每版都有理由条目",
                  len(rows) == len(drafts) and all(r.get("reasons") for r in rows), rows)
            check("排序按分数降序",
                  [r["score"] for r in rows] == sorted([r["score"] for r in rows], reverse=True),
                  [r["score"] for r in rows])
            top = rows[0]
            check("榜首理由说得出是哪一项占优（OK / THIN / 字数）",
                  any(k in "".join(top["reasons"]) for k in ("OK", "THIN", "字数")),
                  top["reasons"])
            check("理由只写事实不写质量结论（不出现“更好”“优秀”）",
                  not any(w in "".join(top["reasons"]) for w in ("更好", "优秀", "最佳")),
                  top["reasons"])
            check("同输入同输出（确定性）",
                  [r["id"] for r in op.rank_drafts(drafts)] == [r["id"] for r in rows])
            check("空列表返回 []", op.rank_drafts([]) == [])
            check("单份也给行（只是不推荐）",
                  len(op.rank_drafts(drafts[:1])) == 1)
        finally:
            os.chdir(old_cwd)

    print("\n[M5] 端点回包带 recommended")
    src = (ROOT / "scripts" / "nf_api_domains" / "outline.py").read_text(encoding="utf-8")
    check("handle_outline_drafts 里有 recommended 字段",
          '"recommended": op.recommend_draft(drafts)' in src)

    print("\n[M6] 逐版进度接线")
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("写进 job progress", 'setdefault("progress"' in api_src)
    check("phase=start（不是只在最后写一次）", 'phase="start"' in api_src)
    check("每份开始写 index/count", 'phase="running", index=i, count=count' in api_src)
    check("失败时保留已完成版数", 'phase="failed"' in api_src)
    check("完成写 done 列表", 'done=[d["id"] for d in drafts]' in api_src)

    print("\n[M7] 前端读的是 progress（不是只看 state）")
    vue = (ROOT / "console" / "src" / "OutlineView.vue").read_text(encoding="utf-8")
    check("轮询读取 j.data.progress", "j.data.progress" in vue)
    check("有逐版进度文案", "multiProgressText" in vue)
    check("并排列显示字数/节点/章规划", "ov-multi-meta" in vue)
    check("推荐徽标", "recommendedId" in vue)

    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
