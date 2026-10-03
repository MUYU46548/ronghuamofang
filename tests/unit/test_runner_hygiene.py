# -*- coding: utf-8 -*-
"""测试文件自身卫生自检：**用例必须会判定，且必须能变红**（2026-10-03）。

## 为什么要有这条护栏（用户第 3 条：补结构性用例）

`nfctl test` 只看**退出码**。于是"只会 print 的用例"永远退 0 → 被记成通过。
本轮实测抓到三个（都是真的，不是假想）：

| 文件 | 病 | 后果 |
|------|----|------|
| `test_review_parse.py` | 4 个 `print('...:', x is not None)`，零断言 | 解析器坏了也"通过"（已被 `test_review_json_repair.py` 33 断言取代 → 删除） |
| `test_imports.py` | `print(callable(...))` | 模块不可调用也"通过" |
| `test_style_v2.py` | 纯 print 的"临时验证脚本"，却被 AGENTS.md 当自检宣传 | 改成断言后才暴露：那段"对白密集"的样例用 `：` 而非 `“”` 引对白 → `dialogue_ratio` 一直是 0.0，**没人会发现** |

## 判据（结构性、可执行）

1. 有**判定构造**（任一）：本文件自定义的判定函数被调用 / `assert` / `unittest` / `pytest` /
   带布尔量的 `xxx.append((` 清单 / `FAIL += 1` 形态；
2. 有**非零退出路径**：`sys.exit(` / `raise SystemExit(` / `unittest.main()` / `pytest`；
3. **不得**出现"只 print 判定结果"而不判定的形态（`print(... is not None)` /
   `print(callable(` / `print(x in y)`）—— 这正是那三个文件的病根；
4. 每个用例的判定数 ≥ 3（防"一个 check 撑全文件"的退化）。

扫描范围 = **`nfctl test` 实际跑的两套**（`tests/unit` + `tests/http`）；
`tests/e2e` 是真人驱动的验收脚本（含大量历史 print 型脚本），只**统计不判红**。

用法：python tests/unit/test_runner_hygiene.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


DOCSTRING_RE = re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'')

# 「只会展示」的形态：把布尔判定直接 print 出来，而不据它退出
PRINT_ONLY_RE = re.compile(
    r"print\([^)]*("
    r"\bis not None\b|\bis None\b|\bcallable\(|\ball\(|\bany\(|"
    r"\bnone\b\s*\)|"
    r"[A-Za-z_][\w.\[\]'\"]*\s*(==|!=|<=|>=)\s*|"
    r"[A-Za-z_][\w.\[\]'\"]*\s+(in|not in)\s+"
    r")")

EXIT_RE = re.compile(r"\bsys\.exit\(|\braise SystemExit\(|\bunittest\.main\(\)|\bpytest\b")
# 更强的判据：**失败必须驱动退出码**（不是无条件 exit(0)，也不是"只打印状态"）。
# 三种合法形态（本仓实测都有）：
#   · `return 1 if FAIL else 0` / `sys.exit(1 if FAIL else 0)`
#   · `return 0 if ok else 1`
#   · `if FAIL:` … `return 1`
FAIL_DRIVEN_RE = re.compile(
    r"\b(?:return|sys\.exit|SystemExit)\s*\(?\s*[01]\s+if\b|"
    r"\bif\b[^\n]*\b(?:fail|failed|bad|error)\b[^\n]*:(?:.|\n){0,300}?"
    r"\b(?:return|sys\.exit|SystemExit)\s*\(?\s*1\b|"
    r"\bunittest\.main\(\)|\bpytest\b",
    re.I)
JUDGE_RE = re.compile(
    r"^\s*assert\b|"                                  # assert
    r"\bunittest\b|\bpytest\b|"                       # 框架
    r"\.append\(\(\s*[\"']|"                          # checks.append(("名字", ok))
    r"\bFAIL\s*(\+=|=)|\bfailed\s*(\+=|=)|"           # 计数器形态
    r"\bcheck\(|\bok\(|\bt\(|\bexpect\(|\bassert_",   # 常见判定函数名
    re.M)


def strip_comments_and_docstrings(src):
    """去掉 docstring 与整行注释 —— 否则"文档里举的坏例子"会被当成坏代码。"""
    src = DOCSTRING_RE.sub("", src)
    return "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#"))


def scan(p):
    raw = p.read_text(encoding="utf-8")
    code = strip_comments_and_docstrings(raw)
    # 本文件自定义的判定函数名（函数体里动过 FAIL）→ 其调用也算判定
    helpers = set()
    for m in re.finditer(r"^def (\w+)\(", code, re.M):
        body = code[m.end():m.end() + 900]
        if re.search(r"\bFAIL\b\s*(\+=|\.append)|\bFAIL\s*=\s*FAIL\s*\+", body):
            helpers.add(m.group(1))
    helper_calls = sum(len(re.findall(r"\b%s\(" % re.escape(h), code)) - 1 for h in helpers)
    n_judge = len(re.findall(r"\bcheck\(|\bok\(", code)) + helper_calls + len(
        re.findall(r"^\s*assert\b", code, re.M)) + len(re.findall(r"\.append\(\(\s*[\"']", code))
    return {
        "print_only": bool(PRINT_ONLY_RE.search(code)),
        "exit": bool(EXIT_RE.search(code)),
        "judge": bool(JUDGE_RE.search(code)) or bool(helpers),
        "n": n_judge,
        "helpers": sorted(helpers),
    }


def main():
    print("=" * 62)
    print("  测试文件卫生自检（用例必须会判定 + 能变红）")
    print("=" * 62)

    files = sorted((ROOT / "tests" / "unit").glob("test_*.py")) + \
        sorted((ROOT / "tests" / "http").glob("test_*.py"))
    check("扫到用例（unit + http，即 nfctl test 的范围）", len(files) >= 40, len(files))

    no_judge, no_exit, not_driven, thin = [], [], [], []
    counts = []
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        info = scan(p)
        if not info["judge"]:
            no_judge.append(rel)
        if not info["exit"]:
            no_exit.append(rel)
        if not FAIL_DRIVEN_RE.search(strip_comments_and_docstrings(
                p.read_text(encoding="utf-8"))):
            not_driven.append(rel)
        if info["n"] < 3:
            thin.append("%s(%d)" % (rel, info["n"]))
        counts.append((info["n"], rel))

    check("每个用例都有**判定构造**（check/ok/assert/计数器/清单）", not no_judge, no_judge)
    check("每个用例都有**非零退出路径**（sys.exit / raise SystemExit / unittest / pytest）",
          not no_exit, no_exit)
    check("**失败必须驱动退出码**（不是无条件 exit(0)，也不是只打印状态）",
          not not_driven, not_driven)
    check("每个用例的判定数 ≥ 3（防一个 check 撑全文件）", not thin, thin)

    print("\n  判定数最少的 5 个用例：")
    for n, rel in sorted(counts)[:5]:
        print("    %3d  %s" % (n, rel))

    # tests/e2e：真人驱动验收脚本，只统计（不判红）
    e2e = sorted((ROOT / "tests" / "e2e").glob("test_*.py"))
    e2e_print_only = [p.relative_to(ROOT).as_posix() for p in e2e
                      if scan(p)["print_only"]]
    print("\n  tests/e2e（不判红，仅统计）：%d 个脚本，其中 %d 个含「print 型判定行」：%s"
          % (len(e2e), len(e2e_print_only), "、".join(e2e_print_only) or "无"))
    check("tests/e2e 的 print 型判定行不再新增（基线 %d，只许降）" % len(e2e_print_only),
          len(e2e_print_only) <= 7, e2e_print_only)

    # 反证：造一个"只 print 判定"的假用例 → 本护栏必须抓得住
    tmp = ROOT / "tests" / "unit" / "test_zz_reverse_probe.py"
    try:
        tmp.write_text(
            "# -*- coding: utf-8 -*-\n"
            "import sys\n"
            "sys.path.insert(0, 'scripts')\n"
            "print('结果:', 1 == 1)\n"
            "sys.exit(0)\n", encoding="utf-8")
        probe = scan(tmp)
        check("反证：纯 print 用例被判为无判定构造", not probe["judge"], probe)
        check("反证：纯 print 用例被判为「失败不驱动退出码」",
              not FAIL_DRIVEN_RE.search(strip_comments_and_docstrings(
                  tmp.read_text(encoding="utf-8"))))
    finally:
        tmp.unlink(missing_ok=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
