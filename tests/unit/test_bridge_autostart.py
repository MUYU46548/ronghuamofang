# -*- coding: utf-8 -*-
"""垫片后端自启动（ensure_backend）单测 —— 2026-10-06 对齐方寸「配置一次即接入」。

覆盖（全离线，注入假实现，不起任何真实服务）：
A. 项目根解析链：NF_ROOT → project-root.json → 默认（源码态/打包态）→ 全废返 None。
   宁可不启动也不写错项目 —— 打包态误把 payload 当数据根会往安装目录写 data/。
B. ensure_backend 守卫：
   B1 NF_BRIDGE_NO_AUTOSTART=1 → 绝不 spawn（测试/握手自检的隔离门）。
   B2 8765 已健康 → 不 spawn、不解析根。
   B3 根解析不出 → 不 spawn（不猜项目）。
   B4 拉起失败（spawn 返回 None）→ 立即放弃，不空等 12s。
   B5 拉起后健康 → 返回 True，且 spawn 恰好一次（不重复代拉）。
C. _health_ok 真 socket：对真 /health 返回 True；对关端口返回 False。
D. _spawn_nf_api：脚本缺失 → None；popen 注入捕获命令/env（NF_ROOT 钉死 + cwd=根）。

用法：python tests/unit/test_bridge_autostart.py
"""
import io
import json
import os
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_mcp_stdio_bridge as br  # noqa: E402  （scripts/ 在 path 上）

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:240]) if detail else ""))


def make_project(base):
    """建一个最小合法项目根（config/system.yaml 存在）。"""
    p = Path(base)
    (p / "config").mkdir(parents=True, exist_ok=True)
    (p / "config" / "system.yaml").write_text("engine: hermes\n", encoding="utf-8")
    return p


def test_resolve_chain():
    print("\n[A] 项目根解析链（与 project-root.js 同链的 Python 镜像）")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        # A1 NF_ROOT 合法 → 直接用（显式覆盖最高优先）
        proj = make_project(td / "proj1")
        got = br._resolve_project_root(env={"NF_ROOT": str(proj)}, appdata=str(td))
        check("A1 NF_ROOT 合法 → 用它", got == proj.resolve(), got)

        # A2 NF_ROOT 非法 → 落到 project-root.json（记忆配置）
        saved = make_project(td / "saved")
        appdata = td / "appdata"
        (appdata / "绒花墨坊").mkdir(parents=True, exist_ok=True)
        (appdata / "绒花墨坊" / "project-root.json").write_text(
            json.dumps({"projectRoot": str(saved)}), encoding="utf-8")
        got = br._resolve_project_root(env={"NF_ROOT": str(td / "不存在")},
                                       appdata=str(appdata))
        check("A2 NF_ROOT 非法 → 回落到 project-root.json", got == saved.resolve(), got)

        # A3 全都没有 → 源码态默认（here 在 <proj>/scripts/ 下 → 仓库根）
        proj3 = make_project(td / "proj3")
        here = proj3 / "scripts" / "nf_mcp_stdio_bridge.py"
        got = br._resolve_project_root(env={}, appdata="", here=here)
        check("A3 源码态默认 = 仓库根（scripts 的上上级）", got == proj3.resolve(), got)

        # A4 打包态（here 在 payload/scripts/ 下）→ %APPDATA% 工作区，绝不写 payload
        ws = make_project(td / "appdata2" / "绒花墨坊" / "workspace")
        payload = td / "install" / "resources" / "payload"
        (payload / "scripts").mkdir(parents=True, exist_ok=True)
        got = br._resolve_project_root(
            env={}, appdata=str(td / "appdata2"),
            here=payload / "scripts" / "nf_mcp_stdio_bridge.py")
        check("A4 打包态默认 = %APPDATA% 工作区（不是 payload）", got == ws.resolve(), got)

        # A5 打包态但工作区不存在 → None（不猜、不写安装目录）
        got = br._resolve_project_root(
            env={}, appdata=str(td / "空appdata"),
            here=payload / "scripts" / "nf_mcp_stdio_bridge.py")
        check("A5 打包态工作区缺失 → None（宁可不启动）", got is None, got)

        # A6 什么都没有 → None
        got = br._resolve_project_root(env={}, appdata="", here=td / "nowhere.py")
        check("A6 全废 → None", got is None, got)


class FakeProc:
    pid = 424242


def test_ensure_backend_guards():
    print("\n[B] ensure_backend 守卫（注入假实现，零真实进程）")
    calls = {"spawn": 0, "resolve": 0}

    def fake_spawn(root):
        calls["spawn"] += 1
        return FakeProc()

    def fake_resolve():
        calls["resolve"] += 1
        return Path("C:/fake-project")

    # B1 逃生门：无论健康与否都不 spawn
    calls.update(spawn=0, resolve=0)
    os.environ["NF_BRIDGE_NO_AUTOSTART"] = "1"
    try:
        ok = br.ensure_backend(wait_s=0.2, health=lambda: False,
                               resolve=fake_resolve, spawn=fake_spawn)
        check("B1 NO_AUTOSTART=1 → 不 spawn、不解析根（返回 False）",
              calls["spawn"] == 0 and calls["resolve"] == 0 and ok is False, calls)
    finally:
        os.environ.pop("NF_BRIDGE_NO_AUTOSTART", None)

    # B2 已健康 → 不 spawn
    calls.update(spawn=0, resolve=0)
    ok = br.ensure_backend(wait_s=0.2, health=lambda: True,
                           resolve=fake_resolve, spawn=fake_spawn)
    check("B2 8765 已健康 → 不代拉（返回 True）",
          ok is True and calls["spawn"] == 0 and calls["resolve"] == 0, calls)

    # B3 根解析不出 → 不 spawn
    calls.update(spawn=0, resolve=0)
    ok = br.ensure_backend(wait_s=0.2, health=lambda: False,
                           resolve=lambda: (calls.__setitem__("resolve", 1) or None),
                           spawn=fake_spawn)
    check("B3 项目根解析不出 → 不代拉（返回 False）",
          ok is False and calls["spawn"] == 0 and calls["resolve"] == 1, calls)

    # B4 spawn 失败 → 立即放弃（不进 12s 等待）
    calls.update(spawn=0, resolve=0)
    ok = br.ensure_backend(wait_s=5, health=lambda: False, resolve=fake_resolve,
                           spawn=lambda r: (calls.__setitem__("spawn", 1) or None))
    check("B4 拉起失败 → 立即返回 False（不空等）",
          ok is False and calls["spawn"] == 1, calls)

    # B5 拉起后转健康 → True，且 spawn 恰好一次
    calls.update(spawn=0, resolve=0)
    seq = {"n": 0}

    def health_seq():
        seq["n"] += 1
        return seq["n"] >= 3          # 前两拍没好，第三拍就绪
    ok = br.ensure_backend(wait_s=5, health=health_seq,
                           resolve=fake_resolve, spawn=fake_spawn)
    check("B5 代拉后等就绪 → True，spawn 恰好 1 次",
          ok is True and calls["spawn"] == 1, (ok, calls))


def test_health_ok_real_socket():
    print("\n[C] _health_ok 真 socket 行为")
    # 关端口 → False
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()
    check("C1 关端口 → False", br._health_ok("127.0.0.1", dead_port) is False)

    # 真起一个 /health → True
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"ok": true, "current_job": null}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        check("C2 真 /health 200+ok → True", br._health_ok("127.0.0.1", port) is True)
        # 端口被占但不是 nf_api（回 404）→ 不能误判
        class H404(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a):
                pass
        srv2 = HTTPServer(("127.0.0.1", 0), H404)
        port2 = srv2.server_address[1]
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        try:
            check("C3 端口被非 nf_api 程序占用 → False（不误判就绪）",
                  br._health_ok("127.0.0.1", port2) is False)
        finally:
            srv2.shutdown()
    finally:
        srv.shutdown()


def test_spawn_guards():
    print("\n[D] _spawn_nf_api 守卫")
    with tempfile.TemporaryDirectory() as td:
        root = make_project(td)
        # D1 脚本不存在 → None（不猜路径）
        got = br._spawn_nf_api(root, api_script=Path(td) / "nope.py")
        check("D1 nf_api.py 不存在 → None", got is None, got)

        # D2 注入 popen 捕获：命令 = python + 脚本；env 钉 NF_ROOT；cwd = 根
        captured = {}

        def fake_popen(cmd, **kw):
            captured["cmd"] = cmd
            captured["kw"] = kw
            return FakeProc()
        script = make_project(td + "_s") / "scripts" / "nf_api.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("# stub\n", encoding="utf-8")
        got = br._spawn_nf_api(root, api_script=script, popen=fake_popen)
        env = captured.get("kw", {}).get("env", {})
        check("D2 拉起命令正确（python + nf_api.py）",
              got is not None and str(script) in [str(x) for x in captured["cmd"]],
              captured.get("cmd"))
        check("D2b 子进程 env 钉死 NF_ROOT=根（防写错项目）",
              env.get("NF_ROOT") == str(root), env.get("NF_ROOT"))
        check("D2c cwd=项目根", captured.get("kw", {}).get("cwd") == str(root),
              captured.get("kw", {}).get("cwd"))


def main():
    print("=" * 62)
    print("  垫片后端自启动单测（离线，注入假实现）")
    print("=" * 62)
    test_resolve_chain()
    test_ensure_backend_guards()
    test_health_ok_real_socket()
    test_spawn_guards()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
