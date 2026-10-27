# -*- coding: utf-8 -*-
"""重写/流式：SSE 包装、文件尾、MCP 入口（2026-10-27，纯 fake、零 LLM）。

## 为什么需要它

用户实测三连问：「几百秒无新字是什么鬼」「很早就提了做流式输出」「连写到哪里
都看不到」。根因不是没做流式，而是：
  1. `_wrap_client_for_streaming` 只包了 `run_task`，而 stage4 走
     `run_task_stream` —— 最长的一站反而绕过了 SSE；
  2. hermes 用 `write_file` 写正文，text 事件里没有正文，字数只有 8。

本用例钉住四件事：
  A. 包装器**两个方法都包**，且 stage4 自己的 on_piece 不被吞；
  B. 停止标志串联（用户的 stop 与 SSE 的 stop 任一为真都停）；
  C. `_rewrite_file_tail` 把正在写的文件尾部/字数/字节推成 `{type:"file"}`；
  D. MCP 暴露 `nf_rewrite_chapter`（用户裁定：重写算 Agent 可执行）。

用法：python tests/unit/test_rewrite_stream.py
"""
import io
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import nf_api  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


class StreamClient:
    provider = "fake"

    def __init__(self):
        self.stop_seen = None

    def run_task(self, task_file, **kw):
        return {"exit_code": 0, "model": "fake", "tokens": 1, "tokens_out": 1}

    def run_task_stream(self, task_file, on_piece, stop_flag, **kw):
        on_piece("正文一")
        on_piece("")                     # 工具事件心跳（hermes 会这么发）
        self.stop_seen = stop_flag()
        return {"exit_code": 0, "model": "fake", "tokens": 1, "tokens_out": 1}


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def main():
    tmp = Path(tempfile.mkdtemp(prefix="nf_stream_"))
    nf_api._set_root(tmp)
    try:
        # ---- A/B：包装器两个方法都包 + 回调/停止串联 ----
        print("\n【A】流式包装器：stage4 的 run_task_stream 也要走 SSE")
        q = queue.Queue()
        ev = threading.Event()
        with nf_api.STREAMERS_LOCK:
            nf_api.STREAMERS["job_stream"] = {"queue": q, "stop": ev, "text": []}
        c = StreamClient()
        user_pieces = []
        wrapped = nf_api._wrap_client_for_streaming(c, "job_stream")
        wrapped.run_task_stream("task.md", user_pieces.append, lambda: True)
        check("每次调用只记 last_result，不推 done（done 归 job）",
              (nf_api.STREAMERS["job_stream"].get("last_result") or {}).get("model") == "fake",
              nf_api.STREAMERS["job_stream"].get("last_result"))
        nf_api._notify_stream_done("job_stream", "ok", "完成")
        events = _drain(q)
        tokens = [e["text"] for e in events if e.get("type") == "token"]
        done = [e for e in events if e.get("type") == "done"]
        check("正文 token 真的进了 SSE 队列", tokens == ["正文一"], tokens)
        check("stage4 自己的 on_piece 没被吞（进度回调仍在）",
              user_pieces == ["正文一", ""], user_pieces)
        check("用户 stop 与 SSE stop 串联（任一方为真都停）",
              c.stop_seen is True, c.stop_seen)
        check("done 由 job 结束时推且只发一次",
              len(done) == 1 and done[0].get("status") == "ok", done)

        # run_task 老路径仍可用（其它阶段走它）
        q2 = queue.Queue()
        with nf_api.STREAMERS_LOCK:
            nf_api.STREAMERS["job_task"] = {"queue": q2, "stop": threading.Event(),
                                            "text": []}
        c2 = StreamClient()
        nf_api._wrap_client_for_streaming(c2, "job_task").run_task("task.md")
        nf_api._notify_stream_done("job_task", "ok", "完成")
        ev2 = _drain(q2)
        check("run_task 路径推 token，done 由 job 推",
              any(e.get("type") == "token" for e in ev2)
              and any(e.get("type") == "done" for e in ev2), ev2)

        # ---- C：文件尾 ----
        print("\n【C】文件尾：写到哪里 = 文件里的事实")
        parts = tmp / "data" / "chapters" / "_parts" / "07"
        parts.mkdir(parents=True)
        (parts / "g1.md").write_text("第一段正文。" * 80, encoding="utf-8")
        q3 = queue.Queue()
        stop3 = threading.Event()
        t = threading.Thread(target=nf_api._rewrite_file_tail,
                             args=(7, q3, stop3), daemon=True)
        t.start()
        time.sleep(0.5)
        stop3.set()
        t.join(timeout=3)
        fevs = [e for e in _drain(q3) if e.get("type") == "file"]
        check("推了 {type:file} 事件", bool(fevs), fevs[:1])
        check("事件带真实字数/字节/文件尾",
              bool(fevs) and fevs[0]["words"] > 0 and fevs[0]["bytes"] > 0
              and "第一段正文" in fevs[0]["text"], fevs[:1])

        # ---- D：MCP 入口 ----
        print("\n【D】MCP：重写算 Agent 可执行")
        import nf_mcp
        names = [t["name"] for t in nf_mcp.MCP_TOOLS]
        check("MCP 暴露 nf_rewrite_chapter", "nf_rewrite_chapter" in names, names[-5:])
        tool = [t for t in nf_mcp.MCP_TOOLS if t["name"] == "nf_rewrite_chapter"][0]
        check("工具参数 schema 含 n",
              "n" in (tool.get("inputSchema", {}).get("properties") or {}), tool)
        check("工具映射到 POST /chapters/rewrite",
              tool.get("_http") == ("POST", "/chapters/rewrite", None), tool.get("_http"))

        # ---- E：HTTP 端点 stream 分支真的注册 STREAMERS ----
        print("\n【E】POST /chapters/rewrite?stream：注册 SSE 队列并返回 stream=true")
        from nf_api_domains import chapters as dom
        orig_start = nf_api.start_job
        orig_factory = nf_api.act_rewrite_chapter_streamed
        try:
            nf_api.start_job = lambda kind, fn: ("job_fake_1", None)
            nf_api.act_rewrite_chapter_streamed = (
                lambda cfg, n, preview, q, ev: (lambda: (True, "ok")))
            code, payload = dom.handle_chapters_rewrite(None, {"n": 3, "stream": True})
            check("返回 202 + stream=true", code == 202 and payload.get("stream") is True,
                  payload)
            with nf_api.STREAMERS_LOCK:
                check("STREAMERS 里能按真实 job_id 找到队列",
                      "job_fake_1" in nf_api.STREAMERS,
                      list(nf_api.STREAMERS))
        finally:
            nf_api.start_job = orig_start
            nf_api.act_rewrite_chapter_streamed = orig_factory
            with nf_api.STREAMERS_LOCK:
                nf_api.STREAMERS.pop("job_fake_1", None)
    finally:
        with nf_api.STREAMERS_LOCK:
            nf_api.STREAMERS.clear()
        nf_api._set_root(ROOT)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
