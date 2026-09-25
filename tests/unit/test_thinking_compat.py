# -*- coding: utf-8 -*-
"""思考模型兼容自检（任务1：glm-5.1 thinking 模式破坏性变更）。

三部分：
A. 离线单测：名单匹配 / 关闭思考片段注入 / 候选自动切换与降级 / reasoning_content 兜底
   （stub 拦截 _request_json 与 urlopen，零网络、零费用）
B. 真实探针：直连 TokenHub glm-5.1，验证「不关思考 → content 空」与
   「关闭思考 → content 非空」，并记录 reasoning_effort 被平台拒收的事实（少量 token）
C. 集成：make_client 从 config 读到名单/片段，_post_chat 返回的 content 非空

用法：python tests/test_thinking_compat.py [--offline]
     --offline 跳过真实探针（只跑离线单测）
"""
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # noqa: E402
from utils import llm_client  # noqa: E402
from utils.llm_client import OpenAICompatClient, make_client  # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def check_skip(name, reason=""):
    """跳过（**不是失败**）：断言无从执行，例如缺少密钥/外部服务。

    区分 SKIP 与 FAIL 很重要：把「未执行」报成「失败」会让真正的回归信号被淹没，
    也会诱导人用错误手段（比如提交密钥）去消灭红色。
    """
    SKIP.append(name)
    print("  [SKIP] %s%s" % (name, ("  → " + str(reason)[:240]) if reason else ""))


CFG = yaml.safe_load((ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))
PROV = CFG["providers"]["tokenhub"]
MODELS = PROV["disable_thinking_models"]
SNIPPETS = [dict(s) for s in PROV["disable_thinking_payloads"]]
GLM_OFF = {"thinking": {"type": "disabled"}}


def fake_client(model="glm-5.1", **kw):
    kw.setdefault("disable_thinking_models", MODELS)
    kw.setdefault("disable_thinking_payloads", SNIPPETS)
    return OpenAICompatClient(model=model, base_url="http://127.0.0.1:1/v1",
                              api_key="sk-test", retries=3, **kw)


def http400(msg):
    return urllib.error.HTTPError("u", 400, "Bad Request", {},
                                  io.BytesIO(json.dumps({"error": {"message": msg}},
                                                       ensure_ascii=False).encode("utf-8")))


# ---------------------------------------------------------------- A. 离线单测

def case_matching():
    print("\n=== A1. 名单匹配（配置驱动，代码零硬编码模型名）===")
    c = fake_client("glm-5.1")
    check("精确命中", c._thinking_disabled("glm-5.1"))
    check("前缀命中（名单 glm-5 命中 glm-5.1-2026）",
          c._thinking_disabled("glm-5.1-2026"))
    check("大小写不敏感", c._thinking_disabled("GLM-5.1"))
    check("kimi-k2.5 命中", c._thinking_disabled("kimi-k2.5"))
    check("非名单模型不误伤（deepseek-v4-pro）", not c._thinking_disabled("deepseek-v4-pro"))
    check("空名单时全部不命中（纯自适应模式）",
          not OpenAICompatClient(model="glm-5.1", base_url="x", api_key="k",
                                 disable_thinking_models=[])._thinking_disabled("glm-5.1"))


def case_payload_injection():
    print("\n=== A2. payload 注入「关闭思考」片段 ===")
    c = fake_client("glm-5.1")
    p = {"model": "glm-5.1", "messages": []}
    inj = c._apply_no_thinking(p, "glm-5.1")
    check("命中名单 → 注入首选片段 thinking.type=disabled",
          inj == GLM_OFF and p.get("thinking") == {"type": "disabled"}, (inj, p))
    p2 = {"model": "deepseek-v4-pro", "messages": []}
    check("未命中名单 → 不注入",
          c._apply_no_thinking(p2, "deepseek-v4-pro") is None
          and not OpenAICompatClient._thinking_keys_present(p2), p2)
    p3 = {"model": "glm-5.1", "messages": [], "reasoning_effort": "high"}
    check("调用方已显式设置思考参数 → 不覆盖",
          c._apply_no_thinking(p3, "glm-5.1") is None and p3["reasoning_effort"] == "high", p3)
    c2 = fake_client("deepseek-v4-pro", fallback_models=["glm-5-turbo"])
    check("fallback 模型逐个判定（deepseek 不注入 / glm-5-turbo 注入）",
          (not c2._thinking_disabled("deepseek-v4-pro")) and c2._thinking_disabled("glm-5-turbo"))
    c3 = OpenAICompatClient(model="glm-5.1", base_url="x", api_key="k",
                            disable_thinking_models=["glm-5.1"],
                            disable_thinking_payloads=[{"enable_thinking": False}])
    p4 = {}
    check("候选片段可被 config 覆盖（换供应商只改 yaml）",
          c3._apply_no_thinking(p4, "glm-5.1") == {"enable_thinking": False}, p4)
    check("force=True 可绕过名单（自动探测用）",
          fake_client("unknown-model")._apply_no_thinking({}, "unknown-model", force=True)
          == GLM_OFF)


def case_offline_flow():
    print("\n=== A3. 请求链路：注入生效 + 空 content 自适应（stub，零网络）===")
    seen = []

    def stub_ok(payload):
        seen.append(json.loads(json.dumps(payload)))
        if "thinking" not in payload:
            # 模拟 thinking 模式：content 空，正文进 reasoning_content
            return {"choices": [{"message": {"content": "",
                                             "reasoning_content": "让我想想…\n===FILE: data/x.md===\n正文\n===END==="}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2}, "model": payload["model"]}
        return {"choices": [{"message": {"content": "===FILE: data/x.md===\n正文\n===END==="}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2}, "model": payload["model"]}

    c = fake_client("glm-5.1")
    c._request_json = stub_ok
    text, usage, model, _ = c._post_chat([{"role": "user", "content": "hi"}])
    check("名单模型首个请求就带 thinking.type=disabled",
          len(seen) == 1 and seen[0].get("thinking") == {"type": "disabled"}, seen)
    check("返回正文非空", bool(text.strip()) and "正文" in text, text[:60])
    check("usage 正常回传", usage.get("completion_tokens") == 2, usage)

    # 未在名单里的思考模型 → 自动探测并重试
    seen2 = []

    def stub_adaptive(payload):
        seen2.append(json.loads(json.dumps(payload)))
        if not OpenAICompatClient._thinking_keys_present(payload):
            return {"choices": [{"message": {"content": "", "reasoning_content": "先想想再答"}}],
                    "usage": {}, "model": payload["model"]}
        return {"choices": [{"message": {"content": "===FILE: data/y.md===\n答复\n===END==="}}],
                "usage": {}, "model": payload["model"]}

    c2 = OpenAICompatClient(model="unknown-thinker-x", base_url="http://x/v1", api_key="k",
                            retries=3, disable_thinking_models=[])
    c2._request_json = stub_adaptive
    text2, _, _, _ = c2._post_chat([{"role": "user", "content": "hi"}])
    check("未列名单的思考模型：先不带参数 → 空 content → 自动注入关闭思考后重试",
          len(seen2) == 2 and not OpenAICompatClient._thinking_keys_present(seen2[0])
          and seen2[1].get("thinking") == {"type": "disabled"}, seen2)
    check("重试后拿到正文", "答复" in text2, text2[:60])

    # 连重试都只有 reasoning_content → 兜底取用
    def stub_salvage(payload):
        return {"choices": [{"message": {"content": "",
                                         "reasoning_content": "琢磨一下…\n===FILE: data/z.md===\n兜底\n===END==="}}],
                "usage": {}, "model": payload["model"]}

    c3 = OpenAICompatClient(model="unknown-thinker-x", base_url="http://x/v1", api_key="k",
                            retries=1, disable_thinking_models=[])
    c3._request_json = stub_salvage
    text3, _, _, _ = c3._post_chat([{"role": "user", "content": "hi"}])
    check("始终空 content → 从 reasoning_content 兜底提取（不再是空白）",
          "兜底" in text3 and "琢磨一下" not in text3, text3[:80])
    check("_salvage_answer 只取协议块之后",
          OpenAICompatClient._salvage_answer("思考A\n===FILE: p===\nX\n===END===").startswith("===FILE:"))

    # 生产真实形态（2026-09-23 补）：写作是**纯正文**，reasoning_content 里没有协议块。
    # 原实现此时返回整段思考 → 思考被当正文写进小说（实测 minimax-m2.7 复现）。
    # 上面那条 fixture 恰好带 ===FILE: 标记，所以旧断言永远通过 —— 典型的
    # 「测了生产中不走的路」。这两条钉住无标记时必须**丢弃**。
    def stub_pure_thinking(payload):
        return {"choices": [{"message": {"content": "",
                                         "reasoning_content": 'The user says "第 2 次"，我先理解一下…'}}],
                "usage": {}, "model": payload["model"]}

    c4 = OpenAICompatClient(model="unknown-thinker-y", base_url="http://x/v1", api_key="k",
                            retries=1, disable_thinking_models=[])
    c4._request_json = stub_pure_thinking
    text4, _, _, _ = c4._post_chat([{"role": "user", "content": "hi"}])
    check("生产形态（无协议块）→ 思考被丢弃，正文为空（不会写进小说）",
          text4 == "", repr(text4[:80]))
    check("_salvage_answer 无协议标记 → 返回空（不返回整段思考）",
          OpenAICompatClient._salvage_answer('The user says "x"…纯思考残片') == "")


def case_param_rejected():
    print("\n=== A4. provider 拒绝关闭思考参数 → 自动换候选 / 降级（不卡死）===")
    seen = []

    def stub_reject_thinking(payload):
        seen.append(json.loads(json.dumps(payload)))
        if "thinking" in payload:
            raise http400("Unrecognized request argument supplied: thinking")
        return {"choices": [{"message": {"content": "ok"}}], "usage": {}, "model": payload["model"]}

    c = fake_client("glm-5.1")
    c._request_json = stub_reject_thinking
    text, _, _, _ = c._post_chat([{"role": "user", "content": "hi"}])
    check("被拒 → 自动换下一个候选（thinking → reasoning_effort=none）并成功",
          text == "ok" and "thinking" in seen[0] and seen[1].get("reasoning_effort") == "none",
          [sorted(s.keys()) for s in seen])
    check("可用候选被锁定复用（后续调用不再试探）",
          c._chosen_thinking_snippet == {"reasoning_effort": "none"}, c._chosen_thinking_snippet)

    seen2 = []

    def stub_reject_all(payload):
        seen2.append(json.loads(json.dumps(payload)))
        if OpenAICompatClient._thinking_keys_present(payload):
            raise http400("unsupported parameter: extra_forbidden")
        return {"choices": [{"message": {"content": "plain-ok"}}], "usage": {}, "model": payload["model"]}

    c2 = fake_client("glm-5.1")
    c2._request_json = stub_reject_all
    text2, _, _, _ = c2._post_chat([{"role": "user", "content": "hi"}])
    check("候选全部被拒 → 不再注入仍能拿到正文（降级不卡死）",
          text2 == "plain-ok" and not OpenAICompatClient._thinking_keys_present(seen2[-1]),
          [sorted(s.keys()) for s in seen2])
    c3 = fake_client("glm-5.1")
    check("无关 400 不被误判为「拒绝思考参数」",
          not c3._reject_thinking_snippet(GLM_OFF, 400, "invalid api key"))


def case_stream():
    print("\n=== A5. 流式链路（stub urlopen）===")
    captured = []

    class _Resp:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def read(self, n=1024):
            return self.chunks.pop(0) if self.chunks else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def sse(*deltas):
        out = []
        for d in deltas:
            out.append(("data: " + json.dumps({"choices": [{"delta": d}]},
                                              ensure_ascii=False) + "\n\n").encode("utf-8"))
        out.append(b"data: [DONE]\n\n")
        return out

    orig = urllib.request.urlopen

    def fake_urlopen(chunks, state=None, seq=None):
        def _f(req, timeout=None):
            captured.append(json.loads(req.data.decode("utf-8")))
            if seq is not None:
                i = min(state["i"], len(seq) - 1)
                state["i"] += 1
                return _Resp(list(seq[i]))
            return _Resp(list(chunks))
        return _f

    # 5-1 名单模型：直接带关闭思考片段，content 正常流出
    urllib.request.urlopen = fake_urlopen(sse({"content": "正"}, {"content": "文"}))
    try:
        c = fake_client("glm-5.1")
        got = []
        text, _, _, _ = c._post_chat_stream([{"role": "user", "content": "hi"}], on_chunk=got.append)
    finally:
        urllib.request.urlopen = orig
    check("流式：注入 thinking.type=disabled",
          captured[0].get("thinking") == {"type": "disabled"}, captured[0])
    check("流式：正文回流并返回", "".join(got) == "正文" and text == "正文", (got, text))

    # 5-2 未列名单 + 只回 reasoning_content：第一次空 → 自动注入后重试 → 第二次有正文
    state = {"i": 0}
    seq = [sse({"reasoning_content": "想想想"}), sse({"content": "答复正文"})]
    urllib.request.urlopen = fake_urlopen(None, state, seq)
    try:
        c2 = OpenAICompatClient(model="unknown-thinker-x", base_url="http://x/v1", api_key="k",
                                retries=3, disable_thinking_models=[])
        got2 = []
        text2, _, _, _ = c2._post_chat_stream([{"role": "user", "content": "hi"}], on_chunk=got2.append)
    finally:
        urllib.request.urlopen = orig
    check("流式：空 content → 自动注入关闭思考后重试（第二次带参数）",
          len(captured) >= 2 and captured[-1].get("thinking") == {"type": "disabled"}, captured[-1])
    check("流式：重试后正文非空", "答复正文" in text2, (got2, text2))
    check("流式：重试成功时不会把思考内容误当正文回调（避免 GUI 显示思考）",
          all("想想想" not in g for g in got2), got2)


# ---------------------------------------------------------------- B/C. 真实探针

def live_raw(base_url, key, model, extra=None, max_tokens=600):
    payload = {"model": model, "max_tokens": max_tokens,
               "messages": [{"role": "user",
                             "content": "请写一段 80 字左右的雨夜场景描写，不要解释，直接给正文。"}]}
    if extra:
        payload.update(extra)
    req = urllib.request.Request(base_url + "/chat/completions",
                                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                headers={"Content-Type": "application/json",
                                         "Authorization": "Bearer " + key},
                                method="POST")
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def case_live():
    print("\n=== B. 真实探针：TokenHub glm-5.1 ===")
    llm_client._load_env_file(str(ROOT / ".env"))
    import os
    key = os.environ.get(PROV.get("api_key_env", ""), "")
    base = os.environ.get(PROV.get("base_url_env", "") or "") or PROV["base_url"]
    model = CFG["model"]["default"]["id"]
    print("  key=%s base=%s model=%s" % ((key[:3] + "***" + key[-4:]) if len(key) > 8 else "(缺)",
                                         base, model))
    if not key:
        # ⚠️ 这里必须是 **SKIP**，不能是 FAIL。
        #
        # 密钥不入版本控制是**正确行为**（红线：API Key 绝不进 git）。
        # 但曾经这里判 FAIL，导致两个坏后果：
        #   ① 干净检出 / CI 上必然红 —— 红的是「没配密钥」而非代码缺陷，
        #      真正的回归信号被淹没；
        #   ② 更糟：它**激励错误行为** —— 让人为了变绿而把 .env 提交进去。
        # 探针的价值在于「有密钥时验证真实行为」，无密钥时它本就无从验证，
        # 那不是失败，是未执行。
        check_skip("真实探针（需 TOKENHUB_API_KEY，未配置则跳过）",
                   f"未找到 {PROV.get('api_key_env')}（密钥不入库，属预期）")
        return

    def probe(extra):
        try:
            d = live_raw(base, key, model, extra)
        except urllib.error.HTTPError as e:
            return None, "", "", "HTTP %d %s" % (e.code,
                                                 e.read().decode("utf-8", "replace")[:200])
        m = ((d.get("choices") or [{}])[0].get("message") or {})
        det = (d.get("usage") or {}).get("completion_tokens_details") or {}
        return d, (m.get("content") or ""), (m.get("reasoning_content") or ""), ("reasoning_tokens=%s"
                                                                               % det.get("reasoning_tokens"))

    # ── 探针 1：不关思考（记录事实，不做硬断言）─────────────────────────────
    # 历史背景：早期 glm-5.x 在 TokenHub 上不关思考时 content 为空、正文全挤进
    # reasoning_content。这是本模块要防的缺陷。但供应商上游会变 —— 若某天它默认
    # 就正常把正文放 content，那么"content 必为空"这条硬断言反而会误报失败。
    # 因此这里只**记录观测事实**并打印，供人判断上游行为是否变化；真正必须守住
    # 的不变量是「关思考后 content 非空」（探针 2），那才与本项目的修复目的相关。
    d1, c1, r1, note1 = probe(None)
    print("  [默认/不关思考] content=%d 字, reasoning_content=%d 字 (%s)" % (len(c1), len(r1), note1))
    if note1.startswith("HTTP"):
        print("    → 上游返回错误：%s" % note1[:160])
    elif len(c1.strip()) == 0:
        print("    → 观测：content 为空（正是历史缺陷形态；修复依赖自动注入兜底）")
    else:
        print("    → 观测：上游当前已默认把正文放入 content（历史缺陷形态未复现）")
    check("探针1 拿到了响应（HTTP 错误或正常 JSON 均算执行成功）",
          d1 is not None or note1.startswith("HTTP"), note1[:160])

    # ── 探针 2：关闭思考（关键不变量，保留硬断言）──────────────────────────
    _, c2, r2, note2 = probe(GLM_OFF)
    print("  [thinking=disabled] content=%d 字 (%s)" % (len(c2), note2))
    check("修复有效：关闭思考后 content 非空（正文落地）",
          len(c2.strip()) > 0, "content=%r" % c2[:60])

    # ── 探针 3：reasoning_effort=none（记录事实，不做硬断言）────────────────
    # 历史背景：TokenHub 曾以 HTTP 400 拒收该参数，故候选顺序把 GLM 原生 thinking
    # 开关排在前。若上游某天开始接受它，400 断言同样会误报 —— 这里只记录结果。
    d3, c3, r3, note3 = probe({"reasoning_effort": "none"})
    print("  [reasoning_effort=none] → %s" % note3[:110])
    if note3.startswith("HTTP 400"):
        print("    → 观测：上游仍拒收该参数（候选排序依据成立）")
    elif note3.startswith("HTTP"):
        print("    → 观测：上游返回其它错误码 %s，需人工确认语义" % note3[:80])
    else:
        print("    → 观测：上游已接受该参数（候选排序可考虑调整，非缺陷）")
    check("探针3 拿到了响应（HTTP 错误或正常 JSON 均算执行成功）",
          d3 is not None or note3.startswith("HTTP"), note3[:160])

    print("\n=== C. 集成：make_client + _post_chat（走 config 名单）===")
    client = make_client(CFG, "default", verbose=False)
    check("make_client 已载入名单与候选片段",
          client.disable_thinking_models == MODELS and client.disable_thinking_payloads == SNIPPETS,
          (client.disable_thinking_models, client.disable_thinking_payloads))
    text, usage, used, _ = client._post_chat(
        [{"role": "user", "content": "请写一段 60 字左右的雨夜场景描写，直接给正文。"}],
        max_tokens=600)
    check("流水线调用点拿到非空 content（本次修复的最终目的）",
          bool(text.strip()), "content=%r model=%s usage=%s" % (text[:60], used, usage))
    check("用量回传正常（成本可统计）", isinstance(usage.get("prompt_tokens"), int), usage)


def case_cache_field_parse():
    """缓存命中字段解析（2026-09-23 修，防回归锚）。

    修前的 bug：只读 `usage["cache_read_tokens"]` —— 该键在 OpenAI / DeepSeek /
    Anthropic **任何一家都不存在**，于是命中数恒为 0。而上面那条「用量回传正常」
    只断言了 prompt_tokens 是 int，所以这个错误长期零告警：**测试全绿、生产静默失效**。
    这些断言把各家的真实字段形态钉住，谁再改回旧键就立刻红。
    """
    print("\n[D] 缓存命中字段解析（含生产实测形态）")
    f = llm_client.extract_cache_read_tokens

    check("OpenAI / TokenHub：prompt_tokens_details.cached_tokens",
          f({"prompt_tokens": 885, "prompt_tokens_details": {"cached_tokens": 512}}) == 512,
          "本机 2026-09-23 实测返回形态")
    check("DeepSeek 官方：prompt_cache_hit_tokens",
          f({"prompt_tokens": 100, "prompt_cache_hit_tokens": 80}) == 80)
    check("Anthropic：cache_read_input_tokens",
          f({"prompt_tokens": 100, "cache_read_input_tokens": 60}) == 60)
    check("兼容网关：顶层 cache_read_tokens",
          f({"cache_read_tokens": 42}) == 42)
    check("无缓存字段 → 0（未命中，不是错误）",
          f({"prompt_tokens": 10}) == 0)
    check("cached_tokens=0 → 0（零值不能被当成缺失）",
          f({"prompt_tokens_details": {"cached_tokens": 0}}) == 0)
    check("异常输入不抛（None / str / list）",
          f(None) == 0 and f("x") == 0 and f([]) == 0)
    check("优先级：prompt_tokens_details 胜过同名顶层键",
          f({"prompt_tokens_details": {"cached_tokens": 7}, "cached_tokens": 99}) == 7)


def case_truncation():
    """截断契约（finish_reason=length）—— 2026-09-23 新增。

    背景：思考模型会把输出预算耗在 reasoning 上，正文被截断后**静默落盘** = 半截章节
    被当成品。实测 max_tokens=4000 时思考 10521 字、正文仅 466 字即被截断。
    现在：截断 → 不落盘 + 非零退出码（项目统一以 exit_code != 0 判失败）。
    """
    print("\n=== E. 截断契约（finish_reason=length）===")
    import os
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="nf_trunc_"))

    def make_task(name, out_name):
        task = tmp / name
        task.write_text("## 输出\n写入: " + str(tmp / out_name) + "\n", encoding="utf-8")
        return task, tmp / out_name

    class TruncLC(OpenAICompatClient):
        def _post_chat(self, messages, temperature=None, max_tokens=None):
            return "\u534a\u622a\u6b63\u6587", {"prompt_tokens": 100, "completion_tokens": 4000}, "m", "length"

    class OkLC(OpenAICompatClient):
        out_path = None

        def _post_chat(self, messages, temperature=None, max_tokens=None):
            # 正常路径的文本必须带协议块，否则 _apply_ops 不知道写到哪（会 WARN 跳过）
            return ("===FILE: " + str(OkLC.out_path) + "===\n\u5b8c\u6574\u6b63\u6587\n===END===",
                    {"prompt_tokens": 100, "completion_tokens": 50}, "m", "stop")

    old_cwd = os.getcwd()
    os.chdir(tmp)          # 让 _save_truncated 落在临时目录，真实 data/ 零污染
    try:
        task1, out1 = make_task("t1.md", "ch1.md")
        res1 = TruncLC(model="m", base_url="http://x/v1", api_key="k").run_task(task1)
        saved = list(Path("data/state/truncated").glob("*.txt"))

        task2, out2 = make_task("t2.md", "ch2.md")
        OkLC.out_path = out2
        res2 = OkLC(model="m", base_url="http://x/v1", api_key="k").run_task(task2)
    finally:
        os.chdir(old_cwd)

    check("截断 → exit_code=2（上层据此判失败）", res1.get("exit_code") == 2, res1.get("exit_code"))
    check("截断 → truncated=True", res1.get("truncated") is True)
    check("截断 → **不落盘**（半截正文不冒充成品）", not out1.exists(), str(out1))
    check("截断 → error 给出可执行解释",
          "max_tokens" in (res1.get("error") or ""), (res1.get("error") or "")[:90])
    check("截断 → 残缺文本被隔离存放（不是直接丢，可诊断）", len(saved) >= 1, [p.name for p in saved])

    check("对照：finish_reason=stop → 正常落盘且 exit_code=0（没有误伤）",
          res2.get("exit_code") == 0 and out2.exists(), str(res2.get("exit_code")))
    check("对照：正常路径 finish_reasons 也带回（可观测）",
          res2.get("finish_reasons") == ["stop"], res2.get("finish_reasons"))


if __name__ == "__main__":
    print("=" * 70)
    print("思考模型兼容自检（任务1）")
    print("=" * 70)
    for fn in (case_matching, case_payload_injection, case_offline_flow,
               case_param_rejected, case_stream, case_cache_field_parse, case_truncation):
        try:
            fn()
        except Exception as e:      # noqa: BLE001
            FAIL.append(fn.__name__)
            import traceback
            print("  [ERROR] %s → %s" % (fn.__name__, e))
            traceback.print_exc()
    if "--offline" not in sys.argv:
        try:
            case_live()
        except Exception as e:      # noqa: BLE001
            FAIL.append("case_live")
            import traceback
            print("  [ERROR] case_live → %s" % e)
            traceback.print_exc()
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败%s" % (len(PASS), len(FAIL),
          ("  / %d 跳过" % len(SKIP)) if SKIP else ""))
    if SKIP:
        print("跳过项（未执行，非失败）: " + "、".join(SKIP))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 70)
    raise SystemExit(1 if FAIL else 0)
