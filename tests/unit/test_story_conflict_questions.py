# -*- coding: utf-8 -*-
"""跨章硬伤 → 可作答的问题（P1-2，2026-11-19）。

阶段 5 分批检查（每批 ≤5 章），批与批互相看不见 —— "第 2 章死了、第 12 章又出场"
这类硬伤在批内天然看不出来。一致性账本是跨章累计的，能在阶段末捞出来；
本文件钉住"捞出来之后要变成作者能作答的问题"，而不是只在控制台打一行。

| 编号 | 钉什么 |
|---|---|
| Q1 | 第 2 章写死、第 12 章又作为行动主体 → 生成 1 条问题，章号是 12 |
| Q2 | 题干/依据说清是哪两章、什么问题（不是只有一个数字） |
| Q3 | 没有冲突 → 一条都不生成 |
| Q4 | `level=warning`（疑似错名）不刷待办 |
| Q5 | 同一件事再扫一遍不新增（id 稳定） |
| Q6 | 选“重写”真的把第 12 章排进待重写队列 |
| Q7 | 没有账本 / 坏账本 → 静默 0，不炸（老工作区不受影响） |
| Q8 | 时间倒退也能生成问题（不只死亡一种） |

用法：python tests/unit/test_story_conflict_questions.py（零 LLM、零网络）
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import ask                                         # noqa: E402
from utils import story_state as ss                           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)[:220]) if extra else ""))


def _mk(root, chapters, setting=None):
    sp = root / "data" / "setting"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "setting.json").write_text(json.dumps(
        setting or {"characters": [{"name": "沈砚"}], "items": []},
        ensure_ascii=False), encoding="utf-8")
    d = root / "data" / "chapters" / "checked"
    d.mkdir(parents=True, exist_ok=True)
    for n, text in chapters.items():
        (d / ("%02d.md" % n)).write_text(text, encoding="utf-8")


SET_ARGS = {"chapters": {2: "沈砚走进盐仓。沈砚倒在地上，死了。\n",
                         12: "沈砚推开门，走了进来。\n"}}


def main():
    print("【Q1】第 2 章写死、第 12 章又出场 → 一条问题，落在第 12 章")
    r = Path(tempfile.mkdtemp(prefix="nf_story_"))
    _mk(r, SET_ARGS["chapters"])
    state = ss.rebuild(r, r / "data" / "setting" / "setting.json",
                       r / "data" / "chapters" / "checked")
    confs = [c for c in state["conflicts"] if c["level"] == "conflict"]
    check("账本判出了这条硬伤", bool(confs), state["conflicts"][:2])
    added, _ = ask.collect_story_conflict_questions(r, state)
    check("生成 1 条问题", added == 1, added)
    item = [i for i in ask.open_items(r) if i["id"] == "story:ch12"]
    check("id 落在第 12 章", bool(item), ask.open_items(r))

    print("\n【Q2】题干与依据说清“哪两章、什么问题”")
    check("题干带章号与处数", bool(item) and "第 12 章" in item[0]["question"], item and item[0]["question"])
    check("依据里有前文那一章", bool(item) and "第2章" in item[0]["context"], item and item[0]["context"])
    check("三个选项 + 默认先放着",
          bool(item) and len(item[0]["options"]) == 3 and item[0]["default"] == "later")

    print("\n【Q3】没有冲突 → 一条不生成")
    r2 = Path(tempfile.mkdtemp(prefix="nf_story_ok_"))
    _mk(r2, {1: "沈砚推开门，走了进来。\n", 2: "沈砚坐下，倒了杯水。\n"})
    st2 = ss.rebuild(r2, r2 / "data" / "setting" / "setting.json",
                     r2 / "data" / "chapters" / "checked")
    check("账本无硬伤", [c for c in st2["conflicts"] if c["level"] == "conflict"] == [],
          st2["conflicts"])
    check("生成 0 条", ask.collect_story_conflict_questions(r2, st2)[0] == 0)

    print("\n【Q4】warning 级不刷待办（疑似错名是提示，不是硬伤）")
    r3 = Path(tempfile.mkdtemp(prefix="nf_story_warn_"))
    _mk(r3, {1: "沈砚推开门。\n"}, setting={"characters": [{"name": "沈砚"},
                                                       {"name": "周砚清"}], "items": []})
    st3 = {"conflicts": [{"code": "unknown_name", "level": "warning", "chapter": 1,
                          "detail": "第1章出现“周砚青”，疑似“周砚清”的错名"}]}
    check("warning 不生成", ask.collect_story_conflict_questions(r3, st3)[0] == 0)

    print("\n【Q5】同一件事再扫一遍不新增")
    check("第二次 0 新增", ask.collect_story_conflict_questions(r, state)[0] == 0)

    print("\n【Q6】选“重写” → 第 12 章进待重写队列")
    ok, msg, _ = ask.answer(r, "story:ch12", value="rewrite", by="unit-test")
    check("作答成功", ok is True, msg)
    check("队列里有第 12 章", ask.queue_rewrites(r) == [12], ask.queue_rewrites(r))

    print("\n【Q7】没有账本 / 坏账本 → 静默 0")
    r4 = Path(tempfile.mkdtemp(prefix="nf_story_none_"))
    check("没有账本 → (0, 0)", ask.collect_story_conflict_questions(r4) == (0, 0))
    p = r4 / "data" / "state" / "story_state.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ 不是 JSON", encoding="utf-8")
    try:
        res = ask.collect_story_conflict_questions(r4)
        err = None
    except Exception as e:                                    # noqa: BLE001
        res, err = None, "%s: %s" % (type(e).__name__, e)
    check("坏 JSON 不炸", err is None and res == (0, 0), (err, res))

    print("\n【Q8】时间倒退也生成问题")
    r5 = Path(tempfile.mkdtemp(prefix="nf_story_year_"))
    _mk(r5, {1: "一九九八年冬天，沈砚推开门。\n",
             2: "一九九五年秋天，沈砚走进院子。\n"})
    st5 = ss.rebuild(r5, r5 / "data" / "setting" / "setting.json",
                     r5 / "data" / "chapters" / "checked")
    codes = {c["code"] for c in st5["conflicts"]}
    check("账本判出时间倒退", "time_regression" in codes, codes)
    added5, _ = ask.collect_story_conflict_questions(r5, st5)
    check("生成了问题", added5 == 1, added5)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
