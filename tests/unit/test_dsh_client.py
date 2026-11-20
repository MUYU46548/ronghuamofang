# -*- coding: utf-8 -*-
"""DshClient 自检（headless 协议 / 失败可见 / 超时 / 流式停止）。

## 为什么需要这套用例（2026-11-17，转向清单第 1 项）

换内核的第一条判据是“**dsh 不存在时明确失败，不静默、不假 exit 0**”。
这类静默失真在本项目有前科：hermes 引擎曾把工具流水当答案（假成功）、
把 `-t` 传成工具名集合（空集合、假成功）。DshClient 的每条收尾分支都要有
一条会变红的用例，否则“换引擎”就等于把整条流水线押在没人验过的协议上。

## 覆盖

1. 命令构造（headless / --json / stdin `-` / 审批策略 / 无 --model / 续接开关）
2. 事件解析（session / text / step_end usage / final / error）
3. 收尾映射（completed=0；**max-tokens → exit 2 + finish=length**；无 final 但 exit 0 = fail-closed；
   进程非零保留；主动停=0；无 usage → estimated）
4. 真子进程（.bat 桩）：正常 / 超时 124 / 找不到 dsh → 127 + 可行动报错 / 无孤儿
5. 流式：逐段回放 / stop_flag 杀整树 / 超时 124
6. 单轮直答：4 元组 / 空回复报错 / 非零报错
7. 续接失败自动降级为全新会话（带 resumed_fallback）
8. 装配：resolve_dsh_bin / dsh_available 的可行动文案

全程离线：.bat 桩模拟 DSH 的 NDJSON 输出，不发任何请求、不花额度、不写项目数据。
用法：python tests/unit/test_dsh_client.py
"""
import io
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.dsh_client import (DshClient, build_model_patch, dsh_available,  # noqa: E402
                              resolve_dsh_bin)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  → {extra}")


def _argval(cmd, flag):
    """取 `--flag value` 里紧跟的值；flag 不存在返回 None。

    不写成 `cmd[cmd.index(flag)+1]`：一旦 flag 缺失就抛 ValueError，
    整份自检会崩在断言之前（反证时实测踩到，等于把红变成看不出红）。
    """
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def _bat(tmp, name, lines):
    """写一个 CRLF 结尾的 .bat 桩（Windows cmd 需要 CRLF）。"""
    p = Path(tmp) / name
    p.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    return p


def _stub_ok(tmp):
    """健康桩：session + step_end(usage) + 两个 text + final(completed)。"""
    return _bat(tmp, "dsh_ok.bat", [
        "@echo off",
        'echo {"type":"session","sessionId":"sess-stub-1","cwd":"C:/tmp"}',
        'echo {"type":"status","phase":"turn_start","turn":1}',
        'echo {"type":"status","phase":"step_end","turn":1,"step":1,'
        '"usage":{"inputTokens":111,"outputTokens":22,"cacheReadTokens":7}}',
        'echo {"type":"text","text":"STUB_PIECE_1"}',
        'echo {"type":"text","text":" STUB_PIECE_2"}',
        'echo {"type":"status","phase":"turn_end","turn":1,'
        '"reason":{"kind":"completed"}}',
        'echo {"type":"final","sessionId":"sess-stub-1",'
        '"reason":{"kind":"completed"},"text":"STUB_FINAL_OK"}',
    ])


def _stub_max_tokens(tmp):
    """撞输出上限：DSH 用 reason.kind=max-tokens 表达（正文是半截）。"""
    return _bat(tmp, "dsh_trunc.bat", [
        "@echo off",
        'echo {"type":"session","sessionId":"sess-trunc"}',
        'echo {"type":"text","text":"HALF_BODY"}',
        'echo {"type":"final","sessionId":"sess-trunc",'
        '"reason":{"kind":"max-tokens"},"text":"HALF_BODY"}',
    ])


def _stub_no_final(tmp):
    """退出 0 但没有 final（事件流被截 / 版本不匹配）→ 必须 fail-closed。"""
    return _bat(tmp, "dsh_nofinal.bat", [
        "@echo off",
        'echo {"type":"session","sessionId":"sess-x"}',
        'echo {"type":"text","text":"THOUGHT_ONLY"}',
    ])


def _stub_fail(tmp):
    """轮次外失败：error 事件 + 退出 1（没有 final）。"""
    return _bat(tmp, "dsh_fail.bat", [
        "@echo off",
        'echo {"type":"session","sessionId":"sess-bad"}',
        'echo {"type":"error","message":"provider exploded"}',
        "exit /b 1",
    ])


def _stub_empty_answer(tmp):
    """completed 但答案为空（agent 把答案写文件里没回话）。"""
    return _bat(tmp, "dsh_empty.bat", [
        "@echo off",
        'echo {"type":"final","sessionId":"sess-e","reason":{"kind":"completed"},"text":""}',
    ])


def _stub_slow(tmp):
    """先吐一个 text，再起一个带唯一标记的 20 秒子进程（孤儿探测）。"""
    marker = "dshslow_" + Path(tmp).name
    return _bat(tmp, "dsh_slow.bat", [
        "@echo off",
        'echo {"type":"text","text":"SLOW_PIECE"}',
        '"' + sys.executable + '" -c "import time; print(\'%s\', flush=True); time.sleep(20)"' % marker,
        'echo {"type":"final","sessionId":"sess-slow","reason":{"kind":"completed"},'
        '"text":"SLOW_DONE"}',
    ]), marker


def _stub_session_guard(tmp):
    """带 --session-id 就失败；不带就成功 —— 用来验“续接失败自动降级”。"""
    return _bat(tmp, "dsh_resume.bat", [
        "@echo off",
        'echo %* | findstr /C:"--session-id" >nul && exit /b 1',
        'echo {"type":"session","sessionId":"fresh-1"}',
        'echo {"type":"final","sessionId":"fresh-1","reason":{"kind":"completed"},'
        '"text":"FRESH_OK"}',
    ])


def _orphan_count(marker):
    """数还活着的标记子进程（tree kill 失效时它会存活到 20s）。"""
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


# ---------------------------------------------------------------- 命令构造

def test_build_cmd():
    print("· _build_cmd：headless 协议 / stdin 任务 / 审批策略 / 无 --model")
    c = DshClient(dsh_bin="dsh")
    cmd = c._build_cmd()
    check("含 --profile headless", "headless" in cmd and
          cmd[cmd.index("--profile") + 1] == "headless")
    check("含 --json（要事件流才能判成败/记账）", "--json" in cmd)
    check("默认 --approval-policy never（无人值守没有实时审批通道）",
          "--approval-policy" in cmd and
          cmd[cmd.index("--approval-policy") + 1] == "never")
    check("任务标记 `-` 在最后一个参数（任务走 stdin）", cmd[-1] == "-")
    check("默认不传 --permission-mode（用 profile 的 workspace-write 默认）",
          "--permission-mode" not in cmd)
    check("绝不传 --model（headless 没有这个旋钮）", "--model" not in cmd)
    check("续接开关关着时不传 --session-id",
          "--session-id" not in c._build_cmd(session_id="sess-1"))

    # 额外可写目录用临时目录当取值：夹具里不写任何真实磁盘路径
    # （发布树卫生：专名/路径只能取自虚构侧，leak_scan 会抓本机路径）。
    _extra = tempfile.mkdtemp(prefix="nf_adddir_")
    c2 = DshClient(dsh_bin="dsh", permission_mode="workspace-write",
                   add_dirs=[_extra], session_continuation=True)
    cmd2 = c2._build_cmd(session_id="sess-1")
    check("显式配置的 permission-mode / add-dir 会拼上",
          "--permission-mode" in cmd2 and "--add-dir" in cmd2 and _extra in cmd2)
    check("续接开启 + 传了 session_id 才拼 --session-id",
          "--session-id" in cmd2 and cmd2[cmd2.index("--session-id") + 1] == "sess-1")
    check("续接开启也不影响 stdin 标记在最后", cmd2[-1] == "-")

    prompt = DshClient._task_prompt(Path("data/state/tasks/x.md"))
    check("任务提示用绝对路径（cwd 由调用方决定，相对路径会找不到）",
          str(Path("data/state/tasks/x.md").resolve()) in prompt)
    check("任务提示带执行边界（该读的照读，不该翻的别翻）",
          "【执行边界】" in prompt and "设定库" in prompt and "工作区之外" in prompt)


# ---------------------------------------------------------------- 事件解析

def test_parse_events():
    print("· parse_events：session / text / step_end usage / final / error")
    raw = (
        "garbage line\n"
        '{"type":"session","sessionId":"S1","cwd":"E:/x"}\n'
        '{"type":"text","text":"A"}\n'
        '{"type":"thinking","text":"不考虑"}\n'
        '{"type":"status","phase":"step_end","turn":1,"step":1,'
        '"usage":{"inputTokens":100,"outputTokens":20,"cacheReadTokens":5}}\n'
        '{"type":"status","phase":"step_end","turn":1,"step":2,'
        '"usage":{"inputTokens":50,"outputTokens":10}}\n'
        '{"type":"final","sessionId":"S1","reason":{"kind":"completed"},"text":"答"}\n'
    )
    p = DshClient.parse_events(raw)
    check("sessionId 取出", p["session_id"] == "S1")
    check("text 事件按序收集（thinking 不混进正文）", p["texts"] == ["A"],
          extra=str(p["texts"]))
    check("usage 跨 step 累加", p["usage"] and p["usage"]["input"] == 150
          and p["usage"]["output"] == 30 and p["usage"]["cache_read"] == 5,
          extra=str(p["usage"]))
    check("final 取出", (p["final"] or {}).get("text") == "答")

    p2 = DshClient.parse_events('{"type":"error","message":"boom"}\n')
    check("error 事件单独记录（此时没有 final）",
          p2["final"] is None and p2["error"] == "boom")


# ---------------------------------------------------------------- 收尾映射

def test_finish_mapping():
    print("· _finish：completed=0 / max-tokens=截断 / 无 final 不许当成功")
    c = DshClient(dsh_bin="dsh")
    tmp = Path(tempfile.mkdtemp(prefix="nf_dsh_t_"))
    task = tmp / "t.md"
    task.write_text("任务正文" * 300, encoding="utf-8")
    try:
        ok = c._finish(task, 0,
                       '{"type":"session","sessionId":"S"}\n'
                       '{"type":"status","phase":"step_end","turn":1,"step":1,'
                       '"usage":{"inputTokens":900,"outputTokens":40,'
                       '"cacheReadTokens":500}}\n'
                       '{"type":"final","sessionId":"S",'
                       '"reason":{"kind":"completed"},"text":"FINAL_TEXT"}\n')
        check("completed → exit 0 + tail=final.text", ok["exit_code"] == 0
              and ok["stdout_tail"] == "FINAL_TEXT", extra=str(ok))
        check("usage 来自事件流（非估算）+ cache_read 带出",
              ok["tokens"] == 900 and ok["tokens_out"] == 40
              and ok["cache_read"] == 500 and ok["estimated"] is False)
        check("provider=dsh / cost 恒 0（订阅语义，token 照记）",
              ok["provider"] == "dsh" and ok["cost_yuan"] == 0.0)
        check("session_id 带出（供 --session-id 续接）", ok["session_id"] == "S")
        check("正常收尾 finish_reason 为空（不是 length）",
              ok["finish_reason"] == "", extra=repr(ok["finish_reason"]))

        trunc = c._finish(task, 0,
                          '{"type":"final","sessionId":"S",'
                          '"reason":{"kind":"max-tokens"},"text":"HALF"}')
        check("max-tokens → exit 2（截断不许当成品，与直连引擎同码）",
              trunc["exit_code"] == 2, extra=str(trunc["exit_code"]))
        check("max-tokens → finish_reason=length + 文案含“截断”",
              trunc["finish_reason"] == "length" and "截断" in trunc.get("error", ""))

        nofinal = c._finish(task, 0, '{"type":"text","text":"THOUGHT"}')
        check("退出 0 却没有 final → fail-closed（exit 1，不假成功）",
              nofinal["exit_code"] == 1 and "没有 final" in nofinal.get("error", ""),
              extra=str(nofinal))

        bad = c._finish(task, 3, "", stderr="boom-detail")
        check("进程非零 → 保留退出码 + stderr_tail",
              bad["exit_code"] == 3 and "boom-detail" in bad.get("stderr_tail", ""))

        err = c._finish(task, 0, '{"type":"error","message":"provider exploded"}')
        check("error 事件（无 final）→ exit 1 且错误原样透出",
              err["exit_code"] == 1 and "provider exploded" in err.get("error", ""))

        stopped = c._finish(task, 0, "", stopped=True)
        check("stopped → exit 0（主动停 ≠ 失败）",
              stopped["exit_code"] == 0 and stopped["stopped"] is True)

        est = c._finish(task, 0, '{"type":"final","sessionId":"S",'
                                 '"reason":{"kind":"completed"},"text":"X"}')
        check("事件流无 usage → estimated=True 且 token>0（不冒充真实用量）",
              est["estimated"] is True and est["tokens"] > 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------- run_task 真子进程路径

def test_run_task_with_stubs():
    print("· run_task：.bat 桩端到端 / 找不到 dsh / 超时结构化 / 无孤儿")
    tmp = tempfile.mkdtemp(prefix="nf_dsh_")
    try:
        task = Path(tmp) / "task.md"
        task.write_text("任务", encoding="utf-8")

        c = DshClient(dsh_bin=str(_stub_ok(tmp)))
        res = c.run_task(task, workdir=tmp)
        check("健康桩：exit 0 + tail=STUB_FINAL_OK + tokens 111/22",
              res["exit_code"] == 0 and res["stdout_tail"] == "STUB_FINAL_OK"
              and res["tokens"] == 111 and res["tokens_out"] == 22, extra=str(res))
        check("健康桩：estimated=False / cost=0", res["estimated"] is False
              and res["cost_yuan"] == 0.0)

        trunc = DshClient(dsh_bin=str(_stub_max_tokens(tmp))).run_task(task, workdir=tmp)
        check("截断桩：exit 2 + finish_reason=length",
              trunc["exit_code"] == 2 and trunc["finish_reason"] == "length",
              extra=str(trunc))

        nofinal = DshClient(dsh_bin=str(_stub_no_final(tmp))).run_task(task, workdir=tmp)
        check("无 final 桩：exit 1（假成功防线）", nofinal["exit_code"] == 1,
              extra=str(nofinal))

        # 找不到 dsh → 127 + 可行动报错（第 1 项的核心判据）
        missing = DshClient(dsh_bin="no_such_dsh_bin_xyz").run_task(task, workdir=tmp)
        check("dsh 不存在 → exit 127（不静默、不假 exit 0）",
              missing["exit_code"] == 127, extra=str(missing.get("exit_code")))
        check("dsh 不存在 → 报错里给出修法（装 DSH / dsh.bin / NF_DSH_BIN）",
              all(k in missing.get("error", "") for k in ("DeepSeek Harness", "dsh.bin")))

        # 超时：慢桩（20s）+ timeout=1 → 124，且不留孤儿
        slow_bat, marker = _stub_slow(tmp)
        t0 = time.time()
        res2 = DshClient(dsh_bin=str(slow_bat), timeout=1).run_task(task, workdir=tmp)
        el = time.time() - t0
        check("超时 → exit_code=124 + error 含“超时”（不抛异常）",
              res2["exit_code"] == 124 and "超时" in res2.get("error", ""),
              extra=str(res2))
        check("超时在 10s 内返回（未挂死）", el < 10, extra="%.1fs" % el)
        time.sleep(1.0)
        oc = _orphan_count(marker)
        check("超时后无孤儿子进程（taskkill /T 树杀生效）", oc == 0,
              extra="alive=%s" % oc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------- run_task_stream 流式/停止

def test_stream_and_stop():
    print("· run_task_stream：逐段回放 / stop_flag 杀整树 / 流式超时")
    tmp = tempfile.mkdtemp(prefix="nf_dsh_s_")
    try:
        task = Path(tmp) / "task.md"
        task.write_text("任务", encoding="utf-8")

        pieces = []
        res = DshClient(dsh_bin=str(_stub_ok(tmp))).run_task_stream(
            task, lambda p: pieces.append(p), None, workdir=tmp)
        check("text 事件逐段回放（顺序拼接）",
              "".join(pieces) == "STUB_PIECE_1 STUB_PIECE_2", extra=str(pieces))
        check("流式收尾与 run_task 同口径",
              res["exit_code"] == 0 and res["stdout_tail"] == "STUB_FINAL_OK",
              extra=str(res))

        calls = {"n": 0}

        def stop_flag():
            calls["n"] += 1
            return calls["n"] >= 3

        got = []
        slow_bat2, marker2 = _stub_slow(tmp)
        t0 = time.time()
        res2 = DshClient(dsh_bin=str(slow_bat2)).run_task_stream(
            task, lambda p: got.append(p), stop_flag, workdir=tmp)
        el = time.time() - t0
        check("stop → stopped=True 且 exit 0",
              res2["stopped"] is True and res2["exit_code"] == 0, extra=str(res2))
        check("停止在 8s 内生效（桩要跑 20s，证明真杀了进程树）", el < 8,
              extra="%.1fs" % el)
        check("停止前已收到流式片段（读线程活着）", got == ["SLOW_PIECE"], extra=str(got))
        time.sleep(1.0)
        oc2 = _orphan_count(marker2)
        check("停止后无孤儿子进程", oc2 == 0, extra="alive=%s" % oc2)

        slow_bat3, marker3 = _stub_slow(tmp)
        t0 = time.time()
        res3 = DshClient(dsh_bin=str(slow_bat3), timeout=1).run_task_stream(
            task, lambda p: None, None, workdir=tmp)
        el = time.time() - t0
        check("流式超时 → 124 + error", res3["exit_code"] == 124
              and "超时" in res3.get("error", ""), extra=str(res3))
        check("流式超时在 10s 内返回", el < 10, extra="%.1fs" % el)
        time.sleep(1.0)
        oc3 = _orphan_count(marker3)
        check("流式超时后无孤儿子进程", oc3 == 0, extra="alive=%s" % oc3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 单轮直答

def test_post_chat():
    print("· _post_chat / _post_chat_stream：4 元组契约与三种失败")
    tmp = tempfile.mkdtemp(prefix="nf_dsh_q_")
    try:
        ok = DshClient(dsh_bin=str(_stub_ok(tmp)))
        text, usage, model, finish = ok._post_chat([{"role": "user", "content": "hi"}])
        check("返回 4 元组文本", text == "STUB_FINAL_OK", extra=repr(text))
        check("usage 键名与直连引擎一致",
              usage.get("prompt_tokens") == 111 and usage.get("completion_tokens") == 22)
        check("model 如实标 dsh-default（headless 无 --model，不冒充模型名）",
              model == "dsh-default", extra=str(model))
        check("正常收尾 finish=stop", finish == "stop", extra=str(finish))

        trunc = DshClient(dsh_bin=str(_stub_max_tokens(tmp)))
        text2, _, _, finish2 = trunc._post_chat([{"role": "user", "content": "hi"}])
        check("截断时 finish=length（调用方据此不落盘）",
              finish2 == "length" and text2 == "HALF_BODY", extra=str(finish2))

        empty = DshClient(dsh_bin=str(_stub_empty_answer(tmp)))
        try:
            empty._post_chat([{"role": "user", "content": "hi"}])
            check("空回复必须报错（不许当成功）", False, extra="没有抛异常")
        except RuntimeError as e:
            check("空回复必须报错（不许当成功）", "没有返回文本" in str(e),
                  extra=str(e)[:80])

        bad = DshClient(dsh_bin=str(_stub_fail(tmp)))
        try:
            bad._post_chat([{"role": "user", "content": "hi"}])
            check("非零退出必须报错", False, extra="没有抛异常")
        except RuntimeError as e:
            check("非零退出必须报错", "单轮调用失败" in str(e), extra=str(e)[:80])

        pieces = []
        res = DshClient(dsh_bin=str(_stub_ok(tmp)))._post_chat_stream(
            [{"role": "user", "content": "hi"}], on_chunk=pieces.append)
        check("单轮流式：片段回放 + 最终文本",
              "".join(pieces) == "STUB_PIECE_1 STUB_PIECE_2" and res[0] == "STUB_FINAL_OK",
              extra=str(pieces))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 续接降级

def test_resume_fallback():
    print("· 续接失败 → 自动降级为全新会话（优化不该成为新的失败源）")
    tmp = tempfile.mkdtemp(prefix="nf_dsh_r_")
    try:
        task = Path(tmp) / "task.md"
        task.write_text("任务", encoding="utf-8")
        c = DshClient(dsh_bin=str(_stub_session_guard(tmp)), session_continuation=True)
        res = c.run_task(task, workdir=tmp, session_id="stale-session")
        check("首轮失败后降级重跑成功",
              res["exit_code"] == 0 and res.get("resumed_fallback") is True
              and res["stdout_tail"] == "FRESH_OK", extra=str(res))

        c2 = DshClient(dsh_bin=str(_stub_session_guard(tmp)), session_continuation=False)
        res2 = c2.run_task(task, workdir=tmp, session_id="stale-session")
        check("续接开关关着时根本不拼 --session-id（桩不会失败）",
              res2["exit_code"] == 0 and "resumed_fallback" not in res2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 定位与装配

def test_locate_and_wire():
    print("· resolve_dsh_bin / dsh_available：找不到时说人话")
    check("显式参数优先", resolve_dsh_bin("E:/x/dsh.cmd") == "E:/x/dsh.cmd")
    ok, why = dsh_available("no_such_dsh_bin_xyz")
    check("dsh_available 报不可用", ok is False, extra=str(ok))
    check("说明里给出两条修法（装 DSH / 写 dsh.bin）",
          "DeepSeek Harness" in why and "dsh.bin" in why, extra=why)

    from utils.engine import resolve_engine, VALID_ENGINES
    check("engine: dsh 是已知引擎（不会被打成拼错再按 direct 跑）",
          "dsh" in VALID_ENGINES and resolve_engine({"engine": "dsh"}) == "dsh")

    # headless 的 sandbox 工作区 = 进程 cwd；调用点大多不传 workdir，
    # 所以必须能从任务书路径反推项目根（否则 --root 场景写产物会被拒）。
    fake = str(Path(tempfile.mkdtemp(prefix="nf_root_")) / "data" / "state" / "tasks" / "x.md")
    check("能从任务书路径反推项目根（sandbox 工作区=进程 cwd 的坑）",
          DshClient._infer_workdir(fake) == str(Path(fake).parents[3]),
          DshClient._infer_workdir(fake))
    check("临时目录里的任务书不硬猜（返回 None = 用进程 cwd）",
          DshClient._infer_workdir(Path(tempfile.mkdtemp(prefix="nf_t_")) / "q.md") is None)


def test_model_patch():
    print("· 角色模型：--patch 覆盖 profile 的 agent-default-model（2026-11-20）")
    check("spec 不完整 → 一律 None（不猜、不生成半截 patch）",
          build_model_patch(None) is None
          and build_model_patch({}) is None
          and build_model_patch({"provider": "deepseek-official"}) is None
          and build_model_patch({"model": "deepseek-pro"}) is None)

    path = build_model_patch({"provider": "deepseek-official", "model": "deepseek-pro"})
    check("完整 spec → 真的写出一个文件", bool(path) and Path(path).is_file())
    body = Path(path).read_text(encoding="utf-8") if path else ""
    check("patch 落在 agent-default-model 上（别的 id 不会改到模型）",
          "id: agent-default-model" in body
          and "@deepseek-ai/dsh-agent-default-model" in body)
    check("provider / model 都写进去了（引号让特殊字符不会破坏 YAML）",
          '"deepseek-official"' in body and '"deepseek-pro"' in body)

    c = DshClient(dsh_bin="dsh",
                  model_spec={"provider": "deepseek-official", "model": "deepseek-pro"})
    cmd = c._build_cmd()
    check("配了角色模型 → 命令带 --patch 且指向那个文件",
          _argval(cmd, "--patch") == c._patch_path)
    check("--patch 仍在 stdin 标记之前（任务永远最后一个参数）",
          "--patch" in cmd and cmd[-1] == "-" and cmd.index("--patch") < len(cmd) - 1)

    c0 = DshClient(dsh_bin="dsh")
    check("没配角色模型 → 绝不带 --patch（11-17 的老行为不被静默改掉）",
          "--patch" not in c0._build_cmd())

    # 装配层：配置条目 → spec（纯函数，不起进程）
    from utils.llm_client import dsh_model_spec
    check("角色条目写齐 dsh_provider + dsh_model 才生效",
          dsh_model_spec({"dsh_provider": "deepseek-official",
                          "dsh_model": "deepseek-pro"})
          == {"provider": "deepseek-official", "model": "deepseek-pro"})
    check("只写一半 → None（回落到 profile 默认，而不是拼个半截 patch）",
          dsh_model_spec({"dsh_provider": "deepseek-official"}) is None
          and dsh_model_spec({"dsh_model": "deepseek-pro"}) is None
          and dsh_model_spec({"provider": "tokenhub", "id": "kimi-k2.6"}) is None)


def main():
    print("=" * 66)
    print("DshClient 自检（headless / 失败可见 / 超时 / 流式停止）")
    print("=" * 66)
    test_build_cmd()
    test_parse_events()
    test_finish_mapping()
    test_run_task_with_stubs()
    test_stream_and_stop()
    test_post_chat()
    test_resume_fallback()
    test_locate_and_wire()
    test_model_patch()
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
