# -*- coding: utf-8 -*-
"""D1 探针的回归锁（2026-11-04）。

## 锁什么

探针的结论会决定"一期按段还是按章"，所以它的三分类必须可信：

1. 一字未改 → `matched`（哈希相同）
2. 改了一句 → `edited`（相似度 ≥ 阈值），不能算成"删除 + 新增"
3. 真新增 / 真删除 → 各归各的，不许被近似匹配吞掉

用法：python tests/unit/test_d1_probe.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from d1_probe import split_paragraphs, compare, fingerprint  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  → " + str(detail)) if detail and not cond else ""))


OLD = """夜里下起了雨，街灯把水洼照得发白。

他把伞收起来，抖了抖水，靠在门边。

屋里有人在低声说话，听不清内容。

桌上有半碗凉透的面，边上摆着一双筷子。

他坐下来，没有动筷子，也没有开灯。"""

# 改第 2 段、插一段、删第 4 段
NEW = """夜里下起了雨，街灯把水洼照得发白。

他把伞收起来，抖了抖上面的水，靠在门边。

窗外偶尔有车驶过，灯光扫过天花板。

屋里有人在低声说话，听不清内容。

他坐下来，没有动筷子，也没有开灯。"""


def main() -> int:
    old = split_paragraphs(OLD)
    new = split_paragraphs(NEW)
    check("切段 5 → 5", (len(old), len(new)) == (5, 5), (len(old), len(new)))

    rep = compare(old, new)
    check("原样保留 3 段", rep["matched"] == 3, rep["matched"])
    check("改过 1 段（不算删+增）", rep["edited"] == 1, rep["edited"])
    check("新增 1 段", rep["inserted"] == 1, rep["inserted"])
    check("删除 1 段", rep["removed"] == 1, rep["removed"])
    check("carry_rate = 0.8", rep["carry_rate"] == 0.8, rep["carry_rate"])

    # 反证：阈值拉到 0.99，改过的那段就认不出来了（会变成 删+增）
    strict = compare(old, new, threshold=0.99)
    check("阈值 0.99 → 改过段不再认作同一段", strict["edited"] == 0, strict["edited"])
    check("阈值 0.99 → 变成 新增 2 / 删除 2",
          (strict["inserted"], strict["removed"]) == (2, 2),
          (strict["inserted"], strict["removed"]))

    # 哈希只认一字未改
    check("指纹对空白不敏感", fingerprint("他 坐下来") == fingerprint("他  坐下来"),
          None)
    check("指纹对改字敏感", fingerprint("他坐下来") != fingerprint("他站下来"), None)

    # 全等稿：carry_rate = 1
    same = compare(old, old)
    check("全等稿 carry_rate = 1.0", same["carry_rate"] == 1.0 and same["edited"] == 0, same)

    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
