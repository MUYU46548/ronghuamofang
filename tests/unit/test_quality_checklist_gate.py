# -*- coding: utf-8 -*-
"""质量验收 checklist「产物缺失必须失败」自检（2026-10-03）。零 LLM、零网络。

## 为什么（用户拍板）

> 章节文件不存在，如果是跑了但没落盘，当然不是预期的结果。这绝对有问题。

旧实现（`quality_checklist.py`）对缺失章节只打一行 `[SKIP] 文件不存在` 就 `continue`，
`all_pass` 保持 True → 末尾照打「**全部章节通过验收 ✓**」并 **退 0**。
而本脚本是 orchestrator 的收尾闸门（`gates.quality_gate`，默认开）——
"跑了但没落盘"这种最该拦的情形，恰恰被盖了绿灯。

另一个同类假绿：`chapters: 0` 时循环体一次都不执行 → 空集合"全过"。

判据：产物缺失 → **非零退出 + 指出两个候选路径 + 说明含义**；
`--allow-missing` 是**显式**逃生门，且届时摘要必须写"未经验收"，不许说"通过"。

用法：python tests/unit/test_quality_checklist_gate.py
"""
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


GOOD_BODY = (
    "## 第1章 测试\n\n"
    "苏芷推门进来，把伞靠在门边，抖了抖袖子上的水。屋里有炭火的气味。\n\n"
    "她没有立刻说话，先走到炉边烤了烤手，火光在她脸上跳动。\n\n"
    "“回来了。”祁砚把茶盏推过来，语气平常得像什么都没有发生。\n\n"
    "她应了一声，坐下来，把路上遇见的那些人和事一件件说给他听。\n\n"
    "夜很长，他们谁也没有急着把话说完。窗外的雨一直下到天亮。\n"
)


def build_root(chapters, with_files, chapter_count=None):
    """临时项目根：scripts/quality_checklist.py + config + data/chapters/refined。"""
    root = Path(tempfile.mkdtemp(prefix="nf_qcheck_"))
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "quality_checklist.py",
                 root / "scripts" / "quality_checklist.py")
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    (root / "config").mkdir()
    (root / "config" / "project.yaml").write_text(
        'book:\n  name: "验收测试书"\n  target_words: 400\n  chapters: %d\n'
        % (chapter_count if chapter_count is not None else chapters),
        encoding="utf-8")
    (root / "data" / "setting").mkdir(parents=True)
    (root / "data" / "chapters" / "refined").mkdir(parents=True)
    (root / "data" / "chapters" / "raw").mkdir(parents=True)
    (root / "data" / "outline" / "chapters").mkdir(parents=True)
    for n in (with_files or []):
        (root / "data" / "chapters" / "refined" / ("%02d.md" % n)).write_text(
            GOOD_BODY.replace("第1章", "第%d章" % n), encoding="utf-8")
    return root


def run(root, *args):
    return subprocess.run([PY, str(root / "scripts" / "quality_checklist.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(root), timeout=120)


def case_missing_fails():
    print("\n【1】产物缺失 → FAIL（旧实现是 SKIP + 退 0 + 「全部通过」）")
    root = build_root(3, with_files=[1, 2])
    try:
        p = run(root)
        out = p.stdout
        check("退出码非零（闸门真的会拦）", p.returncode == 1, p.returncode)
        check("点明是**产物缺失**而不是普通不合格", "产物缺失" in out, out[-300:])
        check("指出两个候选路径（refined 与 raw）",
              "data/chapters/refined/03.md" in out and "data/chapters/raw/03.md" in out,
              out[-400:])
        check("解释含义（跑过没落盘 = 失败）", "没落盘" in out, out[-300:])
        check("**不许**再出现「全部章节通过验收」", "全部章节通过验收" not in out, out[-200:])
        check("摘要说明有几章缺失", "产物缺失" in out and "3" in out, out[-200:])
        check("已有的两章仍逐项检查（缺失不吞掉其它检查）",
              "第1章: [PASS]" in out and "第2章: [PASS]" in out, out[:300])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_allow_missing_is_explicit():
    print("\n【2】--allow-missing：显式放行，但摘要必须说「未经验收」")
    root = build_root(3, with_files=[1])
    try:
        p = run(root, "--allow-missing")
        out = p.stdout
        check("退出码 0（逃生门生效）", p.returncode == 0, p.returncode)
        check("缺失章节标为 SKIP 且写明未验收",
              "[SKIP]" in out and "未经验收" in out, out[-300:])
        check("摘要**不说**「通过」（跳过 ≠ 通过）",
              "全部章节通过验收" not in out, out[-200:])
        check("摘要点出缺失章号", "2" in out and "3" in out, out[-200:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_all_present_passes():
    print("\n【3】反证：产物齐全且合格 → 仍然退 0 + 「通过」")
    root = build_root(2, with_files=[1, 2])
    try:
        p = run(root)
        out = p.stdout
        check("退出码 0", p.returncode == 0, out[-300:])
        check("输出「全部章节通过验收 ✓」", "全部章节通过验收" in out, out[-200:])
        check("两章都是 PASS", out.count("[PASS]") == 2, out.count("[PASS]"))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_zero_chapters_fails():
    print("\n【4】空集合不许「全过」（vacuous truth 是门禁最阴的假绿）")
    root = build_root(0, with_files=[], chapter_count=0)
    try:
        p = run(root)
        out = p.stdout
        check("chapters=0 → 退出码非零", p.returncode == 1, (p.returncode, out[-200:]))
        check("报错说明没有可验收的章节", "没有可验收的章节" in out, out[-200:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_orchestrator_blocks_on_nonzero():
    print("\n【5】结构性：orchestrator 的收尾闸门确实读这个退出码并阻断")
    src = (REPO / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("orchestrator 起 quality_checklist", "scripts/quality_checklist.py" in src)
    check("用子进程退出码判定", "subprocess.call([sys.executable, \"scripts/quality_checklist.py\"])" in src)
    check("非零 → _finalize(failed, 1)（阻断完成状态，不盖绿灯）",
          'if rc != 0' in src and '_finalize("failed", 1)' in src)
    check("只在全流程收尾跑（单阶段重跑不误伤）",
          'if only_stage is None and cfg.get("gates", {}).get("quality_gate", True):' in src)


def main():
    print("=" * 62)
    print("  质量验收 checklist「产物缺失必失败」自检")
    print("=" * 62)
    case_missing_fails()
    case_allow_missing_is_explicit()
    case_all_present_passes()
    case_zero_chapters_fails()
    case_orchestrator_blocks_on_nonzero()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
