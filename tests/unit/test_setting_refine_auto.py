# -*- coding: utf-8 -*-
"""setting_refine --auto-thin 闭环自检（P1.5-3）。

## 为什么需要这套用例

`--auto-thin` 曾经是**空壳**：feedback 硬编码为
`"请对以下碎片角色进行设定补全（仅从素材推断，llm_inferred 标记）：（见体检报告）"`
—— 既没有角色名、也没说明缺什么维度，而且**无轮次上限、无达标即停、
无 gates 开关、orchestrator 根本没接**。用户看到的「已支持 --auto-thin」
是一个只有 CLI 参数的幻觉。

本套用例锚定闭环的**行为契约**（不是实现位置）：

- 达标即停：THIN 数为 0 → 零调用返回，不产生任何 cost
- 无进展即停：本轮 THIN 数未下降 → 停（继续跑纯烧 token + 放大编造风险）
- 轮次上限：跑满 max_rounds 仍未达标 → 以 `max_rounds` 原因结束
- 定向 feedback：必须点名角色 + 缺失维度（否则旧空壳行为会「通过」）
- 默认关：`gates.setting_refine_auto` 缺失/False → orchestrator 不调用
- 每轮独立备份：setting_v{N}.json 递增，任一轮可单独回溯

## 隔离策略

在**临时项目根**跑（与项目既有策略一致）：复制 scripts/prompts/config，
真实 `data/`、`config/` 零污染。LLM 走 `FakeClient`，
**零真实调用**（暮雨约束：qwen3.5-flash 免费额度，不得产生费用）。
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []

# 沙箱需要的脚本（缺一即 import 失败）
NEEDED = (
    "setting_refine.py", "material_review.py", "stage1_consolidate.py",
    "orchestrator.py", "snapshot.py",
)


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def build_sandbox(prefix="nf_sr_"):
    """搭一个最小可跑的临时项目根。"""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "scripts" / "utils").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    for sub in ("data/state", "data/setting/history", "data/setting/normalized",
                "data/outline", "data/chapters", "logs"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    # scripts + utils 必须真实复制（setting_refine import 它们）
    for name in NEEDED:
        src = REPO / "scripts" / name
        if src.exists():
            shutil.copy2(src, root / "scripts" / name)
    for name in ("utils", "nf_api_domains"):
        src_dir = REPO / "scripts" / name
        if src_dir.is_dir():
            shutil.copytree(src_dir, root / "scripts" / name,
                            ignore=shutil.ignore_patterns("__pycache__"),
                            dirs_exist_ok=True)
    # prompts 必须真实复制（load_template 会读 stage1_refine.md）
    if (REPO / "prompts").is_dir():
        shutil.copytree(REPO / "prompts", root / "prompts",
                        ignore=shutil.ignore_patterns("__pycache__", "history"),
                        dirs_exist_ok=True)
    # config 必须真实复制（但可被用例覆写）
    for name in ("system.yaml", "project.yaml"):
        src = REPO / "config" / name
        if src.exists():
            shutil.copy2(src, root / "config" / name)
    return root


def run_py(root, code, timeout=180):
    """在临时项目根跑一段 python，返回 (parsed_result|None, stdout, stderr)。"""
    import subprocess
    script = root / "_case.py"
    script.write_text(code, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        p = subprocess.run([sys.executable, str(script)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(root), timeout=timeout,
                           env=env)
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT"
    out, errout = p.stdout or "", p.stderr or ""
    marker = "__RESULT__"
    if marker in out:
        payload = out.split(marker, 1)[1].splitlines()[0]
        try:
            return json.loads(payload), out, errout
        except Exception:                          # noqa: BLE001
            return None, out, errout
    return None, out, errout


# ---------------------------------------------------------------- 夹具
#
# ⚠️ 夹具必须产出**自洽**的产物 —— 这是本项目踩过的坑（见「夹具必须产出合法
# 产物」纪律）。本套用例首版夹具就有两处不自洽，导致 15 条假红：
#
# 1. `凤凰` 的 role 是「负责档案室的整理员」—— 不含任何能力词
#    （ABILITY_WORDS 里没有「整理」），于是 ability=False → score=2 → **THIN**。
#    但注释声称它是 WARN、`n_thin=0` 应当「无 THIN」。结果 `already_ok`
#    永远走不到，`thin_before` 恒 ≥1。
# 2. `thin_ok=True` 的「补全版」traits 写成 ['沉默寡言','认路极准',
#    '对陌生人保持距离'] —— personality 判据是 **每条 ≥6 字**，
#    前两条只有 4 字 → personality=False → 补全后仍是 THIN（score=2）。
#    于是「补全成功」的模拟根本没成功，`reached_ok` 永远拿不到。
#
# 结论：`run_auto_thin` 的实现是对的，那 15 条红**全部**来自夹具。
# 修夹具时**不要**去放宽判据 —— 判据阈值是经真实数据定标的
# （见 MEMORY.md「判据阈值定标」），改它就等于为了让测试变绿而放松质量门。
#
# 现在三档角色各自**恰好**命中预期维度：
#   - 露汐 / 凤凰：score=4（WARN，identity+personality+ability+source）→ 不补
#   - 碎片甲/乙/丙（thin_ok=False）：score=1（只 source）→ THIN，要补
#   - 碎片甲/乙/丙（thin_ok=True）：score=3（WARN）→ 不再 THIN，模拟「补全成功」

# WARN 档：role ≥8 字（identity）、traits 3 条且每条 ≥6 字（personality）、
# 文本含能力词（ability）、带 source。**刻意不满足 relations/coverage**
# —— 只要 score=4 就到 WARN，不必把六维填满（填满反而掩盖判据回归）。
WARN_CAST = [
    {"id": "c001", "name": "露汐",
     "role": "绒花帝国魔法学院的年轻讲师，负责低阶课程的讲授",
     "traits": ["冷静克制不轻易表态", "说话喜欢绕弯子", "对旧物有执念"],
     "source": "materials/raw/a.md"},
    {"id": "c002", "name": "凤凰",
     "role": "负责档案室的整理员，擅长索引编目与归档",
     "traits": ["记事极细从不出错", "怕麻烦但不推脱", "习惯用右手扶镜框"],
     "source": "materials/raw/b.md"},
]

# 补全成功后：identity + personality + ability + source = 4 维 → WARN。
# 「擅长」是能力词；三条 traits 全部 ≥6 字（这是 personality 判据的硬要求）。
THIN_FIXED = {
    "role": "来自边缘聚落的信使，负责传递跨区消息",
    "traits": ["沉默寡言极少开口", "认路极准从不迷途", "对陌生人保持距离"],
    "ability": "擅长在夜间辨认星辰方位并快速穿越荒野",
    "relations": [{"target": "露汐", "type": "旧识"}],
    "source": "materials/raw/c.md",
}


def make_thin_char(i):
    """第 i 个碎片角色（THIN：只有 source，score=1）。"""
    return {
        "id": "t%03d" % i, "name": "碎片%s" % "甲乙丙丁戊"[i % 5],
        "role": "", "traits": [], "source": "materials/raw/c.md",
    }


def make_fixed_char(i):
    """第 i 个碎片角色（已补全：score=4 → WARN，不再 THIN）。"""
    c = {"id": "t%03d" % i, "name": "碎片%s" % "甲乙丙丁戊"[i % 5]}
    c.update(THIN_FIXED)
    return c


def make_setting(n_thin=3, thin_ok=False):
    """生成设定集。thin_ok=True 时把 THIN 角色补到 WARN（模拟「补全成功」）。"""
    chars = [dict(c) for c in WARN_CAST]
    for i in range(n_thin):
        chars.append(make_fixed_char(i) if thin_ok else make_thin_char(i))
    return {
        "characters": chars,
        "world": {"地理": "沙都分七区，外围是荒漠。"},
        "plot_fragments": ["碎片甲在路上丢了信"],
        "timeline": ["某年冬，封锁开始"],
    }


def write_setting(root, obj):
    (root / "data" / "setting" / "setting.json").write_text(
        json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# 沙箱内用例的公共前导
PRELUDE = '''
import sys, os, json
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
sys.path.insert(0, os.getcwd())
from utils.file_io import read_text, write_text
from utils.fake_client import FakeClient

def _ticket(**kw):
    import json as _j
    print("__RESULT__" + _j.dumps(kw, ensure_ascii=False))
'''


# ====================================================================== 用例

def case_already_ok():
    """达标即停：无 THIN 条目 → 零调用返回。"""
    print("\n【1】达标即停（无 THIN 时零调用）")
    root = build_sandbox("nf_sr_ok_")
    write_setting(root, make_setting(n_thin=0))
    code = PRELUDE + '''
import setting_refine as srfy
from utils.fake_client import FakeClient

cfg = __import__("yaml").safe_load(read_text("config/system.yaml"))
proj = __import__("yaml").safe_load(read_text("config/project.yaml"))

client = FakeClient()
calls = {"n": 0}
_orig = client.run_task
def counting(task_file, workdir=None, model=None):
    calls["n"] += 1
    return _orig(task_file, workdir=workdir, model=model)
client.run_task = counting

ok, msg, stats = srfy.run_auto_thin(cfg, proj, client=client,
                                    task_dir="data/state/tasks", max_rounds=2)
_ticket(ok=ok, msg=msg, reason=stats["reason"], rounds=stats["rounds"],
        calls=calls["n"], thin_before=stats["thin_before"],
        thin_after=stats["thin_after"])
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "1 子进程产出结果", f"stdout={out[-500:]} stderr={errout[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} reason={p['reason']} calls={p['calls']}")
    check("1 无 THIN → 闭环成功返回", p["ok"] is True, f"→ {p['msg']}")
    check("1 原因为 already_ok", p["reason"] == "already_ok", f"→ {p['reason']}")
    check("1 **零 LLM 调用**（达标短路，不烧 token）", p["calls"] == 0, f"→ {p['calls']}")
    check("1 轮次为 0", p["rounds"] == 0, f"→ {p['rounds']}")
    check("1 thin_before 为 0", p["thin_before"] == 0, f"→ {p['thin_before']}")
    shutil.rmtree(root, ignore_errors=True)


def case_reached_ok_first_round():
    """一轮到位：THIN 4 → 0。应跑 1 轮、原因 reached_ok、不再跑第 2 轮。"""
    print("\n【2】一轮达标即停（THIN 4 → 0）")
    root = build_sandbox("nf_sr_r1_")
    write_setting(root, make_setting(n_thin=4, thin_ok=False))
    ok_setting = make_setting(n_thin=4, thin_ok=True)
    code = PRELUDE + '''
import setting_refine as srfy
from utils.fake_client import FakeClient

cfg = __import__("yaml").safe_load(read_text("config/system.yaml"))
proj = __import__("yaml").safe_load(read_text("config/project.yaml"))

# 让 stage1_refine 任务把设定集改写成「已补全」版本
FIXED = json.loads(read_text("_fixed.json"))
import utils.fake_client as fc
_orig_run = fc.FakeClient.run_task
def patched(self, task_file, workdir=None, model=None):
    name = str(task_file).split(os.sep)[-1]
    if name.startswith("stage1_refine"):
        write_text("data/setting/setting.json",
                   json.dumps(FIXED, ensure_ascii=False, indent=1))
        return {"exit_code": 0, "stdout_tail": "", "tokens": 100, "tokens_out": 50,
                "cost_yuan": 0.0, "estimated": True, "provider": "fake",
                "model": "fake", "requests": 1}
    return _orig_run(self, task_file, workdir=workdir, model=model)
fc.FakeClient.run_task = patched

client = FakeClient()
refine_calls = {"n": 0}
_origt = client.run_task
def counting(task_file, workdir=None, model=None):
    if str(task_file).endswith("stage1_refine.md"):
        refine_calls["n"] += 1
    return _origt(task_file, workdir=workdir, model=model)
client.run_task = counting

ok, msg, stats = srfy.run_auto_thin(cfg, proj, client=client,
                                    task_dir="data/state/tasks", max_rounds=2)
_ticket(ok=ok, msg=msg, reason=stats["reason"], rounds=stats["rounds"],
        refine_calls=refine_calls["n"], thin_before=stats["thin_before"],
        thin_after=stats["thin_after"], versions=stats["versions"])
'''
    (root / "_fixed.json").write_text(
        json.dumps(ok_setting, ensure_ascii=False), encoding="utf-8")
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "2 子进程产出结果", f"stdout={out[-600:]} stderr={errout[-600:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} reason={p['reason']} rounds={p['rounds']} "
          f"refine_calls={p['refine_calls']} THIN {p['thin_before']}→{p['thin_after']}")
    check("2 闭环成功", p["ok"] is True, f"→ {p['msg']}")
    check("2 原因为 reached_ok", p["reason"] == "reached_ok", f"→ {p['reason']}")
    check("2 只跑 1 轮（达标即停，不空跑第 2 轮）",
          p["rounds"] == 1 and p["refine_calls"] == 1,
          f"→ rounds={p['rounds']} calls={p['refine_calls']}")
    check("2 THIN 归零", p["thin_after"] == 0, f"→ {p['thin_after']}")
    check("2 记录了本轮备份版本", len(p["versions"]) == 1, f"→ {p['versions']}")
    shutil.rmtree(root, ignore_errors=True)


def case_no_progress():
    """无进展即停：补全后 THIN 数不降 → 不跑第 2 轮。

    ⚠️ 必须**显式**把 `stage1_refine` 打成 no-op。首版用例想当然地以为
    「FakeClient 不会改 setting」，实际它会给 `stage1_refine` 写一份罐头设定
    （`fake_client._write_setting`：单个 `测试角色`，`traits:["冷静"]`，
    score=1 → 正好 1 个 THIN）。于是 THIN 3→1 **是有进展的**，闭环正确地
    跑了第 2 轮才因 1→1 停下 —— 用例却断言 rounds==1，报假红。

    **实现是对的，是夹具没有制造出「无进展」这个场景。**
    """
    print("\n【3】无进展即停（THIN 数不下降）")
    root = build_sandbox("nf_sr_np_")
    write_setting(root, make_setting(n_thin=3, thin_ok=False))
    code = PRELUDE + '''
import setting_refine as srfy
from utils.fake_client import FakeClient

cfg = __import__("yaml").safe_load(read_text("config/system.yaml"))
proj = __import__("yaml").safe_load(read_text("config/project.yaml"))

# 显式 no-op：子会话「成功」但**不碰** setting.json（THIN 数保持不变）
import utils.fake_client as fc
_orig_run = fc.FakeClient.run_task
refine_calls = {"n": 0}
def patched(self, task_file, workdir=None, model=None):
    name = str(task_file).split(os.sep)[-1]
    if name.startswith("stage1_refine"):
        refine_calls["n"] += 1
        return {"exit_code": 0, "stdout_tail": "", "tokens": 100, "tokens_out": 50,
                "cost_yuan": 0.0, "estimated": True, "provider": "fake",
                "model": "fake", "requests": 1}
    return _orig_run(self, task_file, workdir=workdir, model=model)
fc.FakeClient.run_task = patched

client = FakeClient()
ok, msg, stats = srfy.run_auto_thin(cfg, proj, client=client,
                                    task_dir="data/state/tasks", max_rounds=3)
_ticket(ok=ok, reason=stats["reason"], rounds=stats["rounds"],
        refine_calls=refine_calls["n"], thin_before=stats["thin_before"],
        thin_after=stats["thin_after"])
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "3 子进程产出结果", f"stdout={out[-600:]} stderr={errout[-600:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} reason={p['reason']} rounds={p['rounds']} "
          f"THIN {p['thin_before']}→{p['thin_after']}")
    check("3 无进展时以 no_progress 结束", p["reason"] == "no_progress",
          f"→ {p['reason']}")
    check("3 只跑 1 轮（不空转到轮次上限）", p["rounds"] == 1,
          f"→ {p['rounds']}")
    check("3 **未触达 max_rounds=3**（证明是主动止损而非跑满）",
          p["refine_calls"] == 1, f"→ calls={p['refine_calls']}")
    check("3 返回 ok=True（闭环正常结束，不谎报失败）", p["ok"] is True)
    shutil.rmtree(root, ignore_errors=True)


def case_max_rounds():
    """轮次上限：每轮都有微小进展（THIN 逐个降）→ 跑满上限后以 max_rounds 结束。"""
    print("\n【4】轮次上限（每轮有进展 → 跑满上限）")
    root = build_sandbox("nf_sr_mr_")
    write_setting(root, make_setting(n_thin=4, thin_ok=False))
    # 每轮把「一个」THIN 角色补好：4 → 3 → 2 → 1（3 轮仍有 THIN）
    code = PRELUDE + '''
import setting_refine as srfy
from utils.fake_client import FakeClient

cfg = __import__("yaml").safe_load(read_text("config/system.yaml"))
proj = __import__("yaml").safe_load(read_text("config/project.yaml"))

import utils.fake_client as fc
_orig_run = fc.FakeClient.run_task
ROUND = {"n": 0}
def patched(self, task_file, workdir=None, model=None):
    name = str(task_file).split(os.sep)[-1]
    if name.startswith("stage1_refine"):
        ROUND["n"] += 1
        st = json.loads(read_text("data/setting/setting.json"))
        # 每轮修好一个：把第 ROUND 个 THIN 条目补成厚实条目
        fixed = 0
        for c in st["characters"]:
            if not c.get("traits"):            # 还是 THIN
                fixed += 1
                c["role"] = "来自边缘聚落的信使，负责传递跨区消息"
                c["traits"] = ["沉默寡言极少开口", "认路极准从不迷途", "对陌生人保持距离"]
                c["ability"] = "擅长在夜间辨认星辰方位并快速穿越荒野"
                c["relations"] = [{"target": "露汐", "type": "旧识"}]
                if fixed >= 1:
                    break
        write_text("data/setting/setting.json",
                   json.dumps(st, ensure_ascii=False, indent=1))
        return {"exit_code": 0, "stdout_tail": "", "tokens": 100, "tokens_out": 50,
                "cost_yuan": 0.0, "estimated": True, "provider": "fake",
                "model": "fake", "requests": 1}
    return _orig_run(self, task_file, workdir=workdir, model=model)
fc.FakeClient.run_task = patched

client = FakeClient()
ok, msg, stats = srfy.run_auto_thin(cfg, proj, client=client,
                                    task_dir="data/state/tasks", max_rounds=3)
_ticket(ok=ok, reason=stats["reason"], rounds=stats["rounds"],
        thin_before=stats["thin_before"], thin_after=stats["thin_after"],
        fix_rounds=ROUND["n"], versions=stats["versions"])
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "4 子进程产出结果", f"stdout={out[-600:]} stderr={errout[-600:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} reason={p['reason']} rounds={p['rounds']} "
          f"THIN {p['thin_before']}→{p['thin_after']}")
    check("4 跑满上限后以 max_rounds 结束", p["reason"] == "max_rounds",
          f"→ {p['reason']}")
    check("4 轮次 == 上限 3", p["rounds"] == 3 and p["fix_rounds"] == 3,
          f"→ rounds={p['rounds']} fix={p['fix_rounds']}")
    check("4 THIN 有下降但未归零", 0 < p["thin_after"] < p["thin_before"],
          f"→ {p['thin_before']}→{p['thin_after']}")
    check("4 每轮各留一个备份（可逐轮回溯）",
          len(p["versions"]) == 3, f"→ {p['versions']}")
    shutil.rmtree(root, ignore_errors=True)


def case_max_rounds_one():
    """max_rounds=1（默认）：即使有进展也立即停。"""
    print("\n【5】默认上限 1（有进展也立刻停）")
    root = build_sandbox("nf_sr_m1_")
    write_setting(root, make_setting(n_thin=4, thin_ok=False))
    code = PRELUDE + '''
import setting_refine as srfy
from utils.fake_client import FakeClient

cfg = __import__("yaml").safe_load(read_text("config/system.yaml"))
proj = __import__("yaml").safe_load(read_text("config/project.yaml"))

import utils.fake_client as fc
_orig_run = fc.FakeClient.run_task
def patched(self, task_file, workdir=None, model=None):
    name = str(task_file).split(os.sep)[-1]
    if name.startswith("stage1_refine"):
        st = json.loads(read_text("data/setting/setting.json"))
        for c in st["characters"]:
            if not c.get("traits"):
                c["role"] = "来自边缘聚落的信使，负责传递跨区消息"
                c["traits"] = ["沉默寡言极少开口", "认路极准从不迷途", "对陌生人保持距离"]
                c["ability"] = "擅长在夜间辨认星辰方位并快速穿越荒野"
                c["relations"] = [{"target": "露汐", "type": "旧识"}]
                break
        write_text("data/setting/setting.json",
                   json.dumps(st, ensure_ascii=False, indent=1))
        return {"exit_code": 0, "stdout_tail": "", "tokens": 100, "tokens_out": 50,
                "cost_yuan": 0.0, "estimated": True, "provider": "fake",
                "model": "fake", "requests": 1}
    return _orig_run(self, task_file, workdir=workdir, model=model)
fc.FakeClient.run_task = patched

client = FakeClient()
ok, msg, stats = srfy.run_auto_thin(cfg, proj, client=client,
                                    task_dir="data/state/tasks", max_rounds=1)
_ticket(ok=ok, reason=stats["reason"], rounds=stats["rounds"],
        thin_before=stats["thin_before"], thin_after=stats["thin_after"])
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "5 子进程产出结果", f"stdout={out[-600:]} stderr={errout[-600:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} reason={p['reason']} rounds={p['rounds']} "
          f"THIN {p['thin_before']}→{p['thin_after']}")
    check("5 max_rounds=1 → 恰好 1 轮", p["rounds"] == 1, f"→ {p['rounds']}")
    check("5 以 max_rounds 结束", p["reason"] == "max_rounds", f"→ {p['reason']}")
    check("5 有进展（THIN 下降）", p["thin_after"] < p["thin_before"],
          f"→ {p['thin_before']}→{p['thin_after']}")
    shutil.rmtree(root, ignore_errors=True)


def case_feedback_is_directed():
    """定向 feedback：必须点名角色 + 缺失维度（旧空壳会 FAIL）。"""
    print("\n【6】定向 feedback（点名角色 + 缺失维度）")
    root = build_sandbox("nf_sr_fb_")
    write_setting(root, make_setting(n_thin=3, thin_ok=False))
    code = PRELUDE + '''
import setting_refine as srfy
thin, detail = srfy.thin_report()
fb = srfy.build_auto_feedback(thin, detail, 1, 2)
_ticket(thin=thin, detail=detail, fb=fb)
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "6 子进程产出结果", f"stdout={out[-500:]} stderr={errout[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    fb, thin, detail = p["fb"], p["thin"], p["detail"]
    print(f"  THIN={thin}")
    print(f"  detail={detail}")
    check("6 检出 3 个 THIN 角色", len(thin) == 3, f"→ {thin}")
    for name in thin:
        check(f"6 feedback 点名「{name}」", name in fb, "→ 未出现")
    check("6 feedback 含缺失维度标注", "缺" in fb, "→ 无「缺」字")
    check("6 含 llm_inferred 标记要求", "llm_inferred" in fb)
    check("6 含「禁止编造」约束", "禁止编造" in fb or "留空不写" in fb)
    check("6 标注了轮次进度", "1/2" in fb, f"→ {fb[:60]}")
    # 旧空壳的占位串必须不再出现
    check("6 **未使用旧硬编码占位串**",
          "（见体检报告）" not in fb, "→ 仍是空壳 feedback")
    shutil.rmtree(root, ignore_errors=True)


def case_gate_default_off():
    """默认关：gates.setting_refine_auto 缺失/False → orchestrator 不调用。"""
    print("\n【7】默认关（gates 开关）")
    src = (REPO / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("7 orchestrator 读 gates.setting_refine_auto",
          "setting_refine_auto" in src, "→ 未接入")
    check("7 该分支默认取 False（缺失即关）",
          '"setting_refine_auto", False' in src, "→ 默认值不是 False")
    check("7 调用被 try/except 包裹（失败不阻断）",
          "设定补全(auto-thin) 失败（不影响流程）" in src, "→ 未见降级文案")

    import yaml
    cfg = yaml.safe_load((REPO / "config" / "system.yaml").read_text(encoding="utf-8"))
    gates = cfg.get("gates", {})
    check("7 config 里 setting_refine_auto 存在且为 False",
          gates.get("setting_refine_auto") is False, f"→ {gates.get('setting_refine_auto')}")
    check("7 config 里 setting_refine_max_rounds 存在且 <=2",
          isinstance(gates.get("setting_refine_max_rounds"), int)
          and 1 <= gates["setting_refine_max_rounds"] <= 2,
          f"→ {gates.get('setting_refine_max_rounds')}")
    check("7 setting_refine_reminder 仍为 true（提醒兜底未被关掉）",
          gates.get("setting_refine_reminder") is True)


def case_dry_run():
    """dry-run：只备份 + 生成任务，不调用子会话改文件。"""
    print("\n【8】dry-run（不产生任何改动）")
    root = build_sandbox("nf_sr_dr_")
    orig = make_setting(n_thin=3, thin_ok=False)
    write_setting(root, orig)
    orig_text = (root / "data" / "setting" / "setting.json").read_text(encoding="utf-8")
    code = PRELUDE + '''
import setting_refine as srfy
from utils.fake_client import FakeClient

cfg = __import__("yaml").safe_load(read_text("config/system.yaml"))
proj = __import__("yaml").safe_load(read_text("config/project.yaml"))

client = FakeClient()
run_calls = {"n": 0}
_orig = client.run_task
def counting(task_file, workdir=None, model=None):
    run_calls["n"] += 1
    return _orig(task_file, workdir=workdir, model=model)
client.run_task = counting

ok, msg, stats = srfy.run_auto_thin(cfg, proj, client=client,
                                    task_dir="data/state/tasks", max_rounds=2,
                                    dry_run=True)
_ticket(ok=ok, reason=stats["reason"], rounds=stats["rounds"],
        run_calls=run_calls["n"])
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "8 子进程产出结果", f"stdout={out[-600:]} stderr={errout[-600:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    now_text = (root / "data" / "setting" / "setting.json").read_text(encoding="utf-8")
    print(f"  ok={p['ok']} reason={p['reason']} run_calls={p['run_calls']}")
    check("8 dry-run 未调用子会话", p["run_calls"] == 0, f"→ {p['run_calls']}")
    check("8 dry-run 未改动 setting.json", now_text == orig_text, "→ 文件被改")
    check("8 dry-run 已备份（可回溯）",
          any((root / "data" / "setting" / "history").glob("setting_v*.json")),
          "→ 无备份")
    check("8 原因为 dry_run", p["reason"] == "dry_run", f"→ {p['reason']}")
    shutil.rmtree(root, ignore_errors=True)


def case_no_setting():
    """缺 setting.json → 可行动报错，不静默成功。"""
    print("\n【9】缺设定集（可行动报错）")
    root = build_sandbox("nf_sr_ns_")
    # 不写 setting.json
    code = PRELUDE + '''
import setting_refine as srfy

cfg = __import__("yaml").safe_load(read_text("config/system.yaml"))
proj = __import__("yaml").safe_load(read_text("config/project.yaml"))
ok, msg, stats = srfy.run_auto_thin(cfg, proj, max_rounds=1)
_ticket(ok=ok, msg=msg, reason=stats["reason"])
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "9 子进程产出结果", f"stdout={out[-500:]} stderr={errout[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} msg={p['msg']}")
    check("9 返回失败（不谎报成功）", p["ok"] is False, f"→ {p['ok']}")
    check("9 原因为 no_setting", p["reason"] == "no_setting", f"→ {p['reason']}")
    check("9 报错文案可行动（提示先跑 stage1）", "stage1" in p["msg"],
          f"→ {p['msg']}")
    shutil.rmtree(root, ignore_errors=True)


def case_cli_rounds_priority():
    """CLI --max-rounds 优先级 > gates 配置。"""
    print("\n【10】轮次上限优先级（CLI > gates）")
    src = (REPO / "scripts" / "setting_refine.py").read_text(encoding="utf-8")
    check("10 CLI 有 --max-rounds 参数", '"--max-rounds"' in src, "→ 缺失")
    check("10 gates 作为兜底（args 优先）",
          "if max_rounds is None:" in src and "setting_refine_max_rounds" in src,
          "→ 优先级链不完整")
    check("10 有代码级默认常量", "DEFAULT_MAX_ROUNDS" in src, "→ 缺失")
    check("10 run_auto_thin 暴露 stats（供 orchestrator/测试断言）",
          '"versions"' in src and '"reason"' in src, "→ stats 字段不全")


def case_feedback_reaches_task():
    """端到端：定向 feedback 必须真的**写进 LLM 任务文件**。

    ⚠️ case 6 只孤立调用 `build_auto_feedback` —— 它能证明「函数返回值是对的」，
    但证明不了「`run_auto_thin` 真的把它传下去了」。若某次改动让
    `run_auto_thin` 重新传占位串给 `run_refine`，case 6 依然全绿
    （它压根没进 `run_auto_thin`）。这条用例堵的就是这个缺口：
    直接读子会话**实际收到的任务文件**，断言角色名与缺失维度在里面。

    这就是本项目的老毛病：**单测了「零件正确」，没测「零件被装上」**。
    """
    print("\n【11】端到端：feedback 到达任务文件")
    root = build_sandbox("nf_sr_e2e_")
    write_setting(root, make_setting(n_thin=3, thin_ok=False))
    code = PRELUDE + '''
import setting_refine as srfy
from utils.fake_client import FakeClient

cfg = __import__("yaml").safe_load(read_text("config/system.yaml"))
proj = __import__("yaml").safe_load(read_text("config/project.yaml"))

CAPTURED = {"task": None}

import utils.fake_client as fc
_orig_write = fc.FakeClient.write_task
def cap_write(self, task_dir, name, body):
    CAPTURED["task"] = (str(name), body)
    return _orig_write(self, task_dir, name, body)
fc.FakeClient.write_task = cap_write

# 让子会话「什么都不做」：本轮必然无进展，闭环 1 轮即停，任务文件已捕获
_orig_run = fc.FakeClient.run_task
def noop(self, task_file, workdir=None, model=None):
    return {"exit_code": 0, "stdout_tail": "", "tokens": 100, "tokens_out": 50,
            "cost_yuan": 0.0, "estimated": True, "provider": "fake",
            "model": "fake", "requests": 1}
fc.FakeClient.run_task = noop

client = FakeClient()
ok, msg, stats = srfy.run_auto_thin(cfg, proj, client=client,
                                    task_dir="data/state/tasks", max_rounds=1)
nm, body = CAPTURED["task"] or ("", "")
_ticket(ok=ok, name=nm, body=body, rounds=stats["rounds"])
'''
    p, out, errout = run_py(root, code)
    if p is None:
        check(False, "11 子进程产出结果", f"stdout={out[-600:]} stderr={errout[-600:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    body, name = p["body"], p["name"]
    print(f"  任务文件={name} 长度={len(body)}")
    check("11 走的是 stage1_refine 模板", name == "stage1_refine.md", f"→ {name}")
    check("11 任务文件非空", len(body) > 200, f"→ {len(body)}")
    # ⚠️ 判别力说明（实测）：下面三条「含角色名」断言**不判别** feedback 是否传对
    # —— `run_refine` 里的 `review_thin`（体检得出的 THIN 名单）会独立往模板里
    # 塞同样的名字。注入 D6（把占位串传给 run_refine）时这三条**仍然全绿**。
    # 真正判别的是随后三条：缺失维度 / 轮次进度 / 无占位串。
    # 保留它们只为防「整体断链」，不要把它们当成定向 feedback 的证据。
    for who in ("碎片甲", "碎片乙", "碎片丙"):
        check(f"11 任务文件含角色名「{who}」", who in body, "→ 未出现")
    check("11 任务文件含缺失维度标注（身份/定位 等）",
          "身份/定位" in body or "能力/特殊设定" in body, "→ 缺维度列表")
    check("11 任务文件含定向箭头「→ 缺」（build_auto_feedback 独有形态）",
          "→ 缺" in body, "→ 未见定向清单")
    check("11 任务文件含 llm_inferred 约束", "llm_inferred" in body)
    check("11 任务文件含轮次进度", "1/1" in body, "→ 未见轮次")
    check("11 **任务文件不含旧占位串**", "（见体检报告）" not in body,
          "→ 占位串漏进任务")
    shutil.rmtree(root, ignore_errors=True)


def case_orchestrator_hook_callable():
    """orchestrator 钩子的**可执行契约**：签名必须绑得上。

    ## 为什么单独立一条

    orchestrator 的钩子被 `try/except Exception` 包着，只 print 一行
    「设定补全(auto-thin) 失败（不影响流程）」。这意味着**签名不匹配、
    导入失败、参数名写错**这类问题会**静默降级** —— 用户开了开关、
    看到流程正常结束，功能却从未生效。这是本项目最忌讳的失败模式。

    静态断言（`"setting_refine_auto" in src`，case 7）挡不住它：
    字符串在不在，与调用能不能成立是两件事。这里把 AST 取出的
    **真实调用形态**拿去 `inspect.signature().bind()`，绑不上就红。

    这也是 MEMORY.md 的纪律：锚定**契约**，不锚定实现位置；
    用 **AST**，不用源码文本切块。
    """
    print("\n【12】orchestrator 钩子签名可绑定（防静默降级）")
    import ast
    import inspect

    src = (REPO / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    call = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run_auto_thin"):
            call = node
            break
    if call is None:
        check(False, "12 找到 orchestrator 里的 run_auto_thin 调用", "→ 未找到")
        return
    check("12 找到 orchestrator 里的 run_auto_thin 调用", True)

    # 调用形态 → 参数名（位置参数记为 <pos#>，值不参与，只为 bind 占位）
    pos_n = len(call.args)
    kw = [k.arg for k in call.keywords if k.arg]
    print(f"  调用形态: {pos_n} 个位置参数 + kwargs={kw}")

    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import setting_refine as srfy
    finally:
        sys.path.pop(0)

    sig = inspect.signature(srfy.run_auto_thin)
    params = list(sig.parameters)
    check("12 run_auto_thin 首个位置参数是 cfg", params and params[0] == "cfg",
          f"→ {params[:3]}")
    check("12 第二个位置参数是 proj", len(params) > 1 and params[1] == "proj",
          f"→ {params[:3]}")

    # 真绑定：每个 kwargs 必须存在于签名（拼错名字立刻失败）
    dummy = {p: None for p in params}
    for k in kw:
        dummy[k] = None
    try:
        sig.bind(**dummy)
        bind_ok, bind_err = True, ""
    except TypeError as e:
        bind_ok, bind_err = False, str(e)
    check("12 钩子的 kwargs 全部能绑定到 run_auto_thin 签名（拼错即红）",
          bind_ok, f"→ {bind_err}")
    check("12 钩子显式传了 client（复用同一客户端，不另起）", "client" in kw,
          f"→ {kw}")
    check("12 钩子显式传了 max_rounds（受 gates 控制）", "max_rounds" in kw,
          f"→ {kw}")

    check("12 DEFAULT_MAX_ROUNDS 是正整数", 
          isinstance(srfy.DEFAULT_MAX_ROUNDS, int) and srfy.DEFAULT_MAX_ROUNDS >= 1,
          f"→ {srfy.DEFAULT_MAX_ROUNDS!r}")

    # 返回值必须是 3 元组解包形态（orchestrator 写的是 ok_sr, msg_sr, _st = ...）
    # ⚠️ 解包赋值里 `targets[0]` 是 **Tuple**（三个 Name），不是单个 Name —
    # 写成 `any(isinstance(x, ast.Name) …)` 会永远为假（空断言）。
    unpack3 = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not node.targets:
            continue
        t0 = node.targets[0]
        if not isinstance(t0, ast.Tuple) or len(t0.elts) != 3:
            continue
        names = [e.id for e in t0.elts if isinstance(e, ast.Name)]
        if "ok_sr" in names and len(names) == 3:
            unpack3 = True
            break
    check("12 钩子按 3 元组解包（ok_sr, msg_sr, _st）", unpack3,
          "→ 解包形态不符")
    # 返回 stats 含 orchestrator 可能用到的键
    real = srfy.run_auto_thin.__doc__ or ""
    check("12 stats 契约在 docstring 里写明（rounds/thin_before/thin_after/reason/versions）",
          all(k in real for k in ("rounds", "thin_before", "thin_after",
                                  "reason", "versions")),
          "→ docstring 未写明 stats 契约")


def main():
    print("=" * 70)
    print("setting_refine --auto-thin 闭环自检（fake，临时工作目录，零真实调用）")
    print("=" * 70)
    for fn in (case_already_ok, case_reached_ok_first_round, case_no_progress,
               case_max_rounds, case_max_rounds_one, case_feedback_is_directed,
               case_gate_default_off, case_dry_run, case_no_setting,
               case_cli_rounds_priority, case_feedback_reaches_task,
               case_orchestrator_hook_callable):
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
