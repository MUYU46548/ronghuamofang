# -*- coding: utf-8 -*-
"""设定集 ↔ 大纲 重合度护栏（方案 C）的回归锁（2026-10-19）。

## 为什么需要它

这本仓栽过「设定集来自另一个宇宙、全流程绿灯」的事故：`user_outline` 讲沈砚/机关术，
素材卡是潮神/鲸民 → 归并出的设定集 16 个角色只有 2 个属于大纲、18 个地点 0 个。
护栏的价值全在「它不会悄悄失效」，所以这里对**每一条判据**单独钉：

| 编号 | 钉什么 |
|---|---|
| C1 | **真实事故回放**：history 里的污染设定集 + 雾港核心大纲 → 必须报 warn/block |
| C2 | 配套的设定 + 同一份大纲 → 必须 ok（防「一律报错」的假阳性护栏） |
| C3 | 大纲为空 → skipped（系统自拟大纲时无从判断） |
| C4 | `mode=block` → verdict=block（硬拦口径可切换） |
| C5 | `mode=off` → skipped 且 checked=False（开关真的关掉计算，而不是关掉打印） |
| C6 | `world.all` 不计入（它是各分类并集，计入会让分母翻倍） |
| C7 | 别名命中算命中 |
| C8 | 总命中率低于阈值 → warn（角色/地点都有命中也要拦「整体不配套」） |
| C9 | 单字名不参与命中（「甲」「乙」这类会假命中） |
| C10 | `summary_line` 带总数与百分比（终端/进度条看得懂） |
| C11 | 报告落盘/读回可往返 |

用法：python tests/unit/test_setting_coherence.py（零 LLM、零网络）
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import coherence as coh                             # noqa: E402

PASS, FAIL = [], []
OUTLINE = ("落魄机关术师沈砚被边城守卫队长程砧找上门，一同前往雾港遗址。"
           "三年前失控的「雾港核心」与他当年一个决定有关。")


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(extra)[:200]) if extra else ""))


def setting_of(chars=(), locations=(), factions=(), items=(), concepts=()):
    return {
        "characters": [{"name": c} for c in chars],
        "world": {
            "locations": [{"name": n} for n in locations],
            "factions": [{"name": n} for n in factions],
            "items": [{"name": n} for n in items],
            "concepts": [{"name": n} for n in concepts],
        },
        "plot_fragments": [],
        "timeline": [],
    }


def main():
    print("=" * 66)
    print("  设定集 ↔ 大纲 重合度护栏")
    print("=" * 66)

    # ---------------------------------------------------------------- C1
    print("\n[C1] 真实事故回放：潮神/鲸民设定集 vs 雾港核心大纲")
    snap = REPO / "history" / "20261011_154844_stage1_done" / "data" / "setting" / "setting.json"
    if not snap.exists():
        print("  [SKIP] 快照不存在（干净克隆）：%s" % snap)
    else:
        polluted = json.loads(snap.read_text(encoding="utf-8"))
        rep = coh.evaluate(polluted, OUTLINE, mode="warn")
        check("C1 事故设定集被判 warn（不是 ok）", rep["verdict"] == "warn",
              rep["verdict"])
        check("C1 角色与地点双双 0 命中被列为决定项",
              len(rep["fatal"]) >= 2 and rep["checked"],
              rep["fatal"])
        check("C1 报告里能看到命中率（%d/%d）" % (rep["hits"], rep["total"]),
              rep["total"] > 0 and rep["ratio"] < 0.3, rep["ratio"])

    # ---------------------------------------------------------------- C2
    print("\n[C2] 配套设定 + 同一份大纲 → ok（护栏不得一律报错）")
    good = setting_of(chars=["沈砚", "程砧", "祝鹤川"],
                      locations=["雾港遗址", "边城废屋"],
                      items=["封印石", "七锁回环"])
    rep = coh.evaluate(good, OUTLINE)
    check("C2 配套设定判 ok", rep["verdict"] == "ok", rep["reasons"])
    check("C2 角色与地点都有命中", rep["kinds"]["characters"]["hits"] >= 2
          and rep["kinds"]["locations"]["hits"] >= 1,
          {k: v["hits"] for k, v in rep["kinds"].items()})

    # ---------------------------------------------------------------- C3
    print("\n[C3] 大纲为空 → skipped")
    rep = coh.evaluate(good, "   ")
    check("C3 空大纲不判错（skipped）", rep["verdict"] == "skipped" and not rep["checked"],
          rep["reasons"])

    # ---------------------------------------------------------------- C4 / C5
    print("\n[C4/C5] 开关：block 硬拦 · off 真的不算")
    bad = setting_of(chars=["潮神", "塞尔·兰"], locations=["月宫", "鲸民之城"])
    check("C4 mode=block → verdict=block",
          coh.evaluate(bad, OUTLINE, mode="block")["verdict"] == "block")
    off = coh.evaluate(bad, OUTLINE, mode="off")
    check("C5 mode=off → skipped 且 checked=False",
          off["verdict"] == "skipped" and off["checked"] is False, off["reasons"])
    check("C5 off 时不产生 fatal 结论", not off["fatal"], off["fatal"])

    # ---------------------------------------------------------------- C6
    print("\n[C6] world.all 不计入分母（它是各分类并集）")
    with_all = {
        "characters": [{"name": "沈砚"}],
        "world": {
            "locations": [{"name": "雾港遗址"}],
            "all": [{"name": "雾港遗址"}, {"name": "月宫"}, {"name": "镜铃"}],
        },
        "plot_fragments": [], "timeline": [],
    }
    names = coh.collect_names(with_all)
    check("C6 `all` 键被跳过", "all" not in names, list(names.keys()))
    check("C6 分母只算分类条目（2 条，不是 5 条）",
          sum(len(v) for v in names.values()) == 2,
          {k: len(v) for k, v in names.items()})

    # ---------------------------------------------------------------- C7
    print("\n[C7] 别名命中算命中")
    alias_setting = {
        "characters": [{"name": "程砧", "aliases": ["边城守卫队长"]}],
        "world": {"locations": [{"name": "雾港遗址", "aliases": ["旧港"]}]},
        "plot_fragments": [], "timeline": [],
    }
    a = coh.evaluate(alias_setting, "边城守卫队长在旧港等他。")
    check("C7 别名出现在大纲里也算命中", a["verdict"] == "ok",
          {k: v["hits"] for k, v in a["kinds"].items()})

    # ---------------------------------------------------------------- C8
    print("\n[C8] 角色/地点都有命中，但整体命中率过低 → 仍然要报")
    lopsided = setting_of(chars=["沈砚", "甲一", "乙二", "丙三", "丁四"],
                          locations=["雾港遗址", "戊五", "己六", "庚七"])
    r8 = coh.evaluate(lopsided, "沈砚走进雾港遗址。")
    check("C8 总命中率 2/9=22% > 默认 15% → ok（阈值口径）", r8["verdict"] == "ok",
          r8["ratio"])
    r8b = coh.evaluate(lopsided, "沈砚走进雾港遗址。", min_ratio=0.5)
    check("C8 把阈值抬到 50% → warn（阈值真的生效）", r8b["verdict"] == "warn",
          r8b["reasons"])

    # ---------------------------------------------------------------- C9
    print("\n[C9] 单字名不参与命中")
    one = setting_of(chars=["湛", "沈砚"], locations=["雾港遗址"])
    r9 = coh.evaluate(one, "湛湛天光下，沈砚到了雾港遗址。")
    check("C9 单字名即使出现也不算命中",
          r9["kinds"]["characters"]["hits"] == 1,
          r9["kinds"]["characters"])

    # ---------------------------------------------------------------- C10
    print("\n[C10] summary_line 可读")
    line = coh.summary_line(r9)
    check("C10 摘要含总数与百分比", "总" in line and "%" in line and "角色" in line, line)

    # ---------------------------------------------------------------- C11
    print("\n[C11] 报告落盘/读回")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "coherence.json"
        coh.write_report(r9, p)
        back = coh.load_report(p)
        check("C11 往返一致（verdict/ratio）",
              back and back["verdict"] == r9["verdict"] and back["ratio"] == r9["ratio"],
              back and {k: back[k] for k in ("verdict", "ratio")})

    print("\n" + "=" * 66)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
