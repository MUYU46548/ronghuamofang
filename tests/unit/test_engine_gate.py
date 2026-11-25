# -*- coding: utf-8 -*-
"""引擎白名单收窄（2026-11-25）：只留 dsh / self，旧取值必须**出声**。

为什么值得单独立判据：`resolve_engine` 以前对未知取值是“WARN 一句 + 按默认跑”。
默认值当时是 direct —— 于是配置写 `hermes` 的人会得到“看起来在跑、其实换了引擎”
的状态。这类静默退化在界面上无法察觉，只能靠判据钉住。

用法：python tests/unit/test_engine_gate.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import engine as eng                       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if (extra and not cond) else ""))


def main():
    check("白名单只剩 dsh / self", tuple(eng.VALID_ENGINES) == ("dsh", "self"),
          eng.VALID_ENGINES)
    check("缺键时默认 dsh（不是 direct）", eng.DEFAULT_ENGINE == "dsh", eng.DEFAULT_ENGINE)

    check("direct 已登记为下线并写了原因",
          "direct" in eng.RETIRED_ENGINES and len(eng.RETIRED_ENGINES["direct"]) > 10)
    check("hermes 已登记为下线并写了原因",
          "hermes" in eng.RETIRED_ENGINES and len(eng.RETIRED_ENGINES["hermes"]) > 10)

    for old in ("direct", "hermes"):
        eng._WARNED.clear()
        got = eng.resolve_engine({"engine": old})
        check("%s 被解析成 dsh（不再按原引擎跑）" % old, got == "dsh", got)
        check("%s 会把原因记进 _WARNED（界面/日志能拿到）" % old, old in eng._WARNED)

    check("self 仍然可用（离线出任务书是保底）", eng.resolve_engine({"engine": "self"}) == "self")
    check("dsh 可用", eng.resolve_engine({"engine": "dsh"}) == "dsh")
    check("空值 / 缺键 → dsh", eng.resolve_engine({}) == "dsh"
          and eng.resolve_engine({"engine": ""}) == "dsh")
    check("大写/空白也能识别", eng.resolve_engine({"engine": "  DSH "}) == "dsh")

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    print("=" * 60)
    for f in FAIL:
        print("    - " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
