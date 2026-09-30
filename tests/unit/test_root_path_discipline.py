# -*- coding: utf-8 -*-
"""API 层「路径一律经 ROOT」护栏（2026-09-29 起）。

## 为什么需要它

`nf_api` 支持 `--root`（GUI 切书档 / 自动化验收用夹具项目）。此时**进程 CWD 未必是数据根**，
而 API 代码里只要有裸的相对路径（`"data/state/progress.json"` 这种），就会**静默读写另一个项目**：
返回 200、内容却是别人的，不报错、极难发现。历史上已抓到两次：

1. `build_state()` 写死相对 `data/state/progress.json` → `/state` 返回**主项目**的阶段状态；
2. `GLOBAL = "data/outline/global.md"` 这类模块级常量：它在 import 时固化，只 `global ROOT`
   不会刷新 → `--root` 下大纲相关端点仍盯着旧项目。

第 2 条尤其阴：它不是"某处忘了拼 ROOT"，而是"拼了、但拼的是**旧 ROOT**"。
故本护栏由三条判据组成，全部可执行：

A. **静态**：`nf_api.py` / `nf_api_domains/*.py` 里，凡把相对数据路径字面量**直接当函数实参**
   传的，该行必须出现 `ROOT`（即已拼接）或显式豁免 `# noqa: root: <理由>`。
   （放在 dict/list 里的展示用字符串不在此列 —— 它们不参与文件读写。）
B. **静态**：`global ROOT` 只允许出现在 `_set_root()` 里 —— 绕过它就会留下未刷新的派生常量。
C. **功能（反证）**：`_set_root(夹具)` 后 GLOBAL/HISTORY_DIR 必须跟着走，且 `build_state()`
   必须读到**夹具的书名**；`_set_root` 复原后必须读回主项目。没有 C，A/B 只能证明"写法合规"，
   证明不了"真的换了项目"。

全程离线、零网络、零费用。
用法：python tests/unit/test_root_path_discipline.py
"""
import ast
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


# 数据根下的顶层目录名（出现这些前缀的字符串 = 路径语义）
ROOT_PATH_RE = re.compile(r"^(data|config|materials|logs|output|history|prompts|templates)/")
NOQA_RE = re.compile(r"#\s*noqa:\s*root[:：]\s*(\S.*)$")

TARGET_FILES = [ROOT / "scripts" / "nf_api.py"] + sorted(
    (ROOT / "scripts" / "nf_api_domains").glob("*.py"))


def _sources():
    for f in TARGET_FILES:
        yield f, f.read_text(encoding="utf-8")


def test_A_no_bare_path_argument():
    print("\n[A] 相对数据路径不得裸传给函数（--root 下会静默读写别的项目）")
    violations, exemptions = [], []
    for f, src in _sources():
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                if not ROOT_PATH_RE.match(arg.value):
                    continue
                line = lines[arg.lineno - 1] if arg.lineno <= len(lines) else ""
                m = NOQA_RE.search(line)
                if m:
                    exemptions.append("%s:%d %s" % (f.name, arg.lineno, m.group(1)[:40]))
                elif "ROOT" not in line:
                    violations.append("%s:%d  %s" % (f.name, arg.lineno, line.strip()[:90]))
    check("没有被裸传的相对数据路径", not violations,
          "；".join(violations[:6]) if violations else "")
    print("     豁免项（noqa: root，均带理由）: %s" % (exemptions or "无"))


def test_B_global_root_only_in_set_root():
    print("\n[B] global ROOT 只允许出现在 _set_root()（否则派生常量不会刷新）")
    bad = []
    for f, src in _sources():
        tree = ast.parse(src)
        for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Global) and "ROOT" in sub.names and fn.name != "_set_root":
                    bad.append("%s:%d %s()" % (f.name, sub.lineno, fn.name))
    check("没有绕过 _set_root 直接改 ROOT", not bad, "；".join(bad))


def test_C_functional_switch():
    print("\n[C] 反证：_set_root 后派生常量与 build_state 真跟着换项目")
    import nf_api

    fixture = ROOT / "tests" / "e2e" / "fixture_project"
    orig = nf_api.ROOT
    try:
        nf_api._set_root(fixture)
        check("GLOBAL 跟着 ROOT 走（不是 import 时固化的旧值）",
              nf_api.GLOBAL == str(fixture / "data" / "outline" / "global.md"), nf_api.GLOBAL)
        check("HISTORY_DIR 跟着 ROOT 走",
              nf_api.HISTORY_DIR == str(fixture / "data" / "outline" / "history"),
              nf_api.HISTORY_DIR)
        st = nf_api.build_state()
        check("夹具 root 下 build_state 读到夹具书名",
              st.get("book") == "e2e 夹具书", st.get("book"))
        check("夹具 root 下阶段状态来自夹具 progress.json（1-4 done）",
              [s["status"] for s in st.get("stages", [])[:4]] == ["done"] * 4,
              [s["status"] for s in st.get("stages", [])])
    finally:
        nf_api._set_root(orig)

    st2 = nf_api.build_state()
    check("复原后读回主项目（反证：上一条不是碰巧读到夹具）",
          st2.get("book") != "e2e 夹具书", st2.get("book"))
    check("复原后 GLOBAL 回到主项目",
          nf_api.GLOBAL == str(Path(orig).resolve() / "data" / "outline" / "global.md"),
          nf_api.GLOBAL)


def test_D_corrupt_noqa():
    print("\n[D] noqa 豁免必须带理由（防止拿它当万能挡箭牌）")
    bare = []
    for f, src in _sources():
        for i, line in enumerate(src.splitlines(), 1):
            if "noqa: root" in line and not NOQA_RE.search(line):
                bare.append("%s:%d %s" % (f.name, i, line.strip()[:60]))
    check("没有无理由的 noqa: root", not bare, "；".join(bare))


def main():
    print("=" * 62)
    print("  API 层路径纪律护栏（离线）")
    print("=" * 62)
    test_A_no_bare_path_argument()
    test_B_global_root_only_in_set_root()
    test_C_functional_switch()
    test_D_corrupt_noqa()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
