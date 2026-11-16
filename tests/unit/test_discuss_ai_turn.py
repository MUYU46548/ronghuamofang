# -*- coding: utf-8 -*-
"""讨论 AI 轮：AI 真的能说一轮（C1/C2/C18/C20，2026-11-16）。

## 为什么有这份用例

施工前的事实：`DiscussThreadPanel.vue:41` 那个"以 AI 身份说"按钮，点下去是
**作者自己替 AI 打字** —— `POST /discuss/say {role:"ai"}` 只是追加一个字符串。
本用例钉住补上之后的六件事，每条都能变红：

- Q1 会话体：md 是投影、session 是模型视图；坏了能从 md 回填；派生 json 不冒充讨论
- Q2 装配：system 逐字不变（前缀缓存）、背景带章号/正文、历史按窗口取
- Q3 解析：围栏/废话/坏 JSON/空 evidence/非法 stance 都要被拦住
- Q4 引文：逐字命中才算数；一条都不中 → 整轮拒绝（不许"看着像"进讨论稿）
- Q5 落盘：成功一轮 = md 多一轮 + session 多一条 + .ai.json 有结构化结果
- Q6 停止/截断：**都不算一轮**，残片进 data/state/truncated/，讨论稿一个字都不加

用法：python tests/unit/test_discuss_ai_turn.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import discussion, discuss_ai                     # noqa: E402

PASS, FAIL = [], []

BODY = ("第 03 章 雨夜\n\n"
        "他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。\n\n"
        "她把杯子放下，杯底和木桌碰出很轻的一响，茶水晃了一圈。\n")


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


class Client:
    """最小假客户端：可控 finish_reason / 拒绝流式 / 中途置 stop。"""

    model = "stub"

    def __init__(self, payload=None, finish="stop", stream=True, stop_after=0, timing=None):
        self.payload = payload
        self.finish = finish
        self.stream = stream
        self.stop_after = stop_after
        self.timing = timing
        self.calls = 0

    def _body(self):
        if self.payload is not None:
            return self.payload if isinstance(self.payload, str) else json.dumps(
                self.payload, ensure_ascii=False)
        return json.dumps({
            "text": "这一轮先说结论：冲突压在场面里，读者看不到代价。",
            "stance": "push",
            "evidence": [{"kind": "quote",
                          "quote": "他推门进来的时候，屋檐上的水线正连成了串。",
                          "why": "开场就把人放在雨里"}],
            "decision": "代价要不要提前？",
            "action": {"kind": "suggest_edit",
                       "target": {"para_index": 1, "quote": "他推门进来"},
                       "why": "把代价前置"},
            "needs_author": True,
        }, ensure_ascii=False)

    def _post_chat(self, messages, temperature=None, max_tokens=None):
        self.calls += 1
        return self._body(), {"prompt_tokens": 10, "completion_tokens": 5}, "stub", self.finish

    def _post_chat_stream(self, messages, temperature=None, max_tokens=None,
                          on_chunk=None, stop_flag=None):
        if not self.stream:
            raise AssertionError("用例不该走流式")
        self.calls += 1
        text = self._body()
        i, k = 0, 20
        while i < len(text):
            if self.timing:
                self.timing()
            if stop_flag and stop_flag():
                return text[:i], {}, "stub", "stopped"
            if on_chunk:
                on_chunk(text[i:i + k])
            i += k
            if self.stop_after and self.calls == 1 and i >= self.stop_after:
                # 模拟"作者在第 N 字时点了闭嘴"：只能靠外部事件置位，
                # 这里由调用方通过 timing 回调设置 stop 状态。
                pass
        return text, {"prompt_tokens": 10, "completion_tokens": 5}, "stub", self.finish


def make_root():
    root = Path(tempfile.mkdtemp())
    (root / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "data" / "outline" / "chapters").mkdir(parents=True, exist_ok=True)
    (root / "data" / "chapters" / "raw" / "03.md").write_text(BODY, encoding="utf-8")
    (root / "data" / "outline" / "chapters" / "03.md").write_text(
        "核心事件：雨夜来客。\n", encoding="utf-8")
    return root


def main():
    print("=" * 66)
    print("讨论 AI 轮（discuss_ai）")
    root = make_root()
    topic = "结局要不要开信"

    print("\n【1】会话体：md 是投影，session 是模型视图")
    md, js = discussion.open_thread(3, topic, "这一章到底在犹豫什么", root=root)
    discussion.append_round(3, topic, "author", "先别开，留到最后一章", root=root)
    sp = discussion.session_path(3, topic, root)
    sess = discussion.load_session(3, topic, root)
    check("Q1a 追加一轮就写出 session", sp.exists(), sp)
    check("Q1a 会话里有作者那条", any(m["role"] == "author" for m in sess["messages"]),
          sess["messages"])
    discussion.append_round(3, topic, "ai", "可以先让她只摸到信封", root=root)
    sess = discussion.load_session(3, topic, root)
    check("Q1b AI 那条也进了会话", len(sess["messages"]) == 2, sess["messages"])

    discussion.open_thread(3, "第三处讨论", root=root)
    discussion.append_round(3, "第三处讨论", "ai", "随便一句", root=root)
    items = discussion.list_threads(3, root=root)
    check("Q1c 派生 json 不冒充讨论（只两篇）", len(items) == 2,
          [i.get("topic") for i in items])
    check("Q1c 列表里没有 session/ai 当主题",
          all("session" not in (i.get("topic") or "") for i in items),
          [i.get("topic") for i in items])

    sp.unlink()
    restored = discussion.load_session(3, topic, root)
    check("Q1d session 删了能从 md 回填", len(restored["messages"]) == 2,
          restored["messages"])
    check("Q1d 回填保住了角色",
          [m["role"] for m in restored["messages"]] == ["author", "ai"],
          [m["role"] for m in restored["messages"]])

    msgs, omitted = discussion.session_messages(3, topic, root, keep=1)
    check("Q1e keep=1 只带最后一轮", len(msgs) == 1, msgs)
    check("Q1e 说明省掉了哪几轮", omitted == [1], omitted)
    check("Q1e AI 侧是 assistant", msgs[0]["role"] == "assistant", msgs[0])

    print("\n【2】装配：固定前缀 + 背景 + 历史 + 本轮指令")
    m1, meta = discuss_ai.build_messages(3, topic, "往结局想", root=root)
    m2, _ = discuss_ai.build_messages(3, topic, "换个说法", root=root)
    check("Q2a 第一条是 system 且两次逐字相同",
          m1[0]["role"] == "system" and m1[0]["content"] == m2[0]["content"],
          m1[0]["content"][:40])
    check("Q2a system 里写明只要 JSON", "只输出一个 JSON" in m1[0]["content"])
    check("Q2b 第二条 system 带章号与主题",
          m1[1]["role"] == "system" and "第 3 章" in m1[1]["content"]
          and topic in m1[1]["content"], m1[1]["content"][:80])
    check("Q2b 正文用固定标记包着", "<<<本章正文开始>>>" in m1[1]["content"])
    check("Q2b 带了本章大纲", "核心事件" in m1[1]["content"])
    check("Q2c 最后一条是 user 且带提示",
          m1[-1]["role"] == "user" and "往结局想" in m1[-1]["content"], m1[-1])
    check("Q2c 中间有历史", meta["history"] >= 1, meta)

    print("\n【3】解析：形状错一律拦住")
    good = json.loads(Client()._body())
    check("Q3a 纯 JSON 通过", discuss_ai.parse_turn(json.dumps(good))["stance"] == "push")
    check("Q3b ```json 围栏能剥",
          discuss_ai.parse_turn("```json\n" + json.dumps(good) + "\n```")["text"].startswith("这一轮"))
    check("Q3c 前后废话能剥",
          discuss_ai.parse_turn("好的，我的看法是：\n" + json.dumps(good) + "\n以上。")["stance"] == "push")

    def expect_fail(name, raw, kind):
        try:
            discuss_ai.parse_turn(raw)
        except discuss_ai.DiscussAIError as e:
            check(name, e.kind == kind, e.kind)
        else:
            check(name, False, "没有抛错")

    expect_fail("Q3d 空输出 → bad_json", "", "bad_json")
    expect_fail("Q3d 没有 JSON → bad_json", "我觉得可以改一下。", "bad_json")
    expect_fail("Q3e 缺 text → bad_payload",
                json.dumps(dict(good, text="")), "bad_payload")
    expect_fail("Q3f stance 非法 → bad_payload",
                json.dumps(dict(good, stance="argue")), "bad_payload")
    expect_fail("Q3g evidence 空 → no_evidence",
                json.dumps(dict(good, evidence=[])), "no_evidence")
    expect_fail("Q3h action.kind 非法 → bad_payload",
                json.dumps(dict(good, action={"kind": "just_do_it"})), "bad_payload")

    print("\n【4】引文：逐字命中才算数")
    ok1, rep1 = discuss_ai.verify_evidence(good, 3, root)
    check("Q4a 逐字命中 → verified=1",
          rep1["verified"] == 1 and ok1["evidence"][0]["verified"] is True, rep1)
    tail = json.loads(Client()._body())
    tail["evidence"][0]["quote"] = "他推门进来的时候，屋檐上的水线正连成串了。"   # 改了一个字
    ok2, rep2 = discuss_ai.verify_evidence(tail, 3, root)
    check("Q4b 尾部被改写 → 仍按前 12 字认（容错）", rep2["verified"] == 1, rep2)
    bad = json.loads(Client()._body())
    bad["evidence"][0]["quote"] = "他推开门，看见雨里站着一个人。"
    try:
        discuss_ai.verify_evidence(bad, 3, root)
    except discuss_ai.DiscussAIError as e:
        check("Q4c 一条都不中 → missing_quote", e.kind == "missing_quote", e.kind)
    else:
        check("Q4c 一条都不中 → missing_quote", False, "没有抛错")
    empty_root = Path(tempfile.mkdtemp())
    outline_only = json.loads(Client()._body())
    outline_only["evidence"] = [{"kind": "outline", "name": "本章大纲", "why": "还没正文"}]
    _ok, rep4 = discuss_ai.verify_evidence(outline_only, 3, empty_root)
    check("Q4d 没有正文时 outline 证据放行", rep4["quotes"] == 0, rep4)

    print("\n【5】成功一轮：三处一起落")
    before = discussion._read_json(js).get("rounds") or 0
    res = discuss_ai.run_turn(Client(), 3, topic, "", root=root)
    st = discussion._read_json(js)
    check("Q5a 轮次 +1", st.get("rounds") == before + 1, st.get("rounds"))
    check("Q5b md 里有 AI 那段话",
          "冲突压在场面里" in md.read_text(encoding="utf-8"))
    ap = discussion.ai_payload_path(3, topic, root)
    check("Q5c .ai.json 落盘", ap.exists(), ap)
    data = json.loads(ap.read_text(encoding="utf-8"))
    check("Q5d 结构化字段齐", data.get("stance") == "push"
          and data.get("action", {}).get("kind") == "suggest_edit"
          and isinstance(data.get("evidence"), list), data)
    check("Q5e 轮次号由服务端回填", data.get("round") == st.get("rounds"), data.get("round"))
    check("Q5f session 同步增长",
          len(discussion.load_session(3, topic, root)["messages"]) == 3,
          len(discussion.load_session(3, topic, root)["messages"]))

    # 没有流式接口的引擎（hermes 单轮）：能跑，但必须如实标 streaming=False，
    # 否则界面会给出一个按不停的"让 AI 闭嘴"。
    class PlainClient:
        model = "plain"

        def _post_chat(self, messages, temperature=None, max_tokens=None):
            return Client()._body(), {"prompt_tokens": 1}, "plain", "stop"

    res_plain = discuss_ai.run_turn(PlainClient(), 3, topic, "", root=root)
    check("Q5g 没有流式接口 → streaming=False（不假装能停）",
          res_plain.get("streaming") is False, res_plain.get("streaming"))
    check("Q5g 没有流式接口也能落盘", res_plain.get("round") == before + 2,
          res_plain.get("round"))

    print("\n【6】停止：不算一轮，残片隔离")
    stop = {"on": True}
    rounds_before = discussion._read_json(js).get("rounds")
    c1 = Client()
    try:
        discuss_ai.run_turn(c1, 3, topic, "", root=root, stop_flag=lambda: stop["on"])
    except discuss_ai.DiscussAIError as e:
        check("Q6a 一开始就停 → stopped", e.kind == "stopped", e.kind)
    else:
        check("Q6a 一开始就停 → stopped", False, "没有抛错")
    check("Q6b 停下不写讨论稿", discussion._read_json(js).get("rounds") == rounds_before)
    check("Q6b 停下一轮都没发起", c1.calls == 0, c1.calls)

    # 中途停：第一块之后置位（用 timing 在第 5 次检查时置位）
    ticks = {"n": 0}
    stop2 = {"on": False}

    def tick():
        ticks["n"] += 1
        if ticks["n"] >= 5:
            stop2["on"] = True

    try:
        discuss_ai.run_turn(Client(timing=tick), 3, topic, "", root=root,
                            stop_flag=lambda: stop2["on"])
    except discuss_ai.DiscussAIError as e:
        check("Q6c 中途停 → stopped", e.kind == "stopped", e.kind)
        check("Q6c 残片里说明了原因", "作者停下" in Path(e.extra.get("truncated_path", "")).read_text(encoding="utf-8")
              if e.extra.get("truncated_path") else False, e.extra)
    else:
        check("Q6c 中途停 → stopped", False, "没有抛错")
    check("Q6d 中途停也不写讨论稿", discussion._read_json(js).get("rounds") == rounds_before,
          discussion._read_json(js).get("rounds"))

    print("\n【7】截断：更不算一轮")
    try:
        discuss_ai.run_turn(Client(finish="length"), 3, topic, "", root=root)
    except discuss_ai.DiscussAIError as e:
        check("Q7a finish=length → truncated", e.kind == "truncated", e.kind)
        tp = e.extra.get("truncated_path") or ""
        check("Q7a 残片落盘", bool(tp) and Path(tp).exists(), tp)
        check("Q7a 残片在 truncated 目录", "truncated" in tp.replace("\\", "/"), tp)
    else:
        check("Q7a finish=length → truncated", False, "没有抛错")
    check("Q7b 截断不写讨论稿", discussion._read_json(js).get("rounds") == rounds_before,
          discussion._read_json(js).get("rounds"))

    print("\n【8】接线：模板 / 路由 / 分发器 / 假客户端")
    tpl = ROOT / "prompts" / "discuss_ai.md"
    check("Q8a 讨论提示词模板存在", tpl.exists(), tpl)
    check("Q8a 模板写明 evidence 硬要求", "evidence" in tpl.read_text(encoding="utf-8"))
    dom = (ROOT / "scripts" / "nf_api_domains" / "discuss_thread.py").read_text(encoding="utf-8")
    check("Q8b 域模块注册了 ai_turn 路由",
          '"/discuss/ai_turn", handle_discuss_ai_turn' in dom, dom[-400:])
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("Q8c 分发器有 ai_turn 分支", 'p == "/discuss/ai_turn"' in api_src)
    check("Q8c 分支走域模块", "dom_thread.handle_discuss_ai_turn" in api_src)
    fake = (ROOT / "scripts" / "utils" / "fake_client.py").read_text(encoding="utf-8")
    check("Q8d 假客户端有流式接口（否则停止测不到）",
          "def _post_chat_stream" in fake)
    check("Q8d 假客户端认识讨论字段", "'\"stance\"' in blob" in fake)

    shutil.rmtree(root, ignore_errors=True)
    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
