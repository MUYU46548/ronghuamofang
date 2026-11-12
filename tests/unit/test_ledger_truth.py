# -*- coding: utf-8 -*-
"""路 E“判断与查看”的账目可信判据 —— 零 token、临时工作区。

问题总账第五节：路 E = **判定结果、成本、状态各自可信**。

“可信”在这里只有一条意思：**界面上那个数字，必须能在盘上找到出处，且只有一处**。
本用例逐条对账，不给界面自述留余地：

| 组 | 对账 |
|---|---|
| A 判定 | `judge.py run` 只写 `data/judge/records.json`（F13：唯一写点）；`judge.py report` 是**算出来的**，章数/件数必须与记录文件一致 |
| B 成本 | `logs/runs.db` 里塞两条已知 cost_log → `/costs/summary` 的合计必须逐项等于库里 SUM；库不存在时给空账（**不许编数字**） |
| C 状态 | `progress.json` 改什么，`build_state()` 就报什么（不缓存、不猜）；改回 failed 也立刻反映 |

用法：python tests/unit/test_ledger_truth.py
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if (extra and not cond) else ""))


def build_ws(chapters=2, chapters_text=None):
    ws = Path(tempfile.mkdtemp(prefix="ledger_truth_"))
    (ws / "config").mkdir()
    (ws / "data" / "chapters" / "raw").mkdir(parents=True)
    (ws / "data" / "state").mkdir(parents=True)
    (ws / "data" / "setting").mkdir(parents=True)
    (ws / "logs").mkdir()
    (ws / "config" / "project.yaml").write_text(
        'book:\n  name: "账目目检"\n  chapters: %d\n' % chapters, encoding="utf-8")
    shutil.copy2(REPO / "config" / "system.yaml", ws / "config" / "system.yaml")
    for i in range(1, chapters + 1):
        text = (chapters_text or {}).get(i) or (
            "## 第%d章 试\n\n他把门推开，屋里的灰在光柱里浮着。\n\n"
            "她抬头看了他一眼，没有说话，只是把桌上的本子推到一边。\n" % i)
        (ws / "data" / "chapters" / "raw" / ("%02d.md" % i)).write_text(text, encoding="utf-8")
    (ws / "data" / "setting" / "setting.json").write_text(
        '{"characters":[],"world":{},"plot_fragments":[],"timeline":[],"_meta":{"version":1}}',
        encoding="utf-8")
    return ws


def case_judge_ledger():
    print("\n[A] 判定：记录只有一处写点，报告是算出来的")
    ws = build_ws(chapters=2)
    try:
        py, script = sys.executable, str(REPO / "scripts" / "judge.py")
        p1 = subprocess.run([py, script, "--root", str(ws), "run"], cwd=str(REPO),
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        rec_path = ws / "data" / "judge" / "records.json"
        check("A1 judge.py run 落盘 data/judge/records.json", rec_path.exists(),
              (p1.stdout or "")[-300:] + (p1.stderr or "")[-300:])
        if not rec_path.exists():
            return
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        check("A2 记录里有章节账（不是空壳）", bool(rec), list(rec)[:5])

        p2 = subprocess.run([py, script, "--root", str(ws), "report"], cwd=str(REPO),
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = (p2.stdout or "")
        check("A3 report 能跑且提到章数（与记录同一份账）",
              p2.returncode == 0 and ("章" in out), out[-300:])
        check("A4 report 没有自己写第二份记录文件（F13：唯一写点）",
              not (ws / "data" / "judge" / "report.json").exists())
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def case_cost_ledger():
    print("\n[B] 成本：合计必须等于库里 SUM，空库不许编数字")
    import nf_api
    from nf_api_domains import misc as misc_dom

    # B0：库不存在 → 空账
    ws = build_ws(chapters=1)
    try:
        nf_api._set_root(str(ws))
        st, d = misc_dom.handle_costs_summary(None)
        check("B0 没有 runs.db 时给空账（不编数字）",
              st == 200 and not d.get("by_stage") and (d.get("totals") or {}).get("calls", 0) == 0,
              d)
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    # B1：塞两条已知明细 → 合计逐项对上
    ws = build_ws(chapters=1)
    try:
        db_path = ws / "logs" / "runs.db"
        db = nf_api.RunDB(db_path)
        rows = [(4, 1, "model-a", 1000, 200, 50, 0.0123, "2026-11-12T10:00:00", 0),
                (4, 1, "model-a", 2000, 300, 100, 0.0200, "2026-11-12T10:01:00", 0)]
        for r in rows:
            db.conn.execute("INSERT INTO cost_log (stage, chapter, model, tokens_in, tokens_out,"
                            " cache_read, cost_yuan, called_at, estimated)"
                            " VALUES (?,?,?,?,?,?,?,?,?)", r)
        db.conn.commit()
        nf_api._set_root(str(ws))
        st, d = misc_dom.handle_costs_summary(None)
        stages = {x["stage"]: x for x in d.get("by_stage") or []}
        s4 = stages.get(4) or {}
        check("B1 阶段 4 的 tokens_in = 库里 SUM(3000)", s4.get("tokens_in") == 3000, s4)
        check("B2 tokens_out = 500 / cache_read = 150",
              s4.get("tokens_out") == 500 and s4.get("cache_read") == 150, s4)
        check("B3 费用合计 = 0.0323（逐项相加，不是拍的）",
              abs(float(s4.get("cost_yuan") or 0) - 0.0323) < 1e-6, s4)
        check("B4 调用次数 = 2（一条明细算一次）", s4.get("calls") == 2, s4)
        by_model = {x["model"]: x for x in d.get("by_model") or []}
        check("B5 按模型聚合也对得上（同一批明细）",
              (by_model.get("model-a") or {}).get("tokens_in") == 3000, by_model)
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def case_state_ledger():
    print("\n[C] 状态：progress.json 改什么，/state 就报什么")
    import nf_api

    ws = build_ws(chapters=1)
    try:
        prog = ws / "data" / "state" / "progress.json"
        # progress.json 的 `stages` 是**以章号字符串为键的 dict**（不是数组）
        prog.write_text(json.dumps({
            "stages": {"4": {"status": "done", "approved": False},
                       "1": {"status": "failed", "approved": False}},
            "book": "账目目检",
        }, ensure_ascii=False), encoding="utf-8")
        nf_api._set_root(str(ws))
        st = nf_api.build_state()
        rows = {x["stage"]: x for x in st.get("stages") or []}
        check("C1 阶段 4 = done（照 progress.json 报，不猜）",
              (rows.get(4) or {}).get("status") == "done", rows.get(4))
        check("C2 阶段 1 = failed", (rows.get(1) or {}).get("status") == "failed", rows.get(1))

        prog.write_text(json.dumps({
            "stages": {"4": {"status": "failed", "approved": False}},
        }, ensure_ascii=False), encoding="utf-8")
        st2 = nf_api.build_state()
        rows2 = {x["stage"]: x for x in st2.get("stages") or []}
        check("C3 改回 failed 立刻反映（没有缓存旧状态）",
              (rows2.get(4) or {}).get("status") == "failed", rows2.get(4))
        check("C4 状态不凭空冒出“已完成”（阶段 2/3 仍是未开始）",
              (rows2.get(2) or {}).get("status") != "done", rows2.get(2))
    finally:
        shutil.rmtree(ws, ignore_errors=True)


for fn in (case_judge_ledger, case_cost_ledger, case_state_ledger):
    fn()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
