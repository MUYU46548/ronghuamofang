# -*- coding: utf-8 -*-
"""MCP 自检脚本：验证 MCP 握手、tools/list、tools/call。

用法：
  # 先起 nf_api：
  python scripts/nf_api.py --port 8799 --allow-fake &
  # 等 HTTP 服务起来（2-3 秒）后跑：
  python tests/unit/test_mcp_basic.py

只调用只读端点，不修改任何文件。
"""

import json
import socket
import subprocess
import sys
import time
import os

# 让脚本可以从 tests/ 目录直接跑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API_PORT = 8799
MCP_PORT = 8796


def start_nf_api():
    """启动 nf_api.py，等待端口就绪，返回 Popen 对象。"""
    env = os.environ.copy()
    env["NF_MCP_PORT"] = str(MCP_PORT)
    proc = subprocess.Popen(
        [sys.executable, "scripts/nf_api.py", "--port", str(API_PORT),
         "--allow-fake", "--host", "127.0.0.1"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # 等服务起来（最多 20 秒）
    for _ in range(40):
        time.sleep(0.5)
        api_ok = mcp_ok = False
        try:
            with socket.create_connection(("127.0.0.1", API_PORT), timeout=1):
                api_ok = True
        except OSError:
            pass
        try:
            with socket.create_connection(("127.0.0.1", MCP_PORT), timeout=1):
                mcp_ok = True
        except OSError:
            pass
        if api_ok and mcp_ok:
            return proc
    proc.kill()
    raise RuntimeError(f"服务启动失败: http={api_ok} mcp={mcp_ok}")


def mcp_call(sock, method, params=None):
    """发送一个 JSON-RPC 请求，读一行响应（newline-delimited）。"""
    global _call_id
    _call_id += 1
    my_id = _call_id
    req = {"jsonrpc": "2.0", "id": my_id, "method": method}
    if params is not None:
        req["params"] = params
    data = (json.dumps(req) + "\n").encode("utf-8")
    sock.sendall(data)
    # 逐行读取，直到拿到匹配 id 的响应
    buf = b""
    sock.settimeout(10)
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            # 尝试逐行解析
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8"))
                    if obj.get("id") == my_id:
                        return obj
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
        except socket.timeout:
            break
    return None


_call_id = 0


def main():
    global _call_id
    _call_id = 0
    proc = start_nf_api()
    checks = []
    sock = None
    try:
        # ---- 1. 连接 MCP ----
        sock = socket.create_connection(("127.0.0.1", MCP_PORT), timeout=5)
        checks.append(("MCP TCP 连接", True))

        # ---- 2. initialize ----
        resp = mcp_call(sock, "initialize")
        ok = (resp is not None
              and "result" in resp
              and resp["result"].get("serverInfo", {}).get("name") == "novelforge-mcp")
        checks.append(("initialize 握手", ok, str(resp)[:200]))

        # ---- 3. tools/list ----
        resp = mcp_call(sock, "tools/list")
        tools = resp.get("result", {}).get("tools", []) if resp else []
        ok = len(tools) >= 5
        checks.append((f"tools/list 返回 {len(tools)} 个工具", ok))

        # 检查工具 schema
        has_schema = all(
            "inputSchema" in t and "description" in t
            for t in tools
        )
        checks.append(("所有工具都有 schema 和 description", has_schema))

        # 检查无内部 _http 泄露
        has_http_field = any("_http" in t for t in tools)
        checks.append(("tools/list 不包含内部 _http 字段", not has_http_field))

        # ---- 4. tools/call: nf_get_state ----
        resp = mcp_call(sock, "tools/call", {"name": "nf_get_state", "arguments": {}})
        ok = (resp is not None
              and "result" in resp
              and "content" in resp["result"])
        checks.append(("tools/call nf_get_state", ok, str(resp)[:150]))

        # ---- 5. tools/call: nf_get_costs ----
        resp = mcp_call(sock, "tools/call", {"name": "nf_get_costs", "arguments": {}})
        ok = (resp is not None
              and "result" in resp
              and "content" in resp["result"])
        checks.append(("tools/call nf_get_costs", ok))

        # ---- 6. tools/call: 未知工具 ----
        resp = mcp_call(sock, "tools/call", {"name": "nf_not_exist", "arguments": {}})
        ok = (resp is not None
              and "error" in resp)
        checks.append(("tools/call 未知工具返回 error", ok))



    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    # ---- 输出结果 ----
    print("\n=== MCP 自检结果 ===\n")
    passed = sum(1 for c in checks if c[1])
    failed = len(checks) - passed
    for name, ok, *extra in checks:
        status = "PASS" if ok else "FAIL"
        detail = f" — {extra[0]}" if extra else ""
        print(f"  [{status}] {name}{detail}")
    print(f"\n{passed}/{len(checks)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
