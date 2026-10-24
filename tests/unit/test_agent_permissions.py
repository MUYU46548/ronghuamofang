# -*- coding: utf-8 -*-
"""Agent 模式权限清单端点自检（GET /agent/permissions）。零 LLM、零网络。

## 为什么需要

用户 2026-10-24 原话：「在客户端内根本分不清哪些会走 agent 哪些直接走内部
配置 API，很混乱。Agent 模式没做好界面」。

病根是**两份名单**：判据的唯一真源是 `utils/agent_guard.FORBIDDEN_IN_AGENT_MODE`，
而 GUI 上「哪些按钮被锁、哪些不受影响」是模板里**硬编码的一句话**（agent-bar）。
两处各自维护 → 必然漂移，漂移时界面就在对用户说假话，且没有任何信号暴露它。

本端点把权威名单交给前端渲染；本用例守四件事：

1. **清单与真源逐条一致**（不许漏、不许多）—— 这是「不再有两份名单」的机械保证；
2. **每条都有人话说明** —— 只甩端点路径给用户看等于没说（原抱怨的一半）；
3. **三类边界齐全**：forbidden（后端 403）/ internal（Agent 可驱动）/ run_locked
   （GUI 运行锁，是防双跑不是权限拦截，必须与前两者区分开）；
4. **不改判据**：本端点纯只读展示，`agent_mode_enabled` 只是回显当前开关。

用法：python tests/unit/test_agent_permissions.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import agent_guard                                  # noqa: E402
from nf_api_domains import project as dom_project              # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def _call(monkey_agent_mode=None):
    """直接调域函数（不走 HTTP）：端点零参数、零副作用，直调等价。"""
    if monkey_agent_mode is not None:
        import nf_api
        orig = nf_api.load_all
        nf_api.load_all = lambda: ({"gates": {"agent_mode": monkey_agent_mode}}, {})
        try:
            return dom_project.handle_agent_permissions(None)
        finally:
            nf_api.load_all = orig
    return dom_project.handle_agent_permissions(None)


def case_shape():
    print("\n【1】清单与真源逐条一致")
    code, payload = _call()
    check("HTTP 200", code == 200, code)
    check("ok=true", payload.get("ok") is True)
    got = [f["path"] for f in payload.get("forbidden", [])]
    truth = sorted(agent_guard.FORBIDDEN_IN_AGENT_MODE)
    check("forbidden 条数与真源一致", len(got) == len(truth),
          "端点 %d vs 真源 %d" % (len(got), len(truth)))
    check("forbidden 集合与真源完全相等（不漏不多）",
          set(got) == set(truth),
          "缺=%s 多=%s" % (sorted(set(truth) - set(got)),
                            sorted(set(got) - set(truth))))
    check("forbidden 已排序（渲染顺序稳定，避免界面每次刷新跳动）",
          got == sorted(got))
    # 冒烟：几个关键条目必须在（防止真源被误删时端点静默跟随）
    for p in ("/approve", "/reject", "/config/agent_mode", "/config/token_limit",
              "/project/create", "/project/archive", "/stage/skip"):
        check("含关键条目 %s" % p, p in set(got))


def case_why_present():
    print("\n【2】每条都有人话说明")
    code, payload = _call()
    empty = [f["path"] for f in payload["forbidden"]
             if not str(f.get("why") or "").strip()]
    check("无 why 为空的条目（只甩路径等于没说）", not empty, empty)
    # why 必须解释「为什么归用户」，而不是复读端点名
    sample = next((f for f in payload["forbidden"] if f["path"] == "/approve"), None)
    check("/approve 的 why 提到审批语义",
          sample and "审批" in sample["why"], sample)


def case_three_buckets():
    print("\n【3】三类边界齐全且互不混淆")
    code, payload = _call()
    check("有 forbidden 桶", bool(payload.get("forbidden")))
    check("有 internal 桶（Agent 可驱动的）", bool(payload.get("internal")))
    check("有 run_locked 桶（GUI 运行锁，非权限拦截）",
          bool(payload.get("run_locked")))
    # 三桶不得串味：审批类绝不能出现在 internal 里
    internal_paths = {i["path"] for i in payload["internal"]}
    forbidden_paths = {f["path"] for f in payload["forbidden"]}
    overlap = internal_paths & forbidden_paths
    check("forbidden 与 internal 无交集（同一条不能既禁又放）",
          not overlap, sorted(overlap))
    check("/approve 只在 forbidden，不在 internal",
          "/approve" in forbidden_paths and "/approve" not in internal_paths)
    # run_locked 的第二条必须讲明「审批不受影响」—— 这正是用户分不清的那件事
    rl_texts = " ".join(r.get("what", "") + r.get("why", "") for r in payload["run_locked"])
    check("run_locked 说明了审批/打回不受影响",
          "审批" in rl_texts and ("不受影响" in rl_texts or "只归桌面端" in rl_texts),
          rl_texts[:200])


def case_readonly_and_note():
    print("\n【4】只读展示：不改判据、回显开关")
    code, off_payload = _call(monkey_agent_mode=False)
    code2, on_payload = _call(monkey_agent_mode=True)
    check("agent_mode=false → enabled=false",
          off_payload.get("agent_mode_enabled") is False)
    check("agent_mode=true → enabled=true",
          on_payload.get("agent_mode_enabled") is True)
    check("两种开关下 forbidden 名单**完全相同**（开关只影响 403 生效与否，"
          "不影响清单本身）",
          [f["path"] for f in off_payload["forbidden"]]
          == [f["path"] for f in on_payload["forbidden"]])
    check("note 里点名真源（让人知道去哪儿核对）",
          "agent_guard" in str(on_payload.get("note") or ""))


def case_no_mutation():
    print("\n【5】零副作用：调用前后名单不变")
    before = set(agent_guard.FORBIDDEN_IN_AGENT_MODE)
    _call()
    _call(monkey_agent_mode=True)
    check("调用后真源集合原样", set(agent_guard.FORBIDDEN_IN_AGENT_MODE) == before)
    check("调用后 is_forbidden 判据原样",
          agent_guard.is_forbidden_in_agent_mode("/approve") is True)


def main():
    case_shape()
    case_why_present()
    case_three_buckets()
    case_readonly_and_note()
    case_no_mutation()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
