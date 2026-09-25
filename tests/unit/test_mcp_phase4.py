# -*- coding: utf-8 -*-
"""MCP Phase 4 测试：nf_dispatch_task / nf_get_agent_run（LOCAL 工具）。

零真实 Hermes 调用 —— hermes_cmd 指向 stub 脚本（写结果文件后立即退出 0），
在**临时项目根**（NF_ROOT）下运行，真实仓库 data/ 零污染。

覆盖：
  1. tools/list 含 2 个 LOCAL 工具（nf_dispatch_task / nf_get_agent_run）
  2. nf_dispatch_task → running →（stub 退出）→ done + result_summary
  3. nf_get_agent_run(不存在) → ok=False + isError
  4. nf_get_agent_run(不传 run_id) → 最近列表
  5. title/context 缺失 → 可行动报错
  6. hermes_cmd 不可用 → error 状态（派发失败不阻塞）
  7. HTTP 工具错误分支返回 {"type":"text","text":...}（裸 key 回归）
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

API_PORT = 8792
MCP_PORT = 8793


def port_ready(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def read_response(sock, timeout=15):
    """读取一行 JSON-RPC 响应（newline-delimited）。"""
    sock.settimeout(timeout)
    buf = b""
    try:
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        pass
    line = buf.strip()
    if not line:
        return None
    try:
        return json.loads(line.decode("utf-8"))
    except Exception:
        return None


def mcp_call(sock, name, arguments, call_id):
    req = {"jsonrpc": "2.0", "id": call_id, "method": "tools/call",
           "params": {"name": name, "arguments": arguments}}
    sock.sendall(json.dumps(req).encode("utf-8") + b"\n")
    return read_response(sock)


def parse_payload(r):
    """从 tools/call 响应提取 payload dict。"""
    if not r or "result" not in r:
        return None
    try:
        return json.loads(r["result"]["content"][0]["text"])
    except Exception:
        return None


def main():
    tmp_root = tempfile.mkdtemp(prefix="nf_mcp_p4_")
    stub_dir = os.path.join(tmp_root, "stub")
    os.makedirs(stub_dir, exist_ok=True)

    # ---- stub hermes：写结果摘要到任务书同目录，退出 0 ----
    stub = os.path.join(stub_dir, "hermes_stub.bat")
    with open(stub, "w", encoding="utf-8") as f:
        f.write("@echo off\r\nexit /b 0\r\n")

    # ---- 临时项目根：最小 config + data ----
    cfg_dir = os.path.join(tmp_root, "config")
    os.makedirs(cfg_dir, exist_ok=True)
    with open(os.path.join(cfg_dir, "system.yaml"), "w", encoding="utf-8") as f:
        f.write("engine: direct\nbudget:\n  limit_yuan: 100\n")

    env = os.environ.copy()
    env["NF_MCP_PORT"] = str(MCP_PORT)
    env["NF_ROOT"] = tmp_root
    env["NF_HERMES_CMD"] = stub

    proc = subprocess.Popen(
        [sys.executable, "scripts/nf_api.py", "--port", str(API_PORT),
         "--allow-fake", "--host", "127.0.0.1"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    api_ok = mcp_ok = False
    for _ in range(40):
        time.sleep(0.5)
        api_ok = port_ready(API_PORT)
        mcp_ok = port_ready(MCP_PORT)
        if api_ok and mcp_ok:
            break

    checks = []
    sock = None
    try:
        if not (api_ok and mcp_ok):
            checks.append((f"服务启动 api={api_ok} mcp={mcp_ok}", False))
            return report(checks)

        sock = socket.create_connection(("127.0.0.1", MCP_PORT), timeout=5)

        # ---- 1. tools/list 含 LOCAL 工具 ----
        sock.sendall(b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n')
        r = read_response(sock)
        tools = [t["name"] for t in (r or {}).get("result", {}).get("tools", [])]
        has_local = "nf_dispatch_task" in tools and "nf_get_agent_run" in tools
        checks.append(("tools/list 含 nf_dispatch_task + nf_get_agent_run", has_local))

        # ---- 2. 派发 → running → done ----
        r = mcp_call(sock, "nf_dispatch_task", {
            "title": "P4 测试任务",
            "context": "这是测试上下文，stub 会立即退出。",
            "timeout_s": 30,
        }, 2)
        p = parse_payload(r)
        ok = (p and p.get("ok") and p.get("status") == "running" and p.get("run_id"))
        checks.append((f"dispatch → running（run_id={p.get('run_id') if p else None}）", bool(ok)))
        run_id = p.get("run_id") if p else None

        if run_id:
            # 等 watcher 线程把终态写进 jsonl（stub 秒退，轮询 ≤5s）
            done_rec = None
            for _ in range(10):
                time.sleep(0.5)
                r2 = mcp_call(sock, "nf_get_agent_run", {"run_id": run_id}, 90)
                p2 = parse_payload(r2)
                if p2 and p2.get("status") in ("done", "error"):
                    done_rec = p2
                    break
            ok = done_rec is not None and done_rec.get("status") == "done"
            checks.append((f"watcher 终态 done（实际 {done_rec.get('status') if done_rec else 'timeout'}）", ok))

            # ---- 4. 列表模式 ----
            r3 = mcp_call(sock, "nf_get_agent_run", {"limit": 5}, 91)
            p3 = parse_payload(r3)
            ok = p3 and p3.get("ok") and any(x.get("run_id") == run_id for x in p3.get("runs", []))
            checks.append(("列表模式包含新 run", bool(ok)))

        # ---- 3. 不存在的 run ----
        r4 = mcp_call(sock, "nf_get_agent_run", {"run_id": "no_such_run"}, 92)
        p4 = parse_payload(r4)
        is_err = (r4 or {}).get("result", {}).get("isError") is True
        ok = p4 and p4.get("ok") is False and is_err
        checks.append(("不存在 run → ok=False + isError", bool(ok)))

        # ---- 5. 参数缺失 ----
        r5 = mcp_call(sock, "nf_dispatch_task", {"title": "只有标题"}, 93)
        p5 = parse_payload(r5)
        ok = p5 and p5.get("ok") is False and "context" in str(p5.get("error", ""))
        checks.append(("缺 context → 可行动报错", bool(ok)))

        # ---- 6. hermes 不可用 → error 状态（不阻塞）----
        # NF_HERMES_CMD 指向不存在的路径：通过临时进程环境难以热改，
        # 用 dispatch 模块直调验证（MCP 层只兜异常，模块层已兜错）。
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        os.environ["NF_ROOT"] = tmp_root
        os.environ["NF_HERMES_CMD"] = os.path.join(stub_dir, "not_exist.exe")
        import nf_agent_dispatch as disp
        bad = disp.dispatch_to_hermes("坏命令测试", "上下文", source="test")
        ok = bad.get("ok") is False and bad.get("status") == "error"
        checks.append(("hermes 不可用 → error 状态（不阻塞）", bool(ok)))

        # ---- 7. HTTP 错误分支裸 key 回归 ----
        # nf_get_review_report 在无报告时返回 404/400 → 走 isError 分支，
        # 响应必须含 content[0].text（曾裸 key 丢字段）。
        r7 = mcp_call(sock, "nf_get_review_report", {}, 94)
        ok = (r7 and "result" in r7 and "content" in r7.get("result", {})
              and "text" in r7["result"]["content"][0])
        checks.append(("HTTP 错误分支 content[0].text 存在（裸 key 回归）", bool(ok)))

    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        import shutil
        shutil.rmtree(tmp_root, ignore_errors=True)

    return report(checks)


def report(checks):
    passed = sum(1 for _, ok in checks if ok)
    failed = len(checks) - passed
    print(f"\n=== MCP Phase 4 ({passed}/{len(checks)}) ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
