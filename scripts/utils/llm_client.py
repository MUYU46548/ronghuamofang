# -*- coding: utf-8 -*-
"""LLM 执行引擎抽象层（P2 引擎无关化，2026-09-09）。

目标：流水线所有调用点经 make_client() 取客户端，引擎可切换
（OpenAI 兼容直连 / 子会话执行层 / 未来可插拔）。

直连模式设计：
- 无子会话工具循环 → 任务文件「输入文件」段解析后全文内联进 prompt；
- 单请求输出上限 → 多文件输出任务按目标文件拆分请求（segment_requests）；
- 输出协议：===FILE: 路径=== ... ===END===（写入）/ ===APPEND:（追加）/ ===DELETE:（删除）；
- 模型给的路径按任务解析出的期望路径贴齐（snap_to_expected），再过写白名单（data/**、
  logs/runs.db），防越权写；
- usage 取 API 真实返回（estimated=False），单价走 cost_tracker.RATES。

思考模型兼容（2026-09-14，TokenHub glm-5.1 破坏性变更）：
- 部分模型（glm-5.x / kimi 等）默认进 thinking 模式：正文全进 reasoning_content、
  content 为空，且思考 token 会吃掉 max_tokens 预算 → 流水线所有产物空白。三道防线：
  1) 主动：模型名命中 config 的 providers.<id>.disable_thinking_models 时，
     注入「关闭思考」payload 片段，模型直接给 content（名单与片段都不硬编码在代码里）；
  2) 自适应：未列名单的思考模型，若返回空 content 且 reasoning_content 非空，
     自动注入关闭思考片段重试一次；仍为空则从 reasoning_content 兜底提取正文并告警；
  3) 降级：provider 拒绝该参数（HTTP 400/422）时自动换下一个候选片段，
     候选用完则不再注入（不因参数不兼容而卡死流水线）。
- 实测（2026-09-14 / TokenHub）：`reasoning_effort=none` 会被拒（400，只接受
  low/medium/…）；GLM 原生 `thinking={"type":"disabled"}` 有效，正文 100% 落地。
  故候选片段顺序为 thinking.type=disabled → reasoning_effort=none → enable_thinking=false，
  换供应商时改 config/system.yaml 即可，无需改代码。

实现注意：本文件源码零反斜杠字面量（BS = chr(92) 动态构造），
规避工具链 JSON 参数对反斜杠的半化陷阱。改动解析逻辑必须跑 Temp/ mock 集成测试。
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from utils.file_io import read_text, write_text
from utils import cost_tracker

BS = chr(92)          # 反斜杠字符（源码零字面量纪律）
NEWLINE = chr(10)     # 换行字符

# ---------------------------------------------------------------- 正则（动态构造）

SEP = "[" + re.escape(BS) + "/]"                       # 匹配分隔符：\ 或 /
EXT = "[.](?:md|json|txt|markdown|yaml|yml)"
EXT_MJ = "[.](?:md|json)"

# 统一路径模式：可选盘符前缀 + 路径体（排除分隔标点/冒号）+ 扩展名
PATH_RE = re.compile("(?:[A-Za-z]:" + SEP + ")?[^：（(，,、:" + NEWLINE + "]*?" + EXT)
PATH_RE_MJ = re.compile("(?:[A-Za-z]:" + SEP + ")?[^：（(，,、:" + NEWLINE + "]*?" + EXT_MJ)

INPUT_SECTION_RE = re.compile("^[#]{1,3}[^" + NEWLINE + "]*输入文件", re.M)
HEADING_RE = re.compile("^[#]{1,3}[ ]", re.M)
OP_RE = re.compile("^[^\r" + NEWLINE + "]*?=== *(FILE|APPEND|DELETE) *: *(.+?) *===*[ ]*$",
                   re.M | re.IGNORECASE)
END_RE = re.compile("^[^\r" + NEWLINE + "]*?=== *END *===*[ ]*$", re.M | re.IGNORECASE)
DIR_SPEC_RE = re.compile("下的\\s*[*][.]md")
_NOISE_RE = re.compile("[*（(，,、" + NEWLINE + " ]")


def _split_sections(body):
    heads = list(HEADING_RE.finditer(body))
    secs = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        secs.append((body[h.start():end].split(NEWLINE, 1)[0], body[h.start():end]))
    return secs


def extract_input_paths(body):
    """解析任务正文「输入文件」段的路径引用。返回 [(path_str, is_dir)]。"""
    out, seen = [], set()
    m = INPUT_SECTION_RE.search(body)
    if not m:
        return out
    end_m = HEADING_RE.search(body, m.end())
    section = body[m.end(): end_m.start() if end_m else len(body)]
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        content = line[2:]
        if DIR_SPEC_RE.search(content):
            rest = DIR_SPEC_RE.sub("", content)
            rest = re.split("[（(，,、]", rest)[0]  # 切掉尾随括号说明
            p = rest.split(": ", 1)[-1].strip().rstrip("/" + BS)
            if p and p not in seen and Path(p).is_dir():
                seen.add(p)
                out.append((p, True))
            continue
        pm = PATH_RE.search(content)
        if pm:
            p = pm.group(0).strip()
            if p not in seen:
                seen.add(p)
                out.append((p, False))
    return out


def inline_inputs(body, char_limit=220000):
    """把「输入文件」段引用的文件内容内联进正文。返回 (new_body, [缺失路径])。"""
    refs = extract_input_paths(body)
    if not refs:
        return body, []
    parts, missing, budget = [], [], 0
    for p, is_dir in refs:
        try:
            if is_dir:
                chunks = []
                for f in sorted(Path(p).glob("*.md")):
                    c = "===== 文件: " + str(f) + " =====" + NEWLINE + read_text(f)
                    if budget + len(c) > char_limit:
                        print("[llm_client] WARN 输入内联超限，跳过 " + f.name)
                        break
                    chunks.append(c)
                    budget += len(c)
                if chunks:
                    parts.append((p, NEWLINE.join(chunks)))
            else:
                if not Path(p).exists():
                    missing.append(p)
                    continue
                c = "===== 文件: " + p + " =====" + NEWLINE + read_text(p)
                if budget + len(c) > char_limit:
                    print("[llm_client] WARN 输入内联超限，跳过 " + p)
                    continue
                parts.append((p, c))
                budget += len(c)
        except Exception as e:
            missing.append(p + " (" + str(e) + ")")
    if parts:
        body = body.replace("（用 read_file 读取）",
                            "（本次调用为直连引擎，无文件工具；下列引用文件的内容已内联于文末【内联输入】区，直接使用）")
        inlined = (NEWLINE * 2).join(x[1] for x in parts)
        body = body.rstrip() + NEWLINE * 2 + "## 内联输入" + NEWLINE * 2 + inlined + NEWLINE
    if missing:
        warn = "## 输入缺失警告（未能内联，请基于已有信息继续）" + NEWLINE + \
               NEWLINE.join("- " + m for m in missing)
        body = body.rstrip() + NEWLINE * 2 + warn + NEWLINE
    return body, missing


def _strip_fence(content):
    """剥掉模型顺手包的代码围栏。"""
    c = content.strip()
    if c.startswith("```"):
        i = c.find(NEWLINE)
        c = c[i + 1:] if i >= 0 else c
        c = c.rstrip()
        if c.endswith("```"):
            c = c[:-3].rstrip()
        return c + NEWLINE
    return content


def parse_ops(text):
    """解析 FILE/APPEND/DELETE 块。返回 [(op, path, content)]。"""
    text = text.replace(BS + BS, BS)          # 模型常把 Windows 路径双反斜杠转义
    ops, pos = [], 0
    matches = []
    for m in OP_RE.finditer(text):
        end_m = END_RE.search(text, m.end())
        if end_m:
            matches.append((m, end_m))
    for m, end_m in matches:
        if m.start() < pos:
            continue
        op = m.group(1).upper()
        path = m.group(2).strip().strip('"').strip("'").strip()
        ops.append((op, path, _strip_fence(text[m.end():end_m.start()])))
        pos = end_m.end()
    return ops


def allowed_paths(paths):
    """写路径白名单：data/** 与 logs/runs.db。返回非法路径列表。"""
    bad = []
    data_root = Path("data").resolve()
    log_db = Path("logs/runs.db").resolve()
    for p in paths:
        pp = Path(p).resolve()
        if pp == log_db:
            continue
        if data_root == pp or data_root in pp.parents:
            continue
        bad.append(p)
    return bad


def snap_to_expected(got_path, expected):
    """按文件名把模型给的路径贴齐到期望路径。返回贴齐路径或 None。"""
    if Path(got_path).name == Path(expected).name:
        return expected
    return None


def _dedupe_by_name(paths):
    """同 basename 去重：保留 data/ 前缀者（裸文件名多为标题/括号噪声）。"""
    by_name = {}
    for p in paths:
        by_name.setdefault(Path(p).name, []).append(p)
    out = []
    for ps in by_name.values():
        data_ones = [x for x in ps if x.replace(BS, "/").startswith("data/")]
        out.extend(data_ones if data_ones else ps[:1])
    return sorted(out)


def segment_requests(body, expected_writes, expected_appends):
    """多文件输出任务 → 拆为逐请求指令。返回 [(sub_prompt, writes, appends)]。"""
    if len(expected_writes) <= 1:
        return [(body, list(expected_writes), list(expected_appends))]
    reqs = []
    for i, w in enumerate(expected_writes, 1):
        head = ("【本次调用仅处理第 " + str(i) + "/" + str(len(expected_writes)) +
                " 个输出文件：" + w + "。其余文件由其他调用处理，严禁输出其他文件的"
                "内容块。报告类输出只记录与本文件相关的内容。】" + NEWLINE * 2)
        reqs.append((head + body, [w], list(expected_appends)))
    return reqs


# ---------------------------------------------------------------- 客户端

SYSTEM_PROMPT = (
    "你是绒花墨坊（NovelForge）流水线的执行器，严格按用户消息中的任务说明执行，"
    "不闲聊、不复述任务。" + NEWLINE +
    "本次调用没有文件工具：任务提到的输入文件内容已内联在用户消息中；"
    "所有产物通过下述协议交回，由系统落盘。" + NEWLINE +
    "输出协议（必须严格遵守）：" + NEWLINE +
    "1. 需要写入文件时，用如下标记输出，标记外只允许极简说明：" + NEWLINE +
    "===FILE: 任务中给出的完整文件路径===" + NEWLINE +
    "（文件完整内容，Markdown/JSON 原样，禁止截断或用省略号省略）" + NEWLINE +
    "===END===" + NEWLINE +
    "2. 需要在既有文件末尾追加时用 ===APPEND: 路径===，其余同上。" + NEWLINE +
    "3. 绝不写入任务指定之外的路径；不输出 Base64；不改换编码。" + NEWLINE +
    "4. 一次调用只处理任务里被指定的那部分输出。"
)


def estimate_tokens(task_path):
    """解析失败兜底：与 api_client.estimate_tokens 同口径。"""
    try:
        size = Path(task_path).stat().st_size
    except OSError:
        size = 0
    return max(1000, int(size * 0.4)), max(500, int(size * 0.4) // 3)


# ---- 思考模型兼容（关闭思考开关，跨平台候选）----
# 各平台开关名不同：GLM 原生 thinking.type=disabled（TokenHub 实测有效）、
# 部分平台 reasoning_effort=none / enable_thinking=false。
# 具体模型名单与候选片段都来自 config（providers.<id>.disable_thinking_models /
# disable_thinking_payloads），代码里不出现任何模型名。
THINKING_KEYS = ("reasoning_effort", "thinking", "enable_thinking", "reasoning",
                 "chat_template_kwargs")
DEFAULT_DISABLE_THINKING_PAYLOADS = (
    {"thinking": {"type": "disabled"}},
    {"reasoning_effort": "none"},
    {"enable_thinking": False},
)


class OpenAICompatClient:
    """OpenAI 兼容 Chat Completions 直连客户端。

    run_task 签名与 HermesClient 兼容：调用点零改动切换引擎。
    """

    def __init__(self, model, base_url, api_key, timeout=600, retries=3,
                 provider="openai-compat", model_key=None, fallback_models=None,
                 disable_thinking_models=None, disable_thinking_payloads=None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.provider = provider
        self.model_key = model_key
        self.fallback_models = fallback_models or []
        # 配置驱动（config/system.yaml → providers.<id>.disable_thinking_models），
        # 代码内不硬编码任何模型名。
        self.disable_thinking_models = [str(x) for x in (disable_thinking_models or []) if x]
        snippets = []
        for s in (disable_thinking_payloads or DEFAULT_DISABLE_THINKING_PAYLOADS):
            if isinstance(s, dict) and s:
                snippets.append(dict(s))
        self.disable_thinking_payloads = snippets
        self._chosen_thinking_snippet = None    # 已被 provider 接受的候选（锁定复用）
        self._bad_thinking_snippets = []        # 被 provider 拒绝过的候选

    # ---- 思考模式兼容 ----

    def _thinking_disabled(self, model_name):
        """模型是否命中「禁用思考」名单：精确名 / 双向前缀（glm-5 命中 glm-5.1）。"""
        m = str(model_name or "").strip().lower()
        if not m:
            return False
        for raw in self.disable_thinking_models:
            k = str(raw or "").strip().lower()
            if not k:
                continue
            if m == k or m.startswith(k) or (len(m) >= 3 and k.startswith(m)):
                return True
        return False

    @staticmethod
    def _thinking_keys_present(payload):
        """payload 中已存在的思考相关键（调用方显式设置的，不覆盖）。"""
        return [k for k in THINKING_KEYS if k in payload]

    def _next_thinking_snippet(self):
        """取下一个可用的「关闭思考」片段（已锁定优先）。返回 dict 或 None。"""
        if self._chosen_thinking_snippet is not None:
            return dict(self._chosen_thinking_snippet)
        for s in self.disable_thinking_payloads:
            if s not in self._bad_thinking_snippets:
                self._chosen_thinking_snippet = dict(s)
                return dict(s)
        return None

    def _apply_no_thinking(self, payload, model_name, force=False):
        """注入「关闭思考」片段。返回注入的片段（dict）或 None。

        force=True 用于「自动探测」场景：模型不在名单里，但实测正在 thinking
        （content 空 + reasoning_content 非空）或刚被拒需换候选。
        """
        if self._thinking_keys_present(payload):
            return None       # 调用方显式指定了思考相关参数 → 尊重调用方
        if not force and not self._thinking_disabled(model_name):
            return None
        snippet = self._next_thinking_snippet()
        if not snippet:
            return None
        payload.update(snippet)
        return snippet

    @staticmethod
    def _thinking_keys_mentioned(body):
        """错误体里点名了哪些思考相关参数。"""
        low = (body or "").lower()
        return [k for k in THINKING_KEYS if k.lower() in low]

    def _reject_thinking_snippet(self, snippet, code, body):
        """provider 拒绝该「关闭思考」片段 → 拉黑并换下一个候选。返回是否发生了切换。"""
        if not snippet or code not in (400, 422):
            return False
        if snippet in self._bad_thinking_snippets:
            return False
        low = (body or "").lower()
        mentioned = self._thinking_keys_mentioned(body)
        if mentioned:
            if not any(k in snippet for k in mentioned):
                return False      # 报错点的是别的参数 → 不背锅
        elif not any(w in low for w in ("unsupported", "unknown", "unrecognized",
                                        "literal_error", "extra_forbidden")):
            return False
        self._bad_thinking_snippets.append(dict(snippet))
        self._chosen_thinking_snippet = None
        return True

    @staticmethod
    def _split_answer(msg):
        """从响应 message 提取 (content, thinking)。兼容 reasoning_content / reasoning 字段。"""
        if not isinstance(msg, dict):
            return "", ""
        text = msg.get("content") or ""
        thinking = msg.get("reasoning_content") or msg.get("reasoning") or ""
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        if not isinstance(thinking, str):
            thinking = "" if thinking is None else str(thinking)
        return text, thinking

    @staticmethod
    def _salvage_answer(text):
        """thinking 兜底：尽量只取「正式答复」部分（首个协议块起），去掉思考过程。"""
        for marker in ("===FILE:", "===APPEND:", "===DELETE:"):
            idx = text.find(marker)
            if idx >= 0:
                return text[idx:]
        return text

    def _request_json(self, payload):
        """单次非流式请求，返回解析后的 JSON。"""
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.api_key},
            method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _validate_creds(self):
        if not self.base_url:
            raise SystemExit("[llm_client] provider 缺 base_url（检查 config/system.yaml 的 providers）")
        if not self.api_key:
            raise SystemExit("[llm_client] 缺 API Key：请在项目 .env 设置对应 KEY（.env 不入 git）")

    def write_task(self, task_dir, name, content):
        """写入任务文件并返回路径（与 HermesClient 同构）。"""
        task_dir = Path(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / name
        write_text(path, content)
        return path

    def _post_chat(self, messages, temperature=None, max_tokens=None):
        self._validate_creds()
        models_to_try = [self.model] + list(self.fallback_models)
        last_err = None
        for model_name in models_to_try:
            payload = {"model": model_name, "messages": messages}
            if temperature is not None:
                payload["temperature"] = temperature
            if max_tokens:
                payload["max_tokens"] = max_tokens
            injected = self._apply_no_thinking(payload, model_name)
            if injected is not None:
                print("[llm_client] " + str(model_name) + " 命中 disable_thinking_models → 注入 "
                      + json.dumps(injected, ensure_ascii=False) + "（关闭思考，保证正文进 content）")
            attempt = 0
            while attempt < self.retries:
                attempt += 1
                try:
                    data = self._request_json(payload)
                    choices = data.get("choices") or []
                    text, thinking = self._split_answer(
                        (choices[0].get("message") if choices else None) or {})
                    usage = data.get("usage") or {}
                    if not text.strip() and thinking.strip():
                        # thinking 模式：正文被塞进 reasoning_content（TokenHub glm-5.x 默认行为）
                        if injected is None and attempt < self.retries:
                            injected = self._apply_no_thinking(payload, model_name, force=True)
                            if injected is not None:
                                print("[llm_client] WARN " + str(model_name) +
                                      " content 为空但 reasoning_content 非空（thinking 模式）→ 自动注入 "
                                      + json.dumps(injected, ensure_ascii=False) + " 后重试")
                                continue
                        print("[llm_client] WARN " + str(model_name) +
                              " 仅返回 reasoning_content，已兜底取用；建议把该模型加入 "
                              "config/system.yaml 的 providers." + str(self.provider) +
                              ".disable_thinking_models")
                        text = self._salvage_answer(thinking)
                    if model_name != self.model:
                        print(f"[llm_client] fallback {self.model} → {model_name} 成功")
                    return text, usage, data.get("model", model_name)
                except urllib.error.HTTPError as e:
                    body = e.read().decode("utf-8", "replace")[:500]
                    if self._reject_thinking_snippet(injected, e.code, body):
                        for k in injected:
                            payload.pop(k, None)
                        last_err = "HTTP " + str(e.code) + ": " + body
                        injected = self._apply_no_thinking(payload, model_name, force=True)
                        print("[llm_client] " + str(model_name) + " 拒绝关闭思考参数，改用 "
                              + (json.dumps(injected, ensure_ascii=False) if injected
                                 else "不注入（候选已用尽，依赖空 content 兜底）")
                              + " 重试: " + body[:120])
                        attempt -= 1          # 参数自适应不计入重试次数（不重复烧预算）
                        continue
                    if e.code in (400, 401, 403, 404):
                        raise RuntimeError(
                            "[llm_client] 请求被拒 HTTP " + str(e.code) + ": " + body) from e
                    last_err = "HTTP " + str(e.code) + ": " + body
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                    last_err = repr(e)
                print("[llm_client] 请求失败（第 " + str(attempt) + "/" +
                      str(self.retries) + " 次），重试中: " + last_err)
                time.sleep(2 ** attempt)
            # 当前模型重试耗尽，尝试下一个 fallback
            if model_name != models_to_try[-1]:
                print(f"[llm_client] {model_name} 重试耗尽，尝试 fallback: {models_to_try[models_to_try.index(model_name)+1]}")
        raise RuntimeError("[llm_client] 重试耗尽（含 fallback）: " + str(last_err))

    def _post_chat_stream(self, messages, temperature=None, max_tokens=None, on_chunk=None, stop_flag=None):
        """流式请求：逐 token 产出文本块。on_chunk(text) 每次收到新内容时调用。
        stop_flag: callable，返回 True 时中止流。

        thinking 模式同理：命中名单注入「关闭思考」片段；未命中而只收到
        reasoning_content 时自动注入后重试，仍为空则把思考内容兜底回调（避免 GUI 空白）。
        """
        self._validate_creds()
        payload = {"model": self.model, "messages": messages, "stream": True}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens:
            payload["max_tokens"] = max_tokens
        injected = self._apply_no_thinking(payload, self.model)
        if injected is not None:
            print("[llm_client] " + str(self.model) + " 命中 disable_thinking_models → 注入 "
                  + json.dumps(injected, ensure_ascii=False) + "（关闭思考，流式）")
        last_err = None
        attempt = 0
        while attempt < self.retries:
            attempt += 1
            content_parts, thinking_parts = [], []
            stopped = False
            try:
                req = urllib.request.Request(
                    self.base_url + "/chat/completions",
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + self.api_key},
                    method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    buf = ""
                    done = False
                    while not done:
                        if stop_flag and stop_flag():
                            stopped = True
                            break
                        raw = resp.read(1024).decode("utf-8", "replace")
                        if not raw:
                            break
                        buf += raw
                        while NEWLINE + NEWLINE in buf:
                            line, buf = buf.split(NEWLINE + NEWLINE, 1)
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                done = True
                                break
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            choices = chunk.get("choices") or []
                            if not choices:
                                continue
                            delta = choices[0].get("delta") or {}
                            piece = delta.get("content") or ""
                            if piece:
                                content_parts.append(piece)
                                if on_chunk:
                                    on_chunk(piece)
                                continue
                            rpiece = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            if rpiece:
                                thinking_parts.append(rpiece)
                full = "".join(content_parts)
                if stopped:
                    return full, {}, self.model
                if not full.strip() and thinking_parts:
                    if injected is None and attempt < self.retries:
                        injected = self._apply_no_thinking(payload, self.model, force=True)
                        if injected is not None:
                            print("[llm_client] WARN " + str(self.model) +
                                  " 流式 content 为空但收到 reasoning_content（thinking 模式）"
                                  " → 自动注入 " + json.dumps(injected, ensure_ascii=False) + " 后重试")
                            continue
                    salvaged = self._salvage_answer("".join(thinking_parts))
                    print("[llm_client] WARN " + str(self.model) +
                          " 流式仅返回 reasoning_content，已兜底回调；建议把该模型加入 "
                          "config/system.yaml 的 providers." + str(self.provider) +
                          ".disable_thinking_models")
                    if on_chunk and salvaged:
                        on_chunk(salvaged)
                    return salvaged, {}, self.model
                return full, {}, self.model
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:500]
                if self._reject_thinking_snippet(injected, e.code, body):
                    for k in injected:
                        payload.pop(k, None)
                    last_err = "HTTP " + str(e.code) + ": " + body
                    injected = self._apply_no_thinking(payload, self.model, force=True)
                    print("[llm_client] " + str(self.model) + " 拒绝关闭思考参数（流式），改用 "
                          + (json.dumps(injected, ensure_ascii=False) if injected
                             else "不注入（候选已用尽，依赖空 content 兜底）")
                          + " 重试: " + body[:120])
                    attempt -= 1
                    continue
                if e.code in (400, 401, 403, 404):
                    raise RuntimeError(
                        "[llm_client] 请求被拒 HTTP " + str(e.code) + ": " + body) from e
                last_err = "HTTP " + str(e.code) + ": " + body
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = repr(e)
            print("[llm_client] 流式请求失败（第 " + str(attempt) + "/" +
                  str(self.retries) + " 次），重试中: " + last_err)
            time.sleep(2 ** attempt)
        raise RuntimeError("[llm_client] 流式重试耗尽: " + str(last_err))

    def _expected_outputs(self, body):
        """提取期望写入路径：只扫任务正文（排除输入段/内联输入区/缺失警告区）。"""
        head = body.split("## 内联输入", 1)[0].split("## 输入缺失警告", 1)[0]
        input_paths = {p for p, _ in extract_input_paths(head)}
        appends, writes = [], []
        for m in PATH_RE_MJ.finditer(head):
            p = m.group(0).strip()
            if _NOISE_RE.search(p):  # 排除「xx/ 下的 *.md」与括号说明里的裸词
                continue
            if m.start() > 0 and head[m.start() - 1] in "（(":
                continue  # 路径在括号内（如 ## 输出格式（setting.json，…））→ 噪声
            ctx = head[max(0, m.start() - 12):m.start()]
            bucket = appends if "追加到" in ctx else writes
            if p not in bucket and p not in input_paths:
                bucket.append(p)
        return (_dedupe_by_name(sorted(set(writes))),
                _dedupe_by_name(sorted(set(appends))))

    def _apply_ops(self, text, expected_writes, expected_appends):
        ops = parse_ops(text)
        if not ops:
            print("[llm_client] WARN 模型输出不含任何 FILE/APPEND 块")
            return
        for op, path, content in ops:
            matched = None
            pool = expected_writes + expected_appends
            for exp in pool:
                snapped = snap_to_expected(path, exp)
                if snapped:
                    matched = snapped
                    break
            if matched is None and op != "DELETE":
                if allowed_paths([path]):
                    print("[llm_client] 拒绝写入白名单外路径: " + path)
                    continue
                matched = path
            p = Path(matched)
            if op == "DELETE":
                # DELETE 也强制校验白名单：只能删除 data/** 下路径
                if allowed_paths([path]):
                    print("[llm_client] 拒绝删除白名单外路径: " + path)
                    continue
                if p.exists():
                    p.unlink()
                    print("[llm_client] 已删除 " + str(p))
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            if op == "APPEND":
                with open(p, "a", encoding="utf-8", newline=NEWLINE) as f:
                    f.write(content if content.endswith(NEWLINE) else content + NEWLINE)
            else:
                write_text(p, content)
            print("[llm_client] 已写入 (" + op + ") " + str(p) +
                  "  (" + str(len(content)) + " chars)")

    def run_task(self, task_file, workdir=None, model=None, dry_run=False):
        """执行自包含任务文件。返回 dict 与 HermesClient 同构。

        model 参数兼容保留：与配置模型不同时仅告警（直连模式换模型改 config/system.yaml）。
        设 NOVELFORGE_DEBUG=1 时把每次请求/响应原文存 data/state/llm_raw/。
        """
        if model and model != self.model:
            print("[llm_client] WARN run_task(model=" + str(model) + ") 与配置模型 " +
                  self.model + " 不同，按配置执行（换模型请改 config/system.yaml）")
        self._validate_creds()
        task_path = Path(task_file)
        body = read_text(task_path)
        new_body, missing = inline_inputs(body)
        writes, appends = self._expected_outputs(new_body)
        reqs = segment_requests(new_body, writes, appends)
        if len(writes) > 1:
            print("[llm_client] 多文件输出任务，拆分 " + str(len(reqs)) + " 个请求")

        all_text, tokens_in, tokens_out, cache_read, model_used = [], 0, 0, 0, self.model
        for sub_prompt, wr, ap in reqs:
            text, usage, mu = self._post_chat(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": sub_prompt}])
            model_used = mu
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
            cache_read += int(usage.get("cache_read_tokens") or 0)
            all_text.append(text)
            if os.environ.get("NOVELFORGE_DEBUG"):
                dump = Path("data/state/llm_raw")
                dump.mkdir(parents=True, exist_ok=True)
                name = task_path.stem + "_" + str(len(all_text)) + ".txt"
                write_text(dump / name,
                           "--- PROMPT (" + str(len(sub_prompt)) + " chars) ---\n" +
                           sub_prompt[:3000] + "\n" + "--- RESPONSE ---\n" + text)
            if not dry_run:
                self._apply_ops(text, wr, ap)

        cost_yuan = cost_tracker.estimate_cost_yuan(tokens_in, tokens_out, model_used,
                                                   cache_read=cache_read)
        return {
            "exit_code": 0,
            "stdout_tail": NEWLINE.join(all_text)[-2000:],
            "tokens": tokens_in,
            "tokens_out": tokens_out,
            "cache_read": cache_read,
            "cost_yuan": cost_yuan,
            "estimated": False,
            "provider": self.provider,
            "model": model_used,
            "model_key": self.model_key,
            "requests": len(reqs),
            "missing_inputs": missing,
        }

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None, model=None, dry_run=False):
        """流式执行自包含任务文件。on_piece(text) 逐 token 回调。
        stop_flag: callable，返回 True 时中止流。返回 dict 同 run_task。"""
        if model and model != self.model:
            print("[llm_client] WARN run_task_stream(model=" + str(model) + ") 与配置模型 " +
                  self.model + " 不同，按配置执行")
        self._validate_creds()
        task_path = Path(task_file)
        body = read_text(task_path)
        new_body, missing = inline_inputs(body)
        writes, appends = self._expected_outputs(new_body)
        reqs = segment_requests(new_body, writes, appends)
        if len(writes) > 1:
            print("[llm_client] 多文件输出任务（流式），拆分 " + str(len(reqs)) + " 个请求")

        all_text, tokens_in, tokens_out, cache_read, model_used = [], 0, 0, 0, self.model
        for sub_prompt, wr, ap in reqs:
            collected = []

            def _on_chunk(piece, _collected=collected):
                _collected.append(piece)
                on_piece(piece)

            text, usage, mu = self._post_chat_stream(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": sub_prompt}],
                on_chunk=_on_chunk, stop_flag=stop_flag)
            model_used = mu
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
            cache_read += int(usage.get("cache_read_tokens") or 0)
            full_text = "".join(collected)
            all_text.append(full_text)
            if os.environ.get("NOVELFORGE_DEBUG"):
                dump = Path("data/state/llm_raw")
                dump.mkdir(parents=True, exist_ok=True)
                name = task_path.stem + "_stream_" + str(len(all_text)) + ".txt"
                write_text(dump / name,
                           "--- PROMPT (" + str(len(sub_prompt)) + " chars) ---\n" +
                           sub_prompt[:3000] + "\n" + "--- RESPONSE ---\n" + full_text)
            if not dry_run and stop_flag and not stop_flag():
                self._apply_ops(full_text, wr, ap)

        cost_yuan = cost_tracker.estimate_cost_yuan(tokens_in, tokens_out, model_used,
                                                   cache_read=cache_read)
        return {
            "exit_code": 0,
            "stdout_tail": NEWLINE.join(all_text)[-2000:],
            "tokens": tokens_in,
            "tokens_out": tokens_out,
            "cache_read": cache_read,
            "cost_yuan": cost_yuan,
            "estimated": False,
            "provider": self.provider,
            "model": model_used,
            "model_key": self.model_key,
            "requests": len(reqs),
            "missing_inputs": missing,
            "stopped": bool(stop_flag and stop_flag()),
        }


class HermesClient:
    """Hermes 子会话客户端（原 api_client.HermesClient，保留为引擎之一）。"""

    def __init__(self, hermes_bin="hermes", timeout=900, model=None):
        self.hermes_bin = hermes_bin
        self.timeout = timeout
        self.model = model

    def run_task(self, task_file, workdir=None, model=None):
        import subprocess
        task_path = Path(task_file)
        cmd = [self.hermes_bin, "chat", "-q",
               "阅读并严格按 " + str(task_path) + " 中的指示执行全部步骤。"
               "完成后简要汇报：产物路径、校验结果、遇到的问题。"]
        eff_model = model or self.model
        if eff_model:
            cmd += ["-m", eff_model]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=self.timeout, cwd=workdir)
        from utils.api_client import parse_usage, estimate_tokens as est
        stdout = proc.stdout or ""
        t_in, t_out, cost_yuan, estimated = parse_usage(stdout)
        if estimated:
            t_in, t_out = est(task_path)
        return {"exit_code": proc.returncode, "stdout_tail": stdout[-2000:],
                "tokens": t_in, "tokens_out": t_out, "cost_yuan": cost_yuan,
                "estimated": estimated, "provider": "hermes",
                "model": eff_model or "default", "requests": 1}

    def write_task(self, task_dir, name, content):
        task_dir = Path(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / name
        write_text(path, content)
        return path

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None, model=None):
        """Hermes 引擎暂不支持真流式：降级为 run_task，结束后一次性回调。"""
        print("[llm_client] Hermes 引擎不支持流式，降级为整块返回")
        result = self.run_task(task_file, workdir=workdir, model=model)
        if result["exit_code"] == 0 and result.get("stdout_tail"):
            on_piece(result["stdout_tail"])
        return result


# ---------------------------------------------------------------- 工厂

def _load_env_file(path=".env"):
    """极简 .env 加载：仅设置尚未在环境中的键（不覆盖真实环境变量）。"""
    env_path = Path(path)
    if not env_path.exists():
        return
    try:
        raw = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def make_client(cfg, model_key="default", verbose=True):
    """按 config/system.yaml 构建客户端。model_key: default/architect/outliner/writer/checker/reviewer/polisher。"""
    _load_env_file()
    engine = (cfg or {}).get("engine", "hermes")
    mspec = ((cfg or {}).get("model") or {}).get(model_key) or {}
    if isinstance(mspec, str):
        mspec = {"provider": "hermes", "id": mspec}
    provider_id = mspec.get("provider", "hermes" if engine == "hermes" else "")
    model_id = mspec.get("id", "")
    provs = (cfg or {}).get("providers") or {}

    if engine == "hermes" or provider_id == "hermes":
        if verbose:
            print("[client] 引擎=hermes 模型=" + (model_id or "(默认)"))
        return HermesClient(model=model_id or None)

    prov = provs.get(provider_id) or {}
    ptype = prov.get("type", "openai-compat")
    if ptype != "openai-compat":
        raise SystemExit("[client] 未知 provider 类型: " + str(ptype))
    base_url = os.environ.get(prov.get("base_url_env", "") or "") or prov.get("base_url")
    api_key = os.environ.get(prov.get("api_key_env", "") or "") or ""
    if not base_url:
        raise SystemExit("[client] provider " + provider_id + " 缺 base_url（检查 config/system.yaml）")
    if not api_key:
        print("[client] WARN 未设 " + str(prov.get("api_key_env", "?")) +
              "（项目 .env，不入 git）；客户端构造成功，实际调用时才会报错")
    if verbose:
        print("[client] 引擎=direct provider=" + provider_id + " 模型=" + model_id +
              " 角色=" + model_key)
    return OpenAICompatClient(
        model=model_id, base_url=base_url, api_key=api_key,
        timeout=int(prov.get("timeout", 600)), retries=int(prov.get("retries", 3)),
        provider=provider_id, model_key=model_key, fallback_models=prov.get("fallback", []),
        disable_thinking_models=prov.get("disable_thinking_models", []),
        disable_thinking_payloads=prov.get("disable_thinking_payloads"))
