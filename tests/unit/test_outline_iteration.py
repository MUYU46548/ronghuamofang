# -*- coding: utf-8 -*-
"""大纲迭代闭环自检：收敛视图 + 成本记账（2026-09-21 新增）。

## 为什么需要这套用例

就绪度审计（同日前一轮）确认了两个缺口，都是「迭代 10~50 轮」场景的刚需：

1. **无收敛判断**：`outline_review` 只看当前绝对值，不与上一版对比 ——
   用户不知道「再迭代一轮还有没有收益」，只能凭感觉停下来。
2. **迭代成本不进账本**：`refine_outline` 把 `result["cost_yuan"]` **直接丢弃**，
   `cost_report.py` 里 `outline` 出现 0 次 —— 迭代 50 轮花了多少钱查不到。

本套用例锚定这两项的行为契约：

- 对比：问题数/碎片数/平均分的 delta、逐条目改善/退化、三种判定（改善/停滞/退化）
- 趋势：`review_series` 按版本序返回 v1..vN + 当前；`convergence_verdict` 五分支出对
- 记账：`stage=2` + `chapter=版本号`（初版是 `chapter=0`，免 schema 迁移）；
  `cost_report --by-outline` 据此把「初版」与「迭代」分开
- 不记账时也不能崩（`未记账` 明确写在消息里，不静默）

## 隔离策略

临时项目根（与项目既有策略一致）：复制 `scripts/` + `prompts/` + `config/`，
真实 `data/`、`logs/` 零污染。零 LLM 调用（`FakeClient`）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []

NEEDED = ("refine_outline.py", "outline_review.py", "outline_panel.py",
          "stage2_outline.py", "cost_report.py", "stage1_consolidate.py")


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def build_sandbox(prefix="nf_oi2_"):
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "scripts" / "utils").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    for sub in ("data/state", "data/setting", "data/outline/history",
                "data/chapters", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for n in NEEDED:
        s = REPO / "scripts" / n
        if s.exists():
            shutil.copy2(s, root / "scripts" / n)
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"),
                    dirs_exist_ok=True)
    shutil.copytree(REPO / "prompts", root / "prompts",
                    ignore=shutil.ignore_patterns("__pycache__", "history"),
                    dirs_exist_ok=True)
    for n in ("system.yaml", "project.yaml"):
        s = REPO / "config" / n
        if s.exists():
            shutil.copy2(s, root / "config" / n)
    # 空设定集：role 维度跳过，判据只看 dense/event/conflict/scene
    (root / "data" / "setting" / "setting.json").write_text(
        json.dumps({"characters": [], "world": {}, "plot_fragments": [],
                    "timeline": []}, ensure_ascii=False), encoding="utf-8")
    return root


def run_py(root, code, env_extra=None, timeout=180):
    script = root / "_case.py"
    script.write_text(code, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run([sys.executable, str(script)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           cwd=str(root), timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT"
    out = p.stdout or ""
    if "__RESULT__" in out:
        payload = out.split("__RESULT__", 1)[1].splitlines()[0]
        try:
            return json.loads(payload), out, p.stderr or ""
        except Exception:                          # noqa: BLE001
            return None, out, p.stderr or ""
    return None, out, p.stderr or ""


PRELUDE = '''
import sys, os, json
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
from utils.file_io import read_text, write_text

def _ticket(**kw):
    print("__RESULT__" + json.dumps(kw, ensure_ascii=False))
'''

# ---------------------------------------------------------------- 夹具大纲
#
# 两份自洽的大纲，key（= issues + thin）可控：
#   GOOD：1 节点 + 1 规划，条目够长且含事件/冲突/场景词 → thin=0, issues=0 → key=0
#   BAD ：0 节点 + 0 规划，预计章节数 1 → issues=2, thin=0 → key=2
# 用它们组合出「改善 / 退化 / 停滞」三种对比结论。

_LONG_NODE = ("- 节点1：第1章 露汐在沙都的医院里发现那封匿名信，"
              "与罗霄在回廊发生对峙，冲突迅速升级，她决定追查真相并封锁消息。")
_LONG_PLAN = ("- 第1章（铺垫）：露汐在沙都医院发现匿名信，"
              "与罗霄对峙后冲突升级，她决定追查并封锁消息，交代学院与沙都的关系。")

GOOD_OUTLINE = f"""# 《迭代测试书》整体大纲

## 起
露汐在沙都医院发现匿名信，决定追查。

## 承
与罗霄对峙，冲突升级。

## 转
调查触及高层，她被停职。

## 合
真相揭开，她选择留下。

## 关键节点
{_LONG_NODE}

## 预计章节数
1

## 章节规划
{_LONG_PLAN}
"""

BAD_OUTLINE = """# 《迭代测试书》整体大纲

## 起
开端。

## 承
发展。

## 转
高潮。

## 合
结局。

## 关键节点
（无）

## 预计章节数
1

## 章节规划
（无）
"""


def write_global(root, text):
    (root / "data" / "outline" / "global.md").write_text(text, encoding="utf-8")


def write_backup(root, version, text):
    (root / "data" / "outline" / "history" / f"global_v{version}.md").write_text(
        text, encoding="utf-8")


# ====================================================================== 对比

def case_compare_reads_latest_backup():
    """取编号最大的备份作为「上一版」，并与当前对比。"""
    print("\n【C1】与上一版对比（取最大版本号）")
    root = build_sandbox("nf_oi2_c1_")
    write_backup(root, 1, BAD_OUTLINE)
    write_backup(root, 2, BAD_OUTLINE)      # 故意留多个：必须选 v2
    write_global(root, GOOD_OUTLINE)
    code = PRELUDE + '''
import outline_review as ov
lb = ov.latest_backup("data/outline/history")
cmp = ov.compare_with_previous("data/outline/global.md",
                               "data/setting/setting.json",
                               history_dir="data/outline/history")
_ticket(lb_ver=(lb[0] if lb else None),
        cmp_from=cmp["from_version"] if cmp else None,
        verdict=cmp["verdict"] if cmp else None,
        deltas=cmp["deltas"] if cmp else None,
        prev_key=cmp["prev"]["key"] if cmp else None,
        cur_key=cmp["cur"]["key"] if cmp else None,
        advice=cmp["advice"] if cmp else None)
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("C1 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  lb=v{p['lb_ver']} from=v{p['cmp_from']} verdict={p['verdict']} "
          f"key {p['prev_key']}→{p['cur_key']}")
    check("C1 取到编号最大的备份（v2）", p["lb_ver"] == 2, f"→ {p['lb_ver']}")
    check("C1 对比来源是该备份", p["cmp_from"] == 2, f"→ {p['cmp_from']}")
    check("C1 主键确为 问题数+碎片数（BAD=2 → GOOD=0）",
          p["prev_key"] == 2 and p["cur_key"] == 0,
          f"→ {p['prev_key']}→{p['cur_key']}")
    check("C1 判定为 improved", p["verdict"] == "improved", f"→ {p['verdict']}")
    check("C1 deltas 含 key/issues/thin/avg_score",
          all(k in (p["deltas"] or {}) for k in
              ("key", "issues", "thin", "avg_score")), f"→ {p['deltas']}")
    check("C1 deltas[issues] 为 -2", (p["deltas"] or {}).get("issues") == -2,
          f"→ {p['deltas']}")
    shutil.rmtree(root, ignore_errors=True)


def case_compare_no_backup():
    """无备份时返回 None —— 不得假装「已对比」。"""
    print("\n【C2】无历史备份 → 不做对比")
    root = build_sandbox("nf_oi2_c2_")
    write_global(root, GOOD_OUTLINE)
    code = PRELUDE + '''
import outline_review as ov
cmp = ov.compare_with_previous("data/outline/global.md",
                               "data/setting/setting.json",
                               history_dir="data/outline/history")
_ticket(cmp_is_none=cmp is None, lb=ov.latest_backup("data/outline/history"))
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("C2 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("C2 无备份时 latest_backup 返回 None", p["lb"] is None, f"→ {p['lb']}")
    check("C2 无备份时不产出对比（不假装已对比）", p["cmp_is_none"] is True)
    shutil.rmtree(root, ignore_errors=True)


def case_compare_three_verdicts():
    """改善 / 停滞 / 退化三种判定 + 逐条目差分。"""
    print("\n【C3】三种判定与逐条目差分")
    root = build_sandbox("nf_oi2_c3_")
    code = PRELUDE + '''
import outline_review as ov

def probe(prev_text, cur_text):
    write_text("data/outline/history/global_v1.md", prev_text)
    write_text("data/outline/global.md", cur_text)
    return ov.compare_with_previous("data/outline/global.md",
                                   "data/setting/setting.json",
                                   history_dir="data/outline/history")

BAD = read_text("_bad.md")
GOOD = read_text("_good.md")
out = {}
c1 = probe(BAD, GOOD)
out["improved"] = {"verdict": c1["verdict"], "advice": c1["advice"],
                   "added": c1["entries"]["added"],
                   "removed": c1["entries"]["removed"]}
c2 = probe(GOOD, BAD)
out["regressed"] = {"verdict": c2["verdict"], "advice": c2["advice"]}
c3 = probe(BAD, BAD)
out["stalled"] = {"verdict": c3["verdict"], "advice": c3["advice"]}
_ticket(**out)
'''
    (root / "_good.md").write_text(GOOD_OUTLINE, encoding="utf-8")
    (root / "_bad.md").write_text(BAD_OUTLINE, encoding="utf-8")
    p, out, err = run_py(root, code)
    if p is None:
        check("C3 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    chk = p["improved"]
    print(f"  improved.advice={chk['advice'][:50]}")
    check("C3 问题减少 → improved", chk["verdict"] == "improved", f"→ {chk['verdict']}")
    check("C3 改善时 advice 正面（可继续）",
          "改善" in chk["advice"] or "继续" in chk["advice"], f"→ {chk['advice'][:50]}")
    check("C3 逐条目差分识别出新增（节点1/第1章）",
          any("节点1" in t for t in chk["added"]) or any("第1章" in t for t in chk["added"]),
          f"→ added={chk['added']}")
    r = p["regressed"]
    check("C3 问题增加 → regressed", r["verdict"] == "regressed", f"→ {r['verdict']}")
    check("C3 退化时 advice 指向回退（含版本号/恢复）",
          "回退" in r["advice"] and ("v1" in r["advice"] or "恢复" in r["advice"]),
          f"→ {r['advice'][:60]}")
    s = p["stalled"]
    check("C3 内容相同 → stalled", s["verdict"] == "stalled", f"→ {s['verdict']}")
    check("C3 停滞时 advice 点明收益递减/换策略/开工",
          any(w in s["advice"] for w in ("递减", "开工", "换")),
          f"→ {s['advice'][:60]}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== 趋势

def case_series_and_verdicts():
    """review_series 按版本序返回；convergence_verdict 五分支出对。"""
    print("\n【C4】趋势序列 + 收敛五分支出对")
    root = build_sandbox("nf_oi2_c4_")
    write_backup(root, 1, BAD_OUTLINE)
    write_backup(root, 2, BAD_OUTLINE)
    write_global(root, GOOD_OUTLINE)
    code = PRELUDE + '''
import outline_review as ov

series = ov.review_series("data/outline/global.md", "data/setting/setting.json",
                          history_dir="data/outline/history")

# 纯函数：用合成指纹直接验五分支出对（它只读 key/total）
def fp(key, total=1):
    return {"key": key, "total": total}

out = {}
out["series_labels"] = [e["label"] for e in series]
out["series_keys"] = [e["key"] for e in series]
out["verdicts"] = {
    "insufficient": ov.convergence_verdict([fp(2)])[0],
    "done": ov.convergence_verdict([fp(2), fp(0)])[0],
    "stalled": ov.convergence_verdict([fp(3), fp(3), fp(3)])[0],
    "improving": ov.convergence_verdict([fp(5), fp(4), fp(3)])[0],
    "mixed": ov.convergence_verdict([fp(3), fp(5), fp(4)])[0],
}
out["notes"] = {
    "done": ov.convergence_verdict([fp(2), fp(0)])[1],
    "stalled": ov.convergence_verdict([fp(3), fp(3), fp(3)])[1],
    "mixed": ov.convergence_verdict([fp(3), fp(5), fp(4)])[1],
}
_ticket(**out)
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("C4 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  labels={p['series_labels']}  keys={p['series_keys']}")
    check("C4 序列按版本序 + 当前在末位",
          p["series_labels"] == ["v1（第1轮前）", "v2（第2轮前）", "当前"],
          f"→ {p['series_labels']}")
    check("C4 序列 key 反映各版本（BAD,BAD,GOOD → 2,2,0）",
          p["series_keys"] == [2, 2, 0], f"→ {p['series_keys']}")
    v = p["verdicts"]
    check("C4 样本不足 → insufficient", v["insufficient"] == "insufficient", f"→ {v}")
    check("C4 最新 key=0 → done", v["done"] == "done", f"→ {v['done']}")
    check("C4 最近 3 轮 key 全同 → stalled", v["stalled"] == "stalled", f"→ {v['stalled']}")
    check("C4 key 严格递减 → improving", v["improving"] == "improving",
          f"→ {v['improving']}")
    check("C4 有升有降 → mixed", v["mixed"] == "mixed", f"→ {v['mixed']}")
    check("C4 done 说明含「可以开工」", "开工" in p["notes"]["done"],
          f"→ {p['notes']['done'][:50]}")
    check("C4 stalled 说明含「递减」", "递减" in p["notes"]["stalled"],
          f"→ {p['notes']['stalled'][:50]}")
    check("C4 mixed 说明指向换策略而非微调",
          "策略" in p["notes"]["mixed"], f"→ {p['notes']['mixed'][:60]}")
    shutil.rmtree(root, ignore_errors=True)


def case_trend_cli():
    """`outline_review --trend` 打出表格与判定。"""
    print("\n【C5】`--trend` CLI 输出")
    root = build_sandbox("nf_oi2_c5_")
    write_backup(root, 1, BAD_OUTLINE)
    write_global(root, GOOD_OUTLINE)
    out = _run_cli(root, ["scripts/outline_review.py", "--trend", "--quiet"])
    check("C5 --trend 输出趋势表头", "迭代趋势" in out, f"→ {out[-300:]}")
    check("C5 --trend 列出各版本行", "v1（第1轮前）" in out and "当前" in out,
          f"→ {out[-300:]}")
    check("C5 --trend 给出收敛判定", ("已收敛" in out or "停滞" in out
                                      or "改善中" in out or "波动" in out),
          f"→ {out[-300:]}")
    check("C5 --trend 不写报告文件（quiet）",
          not (root / "data" / "outline" / "review_report.md").exists())
    shutil.rmtree(root, ignore_errors=True)


def case_compare_cli_default():
    """默认（不带 --trend）也打印与上一版对比。"""
    print("\n【C6】默认打印版本对比")
    root = build_sandbox("nf_oi2_c6_")
    write_backup(root, 1, BAD_OUTLINE)
    write_global(root, GOOD_OUTLINE)
    out = _run_cli(root, ["scripts/outline_review.py", "--quiet"])
    check("C6 默认输出含「与上一版对比」", "与上一版对比" in out, f"→ {out[-300:]}")
    check("C6 对比里给出 delta", "→" in out and ("问题" in out), f"→ {out[-300:]}")
    check("C6 对比里给出建议", "→" in out, f"→ {out[-300:]}")
    out2 = _run_cli(root, ["scripts/outline_review.py", "--quiet", "--no-compare"])
    check("C6 --no-compare 可关闭对比", "与上一版对比" not in out2, f"→ {out2[-200:]}")
    shutil.rmtree(root, ignore_errors=True)


def _run_cli(root, argv):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(root), env=env,
                       timeout=180)
    return (p.stdout or "") + (p.stderr or "")


# ====================================================================== 成本

def case_refine_charges_cost():
    """refine_outline 把成本写进 cost_log：stage=2 + chapter=版本号。

    ⚠️ 必须显式 patch `FakeClient.run_task`：它对 `stage2*` 任务会调
    `_write_global`，把 `global.md` **覆写**成罐头大纲（预计章节数 3、只有 1 个节点），
    于是体检判 FAIL（节点 1 < 3），用例拿不到成功路径。
    这是本项目反复记的坑：**FakeClient 会重写产物**，验证时必须自己接管。
    这里让它写回期望的 GOOD_OUTLINE，同时保留真实 token 返回以驱动成本计算。
    """
    print("\n【C7】精修成本记账（stage=2 / chapter=版本号）")
    root = build_sandbox("nf_oi2_c7_")
    write_global(root, GOOD_OUTLINE)
    (root / "_good.md").write_text(GOOD_OUTLINE, encoding="utf-8")
    code = PRELUDE + '''
import yaml
from utils.fake_client import FakeClient
from utils.db import RunDB
from utils.cost_tracker import CostTracker
import refine_outline as ro

cfg = yaml.safe_load(read_text("config/system.yaml"))
proj = yaml.safe_load(read_text("config/project.yaml"))

# 接管 stage2_refine：写回期望大纲，保留真实 token（驱动成本记账）
import utils.fake_client as fc
GOOD = read_text("_good.md")
_orig = fc.FakeClient.run_task
def patched(self, task_file, workdir=None, model=None):
    if str(task_file).split(os.sep)[-1].startswith("stage2"):
        write_text("data/outline/global.md", GOOD)
        return {"exit_code": 0, "stdout_tail": "", "tokens": 20000,
                "tokens_out": 6000, "cost_yuan": 0.0310, "estimated": True,
                "provider": "fake", "model": "fake", "requests": 1}
    return _orig(self, task_file, workdir=workdir, model=model)
fc.FakeClient.run_task = patched

db = RunDB("logs/runs.db")
cost = CostTracker(db, limit_yuan=300, warn_ratio=0.7)
run_id = db.start_run(plan_json="outline_refine_cli")

ok, msg = ro.run_refine(cfg, proj, "加强第1章的冲突张力", client=FakeClient(),
                        task_dir="data/state/tasks", db=db, cost=cost, run_id=run_id)
db.finish_run(run_id, "done" if ok else "failed")

rows = db.conn.execute(
    "SELECT stage, chapter, tokens_in, tokens_out, cost_yuan, run_id, model"
    " FROM cost_log ORDER BY id").fetchall()
runs = db.conn.execute("SELECT id, status, plan_json FROM runs").fetchall()
db.close()
_ticket(ok=ok, msg=msg,
        rows=[list(r) for r in rows], runs=[list(r) for r in runs])
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("C7 子进程产出结果", False, f"{out[-500:]} {err[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} rows={p['rows']} runs={p['runs']}")
    check("C7 精修成功", p["ok"] is True, f"→ {p['msg'][:60]}")
    check("C7 **cost_log 有记账行**（旧行为：0 行）", len(p["rows"]) == 1,
          f"→ {len(p['rows'])} 行")
    if p["rows"]:
        st, ch, ti, to, c, rid, model = p["rows"][0]
        check("C7 stage=2（沿用整体大纲阶段号）", st == 2, f"→ {st}")
        check("C7 chapter=版本号（本轮是首轮精修 → 1）", ch == 1, f"→ {ch}")
        check("C7 记录了 token 且费用 > 0", ti > 0 and to > 0 and c > 0,
              f"→ in={ti} out={to} cost={c}")
        check("C7 关联到本次 run_id", rid == p["runs"][0][0], f"→ {rid}")
    check("C7 runs 表留下运行记录且标 done",
          p["runs"] and p["runs"][0][1] == "done", f"→ {p['runs']}")
    check("C7 运行记录标明来源 CLI",
          p["runs"] and p["runs"][0][2] == "outline_refine_cli", f"→ {p['runs']}")
    check("C7 成功消息里带上本轮费用", "费用" in p["msg"], f"→ {p['msg'][:80]}")
    shutil.rmtree(root, ignore_errors=True)


def case_refine_without_db_ok():
    """不传 db/cost 时不得崩，且明确写「未记账」（不静默丢账）。"""
    print("\n【C8】未提供 db/cost → 不崩且明确未记账")
    root = build_sandbox("nf_oi2_c8_")
    write_global(root, GOOD_OUTLINE)
    (root / "_good.md").write_text(GOOD_OUTLINE, encoding="utf-8")
    code = PRELUDE + '''
import yaml
from utils.fake_client import FakeClient
import refine_outline as ro

cfg = yaml.safe_load(read_text("config/system.yaml"))
proj = yaml.safe_load(read_text("config/project.yaml"))

# 同上：接管 stage2_refine，否则 FakeClient 覆写 global.md 导致体检 FAIL
import utils.fake_client as fc
GOOD = read_text("_good.md")
_orig = fc.FakeClient.run_task
def patched(self, task_file, workdir=None, model=None):
    if str(task_file).split(os.sep)[-1].startswith("stage2"):
        write_text("data/outline/global.md", GOOD)
        return {"exit_code": 0, "stdout_tail": "", "tokens": 20000,
                "tokens_out": 6000, "cost_yuan": 0.0310, "estimated": True,
                "provider": "fake", "model": "fake", "requests": 1}
    return _orig(self, task_file, workdir=workdir, model=model)
fc.FakeClient.run_task = patched

ok, msg = ro.run_refine(cfg, proj, "随便改改", client=FakeClient(),
                        task_dir="data/state/tasks")
_ticket(ok=ok, msg=msg)
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("C8 子进程产出结果", False, f"{out[-500:]} {err[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("C8 未记账时不崩且成功", p["ok"] is True, f"→ {p['msg'][:60]}")
    check("C8 消息明确标注「未记账」（不静默）", "未记账" in p["msg"],
          f"→ {p['msg'][:80]}")
    shutil.rmtree(root, ignore_errors=True)


def case_cost_report_by_outline():
    """cost_report --by-outline 把初版与迭代分开，并给累计与均值。"""
    print("\n【C9】`cost_report --by-outline` 展示迭代维度")
    root = build_sandbox("nf_oi2_c9_")
    code = PRELUDE + '''
from utils.db import RunDB
db = RunDB("logs/runs.db")
rid = db.start_run(plan_json="probe")
# 初版（chapter=0，stage2_outline 的口径）+ 三轮迭代
db.log_cost(rid, 2, 0, "fake", 10000, 3000, 0.0300)
db.log_cost(rid, 2, 1, "fake", 12000, 4000, 0.0360)
db.log_cost(rid, 2, 2, "fake", 13000, 4500, 0.0400)
db.log_cost(rid, 2, 3, "fake", 11000, 3800, 0.0330)
db.log_cost(rid, 4, 1, "fake", 50000, 20000, 0.1500)
db.finish_run(rid, "done")
db.close()
print("__RESULT__" + json.dumps({"ok": True}))
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("C9 子进程产出结果", False, f"{out[-500:]} {err[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    out2 = _run_cli(root, ["scripts/cost_report.py", "--by-outline"])
    print(f"  {out2.strip()[-260:]}")
    check("C9 列出初版行 v0", "v0(初版)" in out2, f"→ {out2[-250:]}")
    check("C9 列出各迭代版本 v1/v2/v3",
          all(f"v{i}" in out2 for i in (1, 2, 3)), f"→ {out2[-250:]}")
    check("C9 给出累计列", "累计" in out2, f"→ {out2[-250:]}")
    check("C9 汇总迭代轮次", "迭代轮次: 3" in out2, f"→ {out2[-250:]}")
    check("C9 汇总平均每轮", "平均每轮" in out2, f"→ {out2[-250:]}")
    check("C9 全文费用不含阶段4 的 0.15（只统计 stage=2）",
          "0.1500" not in out2, f"→ 混入了其他阶段")

    ov = _run_cli(root, ["scripts/cost_report.py"])
    check("C9 总览含「阶段2 细分（初版 vs 迭代）」",
          "阶段2 细分" in ov, f"→ {ov[-300:]}")
    check("C9 总览区分初版生成 / 大纲迭代",
          "初版生成" in ov and "大纲迭代" in ov, f"→ {ov[-300:]}")
    shutil.rmtree(root, ignore_errors=True)


def main():
    print("=" * 70)
    print("大纲迭代闭环自检：收敛视图 + 成本记账（临时项目根，零 LLM 调用）")
    print("=" * 70)
    for fn in (case_compare_reads_latest_backup, case_compare_no_backup,
               case_compare_three_verdicts, case_series_and_verdicts,
               case_trend_cli, case_compare_cli_default,
               case_refine_charges_cost, case_refine_without_db_ok,
               case_cost_report_by_outline):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            import traceback
            FAIL.append(fn.__name__)
            print(f"  [FAIL] {fn.__name__} 抛异常: {e}")
            traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        for f in FAIL:
            print(f"  FAIL: {f}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
