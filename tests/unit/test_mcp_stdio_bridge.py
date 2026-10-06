# -*- coding: utf-8 -*-
"""MCP stdio 垫片自检（2026-09-30）。

垫片是「标准 MCP 客户端（Hermes / Claude Code）→ 8766」的桥：
客户端只在本地 spawn 进程、用 stdin/stdout 说话，而 `nf_mcp.py` 是 TCP 换行分帧的
裸 JSON-RPC → 中间必须有这一层。它坏掉的表现是**客户端直接连不上**，
所以这里覆盖的是「桥本身」的行为，不是 nf_mcp 的工具实现。

本自检覆盖（全部离线，用本地 mock TCP 服务当 8766）：
A. 透传保真：有 id 的请求 → 原样送到 8766，响应原样回 stdout（id / 字段不丢）。
B. **通知（无 id）不能阻塞后续请求** —— 若实现按「请求-响应」严格配对，这里会死锁。
C. 非法 JSON → -32700，且**进程不退**（之后仍能正常服务）。
D. 空行不是错误，也不产生响应。
E. 8766 连不上 → -32002 + 带层号与启动命令的干净报错，**id 保真**。
F. **惰性重连**：8766 后起时，之后的请求必须成功（客户端常驻，服务不一定先起）。
G. stdout **只走协议**：读到的每一行都必须是合法 JSON（日志一律 stderr）。

用法：python tests/unit/test_mcp_stdio_bridge.py
"""
import io
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "scripts" / "nf_mcp_stdio_bridge.py"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class MockNfMcp:
    """最小 nf_mcp 替身：读一行 → 有 id 就回一行 echo。可后启动（测重连）。"""

    def __init__(self, port):
        self.port = port
        self.received = []
        self.running = True
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(5)
        self.srv.settimeout(0.3)
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while self.running:
            try:
                c, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _client(self, c):
        buf = b""
        c.settimeout(0.5)
        while self.running:
            try:
                chunk = c.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, _, buf = buf.partition(b"\n")
                if not line.strip():
                    continue
                self.received.append(line.decode("utf-8"))
                try:
                    req = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(req, dict) and "id" in req:
                    resp = {"jsonrpc": "2.0", "id": req["id"],
                            "result": {"echo": req.get("method")}}
                    try:
                        c.sendall(json.dumps(resp).encode("utf-8") + b"\n")
                    except OSError:
                        break
        try:
            c.close()
        except OSError:
            pass

    def stop(self):
        self.running = False
        try:
            self.srv.close()
        except OSError:
            pass


class Bridge:
    """垫子进程 + 行队列（读 stdout 的线程，避免测试被阻塞）。"""

    def __init__(self, port):
        # NF_BRIDGE_NO_AUTOSTART=1：垫片的后端自启动对本测试是纯污染 ——
        # 健康探测打真实 8765，若恰逢用户服务没开，会代拉起真的 nf_api。
        env = dict(os.environ, NF_BRIDGE_NO_AUTOSTART="1")
        self.p = subprocess.Popen(
            [sys.executable, str(BRIDGE), "--tcp-port", str(port)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env)
        self.lines = queue.Queue()
        self.stderr = []
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    def _pump(self):
        for raw in self.p.stdout:
            self.lines.put(raw.decode("utf-8", "replace").strip())

    def _pump_err(self):
        for raw in self.p.stderr:
            self.stderr.append(raw.decode("utf-8", "replace").strip())

    def send_raw(self, text):
        self.p.stdin.write((text + "\n").encode("utf-8"))
        self.p.stdin.flush()

    def send(self, obj):
        self.send_raw(json.dumps(obj))

    def recv(self, timeout=6.0):
        try:
            return self.lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self):
        out = []
        while True:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                return out

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.p.kill()


def test_transport_and_notifications():
    print("\n[A/B/D] 透传保真 · 通知不阻塞 · 空行不算错")
    port = free_port()
    srv = MockNfMcp(port)
    br = Bridge(port)
    try:
        br.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2024-11-05"}})
        line = br.recv()
        ok = False
        if line:
            try:
                resp = json.loads(line)
                ok = resp.get("id") == 1 and resp["result"]["echo"] == "initialize"
            except ValueError:
                pass
        check("initialize 透传到 8766 且响应原样回 stdout", ok, line)
        check("8766 侧收到的请求逐字保真（id/method/params 都在）",
              any('"protocolVersion": "2024-11-05"' in r and '"id": 1' in r
                  for r in srv.received), srv.received[:1])

        # 通知（无 id）：服务端不回。若垫片按请求-响应配对，下一条请求会被卡住。
        br.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        br.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        line2 = br.recv()
        ok2 = False
        if line2:
            try:
                resp2 = json.loads(line2)
                ok2 = resp2.get("id") == 2 and resp2["result"]["echo"] == "tools/list"
            except ValueError:
                pass
        check("通知（无 id）不去等响应，后续请求照常拿到结果", ok2, line2)
        check("通知确实被送到了 8766",
              any("notifications/initialized" in r for r in srv.received), srv.received[-2:])

        br.send_raw("")                      # 空行
        time.sleep(0.3)
        check("空行不产生任何响应（也不崩）", br.drain() == [], None)

        br.send_raw("{ this is not json")
        line3 = br.recv()
        ok3 = False
        if line3:
            try:
                resp3 = json.loads(line3)
                ok3 = resp3.get("error", {}).get("code") == -32700 and resp3.get("id") is None
            except ValueError:
                pass
        check("非法 JSON → -32700（id=null）", ok3, line3)

        br.send({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        line4 = br.recv()
        check("非法输入后进程不退，仍能正常服务",
              bool(line4) and json.loads(line4).get("id") == 3, line4)
    finally:
        br.close()
        srv.stop()


def test_unreachable_and_reconnect():
    print("\n[E/F/G] 8766 未起 → -32002 带层号 · 服务后起 → 能重连")
    port = free_port()          # 先不监听
    br = Bridge(port)
    srv = None
    try:
        br.send({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        line = br.recv()
        err = json.loads(line) if line else {}
        ed = err.get("error", {}) if isinstance(err, dict) else {}
        check("8766 不可达 → 错误码 -32002（沿用原交付物约定）",
              ed.get("code") == -32002, line)
        check("不可达错误 **id 保真**（客户端才会把它当该请求的响应）",
              err.get("id") == 7, err.get("id"))
        data = ed.get("data", {}) or {}
        check("报错标注了层（8766 传输层）",
              "8766" in str(data.get("layer", "")) + str(data.get("message", "")),
              data.get("layer"))
        check("报错带可执行的启动指引（而不是裸栈）",
              "nf_mcp.py" in str(data.get("hint", "")), str(data.get("hint"))[:100])
        check("提示里点明「两层分开看」（避免拿 A 层的错修 B 层）",
              "两层" in str(data.get("hint", "")), None)

        # 服务后起 → 惰性重连
        srv = MockNfMcp(port)
        time.sleep(0.4)
        br.send({"jsonrpc": "2.0", "id": 8, "method": "tools/list"})
        line2 = br.recv()
        ok = False
        if line2:
            try:
                ok = json.loads(line2).get("id") == 8
            except ValueError:
                ok = False
        check("8766 后起时自动重连并成功（客户端常驻，服务不一定先起）", ok, line2)
    finally:
        br.close()
        if srv:
            srv.stop()


def test_stdout_is_protocol_only():
    print("\n[G] stdout 只走协议：每一行都必须是合法 JSON")
    port = free_port()
    srv = MockNfMcp(port)
    br = Bridge(port)
    try:
        for i in range(1, 4):
            br.send({"jsonrpc": "2.0", "id": i, "method": "tools/list"})
        time.sleep(0.8)
        lines = br.drain()
        all_json = bool(lines)
        for ln in lines:
            try:
                json.loads(ln)
            except ValueError:
                all_json = False
        check("收到了 %d 行响应且全部是合法 JSON" % len(lines), all_json, lines[:2])
        check("日志走 stderr（stdout 没有被日志污染）",
              any("[stdio-bridge]" in s for s in br.stderr), br.stderr[:1])
    finally:
        br.close()
        srv.stop()


def main():
    print("=" * 62)
    print("  MCP stdio 垫片自检（离线）")
    print("=" * 62)
    test_transport_and_notifications()
    test_unreachable_and_reconnect()
    test_stdout_is_protocol_only()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
