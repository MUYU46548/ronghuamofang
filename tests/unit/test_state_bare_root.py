# -*- coding: utf-8 -*-
"""裸工作区下 /state 不该 500（问题总账 #14）—— 零 token、临时目录。

## 这条判据守的是什么

`--root` 指向一个**还没有 config/ 的目录**（工作区刚建、种子还没铺完、
或者有人拿空目录手工起了 nf_api），`load_all()` 返回 `(None, None)`。

`build_state()` 里每处读配置都写了 `(_cfg or {})` / `(_project or {})` 兜底，
唯独 `budget = load_all()[0].get("budget", {})` 漏了 —— 于是签完字、
总闸放行之后第一次 `GET /state` 就是
`AttributeError: 'NoneType' object has no attribute 'get'` → 500。

2026-11-19 用**打包 runtime 的 python 跑打包 payload 的 nf_api**、拿真正的
空目录实测到的（当时 `/state` 500、铺上 config/ 后 200）。

真实首启走不到这条路（主进程先 `seedWorkspace()` 再起 API），但：
- 用户可能自己拿 CLI 起；
- 工作区被清理 / 半途中断，也会落到这个状态。

裸目录应该**给一份空状态**（前端本来就有 `has_work` / `has_progress`
两条冷启动判据），不是 500。

A 段是源码契约（防回潮）；B 段是真调 `build_state()`。

用法：python tests/unit/test_state_bare_root.py
"""
import io
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if (extra and not cond) else ""))


# ---------- A. 源码契约：load_all()[0] 不许直接点属性 ----------

print("\n【A】load_all()[0] 必须自带兜底")

API_PY = REPO / "scripts" / "nf_api.py"
DOM_DIR = REPO / "scripts" / "nf_api_domains"

# 允许的两种写法：`(load_all()[0] or {})` / `api.load_all()[0] or {}`；
# 命中这条正则是“取出后立刻点属性”，裸配置下必炸。
BARE = re.compile(r"load_all\(\)\[0\]\s*\.")
SCAN = [API_PY] + sorted(DOM_DIR.glob("*.py"))
offenders = []
for f in SCAN:
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        continue
    for ln, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if BARE.search(line):
            offenders.append("%s:%d  %s" % (f.name, ln, stripped[:90]))

check("没有 load_all()[0] 直接点属性（%d 个文件）" % len(SCAN), not offenders,
      offenders)


# ---------- B. 行为判据：裸配置下 build_state() 给空状态 ----------

print("\n【B】load_all() 返回 (None, None) 时 build_state() 不抛")

import nf_api  # noqa: E402  （模块级会解析 ROOT，放 A 段之后导，失败信息更清楚）

old_root = nf_api.ROOT
old_load = nf_api.load_all
tmp = tempfile.mkdtemp(prefix="nf_bare_state_")
try:
    # 必须走 _set_root()：它同时刷 switch_book.DATA_DIR / project_config 等
    # 模块级派生常量。直接 `nf_api.ROOT = tmp` 会漏刷，has_work() 于是仍盯
    # 真实仓库返回 True —— 那是夹具失真，不是被测行为（今天第 N 次踩）。
    nf_api._set_root(tmp)
    nf_api.load_all = lambda: (None, None)
    st, err = None, None
    try:
        st = nf_api.build_state()
    except Exception as e:                       # noqa: BLE001
        err = "%s: %s" % (type(e).__name__, e)

    check("build_state() 不抛异常", err is None, err)
    check("空配置下 book 为空串", isinstance(st, dict) and st.get("book") == "",
          (st or {}).get("book"))
    check("空配置下字数区间回落出厂 [4000, 7200]",
          (st or {}).get("chapter_target_words") == [4000, 7200],
          (st or {}).get("chapter_target_words"))
    check("空配置下 token 上限有值（不是 None）",
          ((st or {}).get("budget") or {}).get("token_limit") is not None,
          ((st or {}).get("budget") or {}).get("token_limit"))
    # has_progress / has_work 是前端冷启动引导的判据：裸目录下必须是 False，
    # 不能是 None 也不能抛 —— None 会让前端 `v-if` 静默走错分支。
    check("has_progress 是 False（不是 None）", (st or {}).get("has_progress") is False,
          (st or {}).get("has_progress"))
    check("has_work 是 False（不是 None）", (st or {}).get("has_work") is False,
          (st or {}).get("has_work"))
    check("stages 仍然是 8 个阶段",
          isinstance((st or {}).get("stages"), list) and len((st or {})["stages"]) == 8,
          len((st or {}).get("stages") or []))
finally:
    nf_api._set_root(old_root)
    nf_api.load_all = old_load
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


print("\n======== %d 通过 / %d 失败 ========" % (len(PASS), len(FAIL)))
if FAIL:
    for n in FAIL:
        print("  失败：" + n)
sys.exit(1 if FAIL else 0)
