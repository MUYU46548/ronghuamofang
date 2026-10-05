# -*- coding: utf-8 -*-
"""Agent 模式 CLI 守卫自检（P2，2026-10-03；2026-10-04 TTY 加固跟进）。零 LLM、零网络。

## 为什么需要

`gates.agent_mode = true` 的语义（2026-09-21 用户确认）是：
**外部 Agent 可读、可跑流水线，但不能代替用户审批**。

HTTP 层早就有这个守卫（`/approve` 等 7 条路径 → 403），但**同一条禁令还有一条
CLI 后门**：外部 Agent 完全可以直接

    python scripts/approve.py --stage 2

照着 AGENTS.md 的文档跑一条命令就替用户拍板了 —— 判据只写在 HTTP 层，
CLI 层就是敞开的。本用例守两件事：

1. **判据只有一份**（`utils/agent_guard`）：禁止清单、模式判定、审计落盘，
   HTTP 与 CLI 共用；任一侧改动都会让另一侧的行为断言变红。
2. **拒绝发生在任何写操作之前**：progress.json 不许被改（审批没落地）、
   data/state/agent_audit.jsonl 要留下 blocked 记录。

2026-10-04 TTY 加固跟进（修复单第三节，用例随新规格改）：
- 加固档（agent_mode=true）下声明通道（--human / MOFANG_SOURCE=gui）死透
  → 用例 4/7 的「声明放行」断言**反转**为「声明照样被拒」（V1/V2 死透）；
- 拒文 V5 零通道名 → 用例 2/3 的文案断言反转（不得再出现 --human 等通道指路）；
- 正路（真实交互终端）= 用例 2b 单元级注入祖先链断言；真实终端的动态放行验收
  归红队/用户亲手 TTY（修复方不自验，Step 4b）。

用法：python tests/unit/test_agent_mode_cli_guard.py
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
sys.path.insert(0, str(ROOT / "scripts"))

from utils import agent_guard                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


# ============================================================ 1. 共享判据
def case_shared_predicate():
    print("\n【1】共享判据：禁止清单 / 模式判定（HTTP 与 CLI 同一份）")
    check("agent_mode 缺键 → 关闭（默认不改行为）",
          agent_guard.agent_mode_enabled({}) is False)
    check("gates.agent_mode=true → 开启",
          agent_guard.agent_mode_enabled({"gates": {"agent_mode": True}}) is True)
    check("gates 结构异常不抛（按关闭处理）",
          agent_guard.agent_mode_enabled({"gates": None}) is False
          and agent_guard.agent_mode_enabled(None) is False)
    for p in ("/approve", "/reject", "/project/create", "/project/archive",
              "/project/restore", "/project/init", "/config/agent_mode"):
        check("禁止清单含 %s" % p, agent_guard.is_forbidden_in_agent_mode(p))
    for p in ("/state", "/stage/4/run", "/outline/save", "/sandbox/review"):
        check("只读/沙盒类不受限：%s" % p,
              agent_guard.is_forbidden_in_agent_mode(p) is False)
    check("来源归一：大小写/空白不影响判定",
          agent_guard.normalize_source(" GUI ") == "gui")


def case_human_evidence():
    print("\n【2】「人工来源」取证·标准档历史语义（agent_mode=false，接口兼容反证）；加固档见【2b】")
    check("MOFANG_SOURCE=gui → 人工",
          agent_guard.cli_human_evidence(source_env="gui", isatty=False)[0] is True)
    check("MOFANG_SOURCE=agent → 非人工",
          agent_guard.cli_human_evidence(source_env="agent", isatty=True)[0] is False)
    check("两端都是 TTY → 人工",
          agent_guard.cli_human_evidence(source_env="", isatty=(True, True))[0] is True)
    check("**只有 stdin 是 TTY** → 非人工（输出被管道接管 = 被程序捕获）",
          agent_guard.cli_human_evidence(source_env="", isatty=(True, False))[0] is False)
    check("**只有 stdout 是 TTY** → 非人工（stdin 被喂数据 = 程序驱动）",
          agent_guard.cli_human_evidence(source_env="", isatty=(False, True))[0] is False)
    check("都不是 TTY → 非人工（外部 Agent 的典型形态）",
          agent_guard.cli_human_evidence(source_env="", isatty=(False, False))[0] is False)
    check("显式 --human → 人工（人在非交互环境里的逃生门）",
          agent_guard.cli_human_evidence(source_env="", isatty=False,
                                         explicit_human=True)[0] is True)
    check("MOFANG_SOURCE=agent 优先于 --human（显式标了非人工，不许再自我加冕）",
          agent_guard.cli_human_evidence(source_env="agent", isatty=False,
                                         explicit_human=True)[0] is False)
    msg = agent_guard.refusal_message("/approve", "证据", "approve.py")
    check("V5 拒文：点名 agent_mode + 停手/报告用户指引",
          all(k in msg for k in ("不是人工来源", "agent_mode", "停手", "报告用户")),
          msg[:160])
    check("V5 拒文零通道名（--human / MOFANG_SOURCE / pty / 交互式终端 / 改配置退路）",
          all(k not in msg for k in
              ("--human", "MOFANG_SOURCE", "pty", "交互式终端", "设为 false")), msg[:160])


# ================================================ 2b. agent_mode=true 加固档（2026-10-04）
def case_agent_mode_strict():
    print("\n【2b】加固档：声明死透（V1/V2）+ 双端 TTY 快筛 + 祖先链主判据（fail-closed）")
    GOOD = ["powershell.exe", "WindowsTerminal.exe", "explorer.exe"]

    # 正路（单元级注入；真实终端动态验收归红队/用户）
    ok, ev = agent_guard.cli_human_evidence(
        source_env="", isatty=(True, True), agent_mode=True, ancestry=GOOD)
    check("正路：双端 TTY + 用户终端链 → 放行", ok is True, ev)
    check("放行证据带完整进程链（台账可追责）", "explorer" in ev, ev)

    # 自动化宿主（V3：hermes 今早的 pty 形态 —— TTY 齐全也照拒）
    for chain, label in (
            (["bash.exe", "Hermes.exe", "explorer.exe"], "hermes"),
            (["node.exe", "explorer.exe"], "node"),
            (["python.exe", "explorer.exe"], "python（执行者包装层）"),
            (["winpty-agent.exe", "explorer.exe"], "winpty（pty 孵化器）")):
        ok, ev = agent_guard.cli_human_evidence(
            source_env="", isatty=(True, True), agent_mode=True, ancestry=chain)
        check("祖先链含 %s → 拒（快筛 TTY 全过也照拒）" % label,
              ok is False and "自动化宿主" in ev, ev)

    # 无锚点
    ok, ev = agent_guard.cli_human_evidence(
        source_env="", isatty=(True, True), agent_mode=True,
        ancestry=["svchost.exe", "services.exe"])
    check("链不落在用户 shell/explorer → 拒", ok is False and "未落在用户交互终端" in ev, ev)

    # fail-closed
    ok, ev = agent_guard.cli_human_evidence(
        source_env="", isatty=(True, True), agent_mode=True, ancestry=None)
    check("取证失败 → fail-closed 拒", ok is False and "取证失败" in ev, ev)
    ok, ev = agent_guard.cli_human_evidence(
        source_env="", isatty=(True, True), agent_mode=True, ancestry=["bash.exe", "?"])
    check("链截断（未知祖先段无法核验）→ 拒", ok is False and "不完整" in ev, ev)

    # V1/V2 死透：声明救不了 TTY 缺失，也救不了脏链
    ok, ev = agent_guard.cli_human_evidence(
        source_env="gui", isatty=(False, False), agent_mode=True, ancestry=GOOD)
    check("V2：MOFANG_SOURCE=gui 但无 TTY → 拒（声明不构成证据）", ok is False, ev)
    ok, ev = agent_guard.cli_human_evidence(
        source_env="", isatty=(False, False), explicit_human=True,
        agent_mode=True, ancestry=GOOD)
    check("V1：--human 但无 TTY → 拒", ok is False, ev)
    ok, ev = agent_guard.cli_human_evidence(
        source_env="gui", isatty=(True, True), agent_mode=True,
        ancestry=["bash.exe", "node.exe"])
    check("声明 + TTY + 宿主链 → 照样拒", ok is False, ev)
    check("声明痕迹进证据（台账可追责）", "不构成人工证据" in ev, ev)

    # 前置快筛：半端 TTY（管道接管）拒
    ok, ev = agent_guard.cli_human_evidence(
        source_env="", isatty=(True, False), agent_mode=True, ancestry=GOOD)
    check("只有一端 TTY → 拒（前置快筛）", ok is False, ev)

    # 实测冒烟：不抛异常，返回 None 或字符串列表
    chain = agent_guard.probe_ancestry()
    check("probe_ancestry 实测不抛（None 或 list[str]）",
          chain is None or (isinstance(chain, list)
                            and all(isinstance(x, str) for x in chain)), chain)


# ============================================================ 3. CLI 真跑
def build_root(agent_mode):
    tmp = Path(tempfile.mkdtemp(prefix="nf_guard_"))
    (tmp / "config").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "config" / "system.yaml").write_text(
        "engine: hermes\ngates:\n  agent_mode: %s\n" % ("true" if agent_mode else "false"),
        encoding="utf-8")
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "守卫测试书"\n', encoding="utf-8")
    (tmp / "data" / "state" / "progress.json").write_text(
        json.dumps({"project": "守卫测试书", "budget": {"paused": False},
                    "stages": {"1": {"status": "done", "approved": False}}},
                   ensure_ascii=False), encoding="utf-8")
    return tmp


def run_approve(tmp, extra_args=(), env_extra=None):
    """在临时根跑 approve.py（管道捕获 = 无 TTY = 非人工形态）。"""
    env = dict(os.environ)
    env.pop("MOFANG_SOURCE", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "approve.py"), "--stage", "1",
         *extra_args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(tmp), env=env, timeout=120)
    return proc


def approved(tmp):
    try:
        data = json.loads((tmp / "data" / "state" / "progress.json").read_text("utf-8"))
        return bool(data["stages"]["1"].get("approved"))
    except Exception as e:                                    # noqa: BLE001
        return "读取失败: %r" % e


def audit_entries(tmp):
    p = tmp / "data" / "state" / "agent_audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def case_cli_refuses_non_human():
    print("\n【3】agent_mode=true 且非人工来源 → CLI 拒绝，且**不写任何东西**")
    tmp = build_root(agent_mode=True)
    try:
        p = run_approve(tmp)
        check("退出码非零（拒绝执行）", p.returncode == 1, p.returncode)
        check("报错点明「不是人工来源」", "不是人工来源" in p.stdout, p.stdout[-300:])
        check("V5 拒文零通道名（不教 --human / MOFANG_SOURCE / pty / 交互终端 / 改配置）",
              all(k not in p.stdout for k in
                  ("--human", "MOFANG_SOURCE", "pty", "交互式终端", "设为 false")),
              p.stdout[-300:])
        check("V5 拒文要求停手并报告用户",
              "停手" in p.stdout and "报告用户" in p.stdout, p.stdout[-300:])
        check("**审批没有被写入 progress.json**（守卫在任何写操作之前）",
              approved(tmp) is False, approved(tmp))
        entries = audit_entries(tmp)
        check("审计台账留下 blocked 记录",
              len(entries) == 1 and entries[0]["status"] == 403,
              entries)
        check("审计记录标出非人工来源", entries and entries[0]["source"] != "gui", entries)
        p2 = run_approve(tmp, env_extra={"MOFANG_SOURCE": "agent"})
        check("显式 MOFANG_SOURCE=agent 同样被拒", p2.returncode == 1, p2.returncode)
        check("仍未被写入", approved(tmp) is False, approved(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_cli_declared_channels_dead():
    print("\n【4】加固档：声明通道死透（V1/V2）—— --human / MOFANG_SOURCE=gui 照样被拒")
    tmp = build_root(agent_mode=True)
    try:
        p = run_approve(tmp, env_extra={"MOFANG_SOURCE": "gui"})
        check("V2：MOFANG_SOURCE=gui → 照样拒绝（退 1）", p.returncode == 1, p.returncode)
        check("V2：审批没有落地", approved(tmp) is False, approved(tmp))
        check("V2：声明痕迹进 blocked 台账（不构成人工证据）",
              any("不构成人工证据" in (e.get("detail") or "")
                  for e in audit_entries(tmp)), audit_entries(tmp))

        p2 = run_approve(tmp, extra_args=("--human",))
        check("V1：--human → 照样拒绝（退 1）", p2.returncode == 1, p2.returncode)
        check("V1：审批仍未落地", approved(tmp) is False, approved(tmp))
        entries = audit_entries(tmp)
        check("两次尝试都留 blocked 台账",
              sum(1 for e in entries if e.get("status") == 403) >= 2, entries)
        # 注：正路「放行也留痕（allowed）」无法在 agent 宿主环境自验（加固档下
        # 本机任何自动化形态都过不了祖先链 —— 这正是修复目标）；
        # 真实终端的放行 + allowed 台账由红队/用户亲手 TTY 跑出（验收 2）。
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_standard_mode_unaffected():
    print("\n【5】反证：agent_mode=false 时 CLI 行为与改动前一致")
    tmp = build_root(agent_mode=False)
    try:
        p = run_approve(tmp)
        check("标准模式下裸调用照常执行（approve 落地）", approved(tmp) is True,
              p.stdout[-300:])
        check("标准模式**不写** Agent 审计台账（不制造噪音）",
              audit_entries(tmp) == [], audit_entries(tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_reject(tmp, extra_args=(), env_extra=None):
    """在临时根跑 reject.py（管道捕获 = 无 TTY = 非人工形态）。"""
    env = dict(os.environ)
    env.pop("MOFANG_SOURCE", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "reject.py"), "--stage", "6",
         "--reason", "测试", *extra_args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(tmp), env=env, timeout=120)
    return proc


def case_reject_cli_guarded():
    print("\n【7】reject.py 同款守卫（打回也是「代替用户拍板」，只补 approve 等于半开）")
    tmp = build_root(agent_mode=True)
    try:
        p = run_reject(tmp)
        check("agent_mode=true + 非人工 → 拒绝", p.returncode == 1, p.returncode)
        check("报错点明不是人工来源", "不是人工来源" in p.stdout, p.stdout[-200:])
        check("**progress.json 未被写入**（守卫在打回写操作之前）",
              json.loads((tmp / "data" / "state" / "progress.json").read_text("utf-8")
                         )["stages"]["1"].get("rejected") is None)
        entries = audit_entries(tmp)
        check("审计台账记 /reject 的 blocked",
              any(e["path"] == "/reject" and e["status"] == 403 for e in entries), entries)
        p2 = run_reject(tmp, env_extra={"MOFANG_SOURCE": "gui"})
        check("V2：reject 同样不认声明通道（打回也是拍板，照样拒）",
              p2.returncode == 1, p2.returncode)
        st = json.loads((tmp / "data" / "state" / "progress.json").read_text("utf-8"))
        check("两次尝试都未写入 rejected（守卫先于一切写操作）",
              st["stages"].get("6", {}).get("rejected") is None, st.get("stages"))
        entries = audit_entries(tmp)
        check("reject 两次 blocked 都在台账",
              sum(1 for e in entries if e["path"] == "/reject" and e["status"] == 403) >= 2,
              entries)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_http_layer_uses_same_set():
    print("\n【6】HTTP 层确实改用共享判据（源码断言，防两侧漂移）")
    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("do_POST 守卫调用 agent_guard.is_forbidden_in_agent_mode",
          "agent_guard.is_forbidden_in_agent_mode(p)" in src)
    check("模式判定走 agent_guard.agent_mode_enabled",
          "agent_guard.agent_mode_enabled(cfg_check)" in src)
    check("禁止清单不再在 nf_api 里**另写一份**",
          "_FORBIDDEN_POST = {" not in src)
    check("审计落盘也委托共享实现",
          "agent_guard.log_audit(" in src)
    for script, action in (("approve.py", "/approve"), ("reject.py", "/reject")):
        s = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        check("%s 有 agent_mode 守卫（CLI 后门已堵）" % script,
              "agent_guard.agent_mode_enabled" in s and action in s, script)
        check("%s 接线加固档（cli_human_evidence 传 agent_mode=True）" % script,
              "agent_mode=True" in s, script)
    check("HTTP 403 拒文共用零通道文案（V5，不教「仅 GUI」通道）",
          "refusal_message(" in src and "仅 GUI 可执行" not in src)


def case_audit_detail_intact():
    print("\n【8】审计 detail 不截断声明痕迹（红队 2026-10-05 实测发现的自伤）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_audit_"))
    try:
        # 真实尺寸：16 跳祖先链 + 声明痕迹（原 [:120] 在这个长度必切到声明）
        chain = " → ".join(["python.exe", "cmd.exe", "python.exe", "bash.exe",
                            "Hermes.exe", "explorer.exe"] * 3)
        ev = ("blocked: 祖先链含自动化宿主 python.exe（链: %s）；附带声明"
              "（不构成人工证据）: 声明来源=gui、声明--human" % chain)
        check("回归现场：该证据确实超过原 120 上限", len(ev) > 120, len(ev))
        agent_guard.log_audit("/approve", method="CLI", status=403, source="gui",
                              detail=ev, root=str(tmp))
        p = tmp / "data" / "state" / "agent_audit.jsonl"
        entry = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
        check("声明痕迹在台账完整可读（V1/V2 留痕可追责）",
              "不构成人工证据" in entry["detail"] and "声明--human" in entry["detail"],
              entry["detail"][-160:])
        check("进程链在台账完整可读", "explorer.exe" in entry["detail"],
              entry["detail"][:160])
        # 上限仍在（防有人直接删 cap 导致 JSONL 无界膨胀）
        agent_guard.log_audit("/x", method="POST", status=200, source="gui",
                              detail="Z" * 5000, root=str(tmp))
        last = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
        check("detail 仍有上限（≤600，防 JSONL 无界膨胀）",
              len(last["detail"]) <= 600, len(last["detail"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("  Agent 模式 CLI 守卫自检（P2）")
    print("=" * 62)
    case_shared_predicate()
    case_human_evidence()
    case_agent_mode_strict()
    case_cli_refuses_non_human()
    case_cli_declared_channels_dead()
    case_standard_mode_unaffected()
    case_reject_cli_guarded()
    case_http_layer_uses_same_set()
    case_audit_detail_intact()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
