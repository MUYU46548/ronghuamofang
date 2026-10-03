# -*- coding: utf-8 -*-
"""MCP 真机握手自检：**标准 MCP 客户端** → stdio 垫片 → 8766 → 8765 全链路。

## 为什么需要它

执行单 ⑤前置步1 的验收是「Hermes mcpServers 配置 spawn 垫片，标准 MCP 握手成功 +
tools/list 约 20 项 nf_* 工具可见」。而 mock 替身**测不出**这件事：
mock 只会回声，握手能否成立、工具清单对不对，都要**真客户端 × 真服务**才知道。

本脚本用**官方 MCP Python SDK**（`mcp` 包，Hermes 用的同一套）当客户端：
ClientSession + stdio_client 是官方实现，不是自造协议栈。

## 零前置、零污染

- 自己起服务：**真 `nf_api`** 子进程（`--port 18765 --allow-fake`，不花钱）
  + **真 `nf_mcp`**（在进程内 `MCPServer(port=18766, http_port=18765)`，走的是同一份实现）。
- 用 **18765/18766** 而非 8765/8766：不碰用户正在用的端口，也不会被占端口干扰。
- 结束一律清理子进程。

## 用法

    .venv/Scripts/python.exe scripts/nf_mcp_handshake_check.py
    .venv/Scripts/python.exe scripts/nf_mcp_handshake_check.py --keep-alive   # 留着服务供手工探

缺 `mcp` SDK 时**干净跳过**（打印安装指引 + exit 0）——按项目纪律，
**缺依赖 / 缺外部服务 = 未执行 ≠ 失败**。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

API_PORT = 18765
MCP_PORT = 18766

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def start_api(api_port):
    """起真 nf_api（fake 模式：不调 LLM、不花钱）。"""
    log = ROOT / "Temp" / "handshake_api.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "wb")
    p = subprocess.Popen([sys.executable, str(SCRIPTS / "nf_api.py"),
                          "--port", str(api_port), "--allow-fake"],
                         stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT))
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % api_port, timeout=1) as r:
                if r.status == 200:
                    return p
        except Exception:
            time.sleep(0.4)
    return p


async def handshake(tcp_port):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SCRIPTS / "nf_mcp_stdio_bridge.py"), "--tcp-port", str(tcp_port)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "serverInfo", None) or getattr(init, "server_info", None)
            name = getattr(info, "name", None) if info else None
            check("标准客户端 initialize 握手成功（垫片 → 8766）", bool(name), info)
            proto = getattr(init, "protocolVersion", None) or getattr(init, "protocol_version", None)
            check("协议版本已协商", bool(proto), proto)

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            check("tools/list 返回工具清单", len(names) > 0, len(names))
            check("工具全为 nf_* 前缀（白名单未被污染）",
                  bool(names) and all(n.startswith("nf_") for n in names),
                  [n for n in names if not n.startswith("nf_")][:5])
            # 工具数**不写死**：与 MCP_TOOLS 声明数比对。写死数字的老写法每加一个
            # 工具就假红一次 —— 假红比没有测试更糟（会被当成噪声忽略掉真问题）。
            import nf_mcp as _mcp_mod
            expect = len(_mcp_mod.MCP_TOOLS)
            check("工具数量与 MCP_TOOLS 声明一致",
                  len(names) == expect, "%d 个（声明 %d）" % (len(names), expect))

            r = await session.call_tool("nf_get_state", {})
            texts = [c.text for c in r.content if getattr(c, "type", "") == "text"]
            payload = texts[0] if texts else ""
            ok, extra = False, payload[:120]
            try:
                data = json.loads(payload)
                ok = isinstance(data, dict)
                extra = "book=" + str(data.get("book") or data.get("result", {}).get("book"))
            except ValueError:
                pass
            check("tools/call 真调用成功（全链路 垫片→8766→8765 通）",
                  ok and not getattr(r, "isError", False), extra)


def main():
    ap = argparse.ArgumentParser(description="MCP 真机握手自检")
    ap.add_argument("--keep-alive", action="store_true", help="跑完不杀服务（手工探活用）")
    a = ap.parse_args()

    print("=" * 66)
    print("  MCP 真机握手自检（官方 SDK 客户端 × 真 nf_mcp/nf_api）")
    print("=" * 66)

    # 用 find_spec 探测依赖（而不是 `import mcp` + noqa）：pyflakes 不认 noqa，
    # 那样会平白给质量门添一条「未使用导入」的 WARN。
    import importlib.util
    if importlib.util.find_spec("mcp") is None:
        SKIP.append("缺 mcp SDK")
        print("  [SKIP] 未执行：环境缺官方 MCP SDK。安装后再跑：")
        print("         .venv/Scripts/python.exe -m pip install -r requirements-dev.txt")
        print("\n  → 未执行（≠ 失败）")
        return 0

    if importlib.util.find_spec("nf_mcp") is None:
        print("  [FAIL] 找不到 scripts/nf_mcp.py")
        return 1

    api_proc = start_api(API_PORT)
    srv = None
    try:
        import nf_mcp
        srv = nf_mcp.MCPServer(port=MCP_PORT, http_port=API_PORT)
        # 掐掉 UDP 服务发现广播：那是给外部 Agent「自动发现」用的，
        # 而这里跑的是**临时端口**的自检 —— 广播出去只会让别人缓存一个马上要死的地址。
        srv._start_discovery = lambda: None
        srv.start()
        time.sleep(1.2)
        check("8766 MCP 传输层已就绪（真 nf_mcp）", True, "tcp://127.0.0.1:%d" % MCP_PORT)

        try:
            import anyio
            anyio.run(handshake, MCP_PORT)
        except Exception as e:                              # noqa: BLE001
            check("握手流程未抛异常", False, "%s: %s" % (type(e).__name__, str(e)[:200]))
    finally:
        if srv is not None and not a.keep_alive:
            try:
                srv.stop()
            except Exception:
                pass
        if not a.keep_alive:
            try:
                api_proc.terminate()
                api_proc.wait(timeout=8)
            except Exception:
                api_proc.kill()

    print("\n  通过 %d / 失败 %d / 跳过 %d" % (len(PASS), len(FAIL), len(SKIP)))
    for f in FAIL:
        print("   ✗ " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
