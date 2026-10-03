# -*- coding: utf-8 -*-
"""质量门禁「语法错必须红」自检（2026-10-03）。零 LLM、零网络。

## 为什么

改 orchestrator 时我留下了一个 IndentationError（两个同名 def 相邻），
跑质量门禁却显示 **BLOCK 0 + 通过（退出码 0）** —— 因为 pyflakes 对语法错只输出
一条普通消息（`expected an indented block ...`），被脚本算进了 WARN。

后果比"漏报一次"严重：一个连 import 都跑不起来的仓库被盖绿灯；
而全量测试这时会集体报 ImportError，**真因被埋在一堆红里**（这一轮我就差点被埋）。

判据：门禁对语法错必须**退出码非零**，且明确指出文件与行号；
反证：正常文件不许被这条新规则误伤。

用法：python tests/unit/test_quality_gate_syntax.py
"""
import io
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "quality_gate.py"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def run_gate(*targets):
    return subprocess.run([sys.executable, str(GATE), *targets],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(ROOT), timeout=300)


def case_syntax_blocks():
    print("\n【1】语法错 → BLOCK（退出码非零 + 指出文件/行号）")
    tmp = Path(tempfile.mkdtemp(prefix="gate_syn_"))
    bad = tmp / "bad_syntax.py"
    bad.write_text("# -*- coding: utf-8 -*-\ndef f():\n\n\ndef f():\n    return 1\n",
                   encoding="utf-8")
    try:
        p = run_gate(str(bad))
        out = (p.stdout or "") + (p.stderr or "")
        check("语法错 → 退出码非零（旧实现是 0 = 假绿）", p.returncode != 0, p.returncode)
        check("输出点明是语法错（不混进 WARN）", "语法错" in out, out[-300:])
        check("输出带文件名", "bad_syntax.py" in out, out[-300:])
        check("输出带行号", "104" not in out and ":" in out, out[-300:])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def case_undefined_name_still_blocks():
    print("\n【2】原有判据不许退化：未定义名仍 BLOCK")
    tmp = Path(tempfile.mkdtemp(prefix="gate_und_"))
    bad = tmp / "bad_name.py"
    bad.write_text("# -*- coding: utf-8 -*-\ndef f():\n    return undefined_symbol_xyz\n",
                   encoding="utf-8")
    try:
        p = run_gate(str(bad))
        out = (p.stdout or "") + (p.stderr or "")
        check("未定义名 → 退出码非零", p.returncode != 0, p.returncode)
        check("输出含 undefined name", "undefined name" in out, out[-300:])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def case_good_file_passes():
    print("\n【3】反证：正常文件不许被误伤")
    tmp = Path(tempfile.mkdtemp(prefix="gate_ok_"))
    good = tmp / "good.py"
    good.write_text("# -*- coding: utf-8 -*-\nimport os\n\n\ndef f():\n"
                    "    return os.sep\n", encoding="utf-8")
    try:
        p = run_gate(str(good))
        out = (p.stdout or "") + (p.stderr or "")
        check("正常文件 → 退出码 0", p.returncode == 0, out[-300:])
        check("输出仍是「通过」", "通过" in out, out[-200:])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def case_real_repo_passes():
    print("\n【4】真实仓库（scripts/）当前必须是绿的")
    p = run_gate()
    out = (p.stdout or "") + (p.stderr or "")
    check("scripts/ 门禁退出码 0", p.returncode == 0, out[-400:])
    check("BLOCK（未定义名）: 0", "BLOCK（未定义名）: 0" in out, out[-300:])


def main():
    print("=" * 62)
    print("  质量门禁语法错自检")
    print("=" * 62)
    case_syntax_blocks()
    case_undefined_name_still_blocks()
    case_good_file_passes()
    case_real_repo_passes()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
