# -*- coding: utf-8 -*-
"""MCP 最小化端到端测试 - 快速验证 MCP 核心链路。"""

import json
import socket
import subprocess
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

API_PORT = 8790
MCP_PORT = 8791


def port_ready(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def read_response(sock, timeout=5):
    """读取一行 JSON-RPC 响应。"""
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


def main():
    # 清理旧进程（注意：os.system 走 cmd.exe，重定向须用 NUL 而非 $null）
    os.system(
        f'powershell -Command "Get-NetTCPConnection -LocalPort {API_PORT},{MCP_PORT} -ErrorAction SilentlyContinue | '
        f'ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}" > NUL 2>&1'
    )
    
    env = os.environ.copy()
    env["NF_MCP_PORT"] = str(MCP_PORT)
    
    proc = subprocess.Popen(
        [sys.executable, "scripts/nf_api.py", "--port", str(API_PORT),
         "--allow-fake", "--host", "127.0.0.1"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # 等待就绪
    import time
    for _ in range(20):
        time.sleep(0.5)
        if port_ready(API_PORT) and port_ready(MCP_PORT):
            break

    checks = []
    sock = None
    try:
        sock = socket.create_connection(("127.0.0.1", MCP_PORT), timeout=5)

        # 1. initialize
        sock.sendall(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
        r = read_response(sock)
        checks.append(("initialize", r and r.get("result", {}).get("serverInfo", {}).get("name") == "novelforge-mcp"))

        # 2. tools/list
        sock.sendall(b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n')
        r = read_response(sock)
        tools = r.get("result", {}).get("tools", []) if r else []
        # tools/list：15 个 HTTP 工具 + nf_dispatch_task + nf_get_agent_run = 17
        # （Phase 4 加 2 个 LOCAL 工具，Phase 5 加 nf_get_stream_status）
        checks.append((f"tools/list: {len(tools)} tools", len(tools) == 17))

        # 3. nf_get_state (真实 HTTP 调用)
        sock.sendall(json.dumps({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"nf_get_state","arguments":{}}}).encode() + b"\n")
        r = read_response(sock, timeout=10)
        ok = False
        if r and "result" in r:
            try:
                payload = json.loads(r["result"]["content"][0]["text"])
                ok = "has_work" in payload
            except Exception:
                pass
        checks.append(("nf_get_state", ok))

        # 4. 未知工具
        sock.sendall(b'{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"nf_xxx","arguments":{}}}\n')
        r = read_response(sock)
        checks.append(("unknown tool → error", r and "error" in r))

    finally:
        if sock:
            try: sock.close()
            except: pass
        proc.terminate()
        try: proc.wait(timeout=3)
        except: proc.kill()

    passed = sum(1 for _, ok in checks if ok)
    failed = len(checks) - passed
    print(f"\n=== MCP E2E ({passed}/{len(checks)}) ===")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
