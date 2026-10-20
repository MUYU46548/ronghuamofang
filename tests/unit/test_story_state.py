# -*- coding: utf-8 -*-
"""一致性账本的自检（P3）：5 类矛盾必须报出来，正常叙事一条都不许报。

## 主判据（正反两方都钉）

**必须报**（5/5，构造的 6 章文本）：
1. `dead_again`      韩拙第 2 章死、第 3 章又说"这边走"
2. `death_duplicate` 老周第 4 章、第 6 章各被写死一次
3. `item_resurrect`  怀表第 4 章扔进河里、第 5 章又握在手上
4. `time_regression` 第 1 章 2018 年、第 6 章回到一九九八年（无回忆标记）
5. `unknown_name`    第 5 章"林小曼"（设定是林小满）

**必须不报**（误报锁，每条都对应一个真实写法）：
- 回忆句里的死亡（"他想起三年前父亲死了"）
- 否定与未完成（"他没死" / "差点死了"）
- 同一章出现两个年份（"1998 年……2018 年"是正常写法）
- 物品转手后接回（"递给韩拙"→"接过怀表"）

用法：python tests/unit/test_story_state.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import story_state as S                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                          ("  <- " + str(detail)) if (detail and not cond) else ""))


CHARS = ["沈砚", "韩拙", "林小满", "老周"]
ITEMS = ["怀表", "图纸"]

BROKEN = {
    1: "2018 年冬，边城下了一场雪。沈砚在值班室掏出怀表，看了很久。",
    2: "韩拙死了。他断气的时候，窗外正在刮风。",
    3: "韩拙说：「这边走。」沈砚跟上去。",
    4: "沈砚把怀表扔进了河里。老周死了，丧事办得很简单。",
    5: "沈砚握着怀表站在桥头。林小曼说：「我不知道。」",
    6: "老周死了，这一次没有人去送葬。一九九八年冬天，边城下了第一场雪。",
}

CLEAN = {
    1: "2018 年冬，边城下了一场雪。沈砚在值班室掏出怀表，看了很久。",
    2: "韩拙说：「这边走。」沈砚跟上去。林小满在门口等着。",
    3: "沈砚把怀表递给韩拙。韩拙收下了。",
    4: "韩拙握着怀表站在桥头，风很大。",
    5: "林小满说：「我不知道。」沈砚没有说话。",
    6: "2019 年春天，雪化了。三个人一起离开了边城。",
}


def scan_all(texts):
    ev = []
    for n, t in sorted(texts.items()):
        ev.extend(S.scan_chapter(n, t, CHARS, ITEMS))
    return ev


def codes(conflicts):
    return sorted({c["code"] for c in conflicts})


def main():
    print("== 一致性账本 ==")

    # ---- 1. 五类矛盾必须全报 ----
    print("\n[1] 构造 5 个矛盾（必须 5/5）")
    conf = S.check(scan_all(BROKEN))
    got = codes(conf)
    for code in ("dead_again", "death_duplicate", "item_resurrect",
                 "time_regression", "unknown_name"):
        check("报出 %s" % code, code in got, got)
    check("冲突级别为 conflict 的至少 4 条",
          len([c for c in conf if c["level"] == "conflict"]) >= 4,
          [(c["code"], c["level"]) for c in conf])

    # 证据必须能定位到章与原文
    dead = [c for c in conf if c["code"] == "dead_again"]
    check("死而复生带两条证据", dead and len(dead[0]["evidence"]) == 2, dead[:1])
    check("死而复生标对了章号", dead and dead[0]["chapter"] == 3, dead[:1])
    check("证据里能看到原文片段", dead and "死" in dead[0]["evidence"][0], dead[:1])

    reg = [c for c in conf if c["code"] == "time_regression"]
    check("时间倒退写清两个年份", reg and "2018" in reg[0]["detail"] and "1998" in reg[0]["detail"],
          reg[:1])

    # ---- 2. 正常叙事零误报 ----
    print("\n[2] 正常叙事（必须 0 条）")
    clean_conf = S.check(scan_all(CLEAN))
    check("正常叙事 0 条冲突", [c for c in clean_conf if c["level"] == "conflict"] == [],
          [(c["code"], c["detail"]) for c in clean_conf])

    # ---- 3. 逐条误报锁 ----
    print("\n[3] 误报锁（每种真实写法各一条）")
    ev = S.scan_chapter(1, "他想起三年前，父亲死在那条巷子里。", CHARS, ITEMS)
    check("回忆里的死亡不记账", not [e for e in ev if e["type"] == "death"],
          [e["type"] for e in ev])

    ev = S.scan_chapter(2, "韩拙没有死。他差点死了，但最终活了下来。", CHARS, ITEMS)
    check("否定与「差点」不记账", not [e for e in ev if e["type"] == "death"],
          [e["type"] for e in ev])

    ev = S.scan_chapter(3, "1998 年冬天，沈砚出生。2018 年冬天，他回到边城。", CHARS, ITEMS)
    years = [e["year"] for e in ev if e["type"] == "time"]
    check("同一章只取第一个年份", years == [1998], years)

    ev = S.scan_chapter(4, "沈砚把怀表递给韩拙。", CHARS, ITEMS)
    check("转手不算丢失", not [e for e in ev if e["type"] == "lose_item"],
          [e["type"] for e in ev])
    ev2 = S.scan_chapter(5, "韩拙接过怀表，收进兜里。", CHARS, ITEMS)
    check("接回记为持有", [e for e in ev2 if e["type"] == "hold_item"], [e["type"] for e in ev2])

    # ---- 4. 增量更新与重写 ----
    print("\n[4] 落盘与增量")
    tmp = Path(tempfile.mkdtemp(prefix="nf_state_"))
    try:
        (tmp / "data" / "setting").mkdir(parents=True)
        (tmp / "data" / "setting" / "setting.json").write_text(
            json.dumps({"characters": [{"name": c} for c in CHARS],
                        "items": [{"name": i} for i in ITEMS]}, ensure_ascii=False),
            encoding="utf-8")
        ch = tmp / "data" / "chapters" / "raw"
        ch.mkdir(parents=True)
        for n, t in BROKEN.items():
            (ch / ("%02d.md" % n)).write_text(t, encoding="utf-8")

        state = S.rebuild(tmp)
        check("账本落盘", (tmp / S.STATE_REL).exists())
        check("重建后冲突 5 类",
              {"dead_again", "death_duplicate", "item_resurrect",
               "time_regression", "unknown_name"} <= set(codes(state["conflicts"])),
              codes(state["conflicts"]))
        check("事件条数 > 10", len(state["events"]) > 10, len(state["events"]))

        # 重写第 6 章：让老周彻底退场 → 重复死亡与"死后再现"都应消失
        (ch / "06.md").write_text("沈砚说：「走吧。」林小满点头，跟着他过了桥。", encoding="utf-8")
        state2, mine = S.update_chapter(tmp, 6, (ch / "06.md").read_text(encoding="utf-8"))
        check("重写后不再报第6章的冲突",
              not [c for c in state2["conflicts"] if c.get("chapter") == 6],
              [(c["code"], c.get("chapter")) for c in state2["conflicts"]])
        check("重复死亡随重写消失",
              not [c for c in state2["conflicts"] if c["code"] == "death_duplicate"],
              [(c["code"], c["chapter"]) for c in state2["conflicts"]])
        check("增量不累计旧事件（第6章只剩新事件）",
              len([e for e in state2["events"] if e["chapter"] == 6]) <= 3,
              [e["type"] for e in state2["events"] if e["chapter"] == 6])

        # 幂等：连续两次 rebuild，事件数一致
        s3 = S.rebuild(tmp)
        s4 = S.rebuild(tmp)
        check("rebuild 幂等（事件数一致）",
              len(s3["events"]) == len(s4["events"]), (len(s3["events"]), len(s4["events"])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 5. 设定集缺失不崩 ----
    print("\n[5] 边界")
    tmp = Path(tempfile.mkdtemp(prefix="nf_state_edge_"))
    try:
        st, _mine = S.update_chapter(tmp, 1, "沈砚说：「走。」", str(tmp / "nope.json"))
        check("没有设定集也能跑（不崩）", isinstance(st, dict) and "events" in st)
        check("没角色名时零事件", len(st["events"]) == 0, st["events"])
        check("summarize 可用", "事件" in S.summarize(st), S.summarize(st))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 6. 收尾闸门接了账本 ----
    print("\n[6] quality_checklist 接账本")
    import quality_checklist as qc                              # noqa: E402
    tmp = Path(tempfile.mkdtemp(prefix="nf_state_gate_"))
    try:
        (tmp / "data" / "state").mkdir(parents=True)
        state = S.save_state(tmp, {"version": 1, "events": [
            {"chapter": 2, "type": "death", "who": "韩拙", "evidence": "韩拙死了。"},
            {"chapter": 3, "type": "act", "who": "韩拙", "evidence": "韩拙说：「走。」"},
        ], "conflicts": []})
        check("账本自带冲突（韩拙死而复生）",
              any(c["code"] == "dead_again" for c in state["conflicts"]),
              state["conflicts"])

        old_root = qc.ROOT
        try:
            qc.ROOT = tmp
            issues3 = qc.check_consistency("韩拙说：「走。」", 3, tmp / "nope.json")
            check("第3章读到账本冲突", any("dead_again" in i for i in issues3), issues3)
            issues4 = qc.check_consistency("沈砚说：「走。」", 4, tmp / "nope.json")
            check("第4章不受第3章冲突影响", issues4 == [], issues4)
        finally:
            qc.ROOT = old_root

        # 账本不存在 → 退回旧判据（不崩、不误红）
        qc.ROOT = tmp / "empty"
        try:
            (tmp / "empty").mkdir()
            check("没有账本时退回旧判据", qc.check_consistency("无关文字", 1, tmp / "nope.json") == [])
        finally:
            qc.ROOT = old_root

        # 账本存在但零事件（空壳）→ 同样退回旧判据，不能被静默架空
        qc.ROOT = tmp / "hollow"
        try:
            (tmp / "hollow" / "data" / "state").mkdir(parents=True)
            (tmp / "hollow" / "data" / "state" / "story_state.json").write_text(
                json.dumps({"version": 1, "events": [], "conflicts": []}), encoding="utf-8")
            setting = tmp / "hollow" / "setting.json"
            setting.write_text(json.dumps(
                {"characters": [{"name": "甲"}, {"name": "乙"}, {"name": "丙"}]},
                ensure_ascii=False), encoding="utf-8")
            long_text = "无关的文字。" * 200          # >500 字且一个角色名都没提
            issues = qc.check_consistency(long_text, 1, setting)
            check("空账本不接管旧判据（仍会报未提及角色）",
                  any("未提及" in i for i in issues), issues)
        finally:
            qc.ROOT = old_root
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
