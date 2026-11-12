# -*- coding: utf-8 -*-
"""“要你定的事”：判据给依据，作者给结论（问题总账第 6 条，2026-11-12）。

## 要治的病

用户 2026-11-11 的判词：判据输出只有结论，没有"要问什么 / 选项 / 默认"。
界面因此只能显示红绿，人没有作答位。

## 一个通道，不是两个

这一层并进了 2026-10-31 的 Agent 提问通道（`data/state/questions.json`，
`POST /ask` / `GET /ask/pending` / `GET /ask/answers` / `POST /ask/answer`）。
判据生成的问题与 agent 问的问题走同一条链路 —— 两套写点写同一个文件，
最后一定是老的没人答、新的没人看。

## 判据（每条都会失败）

A. 同一条问题重复登记 → 只有 1 条（不刷屏）
B. 题干 / 选项变了 → 旧答案作废并重开
C. 选项校验：value 不在本题 → 拒绝并列出可选；未知 id → 拒绝
D. 答过的从待答清单消失，`/ask/answers` 仍看得到（审计不丢）
E. `effect=rewrite_chapter` + 选 rewrite → 该章进待重写清单；改选保留 → 出队
F. 阶段 4 真的会重出这一章（答了要能发生事，不是记一笔）
G. 结构指标越线 → 生成可作答的问题（有依据 / 选项 / 默认）；没越线不生成
H. 老通道的字符串选项 + 自由回答仍然能用（不破坏 Agent 提问）
I. 坏账本（不是 JSON / 不是 list）不炸

零 LLM、零费用，全部在临时目录里造。
用法：python tests/unit/test_ask.py
"""
import io
import json
import os
import shutil
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
                           ("  → " + str(detail)[:220]) if detail else ""))


Q = {
    "id": "t:one",
    "stage": 4,
    "chapter": 3,
    "question": "第 3 章 对话段占比 0.52 超过上限 0.35 —— 这一章怎么办？",
    "context": "依据：对话段占比 0.52（上限 0.35）",
    "options": [
        {"value": "rewrite", "label": "重写这一章", "note": "进待重写清单"},
        {"value": "keep", "label": "保留不改", "note": ""},
    ],
    "default": "keep",
    "effect": "rewrite_chapter",
    "source": "structure",
}


def main():
    from utils import ask

    root = Path(tempfile.mkdtemp(prefix="nf_ask_"))

    print("【A】同一条问题重复登记 → 只有 1 条")
    qid1, ch1 = ask.upsert(root, Q)
    qid2, ch2 = ask.upsert(root, dict(Q))
    check("同一个 id", qid1 == qid2 == "t:one", (qid1, qid2))
    check("第二次不算新增", ch1 is True and ch2 is False, (ch1, ch2))
    check("清单里 1 条", len(ask.open_items(root)) == 1, len(ask.open_items(root)))
    check("落盘形状是老通道的 {\"items\": [...]}",
          isinstance(json.loads(ask.state_path(root).read_text(encoding="utf-8")).get("items"), list))

    print("\n【B】题干变了 → 旧答案作废并重开")
    ok, _msg, _item = ask.answer(root, "t:one", value="keep", by="unit-test")
    check("先答一次", ok is True)
    check("答过就不在待答清单里", ask.open_items(root) == [])
    check("/ask/answers 看得到（审计不丢）",
          any(i["id"] == "t:one" and i["status"] == "answered"
              for i in ask.answered_items(root)))
    q2 = dict(Q)
    q2["question"] = "第 3 章 对白里几乎没有问句 —— 这一章怎么办？"
    _qid, ch3 = ask.upsert(root, q2)
    reopened = [i for i in ask.open_items(root) if i["id"] == "t:one"]
    check("内容变了判为新增", ch3 is True, ch3)
    check("重新出现在待答清单里", len(reopened) == 1, reopened)
    check("旧答案已清空", reopened and reopened[0]["answer"] is None,
          reopened[0].get("answer") if reopened else None)

    print("\n【C】作答校验")
    ok, msg, _ = ask.answer(root, "t:one", value="maybe", by="unit-test")
    check("选项不存在 → 拒绝且列出可选",
          ok is False and "rewrite" in msg, msg)
    ok, msg, _ = ask.answer(root, "no:such", value="keep", by="unit-test")
    check("没有这条问题 → 拒绝", ok is False and "没有这条" in msg, msg)

    print("\n【D】作答后从待答清单消失")
    ok, msg, _ = ask.answer(root, "t:one", value="rewrite", by="unit-test")
    check("作答成功", ok is True, msg)
    check("待答清单为空", ask.open_items(root) == [])
    check("回答内容记成 value", ask.find(root, "t:one")["answer"] == "rewrite",
          ask.find(root, "t:one")["answer"])

    print("\n【E】effect=rewrite_chapter 真的会进重写队列")
    check("选了重写 → 第 3 章在队列里", ask.queue_rewrites(root) == [3],
          ask.queue_rewrites(root))
    ok, _msg, _ = ask.answer(root, "t:one", value="keep", by="unit-test")
    check("改主意（带 effect 的题允许改）", ok is True)
    check("改选保留 → 出队且文件被删",
          ask.queue_rewrites(root) == [] and not ask.queue_path(root).exists(),
          ask.queue_rewrites(root))
    ask.upsert(root, Q)
    ask.answer(root, "t:one", value="rewrite", by="unit-test")
    ask.clear_rewrites(root, [3])
    check("合成后出队（clear_rewrites）", ask.queue_rewrites(root) == [])

    print("\n【F】答“重写”之后阶段 4 真会重出这一章")
    ws = Path(tempfile.mkdtemp(prefix="nf_ask_ws_"))
    (ws / "config").mkdir(parents=True)
    (ws / "config" / "system.yaml").write_text(
        "engine: self\nchapter:\n  target_words: [150, 9000]\n", encoding="utf-8")
    (ws / "config" / "project.yaml").write_text(
        "book:\n  name: 闭环夹具\n  genre: 悬疑\n  chapters: 1\n", encoding="utf-8")
    _sents = [
        "雨从傍晚开始落，瓦沟里的水一股股往下淌。",
        "阿禾把最后一格抽屉拉开，木板发出干涩的响。",
        "里面空着，只剩一层薄灰，灰上有两道指印。",
        "掌柜在柜台后翻账本，算盘珠子偶尔碰出一点声。",
        "灯芯爆了响，影子在墙上晃了一下又稳住。",
        "阿禾想起父亲临走那夜也这样拨过灯。",
        "他把指印描在纸上，又觉着不像自己的手。",
        "巷口的狗叫了两声，随后安静下来。",
        "后院的木门被风推了一下，闩子跟着响。",
        "掌柜终于抬头，问他找到了没有。",
        "阿禾把抽屉推回去，说还差一格没看。",
        "掌柜没有再问，低头继续记账。",
    ]
    body = ("## 第1章 暗格\n\n"
            + "\n\n".join(_sents[:4]) + "\n\n" + "\n\n".join(_sents[4:8])
            + "\n\n" + "\n\n".join(_sents[8:])
            + "\n\n<!-- summary: 找到暗格。 -->\n<!-- quality: 7 -->\n")
    (ws / "data" / "chapters" / "raw").mkdir(parents=True)
    (ws / "data" / "chapters" / "raw" / "01.md").write_text(body, encoding="utf-8")
    cwd = os.getcwd()
    os.chdir(ws)
    try:
        import stage4_writing as s4
        from utils.config_io import load_pipeline_config
        cfg, proj = load_pipeline_config()
        _t0, before = s4._self_pending(cfg, proj)
        check("正文本身合格（没有任何重写要求）", before == [], before)
        ask.add_rewrite(ws, 1, reason="作者选了重写")
        total, pending = s4._self_pending(cfg, proj)
        check("作者点了重写 → 排进待写", 1 in pending, (total, pending))
        ask.clear_rewrites(ws, [1])
        _total, pending2 = s4._self_pending(cfg, proj)
        check("出队后就不再被当待写（不会每轮重派）", 1 not in pending2, pending2)
    finally:
        os.chdir(cwd)
        shutil.rmtree(ws, ignore_errors=True)

    print("\n【G】结构指标越线 → 生成可作答的问题")
    result = {"ok": True, "chapters": [
        {"chapter": 1, "triggered": [], "over_dialogue_max": False},
        {"chapter": 2, "triggered": [
            {"code": "question_ratio", "label": "问号占对白",
             "value": 0.03, "limit": 0.05, "direction": "min", "hint": ""}],
         "over_dialogue_max": True,
         "metrics": {"dialogue_cover": 0.52}, "dialogue_max": 0.35},
    ]}
    added, skipped = ask.collect_structure_questions(root, result)
    check("只给越线的章生成问题（1 条）", added == 1, (added, skipped))
    items = [i for i in ask.open_items(root) if i.get("source") == "structure"]
    check("问题里写明依据（含阈值）",
          items and "问号占对白" in items[0]["context"] and "0.05" in items[0]["context"],
          items[0]["context"] if items else None)
    check("有选项、有默认",
          items and len(items[0]["options"]) >= 2
          and items[0]["default"] in [o["value"] for o in items[0]["options"]],
          items[0].get("default") if items else None)
    added2, _ = ask.collect_structure_questions(root, result)
    check("再跑一次不重复生成", added2 == 0, added2)

    print("\n【H】老通道：字符串选项 + 自由回答仍然能用")
    legacy = Path(tempfile.mkdtemp(prefix="nf_ask_legacy_"))
    qid, ch = ask.upsert(legacy, {"question": "第 3 章让谁先动手？",
                                  "options": ["沈砚", "老周"],
                                  "context": "两种走向后面十章完全不同"})
    item = ask.find(legacy, qid)
    check("字符串选项原样保存（老界面照旧显示）",
          item.get("options") == ["沈砚", "老周"], item.get("options"))
    ok, msg, _ = ask.answer(legacy, qid, answer_text="老周")
    check("自由回答能记下", ok is True and ask.find(legacy, qid)["answer"] == "老周", msg)
    ok, msg, _ = ask.answer(legacy, qid, answer_text="又改主意了")
    check("纯提问不能改（一次性）", ok is False and "已经回答" in msg, msg)

    print("\n【I】坏账本不炸")
    ask.state_path(root).write_text("{ 不是 JSON", encoding="utf-8")
    try:
        n = len(ask.open_items(root))
        err = None
    except Exception as e:                                    # noqa: BLE001
        n, err = -1, "%s: %s" % (type(e).__name__, e)
    check("坏 JSON → 当空清单，不抛", err is None and n == 0, (n, err))

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
