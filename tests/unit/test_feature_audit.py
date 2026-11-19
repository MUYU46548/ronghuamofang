# -*- coding: utf-8 -*-
"""功能—证据对账的判据（2026-11-19）。

守两件事：
A. `_check()` 三种证据前缀的行为对（文件名/符号/路由，命中与不命中）；
B. `audit()` 两个方向都会红 —— 这是这个脚本唯一有价值的地方：
   - done 项证据缺失 → 红（防“账上吹牛”）
   - todo 项证据存在 → 红（防“账本过期”，2026-11-19 就栽在这）
C. 全表当前是真绿（0 红）—— 谁把功能删了、或把账本改得对不上代码，这里立刻红。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import feature_audit as FA                                     # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        print("  FAIL  " + name + ("  | " + str(detail) if detail else ""))


print("\n【A】证据解析：三种前缀，命中与不命中")
ok, why = FA._check("file:scripts/nf_api.py")
check("A1 file: 已存在的文件 → 成立", ok, why)
ok, why = FA._check("file:scripts/没有这个文件_xyz.py")
check("A2 file: 不存在的文件 → 不成立", not ok, why)

ok, why = FA._check("sym:scripts/utils/ask.py::collect_quality_questions")
check("A3 sym: 文件里有这个符号 → 成立", ok, why)
ok, why = FA._check("sym:scripts/utils/ask.py::这个符号不存在_xyz")
check("A4 sym: 文件里没这个符号 → 不成立", not ok, why)
ok, why = FA._check("sym:scripts/没有这个文件_xyz.py::foo")
check("A5 sym: 文件本身不存在 → 不成立（不是崩溃）", not ok, why)

ok, why = FA._check("route:GET /setting/impact")
check("A6 route: 路由在 nf_api.py → 成立", ok, why)
ok, why = FA._check("route:GET /这个路由不存在_xyz")
check("A7 route: 路由不在 → 不成立", not ok, why)

ok, why = FA._check("absent:scripts::这个关键词全仓都没有_xyzq")
check("A8 absent: 全目录搜不到 → 成立（= 确实没有）", ok, why)
ok, why = FA._check("absent:scripts::def ")
check("A9 absent: 搜得到 → 不成立（= 能力已存在）", not ok, why)
ok, why = FA._check("absent:scripts/没有这个目录_xyz::foo")
check("A10 absent: 目录本身不存在 → 不成立（不许静默通过）", not ok, why)

ok, why = FA._check("瞎写的证据")
check("A11 未知前缀 → 不成立并说明", (not ok) and "未知" in why, why)


print("\n【B】双向判定：两个方向都必须会红")
orig = FA.FEATURES

# B1 done 项证据缺失 → 红
FA.FEATURES = [("T1", "假功能甲", "done",
                ["file:scripts/没有这个文件_xyz.py"], "测试用")]
rc_done = FA.audit(verbose=False)
check("B1 done 项缺证据 → 红（退出码 1）", rc_done == 1, rc_done)

# B2 done 项证据齐 → 绿
FA.FEATURES = [("T2", "假功能乙", "done", ["file:scripts/nf_api.py"], "测试用")]
rc_ok = FA.audit(verbose=False)
check("B2 done 项证据齐 → 绿（退出码 0）", rc_ok == 0, rc_ok)

# B3 todo 项用了 file: 证据 → 红（格式就不对，防路径写错假绿）
FA.FEATURES = [("T3", "假功能丙", "todo", ["file:scripts/nf_api.py"], "测试用")]
rc_todo_file = FA.audit(verbose=False)
check("B3 todo 项用 file: 证据 → 红（必须用 absent:）", rc_todo_file == 1, rc_todo_file)

# B4 todo + absent，关键词确实搜不到 → 绿
FA.FEATURES = [("T4", "假功能丁", "todo",
                ["absent:scripts::这个关键词全仓都没有_xyzq"], "测试用")]
rc_todo_ok = FA.audit(verbose=False)
check("B4 todo + absent（真没有）→ 绿", rc_todo_ok == 0, rc_todo_ok)

# B5 todo + absent，但关键词其实搜得到 → 红（“账本过期”方向）
FA.FEATURES = [("T5", "假功能戊", "todo", ["absent:scripts::def "], "测试用")]
rc_todo_hit = FA.audit(verbose=False)
check("B5 todo + absent（其实有）→ 红（账本过期）", rc_todo_hit == 1, rc_todo_hit)

# B6 partial 项一条证据都没有 → 红
FA.FEATURES = [("T6", "假功能己", "partial",
                ["file:scripts/没有这个文件_xyz.py"], "测试用")]
rc_part = FA.audit(verbose=False)
check("B6 partial 项零证据 → 红", rc_part == 1, rc_part)

# B7 frozen 项不计入红
FA.FEATURES = [("T7", "假功能庚", "frozen",
                ["file:scripts/没有这个文件_xyz.py"], "冻结")]
rc_frozen = FA.audit(verbose=False)
check("B7 frozen 项不计入（缺证据也绿）", rc_frozen == 0, rc_frozen)

FA.FEATURES = orig


print("\n【C】全表当前真绿")
rc_now = FA.audit(verbose=False)
check("C1 全表 0 红（账本与代码一致）", rc_now == 0, rc_now)
check("C2 表非空（防止被清空后空转通过）", len(FA.FEATURES) >= 15, len(FA.FEATURES))

# C3 每条都必须有证据 —— 没有证据的“做了”等于没做
empty = [f[0] for f in FA.FEATURES if not (f[3] or [])]
check("C3 每条都有证据（没有空证据的条目）", not empty, empty)

# C4 状态值只在四个里
bad_status = [(f[0], f[2]) for f in FA.FEATURES
              if f[2] not in ("done", "partial", "todo", "frozen")]
check("C4 状态值合法", not bad_status, bad_status)

# C5 todo 项的证据必须全是 absent —— 2026-11-19 的坑：todo 用 file: 指定路径，
# 路径一写错就判“确实不存在”，假绿。absent 扫整目录，记错路径也抓得到。
bad_todo = [(f[0], ev) for f in FA.FEATURES if f[2] == "todo"
            for ev in (f[3] or []) if not ev.startswith("absent:")]
check("C5 todo 项只用 absent 证据（防路径写错假绿）", not bad_todo, bad_todo)

print("\n合计：%d 通过 / %d 失败" % (passed, failed))
sys.exit(1 if failed else 0)
