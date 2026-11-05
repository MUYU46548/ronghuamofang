# -*- coding: utf-8 -*-
"""阶段 3 的审批门口径（试跑第 20 条，2026-11-05）。

## 要治的病

界面按 “done 且未 approved” 判待审批，于是阶段 3（逐章大纲）跑完也长出
“待审批 / 确认”按钮；而 orchestrator 的审批门只有 `gates.require_approval`
（出厂 [2, 6]，陪跑模式再加 1）。用户按了放行，流水线毫无变化，外部 Agent
也说“阶段 3 没有审批门，原因不明”。

## 修法与单一来源

- 判据只有一份：`utils.approval_policy.approval_stages(gates)`
  （orchestrator / nfctl / nf_api 全走它）。
- `GET /state` 把它透出为 `approval_stages`，前端**只认这个字段**。
- kickoff 提示词把“审批门在哪、阶段 3 无门”写死成文，并指回 /state。

## 判据（一条用例 + 一条反证）

- C1 `approval_stages` 由 require_approval 决定；陪跑（material_autonomy=false）并入 1
- C2 orchestrator 不再自己写一份集合（只调 _approval_stages）
- C3 `/state` 透出 approval_stages
- C4 前端按 isGate 过滤（待审批 / 确认 / 收件箱 / 下一步都不得漏）
- C5 kickoff 写明 approval_stages 与“阶段 3 无审批门”
- C6 播种会刷新 kickoff（否则旧工作区永远拿到旧说明）
反证：上面任一处仍按“done 且未 approved”判 = 未完成。

用法：python tests/unit/test_approval_gate_alignment.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def main():
    print("=" * 68)
    print("阶段 3 的审批门口径（试跑第 20 条）")
    print("=" * 68)

    from utils.approval_policy import approval_stages  # noqa: E402

    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    orch = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    kickoff = (ROOT / "prompts" / "agent_kickoff.md").read_text(encoding="utf-8")
    main_js = (ROOT / "console" / "main" / "index.js").read_text(encoding="utf-8")

    print("\n[C1] 判据本身（唯一来源）")
    check("出厂 [2, 6]", approval_stages({"require_approval": [2, 6]}) == [2, 6])
    check("陪跑（material_autonomy=false）并入阶段 1",
          approval_stages({"require_approval": [2, 6], "material_autonomy": False}) == [2, 6, 1])
    check("自主（true）不额外停",
          approval_stages({"require_approval": [2, 6], "material_autonomy": True}) == [2, 6])
    check("键缺失 = 保持旧行为", approval_stages({}) == [2])

    print("\n[C2] orchestrator 不自写集合")
    check("orchestrator 经 utils.approval_policy",
          "from utils.approval_policy import approval_stages" in orch)
    check("orchestrator 里没有第二份阶段字面量",
          "require_approval" not in orch)

    print("\n[C3] /state 透出 approval_stages")
    check("build_state 计算 _approval", "approval_stages as _approval_stages" in api_src)
    check("payload 带 approval_stages", '"approval_stages": _approval' in api_src)

    print("\n[C4] 前端只认 isGate")
    check("从 /state 读 approval_stages",
          "state.value && state.value.approval_stages" in app)
    check("有 isGate 判定", "function isGate(stage)" in app)
    check("收件箱按门过滤",
          's.status === "done" && !s.approved && isGate(s.stage)' in app)
    check("阶段行“待审批”徽标按门过滤",
          "s.status === 'done' && !s.approved && isGate(s.stage)" in app)
    check("阶段行“确认”按钮按门过滤",
          "s.status === 'done' && !s.approved && isGate(s.stage)\" class=\"mini primary\"" in app)
    check("无门阶段显式标注“无需审批”", "st-nogate" in app)
    check("退出守卫不再把无门阶段当未完成",
          "(s.status === \"done\" && !s.approved && isGate(s.stage))" in app)

    print("\n[C5] kickoff 说清楚")
    check("指认 /state.approval_stages 为准", "approval_stages" in kickoff)
    check("写明“阶段 3（逐章大纲）没有审批门”", "阶段 3（逐章大纲）没有审批门" in kickoff)

    print("\n[C6] 旧工作区也能拿到新 kickoff")
    check("播种把 kickoff 列为随版本刷新",
          'SEED_FORCE_REFRESH_FILES = ["prompts/agent_kickoff.md"]' in main_js)

    print("\n" + "=" * 68)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
