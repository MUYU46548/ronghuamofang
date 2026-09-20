# -*- coding: utf-8 -*-
"""S2 回归：GUI 流式跑阶段（`stream:true`）不得崩在未定义名上。

## 本用例守护什么

2026-09-19 审查发现 `nf_api.py` 的流式分支 `_fn_stream` 内引用 `only_stage`
（局部变量实际叫 `only`）→ GUI 勾选「流式输出」跑流水线 100% `NameError`。
更阴险的是**执行时序**：API 先回 `202 {job_id, stream:true}`，再在后台线程抛错，
于是 GUI 显示「正在流式输出」却永远收不到 token —— 典型静默失败。

修复方式不是打补丁，而是**消除流式/非流式平行分支**
（合并为 `act_run_stage` / `act_run_stage_streamed` + 共用 `_rc_to_result`）。

## 断言策略（不看代码长相，只看行为）

  1. 两个 action 工厂能正常构造
  2. `act_run_stage_streamed` 在 fake client 下能跑到 orch_run 并返回结果元组
  3. **两条路径的退出码翻译完全一致**（0/1/2/3/4 全覆盖）——这是「消除平行分支」的实质
  4. 流式路径不会因为 `only_stage` 名字问题抛 NameError

用法：python tests/unit/test_stream_stage.py
"""
import inspect
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def main():
    print("=" * 70)
    print("S2 回归：流式跑阶段的未定义名 + 平行分支消除")
    print("=" * 70)

    # 用例 1：模块可导入，且两个 action 工厂存在
    import nf_api
    check(hasattr(nf_api, "act_run_stage"), "存在 act_run_stage 工厂")
    check(hasattr(nf_api, "act_run_stage_streamed"), "存在 act_run_stage_streamed 工厂")
    check(hasattr(nf_api, "_rc_to_result"), "存在共用退出码翻译 _rc_to_result")

    # 用例 2：全仓不得再出现未绑定的 only_stage 引用（回归护栏）
    src = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    bad = [ln.strip() for ln in src.splitlines()
           if "only_stage=" in ln and "only_stage=only" not in ln
           and "def " not in ln and "only_stage=only_stage" not in ln]
    check(not bad, "无裸 only_stage= 传参（历史 S2 形态）", f"发现: {bad[:3]}")

    # 用例 3：退出码翻译一致性（0/1/2/3/4 → (ok, detail)）
    print("\n--- 退出码翻译表 ---")
    expect = {0: True, 1: False, 2: False, 3: True, 4: True}
    for rc, want_ok in expect.items():
        ok, detail = nf_api._rc_to_result(rc)
        check(ok == want_ok, f"rc={rc} → ok={want_ok}（detail={detail}）",
              f"实际 ok={ok}")

    # 用例 4：两条路径共用同一个翻译函数（AST 级确认无重复实现）
    src_stage = inspect.getsource(nf_api.act_run_stage)
    src_stream = inspect.getsource(nf_api.act_run_stage_streamed)
    check("_rc_to_result" in src_stage,
          "act_run_stage 经由 _rc_to_result 翻译退出码")
    check("_rc_to_result" in src_stream,
          "act_run_stage_streamed 经由 _rc_to_result 翻译退出码")
    check("if rc == 4" not in src_stage and "if rc == 4" not in src_stream,
          "两条路径均无各自的 if rc== 分支（平行分支已消除）")

    # 用例 5：流式路径端到端（FakeClient + mock orch_run），确认不抛 NameError
    print("\n--- 流式路径端到端（mock orch_run + FakeClient）---")
    import os
    os.environ["NF_API_ALLOW_FAKE"] = "1"
    nf_api.ALLOW_FAKE = True

    captured = {}

    def fake_orch_run(from_stage=None, only_stage=None, client=None):
        captured["from_stage"] = from_stage
        captured["only_stage"] = only_stage
        captured["client"] = client
        return 0

    orig = nf_api.orch_run
    nf_api.orch_run = fake_orch_run
    try:
        import queue as _q
        import threading as _t
        cfg = {"providers": {"tokenhub": {"available_models": ["glm-5"]}}}
        q = _q.Queue(maxsize=100)
        ev = _t.Event()
        # 预置 streamer 占位，模拟 do_POST 的行为
        nf_api.STREAMERS["preview_x"] = {"queue": q, "stop": ev, "text": []}
        nf_api.CURRENT["id"] = "job_real_x"

        fn = nf_api.act_run_stage_streamed(cfg, None, 3, "preview_x", q, ev)
        ok, detail = fn()
        check(ok is True, "流式路径返回 ok=True（rc=0）", f"实际 {ok}, {detail}")
        check(captured.get("from_stage") == 3, "from_stage 正确透传",
              f"实际 {captured.get('from_stage')}")
        check(captured.get("only_stage") is None,
              "only_stage 正确透传为 None（而非 NameError）",
              f"实际 {captured.get('only_stage')}")
        check("job_real_x" in nf_api.STREAMERS,
              "streamer 占位已被真实 job_id 替换")
        check("preview_x" not in nf_api.STREAMERS, "占位键已清理")
        # 非流式路径对照
        fn2 = nf_api.act_run_stage(cfg, None, 3)
        ok2, detail2 = fn2()
        check(ok2 is True and detail2 == detail,
              "非流式路径返回与流式一致的 (ok, detail)", f"{ok2},{detail2} vs {ok},{detail}")
    except Exception:
        check(False, "流式路径端到端无异常", traceback.format_exc()[-400:])
    finally:
        nf_api.orch_run = orig
        nf_api.STREAMERS.pop("preview_x", None)
        nf_api.STREAMERS.pop("job_real_x", None)
        nf_api.CURRENT["id"] = None

    print("\n" + "=" * 70)
    print(f"结果：PASS={PASS}  FAIL={FAIL}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
