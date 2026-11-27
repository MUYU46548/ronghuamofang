# -*- coding: utf-8 -*-
"""DSH 会话账：从 ~/.dsh/sessions 复算每次调用的耗时 / token / 工具分布。

用法（用项目 .venv python，已装 zstandard）：
    python scripts/dsh_session_report.py [<sessions_dir>] [--json]

`<sessions_dir>` 省略时，自动取 `~/.dsh/sessions` 下最近修改的、含
`session-*` 的目录。只读，零 token；不改 `logs/runs.db`，也不发任何请求。

为什么要这个脚本（2026-11-27）：
`logs/runs.db` 只记 token，不记墙钟与步数，于是“一章跑了 15 分钟”只能靠人看表。
DSH 自己的会话日志里有毫秒时间戳、每步 usage 与工具调用，足够复算出
“模型生成 / 工具执行 / 思考 / 正文”各占多少 —— 先量出来，再决定改哪里。
"""
import argparse
import collections
import datetime
import json
import pathlib


def load_session(path):
    """读多帧 zstd 的 JSONL。

    注意：`ZstdDecompressor().decompress()` 只解第一帧（会话头 213 字节），
    必须 `stream_reader(..., read_across_frames=True)`，否则读到的永远是空账。
    """
    try:
        import zstandard
    except ImportError:                                  # pragma: no cover
        raise SystemExit(
            "需要 zstandard 才能读 DSH 会话日志：\n"
            "  .venv/Scripts/python.exe -m pip install zstandard\n"
            "（只读报告用，不影响墨坊主流程）")

    with path.open("rb") as f:
        raw = zstandard.ZstdDecompressor().stream_reader(
            f, read_across_frames=True).read()
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def block_stats(message):
    """一条助手消息里的思考字符 / 正文与工具参数字符。"""
    think = content = 0
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in ("thinking", "reasoning"):
            think += len(str(block.get("thinking") or block.get("text") or ""))
        elif kind == "text":
            content += len(str(block.get("text") or ""))
        elif kind == "tool-call":
            content += len(str(block.get("arguments") or ""))
    return think, content


def analyze(path):
    """把一个会话文件收敛成一行统计。"""
    events = load_session(path)
    times = [e["time"] for e in events if isinstance(e.get("time"), int)]
    span = (times[-1] - times[0]) if times else 0
    starts = [e for e in events if e.get("type") == "step/start"]
    ends = {(e["data"].get("turn"), e["data"].get("step")): e["time"]
            for e in events if e.get("type") == "step/end"}
    model_ms = []
    for start in starts:
        key = (start["data"].get("turn"), start["data"].get("step"))
        if key in ends:
            delta = ends[key] - start["time"]
            # 只认落在本会话时间跨度内的步（跨会话/时钟跳变的事件不算）
            if 0 <= delta <= span:
                model_ms.append(delta)

    calls = [e for e in events if e.get("type") == "tool/call"]
    results = [e for e in events if e.get("type") == "tool/result"]
    call_time = {c["data"]["callId"]: c["time"]
                 for c in calls if (c.get("data") or {}).get("callId")}
    tool_ms = []
    seen = set()
    for result in results:
        data = result.get("data") or {}
        call_id = data.get("toolCallId") or (data.get("message") or {}).get("toolCallId")
        if call_id not in call_time or not result.get("time"):
            continue
        # 同一个 callId 可能有多条 result（重放/续接）：只算第一条，否则工具耗时虚高
        if call_id in seen:
            continue
        seen.add(call_id)
        delta = result["time"] - call_time[call_id]
        if 0 <= delta <= span:
            tool_ms.append(delta)

    assistants = [e for e in events if e.get("type") == "assistant/message"]
    think = content = in_tok = out_tok = 0
    for message in assistants:
        data = message.get("data") or {}
        t, c = block_stats(data.get("message") or {})
        think += t
        content += c
        usage = data.get("usage") or {}
        in_tok += int(usage.get("inputTokens") or 0)
        out_tok += int(usage.get("outputTokens") or 0)

    return {
        "path": str(path),
        "start": times[0] if times else 0,
        "dur_s": (times[-1] - times[0]) / 1000 if times else 0,
        "steps": len(starts),
        "calls": len(calls),
        "model_s": sum(model_ms) / 1000,
        "tool_s": sum(tool_ms) / 1000,
        "think_ch": think,
        "content_ch": content,
        "in_tok": in_tok,
        "out_tok": out_tok,
        "tools": dict(collections.Counter(
            (c.get("data") or {}).get("name") for c in calls).most_common(6)),
    }


def pick_root(explicit):
    """没给目录时，取最近修改的那个项目会话目录。"""
    if explicit:
        return pathlib.Path(explicit)
    base = pathlib.Path.home() / ".dsh" / "sessions"
    candidates = [d for d in base.glob("*")
                  if d.is_dir() and list(d.glob("session-*"))]
    if not candidates:
        return None
    return max(candidates,
               key=lambda d: max(p.stat().st_mtime for p in d.glob("session-*")))


def main():
    parser = argparse.ArgumentParser(
        description="DSH 会话账（只读、零 token）：耗时 / token / 工具分布")
    parser.add_argument("sessions_dir", nargs="?",
                        help="会话目录（省略时取 ~/.dsh/sessions 下最新的一个）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    root = pick_root(args.sessions_dir)
    if root is None:
        print("没有找到会话目录（~/.dsh/sessions 下没有 session-*）")
        return 1
    if not root.exists():
        print("会话目录不存在：" + str(root))
        return 1
    print("# 会话目录: " + str(root))

    rows = []
    for folder in sorted(root.glob("session-*"), key=lambda p: p.stat().st_mtime):
        target = folder / "session.v4.jsonl.zstd"
        if target.exists():
            rows.append(analyze(target))

    if not rows:
        print("目录里没有 session.v4.jsonl.zstd：" + str(root))
        return 1

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    header = (f"{'session':9} {'start':12} {'dur_s':>7} {'step':>5} {'call':>5} "
              f"{'model_s':>8} {'tool_s':>7} {'think_ch':>9} {'content':>8} "
              f"{'in_tok':>9} {'out_tok':>8}")
    print(header)
    total = collections.Counter()
    for row in rows:
        stamp = (datetime.datetime.fromtimestamp(row["start"] / 1000).strftime("%m-%d %H:%M")
                 if row["start"] else "-")
        name = pathlib.Path(row["path"]).parent.name[-8:]
        print(f"{name:9} {stamp:12} {row['dur_s']:7.0f} {row['steps']:5} {row['calls']:5} "
              f"{row['model_s']:8.0f} {row['tool_s']:7.1f} {row['think_ch']:9} "
              f"{row['content_ch']:8} {row['in_tok']:9} {row['out_tok']:8}")
        for key in ("dur_s", "steps", "calls", "model_s", "tool_s",
                    "think_ch", "content_ch", "in_tok", "out_tok"):
            total[key] += row[key]
    print(f"{'TOTAL':9} {'':12} {total['dur_s']:7.0f} {total['steps']:5} {total['calls']:5} "
          f"{total['model_s']:8.0f} {total['tool_s']:7.1f} {total['think_ch']:9} "
          f"{total['content_ch']:8} {total['in_tok']:9} {total['out_tok']:8}")
    print()
    for row in rows:
        print(pathlib.Path(row["path"]).parent.name[-8:], row["tools"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
