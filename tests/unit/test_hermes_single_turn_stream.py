# -*- coding: utf-8 -*-
"""hermes 通道的"单轮流式 + 可杀"（C18 补完，2026-11-16）。

## 为什么要有这份用例

讨论面板的"让 AI 闭嘴"在 direct 引擎下走 `_post_chat_stream` 的 stop_flag；
而 hermes（agent）引擎此前只有 `_post_chat` —— `communicate()` 等整块返回，
中途只能去 agent 自己的交互里按 Ctrl+C。本用例钉住新补的
`HermesClient._post_chat_stream`：

- H1 真流式：text 事件逐段回调，收尾 result 的干净文本作为最终答案
- H2 **stop_flag 真的杀子进程**：10 秒的流，停在 0.15 秒内，返回 finish=stopped
  （判据是墙钟：没杀就必然跑满 10 秒 —— 这条能变红）
- H3 超时仍然抛错（不许把"超时"当成"正常跑完"）
- H4 stderr 混进来的非 JSON 行不影响解析
- H5 没有 result 事件时用 text 事件拼答案（半截也要如实返回）

用法：python tests/unit/test_hermes_single_turn_stream.py
"""
import io
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.llm_client import HermesClient                     # noqa: E402

PASS, FAIL = [], []

STUB = r'''
import json, sys, time
mode = sys.argv[1] if len(sys.argv) > 1 else "fast"
n = 200 if mode in ("long", "timeout") else 3
if mode == "nasty":
    sys.stderr.write("python: 一条不是 JSON 的告警行\n")
    sys.stderr.flush()
for i in range(n):
    print(json.dumps({"type": "text", "text": "第%d句。" % i}, ensure_ascii=False), flush=True)
    if mode in ("long", "timeout"):
        time.sleep(0.05)
if mode != "noresult":
    print(json.dumps({"type": "result", "text": "收尾答案。", "exit_code": 0,
                      "tokens": {"input": 11, "output": 22},
                      "finish_reason": "stop"}, ensure_ascii=False), flush=True)
'''


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


class StubHermes(HermesClient):
    """替换命令拼装，进程是真的：测的就是"起进程 / 读流 / 杀掉"这一段。"""

    def __init__(self, script, mode="fast", timeout=20):
        super().__init__(hermes_bin="stub", timeout=timeout)
        self._script = str(script)
        self._mode = mode

    def _build_query_cmd(self, query_path, model=None):
        return [sys.executable, "-u", self._script, self._mode]


def main():
    print("=" * 66)
    print("hermes 单轮流式（_post_chat_stream）")
    tmp = Path(tempfile.mkdtemp())
    script = tmp / "stub_hermes.py"
    script.write_text(STUB, encoding="utf-8")
    msgs = [{"role": "system", "content": "你是编辑。"},
            {"role": "user", "content": "说两句。"}]

    print("\n【1】真流式 + 干净收尾")
    chunks = []
    c = StubHermes(script, "fast")
    text, usage, model, finish = c._post_chat_stream(
        msgs, on_chunk=chunks.append)
    check("H1 逐段回调了 3 块", len(chunks) == 3, chunks)
    check("H1 回调内容正确", chunks and chunks[0] == "第0句。", chunks[:1])
    check("H1 最终答案是收尾 result 的干净文本", text == "收尾答案。", text)
    check("H1 finish=stop", finish == "stop", finish)
    check("H1 usage 来自 result.tokens",
          usage.get("prompt_tokens") == 11 and usage.get("completion_tokens") == 22, usage)
    check("H1 model 有值", bool(model), model)

    print("\n【2】没有 result 事件时用 text 拼")
    text2, _u2, _m2, finish2 = StubHermes(script, "noresult")._post_chat_stream(msgs)
    check("H5 text 事件拼出答案", text2 == "第0句。第1句。第2句。", text2)
    check("H5 finish 仍是 stop", finish2 == "stop", finish2)

    print("\n【3】stop_flag：真的杀子进程")
    ticks = {"n": 0}
    stop = {"on": False}

    def on_chunk(piece):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            stop["on"] = True

    t0 = time.time()
    text3, _u3, _m3, finish3 = StubHermes(script, "long")._post_chat_stream(
        msgs, on_chunk=on_chunk, stop_flag=lambda: stop["on"])
    dt = time.time() - t0
    check("H2 返回 finish=stopped", finish3 == "stopped", finish3)
    check("H2 墙钟 < 3 秒（没杀就必然跑满 10 秒）", dt < 3.0, round(dt, 2))
    check("H2 已收到的内容照样返回", "第0句。" in (text3 or ""), text3)

    print("\n【4】超时抛错（不许当正常跑完）")
    t0 = time.time()
    try:
        StubHermes(script, "timeout", timeout=1)._post_chat_stream(msgs)
    except RuntimeError as e:
        dt = time.time() - t0
        check("H3 超时抛 RuntimeError", "超时" in str(e), str(e)[:80])
        check("H3 超时后迅速返回（进程被杀）", dt < 8.0, round(dt, 2))
    else:
        check("H3 超时抛 RuntimeError", False, "没有抛错")

    print("\n【5】stderr 混入非 JSON 不影响")
    text5, _u5, _m5, finish5 = StubHermes(script, "nasty")._post_chat_stream(msgs)
    check("H4 非 JSON 行被忽略，答案正常", text5 == "收尾答案。", text5)
    check("H4 finish 正常", finish5 == "stop", finish5)

    print("\n【6】接线：讨论轮现在能拿到流式")
    src = (ROOT / "scripts" / "utils" / "llm_client.py").read_text(encoding="utf-8")
    check("H6 HermesClient 有 _post_chat_stream", "def _post_chat_stream" in src)
    dom = (ROOT / "scripts" / "nf_api_domains" / "discuss_thread.py").read_text(encoding="utf-8")
    check("H6 端点声明 stoppable=True（两通道都能停）",
          '"stoppable": True' in dom, dom[-400:])

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
