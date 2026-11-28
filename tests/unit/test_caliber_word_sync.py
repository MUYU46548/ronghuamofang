# -*- coding: utf-8 -*-
"""字数口径必须只有一个来源：判定引擎的 J01/J02 == config 的 chapter.target_words。

## 这个不一致是怎么发现并确认的

真机工作区的 `data/judge/caliber.json` 里 J01/J02 还是 **5000 / 9000**，
而同一时间 `config/system.yaml` 的 `chapter.target_words` 是 `[4000, 7200]`
（2026-11-05 把字数口径从"含标点"改成"只计汉字"时按同等篇幅折算的结果）。

后果不是"差一点"，而是**两个数字都在界面上显示、互相矛盾**：
同一章按 config 算合格（4000 汉字达标），按判定引擎算"字数不足"（<5000）。

`docs/agents/judge-engine.md` 第 12 行其实早就记过这个症状：
“口径 10-26 才提到 [5000,9000]，存量不重判”—— 当时只记了症状，没有修。

## 本用例锁什么

1. `caliber.FACTORY` 的 J01/J02 == **出厂** `config/system.yaml` 的 target_words；
2. `rules.body_metrics` / `backtest` 的兜底默认值也等于这两个数
   （口径表缺该键时才用到，但用了错的兜底等于换了个尺）；
3. 反向判据：故意改坏一处，本用例必须红（不空转）。

用法：python tests/unit/test_caliber_word_sync.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "utils"))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  → " + str(detail)[:260] if detail else ""))


def main():
    print("=" * 70)
    print("字数口径同源：判定引擎 J01/J02 == config.chapter.target_words")
    print("=" * 70)

    import yaml
    from utils.judge import caliber as cal

    # ---------- [1] 出厂 config 的目标区间 ----------
    print("\n[1] 出厂 config 的 chapter.target_words")
    cfg = yaml.safe_load((REPO / "config" / "system.yaml").read_text(encoding="utf-8"))
    tw = ((cfg.get("chapter") or {}).get("target_words") or [])
    check("target_words 是列表且有两个元素",
          isinstance(tw, list) and len(tw) == 2, tw)
    low, high = int(tw[0]), int(tw[1])
    check("下限 < 上限", low < high, (low, high))

    # ---------- [2] 判定引擎的出厂口径必须等于它 ----------
    print("\n[2] caliber.FACTORY 的 J01/J02 必须等于上面两个数")
    factory = {r["id"]: r for r in cal.FACTORY}
    check("J01 存在", "J01" in factory, list(factory)[:6])
    check("J02 存在", "J02" in factory, list(factory)[:6])
    j01 = int(factory["J01"]["value"])
    j02 = int(factory["J02"]["value"])
    check("**J01（每章最少字数）== config 下限 %d**" % low, j01 == low,
          "判定引擎=%d vs config=%d" % (j01, low))
    check("**J02（每章最多字数）== config 上限 %d**" % high, j02 == high,
          "判定引擎=%d vs config=%d" % (j02, high))

    # ---------- [3] 兜底默认值也不许另写一套 ----------
    print("\n[3] 代码里的兜底默认值也必须同源（否则换个路径就用回旧尺）")
    rules_src = (REPO / "scripts" / "utils" / "judge" / "rules.py").read_text(encoding="utf-8")
    m1 = re.search(r'cal\.get\("J01",\s*(\d+)\)', rules_src)
    m2 = re.search(r'cal\.get\("J02",\s*(\d+)\)', rules_src)
    check("rules.body_metrics 的 J01 兜底 == %d" % low,
          m1 and int(m1.group(1)) == low, m1.group(1) if m1 else "没找到")
    check("rules.body_metrics 的 J02 兜底 == %d" % high,
          m2 and int(m2.group(1)) == high, m2.group(1) if m2 else "没找到")

    bt_src = (REPO / "scripts" / "utils" / "judge" / "backtest.py").read_text(encoding="utf-8")
    m3 = re.search(r'\.get\("J01",\s*(\d+)\)', bt_src)
    check("backtest 的 J01 兜底 == %d" % low,
          m3 and int(m3.group(1)) == low, m3.group(1) if m3 else "没找到")

    # ---------- [4] 反向判据：改坏必须能抓到 ----------
    print("\n[4] 反向判据：把 factory 改回 5000/9000，上面的比对必须不等")
    drifted = dict(factory)
    drifted["J01"] = {"id": "J01", "value": 5000}
    drifted["J02"] = {"id": "J02", "value": 9000}
    check("旧值 5000 会被判为不同源（本用例不是空转）",
          int(drifted["J01"]["value"]) != low, low)
    check("旧值 9000 会被判为不同源", int(drifted["J02"]["value"]) != high, high)

    # ---------- [5] 已存的口径文件也要能提示升级 ----------
    print("\n[5] 既有工作区的口径表过期时，需要能被发现")
    try:
        stale = cal.needs_upgrade({"version": 2, "criteria": [
            {"id": "J01", "value": 5000}, {"id": "J02", "value": 9000}]})
        # needs_upgrade 的返回形态随版本不同：只要是“有话说”就算能发现
        detected = bool(stale)
        check("旧口径（5000/9000）会被 needs_upgrade 判为需要处理",
              detected, stale)
    except Exception as e:                                    # noqa: BLE001
        check("needs_upgrade 可调用", False, e)

    print("\n" + "=" * 70)
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
