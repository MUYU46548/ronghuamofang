# -*- coding: utf-8 -*-
"""MCP 服务发现：监听 UDP 广播，自动发现绒花墨坊 MCP 服务。

用法：
    from nf_mcp_discovery import discover_mcp
    info = discover_mcp(timeout=5)
    if info:
        print(f"发现 MCP 服务: {info['host']}:{info['port']}")
"""
import json
import socket


def discover_mcp(timeout=5, broadcast_port=8767):
    """监听 UDP 广播，发现绒花墨坊 MCP 服务。

    Args:
        timeout: 监听超时（秒）
        broadcast_port: 广播端口（默认 8767）

    Returns:
        dict: {"service": "novelforge-mcp", "host": "127.0.0.1", "port": 8766, ...}
        None: 未发现服务
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)
    try:
        sock.bind(("", broadcast_port))
        data, addr = sock.recvfrom(1024)
        msg = json.loads(data.decode("utf-8"))
        if msg.get("service") == "novelforge-mcp":
            msg["_discovered_from"] = addr[0]
            return msg
    except socket.timeout:
        pass
    except Exception as e:
        print(f"[nf_mcp_discovery] 发现失败: {e}")
    finally:
        sock.close()
    return None


if __name__ == "__main__":
    print("监听绒花墨坊 MCP 服务广播（5 秒）...")
    info = discover_mcp(timeout=5)
    if info:
        print(f"发现服务: {info['host']}:{info['port']}")
        print(f"HTTP API: {info['host']}:{info.get('http_port', 8765)}")
    else:
        print("未发现服务")
