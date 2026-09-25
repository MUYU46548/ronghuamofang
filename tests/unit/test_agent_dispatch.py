# -*- coding: utf-8 -*-
"""多 Agent 派发自检（Phase 4）：nf_agent_dispatch + MCP 工具端到端。

**不碰真实仓库数据**：把 scripts/ 复制到临时目录当 ROOT（NF_ROOT），
派发用 stub hermes（本文件生成的 .bat/.sh，秒退）与不存在的命令两种，
覆盖任务书生成 / 派发 / 状态查询 / jsonl 落盘 / 失败不阻塞。

用法：python tests/unit/test_agent_dispatch.py
"""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
MCP_PORT = 8793

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def build_tmp_root():
    tmp = Path(tempfile.mkdtemp(prefix="agent_dispatch_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "data" / "state").mkdir(parents=True)
    return tmp


def make_stub_hermes(tmp):
    """生成秒退的 stub hermes（模拟子会话成功结束）。返回命令字符串。"""
    stub = tmp / "stub_hermes.bat"
    stub.write_text("@echo off\r\necho stub-hermes-ok\r\nexit /b 0\r\n",
                    encoding="ascii")
    return str(stub).replace("\\", "/")


def read_runs(tmp):
    path = tmp / "data" / "state" / "agent_runs.jsonl"
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def latest_run(tmp, run_id):
    rec = None
    for r in read_runs(tmp):
        if r.get("run_id") == run_id:
            rec = r
    return rec


def wait_status(tmp, run_id, statuses, timeout=15):
    for _ in range(int(timeout / 0.2)):
        rec = latest_run(tmp, run_id)
        if rec and rec.get("status") in statuses:
            return rec
        time.sleep(0.2)
    return latest_run(tmp, run_id)


def call_py(tmp, code):
    """在临时 ROOT 下跑一段 Python（import nf_agent_dispatch）。"""
    env = dict(os.environ, NF_ROOT=str(tmp))
    out = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=env,
                         cwd=str(tmp))
    return out.returncode, out.stdout, out.stderr


def mcp_call(sock, method, params=None, id_=1):
    req = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        req["params"] = params
    sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
    buf = b""
    sock.settimeout(20)
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    line = buf.split(b"\n")[0].strip()
    return json.loads(line.decode("utf-8")) if line else None


def main():
    tmp = build_tmp_root()
    stub_cmd = make_stub_hermes(tmp)
    print("临时项目根:", tmp)

    print("\n=== 1. build_task_brief：任务书生成 ===")
    code = (
        "import sys, json; sys.path.insert(0, r'%s');\n"
        "import nf_agent_dispatch as d\n"
        "rid, p = d.build_task_brief('测试标题', '测试上下文正文')\n"
        "print(json.dumps({'run_id': rid, 'exists': p.is_file(),"
        " 'text': p.read_text(encoding='utf-8')}))"
    ) % (tmp / "scripts")
    rc, out, err = call_py(tmp, code)
    ok = rc == 0
    data = {}
    if ok:
        data = json.loads(out.strip().splitlines()[-1])
    check("build_task_brief 返回 run_id", ok and bool(data.get("run_id")), err[:200])
    check("任务书落盘且含标题/上下文",
          ok and data.get("exists") and "测试标题" in data.get("text", "")
          and "测试上下文正文" in data.get("text", ""))

    print("\n=== 2. 参数校验（缺 title/context 不派发、不落盘）===")
    code = (
        "import sys, json; sys.path.insert(0, r'%s');\n"
        "import nf_agent_dispatch as d\n"
        "r1 = d.dispatch_to_hermes('', 'x')\n"
        "r2 = d.dispatch_to_hermes('t', '')\n"
        "print(json.dumps({'r1': r1.get('ok'), 'r2': r2.get('ok'),"
        " 'runs': len(d.list_agent_runs())}))"
    ) % (tmp / "scripts")
    rc, out, err = call_py(tmp, code)
    d2 = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("缺 title / context 各自 ok=False", d2.get("r1") is False and d2.get("r2") is False)
    check("校验失败不写 agent_runs.jsonl", d2.get("runs") == 0)

    print("\n=== 3. dispatch_to_hermes：stub 子会话成功路径 ===")
    code = (
        "import sys, json; sys.path.insert(0, r'%s');\n"
        "import nf_agent_dispatch as d\n"
        "r = d.dispatch_to_hermes('派发测试', '给子会话的上下文',"
        " hermes_cmd=r'%s', timeout_s=30)\n"
        "print(json.dumps(r))"
    ) % (tmp / "scripts", stub_cmd)
    rc, out, err = call_py(tmp, code)
    rec = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("派发即返回 ok=True + run_id", rec.get("ok") and bool(rec.get("run_id")), out[:200])
    check("brief_path 是相对路径（不泄露本机绝对路径）",
          str(rec.get("brief_path", "")).startswith("data/state/agent_briefs/"))
    run_id = rec.get("run_id", "")
    # 派发进程退出后 watcher 线程也没了 → 状态停在 running（记录仍在）。
    # 换成常驻模式验证终态：起一个 python 进程派发并等终态。
    code = (
        "import sys, json, time; sys.path.insert(0, r'%s');\n"
        "import nf_agent_dispatch as d\n"
        "r = d.dispatch_to_hermes('常驻验证', 'ctx', hermes_cmd=r'%s', timeout_s=60)\n"
        "for _ in range(100):\n"
        "    time.sleep(0.2)\n"
        "    if d.get_agent_run(r['run_id']).get('status') in ('done','error'):\n"
        "        break\n"
        "print(json.dumps(d.get_agent_run(r['run_id'])))"
    ) % (tmp / "scripts", stub_cmd)
    rc, out, err = call_py(tmp, code)
    final = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("stub 子会话退出 → status=done", final.get("status") == "done", out[:250])
    check("终态记录含 exit_code=0", final.get("exit_code") == 0)
    check("get_agent_run 返回 ok=True", final.get("ok") is True)

    print("\n=== 4. 失败路径：hermes 不存在 → error 状态、不阻塞 ===")
    code = (
        "import sys, json; sys.path.insert(0, r'%s');\n"
        "import nf_agent_dispatch as d\n"
        "r = d.dispatch_to_hermes('坏命令测试', 'ctx',"
        " hermes_cmd='no_such_hermes_xyz')\n"
        "print(json.dumps(r))"
    ) % (tmp / "scripts")
    rc, out, err = call_py(tmp, code)
    bad = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("坏命令 → ok=False 且不抛异常", rc == 0 and bad.get("ok") is False, out[:200])
    check("坏命令 → status=error + error 文本",
          bad.get("status") == "error" and bool(bad.get("error")))
    bad_id = bad.get("run_id", "")
    check("失败也落盘 agent_runs.jsonl", latest_run(tmp, bad_id) is not None)

    print("\n=== 5. list_agent_runs / get_agent_run 不存在 ===")
    code = (
        "import sys, json; sys.path.insert(0, r'%s');\n"
        "import nf_agent_dispatch as d\n"
        "print(json.dumps({'n': len(d.list_agent_runs()),"
        " 'miss': d.get_agent_run('no_such_run')}))"
    ) % (tmp / "scripts")
    rc, out, err = call_py(tmp, code)
    d5 = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    check("list_agent_runs ≥ 2（成功 + 失败各一）", (d5.get("n") or 0) >= 2, d5)
    check("查不存在 run → ok=False 不抛异常",
          d5.get("miss", {}).get("ok") is False and "error" in d5.get("miss", {}))

    print("\n=== 6. MCP 端到端：tools/list + nf_dispatch_task + nf_get_agent_run ===")
    # ⚠️ 必须注入 NF_HERMES_CMD=stub —— 否则 MCP 进程用默认 "hermes" 命令
    # 真实拉起子会话（烧 token），且真实会话不会在轮询窗口内结束 → FAIL。
    env = dict(os.environ, NF_ROOT=str(tmp), NF_MCP_PORT=str(MCP_PORT),
               NF_HERMES_CMD=stub_cmd)
    proc = subprocess.Popen(
        [PY, str(tmp / "scripts" / "nf_mcp.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sock = None
    try:
        sock = None
        for _ in range(40):
            time.sleep(0.25)
            try:
                sock = socket.create_connection(("127.0.0.1", MCP_PORT), timeout=2)
                break
            except OSError:
                continue
        check("MCP server 启动（tcp://127.0.0.1:%d）", sock is not None)
        if sock is None:
            raise RuntimeError("MCP 未起来")

        r = mcp_call(sock, "tools/list")
        tools = r.get("result", {}).get("tools", []) if r else []
        names = [t["name"] for t in tools]
        # 15 个 HTTP 工具 + 2 个 LOCAL 工具 = 17（Phase 5 加了 nf_get_stream_status）
        check("tools/list 含 17 个工具（含 2 个新工具）", len(tools) == 17, names)
        check("新工具在清单里",
              "nf_dispatch_task" in names and "nf_get_agent_run" in names)
        check("tools/list 不泄露 _local/_http 内部字段",
              all("_local" not in t and "_http" not in t for t in tools))

        # 派发（stub）—— 注意 MCP 进程的 NF_HERMES_CMD 用环境变量注入 stub
        r = mcp_call(sock, "tools/call", {
            "name": "nf_dispatch_task",
            "arguments": {"title": "MCP 派发测试", "context": "经 MCP 派发的上下文",
                          "timeout_s": 30}})
        payload = {}
        try:
            payload = json.loads(r["result"]["content"][0]["text"])
        except Exception:
            pass
        check("nf_dispatch_task 返回 run_id",
              payload.get("ok") and bool(payload.get("run_id")), r)
        mcp_run = payload.get("run_id", "")
        check("返回 brief_path", str(payload.get("brief_path", "")).startswith(
            "data/state/agent_briefs/"))
        check("初始状态 running", payload.get("status") == "running")

        # 轮询到终态（stub 秒退，watcher 线程在 MCP 进程内）
        final_mcp = {}
        for _ in range(75):
            time.sleep(0.2)
            r = mcp_call(sock, "tools/call", {
                "name": "nf_get_agent_run",
                "arguments": {"run_id": mcp_run}}, id_=2)
            try:
                final_mcp = json.loads(r["result"]["content"][0]["text"])
            except Exception:
                final_mcp = {}
            if final_mcp.get("status") in ("done", "error"):
                break
        check("nf_get_agent_run 查到终态 done", final_mcp.get("status") == "done",
              final_mcp)
        check("MCP 派发也落盘 agent_runs.jsonl",
              any(x.get("run_id") == mcp_run for x in read_runs(tmp)))

        # 列表模式
        r = mcp_call(sock, "tools/call", {
            "name": "nf_get_agent_run", "arguments": {}}, id_=3)
        lst = {}
        try:
            lst = json.loads(r["result"]["content"][0]["text"])
        except Exception:
            pass
        check("nf_get_agent_run 无 run_id → 列表模式",
              lst.get("ok") and len(lst.get("runs", [])) >= 3, lst.get("runs", [])[:1])

        # 缺参 → isError + ok:false（不崩协议）
        r = mcp_call(sock, "tools/call", {
            "name": "nf_dispatch_task",
            "arguments": {"title": "只有标题"}}, id_=4)
        badp = {}
        try:
            badp = json.loads(r["result"]["content"][0]["text"])
        except Exception:
            pass
        check("缺 context → isError + ok:false",
              r.get("result", {}).get("isError") is True and badp.get("ok") is False)
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    print("\n=== 7. CLI：--list / --get ===")
    env = dict(os.environ, NF_ROOT=str(tmp))
    out = subprocess.run([PY, str(tmp / "scripts" / "nf_agent_dispatch.py"), "--list"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=env)
    check("CLI --list 退出码 0 且含表头", out.returncode == 0 and "run_id" in out.stdout)
    out = subprocess.run([PY, str(tmp / "scripts" / "nf_agent_dispatch.py"),
                          "--get", mcp_run],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=env)
    check("CLI --get 返回该 run（done）",
          out.returncode == 0 and '"status": "done"' in out.stdout)

    print("\n=== 8. jsonl 完整性 ===")
    runs = read_runs(tmp)
    check("agent_runs.jsonl 每行都是合法 JSON 且带 run_id",
          runs and all(r.get("run_id") for r in runs))
    statuses = {r.get("status") for r in runs}
    check("状态机只出现 running/done/error", statuses <= {"running", "done", "error"},
          statuses)

    print("\n=== 结果：{}/{} ===".format(len(PASS), len(PASS) + len(FAIL)))
    for name in FAIL:
        print("  [FAIL]", name)
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
