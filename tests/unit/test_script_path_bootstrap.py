# -*- coding: utf-8 -*-
"""打包运行时「入口脚本必须自举 sys.path」护栏（2026-10-11 起）。

## 为什么需要它

安装版自带的 embeddable python 用 `python311._pth` 冻结路径：实测 sys.path
恒为 `[python311.zip, <runtime>, Lib\\site-packages]` —— **不含脚本目录，连 cwd 都没有**
（`_pth` 里的 `.` 解析的是 runtime 自身目录）。于是 `python scripts/x.py` 在打包态下：

    ModuleNotFoundError: No module named 'utils'

连 `utils.interp_guard` 自己都够不着 —— 守卫的 import 就排在被守的 import 上，
鸡生蛋，救不了。后果是**分叉**：有自举的 18 个脚本（nf_api/nfctl/…）在装机版一直正常，
缺自举的 35 个（orchestrator/approve/reject/stage1-7/refine-*/…）在装机版 CLI 全炸 ——
而后者恰好是文档与 agent 用的入口。2026-10-11 实测：同一台机器、同一份代码，
自带 python 跑 `orchestrator.py --dry-run` 崩、`.venv` python 跑正常，补自举后两态皆通。

## 判据（静态 AST，零副作用）

A. 凡 `scripts/*.py` 里存在**本地模块 import**（utils / nf_api_domains / orchestrator /
   llm_client / stageN / obsidian_bridge / approve / reject，模块级或函数内均可），
   模块级必须出现 `sys.path.insert(...)`，且位置在**首个模块级本地 import 之前**
   （函数内的本地 import 靠模块级自举在执行期兜底，但自举本身必须先于一切顶层本地导入）。
B. 功能（-P 复现）：`python -P <入口> --help` 必须退出 0。`-P` = 禁止把脚本目录塞进
   sys.path，正是打包态的行为 —— 在任何 python 上都能复现「装机版崩溃」的条件。
   只跑 `--help`：argparse 在执行任何副作用前退出，且不依赖 config/project.yaml。

全程离线、零网络、零费用、不读写项目数据。
用法：python tests/unit/test_script_path_bootstrap.py
"""
import ast
import io
import re
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


LOCAL_RE = re.compile(
    r"^(nf_api_domains|orchestrator|llm_client|obsidian_bridge|stage\d|utils|approve|reject)$")


def module_level_inserts(tree):
    """模块级 sys.path.insert 语句的行号列表。"""
    out = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if (isinstance(f, ast.Attribute) and f.attr == "insert"
                    and isinstance(f.value, ast.Attribute) and f.value.attr == "path"
                    and isinstance(f.value.value, ast.Name) and f.value.value.id == "sys"):
                out.append(node.lineno)
    return out


def first_top_local_import(tree):
    for node in tree.body:
        if isinstance(node, ast.Import) and any(
                LOCAL_RE.match(a.name.split(".")[0]) for a in node.names):
            return node.lineno
        if isinstance(node, ast.ImportFrom) and node.module and LOCAL_RE.match(
                node.module.split(".")[0]):
            return node.lineno
    return None


def has_any_local_import(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
                LOCAL_RE.match(a.name.split(".")[0]) for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and LOCAL_RE.match(
                node.module.split(".")[0]):
            return True
    return False


def main():
    print("【1】静态：入口脚本的模块级 sys.path 自举（AST 判据）")
    files = sorted(SCRIPTS.glob("*.py"))
    covered = 0
    for f in files:
        src = f.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            check(f"{f.name} 可解析", False, e)
            continue
        if not has_any_local_import(tree):
            continue                      # 纯标准库脚本不作要求
        covered += 1
        inserts = module_level_inserts(tree)
        first_local = first_top_local_import(tree)
        if first_local is None:
            ok = bool(inserts)            # 本地 import 全在函数内 → 有模块级自举即可
            detail = "无顶层本地 import，但需模块级自举" if not ok else ""
        else:
            ok = any(n < first_local for n in inserts)
            detail = f"首个顶层本地 import@L{first_local}，自举行={inserts or '无'}"
        check(f"{f.name} 自举先于本地导入", ok, detail)
    check(f"覆盖 {covered}/{len(files)} 个脚本（其余为纯标准库）", covered >= 40,
          f"covered={covered}")

    print("【2】功能：-P 复现打包态（sys.path 无脚本目录），入口 --help 必须通")
    for entry, argv in [
        ("orchestrator", ["scripts/orchestrator.py", "--help"]),
        ("approve", ["scripts/approve.py", "--help"]),
        ("reject", ["scripts/reject.py", "--help"]),
        ("nfctl", ["scripts/nfctl.py", "--help"]),
    ]:
        try:
            r = subprocess.run([sys.executable, "-P"] + argv, cwd=str(ROOT),
                               capture_output=True, text=True, timeout=120,
                               encoding="utf-8", errors="replace")
            check(f"-P {entry} {' '.join(argv[1:])}", r.returncode == 0,
                  (r.stderr or r.stdout)[:240])
        except Exception as e:  # noqa: BLE001
            check(f"-P {entry}", False, e)

    print("\n  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
