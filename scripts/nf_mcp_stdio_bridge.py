# -*- coding: utf-8 -*-
"""MCP stdio 垫片：把标准 MCP 客户端（Hermes / Claude Code / Cline …）接到 nf_mcp 的 8766。

## 它解决什么问题

`scripts/nf_mcp.py` 跑在 **TCP 8766** 上，用的是「TCP + 换行分帧的裸 JSON-RPC」——
**不是** MCP 标准的 stdio 传输。于是标准 MCP 客户端**直连不上 8766**：
它们只会在本地 **spawn 一个进程**，通过 stdin/stdout 说话。

本垫片就是那个进程：**stdio ↔ TCP 8766 的双向透传**。两侧的分帧格式恰好一致
（都是换行分隔的 JSON-RPC 2.0，一行一条消息），所以核心是逐行搬运，
但「搬不动的时候怎么说话」才是它真正的价值 —— 见下。

## 交付 / 收编来源

- 需求出自 `shared/report-20260929/开工执行单-v2.2.md` 的 **⑤前置步0「垫片收编进仓库」**
  与 **⑤前置步1「stdio 垫片真机验证」**（`shared/nf_mcp_stdio_bridge.py` → `scripts/`）。
- 原交付物（哨兵产出的那份）**从未落到本机磁盘**：本仓 + `E:/CODE` + git 全历史 +
  Hermes 技能库 + `Downloads/shared/` 全部零命中；执行单自己也写了
  「**按今天产出蒸发教训，落盘必须落库**」。故本文件是**按需求重写**，不是收编原件。
- 沿用原交付物已实测的对外约定：**拒连时返回 `-32002` + 启动指引**
  （执行单 件6 记「拒连层垫片已实现（-32002+启动指引，哨兵桥 L90-92 实测）」）。

## 三层要分清（报错文案都带层号）

    MCP 客户端 (Hermes / Claude Code …)
        │  stdio：换行分隔 JSON-RPC 2.0
        ▼
    本垫片 nf_mcp_stdio_bridge.py        ← 「stdio 垫片」层
        │  TCP：换行分隔 JSON-RPC 2.0
        ▼
    nf_mcp.py  (tcp://127.0.0.1:8766)    ← 「8766 MCP 传输层」
        │  HTTP
        ▼
    nf_api.py  (http://127.0.0.1:8765)   ← 「8765 服务本体」

任何一层没起来，你都会拿到**明确写着是哪一层**的报错，而不是挂死或裸栈。
**别拿一层的错去修另一层的服务。**

## 用法

Hermes / Claude Code 的 `mcpServers` 配置（把 `<项目根>` 换成你机器上的实际路径；
Windows 路径里的反斜杠要写成 `\\`）::

    {
      "mcpServers": {
        "novelforge": {
          "command": "<项目根>/.venv/Scripts/python.exe",
          "args": ["<项目根>/scripts/nf_mcp_stdio_bridge.py"]
        }
      }
    }

要点：`command` 指向**项目 venv 的 python**（不是系统 python），`args` 指向本文件。
两端都不需要额外参数 —— 垫片默认连 `127.0.0.1:8766`（`NF_MCP_HOST` / `NF_MCP_PORT` 可覆盖）。

命令行（手动联调）::

    .venv/Scripts/python.exe scripts/nf_mcp_stdio_bridge.py [--tcp-host 127.0.0.1] [--tcp-port 8766]

## 纪律

- **stdout 只走协议**。日志一律 stderr —— 往 stdout 写一行非 JSON，MCP 客户端就废了。
- **惰性连接 + 断线重连**：客户端常驻，8766 可能后起；连接失败只影响当次请求（回 -32002）。
- **不解析、不改写 payload**：透传保真。唯一例外是请求在本地就无法送出时，按同一个 `id`
  回一条 JSON-RPC 错误 —— 客户端会把它当该请求的正常响应处理。
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

# 拒连错误码：沿用原交付物约定（JSON-RPC server error 区间），不要随意更改 ——
# 下游若按码分流，改码等于静默破协议。
CODE_UNREACHABLE = -32002
CODE_PARSE = -32700

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
CONNECT_TIMEOUT = 2.0


def _log(msg):
    """日志只写 stderr（stdout 是协议通道）。"""
    sys.stderr.write("[stdio-bridge] " + msg + "\n")
    sys.stderr.flush()


def _write_stdout(payload):
    """向 MCP 客户端回一行。必须 flush —— 否则客户端会一直等。"""
    sys.stdout.buffer.write(payload + b"\n")
    sys.stdout.buffer.flush()


def _unreachable(rid, reason, host, port):
    """8766 连不上时的干净报错（带层号 + 可执行启动命令）。

    注意与 nf_mcp 的分层口径**对齐**：那边报的是「8766→8765」，
    这边报的是「垫片→8766」。用户在任一层都能一眼看出该去启动谁。
    """
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {
            "code": CODE_UNREACHABLE,
            "message": "无法连接 8766 MCP 传输层（nf_mcp）",
            "data": {
                "ok": False,
                "layer": "stdio 垫片 → 8766 MCP 传输层",
                "error": "连接 " + host + ":" + str(port) + " 失败: " + str(reason),
                "hint": ("stdio 垫片本身是好的，缺的是 **8766（nf_mcp 传输层）**。"
                         "它通常由绒花墨坊控制台一同拉起；单独跑用："
                         "python scripts/nf_mcp.py（NF_MCP_PORT 可换端口）。"
                         "若 8766 已起却报 `8765 服务本体` 字样，那是再下一层的事 —— "
                         "两层分开看，别拿一层的错去修另一层的服务。"),
            },
        },
    }


def _parse_error(rid, detail=""):
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": CODE_PARSE, "message": "Parse error" + (": " + detail if detail else "")}}


class TcpConn:
    """到 8766 的惰性连接：写与读各一个线程共用，断了自己重连。"""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._sock = None
        self._inbuf = b""              # 分帧缓冲：一次 recv 可能带回多行
        self.last_error = None

    def current(self):
        return self._sock

    def _connect(self):
        s = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        s.settimeout(None)          # 连上后阻塞读，交给读线程
        return s

    def drop(self):
        with self._lock:
            s, self._sock = self._sock, None
            self._inbuf = b""
        if s is not None:
            try:
                s.close()
            except OSError:
                pass

    def send(self, data):
        """送一行。失败返回 False，并把原因记到 last_error。

        连接策略：**没连接就建；发失败就丢掉连接，下次 send 自然重连**。

        刻意**不做**「发送失败后立即重连再试一次」：socket 半开时 `sendall` 往往
        **成功**（数据进了内核缓冲，随后才收到 RST），那个分支几乎不会被触发 ——
        留着只会让人误以为有保护。反向验证时实测：把那段重试改坏，自检**照样全绿**
        （打不出红 = 断言测不到它）。真断了的话客户端会重试，这边下次 send 会重连。
        """
        with self._lock:
            if self._sock is None:
                try:
                    self._sock = self._connect()
                except OSError as e:
                    self.last_error = e
                    return False
            try:
                self._sock.sendall(data)
                return True
            except OSError as e:
                self.last_error = e
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
                return False

    def readline(self):
        """从当前连接读一行；未连接返回 (False, None)；EOF/错误返回 (False, err)。

        分帧缓冲**必须留在实例上**：一次 recv 常带回多行（服务端连续应答时尤其如此），
        就地拆行会把半行也当成整行发出去 —— 那是**静默的错误帧**，客户端只会看到解析失败。
        """
        s = self.current()
        if s is None:
            return False, None
        while b"\n" not in self._inbuf:
            try:
                chunk = s.recv(4096)
            except OSError as e:
                return False, e
            if not chunk:
                return False, EOFError("连接被对端关闭")
            self._inbuf += chunk
        line, _, self._inbuf = self._inbuf.partition(b"\n")
        return True, line


def _tcp_to_stdout(conn, stop):
    """读线程：8766 → stdout（纯透传，不解析）。"""
    while not stop.is_set():
        if conn.current() is None:
            time.sleep(0.05)
            continue
        ok, line = conn.readline()
        if not ok:
            if line is not None:
                conn.drop()             # 断了就丢连接，等下次请求重连
            time.sleep(0.02)
            continue
        if line.strip():
            _write_stdout(line.strip())


def run(host, port):
    conn = TcpConn(host, port)
    stop = threading.Event()
    threading.Thread(target=_tcp_to_stdout, args=(conn, stop), daemon=True).start()

    _log("stdio 垫片已就绪 → tcp://%s:%d（stdout 只走协议，日志在本行）" % (host, port))

    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue                        # 空行：忽略（不是错误）
        try:
            req = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            _write_stdout(json.dumps(_parse_error(None, str(e)[:120])).encode("utf-8"))
            continue
        rid = req.get("id") if isinstance(req, dict) else None

        if not conn.send(line + b"\n"):
            err = _unreachable(rid, conn.last_error, host, port)
            _write_stdout(json.dumps(err, ensure_ascii=False).encode("utf-8"))

    # stdin EOF：客户端走了
    stop.set()
    conn.drop()
    _log("stdin 已关闭，垫片退出")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="MCP stdio ↔ TCP 8766 桥")
    ap.add_argument("--tcp-host", default=os.environ.get("NF_MCP_HOST", DEFAULT_HOST))
    ap.add_argument("--tcp-port", type=int,
                    default=int(os.environ.get("NF_MCP_PORT", DEFAULT_PORT)))
    a = ap.parse_args(argv)
    try:
        return run(a.tcp_host, a.tcp_port)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
