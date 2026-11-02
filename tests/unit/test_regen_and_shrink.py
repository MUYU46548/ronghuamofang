# -*- coding: utf-8 -*-
"""补长上限 + 字数缩水（2026-11-02，清单第 6、7 条）。

## 两件不同的事

- **第 6 条**：一章写短了会被自动打回重写 / 扩写 / 转加强模式，实测能烧到
  200 万 token 还失败（run 45：输入 791 万 / 4 小时 40 分），而同样任务直连
  只要 6.5 万。给"为这一章补长"设一条 token 线，超了就停并说清。
- **第 7 条**：raw/03.md 7184 字 → refined 2474 字，用户完全不知道。
  缩减超 10% 要记进 progress，界面标红。

## 判据

- B1 达到上限即判定超线（等于也算）
- B2 没到不误报
- B3 limit=0 / 负数 = 不设限（不许把"没配置"当成"上限 0 = 立刻停"）
- B4 配置项存在且默认值可读
- S1 缩水 65% 被记录，字段齐（before/after/ratio）
- S2 缩水 5% 不记（不许狼来了）
- S3 字数变多不记
- S4 before=0 / 坏数据不炸

用法：python tests/unit/test_regen_and_shrink.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from stage4_writing import _regen_over_budget                      # noqa: E402
from utils import shrink_watch                                     # noqa: E402
from utils.config_io import load_config_yaml                       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


class FakeProgress:
    """够用的假 progress：只要能 setdefault + save。"""
    def __init__(self):
        self.data = {}
        self.saved = 0

    def save(self):
        self.saved += 1


def main():
    print("=" * 66)
    print("补长上限 + 字数缩水")
    print("=" * 66)

    # ---- 第 6 条 ----
    check("B1 到线即超", _regen_over_budget(400000, 400000) is True)
    check("B1 超过更超", _regen_over_budget(1200000, 400000) is True)
    check("B2 没到不误报", _regen_over_budget(399999, 400000) is False)
    check("B3 limit=0 不设限", _regen_over_budget(99999999, 0) is False)
    check("B3 负 limit 不设限", _regen_over_budget(99999999, -1) is False)
    check("B3 None 不炸", _regen_over_budget(100, None) is False)

    cfg = load_config_yaml("config/system.yaml")
    limit = (cfg.get("chapter") or {}).get("regen_token_limit")
    check("B4 system.yaml 有 regen_token_limit", isinstance(limit, int), limit)
    check("B4 默认值是 40 万", limit == 400000, limit)

    # ---- 第 7 条 ----
    check("S0 比率算对（7184 → 2474）",
          abs(shrink_watch.ratio_of(7184, 2474) - 0.6556) < 0.001,
          shrink_watch.ratio_of(7184, 2474))

    pr = FakeProgress()
    d = shrink_watch.note(pr, 5, 3, 7184, 2474)
    check("S1 缩水 65% 被记录", bool(d), d)
    check("S1 字段齐", d and {"before", "after", "ratio"} <= set(d), d)
    check("S1 写进 progress.stages[5].shrink", 
          pr.data.get("stages", {}).get("5", {}).get("shrink", {}).get("3") == d,
          pr.data)
    check("S1 触发了 save", pr.saved >= 1, pr.saved)
    check("S1 一句话说人话", "66%" in shrink_watch.describe(d), shrink_watch.describe(d))

    pr2 = FakeProgress()
    check("S2 只缩 5% 不记", shrink_watch.note(pr2, 5, 1, 6000, 5700) is None, pr2.data)
    check("S3 变多不记", shrink_watch.note(pr2, 5, 2, 6000, 7000) is None, pr2.data)
    check("S3 一字不变不记", shrink_watch.note(pr2, 5, 4, 6000, 6000) is None, pr2.data)
    check("S4 before=0 不炸", shrink_watch.note(pr2, 5, 5, 0, 100) is None)
    check("S4 坏数据不炸", shrink_watch.note(pr2, 5, 6, "abc", None) is None)
    check("S4 progress=None 只算不记",
          bool(shrink_watch.note(None, 5, 7, 7000, 3000)))

    # ---- 接线 ----
    s5 = (ROOT / "scripts" / "stage5_check.py").read_text(encoding="utf-8")
    check("S5 stage5 真的记缩水", "shrink_watch.note(progress, 5" in s5)
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("S6 /state 透传 shrink", 'out["shrink"] = st["shrink"]' in api)
    s4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("B5 stage4 真的用超线判据", "_regen_over_budget(_used, regen_token_limit)" in s4)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
