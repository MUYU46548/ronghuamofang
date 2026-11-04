# -*- coding: utf-8 -*-
"""stage5 改稿裁定闸的**集成**判据（2026-11-04，N1=B）。

单测锁的是 diff/合成算法；这条锁的是**真跑 run_stage**的行为：

1. 模型改稿落 `checked_pending`，`checked/` 不存在；
2. 阶段状态是 `awaiting_review`（不是 done）；
3. 作者全部采用 → 合成后 `checked/` 有模型改法；
4. 再跑一遍 → 直接 done（不再调模型、不再等）。

用法：python tests/unit/test_stage5_gate_integration.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  → " + str(detail)) if detail and not cond else ""))


PROBE = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
import stage5_check
import stage5_gate as G
from utils.progress_manager import ProgressManager

RAW = "甲段。\n\n乙段。\n\n丙段。"

class Stub:
    provider = "stub"; model = "stub"
    def write_task(self, task_dir, name, content):
        p = Path(task_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p
    def run_task(self, task_file, workdir=None, model=None, session_id=None):
        Path("data/chapters/checked_pending").mkdir(parents=True, exist_ok=True)
        Path("data/chapters/checked_pending/01.md").write_text(
            "甲段。\n\n乙段改了。\n\n丙段。\n\n新增段。", encoding="utf-8")
        Path("data/outline").mkdir(parents=True, exist_ok=True)
        Path("data/outline/check_report.md").write_text("# 报告\n- 无\n", encoding="utf-8")
        return {"exit_code": 0, "stdout_tail": "", "tokens": 0, "tokens_out": 0,
                "cache_read": 0, "cost_yuan": 0.0}

p = ProgressManager("data/state/progress.json")
ok1, msg1 = stage5_check.run_stage({}, {}, p, None, None,
                                   client=Stub(), task_dir="data/state/tasks")
st1 = (p.data.get("stages") or {}).get("5") or {}
checked1 = Path("data/chapters/checked/01.md").exists()
meta = G.load(1) or {}
items = meta.get("items") or []

G.decide(1, {it["id"]: True for it in items})
ok2, msg2, _ = G.apply_chapter(1)
txt = Path("data/chapters/checked/01.md").read_text(encoding="utf-8")

p2 = ProgressManager("data/state/progress.json")
ok3, msg3 = stage5_check.run_stage({}, {}, p2, None, None,
                                   client=Stub(), task_dir="data/state/tasks")
st3 = (p2.data.get("stages") or {}).get("5") or {}

print("__RESULT__" + json.dumps({
    "ok1": bool(ok1), "status1": st1.get("status"), "checked1": checked1,
    "items": len(items), "ok2": bool(ok2), "text_has_edit": "乙段改了" in txt,
    "text_has_new": "新增段" in txt, "ok3": bool(ok3),
    "status3": st3.get("status"),
}, ensure_ascii=False))
'''


def build_sandbox() -> Path:
    root = Path(tempfile.mkdtemp(prefix="s5int_"))
    (root / "scripts" / "utils").mkdir(parents=True)
    for n in ("stage5_check.py", "stage5_gate.py"):
        shutil.copy2(ROOT / "scripts" / n, root / "scripts" / n)
    shutil.copytree(ROOT / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    (root / "prompts").mkdir()
    shutil.copy2(ROOT / "prompts" / "stage5_check.md", root / "prompts" / "stage5_check.md")
    for sub in ("data/chapters/raw", "data/chapters/checked", "data/outline", "data/state"):
        (root / sub).mkdir(parents=True)
    (root / "data/chapters/raw/01.md").write_text("甲段。\n\n乙段。\n\n丙段。", encoding="utf-8")
    return root


def main() -> int:
    root = build_sandbox()
    try:
        (root / "_probe.py").write_text(PROBE, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.run([sys.executable, "_probe.py"], cwd=str(root),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180, env=env)
        out = p.stdout or ""
        if "__RESULT__" not in out:
            check("子进程跑通", False, (out + (p.stderr or ""))[-600:])
        else:
            d = json.loads(out.split("__RESULT__", 1)[1].splitlines()[0])
            check("第一次运行成功", d["ok1"], d)
            check("停在 awaiting_review", d["status1"] == "awaiting_review", d["status1"])
            check("checked 尚未生成", d["checked1"] is False, d["checked1"])
            check("生成了裁定项", d["items"] >= 2, d["items"])
            check("合成成功", d["ok2"], d)
            check("合成稿包含模型改法", d["text_has_edit"], d)
            check("合成稿包含新增段", d["text_has_new"], d)
            check("再跑直接完成", d["ok3"] and d["status3"] == "done", d)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
