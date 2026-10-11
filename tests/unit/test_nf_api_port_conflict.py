# -*- coding: utf-8 -*-
"""端口占用提示 `port_busy_hint` + 绑定兜底（产品池「端口占用无处理」收口，2026-10-11）。

## 为什么需要
`ThreadingHTTPServer` 绑不上端口时原来直接抛 OSError 裸堆栈（Windows 是
WinError 10048）：用户只看到一行英文异常，不知道谁占着端口、也不知道下一步。
更糟的是 GUI 代拉 / MCP 垫片看进程退出码，裸异常和「正常退出」一样难以归因。

## 判据
1. 探测不到服务 → 退化提示必须带端口号 + 查占用者的命令（可执行，不是空话）；
2. 探测到服务 → 必须报出它的 `project_root`（说清是**哪个项目**占着）+ 两条处置路径；
3. `/about` 坏掉时不能崩，也不能少掉「另一个绒花墨坊 API」这个判断；
4. 默认探测器（probe=None）打的是本机只读端点 —— 用真起一个临时服务来实测；
5. 静态：`main()` 里绑定必须包在 `except OSError` 内、调用 `port_busy_hint`、
   并以 `sys.exit` 明确失败（缺一条，提示就落不到用户眼前）。

全程离线（只碰 127.0.0.1 临时端口）、零 LLM、零费用。
用法：python tests/unit/test_nf_api_port_conflict.py
"""
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api  # noqa: E402

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

PASS, FAIL = [], []


class _QuietHandler(BaseHTTPRequestHandler):
    """最小应答器：/health 与 /about 都回 200 JSON（供绑定/探测分支实测）。"""

    def do_GET(self):
        body = b'{"ok": true, "project_root": "Q:/fixture/other_project"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


_QuietServer = ThreadingHTTPServer


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


# ---- 1. 探测不通：退化提示可执行 ----
def case_probe_down():
    print("\n【1】探测不通 → 带端口号 + 查占用者命令")

    def boom(url):
        raise OSError("WinError 10048")

    hint = nf_api.port_busy_hint("127.0.0.1", 8765, probe=boom)
    check("含端口号", ":8765" in hint, hint)
    check("含查占用者命令", "netstat" in hint and "findstr" in hint, hint)
    check("不含崩溃式空话", "其他进程占用" in hint, hint)


# ---- 2. 探测到服务：报项目根 + 两条处置 ----
def case_probe_alive():
    print("\n【2】端口上是另一个 nf_api → 报 project_root + 处置路径")
    about = json.dumps({"project_root": r"Q:\fixture\other_project"}, ensure_ascii=False)

    def probe(url):
        if url.endswith("/health"):
            return '{"ok": true}'
        return about

    hint = nf_api.port_busy_hint("127.0.0.1", 8765, probe=probe)
    check("报出占位者项目根", r"Q:\fixture\other_project" in hint, hint)
    check("给出 GUI 处置路径", "重启后端" in hint, hint)
    check("给出 CLI 换端口路径", "--port" in hint, hint)


# ---- 3. /about 坏掉：不崩、判断仍在 ----
def case_about_broken():
    print("\n【3】/health 通但 /about 返回坏 JSON → 不崩、仍判定为已有 API")

    def probe(url):
        if url.endswith("/health"):
            return '{"ok": true}'
        return "<html>not json</html>"

    hint = nf_api.port_busy_hint("127.0.0.1", 8765, probe=probe)
    check("仍判定为已有 API", "另一个绒花墨坊 API" in hint, hint)
    check("不带伪造的项目根", "project_root=" not in hint, hint)
    check("处置路径仍在", "--port" in hint and "重启后端" in hint, hint)


# ---- 4. 默认探测器（probe=None）真打本机端点 ----
def case_default_probe():
    print("\n【4】默认探测器（真起临时服务）")

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = ('{"ok": true}' if self.path == "/health"
                    else json.dumps({"project_root": r"D:\tmp\seeded"}))
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        hint = nf_api.port_busy_hint("127.0.0.1", port)   # probe=None 走真实探测
        check("默认探测读到 project_root", r"D:\tmp\seeded" in hint, hint)
    finally:
        srv.shutdown()
        srv.server_close()


# ---- 5. 静态：绑定必须走 bind_or_explain 并明确失败 ----
def case_static_wiring():
    print("\n【5】main() 的绑定兜底接线（静态）")
    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    main_src = src[src.index("def main():"):]
    check("绑定走 bind_or_explain", "bind_or_explain(args.host, args.port)" in main_src)
    check("失败即打印人话", "端口 %d 不可用" in main_src or "启动失败" in main_src)
    check("明确非零退出", "sys.exit(1)" in main_src)
    check("裸绑定已不存在",
          not [l for l in src.splitlines()
               if l == "    httpd = ThreadingHTTPServer((args.host, args.port), Handler)"],
          "仍存在未包裹的绑定语句")
    check("严格绑定子类存在", "allow_reuse_address = False" in src)


# ---- 6. bind_or_explain：空闲端口直接绑上 ----
def case_bind_free():
    print("\n【6】空闲端口 → 直接返回可用 server")
    srv = None
    try:
        srv = nf_api.bind_or_explain("127.0.0.1", 0, handler=_QuietHandler)
        check("返回对象而非提示串", not isinstance(srv, str), repr(srv)[:120])
        check("绑上了临时端口", bool(getattr(srv, "server_address", None)),
              getattr(srv, "server_address", None))
    finally:
        if srv is not None and not isinstance(srv, str):
            srv.server_close()


# ---- 7. bind_or_explain：端口真被占 → 返回提示而不是静默双绑（核心回归）----
def case_bind_busy():
    print("\n【7】端口已被另一个服务占用 → 返回提示串（不许静默双绑）")
    holder = _QuietServer(("127.0.0.1", 0), _QuietHandler)
    port = holder.server_address[1]
    check("占位者按生产同样方式持有端口（reuse=1）",
          holder.allow_reuse_address == 1, holder.allow_reuse_address)
    t = threading.Thread(target=holder.serve_forever, daemon=True)
    t.start()                                   # 真的应答 HTTP（更贴生产占用场景）
    try:
        res = nf_api.bind_or_explain("127.0.0.1", port, handler=_QuietHandler)
        check("返回的是人话提示而非 server", isinstance(res, str), repr(res)[:200])
        if isinstance(res, str):
            check("提示含处置路径", "--port" in res, res)
            check("报出占位者的项目根", "Q:/fixture/other_project" in res, res)
        else:
            check("返回的是人话提示而非 server", False, repr(res)[:160])
    finally:
        holder.shutdown()
        holder.server_close()


# ---- 8. bind_or_explain：TIME_WAIT 残留（无监听者）→ 放宽重绑一次 ----
def case_bind_timewait():
    print("\n【8】严格绑定失败但**无监听者**（TIME_WAIT 残留）→ 放宽重绑成功")

    class _ReuseOnly:
        """模拟：allow_reuse_address=False 必失败，True 才成功。"""
        allow_reuse_address = True

        def __init__(self, addr, handler):
            if not self.allow_reuse_address:
                raise OSError(10048, "Only one usage of each socket address")
            self.server_address = addr

        def server_close(self):
            pass

    res = nf_api.bind_or_explain("127.0.0.1", 8799, server_cls=_ReuseOnly,
                                 handler=_QuietHandler, listening=lambda: False)
    check("放宽重绑后拿到 server", not isinstance(res, str), repr(res)[:160])


# ---- 9. bind_or_explain：真有人听但不是 HTTP → 也给提示 ----
def case_bind_tcp_holder():
    print("\n【9】有 TCP 监听者但非本服务 → 返回提示")

    class _AlwaysBusy:
        allow_reuse_address = True

        def __init__(self, addr, handler):
            raise OSError(10048, "Only one usage of each socket address")

    res = nf_api.bind_or_explain("127.0.0.1", 8798, server_cls=_AlwaysBusy,
                                 handler=_QuietHandler, listening=lambda: True)
    check("返回人话提示", isinstance(res, str), repr(res)[:200])
    if isinstance(res, str):
        check("提示含查占用者命令", "netstat" in res or "重启后端" in res, res)


for fn in (case_probe_down, case_probe_alive, case_about_broken,
           case_default_probe, case_static_wiring,
           case_bind_free, case_bind_busy, case_bind_timewait, case_bind_tcp_holder):
    fn()

print("\n==== %d PASS, %d FAIL %s" % (len(PASS), len(FAIL), FAIL))
sys.exit(1 if FAIL else 0)
