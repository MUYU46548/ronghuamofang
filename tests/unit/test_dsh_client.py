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

from utils.dsh_client import (DshClient, dsh_available,              # noqa: E402
                              resolve_dsh_bin)

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


def _stub_transport_then_ok(tmp, fail_times=2):
    """前 fail_times 次报 `TRANSPORT: Connection error` 并退出 1，之后成功。

    真机形态照抄 2026-11-24 实测：段群已落盘、DSH 打一行诊断到 stderr、退出非零。
    """
    cnt = Path(tmp) / "dsh_transport_count.txt"
    return _bat(tmp, "dsh_transport.bat", [
        "@echo off",
        "set N=0",
        'if exist "%s" set /p N=<"%s"' % (cnt, cnt),
        "set /a N=N+1",
        '> "%s" echo %%N%%' % cnt,
        "if %%N%% LEQ %d (" % fail_times,
        "  echo dsh: TRANSPORT: Connection error. 1>&2",
        "  exit /b 1",
        ")",
        'echo {"type":"session","sessionId":"sess-tr"}',
        'echo {"type":"status","phase":"step_end","turn":1,"step":1,'
        '"usage":{"inputTokens":50,"outputTokens":10}}',
        'echo {"type":"final","sessionId":"sess-tr",'
        '"reason":{"kind":"completed"},"text":"TR_RECOVERED"}',
    ])


# ------------------------------------------------- 传输断连重发（2026-11-24）

def test_transport_retry():
    print("· 传输断连：自动重发 / 只认 TRANSPORT / 重发不丢账")
    tmp = tempfile.mkdtemp(prefix="nf_dsh_tr_")
    try:
        task = Path(tmp) / "task.md"
        task.write_text("任务", encoding="utf-8")

        c = DshClient(dsh_bin=str(_stub_transport_then_ok(tmp, 2)),
                      transport_retries=2, transport_backoff_sec=0)
        c._sleep = lambda s: None
        res = c.run_task(task, workdir=tmp)
        check("两次断连后重发生效（exit 0 + 正文回来）",
              res["exit_code"] == 0 and res["stdout_tail"] == "TR_RECOVERED",
              extra=str(res))
        check("重发次数有记录（transport_retries=2）",
              res.get("transport_retries") == 2, extra=str(res.get("transport_retries")))
        check("三次尝试都进账（requests=3，失败那次的 token 不丢）",
              res["requests"] == 3 and res["tokens"] >= 50,
              extra="requests=%s tokens=%s" % (res.get("requests"), res.get("tokens")))

        # 关掉重发 → 回到旧行为：一次就交上去（否则真断网时会白等 15 秒）
        c0 = DshClient(dsh_bin=str(_stub_transport_then_ok(tmp, 99)),
                       transport_retries=0, transport_backoff_sec=0)
        r0 = c0.run_task(task, workdir=tmp)
        check("transport_retries=0 → 不重发（exit 1 / requests=1）",
              r0["exit_code"] == 1 and r0["requests"] == 1, extra=str(r0))

        # 非传输失败（模型/provider 报错）**不重发** —— 重发只会再错一次，还白烧额度
        cf = DshClient(dsh_bin=str(_stub_fail(tmp)),
                       transport_retries=2, transport_backoff_sec=0)
        cf._sleep = lambda s: None
        rf = cf.run_task(task, workdir=tmp)
        check("provider 报错不重发（exit 1 / requests=1）",
              rf["exit_code"] == 1 and rf["requests"] == 1, extra=str(rf))

        # 流式那条路（stage4 实际走的）也得重发，且不许把正文在界面上叠两遍
        tr2 = tempfile.mkdtemp(prefix="nf_dsh_trs_")
        try:
            pieces = []
            cs = DshClient(dsh_bin=str(_stub_transport_then_ok(tr2, 1)),
                           transport_retries=2, transport_backoff_sec=0)
            cs._sleep = lambda s: None
            rs = cs.run_task_stream(task, lambda p: pieces.append(p), None, workdir=tr2)
            check("流式路径断连后重发生效（exit 0）",
                  rs["exit_code"] == 0, extra=str(rs))
            check("重发只留一句中断提示，不回放已收片段",
                  any("中断" in p for p in pieces)
                  and sum(1 for p in pieces if "STUB" in p or "TR_" in p) == 0,
                  extra=str(pieces[:4]))
        finally:
            shutil.rmtree(tr2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    print("· _build_cmd：profile / stdin 任务 / 审批策略 / 无 --model")
    c = DshClient(dsh_bin="dsh")
    cmd = c._build_cmd()
    # 2026-11-24 实测翻转：`desktop` 档被 DSH 拒绝命令行调用
    # （"managed exclusively by the Electron application"，连 --dump-config 都拒），
    # 墨坊走命令行 → 默认必须是 headless，否则每一章都跑不起来。
    check("默认 --profile headless（desktop 档命令行调不通）",
          "--profile" in cmd and cmd[cmd.index("--profile") + 1] == "headless")
    check("显式传 profile 会覆盖默认", DshClient(dsh_bin="dsh", profile="desktop")
          ._build_cmd()[2] == "desktop")
    check("空字符串 profile 回落 headless", DshClient(dsh_bin="dsh", profile="  ")
          ._build_cmd()[2] == "headless")
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

    cmd_patch = c._build_cmd(patch="Q:/fixture/nf_write_patch.yml")
    check("显式传 patch 时拼上 --patch（写作边界用，不改模型）",
          "--patch" in cmd_patch
          and cmd_patch[cmd_patch.index("--patch") + 1] == "Q:/fixture/nf_write_patch.yml")
    check("拼了 --patch 后 stdin 标记仍在最后", cmd_patch[-1] == "-")


def test_writing_boundary():
    print("· 写作边界：任务书内联 / 禁工具补丁 / 非写作任务不变")
    tmp = Path(tempfile.mkdtemp(prefix="nf_dsh_w_"))
    try:
        tasks = tmp / "data" / "state" / "tasks"
        tasks.mkdir(parents=True)
        body = ("# 任务书\n\nUNIQUE_TASK_BODY_MARKER_42\n\n"
                "## 输入文件（用 read_file 读取）\n- " + str(tmp / "in.md") + "\n")
        t_stage4 = tasks / "stage4_ch01_g1.md"
        t_stage4.write_text(body, encoding="utf-8")
        t_stage2 = tasks / "stage2_global_outline.md"
        t_stage2.write_text(body, encoding="utf-8")
        t_refine = tasks / "stage4_refine_ch01.md"
        t_refine.write_text(body, encoding="utf-8")
        t_stage5 = tasks / "stage5_check_batch1.md"
        t_stage5.write_text(body, encoding="utf-8")

        c = DshClient(dsh_bin="dsh")
        check("stage4 章任务认成写作任务", c._is_writing_task(t_stage4) is True)
        check("stage2 大纲任务认成写作任务", c._is_writing_task(t_stage2) is True)
        check("stage4 精修不算写作任务（不动 GUI 精修）",
              c._is_writing_task(t_refine) is False)
        check("stage5 检查不算写作任务（不动审查/校对）",
              c._is_writing_task(t_stage5) is False)

        inline = DshClient._task_prompt(t_stage4, inline=True)
        check("写作提示内联任务书正文（不再让模型读任务文件）",
              "UNIQUE_TASK_BODY_MARKER_42" in inline)
        check("写作提示带硬边界（不逛目录 / 不跑命令自检 / 一次 write）",
              "【写作任务边界" in inline and "不要运行命令" in inline
              and "一次 write" in inline)
        old = DshClient._task_prompt(t_stage5)
        check("非写作任务维持旧行为（只给路径，不内联正文）",
              "UNIQUE_TASK_BODY_MARKER_42" not in old
              and str(t_stage5.resolve()) in old)

        p = c._writing_patch()
        check("写作补丁文件已生成", bool(p) and Path(p).exists(), str(p))
        txt = Path(p).read_text(encoding="utf-8")
        check("补丁禁掉 shell / 搜索 / 子代理 / 联网",
              all(("- id: " + x) in txt for x in
                  ("tool-pwsh", "tool-bash", "tool-fs-search",
                   "tool-jobs", "tool-skill", "tool-agent-team", "tool-web"))
              and "disabled: true" in txt)
        check("补丁保留 tool-fs（正文要落盘）", "- id: tool-fs\n" not in txt)
        check("补丁不碰模型配置（不许出现 agent-default-model / provider）",
              "agent-default-model" not in txt and "provider:" not in txt)

        # 端到端：桩记录 argv 与 stdin —— 判“物理上真的禁了工具、真的内联了”
        cap = _bat(tmp, "dsh_capture.bat", [
            "@echo off",
            'echo %* > "%~dp0argv.txt"',
            '"' + sys.executable + '" "%~dp0cap.py" >nul',
            'echo {"type":"session","sessionId":"s"}',
            'echo {"type":"final","sessionId":"s","reason":{"kind":"completed"},'
            '"text":"OK"}',
        ])
        (tmp / "cap.py").write_text(
            "import pathlib, sys\n"
            "p = pathlib.Path(sys.argv[0]).resolve().with_name('stdin.txt')\n"
            "p.write_text(sys.stdin.read(), encoding='utf-8')\n",
            encoding="utf-8")

        res = DshClient(dsh_bin=str(cap)).run_task(t_stage4, workdir=tmp)
        args = (tmp / "argv.txt").read_text(encoding="utf-8", errors="replace")
        stdin = (tmp / "stdin.txt").read_text(encoding="utf-8", errors="replace")
        check("写作调用真的带上 --patch（物理禁工具，不只靠提示词）",
              "--patch" in args and "nf_dsh_writing_patch" in args, args)
        check("写作调用 stdin 是内联后的任务书（含边界与正文）",
              "【写作任务边界" in stdin and "UNIQUE_TASK_BODY_MARKER_42" in stdin)
        check("写作调用成功收尾", res["exit_code"] == 0, str(res))

        for f in ("argv.txt", "stdin.txt"):
            (tmp / f).unlink(missing_ok=True)
        res2 = DshClient(dsh_bin=str(cap)).run_task(t_stage5, workdir=tmp)
        args2 = (tmp / "argv.txt").read_text(encoding="utf-8", errors="replace")
        stdin2 = (tmp / "stdin.txt").read_text(encoding="utf-8", errors="replace")
        check("非写作调用不带 --patch", "--patch" not in args2, args2)
        check("非写作调用维持旧提示（只给任务文件路径）",
              "【写作任务边界" not in stdin2
              and str(t_stage5.resolve()) in stdin2)
        check("非写作调用成功收尾", res2["exit_code"] == 0, str(res2))

        for f in ("argv.txt", "stdin.txt"):
            (tmp / f).unlink(missing_ok=True)
        res3 = DshClient(dsh_bin=str(cap)).run_task_stream(
            t_stage4, lambda piece: None, None, workdir=tmp)
        args3 = (tmp / "argv.txt").read_text(encoding="utf-8", errors="replace")
        stdin3 = (tmp / "stdin.txt").read_text(encoding="utf-8", errors="replace")
        check("流式写作调用也带 --patch", "--patch" in args3, args3)
        check("流式写作调用也内联任务书正文",
              "UNIQUE_TASK_BODY_MARKER_42" in stdin3)
        check("流式写作调用成功收尾", res3["exit_code"] == 0, str(res3))

        c_off = DshClient(dsh_bin="dsh", writing_boundary=False)
        off_prompt = c_off._task_prompt(t_stage4, inline=c_off.writing_boundary)
        check("writing_boundary=False 回到旧行为（不内联）",
              "UNIQUE_TASK_BODY_MARKER_42" not in off_prompt
              and str(t_stage4.resolve()) in off_prompt)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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


def test_never_passes_model():
    print("· 墨坊不向 DSH 传模型名（2026-11-20 作者定案）")
    c = DshClient(dsh_bin="dsh")
    cmd = c._build_cmd()
    check("默认命令里没有 --patch（不拿 patch 去改 DSH 的模型配置）",
          "--patch" not in cmd)
    check("命令里没有 --model（headless 本就没这旋钮，也不许自己拼）",
          not any(str(a).startswith("--model") for a in cmd))
    check("任务永远最后一个参数（stdin 标记 '-' 收尾）", cmd[-1] == "-")

    # 写作边界用的是同一个 --patch 旋钮，但只覆盖工具插件（见 test_writing_boundary）。
    # 这里锁的是“默认调用”不带它 —— 精修/审查/单轮直答都不受影响。
    check("写作边界的 --patch 只走显式参数，默认调用不受影响",
          "--patch" not in DshClient(dsh_bin="dsh")._build_cmd())


def test_headless_patch_guard():
    print("· headless 补丁层：不许被打开成“吃桌面登录态”（2026-11-20 事故护栏）")
    import utils.dsh_client as dc
    tmp = Path(tempfile.mkdtemp(prefix="nf_patch_"))
    try:
        prof = tmp / "profiles" / "headless"
        prof.mkdir(parents=True)
        patch = prof / "cordis.patch.yml"

        ok, why = dc.headless_patch_guard(home=tmp)
        check("补丁层不存在 → 视为干净", ok is True, why)

        patch.write_text("[]\n", encoding="utf-8")
        ok, why = dc.headless_patch_guard(home=tmp)
        check("空补丁层 [] → 干净", ok is True, why)

        patch.write_text(
            "- id: authorization\n  name: '@deepseek-ai/dsh-authorization'\n  disabled: false\n",
            encoding="utf-8")
        ok, why = dc.headless_patch_guard(home=tmp)
        check("打开 authorization → 红", ok is False, why)
        check("红的时候要说明后果是“官方账户”", "官方账户" in why, why)
        check("红的时候只点出真被打开的那条", "authorization" in why and "credentials" not in why, why)

        patch.write_text(
            "- id: authorization\n  name: '@deepseek-ai/dsh-authorization'\n  disabled: false\n"
            "- id: credentials\n  name: '@deepseek-ai/dsh-credentials-local'\n  disabled: false\n"
            "- id: deepseek-account\n  name: '@deepseek-ai/dsh-deepseek-account'\n  disabled: false\n",
            encoding="utf-8")
        ok, why = dc.headless_patch_guard(home=tmp)
        check("三条全开 → 红，且三条都点名", ok is False and why.count("-") >= 0 and all(
            k in why for k in ("authorization", "credentials", "deepseek-account")), why)

        patch.write_text(
            "- id: authorization\n  disabled: true\n"
            "- id: credentials\n  disabled: true\n"
            "- id: deepseek-account\n  disabled: true\n",
            encoding="utf-8")
        ok, why = dc.headless_patch_guard(home=tmp)
        check("显式 disabled: true → 干净", ok is True, why)

        # 反证：真机文件（作者的 ~/.dsh）现在必须是干净的 —— 这轮已还原成 []
        real_ok, real_why = dc.headless_patch_guard()
        check("本机 headless 补丁层当前干净（已还原）", real_ok is True, real_why)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 66)
    print("DshClient 自检（headless / 失败可见 / 超时 / 流式停止）")
    print("=" * 66)
    test_build_cmd()
    test_writing_boundary()
    test_parse_events()
    test_finish_mapping()
    test_run_task_with_stubs()
    test_transport_retry()
    test_stream_and_stop()
    test_post_chat()
    test_resume_fallback()
    test_locate_and_wire()
    test_never_passes_model()
    test_headless_patch_guard()
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
