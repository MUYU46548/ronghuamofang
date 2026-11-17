# -*- coding: utf-8 -*-
"""讨论的动作建议 / 锚点 / 长度治理 / 记账（C5+C14+C11+C10，2026-11-16）。

## 为什么这四条要一起测

它们是**同一条链**的四段，任何一段断了，"AI 说改哪里"就还是空话：

    AI 说 action（C2 的结构化产物）
      → 落成建议（C5，本文件 Q1）
      → 段落锚点能不能扛住正文变动（C14，Q2/Q3）
      → 讨论越长越贵，老轮次压成摘要（C11，Q4）
      → 花了多少钱看得见（C10，Q7）

## 判据（每条都能变红）

- Q1  action≠none → 落一条建议；同轮同 kind 重跑不翻倍；`none` 一条都不落
- Q1b 模型给错的段号以**正文为准**纠正（quote 命中哪段就是哪段，带 para_hash）
- Q2  quote 还在但段号变了 → ok + drift（能定位、并说明挪了）
- Q3  quote 被改掉、para_hash 也对不上 → **不许猜**，ok=False
- Q4  压缩只动 session：md 逐字节不变、archived_rounds 记全、摘要标"确定性"
- Q5  `.actions.json` 不被当成"一篇讨论"（讨论列表不多出空条目）
- Q6  建议清单能按状态筛（pending / dismissed）
- Q7  讨论轮的花费进 logs/runs.db，stage=discuss
- Q8  run_turn 全程：建议 + 记账 + ai.json 都在（不是只写了讨论稿）

用法：python tests/unit/test_discuss_actions.py
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
from utils import discuss_actions, discuss_ai, discussion    # noqa: E402
from nf_api_domains import discuss_thread as dom             # noqa: E402

PASS, FAIL = [], []
TOPIC = "第十二段的冲突"
CH = 3
P1 = "他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。"
P2 = "她把伞收在门边，没有看他，只说了一句：你回来得比信上写的晚了三天。"
BODY = "第 03 章 雨夜\n\n" + P1 + "\n\n" + P2 + "\n"


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def prep():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw" / "03.md").write_text(BODY, encoding="utf-8")
    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    for name in ("system.yaml", "project.yaml"):
        src = ROOT / "config" / name
        if src.exists():
            (tmp / "config" / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            # CI 干净 checkout 没有 project.yaml（每本书一份的用户资产）
            (tmp / "config" / name).write_text("book:\n  name: 测试书\n  chapters: 10\n",
                                               encoding="utf-8")
    return tmp


def turn_payload(kind="refine_paragraph", target=None, round_no=1):
    return {"round": round_no, "text": "这一段该收住。", "stance": "push",
            "evidence": [{"kind": "quote", "quote": P1, "why": "开场"}],
            "decision": "", "needs_author": True,
            "action": {"kind": kind, "target": target or {
                "chapter": CH, "para_index": 0, "quote": P1}, "why": "把情绪压回场面里"}}


class StubClient:
    """没有流式接口的客户端 —— 走 discuss_ai.call_model 的整块回放分支。"""
    provider = "tokenhub"

    def __init__(self, payload):
        self.payload = payload

    def _post_chat(self, messages, max_tokens=None, **_kw):
        return (json.dumps(self.payload, ensure_ascii=False),
                {"prompt_tokens": 1200, "completion_tokens": 260,
                 "prompt_tokens_details": {"cached_tokens": 900}},
                "deepseek-v4-flash", "stop")


def main():
    print("=" * 66)
    print("讨论建议 / 锚点 / 压缩 / 记账（C5+C14+C11+C10）")
    tmp = prep()
    orig_root = nf_api.ROOT
    nf_api.ROOT = tmp
    try:
        discussion.open_thread(CH, TOPIC, "第 3 段要不要收住", root=str(tmp))
        md, js = discussion.thread_paths(CH, TOPIC, str(tmp))

        print("\n【1】action 落成建议（C5）")
        got = discuss_actions.record_suggestion(CH, TOPIC, turn_payload(), root=str(tmp))
        check("Q1 存下一条建议", bool(got) and got.get("kind") == "refine_paragraph", got)
        check("Q1 状态是 pending", (got or {}).get("status") == "pending", got)
        check("Q1 带 why", "压回场面" in str((got or {}).get("why")), got)
        again = discuss_actions.record_suggestion(CH, TOPIC, turn_payload(), root=str(tmp))
        check("Q1 同轮同 kind 重跑不翻倍", again is None, again)
        none_payload = turn_payload()
        none_payload["action"] = {"kind": "none", "target": {}, "why": ""}
        none_payload["round"] = 2
        check("Q1 action=none 不落建议",
              discuss_actions.record_suggestion(CH, TOPIC, none_payload, root=str(tmp)) is None)

        print("\n【1b】段号以正文为准（C14）")
        wrong = turn_payload(round_no=3, target={"chapter": CH, "para_index": 9, "quote": P1})
        got2 = discuss_actions.record_suggestion(CH, TOPIC, wrong, root=str(tmp))
        tgt = (got2 or {}).get("target") or {}
        # 切分口径与段落精修同一份：本章第 0 段是标题行，正文第一段是 1
        check("Q1b 段号被纠正成 1", tgt.get("para_index") == 1, tgt)
        check("Q1b 补上了 para_hash", len(str(tgt.get("para_hash") or "")) == discuss_actions.HASH_LEN,
              tgt)
        check("Q1b 留了原文 quote", str(tgt.get("quote") or "").startswith("他推门进来"), tgt)

        print("\n【2】quote 还在、段号变了 → drift")
        anc = discuss_actions.resolve_anchor(CH, {"para_index": 0, "quote": P1}, str(tmp))
        check("Q2 能定位", anc.get("ok") is True, anc)
        check("Q2 段号按原文纠正成 1", anc.get("index") == 1, anc)
        check("Q2 标了 drift", anc.get("drift") is True, anc)

        print("\n【3】原句被改掉 → 不许猜")
        gone = discuss_actions.resolve_anchor(
            CH, {"para_index": 0, "quote": "他把伞靠在门边，什么也没说。",
                 "para_hash": discuss_actions.para_hash("完全不是这一段")}, str(tmp))
        check("Q3 拒绝定位", gone.get("ok") is False, gone)
        check("Q3 说清为什么", "不在原位" in str(gone.get("why")), gone)

        print("\n【4】压缩老轮次（C11）")
        for i in range(2, 7):
            discussion.append_round(CH, TOPIC, "author" if i % 2 else "ai",
                                    "第 %d 轮的话：这里在讨论节奏。" % i, root=str(tmp))
        before_md = md.read_bytes()
        before_state = json.loads(js.read_text(encoding="utf-8"))
        res = discussion.compress_session(CH, TOPIC, root=str(tmp), keep=2, threshold=4)
        sess = discussion.load_session(CH, TOPIC, str(tmp))
        check("Q4 真压了", bool(res) and res.get("kept") == 2, res)
        check("Q4 archived_rounds 记全", sess.get("archived_rounds") == [1, 2, 3], sess.get("archived_rounds"))
        check("Q4 摘要标明是确定性摘要", "确定性摘要" in str(sess.get("summary")), sess.get("summary"))
        check("Q4 讨论稿 md 一个字节没动", md.read_bytes() == before_md)
        check("Q4 机读状态 json 没被压", json.loads(js.read_text(encoding="utf-8")) == before_state)
        check("Q4 压缩事件留痕", bool(sess.get("compressed_at")) and sess.get("compression_count") == 1, sess)
        check("Q4 再压一次没东西可压",
              discussion.compress_session(CH, TOPIC, root=str(tmp), keep=2, threshold=4) is None)

        print("\n【4b】压缩后的请求里带摘要")
        msgs, meta = discuss_ai.build_messages(CH, TOPIC, "", str(tmp))
        joined = "\n".join(str(m.get("content") or "") for m in msgs)
        check("Q4b system 段带摘要锚点", "更早轮次的摘要" in joined)
        check("Q4b 摘要里能看到被压掉的轮次", "第 2 轮" in joined)

        print("\n【5】派生文件不算一篇讨论")
        topics = [t.get("topic") for t in discussion.list_threads(CH, str(tmp))]
        check("Q5 讨论列表没有多出空条目", topics == [TOPIC], topics)

        print("\n【6】建议清单与状态")
        pend = discuss_actions.list_suggestions(CH, str(tmp), status="pending")
        check("Q6 pending 有 2 条", len(pend) == 2, pend)
        sid = pend[0]["id"]
        discuss_actions.set_status(CH, TOPIC, sid, "dismissed", str(tmp))
        check("Q6 放弃后不在 pending 里",
              all(x["id"] != sid for x in discuss_actions.list_suggestions(CH, str(tmp), status="pending")))
        check("Q6 dismissed 能查到",
              any(x["id"] == sid for x in discuss_actions.list_suggestions(CH, str(tmp), status="dismissed")))

        print("\n【6b】建议落成批注（讨论 P0，2026-11-17）")
        left = discuss_actions.list_suggestions(CH, str(tmp), status="pending")
        target = left[0]
        okA, msgA, itemA, ancA = discuss_actions.annotate_suggestion(
            CH, TOPIC, target["id"], str(tmp))
        check("Q10 落成批注成功", bool(okA and itemA), msgA)
        check("Q10 批注写明来源（讨论 + 轮次）",
              "来源：讨论《" in str((itemA or {}).get("note")), (itemA or {}).get("note"))
        check("Q10 批注和锚点同一层（本章只有 raw）", (itemA or {}).get("layer") == "raw", itemA)
        check("Q10 段号就是引文所在段",
              int((itemA or {}).get("para_index", -1)) == int(ancA.get("index")), (itemA, ancA))
        ann_file = tmp / "data" / "state" / "annotations" / "ch03.json"
        items = json.loads(ann_file.read_text(encoding="utf-8"))["annotations"]
        check("Q10 批注真的落盘", len(items) == 1, items)
        after = discuss_actions.get_suggestion(CH, TOPIC, target["id"], str(tmp))
        check("Q10 建议状态变成 annotated 并记下批注 id",
              after and after.get("status") == "annotated"
              and after.get("annotation_id") == (itemA or {}).get("id"), after)
        ok2, msg2, _i2, _a2 = discuss_actions.annotate_suggestion(
            CH, TOPIC, target["id"], str(tmp))
        check("Q10 同一条建议不许落两次", (not ok2) and "已经落成批注" in msg2, msg2)

        dismissed_id = sid                       # 【6】里放弃的那条
        ok3, msg3, _i3, _a3 = discuss_actions.annotate_suggestion(
            CH, TOPIC, dismissed_id, str(tmp))
        check("Q10 已放弃的建议不能落批注", (not ok3) and "放弃" in msg3, msg3)

        bad = turn_payload(round_no=9, target={"chapter": CH, "para_index": 0,
                                               "quote": "这句根本不在正文里，别猜段号。"})
        sug_bad = discuss_actions.record_suggestion(CH, TOPIC, bad, root=str(tmp))
        ok4, msg4, _i4, _a4 = discuss_actions.annotate_suggestion(
            CH, TOPIC, sug_bad["id"], str(tmp))
        check("Q10 引文不在原位 → 拒绝落批注", (not ok4) and "不在原位" in msg4, msg4)
        items2 = json.loads(ann_file.read_text(encoding="utf-8"))["annotations"]
        check("Q10 拒绝时没有多出批注（不猜段号）", len(items2) == 1, items2)
        ok5, msg5, _i5, _a5 = discuss_actions.annotate_suggestion(CH, TOPIC, "act_nope", str(tmp))
        check("Q10 不存在的建议 → 找不到", (not ok5) and "找不到" in msg5, msg5)

        print("\n【7】讨论轮记账（C10）")
        spent = discuss_ai.record_cost(CH, {"prompt_tokens": 1200, "completion_tokens": 260,
                                            "prompt_tokens_details": {"cached_tokens": 900}},
                                       model="deepseek-v4-flash", provider="tokenhub", root=str(tmp))
        from utils.db import RunDB
        db = RunDB(str(tmp / "logs" / "runs.db"))
        try:
            rows = db.conn.execute(
                "SELECT run_id, stage, chapter, tokens_in, tokens_out, cache_read "
                "FROM cost_log WHERE stage='discuss'").fetchall()
        finally:
            db.close()
        check("Q7 落了一行 discuss 记账", len(rows) == 1, rows)
        check("Q7 run_id/stage/chapter 对得上",
              rows and rows[0][0] == "discuss" and rows[0][2] == CH, rows)
        check("Q7 token 与缓存都记了", rows and rows[0][3] == 1200 and rows[0][5] == 900, rows)
        check("Q7 返回了金额（hermes 之外按量估算）", isinstance(spent, float) and spent > 0, spent)

        print("\n【8】run_turn 全程：建议 + 记账 + 结构化产物")
        payload = turn_payload(kind="rewrite_chapter", round_no=8,
                               target={"chapter": CH, "para_index": 0, "quote": P2})
        res = discuss_ai.run_turn(StubClient(payload), CH, TOPIC, "", root=str(tmp),
                                  cfg={"discuss": {"session_keep": 12, "compress_after": 99}})
        # round 由服务端按讨论稿回填，不采信模型自报（契约硬要求）——
        # 这里已有 5 轮（1–5），所以这一轮是 6，而不是模型写的 8
        check("Q8 讨论稿多了一轮（轮号由服务端回填）", (res or {}).get("round") == 6, res)
        check("Q8 建议是整章重写", ((res or {}).get("suggestion") or {}).get("kind") == "rewrite_chapter", res)
        check("Q8 建议锚点按原文指到第 3 段（含标题行）",
              (((res or {}).get("suggestion") or {}).get("target") or {}).get("para_index") == 2, res)
        check("Q8 记账有回值", isinstance((res or {}).get("cost_yuan"), float), res)
        aij = discussion.ai_payload_path(CH, TOPIC, str(tmp))
        check("Q8 ai.json 落盘且带 round", aij.exists()
              and json.loads(aij.read_text(encoding="utf-8")).get("round") == 6)
        check("Q8 压缩阈值没到就不压", (res or {}).get("compressed") is None, res)
        print("")
        print("【9】路由真的接上（ROUTES + 分发器）")
        # 只登记 ROUTES 不写分发器 = 真机 404（`/config/enhanced_mode` 就那样躺在
        # ROUTES 里过了一版）。两个都要查，否则界面上点了没反应还没人知道。
        api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        pairs = [("GET", "/discuss/actions", "handle_discuss_actions"),
                 ("POST", "/discuss/actions/apply", "handle_discuss_action_apply"),
                 ("POST", "/discuss/actions/annotate", "handle_discuss_action_annotate"),
                 ("POST", "/discuss/actions/dismiss", "handle_discuss_action_dismiss"),
                 ("POST", "/discuss/compress", "handle_discuss_compress")]
        for method, path, fn in pairs:
            check("Q9 ROUTES 有 %s %s" % (method, path),
                  any(m == method and p == path for m, p, _ in dom.ROUTES))
            check("Q9 分发器有 %s" % fn, ("dom_thread." + fn) in api_src)
        panel_src = (ROOT / "console" / "src" / "DiscussThreadPanel.vue").read_text(encoding="utf-8")
        check("Q9 界面有“落成批注”入口（不是只在后端）",
              "落成批注" in panel_src and "/discuss/actions/annotate" in panel_src)
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
