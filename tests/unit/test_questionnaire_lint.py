# -*- coding: utf-8 -*-
"""出卷校验器的回归锁（2026-11-04）。

## 这个文件锁什么

`scripts/questionnaire_lint.py` 是问卷系统的第一道机器闸门。它自己出错，
比没有它更糟（会给出"通过"的假绿灯）。这里锁四条：

1. 现在这两卷 r2 **必须通过**（0 错误）——两卷是后续所有卷子的样板。
2. **引文与原文对不上**必须被拦（2026-11-04 事故：8 条出处 5 红 3 黄）。
3. **选项缺 cost** 必须被拦（没有代价的选项只能按篇幅猜）。
4. **`!` 断言**必须双向可信：断言"不存在"的东西真的不存在才算过。

用法：python tests/unit/test_questionnaire_lint.py
"""
import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import questionnaire_lint as QL  # noqa: E402

PASS, FAIL = [], []
QDIR = ROOT / "docs" / "questionnaires"
R2 = [str(QDIR / "20261104-宪法层-r2.md"), str(QDIR / "20261104-裁决层A-r2.md")]


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + ("  → " + str(detail) if detail and not cond else ""))


def run(files, extra=("--json",)):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = QL.main(list(files) + list(extra))
    out = buf.getvalue()
    try:
        data = json.loads(out[out.index("{"):])
    except ValueError:
        data = {"raw": out}
    return code, data


def mutate(src: Path, dst: Path, old: str, new: str) -> None:
    s = src.read_text(encoding="utf-8")
    assert old in s, "变异点没找到：" + old
    dst.write_text(s.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    print("== 1. r2 两卷必须通过 ==")
    code, data = run(R2)
    check("r2 通过（0 错误）", code == 0 and data.get("ok") is True,
          data.get("errors"))
    check("两卷题数 13 + 6", (data.get("files") or [{}])[0].get("questions") in (6, 13)
          and len(data.get("files") or []) == 2, data.get("files"))

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        src = QDIR / "20261104-宪法层-r2.md"

        print("== 2. 假引文必须被拦 ==")
        bad = tmp / "bad_quote.md"
        mutate(src, bad, 'quote: "### 3.3 P0 二十项功能"', 'quote: "### 3.3 P0 三十项功能"')
        code, data = run([str(bad)], ("--json", "--no-git"))
        msgs = " ".join(e["msg"] for e in data.get("errors") or [])
        check("引文对不上 → 退出码 1", code == 1, code)
        check("错误里说清是引文", "引文与原文对不上" in msgs, msgs[:120])

        print("== 3. 缺 cost 必须被拦 ==")
        bad2 = tmp / "bad_cost.md"
        mutate(src, bad2, "    cost: {token: 0, days: 0.5}\n    reversible: true\n  - key: backtest_plus_locks",
               "    reversible: true\n  - key: backtest_plus_locks")
        code, data = run([str(bad2)], ("--json", "--no-git"))
        msgs = " ".join(e["msg"] for e in data.get("errors") or [])
        check("缺 cost → 退出码 1", code == 1, code)
        check("错误里说清 cost", "缺 cost" in msgs, msgs[:120])

        print("== 4. ! 断言双向可信 ==")
        bad3 = tmp / "bad_neg.md"
        mutate(src, bad3, '    - "!data/chapters/raw"', '    - "!prompts/stage5_check.md"')
        code, data = run([str(bad3)], ("--json", "--no-git"))
        msgs = " ".join(e["msg"] for e in data.get("errors") or [])
        check("断言存在的东西不存在 → 退出码 1", code == 1, code)
        check("错误里说清断言", "断言不存在" in msgs, msgs[:120])

    print("== 5. 收卷器：勾选 / 附言 / 落盘 ==")
    import questionnaire_collect as QC

    ans = []
    for f in R2:
        ans.extend(QC.parse_file(Path(f)))
    checked = [a for a in ans if a["status"] == "checked"]
    free = [a for a in ans if a["status"] == "free"]
    check("解析出 19 题", len(ans) == 19, len(ans))
    check("勾选 15 / 只有附言 4 / 未答 0",
          (len(checked), len(free), len(ans) - len(checked) - len(free)) == (15, 4, 0),
          (len(checked), len(free)))
    decisions = QC.existing(QC.DECISION_LOG)
    keys = {str(d.get("key")) for d in decisions}
    missing = [a["id"] for a in ans if a["id"] not in keys]
    check("每题都落了 decision-log", not missing, missing)
    check("自由作答已归一（无 value=自由作答）",
          not [d for d in decisions if str(d.get("value")) == "自由作答"],
          "仍有未归一条目")

    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
