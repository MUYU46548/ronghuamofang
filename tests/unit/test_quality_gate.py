# -*- coding: utf-8 -*-
"""质量验收闸门自检（件4，2026-09-29）。

验收（执行单 v2.2 件4）：「人为造红样例：流程被打回」。

做法：在临时项目根跑**真实** `orchestrator.run()`（全流程，各 stage 用 FakeStage
mock 掉，零 LLM），章节产物**故意造红**（过短），观察收尾闸门是否真的阻断：

  · gates.quality_gate = true  → 章节不合格 → 退出码必须为 1（打回，不盖绿灯）
  · gates.quality_gate = false → 同一份产物 → 退出码 0（开关确实生效）

同时验证：单阶段重跑（only_stage=1）**不**触发整书闸门（避免误伤）。

全程离线、零网络、零费用。
用法：python tests/unit/test_quality_gate.py
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


STAGE_MODULES = ("stage1_consolidate.py", "stage2_outline.py", "stage3_chapter_outline.py",
                 "stage4_writing.py", "stage5_check.py", "stage6_polish.py",
                 "stage7_convert.py", "stage8_markdown_export.py",
                 "snapshot.py", "material_review.py", "auto_rewrite.py",
                 "chapter_review.py", "proofread.py", "quality_checklist.py")


def build_sandbox(good_chapter):
    """搭最小可跑的临时项目根；good_chapter=True 造合格章，False 造红样例。"""
    root = Path(tempfile.mkdtemp(prefix="nf_qgate_"))
    for sub in ("scripts", "config", "data/state", "data/outline/chapters",
                "data/setting", "data/chapters/raw", "data/chapters/checked",
                "data/chapters/refined", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "scripts" / "orchestrator.py", root / "scripts" / "orchestrator.py")
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for name in STAGE_MODULES:
        src = REPO / "scripts" / name
        if src.exists():
            shutil.copy(src, root / "scripts" / name)

    # target_words=200 / chapters=1 → 门槛 100 字，便于造「刚好合格」的章节
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: 闸门测试书\n  genre: 测试\n  target_words: 200\n  chapters: 1\n",
        encoding="utf-8")
    (root / "config" / "system.yaml").write_text(
        "engine: direct\nbudget:\n  limit_yuan: 300\n  warn_ratio: 0.7\n"
        "gates:\n  require_approval: []\n  pause_on_failure: true\n  auto_retry: false\n",
        encoding="utf-8")

    if good_chapter:
        # 5 段、每段错开长度（避免「连续三行相同」被判复读），共 >100 字
        body = ""
        for i, n in enumerate((70, 60, 50, 40, 35), 1):
            body += "第" + str(i) + "段" + ("测试正文内容" * (n // 6 + 1))[:n] + "\n\n"
        text = body
    else:
        text = "太短了。\n"          # 结构检查直接判「过短 / 无段落分隔」
    (root / "data" / "chapters" / "refined" / "01.md").write_text(text, encoding="utf-8")
    return root


def run_full(root, quality_gate, only_stage="None"):
    code = """
import sys, json, os
sys.path.insert(0, r"{S}/scripts")
sys.path.insert(0, r"{S}")
os.chdir(r"{S}")
import orchestrator as O

class FakeStage:
    @staticmethod
    def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
        return True, "fake ok"

O.STAGES = {n: FakeStage for n in range(1, 10)}
_GATES = {"require_approval": [], "pause_on_failure": True, "auto_retry": False,
          "quality_gate": {QG}}
O.load_config = lambda: ({"gates": dict(_GATES), "budget": {"limit_yuan": 300, "warn_ratio": 0.7}},
                         {"book": {"name": "闸门测试书"}})
O.snap = type("S", (), {"snapshot": staticmethod(lambda lbl: None)})()
err = None
try:
    rc = O.run(from_stage=1, only_stage={ONLY})
except Exception as e:
    rc = None
    err = type(e).__name__ + ": " + str(e)
print("__RESULT__" + json.dumps({"rc": rc, "err": err}))
""".replace("{S}", str(root)).replace("{QG}", "True" if quality_gate else "False") \
   .replace("{ONLY}", only_stage)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180)
    out = proc.stdout or ""
    if "__RESULT__" not in out:
        print("  子进程无结果。stdout=%s\n  stderr=%s" % (out[-700:], (proc.stderr or "")[-700:]))
        return None, out
    payload = json.loads(out.split("__RESULT__", 1)[1].splitlines()[0])
    return payload, out


def main():
    print("=" * 70)
    print("件4 回归：质量验收闸门（人为红样例必须被打回）")
    print("=" * 70)

    # 用例 1：闸门开 + 红章节 → 必须 exit 1
    root = build_sandbox(good_chapter=False)
    p, out = run_full(root, quality_gate=True)
    if p:
        check("闸门开 + 不合格章节 → 未抛异常", p["err"] is None, p["err"])
        check("闸门开 + 不合格章节 → 退出码 1（打回，不盖绿灯）", p["rc"] == 1, "实际=%s" % p["rc"])
        check("输出出现红阻断提示", "🔴" in out and "阻断" in out, out[-200:])
    shutil.rmtree(root, ignore_errors=True)

    # 用例 2：闸门关 + 同一红章节 → exit 0（开关生效，不是硬编码）
    root = build_sandbox(good_chapter=False)
    p2, _ = run_full(root, quality_gate=False)
    if p2:
        check("闸门关 → 同一产物退出码 0（开关生效）", p2["rc"] == 0, "实际=%s" % p2["rc"])
    shutil.rmtree(root, ignore_errors=True)

    # 用例 3：闸门开 + 合格章节 → exit 0（不误伤）
    root = build_sandbox(good_chapter=True)
    p3, out3 = run_full(root, quality_gate=True)
    if p3:
        check("闸门开 + 合格章节 → 退出码 0（不误伤）", p3["rc"] == 0,
              "实际=%s / %s" % (p3["rc"], out3[-200:] if p3["rc"] else ""))
    shutil.rmtree(root, ignore_errors=True)

    # 用例 4：单阶段重跑 → 不触发整书闸门
    root = build_sandbox(good_chapter=False)
    p4, _ = run_full(root, quality_gate=True, only_stage="1")
    if p4:
        check("单阶段 --stage N 不触发整书闸门（避免误伤）", p4["rc"] == 0, "实际=%s" % p4["rc"])
    shutil.rmtree(root, ignore_errors=True)

    print("\n===== 合计: %d passed, %d failed =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
