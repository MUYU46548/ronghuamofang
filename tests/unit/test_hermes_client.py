# -*- coding: utf-8 -*-
"""HermesClient（stream-json 协议 / 工具面 / 超时结构化 / 流式停止）自检。

## 为什么需要这套用例（2026-10-01 排雷 P0/P1 配套）

- P0-1：旧实现把工具流水尾部当 stdout_tail（审稿 JSON 解析失败还静默跳过）、
  usage 恒 0 → 按任务文件大小虚构 token 账。
- P0-3：旧实现超时抛 TimeoutExpired 穿透 run_stage → run 崩 crashed 绕过止损链。
- P1-5：`-t` 必须传 **toolset 名**（file/web/terminal…）。传工具名
  （read_file,write_file,search_files）会得到空集合：模型照旧吐 tool_call 但
  永不派发，result.text 是原始 <longcat_tool_call> 且 exit_code=0 假成功
  （2026-10-01 真机实测踩坑）。本用例对这个坑做回归锁死。
- P0-4：run_task_stream 必须真流式 + stop_flag 真能杀子进程（旧版是整块回调的假流式）。

全程离线：用 .bat 桩模拟 hermes 子进程的 stream-json 输出，不发任何真实请求、
不花 token、不写项目数据（临时目录）。
用法：python tests/unit/test_hermes_client.py
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.llm_client import HermesClient, make_client       # noqa: E402
from utils.cost_tracker import estimate_cost_yuan            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  → {extra}")


def _bat(tmp, name, lines):
    """写一个 CRLF 结尾的 .bat 桩（Windows cmd 需要 CRLF）。"""
    p = Path(tmp) / name
    p.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    return p


def _stub_ok(tmp):
    """立即返回 init/text/text/result 的健康桩。"""
    return _bat(tmp, "stub_ok.bat", [
        "@echo off",
        'echo {"type": "system", "subtype": "init", "model": "stub-model"}',
        'echo {"type": "text", "text": "STUB_PIECE_1"}',
        'echo {"type": "text", "text": " STUB_PIECE_2"}',
        'echo {"type": "result", "exit_code": 0, "text": "STUB_FINAL_OK", '
        '"tokens": {"input": 111, "output": 22, "cache_read": 7}}',
    ])


def _stub_slow(tmp):
    """先吐一个 text 事件，再起一个带唯一标记的 20 秒子进程（python 孤儿探测）。"""
    marker = "nfslow_" + Path(tmp).name
    return _bat(tmp, "stub_slow.bat", [
        "@echo off",
        'echo {"type": "text", "text": "SLOW_PIECE"}',
        '"' + sys.executable + '" -c "import time; print(\'%s\', flush=True); time.sleep(20)"' % marker,
        'echo {"type": "result", "exit_code": 0, "text": "SLOW_DONE", '
        '"tokens": {"input": 1, "output": 1}}',
    ]), marker


def _orphan_count(marker):
    """数还活着的标记子进程（孤儿探测：tree kill 失效时它会存活到 20s）。"""
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter "
             "\"CommandLine LIKE '%%%s%%'\").Count" % marker],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
        return int((ps.stdout or "0").strip() or 0)
    except Exception:                                       # noqa: BLE001
        return -1   # 探测失败不冒充 0


# ------------------------------------------------------------------ 命令构造

def test_build_cmd():
    print("· _build_cmd：协议 / 工具面 / 模型开关")
    c = HermesClient()
    cmd = c._build_cmd(Path("task.md"))
    check("含 --format stream-json（P0-1 协议）", "--format" in cmd and
          "stream-json" in cmd[cmd.index("--format") + 1])
    i = cmd.index("-t") if "-t" in cmd else -1
    check("-t 用 toolset 名 file（P1-5）", i > 0 and cmd[i + 1] == "file",
          extra=str(cmd))
    check("回归锁死：绝不传工具名集合（空集合假成功坑）",
          "read_file,write_file,search_files" not in cmd)
    check("默认不传 -m（agent 内部模型，2026-10-01 定调）", "-m" not in cmd)
    check("含 --max-turns（子会话轮次上限）", "--max-turns" in cmd)

    cmd2 = c._build_cmd(Path("task.md"), model="explicit-model")
    check("显式传 model 时才带 -m", "-m" in cmd2 and
          cmd2[cmd2.index("-m") + 1] == "explicit-model")

    cmd3 = HermesClient(toolsets="")._build_cmd(Path("task.md"))
    check("toolsets 空串 = 逃生门（不加 -t，全量工具）", "-t" not in cmd3)


# -------------------------------------------------------------- 协议解析

def test_parse_and_finish():
    print("· _parse_stream_json / _finish：干净文本 + 真实 usage + 成本语义")
    raw = (
        'garbage line not json\n'
        '{"type": "system", "subtype": "init", "model": "mimo-x"}\n'
        '{"type": "text", "text": "正文前半 "}\n'
        '{"type": "text", "text": "正文后半"}\n'
        '{"type": "result", "exit_code": 0, "text": "FINAL_TEXT", '
        '"tokens": {"input": 900, "output": 40, "cache_read": 500}}\n'
        'session_id: 20261001_xxx\n'
    )
    c = HermesClient()
    res = c._finish(Path("task.md"), 0, raw)
    check("tail = result.text 干净最终消息（P0-1 审稿门依赖）",
          res["stdout_tail"] == "FINAL_TEXT", extra=repr(res["stdout_tail"][:60]))
    check("usage 来自 result.tokens（非估算）", res["tokens"] == 900 and
          res["tokens_out"] == 40 and res.get("cache_read") == 500)
    check("estimated=False", res["estimated"] is False)
    check("cost 恒 0（订阅执行）", res["cost_yuan"] == 0.0)
    check("provider=hermes", res["provider"] == "hermes")
    check("model 取自 init 事件", res["model"] == "mimo-x")
    check("exit 0", res["exit_code"] == 0 and res["stopped"] is False)

    # 只有垃圾行 → 回退估算，不抛
    tf = Path(tempfile.mkdtemp(prefix="nf_hc_")) / "t.md"
    tf.write_text("任务正文" * 500, encoding="utf-8")
    res2 = c._finish(Path(tf), 0, "no json here at all\n")
    check("无 result 事件 → estimated=True 且 token>0（兜底仍记账）",
          res2["estimated"] is True and res2["tokens"] > 0)
    check("兜底时 cost 仍恒 0（hermes 不虚构 ¥）", res2["cost_yuan"] == 0.0)
    check("tail 回退为原始尾部", res2["stdout_tail"].endswith("\n") or
          "no json" in res2["stdout_tail"])

    # 进程非零
    res3 = c._finish(Path(tf), 3, "", stderr="boom-detail")
    check("进程非零 → exit_code 保留 + stderr_tail（P0-3 结构化）",
          res3["exit_code"] == 3 and "boom-detail" in res3.get("stderr_tail", ""))

    # result.exit_code 非零但进程 0（任务级失败）
    res4 = c._finish(Path(tf), 0, '{"type": "result", "exit_code": 1, "text": "任务未完成"}')
    check("result.exit_code=1 → 判失败（保留失败文本）",
          res4["exit_code"] == 1 and res4["stdout_tail"] == "任务未完成")

    # 用户停止
    res5 = c._finish(Path(tf), 0, "", stopped=True)
    check("stopped → exit_code=0（主动停 ≠ 失败，与 direct 同语义）",
          res5["exit_code"] == 0 and res5["stopped"] is True)

    shutil.rmtree(Path(tf).parent, ignore_errors=True)


# --------------------------------------------------- run_task 真子进程路径

def test_run_task_with_stubs():
    print("· run_task：.bat 桩端到端 / 超时结构化 / 启动失败结构化")
    tmp = tempfile.mkdtemp(prefix="nf_hc_")
    try:
        task = Path(tmp) / "task.md"
        task.write_text("任务", encoding="utf-8")

        ok_bat = _stub_ok(tmp)
        c = HermesClient(hermes_bin=str(ok_bat))
        res = c.run_task(task, workdir=tmp)
        check("健康桩：exit 0 + tail=STUB_FINAL_OK + tokens 111/22",
              res["exit_code"] == 0 and res["stdout_tail"] == "STUB_FINAL_OK"
              and res["tokens"] == 111 and res["tokens_out"] == 22,
              extra=str(res))
        check("健康桩：estimated=False / cost=0 / model=stub-model",
              res["estimated"] is False and res["cost_yuan"] == 0.0
              and res["model"] == "stub-model")

        # 超时（P0-3）：慢桩 + timeout=1 → 必须结构化 124，绝不抛异常
        slow_bat, marker = _stub_slow(tmp)
        c2 = HermesClient(hermes_bin=str(slow_bat), timeout=1)
        t0 = time.time()
        res2 = c2.run_task(task, workdir=tmp)
        el = time.time() - t0
        check("超时 → exit_code=124 + error 字段，不抛异常（P0-3）",
              res2["exit_code"] == 124 and "超时" in res2.get("error", ""),
              extra=str(res2))
        check("超时在 10s 内返回（未挂死）", el < 10, extra="%.1fs" % el)
        # 孤儿探测：tree-kill 失效时孙进程会活满 20s（= 旧实现拖 19.3s 的根因）
        time.sleep(1.0)
        oc = _orphan_count(marker)
        check("超时后无孤儿子进程（taskkill /T 树杀生效）", oc == 0,
              extra="alive=%s" % oc)

        # 启动失败（hermes 不在）→ 127 结构化
        c3 = HermesClient(hermes_bin="no_such_hermes_bin_xyz")
        res3 = c3.run_task(task, workdir=tmp)
        check("启动失败 → 127 + error（OSError 结构化）",
              res3["exit_code"] == 127 and res3.get("error"),
              extra=str(res3))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- run_task_stream 流式/停止

def test_stream_and_stop():
    print("· run_task_stream：真流式回放 / stop_flag 杀子进程（P0-4）")
    tmp = tempfile.mkdtemp(prefix="nf_hc_")
    try:
        task = Path(tmp) / "task.md"
        task.write_text("任务", encoding="utf-8")

        # 流式回放：桩的两个 text 事件按序回放，result 收尾
        pieces = []
        c = HermesClient(hermes_bin=str(_stub_ok(tmp)))
        res = c.run_task_stream(task, lambda p: pieces.append(p), None, workdir=tmp)
        check("text 事件逐段回放（顺序拼接）",
              "".join(pieces) == "STUB_PIECE_1 STUB_PIECE_2",
              extra=str(pieces))
        check("流式收尾 result 正确", res["exit_code"] == 0 and
              res["stdout_tail"] == "STUB_FINAL_OK", extra=str(res))

        # 停止：慢桩约 20s，第 3 次探测（约 1.5s）触发停止 → 必须提前杀整棵树
        calls = {"n": 0}

        def stop_flag():
            calls["n"] += 1
            return calls["n"] >= 3

        got = []
        slow_bat2, marker2 = _stub_slow(tmp)
        c2 = HermesClient(hermes_bin=str(slow_bat2))
        t0 = time.time()
        res2 = c2.run_task_stream(task, lambda p: got.append(p), stop_flag, workdir=tmp)
        el = time.time() - t0
        check("stop → stopped=True 且 exit_code=0",
              res2["stopped"] is True and res2["exit_code"] == 0, extra=str(res2))
        check("停止在 8s 内生效（桩要跑 20s，证明真杀了子进程）",
              el < 8, extra="%.1fs" % el)
        check("停止前已收到流式片段（读线程活着）", got == ["SLOW_PIECE"],
              extra=str(got))
        time.sleep(1.0)
        oc2 = _orphan_count(marker2)
        check("停止后无孤儿子进程（否则「停止」形同虚设）", oc2 == 0,
              extra="alive=%s" % oc2)

        # 流式超时：timeout=1 + 慢桩 → 124 结构化
        slow_bat3, marker3 = _stub_slow(tmp)
        c3 = HermesClient(hermes_bin=str(slow_bat3), timeout=1)
        t0 = time.time()
        res3 = c3.run_task_stream(task, lambda p: None, None, workdir=tmp)
        el = time.time() - t0
        check("流式超时 → 124 + error", res3["exit_code"] == 124 and
              "超时" in res3.get("error", ""), extra=str(res3))
        check("流式超时在 10s 内返回", el < 10, extra="%.1fs" % el)
        time.sleep(1.0)
        oc3 = _orphan_count(marker3)
        check("流式超时后无孤儿子进程", oc3 == 0, extra="alive=%s" % oc3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------- 成本与装配

def test_cost_and_make_client():
    print("· estimate_cost_yuan / make_client：hermes 成本语义与装配")
    check("estimate(provider=hermes) 恒 0（订阅执行）",
          estimate_cost_yuan(10000, 5000, model="kimi-k2.6",
                             provider="hermes", role="writer") == 0.0)
    check("direct 引擎不受影响（仍按刊例计价）",
          estimate_cost_yuan(10000, 5000, model="kimi-k2.6",
                             provider="tokenhub", role="writer") > 0.0)

    cfg = {"engine": "hermes",
           "model": {"writer": {"provider": "tokenhub", "id": "kimi-k2.6"}}}
    client = make_client(cfg, "writer")
    check("engine=hermes → HermesClient 且 model=None（agent 内部模型）",
          isinstance(client, HermesClient) and client.model is None,
          extra=str(type(client)))
    check("engine=hermes 装配默认 toolset=file", client.toolsets == "file")
    check("engine=hermes 装配默认超时 900", client.timeout == 900)


# ------------------------------------------------------------ 源码级回归锁

def test_source_guards():
    print("· 源码级回归锁（防改回去）")
    orch = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("审稿门 fail-closed 文案在位", "审稿门 fail-closed" in orch)
    check("调用点区分 3=等审阅 / 其他=失败",
          '"waiting_review" if _code == 3 else "failed"' in orch)
    s4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("stage4 章间停止检查在位", 'aborted = "stopped"' in s4 and
          "_stop_requested()" in s4)


def main():
    print("=" * 66)
    print("HermesClient 自检（stream-json / 工具面 / 超时 / 流式停止）")
    print("=" * 66)
    test_build_cmd()
    test_parse_and_finish()
    test_run_task_with_stubs()
    test_stream_and_stop()
    test_cost_and_make_client()
    test_source_guards()
    print("-" * 66)
    print(f"PASS {len(PASS)} / FAIL {len(FAIL)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  ✗", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
