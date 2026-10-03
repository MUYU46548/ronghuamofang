# -*- coding: utf-8 -*-
"""token 级熔断自检（P0 止烧，2026-10-03）。零 LLM、零网络。

## 为什么需要

`engine: hermes` 下 `estimate_cost_yuan` 对 provider="hermes" **恒返 0**
（订阅流量，刻意的语义）→ `cost_log.cost_yuan` 全是 0 →
`budget.limit_yuan` **永远不会命中**。也就是说：切到 hermes 之后，
整条流水线**没有任何止烧闸门**，一次跑几十章可以一路烧到没边。

本用例守四件事：

1. **累计 token 会熔断**（走的是**现有** budget/pause 机制：`CostTracker.status` →
   orchestrator/stage 的 `state == "pause"` 分支 → `progress.budget.paused` + 退出码 2，
   没有新增状态机）；
2. **判据只有一份**：`status_detail()` 是唯一来源，`status()` 只是它的二元组包装
   （老调用点零改动）；
3. **不误停**：实测 hermes 一次 stage1 子会话输出就有 1.8 万 token（夹具照抄真实
   数值），`per_request_pause_hermes=false`（默认）时**不得**因为单次输出大就停机 ——
   误停比不停更贵；
4. **不误伤旧行为**：配置里没有 `token_limit` 时（老配置、测试替身），
   `CostTracker` 的行为与加 token 判据之前**逐项一致**。

用法：python tests/unit/test_token_circuit.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.db import RunDB                                        # noqa: E402
from utils.cost_tracker import (CostTracker, normalize_token_limit,  # noqa: E402
                                pause_detail, estimate_cost_yuan)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def fresh_db(tag):
    tmp = Path(tempfile.mkdtemp(prefix="token_circuit_%s_" % tag))
    return RunDB(str(tmp / "runs.db")), tmp


# ============================================================ 1. 配置归一
def case_normalize():
    print("\n【1】budget.token_limit 归一：缺键取默认，不因坏值崩")
    d = normalize_token_limit(None)
    check("缺省 = 关闭（老配置行为不变）",
          d["enabled"] is False and d["max_total_tokens"] == 0, d)
    d = normalize_token_limit({"enabled": True, "max_total_tokens": "3000000",
                               "per_request_max_tokens": 8000})
    check("字符串数值被强制成 int",
          d["max_total_tokens"] == 3000000 and d["per_request_max_tokens"] == 8000, d)
    d = normalize_token_limit({"enabled": True, "warn_ratio": 0})
    check("非法 warn_ratio 回落 0.7（不是 0 → 一上来就 warn）", d["warn_ratio"] == 0.7, d)
    d = normalize_token_limit({"enabled": True, "不存在的键": 1})
    check("未知键被忽略（不写入内部状态）", "不存在的键" not in d, d)
    d = normalize_token_limit({"max_total_tokens": "abc"})
    check("坏值不抛异常（保持默认 0）", d["max_total_tokens"] == 0, d)


# ============================================================ 2. 记账口径
def case_db_accounting():
    print("\n【2】cost_log 的 token 记账：累计与单次最大输出")
    db, tmp = fresh_db("db")
    try:
        rid = db.start_run("t")
        db.log_cost(rid, 1, 0, "m", 98241, 18358, 0.0, estimated=0, cache_read=0)
        db.log_cost(rid, 4, 1, "m", 30000, 2000, 0.0, estimated=0, cache_read=0)
        rid2 = db.start_run("t2")
        db.log_cost(rid2, 1, 0, "m", 111, 222, 0.0, estimated=0, cache_read=0)
        check("累计 token（输入+输出）", db.sum_tokens(rid) == 98241 + 18358 + 30000 + 2000,
              db.sum_tokens(rid))
        check("按 run 隔离（第二轮不串账）", db.sum_tokens(rid2) == 333, db.sum_tokens(rid2))
        check("单次最大**输出** token（不是输入）",
              db.max_call_tokens_out(rid) == 18358, db.max_call_tokens_out(rid))
        check("无记账时累计为 0（不抛）", db.sum_tokens(None) > 0, db.sum_tokens(None))
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================ 3. 熔断判据
def case_token_pause():
    print("\n【3】累计 token 超限 → pause（hermes 的主力闸门）")
    db, tmp = fresh_db("pause")
    try:
        rid = db.start_run("t")
        cost = CostTracker(db, limit_yuan=300, warn_ratio=0.7,
                           token_limit={"enabled": True, "max_total_tokens": 100000,
                                        "per_request_max_tokens": 8000})
        # hermes：¥ 恒 0 —— 这正是「金额阈值是虚设」的实证
        cost.charge_cost(rid, 4, 1, {"tokens": 20000, "tokens_out": 5000,
                                     "provider": "hermes", "model": "hermes-x"})
        d1 = cost.status_detail(rid)
        check("未超限 → ok", d1["state"] == "ok", d1)
        check("hermes 记账恒 0 元（金额判据不可能命中）",
              d1["spent_yuan"] == 0.0, d1)
        cost.charge_cost(rid, 4, 2, {"tokens": 90000, "tokens_out": 4000,
                                     "provider": "hermes", "model": "hermes-x"})
        d2 = cost.status_detail(rid)
        check("累计 119000 ≥ 100000 → pause", d2["state"] == "pause", d2)
        check("原因点明是 token 累计（不是金额）", d2["reason"] == "tokens_total", d2)
        check("消息带具体数值（用户据此决定调哪个键）",
              "119000" in d2["message"] and "100000" in d2["message"], d2["message"])
        st, spent = cost.status(rid)
        check("status() 二元组兼容且与 detail 同判据",
              st == "pause" and spent == 0.0, (st, spent))
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def case_no_false_stop_on_hermes_single_call():
    print("\n【4】反证：hermes 单次子会话输出大 ≠ 熔断（默认不误停）")
    db, tmp = fresh_db("single")
    try:
        rid = db.start_run("t")
        # 夹具照抄实测：一次 stage1 子会话 输入 98241 / 输出 18358
        cost = CostTracker(db, limit_yuan=300, warn_ratio=0.7,
                           token_limit={"enabled": True, "max_total_tokens": 3000000,
                                        "per_request_max_tokens": 8000,
                                        "per_request_pause_hermes": False})
        cost.charge_cost(rid, 1, 0, {"tokens": 98241, "tokens_out": 18358,
                                     "provider": "hermes", "model": "hermes-x"})
        d = cost.status_detail(rid)
        check("单次输出 18358 > 8000 但**不熔断**（默认只告警）",
              d["state"] != "pause", d)
        # 显式开启后才熔断
        cost2 = CostTracker(db, limit_yuan=300, warn_ratio=0.7,
                            token_limit={"enabled": True, "max_total_tokens": 3000000,
                                         "per_request_max_tokens": 8000,
                                         "per_request_pause_hermes": True})
        d2 = cost2.status_detail(rid)
        check("显式开启 per_request_pause_hermes 后 → pause",
              d2["state"] == "pause" and d2["reason"] == "tokens_request", d2)
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def case_warn_ratio():
    print("\n【5】预警区（warn 不熔断）")
    db, tmp = fresh_db("warn")
    try:
        rid = db.start_run("t")
        cost = CostTracker(db, limit_yuan=300, warn_ratio=0.7,
                           token_limit={"enabled": True, "max_total_tokens": 100000,
                                        "warn_ratio": 0.7})
        cost.charge_cost(rid, 4, 1, {"tokens": 75000, "tokens_out": 100,
                                     "provider": "hermes", "model": "m"})
        d = cost.status_detail(rid)
        check("累计 75000 ≥ 70000（预警线）→ warn", d["state"] == "warn", d)
        check("warn 不影响流程（orchestrator 只在 pause 时停机）",
              cost.status(rid)[0] == "warn", cost.status(rid))
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================ 6. 旧行为不误伤
def case_legacy_behavior_unchanged():
    print("\n【6】回归：没有 token_limit 配置时，行为与加判据之前一致")
    db, tmp = fresh_db("legacy")
    try:
        rid = db.start_run("t")
        cost = CostTracker(db, limit_yuan=1, warn_ratio=0.7)      # 关键：不传 token_limit
        check("token_limit 缺省 = 关闭", cost.token_limit["enabled"] is False,
              cost.token_limit)
        # 烧掉天文数字的 token：金额仍是 0（hermes）→ 不得因 token 熔断
        cost.charge_cost(rid, 1, 0, {"tokens": 10 ** 9, "tokens_out": 10 ** 9,
                                     "provider": "hermes", "model": "m"})
        check("token 判据关闭时，天文数字 token 也不熔断（老行为）",
              cost.status(rid)[0] == "ok", cost.status(rid))
        check("status_detail 仍可用（返回 ok）", cost.status_detail(rid)["state"] == "ok",
              cost.status_detail(rid))
        # 金额判据照旧：direct 引擎计费 > 1 元 → pause
        rid2 = db.start_run("t2")
        cost2 = CostTracker(db, limit_yuan=1, warn_ratio=0.7)
        cost2.charge_cost(rid2, 4, 1, {"tokens": 1_000_000, "tokens_out": 1_000_000,
                                       "provider": "tokenhub", "model": "kimi-k2.6"})
        check("金额判据未被改动（direct 超限 → pause）",
              cost2.status(rid2)[0] == "pause", cost2.status(rid2))
        check("原因标明是金额", cost2.status_detail(rid2)["reason"] == "yuan",
              cost2.status_detail(rid2))
        check("¥ 记账在 hermes 下为 0（虚设的实证）",
              estimate_cost_yuan(10 ** 6, 10 ** 6, provider="hermes") == 0.0, "not 0")
    finally:
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def case_pause_detail_survives_stub_cost():
    print("\n【7】print 用诊断不得因测试替身缺方法而打断生产线")
    class StubCost:
        def spent(self, run_id=None):
            return 1.25
    d = pause_detail(StubCost(), 1)
    check("替身只有 spent() 也能取到诊断", d["reason"] == "unknown" and d["spent_yuan"] == 1.25, d)

    class Boom:
        def status_detail(self, run_id=None):
            raise RuntimeError("boom")

        def spent(self, run_id=None):
            raise RuntimeError("boom")
    d2 = pause_detail(Boom(), 1)
    check("连 spent() 都抛也不抛出去", d2["reason"] == "unknown", d2)


# ============================================================ 8. orchestrator 集成
SANDBOX_ORCH = r'''
import json, os, sys
sys.path.insert(0, r"{scripts}")
os.chdir(r"{root}")
import orchestrator as O
from utils.progress_manager import ProgressManager

CALLS = []

def _stage(stage_no):
    """假阶段：往账本记一大笔 token，然后宣告阶段成功。"""
    class S:
        @staticmethod
        def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
            CALLS.append(stage_no)
            cost.charge_cost(run_id, stage_no, 0,
                             {{"tokens": 120000, "tokens_out": 4000,
                               "provider": "hermes", "model": "hermes-x"}})
            progress.set_stage(stage_no, "done")
            return True, "模拟阶段成功"
    return S

O.STAGES = {{n: _stage(n) for n in range(1, 10)}}
O.snap = type("S", (), {{"snapshot": staticmethod(lambda lbl: None)}})()
O._stop_requested = lambda: False
O.load_config = lambda: (
    {{"engine": "hermes",
      "gates": {{"pause_on_failure": True, "require_approval": [],
                 "quality_gate": False, "auto_retry": False}},
      "budget": {{"limit_yuan": 300, "warn_ratio": 0.7,
                 "token_limit": {{"enabled": True,
                                  "max_total_tokens": {cap},
                                  "per_request_max_tokens": 8000,
                                  "per_request_pause_hermes": False}}}}}},
    {{"book": {{"name": "token 熔断测试书"}}}})

err, rc = None, None
try:
    rc = O.run(from_stage=1)
except Exception as e:
    err = type(e).__name__ + ": " + str(e)

pm = ProgressManager(r"{root}/data/state/progress.json")
print("__RESULT__" + json.dumps({{
    "rc": rc, "err": err, "calls": CALLS,
    "paused": bool(pm.data.get("budget", {{}}).get("paused")),
}}))
'''


def run_sandbox(cap):
    """起一个临时项目根，真跑 orchestrator.run()（token 上限 = cap）。

    子进程带 PYTHONIOENCODING=utf-8：orchestrator 的启动横幅里有 ¥ 等非 GBK 字符，
    管道下（capture_output）Python 用 cp936 → UnicodeEncodeError 把启动搞崩。
    这是本项目既有约定（见 test_outline_iteration / test_setting_refine_auto）。
    """
    import subprocess
    root = Path(tempfile.mkdtemp(prefix="nf_token_orch_"))
    for sub in ("data/state", "logs", "config"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "config" / "system.yaml").write_text(
        "engine: hermes\nbudget:\n  limit_yuan: 300\n  token_limit:\n    enabled: true\n"
        "    max_total_tokens: %d\n" % cap, encoding="utf-8")
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: token 熔断测试书\n  chapters: 2\n", encoding="utf-8")
    code = SANDBOX_ORCH.format(scripts=str(ROOT / "scripts"), root=str(root), cap=cap)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=180,
                          cwd=str(root), env=env)
    out = proc.stdout or ""
    if "__RESULT__" not in out:
        return None, out, proc.stderr or "", root
    payload = json.loads(out.split("__RESULT__", 1)[1].splitlines()[0])
    return payload, out, proc.stderr or "", root


def case_orchestrator_integration():
    print("\n【8】orchestrator 集成：token 超限 → 走**现有** pause 机制（退出码 2）")
    p, out, errout, root = run_sandbox(cap=100000)   # 阶段1 记 12 万 → 一次即超
    try:
        if p is None:
            check(False, "沙箱跑出结果", "stdout=" + out[-400:] + " stderr=" + errout[-400:])
            return
        check("无异常穿透", p["err"] is None, p["err"])
        check("退出码 2（预算/熔断暂停语义，复用既有退出码）", p["rc"] == 2, p["rc"])
        check("progress.budget.paused=True（GUI/续跑据此熔断）", p["paused"] is True, p)
        check("**熔断后不再启动下一阶段**（只跑了阶段 1）", p["calls"] == [1], p["calls"])
        check("日志点明「熔断暂停」并给出 token 数值",
              "熔断暂停" in out and "累计 token" in out, out[-400:])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_orchestrator_no_false_stop():
    print("\n【9】反证：上限放大到跑得完 → 正常跑完，不误停")
    p, out, errout, root = run_sandbox(cap=10 ** 9)
    try:
        if p is None:
            check(False, "沙箱跑出结果", "stdout=" + out[-400:] + " stderr=" + errout[-400:])
            return
        check("无异常穿透", p["err"] is None, p["err"])
        check("未误停（跑了多个阶段）", len(p["calls"]) > 1, p["calls"])
        check("budget.paused 未被置位", p["paused"] is False, p)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_request_cap_at_call_point():
    print("\n【10】单请求上限落在**调用点**（llm_client）")
    from utils.llm_client import (OpenAICompatClient, HermesClient, make_client,
                                  _request_max_tokens_from_cfg)

    check("配置解析：enabled=true → 8000",
          _request_max_tokens_from_cfg(
              {"budget": {"token_limit": {"enabled": True,
                                          "per_request_max_tokens": 8000}}}) == 8000)
    check("配置解析：enabled=false → None（不设上限，老行为）",
          _request_max_tokens_from_cfg(
              {"budget": {"token_limit": {"enabled": False,
                                          "per_request_max_tokens": 8000}}}) is None)
    check("配置解析：缺键 → None", _request_max_tokens_from_cfg({}) is None)
    check("配置解析：坏值 → None（不抛）",
          _request_max_tokens_from_cfg(
              {"budget": {"token_limit": {"enabled": True,
                                          "per_request_max_tokens": "abc"}}}) is None)

    c = OpenAICompatClient(model="m", base_url="http://x", api_key="k",
                           request_max_tokens=8000)
    check("上限优先：调用方要 16000 → 仍发 8000", c.effective_max_tokens(16000) == 8000,
          c.effective_max_tokens(16000))
    check("更小的调用方值保留", c.effective_max_tokens(4000) == 4000,
          c.effective_max_tokens(4000))
    check("只有配置上限 → 用它", c.effective_max_tokens(None) == 8000,
          c.effective_max_tokens(None))
    c2 = OpenAICompatClient(model="m", base_url="http://x", api_key="k")
    check("都没给 → None（不塞 max_tokens，交给 provider 默认）",
          c2.effective_max_tokens(None) is None and c2.effective_max_tokens(0) is None,
          (c2.effective_max_tokens(None), c2.effective_max_tokens(0)))
    check("坏值不抛", c2.effective_max_tokens("abc") is None, c2.effective_max_tokens("abc"))

    # 真的进了 payload（这是「单请求 max_tokens」的唯一证据）
    seen = {}

    def _fake_request_json(payload):
        seen.update(payload)
        return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "model": "m"}

    c._request_json = _fake_request_json
    c._post_chat([{"role": "user", "content": "hi"}])
    check("payload 里真的带 max_tokens=8000", seen.get("max_tokens") == 8000, seen)
    seen.clear()
    c._post_chat([{"role": "user", "content": "hi"}], max_tokens=99999)
    check("调用方要更大 → 仍被上限压住", seen.get("max_tokens") == 8000, seen)
    seen.clear()
    c2._request_json = _fake_request_json
    c2._post_chat([{"role": "user", "content": "hi"}])
    check("未配置上限 → payload 里没有 max_tokens 键", "max_tokens" not in seen, seen)

    # hermes：无旋钮 → 只比对 + 告警（**不阻断**）
    h = HermesClient(request_max_tokens=8000)
    check("hermes 单次 18358 > 8000 → 判超限（告警）", h.note_request_tokens(18358) is True)
    check("hermes 单次 5000 ≤ 8000 → 不告警", h.note_request_tokens(5000) is False)
    h2 = HermesClient()
    check("hermes 未配上限 → 永不告警", h2.note_request_tokens(10 ** 9) is False)
    check("hermes 坏值不抛", h.note_request_tokens("abc") is False)

    # make_client 接线（chdir 到空目录：避免读到项目 .env）
    tmp = Path(tempfile.mkdtemp(prefix="token_cap_"))
    origin = os.getcwd()
    try:
        os.chdir(tmp)
        hc = make_client({"engine": "hermes",
                          "hermes": {"timeout": 5, "max_turns": 2},
                          "budget": {"token_limit": {"enabled": True,
                                                     "per_request_max_tokens": 8000}}},
                         "writer", verbose=False)
        check("make_client(hermes) 接线上限", hc.request_max_tokens == 8000,
              getattr(hc, "request_max_tokens", None))
        dc = make_client({"engine": "direct",
                          "providers": {"p": {"type": "openai-compat",
                                              "base_url": "http://x", "api_key_env": "NOPE_KEY"}},
                          "model": {"writer": {"provider": "p", "id": "some-model"}},
                          "budget": {"token_limit": {"enabled": True,
                                                     "per_request_max_tokens": 8000}}},
                         "writer", verbose=False)
        check("make_client(direct) 接线上限", dc.request_max_tokens == 8000,
              getattr(dc, "request_max_tokens", None))
        dc0 = make_client({"engine": "direct",
                           "providers": {"p": {"type": "openai-compat",
                                               "base_url": "http://x", "api_key_env": "NOPE_KEY"}},
                           "model": {"writer": {"provider": "p", "id": "some-model"}},
                           "budget": {"token_limit": {"enabled": False,
                                                      "per_request_max_tokens": 8000}}},
                          "writer", verbose=False)
        check("make_client 在 enabled=false 时不设上限（老行为）",
              dc0.request_max_tokens is None, getattr(dc0, "request_max_tokens", None))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("  token 级熔断自检（P0 止烧）")
    print("=" * 62)
    case_normalize()
    case_db_accounting()
    case_token_pause()
    case_no_false_stop_on_hermes_single_call()
    case_warn_ratio()
    case_legacy_behavior_unchanged()
    case_pause_detail_survives_stub_cost()
    case_orchestrator_integration()
    case_orchestrator_no_false_stop()
    case_request_cap_at_call_point()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
