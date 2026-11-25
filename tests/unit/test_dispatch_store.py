# -*- coding: utf-8 -*-
"""派单候选存储：写 / 读 / 改状态（2026-11-25，总账 F7 前半段）。

为什么单独一条：这层的错法都是**静默**的 —— 键写错、状态没落盘、覆盖了上一轮，
界面照样显示，只有到“采用之后正文没变”才发现。所以每个动作都要有断言，
落盘文件还要能被重新读回来（不是只改了内存）。

用法：python tests/unit/test_dispatch_store.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import dispatch as dsp                       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if (extra and not cond) else ""))


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        check("没写过时 load 返回 None", dsp.load(root, 3) is None)

        items = [
            {"id": "c1", "para": 1, "note": "收得太急", "before": "原句一", "after": "改后一", "status": "open"},
            {"id": "c2", "para": 4, "note": "话没问出口", "before": "原句二", "after": "改后二", "status": "open"},
        ]
        dsp.save(root, 3, items, layer="refined", source="test")

        path = root / "data" / "state" / "dispatch" / "ch03.json"
        check("落盘文件按章号命名（ch03.json）", path.exists(), str(path))
        raw = json.loads(path.read_text(encoding="utf-8"))
        check("落盘带章号/层/时间/来源", all(k in raw for k in
              ("chapter", "layer", "created_at", "source")), sorted(raw))

        got = dsp.load(root, 3)
        check("读回来的段数一致", len(got["items"]) == 2, len(got["items"]))
        check("段号与改后文本原样保留",
              got["items"][1]["para"] == 4 and got["items"][1]["after"] == "改后二")

        check("set_status 改一条返回 True", dsp.set_status(root, 3, "c1", "adopted") is True)
        check("改动真的落盘（重新读文件）",
              json.loads(path.read_text(encoding="utf-8"))["items"][0]["status"] == "adopted")
        check("另一条不受影响", dsp.load(root, 3)["items"][1]["status"] == "open")
        check("不存在的 id 返回 False", dsp.set_status(root, 3, "nope", "adopted") is False)

        check("find 能按 id 取到那一条", dsp.find(root, 3, "c2")["para"] == 4)
        check("find 找不到时返回 None", dsp.find(root, 3, "nope") is None)

        # 覆盖写 = 这一轮派单的结果（旧的不该残留）
        dsp.save(root, 3, [{"id": "c9", "para": 7, "note": "", "before": "", "after": "改后九",
                            "status": "open"}], layer="refined")
        check("重复写等于覆盖上一轮", len(dsp.load(root, 3)["items"]) == 1)

        # 坏文件不该炸：返回 None（界面按“没有候选”处理）
        path.write_text("{ 这不是 json", encoding="utf-8")
        check("坏文件返回 None 而不是抛异常", dsp.load(root, 3) is None)

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    print("=" * 60)
    for f in FAIL:
        print("    - " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
