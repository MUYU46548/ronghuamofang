# -*- coding: utf-8 -*-
"""两人同场的互动约束（C 组“关系网驱动互动”，2026-11-03）。

## 判据（每条都能变红）

- R1 关系原文只取“提到对方”的那几句（依据可复核）
- R2 关系词 → 基调：师兄弟=亲近 / 仇敌=仇敌 / 主从=主从 / 认不出=未知
- R3 称呼没写就明说没写（不替作者发明称呼）
- R4 一方不在设定集 → found=false 并点名是谁缺
- R5 同一个角色 → 400（没有“自己跟自己同场”）
- R6 端点接线 + 界面入口
- R7 输出带 guide.address / avoid / writing（可当写作约束用）

用法：python tests/unit/test_interaction.py
"""
import io
import json
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


DOC = {"characters": [
    {"name": "沈砚", "relations": ["与程砧是同门师兄弟，称其为“师兄”；曾并肩守过雾港"]},
    {"name": "程砧", "relations": ["与沈砚同门，视其为师弟，遇事先挡在前面"]},
    {"name": "崔无咎", "relations": ["与沈砚有杀父之仇，是不共戴天的仇敌"]},
    {"name": "柳氏", "relations": ["沈砚的雇主，主仆名分清楚"]},
    {"name": "路人甲", "relations": []},
]}


def main():
    print("=" * 66)
    print("两人同场互动约束")
    print("=" * 66)

    from utils import interaction as it

    print("\n[R1] 只取提到对方的那几句")
    r = it.analyze(DOC, "沈砚", "程砧")
    check("found", r["found"] is True)
    check("双向各一条", len(r["basis"]) == 2, r["basis"])
    check("每条带 who（谁说的）",
          {x["who"] for x in r["basis"]} == {"沈砚", "程砧"}, r["basis"])
    check("没提到对方的句子不进依据",
          all("崔无咎" not in x["text"] for x in r["basis"]), r["basis"])

    print("\n[R2] 关系词 → 基调")
    check("师兄弟 → 亲近", r["tone"] == "亲近", r["tone"])
    r2 = it.analyze(DOC, "沈砚", "崔无咎")
    check("不共戴天 → 仇敌", r2["tone"] == "仇敌", r2["tone"])
    r3 = it.analyze(DOC, "沈砚", "柳氏")
    check("雇主 → 主从", r3["tone"] == "主从", r3["tone"])
    r4 = it.analyze(DOC, "沈砚", "路人甲")
    check("没有关系原文 → 未知", r4["tone"] == "未知", r4)

    print("\n[R3] 称呼没写就明说没写")
    check("写了称呼（含“称”）→ 不报缺", "称呼" not in (r.get("missing") or []),
          r.get("missing"))
    check("没写称呼 → 报缺并说明是建议",
          "称呼" in (r4.get("missing") or []) and "不是正典" in (r4.get("note") or ""),
          r4.get("note"))

    print("\n[R4] 一方不在设定集")
    r5 = it.analyze(DOC, "沈砚", "查无此人")
    check("found=false", r5["found"] is False)
    check("点名缺谁", r5.get("missing") == ["查无此人"], r5.get("missing"))
    check("说清该怎么办", "补条目" in (r5.get("note") or ""), r5.get("note"))

    print("\n[R5] 同一个角色 / 空参数")
    check("同一个角色 → ok=false", it.analyze(DOC, "沈砚", "沈砚")["ok"] is False)
    check("空参数 → ok=false", it.analyze(DOC, "", "沈砚")["ok"] is False)

    print("\n[R6] 关系基调映射出写作约束")
    for tone, ok_key in (("仇敌", "仇敌"), ("主从", "主从"), ("亲近", "亲近")):
        g = it.TONE_GUIDE[ok_key]
        check("%s 有称呼/禁忌/写法三条" % tone,
              bool(g.get("address")) and bool(g.get("avoid")) and len(g.get("writing") or []) >= 2,
              g)
    check("未知基调的写法是“先补关系”而不是瞎编",
          "先" in it.TONE_GUIDE["未知"]["writing"][0])

    print("\n[R7] 从盘上读 + 接线")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "data" / "setting").mkdir(parents=True)
        (root / "data" / "setting" / "setting.json").write_text(
            json.dumps(DOC, ensure_ascii=False), encoding="utf-8")
        rr = it.build(root, "沈砚", "程砧")
        check("build 从 setting.json 读", rr.get("found") is True, rr.get("note"))
        rr2 = it.build(Path(tmp) / "nowhere", "沈砚", "程砧")
        check("没有 setting.json → 明确报缺", rr2.get("ok") is False, rr2)

    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("端点有转发", 'elif p == "/setting/interaction":' in api_src)
    dom = (ROOT / "scripts" / "nf_api_domains" / "setting_state.py").read_text(encoding="utf-8")
    check("域模块声明路由", '"/setting/interaction"' in dom)

    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
