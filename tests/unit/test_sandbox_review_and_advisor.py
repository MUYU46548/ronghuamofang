# -*- coding: utf-8 -*-
"""沙盒审核状态机 + 大纲回写 + 开工方向建议 自检（2026-09-21）。

## 为什么需要这套用例

就绪度审计确认了三个缺口，本套用例锚定它们的**行为契约**：

1. **沙盒没有审核状态**：此前只是往目录丢 .md，用户无法区分「新生成的待审稿」
   与「已确认可粘贴的稿」，也没有「驳回」这个动作。
2. **回写只有完书后通道**：大纲迭代过程中无法把成果推出来审阅。
3. **迭代后不给开工方向**：只有 verdict + THIN 清单，没有「接下来能做什么」。

## 三条最容易写错、也最要紧的契约

- **改过的文件必须重新审核**：登记带内容 sha256，内容变了状态重置 pending。
  否则产物重新生成后仍显示「已通过」——等于让旧审核为新内容背书。
- **只写沙盒、绝不碰 `data/outline/global.md`**：导出是旁路，产物本身只读。
- **建议必须可执行且零 token**：每条动作给 `next_step`（真实端点/命令），
  且不调用任何 LLM。

## 隔离策略

临时项目根，真实 `data/`、`logs/` 零污染；零 LLM 调用。
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

NEEDED = ("obsidian_bridge.py", "obsidian_integrate.py", "outline_review.py",
          "outline_export.py", "outline_advisor.py", "sandbox_review.py",
          "refine_outline.py", "outline_panel.py", "stage2_outline.py",
          "stage1_consolidate.py")


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def build_sandbox(prefix="nf_sra_"):
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
    (root / "data" / "setting" / "setting.json").write_text(
        json.dumps({"characters": [], "world": {}, "plot_fragments": [],
                    "timeline": []}, ensure_ascii=False), encoding="utf-8")
    return root


def run_py(root, code, timeout=180):
    script = root / "_case.py"
    script.write_text(code, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
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


def run_cli(root, argv, timeout=180):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable] + argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(root),
                       env=env, timeout=timeout)
    return (p.stdout or "") + (p.stderr or "")


PRELUDE = '''
import sys, os, json
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
from utils.file_io import read_text, write_text

def _ticket(**kw):
    print("__RESULT__" + json.dumps(kw, ensure_ascii=False))
'''

# ---------------------------------------------------------------- 夹具大纲
#
# BAD：0 节点 + 0 规划 → issues=2（key=2）
# THIN：1 节点 + 1 规划但都很短 → thin=2, issues=0（key=2）
# GOOD：1 节点 + 1 规划且够长 → key=0
_LONG_NODE = ("- 节点1：第1章 露汐在沙都的医院里发现那封匿名信，"
              "与罗霄在回廊发生对峙，冲突迅速升级，她决定追查真相并封锁消息。")
_LONG_PLAN = ("- 第1章（铺垫）：露汐在沙都医院发现匿名信，与罗霄对峙后冲突升级，"
              "她决定追查并封锁消息，交代学院与沙都的关系。")

GOOD_OUTLINE = f"""# 《测试书》整体大纲

## 起
露汐在沙都医院发现匿名信。

## 承
与罗霄对峙。

## 转
调查触及高层。

## 合
真相揭开。

## 关键节点
{_LONG_NODE}

## 预计章节数
1

## 章节规划
{_LONG_PLAN}
"""

# 素材覆盖维度：角色名在 normalized 素材里出现 ≥3 次才算 OK；
# 这里不建 normalized 目录，于是「角色」维度会被判缺 —— 但本夹具不写角色名，
# 所以重点是「篇幅过短 / 无场景锚点」这类可精修的维度。
THIN_OUTLINE = """# 《测试书》整体大纲

## 起
开端。

## 承
发展。

## 转
高潮。

## 合
结局。

## 关键节点
- 节点1：第1章 露汐继续。

## 预计章节数
1

## 章节规划
- 第1章：继续。
"""

BAD_OUTLINE = """# 《测试书》整体大纲

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


# ================================================================== A 审核状态机

def case_register_and_status():
    """登记 → 待审；审核 → 通过/驳回；未登记路径明确报错。"""
    print("\n【A1】登记与审核流转")
    root = build_sandbox("nf_sra_a1_")
    (root / "data" / "state" / "obsidian_sandbox").mkdir(parents=True, exist_ok=True)
    code = PRELUDE + '''
from utils import sandbox_review as srv
SB = "data/state/obsidian_sandbox"
write_text(SB + "/提案A.md", "内容 A")
write_text(SB + "/提案B.md", "内容 B")

e1, ch1 = srv.register("提案A.md", SB, kind="outline_proposal", source="t")
e2, ch2 = srv.register("提案B.md", SB, kind="chapter_draft", source="t")
st0 = srv.stats()
ok_a, msg_a = srv.set_status("提案A.md", srv.APPROVED, note="看着不错")
ok_b, msg_b = srv.set_status("提案B.md", srv.REJECTED, note="与设定冲突")
ok_c, msg_c = srv.set_status("不存在.md", srv.APPROVED)
st1 = srv.stats()
items = srv.list_items()
_ticket(ch=(ch1, ch2), st0=st0, st1=st1,
        ok_a=ok_a, msg_a=msg_a, ok_b=ok_b, msg_b=msg_b,
        ok_c=ok_c, msg_c=msg_c,
        statuses={e["path"]: e["status"] for e in items},
        notes={e["path"]: e["note"] for e in items},
        kinds={e["path"]: e["kind"] for e in items})
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("A1 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("A1 新登记均为 pending", p["st0"] == {"pending": 2, "approved": 0, "rejected": 0},
          f"→ {p['st0']}")
    check("A1 登记返回 changed=True", p["ch"] == [True, True], f"→ {p['ch']}")
    check("A1 approve 成功且记录备注",
          p["ok_a"] and "已通过" in p["msg_a"], f"→ {p['msg_a']}")
    check("A1 reject 成功且记录原因",
          p["ok_b"] and "已驳回" in p["msg_b"], f"→ {p['msg_b']}")
    check("A1 统计更新为 0/1/1",
          p["st1"] == {"pending": 0, "approved": 1, "rejected": 1}, f"→ {p['st1']}")
    check("A1 各条目状态正确",
          p["statuses"] == {"提案A.md": "approved", "提案B.md": "rejected"},
          f"→ {p['statuses']}")
    check("A1 审核备注被保留", p["notes"]["提案B.md"] == "与设定冲突", f"→ {p['notes']}")
    check("A1 kind 被保留（供队列分类）",
          p["kinds"]["提案A.md"] == "outline_proposal", f"→ {p['kinds']}")
    check("A1 **未登记路径明确报错**（不静默）",
          p["ok_c"] is False and "未登记" in p["msg_c"], f"→ {p['msg_c']}")
    shutil.rmtree(root, ignore_errors=True)


def case_hash_guards_re_review():
    """**改过的文件必须重新审核**；内容没变则保留原状态。

    这是本模块最要紧的契约：产物重新生成后仍显示「已通过」，
    等于让旧审核为新内容背书。
    """
    print("\n【A2】内容变化 → 状态重置（核心契约）")
    root = build_sandbox("nf_sra_a2_")
    (root / "data" / "state" / "obsidian_sandbox").mkdir(parents=True, exist_ok=True)
    code = PRELUDE + '''
from utils import sandbox_review as srv
SB = "data/state/obsidian_sandbox"
P = SB + "/提案.md"

write_text(P, "第一版内容")
srv.register("提案.md", SB)
srv.set_status("提案.md", srv.APPROVED, note="通过第一版")
after_approve = srv.list_items()[0]["status"]

# 同一内容重复写入 → 状态保留
e_same, changed_same = srv.register("提案.md", SB)
kept = srv.list_items()[0]["status"]

# 内容变化 → 必须重置 pending
write_text(P, "第二版内容（改了）")
e_new, changed_new = srv.register("提案.md", SB)
reset = srv.list_items()[0]["status"]
note_after = srv.list_items()[0]["note"]

_ticket(after_approve=after_approve, changed_same=changed_same, kept=kept,
        changed_new=changed_new, reset=reset, note_after=note_after)
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("A2 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("A2 审核后状态为 approved", p["after_approve"] == "approved",
          f"→ {p['after_approve']}")
    check("A2 内容未变 → changed=False", p["changed_same"] is False)
    check("A2 **内容未变保留原状态**（重复写入不重置审核结论）",
          p["kept"] == "approved", f"→ {p['kept']}")
    check("A2 内容变化 → changed=True", p["changed_new"] is True)
    check("A2 **内容变化 → 状态重置为 pending**（改过必须重审）",
          p["reset"] == "pending", f"→ {p['reset']}")
    check("A2 重置时清空旧审核备注（避免旧结论误导）",
          p["note_after"] == "", f"→ {p['note_after']!r}")
    shutil.rmtree(root, ignore_errors=True)


def case_orphans_detected():
    """手工放进沙盒的文件会被检为「未登记」——不能成为审核盲区。"""
    print("\n【A3】未登记文件检测（审核盲区）")
    root = build_sandbox("nf_sra_a3_")
    (root / "data" / "state" / "obsidian_sandbox").mkdir(parents=True, exist_ok=True)
    code = PRELUDE + '''
from utils import sandbox_review as srv
SB = "data/state/obsidian_sandbox"
write_text(SB + "/登记过.md", "x")
srv.register("登记过.md", SB)
write_text(SB + "/手工放的.md", "y")
orph = srv.orphans(SB)
_ticket(orph=orph)
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("A3 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("A3 检出未登记文件", p["orph"] == ["手工放的.md"], f"→ {p['orph']}")
    check("A3 已登记文件不算孤儿", "登记过.md" not in p["orph"])
    out2 = run_cli(root, ["scripts/sandbox_review.py", "--orphans"])
    check("A3 CLI --orphans 报出并有可行动说明",
          "手工放的.md" in out2 and "未登记" in out2, f"→ {out2[-200:]}")
    shutil.rmtree(root, ignore_errors=True)


def case_write_sandbox_registers():
    """`write_sandbox` 写入即登记为待审。"""
    print("\n【A4】write_sandbox 自动登记")
    root = build_sandbox("nf_sra_a4_")
    code = PRELUDE + '''
from utils import sandbox_review as srv
import obsidian_bridge as ob
ok, msg = ob.write_sandbox("自动登记.md", "# 内容", kind="outline_proposal",
                           source="probe")
items = srv.list_items()
_ticket(ok=ok, items=[{"path": e["path"], "status": e["status"],
                       "kind": e["kind"], "source": e["source"]} for e in items])
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("A4 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  items={p['items']}")
    check("A4 写入成功", p["ok"] is True)
    check("A4 写入即登记为 pending",
          len(p["items"]) == 1 and p["items"][0]["status"] == "pending",
          f"→ {p['items']}")
    check("A4 kind/source 透传（审核时能看到上下文）",
          p["items"][0]["kind"] == "outline_proposal"
          and p["items"][0]["source"] == "probe", f"→ {p['items'][0]}")
    shutil.rmtree(root, ignore_errors=True)


def case_review_cli():
    """审核队列 CLI：--queue / --approve / --json 可用。"""
    print("\n【A5】审核队列 CLI")
    root = build_sandbox("nf_sra_a5_")
    code = PRELUDE + '''
import obsidian_bridge as ob
ob.write_sandbox("甲.md", "# 甲", kind="outline_proposal", source="s1")
ob.write_sandbox("乙.md", "# 乙", kind="outline_proposal", source="s2")
print("__RESULT__" + __import__("json").dumps({"ok": True}))
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("A5 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    q = run_cli(root, ["scripts/sandbox_review.py", "--queue"])
    check("A5 --queue 列出待审产物", "甲.md" in q and "乙.md" in q, f"→ {q[-250:]}")
    check("A5 --queue 显示状态标签与来源",
          "待审" in q and "s1" in q, f"→ {q[-250:]}")
    check("A5 --queue 给出审核用法提示", "--approve" in q, f"→ {q[-250:]}")
    ap = run_cli(root, ["scripts/sandbox_review.py", "--approve", "甲.md"])
    check("A5 --approve 成功并提示可粘贴路径",
          "已通过" in ap and "粘贴" in ap, f"→ {ap[-200:]}")
    q2 = run_cli(root, ["scripts/sandbox_review.py", "--queue"])
    check("A5 通过后不在待审队列", "甲.md" not in q2 and "乙.md" in q2, f"→ {q2[-200:]}")
    js = run_cli(root, ["scripts/sandbox_review.py", "--json"])
    try:
        d = json.loads(js.strip().splitlines()[-1])
        ok_json = "items" in d and "stats" in d
    except Exception:
        ok_json = False
    check("A5 --json 机器可读", ok_json, f"→ {js[-160:]}")
    rj = run_cli(root, ["scripts/sandbox_review.py", "--reject", "乙.md",
                        "--note", "第二节与设定冲突"])
    check("A5 --reject 记录原因", "已驳回" in rj, f"→ {rj[-160:]}")
    js2 = run_cli(root, ["scripts/sandbox_review.py", "--list", "--json"])
    d2 = json.loads(js2.strip().splitlines()[-1])
    notes = {e["path"]: e["note"] for e in d2["items"]}
    check("A5 驳回原因已落库", notes.get("乙.md") == "第二节与设定冲突",
          f"→ {notes}")
    shutil.rmtree(root, ignore_errors=True)


# ============================================================ B 大纲迭代回写

def case_export_package():
    """导出审核包：3 份文件、全部待审、global.md 未被改动。"""
    print("\n【B1】大纲迭代 → 沙盒审核包")
    root = build_sandbox("nf_sra_b1_")
    write_backup(root, 1, BAD_OUTLINE)
    write_global(root, GOOD_OUTLINE)
    before = (root / "data" / "outline" / "global.md").read_bytes()
    code = PRELUDE + '''
from utils import sandbox_review as srv
import outline_export as oe
ok, msg, files = oe.export()
items = srv.list_items()
sb = "data/state/obsidian_sandbox"
docs = {}
for e in items:
    docs[e["path"]] = read_text(sb + "/" + e["path"])
_ticket(ok=ok, msg=msg, files=files,
        items=[{"path": e["path"], "status": e["status"], "kind": e["kind"]}
               for e in items],
        docs=docs)
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("B1 子进程产出结果", False, f"{out[-500:]} {err[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    after = (root / "data" / "outline" / "global.md").read_bytes()
    names = [i["path"] for i in p["items"]]
    print(f"  files={names}")
    check("B1 导出 3 份审核产物", len(p["items"]) == 3, f"→ {names}")
    check("B1 **全部登记为 pending（待审）**",
          all(i["status"] == "pending" for i in p["items"]), f"→ {p['items']}")
    check("B1 kind 标为 outline_proposal",
          all(i["kind"] == "outline_proposal" for i in p["items"]), f"→ {p['items']}")
    check("B1 含「当前大纲」一份", any("当前" in n for n in names), f"→ {names}")
    check("B1 含「对比」一份", any("对比" in n for n in names), f"→ {names}")
    check("B1 含「趋势」一份", any("趋势" in n for n in names), f"→ {names}")
    check("B1 **global.md 未被改动**（导出是旁路）", before == after)
    # 内容检查
    doc_txt = "\n".join(p["docs"].values())
    check("B1 产物抬头声明「待审提案，不是成品」",
          "待审提案" in doc_txt and "sandbox_review.py --approve" in doc_txt,
          f"→ {doc_txt[:160]}")
    check("B1 抬声明状态不会随文件进 vault",
          "不会随本文件粘进 vault" in doc_txt, f"→ {doc_txt[:200]}")
    check("B1 对比稿含体检指针表（改前/改后/变化）",
          "改前" in doc_txt and "改后" in doc_txt and "变化" in doc_txt)
    check("B1 趋势稿含收敛判定与「两者不是一回事」说明",
          "收敛判定" in doc_txt and "不是一回事" in doc_txt)
    check("B1 当前大纲稿含体检结论", "体检结论" in doc_txt)
    check("B1 当前大纲稿内嵌了 global.md 正文",
          "《测试书》整体大纲" in doc_txt)
    shutil.rmtree(root, ignore_errors=True)


def case_export_idempotent_status():
    """同版本重复导出：文件名稳定 → 内容稳定 → 审核状态保留。"""
    print("\n【B2】重复导出保留审核状态")
    root = build_sandbox("nf_sra_b2_")
    write_backup(root, 1, BAD_OUTLINE)
    write_global(root, GOOD_OUTLINE)
    code = PRELUDE + '''
from utils import sandbox_review as srv
import outline_export as oe
oe.export()
items = srv.list_items()
for e in items:
    srv.set_status(e["path"], srv.APPROVED, note="我看过了")
first = {e["path"]: e["status"] for e in srv.list_items()}

oe.export()          # 再导一次（内容未变）
second = {e["path"]: e["status"] for e in srv.list_items()}
notes = {e["path"]: e["note"] for e in srv.list_items()}
_ticket(first=first, second=second, notes=notes)
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("B2 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("B2 首轮审核全部 approved",
          all(v == "approved" for v in p["first"].values()), f"→ {p['first']}")
    check("B2 **重复导出不重置审核状态**（内容未变）",
          p["second"] == p["first"], f"→ {p['second']}")
    check("B2 审核备注保留",
          all(v == "我看过了" for v in p["notes"].values()), f"→ {p['notes']}")
    shutil.rmtree(root, ignore_errors=True)


def case_export_dry_run():
    """--dry-run 不写任何文件。"""
    print("\n【B3】导出 --dry-run 零写入")
    root = build_sandbox("nf_sra_b3_")
    write_backup(root, 1, BAD_OUTLINE)
    write_global(root, GOOD_OUTLINE)
    out = run_cli(root, ["scripts/outline_export.py", "--dry-run"])
    sb = root / "data" / "state" / "obsidian_sandbox"
    written = list(sb.rglob("*.md")) if sb.exists() else []
    check("B3 dry-run 打印计划", "dry-run" in out and "对比" in out, f"→ {out[-250:]}")
    check("B3 dry-run 未写入文件", not written, f"→ {[p.name for p in written]}")
    check("B3 dry-run 未产生状态库",
          not (root / "data" / "state" / "sandbox_manifest.json").exists())
    shutil.rmtree(root, ignore_errors=True)


# ========================================================== C 开工方向建议

def case_advisor_option_pool():
    """方案池按体检结果确定性产出，且优先级正确。"""
    print("\n【C1】方案池与优先级")
    root = build_sandbox("nf_sra_c1_")
    code = PRELUDE + '''
import outline_advisor as oa
import json

def probe(bad_text, good_text, hist):
    write_text("data/outline/global.md", good_text)
    for i, t in enumerate(hist, 1):
        write_text("data/outline/history/global_v%d.md" % i, t)
    a = oa.advise()
    return {"ids": [o["id"] for o in a["options"]],
            "recommended": a["recommended"],
            "verdict": a["verdict"],
            "metrics": a["metrics"],
            "options": a["options"]}

out = {}
BAD = read_text("_bad.md"); THIN = read_text("_thin.md"); GOOD = read_text("_good.md")

# 1) 结构性问题 → fix_structure 优先
write_text("data/outline/history/global_v1.md", BAD)
write_text("data/outline/global.md", BAD)
a1 = oa.advise()
out["structure"] = {"ids": [o["id"] for o in a1["options"]],
                    "recommended": a1["recommended"],
                    "metrics": a1["metrics"]}

# 2) 空泛条目 → densify 带逐条目动作
write_text("data/outline/history/global_v1.md", THIN)
write_text("data/outline/global.md", THIN)
a2 = oa.advise()
dens = next(o for o in a2["options"] if o["id"] == "densify")
out["densify"] = {"ids": [o["id"] for o in a2["options"]],
                  "recommended": a2["recommended"],
                  "n_actions": len(dens["actions"]),
                  "actions": dens["actions"]}

# 3) 退化 → rollback
write_text("data/outline/history/global_v1.md", GOOD)
write_text("data/outline/global.md", THIN)
a3 = oa.advise()
out["rollback"] = {"ids": [o["id"] for o in a3["options"]],
                   "recommended": a3["recommended"],
                   "rollback": next((o for o in a3["options"]
                                     if o["id"] == "rollback"), None)}

# 4) 达标 → start_writing 推荐
write_text("data/outline/history/global_v1.md", GOOD)
write_text("data/outline/global.md", GOOD)
a4 = oa.advise()
out["done"] = {"ids": [o["id"] for o in a4["options"]],
               "recommended": a4["recommended"], "verdict": a4["verdict"]}
_ticket(**out)
'''
    (root / "_bad.md").write_text(BAD_OUTLINE, encoding="utf-8")
    (root / "_thin.md").write_text(THIN_OUTLINE, encoding="utf-8")
    (root / "_good.md").write_text(GOOD_OUTLINE, encoding="utf-8")
    p, out, err = run_py(root, code)
    if p is None:
        check("C1 子进程产出结果", False, f"{out[-600:]} {err[-600:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  structure={p['structure']['ids']} rec={p['structure']['recommended']}")
    print(f"  densify  rec={p['densify']['recommended']} n={p['densify']['n_actions']}")
    print(f"  rollback={p['rollback']['ids']} rec={p['rollback']['recommended']}")
    print(f"  done     rec={p['done']['recommended']} verdict={p['done']['verdict']}")

    s = p["structure"]
    check("C1 有结构性问题 → 产出 fix_structure",
          "fix_structure" in s["ids"], f"→ {s['ids']}")
    check("C1 有结构性问题 → 推荐 fix_structure",
          s["recommended"] == "fix_structure", f"→ {s['recommended']}")
    check("C1 结构性问题数被算进 metrics", s["metrics"]["issues"] == 2,
          f"→ {s['metrics']}")

    d = p["densify"]
    check("C1 有空泛条目 → 产出 densify", "densify" in d["ids"], f"→ {d['ids']}")
    check("C1 推荐 densify", d["recommended"] == "densify", f"→ {d['recommended']}")
    check("C1 densify 逐条目出动作（不是笼统建议）",
          d["n_actions"] == len(d["actions"]) and d["n_actions"] > 0,
          f"→ {d['n_actions']}")
    if d["actions"]:
        a = d["actions"][0]
        check("C1 动作含 entry_id（可直接喂给精修端点）",
              bool(a.get("entry_id")), f"→ {a}")
        check("C1 动作含缺失维度", bool(a.get("dims")), f"→ {a}")
        check("C1 动作含可执行 next_step", bool(a.get("next_step")), f"→ {a}")
        check("C1 next_step 指向真实端点 /refine/outline/node",
              "/refine/outline/node" in (a.get("next_step") or ""),
              f"→ {a.get('next_step')}")

    r = p["rollback"]
    check("C1 本轮退化 → 产出 rollback", "rollback" in r["ids"], f"→ {r['ids']}")
    check("C1 退化时优先推荐回退", r["recommended"] == "rollback",
          f"→ {r['recommended']}")
    check("C1 rollback 给出回退端点与版本",
          r["rollback"] and "/outline/restore" in r["rollback"]["actions"][0]["next_step"],
          f"→ {(r['rollback'] or {}).get('actions')}")

    dn = p["done"]
    check("C1 达标时推荐开工", dn["recommended"] == "start_writing",
          f"→ {dn['recommended']}")
    check("C1 达标时收敛判定为 done", dn["verdict"] == "done", f"→ {dn['verdict']}")
    check("C1 start_writing 始终在方案池里（用户可坚持开工）",
          "start_writing" in dn["ids"], f"→ {dn['ids']}")
    shutil.rmtree(root, ignore_errors=True)


def case_advisor_stalled_and_material():
    """停滞 → change_approach；素材缺口被识别并给出补素材方向。"""
    print("\n【C2】停滞与素材缺口")
    root = build_sandbox("nf_sra_c2_")
    # 造「素材覆盖」缺失：条目里点名一个不在 setting 里的角色，
    # 且 normalized 素材为空 → coverage=False
    covered = """# 《测试书》整体大纲

## 起
开端。

## 承
发展。

## 转
高潮。

## 合
结局。

## 关键节点
- 节点1：第1章 露汐继续。

## 预计章节数
1

## 章节规划
- 第1章：继续。
"""
    code = PRELUDE + '''
import outline_advisor as oa
T = read_text("_t.md")
for i in (1, 2, 3):
    write_text("data/outline/history/global_v%d.md" % i, T)
write_text("data/outline/global.md", T)
a = oa.advise()
ca = next((o for o in a["options"] if o["id"] == "change_approach"), None)
dens = next((o for o in a["options"] if o["id"] == "densify"), None)
_ticket(ids=[o["id"] for o in a["options"]], verdict=a["verdict"],
        recommended=a["recommended"], note=a["note"],
        has_change=ca is not None,
        change_why=(ca or {}).get("why", ""),
        change_next=((ca or {}).get("actions") or [{}])[0].get("next_step", ""),
        dens_dims=(dens or {}).get("actions", [{}])[0].get("dims", []))
'''
    (root / "_t.md").write_text(covered, encoding="utf-8")
    p, out, err = run_py(root, code)
    if p is None:
        check("C2 子进程产出结果", False, f"{out[-500:]} {err[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  verdict={p['verdict']} ids={p['ids']}")
    print(f"  change_why={p['change_why'][:90]}")
    check("C2 三轮相同 → 收敛判定 stalled", p["verdict"] == "stalled",
          f"→ {p['verdict']}")
    check("C2 停滞 → 产出 change_approach", p["has_change"] is True,
          f"→ {p['ids']}")
    check("C2 change_approach 引用收敛说明（有依据而非套话）",
          "没变" in p["change_why"] or "递减" in p["change_why"],
          f"→ {p['change_why'][:80]}")
    check("C2 change_approach 给出换角度/补素材的具体方向",
          any(w in p["change_next"] for w in ("换角度", "补素材", "素材")),
          f"→ {p['change_next'][:90]}")
    check("C2 densify 动作里带出缺失维度（供判断是精修还是补素材）",
          bool(p["dens_dims"]), f"→ {p['dens_dims']}")
    shutil.rmtree(root, ignore_errors=True)


def case_advisor_cli_json_and_zero_llm():
    """CLI 可用；且建议生成**零 LLM 调用**（结构上不可能调用）。"""
    print("\n【C3】CLI + 零 token 保证")
    root = build_sandbox("nf_sra_c3_")
    write_backup(root, 1, BAD_OUTLINE)
    write_global(root, GOOD_OUTLINE)
    txt = run_cli(root, ["scripts/outline_advisor.py"])
    check("C3 CLI 输出方案与推荐", "可选方向" in txt and "推荐" in txt, f"→ {txt[:200]}")
    check("C3 CLI 说明决定权在用户",
          "决定权在你" in txt, f"→ {txt[-200:]}")
    js = run_cli(root, ["scripts/outline_advisor.py", "--json"])
    try:
        d = json.loads(js.strip())
        ok_json = "options" in d and "recommended" in d
    except Exception as e:
        d, ok_json = {}, False
        print("  json 解析失败:", e)
    check("C3 --json 可解析", ok_json, f"→ {js[:160]}")
    check("C3 json 含 metrics 与 verdict",
          bool(d.get("metrics")) and bool(d.get("verdict")), f"→ {list(d)[:8]}")
    # 零 token：源码里不得出现任何 LLM 客户端构造/调用
    src = (REPO / "scripts" / "outline_advisor.py").read_text(encoding="utf-8")
    bad = [k for k in ("make_client", "run_task", "write_task", "llm_client")
           if k in src]
    check("C3 **零 LLM 调用**（源码不含任何客户端构造/调用）", not bad,
          f"→ 出现 {bad}")
    shutil.rmtree(root, ignore_errors=True)


def main():
    print("=" * 70)
    print("沙盒审核 + 大纲回写 + 开工方向建议 自检（临时项目根，零 LLM）")
    print("=" * 70)
    for fn in (case_register_and_status, case_hash_guards_re_review,
               case_orphans_detected, case_write_sandbox_registers,
               case_review_cli,
               case_export_package, case_export_idempotent_status,
               case_export_dry_run,
               case_advisor_option_pool, case_advisor_stalled_and_material,
               case_advisor_cli_json_and_zero_llm):
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
