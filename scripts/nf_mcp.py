# -*- coding: utf-8 -*-
"""MCP (Model Context Protocol) server for NovelForge.

在 **TCP 8766** 上跑一个 JSON-RPC 2.0 服务，把一组精选的 NovelForge 操作暴露成
MCP 工具（允许清单见 ``MCP_TOOLS``）。业务逻辑一律不在本层实现：它只是薄适配器，
把 ``tools/call`` 映射到 8765（nf_api）的 HTTP 端点转发。

**传输真相（2026-09-29 修正虚标）**：这里是「TCP + 换行分帧的裸 JSON-RPC」，
**不是** MCP 标准的 stdio / Streamable HTTP 传输 —— 标准 MCP 客户端
（Claude Code / Cline / Hermes 等）**无法直连 8766**，中间必须有 stdio 垫片，
把 stdio 上的标准 MCP 帧与 8766 的 TCP 换行帧互转。
原注释写「compatible with any MCP client that supports TCP/HTTP transport」属**虚标**，
已改正 —— 「文档比实现好听」正是本项目最忌讳的那类静默失真。

**垫片已就位**：`scripts/nf_mcp_stdio_bridge.py`（stdio ↔ 8766，逐行透传；
`mcpServers` 配置示例与三层分层口径见该文件头）。它连不上 8766 时返回
**-32002 + 启动指引**，与本文件 8765 不可达时的分层文案配套。

**两层要分清（件6）**：
  · 8766 = 本文件的 MCP 传输层（MCP 客户端 / 垫片连这里）
  · 8765 = 服务本体 nf_api（本文件再往下转发到这里）
两层任一不可用，报错文案都会**标注层号**，别拿 A 层的错去修 B 层的服务。

**启动指引**：服务本体与 MCP 层通常由控制台一起拉起。
需要单独跑本层时用 ``python scripts/nf_mcp.py``（``NF_MCP_PORT`` 可换端口），
但它只是代理 —— 8765 没起时任何 ``tools/call`` 都会返回**带层号与启动命令**的干净报错
（而不是裸栈或挂死）。

Architecture::

    MCP Client (Claude Code / Cursor / ...)
        │  JSON-RPC 2.0 over TCP :8766
        ▼
    nf_mcp.py  (protocol adapter)
        │  HTTP loopback to 127.0.0.1:8765
        ▼
    nf_api.py  (existing HTTP server, domain handlers)

The MCP layer is a thin adapter: it never implements business logic itself.
It maps MCP ``tools/call`` → HTTP endpoint, forwards the request, and
returns the response. This guarantees that MCP agents see exactly the same
behavior as the GUI.

Agent allow-list (Phase 2):
    Only the tools listed in ``MCP_TOOLS`` are exposed. Any new HTTP endpoint
    must be explicitly added here before an agent can call it. This replaces
    the old deny-list model where every new endpoint was automatically exposed.

Phase 4 (multi-agent dispatch):
    ``nf_dispatch_task`` / ``nf_get_agent_run`` are implemented **in-process**
    (``_local`` marker) by ``nf_agent_dispatch.py`` — they never touch the
    HTTP loopback, so no nf_api endpoint is needed. Dispatch failures are
    recorded as ``error`` runs in ``data/state/agent_runs.jsonl`` and returned
    as ``ok:false``; they never raise or block the caller.
"""

import json
import os
import socket
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Tool definitions for MCP ``tools/list``
# Each tool has a name, description, and JSON Schema for its input.
# The ``_http`` field maps to the endpoint called via loopback.
# ---------------------------------------------------------------------------

MCP_TOOLS = [
    {
        "name": "nf_get_state",
        "description": "获取绒花墨坊全景状态：当前阶段、审批门、预算消耗、产物统计",
        "inputSchema": {"type": "object", "properties": {}},
        "_http": ("GET", "/state", None),
    },
    {
        "name": "nf_get_costs",
        "description": "获取成本摘要：按阶段/模型聚合的 token 消耗与费用",
        "inputSchema": {"type": "object", "properties": {}},
        "_http": ("GET", "/costs/summary", None),
    },
    {
        "name": "nf_get_outline",
        "description": "获取结构化大纲视图（acts/nodes/plan/体检评分）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "review": {
                    "type": "boolean",
                    "description": "是否运行确定性体检（默认 true；切视图时可 false）",
                },
            },
        },
        "_http": ("GET", "/outline/structure", None),
    },
    {
        "name": "nf_get_chapters_quality",
        "description": "获取逐章质量评分（quality 0-10、完整性、字数等）",
        "inputSchema": {"type": "object", "properties": {}},
        "_http": ("GET", "/chapters/quality", None),
    },
    {
        "name": "nf_run_stage",
        # ⚠️ 只能是 1-7：后端 `/stage/{n}/run` 显式校验 `1 <= n <= 7`，
        # 传 8 必被 400 拒绝。阶段 8（Markdown 分卷导出）的入口是
        # `POST /export/markdown`（工具 `nf_export_markdown`），不走 run_stage。
        # 契约写错会把 Agent 引到必然失败的调用上。
        "description": "运行流水线的指定阶段（1-7）。返回 job_id，后续可查询 job 状态。"
                       "阶段 8（Markdown 分卷导出）请用 nf_export_markdown",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 7,
                    "description": "阶段号（1=素材归并, 2=整体大纲, 3=逐章大纲, 4=写作, 5=检查, 6=润色, 7=Word）",
                },
                "from_stage": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 7,
                    "description": "从哪阶段开始（默认=stage）",
                },
                "stream": {
                    "type": "boolean",
                    "description": "是否流式输出 token（默认 false，长任务建议 true）",
                },
            },
            "required": ["stage"],
        },
        "_http": ("POST", "/stage/{stage}/run", None),
    },
    {
        "name": "nf_get_stream_status",
        "description": "查询流式 job 的状态和进度（对应 GET /jobs/{job_id}）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "流式 job 的 ID（由 nf_run_stage stream:true 返回）",
                },
            },
            "required": ["job_id"],
        },
        "_http": ("GET", "/jobs/{job_id}", None),
    },
    {
        "name": "nf_save_outline",
        "description": "保存整份大纲（格式校验 + 自动备份旧版到 data/outline/history/）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "大纲正文（Markdown，含 acts/nodes/plan 四节）",
                },
            },
            "required": ["content"],
        },
        "_http": ("POST", "/outline/save", None),
    },
    {
        "name": "nf_refine_outline",
        "description": "AI 精修大纲：按用户意见定向修订（自动备份旧版，支持 dry-run）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "feedback": {
                    "type": "string",
                    "description": "修改意见（如『第二章节奏拖沓，需增加冲突』）",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "只生成任务不实际执行（默认 false）",
                },
            },
            "required": ["feedback"],
        },
        "_http": ("POST", "/refine/outline", None),
    },
    {
        "name": "nf_refine_chapter",
        "description": "AI 精修指定章节（±20% 字数铁律，改前自动备份）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chapter": {
                    "type": "integer",
                    "description": "章节号（如 3 表示第 3 章）",
                },
                "feedback": {
                    "type": "string",
                    "description": "修改意见",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "只生成任务不实际执行（默认 false）",
                },
            },
            "required": ["chapter", "feedback"],
        },
        "_http": ("POST", "/refine/chapter", None),
    },
    {
        "name": "nf_refine_paragraph",
        "description": "段落级定点重生成（只改写指定段落，不动其他部分）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chapter": {
                    "type": "integer",
                    "description": "章节号（如 3 表示第 3 章）",
                },
                "paragraph_index": {
                    "type": "integer",
                    "description": "段落号（0-indexed，按空行切分后的顺序）",
                },
                "feedback": {
                    "type": "string",
                    "description": '修改意见（如"把这段内心独白改成白描风格"）',
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "只生成任务不实际执行（默认 false）",
                },
            },
            "required": ["chapter", "paragraph_index", "feedback"],
        },
        "_http": ("POST", "/refine/paragraph", None),
    },
    {
        "name": "nf_paragraph_restore",
        "description": "回退段落到历史版本（从 para_history 恢复）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chapter": {"type": "integer", "description": "章节号"},
                "paragraph_index": {"type": "integer", "description": "段落号（0-indexed）"},
                "history_id": {"type": "integer", "description": "历史版本 ID"},
            },
            "required": ["chapter", "paragraph_index", "history_id"],
        },
        "_http": ("POST", "/chapters/paragraph/restore", None),
    },
    {
        "name": "nf_get_paragraph_history",
        "description": "获取段落改写历史列表",
        "inputSchema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "章节号"},
                "p": {"type": "integer", "description": "段落号（0-indexed）"},
            },
            "required": ["n", "p"],
        },
        "_http": ("GET", "/chapters/paragraph/history", None),
    },
    {
        "name": "nf_run_auto_rewrite",
        "description": "自动重写低分章节（quality < threshold 的章节）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "质量阈值（默认 6，低于此值的章节会被重写）",
                },
                "chapters": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "指定章节（不传则自动收集低分章）",
                },
                "max_rounds": {
                    "type": "integer",
                    "description": "最大重写轮次（默认 1）",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "只预览不执行（默认 true）",
                },
            },
        },
        "_http": ("POST", "/auto_rewrite/run", None),
    },
    {
        "name": "nf_get_review_report",
        "description": "获取章节审查报告（结构化 findings + 评论链）",
        "inputSchema": {"type": "object", "properties": {}},
        "_http": ("GET", "/review/report", None),
    },
    {
        "name": "nf_save_review_decisions",
        "description": "保存审稿决策（逐条 accept / ignore / 补充意见）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "finding_id": {"type": "string"},
                            "chapter": {"type": "integer"},
                            "action": {"type": "string", "enum": ["accept", "ignore"]},
                            "feedback": {"type": "string"},
                        },
                        "required": ["finding_id", "chapter", "action"],
                    },
                    "description": "决策列表",
                },
            },
            "required": ["decisions"],
        },
        "_http": ("POST", "/review/decisions", None),
    },
    {
        "name": "nf_get_sandbox_queue",
        "description": "获取沙盒审核队列（待审条目 + 状态统计 + 孤儿文件检测）",
        "inputSchema": {"type": "object", "properties": {}},
        "_http": ("GET", "/sandbox/queue", None),
    },
    {
        "name": "nf_sandbox_review",
        "description": "沙盒审核动作：通过 / 驳回 / 退回待审（只改状态库，不动文件）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "沙盒文件相对路径（如 draft/苏芷_角色卡.md）",
                },
                "action": {
                    "type": "string",
                    "enum": ["approve", "reject", "reset"],
                    "description": "approve=通过, reject=驳回, reset=退回待审",
                },
                "note": {
                    "type": "string",
                    "description": "驳回原因（action=reject 时建议填写）",
                },
            },
            "required": ["path", "action"],
        },
        "_http": ("POST", "/sandbox/review", None),
    },
    {
        "name": "nf_estimate_tokens",
        "description": "生成前 token/费用预估（确定性，零 LLM 调用）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage": {
                    "type": "integer",
                    "description": "预估指定阶段（不传则估全部）",
                },
            },
        },
        "_http": ("GET", "/estimate", None),
    },
    {
        "name": "nf_get_remedy",
        "description": "一键补救诊断（只读）：体检当前书档的一致性/产物完整性/"
                       "孤儿文件，并给出可执行的修复建议（含命令）",
        "inputSchema": {"type": "object", "properties": {}},
        "_http": ("GET", "/remedy", None),
    },
    {
        "name": "nf_dispatch_task",
        "description": "派发 sub-agent 任务：生成任务书并拉起 Hermes 子会话（异步）。"
                       "返回 run_id + brief_path，用 nf_get_agent_run 查状态",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "任务标题（如「为第 3 章起草角色卡」）",
                },
                "context": {
                    "type": "string",
                    "description": "任务上下文正文（子会话的唯一输入，必须自包含）",
                },
                "model": {
                    "type": "string",
                    "description": "覆盖子会话模型（可选，默认走 Hermes 配置）",
                },
                "max_turns": {
                    "type": "integer",
                    "description": "子会话最大轮次（可选，建议设置以防失控烧 token）",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "子会话超时秒数（默认 3600）",
                },
            },
            "required": ["title", "context"],
        },
        "_local": "dispatch",   # 直接函数调用（Phase 4：不经 HTTP loopback，不改 nf_api）
    },
    {
        "name": "nf_get_agent_run",
        "description": "查询派发任务状态（running/done/error + 结果摘要）；"
                       "不传 run_id 时列出最近派发",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "派发时返回的 run_id（不传则返回最近列表）",
                },
                "limit": {
                    "type": "integer",
                    "description": "列表模式条数（默认 50）",
                },
            },
        },
        "_local": "run",       # 直接函数调用（Phase 4：不经 HTTP loopback，不改 nf_api）
    },
    {
        "name": "nf_restore_snapshot",
        "description": "从项目快照回退（件5）。默认**只预览**将覆盖/补齐哪些产物；"
                       "传 confirm=true 才真正执行，且恢复前系统会先自动打一份 "
                       "pre_restore 快照（恢复错了还能折返）。不传 snapshot_id 时列出可用快照。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "snapshot_id": {
                    "type": "string",
                    "description": "快照 ID（history/ 下的目录名，如 20260928_180000_stage2_done；支持唯一前缀）",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "true=真正执行恢复；false/缺省=只预览（默认）",
                },
                "delete_extra": {
                    "type": "boolean",
                    "description": "是否同时删除「快照里没有」的额外文件（默认 false=保留，防不可逆误删）",
                },
            },
        },
        "_local": "snapshot_restore",   # 直接函数调用（复用 snapshot.py，不经 HTTP loopback）
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 protocol handling
# ---------------------------------------------------------------------------

def _rpc_error(code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "error": err}


def _local_snapshot_restore(args):
    """本进程内实现的快照回退（件5）。

    **复用 `snapshot.py` 的 `restore_snapshot`**（自带三道守卫：默认 dry-run 预览 /
    恢复前自动打 `pre_restore` 快照 / 路径限定在 SNAPSHOT_ITEMS），**不另造回滚逻辑** ——
    回滚是破坏性操作，判据只允许有一份实现。
    """
    import snapshot as snap
    args = args or {}
    sid = str(args.get("snapshot_id") or "").strip()
    ids = snap.snapshot_ids()
    if not sid:
        return {"ok": True, "snapshots": ids,
                "hint": "未指定 snapshot_id → 仅列出 history/ 下可用快照。"
                        "选定后传 snapshot_id（仍是预览），确认执行再传 confirm=true。"}
    if not any(i == sid or i.startswith(sid) for i in ids):
        return {"ok": False, "error": "未找到快照 '" + sid + "'", "snapshots": ids,
                "hint": "snapshot_id 取上面的目录名；支持唯一前缀。"}
    confirm = bool(args.get("confirm"))
    ok, msgs = snap.restore_snapshot(sid, yes=confirm,
                                     delete_extra=bool(args.get("delete_extra")))
    return {"ok": ok, "confirmed": confirm, "messages": msgs,
            "hint": ("已执行恢复（含恢复前 pre_restore 快照）" if confirm
                     else "以上为预览，未做任何修改；确认无误后再次调用并传 confirm=true")}


def _service_unreachable_text(reason):
    """8765 服务本体不可达时的**统一分层文案**（件6）。

    两个入口共用同一份文案，避免「同一句话写两遍、只改一处」：
      · `urllib.error.URLError`（直连被拒/超时）
      · `HTTPError 502/503/504`（企业代理或沙箱把「连不上」表现成网关 5xx）
    """
    return json.dumps({
        "ok": False,
        "layer": "8766 MCP 传输层",
        "error": "无法连接 8765 服务本体（nf_api）: " + str(reason),
        "hint": ("MCP 传输层（8766）已就绪，缺的是**服务本体（8765）**。"
                 "请在项目根启动：python scripts/nf_api.py"
                 "（或用控制台「调试启动.bat」/ 直接开绒花墨坊控制台）；"
                 "确认 http://127.0.0.1:8765/health 可访问后重试。"
                 "注意：8766 的报错不代表 8765 有问题，两层要分开看。"),
    }, ensure_ascii=False, indent=2)


def handle_rpc(method, params, http_host, http_port):
    """Dispatch a JSON-RPC method. Returns a dict (the response object)."""

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "novelforge-mcp", "version": "0.1.0"},
            },
        }

    if method == "tools/list":
        # Strip internal ``_http`` field from tool definitions
        tools = []
        for t in MCP_TOOLS:
            tools.append({
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            })
        return {"jsonrpc": "2.0", "result": {"tools": tools}}

    if method == "tools/call":
        name = (params or {}).get("name", "")
        args = (params or {}).get("arguments", {})
        # Find the tool definition
        tool = None
        for t in MCP_TOOLS:
            if t["name"] == name:
                tool = t
                break
        if tool is None:
            return _rpc_error(-32601, f"Unknown tool: {name}")

        # ---- Phase 4：本进程内直接实现的工具（_local）----
        # 派发/查询是 nf_mcp 自己的能力，不经 HTTP loopback —— 否则必须改
        # nf_api.py 加端点（Phase 4 约束：不改 nf_api 主逻辑）。
        # 派发失败由 nf_agent_dispatch 兜成 error 状态返回，不抛异常；
        # 这里只兜「模块本身挂了」的极端情况，同样转为 isError 而非崩协议。
        if "_local" in tool:
            try:
                if tool["_local"] == "snapshot_restore":
                    payload = _local_snapshot_restore(args)
                else:
                    import nf_agent_dispatch as disp
                    if tool["_local"] == "dispatch":
                        payload = disp.dispatch_to_hermes(
                            args.get("title"), args.get("context"),
                            model=args.get("model"),
                            max_turns=args.get("max_turns"),
                            timeout_s=args.get("timeout_s"),
                            source="mcp")
                    else:
                        rid = args.get("run_id")
                        if rid:
                            payload = disp.get_agent_run(rid)
                        else:
                            payload = {"ok": True,
                                       "runs": disp.list_agent_runs(args.get("limit"))}
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                return {
                    "jsonrpc": "2.0",
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": not payload.get("ok", True),
                    },
                }
            except Exception as e:                          # noqa: BLE001
                text = json.dumps({"ok": False,
                                   "error": f"{type(e).__name__}: {e}"},
                                  ensure_ascii=False)
                return {
                    "jsonrpc": "2.0",
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": True,
                    },
                }

        http_method, http_path_template, _ = tool["_http"]
        # Substitute path parameters (e.g. /stage/{stage}/run)
        http_path = http_path_template
        for k, v in args.items():
            if "{" + k + "}" in http_path:
                http_path = http_path.replace("{" + k + "}", str(v))

        # Build the HTTP request to loop back to nf_api
        url = f"http://{http_host}:{http_port}{http_path}"
        try:
            if http_method == "GET":
                # Query params from args (excluding path params used above)
                query_parts = []
                for k, v in args.items():
                    if "{" + k + "}" not in http_path_template:
                        if isinstance(v, bool):
                            query_parts.append(f"{k}={1 if v else 0}")
                        else:
                            query_parts.append(f"{k}={urllib.request.quote(str(v))}")
                if query_parts:
                    url += "?" + "&".join(query_parts)
                req = urllib.request.Request(url, headers={"X-Mofang-Source": "mcp"})
            else:
                body = json.dumps(args, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "X-Mofang-Source": "mcp",
                    },
                )

            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = resp.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw[:2000]}

                # Phase 5: 流式输出 — 当 nf_run_stage stream:true 返回 job_id 时，
                # 额外追加 stream_url 和 status: streaming_started
                if (
                    name == "nf_run_stage"
                    and args.get("stream") is True
                    and isinstance(payload, dict)
                    and payload.get("stream") is True
                    and payload.get("job_id")
                ):
                    payload["stream_url"] = (
                        f"http://{http_host}:{http_port}/stream/{payload['job_id']}"
                    )
                    payload["status"] = "streaming_started"

                # MCP expects: { content: [{type: "text", text: "..."}] }
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                return {
                    "jsonrpc": "2.0",
                    "result": {"content": [{"type": "text", "text": text}]},
                }

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            # 件6：企业代理 / 沙箱会把「连不上 8765」表现成网关 5xx ——
            # 这与 URLError 是同一件事（服务本体不可达），给同一份分层指引。
            low = body.lower()
            if e.code in (502, 503, 504) or "connect failed" in low or "connection refused" in low:
                text = _service_unreachable_text("HTTP " + str(e.code) + ": " + body[:200])
                return {
                    "jsonrpc": "2.0",
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": True,
                    },
                }
            try:
                err_payload = json.loads(body)
            except Exception:
                err_payload = {"error": f"HTTP {e.code}: {body[:500]}"}
            text = json.dumps(err_payload, ensure_ascii=False, indent=2)
            return {
                "jsonrpc": "2.0",
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": True,
                },
            }
        except urllib.error.URLError as e:
            # 件6：8765 服务本体不可达（未启动/未监听/被占）→ 干净报错，**明确分层**。
            # 原先落到最后的 `except Exception` → 返回裸 URLError 文本，
            # 用户看到 8766 的错却去翻 8765 的服务，方向全错。
            reason = getattr(e, "reason", e)
            text = json.dumps({
                "ok": False,
                "layer": "8766 MCP 传输层",
                "error": "无法连接 8765 服务本体（nf_api）: " + str(reason),
                "hint": ("MCP 传输层（8766）已就绪，缺的是**服务本体（8765）**。"
                         "请在项目根启动：python scripts/nf_api.py"
                         "（或用控制台「调试启动.bat」/ 直接开绒花墨坊控制台）；"
                         "确认 http://127.0.0.1:8765/health 可访问后重试。"
                         "注意：8766 的报错不代表 8765 有问题，两层要分开看。"),
            }, ensure_ascii=False, indent=2)
            return {
                "jsonrpc": "2.0",
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": True,
                },
            }
        except Exception as e:
            return _rpc_error(-32600, f"MCP call failed: {type(e).__name__}: {e}")

    return _rpc_error(-32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# TCP server (newline-delimited JSON-RPC 2.0)
# ---------------------------------------------------------------------------

def _read_line(sock):
    """Read a full newline-delimited line from socket. Returns bytes or None on EOF."""
    buf = b""
    while True:
        try:
            chunk = sock.recv(1)
            if not chunk:
                return None  # EOF
            buf += chunk
            if chunk == b"\n":
                return buf
        except OSError:
            return None


def _handle_client(client, http_host, http_port):
    """Handle a single MCP client connection."""
    try:
        while True:
            line = _read_line(client)
            if line is None:
                break  # Client disconnected

            line = line.strip()
            if not line:
                continue

            try:
                req = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                resp = _rpc_error(-32700, "Parse error")
                client.sendall(json.dumps(resp).encode("utf-8") + b"\n")
                continue

            method = req.get("method", "")
            params = req.get("params", {})
            resp = handle_rpc(method, params, http_host, http_port)
            resp["id"] = req.get("id")
            data = json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n"
            client.sendall(data)
    except Exception:
        pass
    finally:
        try:
            client.close()
        except Exception:
            pass


class MCPServer:
    def __init__(self, host="127.0.0.1", port=8766, http_host="127.0.0.1", http_port=8765):
        self.host = host
        self.port = port
        self.http_host = http_host
        self.http_port = http_port
        self._sock = None
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        # 服务发现：广播 MCP 地址，让外部 Agent 自动发现
        self._start_discovery()

    def _start_discovery(self):
        """UDP 广播 MCP 服务地址，外部 Agent 监听后即可自动连接。

        广播内容：{"service":"novelforge-mcp","host":"127.0.0.1","port":8766}
        广播端口：8767（UDP，一次性广播 3 次）
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            msg = json.dumps({
                "service": "novelforge-mcp",
                "host": self.host,
                "port": self.port,
                "http_port": self.http_port,
            }).encode("utf-8")
            for _ in range(3):
                sock.sendto(msg, ("<broadcast>", 8767))
            sock.close()
            print(f"[nf_mcp] 服务发现广播已发送 → udp://<broadcast>:8767")
        except Exception as e:
            print(f"[nf_mcp] 服务发现广播失败（不影响 MCP 服务）: {e}")

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def _serve(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(1.0)
        try:
            self._sock.bind((self.host, self.port))
            self._sock.listen(5)
            print(f"[nf_mcp] MCP server listening on tcp://{self.host}:{self.port}")
        except Exception as e:
            print(f"[nf_mcp] FAILED to bind {self.host}:{self.port}: {e}")
            print("[nf_mcp] ↑ 这是【**8766 MCP 传输层**】启动失败（多半端口被占）。"
                  "服务本体是 8765（nf_api），两层互不影响。"
                  "排查：netstat -ano | findstr 8766，或设 NF_MCP_PORT 换端口。")
            return

        while self._running:
            try:
                client, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=_handle_client,
                args=(client, self.http_host, self.http_port),
                daemon=True,
            )
            t.start()


# ---------------------------------------------------------------------------
# Entry point (for standalone testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 与 nf_api.py 的拉起方式一致：NF_MCP_PORT 可覆盖端口（默认 8766）
    srv = MCPServer(port=int(os.environ.get("NF_MCP_PORT", "8766")))
    srv.start()
    print("[nf_mcp] Press Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
