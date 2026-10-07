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
- 原交付物（外部 agent 产出的那份）**从未落到本机磁盘**：本仓 + git 全历史 +
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

## 后端自启动（2026-10-06）

客户端 spawn 本垫片时 8765/8766 往往都没起。垫片启动时会先探 `/health`，
不通就**代拉一次 nf_api**（它连带起 8766 MCP 线程），最多等 12 秒再进循环 ——
「MCP 配置写一次即可用」。守卫：

- `NF_BRIDGE_NO_AUTOSTART=1` 或 `--no-autostart`：完全关闭代拉
  （单元测试 / 握手自检用它隔离，绝不能把测试代拉到 8765 的真实服务）。
- 项目根解析与 `console/main/project-root.js` 同链：`NF_ROOT` →
  `%APPDATA%\绒花墨坊\project-root.json` → 默认（源码态=仓库根 / 打包态=%APPDATA% 工作区）；
  全都不合法就**不代拉**（宁可不启动，也不写错项目）。
- 子进程 DETACHED：垫片退出不影响已拉起的服务；超时不拦，-32002 兜底照旧。

## 纪律

- **stdout 只走协议**。日志一律 stderr —— 往 stdout 写一行非 JSON，MCP 客户端就废了。
- **惰性连接 + 断线重连**：客户端常驻，8766 可能后起；连接失败只影响当次请求（回 -32002）。
- **不解析、不改写 payload**：透传保真。唯一例外是请求在本地就无法送出时，按同一个 `id`
  回一条 JSON-RPC 错误 —— 客户端会把它当该请求的正常响应处理。
"""
import argparse
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

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


# ---- 后端自启动（2026-10-06 · 对齐方寸「配置一次即接入」）------------------
# 背景：客户端 spawn 垫片时 8765/8766 常常都没起（控制台没开）。旧口径只会回
# -32002 让用户去启动服务 —— 「MCP 配置写一次就能用」的目标下这不够。
# 现在垫片启动时若 8765 /health 不通，就**代拉一次 nf_api**（它会连带把 8766 的
# MCP 线程一起起掉），等它就绪再进 stdio 循环。
#
# 四条守卫（缺一条就会变成测试污染或僵尸重生）：
#   1. 只在 8765 真不通时才 spawn —— 健康检查天然挡住重复代拉。
#   2. `NF_BRIDGE_NO_AUTOSTART=1` 完全关闭（单元测试 / 握手自检的逃生门，
#      它们各起各的端口，绝不能被代拉到 8765 的真实服务）。
#   3. 最多等 AUTOSTART_WAIT_S 秒；超时**不拦**——照常进循环，
#      惰性连接 + -32002 兜底照旧（自启动是便利，不是新故障点）。
#   4. 子进程 **DETACHED**：垫片退出（客户端断开）不影响已拉起的服务 ——
#      服务归控制台/用户所有，垫片只「顺手拉起」，不管理生命周期。
AUTOSTART_WAIT_S = 12.0


def _health_ok(host=None, port=None, timeout=0.5):
    """探测 nf_api /health。只认 200 + 含 "ok" 的 JSON —— 端口被别的程序占着
    时不能误判成「后端就绪」。

    timeout 取 0.5s：本机防火墙对**关闭端口的 SYN 是静默丢弃**（不是 RST 快拒，
    实测 connect 吃满超时）—— 探测必须短超时，否则服务没起时每次白等，垫片
    启动被拖慢 1.5s+（test_mcp_stdio_bridge 的 G 组 0.8s 窗口曾因此假红）。"""
    host = host or os.environ.get("NF_API_HOST", "127.0.0.1")
    port = int(port or os.environ.get("NF_API_PORT", "8765"))
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = resp.read(512).decode("utf-8", "replace")
        conn.close()
        return resp.status == 200 and '"ok"' in body
    except OSError:
        return False


def _resolve_project_root(env=None, appdata=None, here=None, valid=None):
    """解析项目根 —— 与 console/main/project-root.js 同一条链的 Python 镜像：

        1) NF_ROOT 环境变量（显式覆盖；进程环境缺失时回读注册表用户环境）
        2) %APPDATA%\\绒花墨坊\\project-root.json（GUI 记忆的项目根）
        3) 默认：源码态 = 仓库根；打包态（脚本在 payload/ 下）= %APPDATA% 工作区

    返回 Path 或 None（None = 无法确定 → 不代拉，宁可不启动也不写错项目）。
    env / appdata / here / valid 全部可注入，纯函数可单测。"""
    env = os.environ if env is None else env
    appdata = env.get("APPDATA", "") if appdata is None else appdata
    here = Path(__file__).resolve() if here is None else Path(here)

    def looks_like_project(p):
        try:
            return bool(p) and (Path(p) / "config" / "system.yaml").is_file()
        except OSError:
            return False
    valid = looks_like_project if valid is None else valid

    root = env.get("NF_ROOT")
    if root and valid(root):
        return Path(root).resolve()
    if root:
        _log("NF_ROOT 指向的项目根无效（缺 config/system.yaml）: " + str(root))
    # 2026-10-07：进程环境拿不到时回读**注册表用户环境变量** —— 长驻宿主
    # （Hermes 网关）的环境可能过期/缺失 NF_ROOT，而新起的 GUI 按注册表拿到
    # 它，两边就解析出不同的数据根（GUI 与垫片双根分裂）。注册表与「新起
    # 进程」看到的一致，读它才能对齐。
    if not root and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as _k:
                root, _ = winreg.QueryValueEx(_k, "NF_ROOT")
            root = str(root or "").strip() or None
        except OSError:
            root = None
        if root and valid(root):
            _log("NF_ROOT（注册表用户环境）: " + str(root))
            return Path(root).resolve()
        root = None
    if appdata:
        cfg = Path(appdata) / "绒花墨坊" / "project-root.json"
        try:
            saved = json.loads(cfg.read_text(encoding="utf-8")).get("projectRoot") or ""
        except (OSError, ValueError):
            saved = ""
        if valid(saved):
            return Path(saved).resolve()
    default = here.parents[1] if here.parent.name == "scripts" else here
    if default.name == "payload":
        # 打包态：payload 是只读负载（数据根不在这）；数据根 = %APPDATA% 工作区
        if appdata:
            ws = Path(appdata) / "绒花墨坊" / "workspace"
            if valid(ws):
                return ws.resolve()
        return None
    return default.resolve() if valid(default) else None


def _spawn_nf_api(root, api_script=None, popen=None):
    """DETACHED 拉起 nf_api。返回 Popen 或 None（脚本不存在 / 起进程失败）。
    成功后**不管理**它：stdout/stderr 落临时日志，之后生死与垫片无关。"""
    api_script = (Path(__file__).resolve().parent / "nf_api.py") if api_script is None \
        else Path(api_script)
    if not api_script.is_file():
        _log("自启动跳过：找不到 nf_api.py（%s）" % api_script)
        return None
    env = dict(os.environ)
    env["NF_ROOT"] = str(root)          # 子进程的数据根必须显式钉住（打包态尤甚）
    try:
        log_path = Path(tempfile.gettempdir()) / "nf_api_bridge.log"
        logf = open(log_path, "ab")
    except OSError:
        logf = subprocess.DEVNULL
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.DEVNULL
                  if logf is subprocess.DEVNULL else logf,
                  env=env, cwd=str(root))
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) \
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    else:
        kwargs["start_new_session"] = True
    try:
        p = (popen or subprocess.Popen)([sys.executable, str(api_script)], **kwargs)
    except OSError as e:
        _log("自启动失败（拉起 nf_api 时出错）: %s" % e)
        return None
    finally:
        if logf is not subprocess.DEVNULL:
            try:
                logf.close()
            except OSError:
                pass
    _log("已代拉 nf_api（pid=%s, root=%s）→ 日志 %s"
         % (p.pid, root, Path(tempfile.gettempdir()) / "nf_api_bridge.log"))
    return p


def ensure_backend(wait_s=AUTOSTART_WAIT_S, health=None, resolve=None, spawn=None):
    """确保 8765 就绪：不通则代拉一次并等待。返回是否就绪。
    health/resolve/spawn 可注入（单测替换为假实现）。"""
    health = _health_ok if health is None else health
    resolve = _resolve_project_root if resolve is None else resolve
    spawn = _spawn_nf_api if spawn is None else spawn
    if os.environ.get("NF_BRIDGE_NO_AUTOSTART") == "1":
        # 逃生门 = 彻底关闭：**连探测都不做**。测试/握手自检各起各的端口，
        # 对 8765 的探测纯属白等（本机防火墙对关闭端口 SYN 静默丢弃 → 吃满超时）。
        _log("自启动已禁用（NF_BRIDGE_NO_AUTOSTART=1），不探测、不代拉")
        return False
    if health():
        return True
    root = resolve()
    if root is None:
        _log("自启动跳过：项目根无法确定（NF_ROOT / project-root.json / 默认都不合法）")
        return False
    if spawn(root) is None:
        return False
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if health():
            return True
        time.sleep(0.4)
    _log("自启动等待超时（%.0fs）：nf_api 仍未就绪 —— 照常进入 stdio 循环，"
         "由 -32002 兜底提示" % wait_s)
    return False


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
    ap.add_argument("--no-autostart", action="store_true",
                    help="不代拉后端（等价环境变量 NF_BRIDGE_NO_AUTOSTART=1）")
    a = ap.parse_args(argv)
    if a.no_autostart:
        os.environ["NF_BRIDGE_NO_AUTOSTART"] = "1"
    try:
        # 先确保 8765 就绪（不通才代拉，最多等 AUTOSTART_WAIT_S 秒），再进循环。
        # 放在 run() 前：客户端 spawn 后立刻发 initialize，这段等待发生在
        # 第一条请求之前 —— 挡住的就是首请求 -32002。
        ensure_backend()
        return run(a.tcp_host, a.tcp_port)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
