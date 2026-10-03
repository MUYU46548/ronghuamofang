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
from utils.console import ensure_utf8_stdout
# 解析层容错（件2）：`<think>` 内嵌剥离 + 从思考里捞可解析的 JSON 文档。
# 同包内复用，避免「同一判据写两遍、只修一处」（见 TESTS.md 的复制实现教训）。
from utils.validator import strip_think, extract_json

# 进程级输出安全网（幂等）：stdout 被捕获时是 cp936，而 ¥/⚠️/✅ 等字符不在 GBK 里
# → 一行日志就能 UnicodeEncodeError 打死整轮长跑。放在这里是因为**所有会跑 LLM 的
# 脚本都 import 本模块**（阶段脚本 / 精修 / 审稿 / 校对 / GUI 服务），覆盖面最广。
# 详见 utils/console.py 的实测记录。
ensure_utf8_stdout()

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
END_RE = re.compile("^[^\r" + NEWLINE + "]*?=== *END(?:[ ]+[A-Za-z][A-Za-z ]*)? *===*[ ]*$",
                    re.M | re.IGNORECASE)
DIR_SPEC_RE = re.compile("下的\\s*[*][.]md")
_NOISE_RE = re.compile("[*（(，,、" + NEWLINE + " ]")


def _split_sections(body):
    heads = list(HEADING_RE.finditer(body))
    secs = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        secs.append((body[h.start():end].split(NEWLINE, 1)[0], body[h.start():end]))
    return secs


class InputSectionFormatError(ValueError):
    """「输入文件」段里存在**路径引用但格式不被解析器接受**。

    这不是"文件缺失"（那种是 FileNotFoundError），而是**段落格式对不上**：
    路径写在了非列表行上、目录引用指向不存在的目录等。旧实现一律**静默跳过**，
    于是任务照跑、prompt 里却没有这些输入 → 模型基于缺失信息产出空壳/胡编，
    而日志里一个字的异常都没有（stage5 空稿事故的同类根因）。
    现在一律抛本异常，报错里带**行号 + 原文 + 为什么没被接受 + 该怎么写**。
    """


# 输入段里允许的两种写法（报错文案里要写清楚，别让人猜解析器的口味）
_INPUT_FORMAT_HINT = (
    "输入段每条路径必须写成**列表行**：`- 名称: <路径>`；"
    "目录引用写成 `- 名称: <目录>/ 下的 *.md` 且该目录必须存在。"
    "（解析器只认以 `- ` 开头的行，这是 stage5 空稿事故的根因）"
)


def extract_input_paths(body, diag=None):
    """解析任务正文「输入文件」段的路径引用。返回 [(path_str, is_dir)]。

    diag（可选 dict）：**诊断出口**，供调用方显式报错而不是静默返空。填入：
      {"section_found": bool, "section_line": int, "accepted": [...],
       "skipped": [{"line": 行号, "text": 原文, "path": 命中路径, "reason": 为什么没被接受}]}
    行号是**任务正文里的绝对行号**（人拿它去模板/任务文件里定位，不用自己数）。
    """
    out, seen, skipped = [], set(), []
    if isinstance(diag, dict):
        diag.update({"section_found": False, "section_line": 0,
                     "accepted": out, "skipped": skipped})
    m = INPUT_SECTION_RE.search(body)
    if not m:
        return out
    if isinstance(diag, dict):
        diag["section_found"] = True
        diag["section_line"] = body.count(NEWLINE, 0, m.start()) + 1
    end_m = HEADING_RE.search(body, m.end())
    section = body[m.end(): end_m.start() if end_m else len(body)]
    base_line = body.count(NEWLINE, 0, m.end()) + 1     # 段内首行的绝对行号
    for offset, raw in enumerate(section.splitlines()):
        lineno = base_line + offset
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("- "):
            # 非列表行：只有**含路径**才算格式错误；纯说明行（无路径）是正常的
            pm = PATH_RE.search(line)
            if pm:
                skipped.append({"line": lineno, "text": line, "path": pm.group(0).strip(),
                                "reason": "非列表行（缺 `- ` 前缀）"})
            continue
        content = line[2:]
        if DIR_SPEC_RE.search(content):
            rest = DIR_SPEC_RE.sub("", content)
            rest = re.split("[（(，,、]", rest)[0]  # 切掉尾随括号说明
            p = rest.split(": ", 1)[-1].strip().rstrip("/" + BS)
            if not p:
                skipped.append({"line": lineno, "text": line, "path": "",
                                "reason": "目录引用里没解析出路径"})
                continue
            if not Path(p).is_dir():
                # 目录不存在 → 旧实现**静默跳过**（内联 0 个文件也不说）
                skipped.append({"line": lineno, "text": line, "path": p,
                                "reason": "目录引用指向的目录不存在"})
                continue
            if p not in seen:
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


def format_input_section_error(diag, task_path=None):
    """把 extract_input_paths 的诊断渲染成**可行动**的报错文案。"""
    head = ("任务「输入文件」段有 %d 处路径引用**不能被解析**（这些输入不会被内联，"
            "模型会收不到它们）：" % len(diag.get("skipped") or []))
    lines = [head]
    for s in diag.get("skipped") or []:
        lines.append("  · 第 %d 行 —— %s：`%s`"
                     % (s.get("line"), s.get("reason"), (s.get("text") or "")[:90]))
    where = ("任务文件: " + str(task_path)) if task_path else "任务正文"
    lines.append("  %s（「输入文件」段起于第 %s 行）" % (where, diag.get("section_line")))
    lines.append("  " + _INPUT_FORMAT_HINT)
    return NEWLINE.join(lines)


def inline_inputs(body, char_limit=220000, task_path=None):
    """把「输入文件」段引用的文件内容内联进正文。返回 (new_body, [缺失路径])。

    **格式不符 = 明确报错**（P1，2026-10-03）：段落里出现「有路径、但解析器不认」的行
    （最常见的形态就是漏写 `- ` 前缀 —— stage5 空稿事故的同类根因）时，抛
    `InputSectionFormatError` 并指出**行号 + 原文 + 正确写法**，而不是静默返空。

    与既有的「输入文件缺失就抛 FileNotFoundError」是同一处置哲学：
    输入构造错了必须**当场停**，不能让模型基于空输入跑出一轮"看起来成功"的产物
    （白烧钱 + 产物不可信，两样都比报错贵）。
    """
    diag = {}
    refs = extract_input_paths(body, diag)
    if diag.get("skipped"):
        msg = format_input_section_error(diag, task_path)
        print("[llm_client] ERROR " + msg.replace(NEWLINE, NEWLINE + "[llm_client] "))
        raise InputSectionFormatError(msg)
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
    """解析 FILE/APPEND/DELETE 块。返回 [(op, path, content)]。

    ## 结束标记策略（2026-10-02 修，两处高危雷的根因）

    旧实现：**OP 后面找不到 END_RE 就把这一块静默丢弃**。而

    ① `END_RE` 只认 `===END===`，stage5 的提示词却教模型写 `===END FILE===`
       → **整批块被丢掉**，stage5 于是走「复制 raw → checked」兜底，
       「逻辑检查完成」照打，报告 `check_report.md` 根本没落盘；
    ② 模型随手漏写结束标记时同样整块丢 → `_apply_ops` 的兜底把**整段模型输出**
       （含 `===FILE:` 标记行与绝对路径）直接写进章节文件。实测
       `data/chapters/raw/01.md` 首行就是 `===FILE: E:\\...\\01.md===`。

    现改两处：`END_RE` 兼容带 tag 的写法（见其定义）；本函数在找不到 END 时
    **不再丢弃**，改用「下一个 OP 之前」当块尾并打 WARN —— 「块尾外扩」的代价
    （最多带一小段收尾说明）远小于「整块丢弃 + 兜底写全文」。
    """
    text = text.replace(BS + BS, BS)          # 模型常把 Windows 路径双反斜杠转义
    ops = []
    op_matches = list(OP_RE.finditer(text))
    for i, m in enumerate(op_matches):
        end_m = END_RE.search(text, m.end())
        next_op = (op_matches[i + 1].start() if i + 1 < len(op_matches)
                   else len(text))
        if end_m and end_m.start() < next_op:
            body_end = end_m.start()
        else:
            body_end = next_op
            print("[llm_client] WARN 协议块缺结束标记（"
                  + m.group(1).upper() + " " + m.group(2).strip()[:60]
                  + "）—— 以块尾/下一个块为界收边（旧实现会整块丢弃）")
        op = m.group(1).upper()
        path = m.group(2).strip().strip('"').strip("'").strip()
        ops.append((op, path, _strip_fence(text[m.end():body_end])))
    return ops


def strip_protocol_markers(text):
    """剥掉正文里**残留**的协议标记行（`===FILE: ...===` / `===END===`）。

    为什么需要（2026-10-02）：旧 `parse_ops` 在块没写结束标记时会整块丢弃，
    `_apply_ops` 的兜底随即把**整段模型输出**（含标记行与绝对路径）写进章节文件。
    实测 `data/chapters/raw/01.md` 首行就是
    `===FILE: E:\\CODE\\...\\01.md===` —— 既是产物污染，也是私路径泄露。

    只修解析器只能防新增；老产物里的残留会随「上一章内容 → 提示词 → 新章节」
    和「合并正文 → Word 成品」继续传播。故在**读取侧**再兜一道。
    """
    if not text:
        return text
    out = OP_RE.sub("", text)
    out = END_RE.sub("", out)
    return out.lstrip(NEWLINE)


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


def common_prefix_len(texts):
    """一组文本的**公共前缀**长度（前缀缓存命中上限，用来定位分叉点）。

    只用于诊断与自检：前缀缓存按「请求开头逐字相同」命中，所以这个数字直接
    决定 N 个子请求能不能共享缓存。**前缀里一个字符都不能变**。
    """
    items = [t or "" for t in texts]
    if not items:
        return 0
    n = min(len(t) for t in items)
    i = 0
    first = items[0]
    while i < n and all(t[i] == first[i] for t in items[1:]):
        i += 1
    return i


# 多文件任务的**统一请求头**（2026-10-03 重排，P1 缓存）。
#
# 分叉 = 独立缓存杀手：N 个子请求内容 95%+ 相同，只要有一处在前部不同，
# 前缀缓存就全灭（实测：指令头插时命中率上限 1.2%，移尾后 77.6%）。
# 所以这里定死两条纪律，并由 tests/unit/test_segment_cache_prefix.py 守：
#   ① 变的部分（第 i/N 个 + 文件路径）**只能出现在最后**；
#   ② 不变的部分（下面这段说明）必须**整段落在公共前缀里** —— 于是把常量放前面、
#      变量放最后；旧写法「第 1/3 个」打头，分叉点落在指令第 3 个字符，
#      后半段那句「严禁输出其他文件的内容块」每个子请求各成一份独立前缀。
SEGMENT_HEAD = ("【本批次共 {total} 个输出文件，本次调用**只**处理其中一个："
                "其余文件由其他调用处理，严禁输出其他文件的内容块；"
                "报告类输出只记录与本次输出相关的内容。"
                "本次输出文件（第 {index}/{total} 个）：{path}】")


def segment_requests(body, expected_writes, expected_appends):
    """多文件输出任务 → 拆为逐请求指令。返回 [(sub_prompt, writes, appends)]。

    **指令必须放在 body 之后、且变量只能在最末**（2026-09-29 随行件3 + 2026-10-03 重排）。
    原先把「仅处理第 i/N 个输出」的指令**头插**在 user 消息最前，于是 N 个子请求虽然
    共享 95%+ 的正文与内联输入，却在**首字符**就分叉 → 前缀缓存全部失效（每次都是
    全新前缀）。挪到内联输入之后，N 个子请求共享同一段前缀，只有尾部差异 →
    前缀缓存可命中（实测 deepseek-v4-flash 重排后 57.9%，重排前上限 1.2%）。

    2026-10-03（P1 缓存）：**连指令内部也统一** —— 常量说明在前、`第 i/N 个 + 路径`
    放最末，使那句「严禁输出其他文件的内容块」也落进公共前缀（旧写法白丢）。
    """
    if len(expected_writes) <= 1:
        return [(body, list(expected_writes), list(expected_appends))]
    reqs = []
    total = len(expected_writes)
    for i, w in enumerate(expected_writes, 1):
        head = SEGMENT_HEAD.format(total=total, index=i, path=w)
        reqs.append((body.rstrip() + NEWLINE * 2 + head, [w], list(expected_appends)))
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
                 disable_thinking_models=None, disable_thinking_payloads=None,
                 request_max_tokens=None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.provider = provider
        self.model_key = model_key
        self.fallback_models = fallback_models or []
        # 单请求**输出**上限（P0 止烧，config: budget.token_limit.per_request_max_tokens）。
        # 直连引擎能真的把它设进 payload["max_tokens"] → 单次生成不可能失控烧钱。
        # None/0 = 不设（沿用 provider 默认）。
        try:
            self.request_max_tokens = int(request_max_tokens or 0) or None
        except (TypeError, ValueError):
            self.request_max_tokens = None
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

    def effective_max_tokens(self, given=None):
        """本次请求实际使用的 max_tokens（单请求**输出**上限，P0 止烧）。

        取 min(调用方指定, 配置上限)；两者都没有 → None（不设，交给 provider 默认）。
        **上限优先**：调用方要 16000、配置上限 8000 → 发 8000 —— 熔断阈值不该被
        调用点的参数悄悄顶掉（否则"设了上限"只是错觉）。
        """
        caps = []
        for v in (given, self.request_max_tokens):
            try:
                n = int(v or 0)
            except (TypeError, ValueError):
                continue
            if n > 0:
                caps.append(n)
        return min(caps) if caps else None

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
        """thinking 兜底：**只取「正式协议块」或「可解析的 JSON 文档」，其余一律丢弃**。

    为什么不能在无标记时返回全文：思考过程与正文混在同一个 `reasoning_content` 里，
    文本层面**无法可靠切分**（实测 minimax-m2.7 返回 `The user says "第 2 次…` 这种纯思考残片）。
    原实现找不到标记就返回**整段**思考 → 思考被当正文写进小说（2026-09-23 实测复现）。

    现改为：找不到协议标记 → **默认返回空**。宁可让上层判失败，也不把思考当正文。

    2026-09-29（件2）增补：若剥离 `<think>` 后文本里有一个**真能 json.loads 通过**的
    JSON 文档，则把它捞回来 —— 审稿/校对的 JSON 结果常整段落进 reasoning_content，
    而协议块兜底管不到（审稿 JSON 断裂事故的根因）。该分支要求「真的能解析」，
    所以**纯散文的思考永远命不中**：写作阶段（无协议块、非 JSON）行为与修复前一致。
    """
        for marker in ("===FILE:", "===APPEND:", "===DELETE:"):
            idx = text.find(marker)
            if idx >= 0:
                return text[idx:]
        stripped = strip_think(text)
        value, offset = extract_json(stripped)
        if value is not None and offset >= 0:
            print("[llm_client] WARN content 为空，已从 reasoning_content 捞回 JSON 结果"
                  "（审稿/校对类结构化任务）")
            return stripped[offset:]
        return ""

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

    def _save_truncated(self, task_path, texts):
        """把被 max_tokens 截断的输出**隔离存放**（诊断用，不作为正式产物）。

        为什么隔离而不是直接丢：截断的正文里往往有可用的开头，人需要能看见
        "究竟写到哪里断的"；但它**绝不能**冒充成品进入 data/chapters/。

        同时写一份**机器可读标记**（`utils.truncation.mark`）：orchestrator 靠它判断
        "这次失败是不是截断类" → 是就不自动重试（同一个上限只会再截断一次，白烧一倍输入）。
        """
        try:
            from utils import truncation
            info = truncation.mark(task_path, texts)
            path = truncation.TRUNC_DIR / (
                Path(task_path).stem + "_" + time.strftime("%Y%m%d_%H%M%S") + ".txt")
            write_text(path, NEWLINE.join(texts))
            print("[llm_client] 被截断的输出已隔离存放（非正式产物）: " + str(path))
            print("[llm_client] 截断标记已写入 %s（自动重试会据此跳过：同一上限只会再截断一次）"
                  % (truncation.TRUNC_DIR / truncation.MARK_NAME))
            _ = info
        except Exception as e:                        # noqa: BLE001
            print("[llm_client] WARN 截断输出隔离存放失败: " + repr(e))

    def _post_chat(self, messages, temperature=None, max_tokens=None):
        self._validate_creds()
        models_to_try = [self.model] + list(self.fallback_models)
        last_err = None
        for model_name in models_to_try:
            payload = {"model": model_name, "messages": messages}
            if temperature is not None:
                payload["temperature"] = temperature
            _mt = self.effective_max_tokens(max_tokens)
            if _mt:
                payload["max_tokens"] = _mt
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
                    # 件2：R1 系网关会把思考直接内嵌在 content 的 <think> 块里，
                    # 不剥离就会随正文落进产物 → 解析层统一先剥一道。
                    text = strip_think(text)
                    usage = data.get("usage") or {}
                    if not text.strip() and thinking.strip():
                        # thinking 模式：正文被塞进 reasoning_content（TokenHub glm-5.x / minimax 默认行为）
                        # 即使配置注入了关闭思考的 payload，模型仍可能进思考 → 标记当前候选无效，换下一个重试
                        if attempt < self.retries:
                            if injected is not None and injected not in self._bad_thinking_snippets:
                                self._bad_thinking_snippets.append(dict(injected))
                                self._chosen_thinking_snippet = None
                                for k in injected:
                                    payload.pop(k, None)
                            injected = self._apply_no_thinking(payload, model_name, force=True)
                            if injected is not None:
                                print("[llm_client] WARN " + str(model_name) +
                                      " 关闭思考 payload 仍无效 → 换下一个候选 " +
                                      json.dumps(injected, ensure_ascii=False) + " 后重试")
                                continue
                        salvaged = self._salvage_answer(thinking)
                        if salvaged:
                            print("[llm_client] WARN " + str(model_name) +
                                  " content 为空，已从 reasoning_content 的协议块兜底提取；"
                                  "建议把该模型加入 config/system.yaml 的 providers." +
                                  str(self.provider) + ".disable_thinking_models")
                        else:
                            print("[llm_client] WARN " + str(model_name) +
                                  " content 为空，且 reasoning_content 里没有协议块 → "
                                  "**思考内容已丢弃**（不会写进产物）；请把该模型加入 "
                                  "config/system.yaml 的 providers." + str(self.provider) +
                                  ".disable_thinking_models，或换模型")
                        text = salvaged
                    # 纯空响应（无正文、无思考）→ 重试
                    if not text.strip() and attempt < self.retries:
                        print("[llm_client] WARN " + str(model_name) +
                              " 返回纯空响应（无正文、无思考），重试中...")
                        time.sleep(2 ** attempt)
                        continue
                    if model_name != self.model:
                        print(f"[llm_client] fallback {self.model} → {model_name} 成功")
                    finish = (choices[0].get("finish_reason") if choices else None) or ""
                    if finish == "length":
                        _det = usage.get("completion_tokens_details") or {}
                        print("[llm_client] WARN " + str(model_name) +
                              " 输出被 max_tokens 截断（finish_reason=length）：completion_tokens=" +
                              str(usage.get("completion_tokens")) + "，其中思考 token=" +
                              str(_det.get("reasoning_tokens")) +
                              " → **正文不完整**；调用方必须判失败，不得当成品落盘")
                    return text, usage, data.get("model", model_name), finish
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
        _mt = self.effective_max_tokens(max_tokens)
        if _mt:
            payload["max_tokens"] = _mt
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
            finish_reason = ""
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
                            finish_reason = choices[0].get("finish_reason") or finish_reason
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
                full = strip_think("".join(content_parts))   # 件2：剥内嵌 <think> 块
                if stopped:
                    return full, {}, self.model, finish_reason
                if not full.strip() and thinking_parts:
                    # 即使配置注入了关闭思考的 payload，模型仍可能进思考 → 标记当前候选无效，换下一个重试
                    if attempt < self.retries:
                        if injected is not None and injected not in self._bad_thinking_snippets:
                            self._bad_thinking_snippets.append(dict(injected))
                            self._chosen_thinking_snippet = None
                            for k in injected:
                                payload.pop(k, None)
                        injected = self._apply_no_thinking(payload, self.model, force=True)
                        if injected is not None:
                            print("[llm_client] WARN " + str(self.model) +
                                  " 流式关闭思考 payload 仍无效 → 换下一个候选 " +
                                  json.dumps(injected, ensure_ascii=False) + " 后重试")
                            continue
                    salvaged = self._salvage_answer("".join(thinking_parts))
                    if salvaged:
                        print("[llm_client] WARN " + str(self.model) +
                              " 流式 content 为空，已从 reasoning_content 的协议块兜底回调；"
                              "建议把该模型加入 config/system.yaml 的 providers." +
                              str(self.provider) + ".disable_thinking_models")
                    else:
                        print("[llm_client] WARN " + str(self.model) +
                              " 流式 content 为空，且 reasoning_content 里没有协议块 → "
                              "**思考内容已丢弃**（不回调、不进产物）；请把该模型加入 "
                              "config/system.yaml 的 providers." + str(self.provider) +
                              ".disable_thinking_models，或换模型")
                    if on_chunk and salvaged:
                        on_chunk(salvaged)
                    return salvaged, {}, self.model, finish_reason
                if finish_reason == "length":
                    print("[llm_client] WARN " + str(self.model) +
                          " 流式输出被 max_tokens 截断（finish_reason=length）→ **正文不完整**；"
                          "调用方必须判失败，不得当成品落盘")
                return full, {}, self.model, finish_reason
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
            # 模型有时忽略协议块、直接输出正文（写作阶段常见故障）。
            # 当只有一个期望输出路径、且该路径在 data/chapters/ 下时，
            # 将整个文本视为正文写入。对其他路径（如大纲/设定集）不启用，
            # 避免把垃圾写入正式产物（靠重试机制修复更可靠）。
            if not text.strip():
                print("[llm_client] WARN 模型输出为空")
                return
            # 文本含 === 标记但不是有效协议块（parse_ops 没匹配到）→
            # 这是格式错误的协议块或正文里巧合含有 ===。
            # 只有看起来像正文（含标题/段落）且不含 thinking 残片时才走 fallback。
            if (len(expected_writes) == 1
                    and "data" + os.sep + "chapters" in str(expected_writes[0])
                    and len(text.strip()) > 100):
                target = expected_writes[0]
                if not allowed_paths([target]):
                    print("[llm_client] [fallback] 模型输出不含有效协议块，将全文写入: " + target)
                    Path(target).parent.mkdir(parents=True, exist_ok=True)
                    write_text(Path(target), text.rstrip() + NEWLINE)
                    return
            print("[llm_client] WARN 模型输出不含任何 FILE/APPEND 块")
            return
        for op, path, content in ops:
            matched = None
            pool = expected_writes + expected_appends
            path_name = Path(path).name
            # 第一轮：精确贴齐
            for exp in pool:
                snapped = snap_to_expected(path, exp)
                if snapped:
                    matched = snapped
                    break
            # 第二轮：basename 贴齐（模型有时输出 data/chapters/03.md 而非 data/chapters/checked/03.md）
            if matched is None:
                for exp in pool:
                    if Path(exp).name == path_name:
                        print(f"[llm_client] snap_to_expected: 贴齐 {path} → {exp}")
                        matched = exp
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

    def run_task(self, task_file, workdir=None, model=None, dry_run=False,
                 session_id=None):
        """执行自包含任务文件。返回 dict 与 HermesClient 同构。

        `session_id` 只为**签名同构**而收：直连（OpenAI 兼容）模式是无状态单次
        POST，**没有会话概念**，此处一律忽略（B2 的会话续接只对 Hermes 子会话有意义）。

        model 参数兼容保留：与配置模型不同时仅告警（直连模式换模型改 config/system.yaml）。
        设 NOVELFORGE_DEBUG=1 时把每次请求/响应原文存 data/state/llm_raw/。
        """
        if model and model != self.model:
            print("[llm_client] WARN run_task(model=" + str(model) + ") 与配置模型 " +
                  self.model + " 不同，按配置执行（换模型请改 config/system.yaml）")
        self._validate_creds()
        task_path = Path(task_file)
        body = read_text(task_path)
        new_body, missing = inline_inputs(body, task_path=task_path)
        if missing:
            raise FileNotFoundError(
                "任务引用的输入文件缺失（" + str(len(missing)) + " 个）：\n" +
                "\n".join("  - " + m for m in missing[:5]) +
                "\n请检查任务文件路径或重新生成上游产物。"
            )
        writes, appends = self._expected_outputs(new_body)
        reqs = segment_requests(new_body, writes, appends)
        if len(writes) > 1:
            print("[llm_client] 多文件输出任务，拆分 " + str(len(reqs)) + " 个请求")

        all_text, tokens_in, tokens_out, cache_read, model_used = [], 0, 0, 0, self.model
        finishes = []
        for sub_prompt, wr, ap in reqs:
            text, usage, mu, finish = self._post_chat(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": sub_prompt}])
            model_used = mu
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
            cache_read += extract_cache_read_tokens(usage)
            all_text.append(text)
            finishes.append(finish)
            if os.environ.get("NOVELFORGE_DEBUG"):
                dump = Path("data/state/llm_raw")
                dump.mkdir(parents=True, exist_ok=True)
                name = task_path.stem + "_" + str(len(all_text)) + ".txt"
                write_text(dump / name,
                           "--- PROMPT (" + str(len(sub_prompt)) + " chars) ---\n" +
                           sub_prompt[:3000] + "\n" + "--- RESPONSE ---\n" + text)
            # finish_reason=length → 输出被 max_tokens 截断，**本次产出不完整**：
            # 不落盘（避免半截正文被当成品），由下面统一判失败 + 隔离存放。
            if not dry_run and finish != "length":
                self._apply_ops(text, wr, ap)

        cost_yuan = cost_tracker.estimate_cost_yuan(tokens_in, tokens_out, model_used,
                                                   cache_read=cache_read)
        truncated = [i + 1 for i, f in enumerate(finishes) if f == "length"]
        if truncated:
            # 输出被 max_tokens 截断 → **正文不完整**。绝不落盘当成品：
            # 正式产物已在上面的循环里跳过，这里只把残缺文本隔离存放供诊断，
            # 并返回非零退出码（本项目统一以 exit_code != 0 判失败）。
            if not dry_run:
                self._save_truncated(task_path, all_text)
            return {
                "exit_code": 2,
                "error": ("输出被 max_tokens 截断（finish_reason=length，第 " +
                          ",".join(str(i) for i in truncated) + " 个子请求）→ 正文不完整，"
                          "已丢弃、未落盘。解决：提高该阶段 max_tokens，"
                          "或换用不会把输出预算耗在思考上的模型。"),
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
                "truncated": True,
                "finish_reasons": finishes,
            }
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
            "finish_reasons": finishes,
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
        new_body, missing = inline_inputs(body, task_path=task_path)
        if missing:
            raise FileNotFoundError(
                "任务引用的输入文件缺失（" + str(len(missing)) + " 个）：\n" +
                "\n".join("  - " + m for m in missing[:5]) +
                "\n请检查任务文件路径或重新生成上游产物。"
            )
        writes, appends = self._expected_outputs(new_body)
        reqs = segment_requests(new_body, writes, appends)
        if len(writes) > 1:
            print("[llm_client] 多文件输出任务（流式），拆分 " + str(len(reqs)) + " 个请求")

        all_text, tokens_in, tokens_out, cache_read, model_used = [], 0, 0, 0, self.model
        finishes = []
        for sub_prompt, wr, ap in reqs:
            collected = []

            def _on_chunk(piece, _collected=collected):
                _collected.append(piece)
                on_piece(piece)

            text, usage, mu, finish = self._post_chat_stream(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content": sub_prompt}],
                on_chunk=_on_chunk, stop_flag=stop_flag)
            model_used = mu
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
            cache_read += extract_cache_read_tokens(usage)
            full_text = "".join(collected)
            all_text.append(full_text)
            if os.environ.get("NOVELFORGE_DEBUG"):
                dump = Path("data/state/llm_raw")
                dump.mkdir(parents=True, exist_ok=True)
                name = task_path.stem + "_stream_" + str(len(all_text)) + ".txt"
                write_text(dump / name,
                           "--- PROMPT (" + str(len(sub_prompt)) + " chars) ---\n" +
                           sub_prompt[:3000] + "\n" + "--- RESPONSE ---\n" + full_text)
            finishes.append(finish)
            # finish_reason=length → 输出被截断，本次产出不完整 → 不落盘
            # （与 run_task 同策略：宁可失败，也不让半截正文冒充成品）
            if not dry_run and stop_flag and not stop_flag() and finish != "length":
                self._apply_ops(full_text, wr, ap)

        cost_yuan = cost_tracker.estimate_cost_yuan(tokens_in, tokens_out, model_used,
                                                   cache_read=cache_read)
        _stopped = bool(stop_flag and stop_flag())
        truncated = [i + 1 for i, f in enumerate(finishes) if f == "length"]
        if truncated and not _stopped:
            # 输出被截断 → 正文不完整，绝不落盘当成品（与 run_task 同策略）。
            # 注意：用户主动 stop 不算截断失败（stopped 优先）。
            if not dry_run:
                self._save_truncated(task_path, all_text)
            return {
                "exit_code": 2,
                "error": ("流式输出被 max_tokens 截断（finish_reason=length，第 " +
                          ",".join(str(i) for i in truncated) + " 个子请求）→ 正文不完整，"
                          "已丢弃、未落盘。解决：提高该阶段 max_tokens，"
                          "或换用不会把输出预算耗在思考上的模型。"),
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
                "stopped": _stopped,
                "truncated": True,
                "finish_reasons": finishes,
            }
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
            "stopped": _stopped,
            "finish_reasons": finishes,
        }


class HermesClient:
    """Hermes 子会话客户端（原 api_client.HermesClient，保留为引擎之一）。

    2026-10-01 改用 `--format stream-json` 协议（排雷 P0-1）：
    - stdout 为 JSONL：init 事件（model）+ text 事件（增量文本）+ 收尾 result 事件
      {text, tokens:{input,output,cache_read,...}, exit_code, duration_ms}。
    - result.text = 干净的最终消息（无 ANSI/工具流水）→ 作为 stdout_tail 直接给
      chapter_review / proofread / outline_panel 解析 JSON。旧实现给的是工具流水
      尾部，JSON 窗口被噪声挤占 → 审稿门解析失败还会静默跳过（fail-open）。
    - result.tokens = 真实 usage → 记账不再按任务文件大小瞎估。
    - text 事件逐段回放 → run_task_stream 真流式，stop_flag 可杀子进程（P0-4/P2）。

    模型：恒不传 -m —— agent 模式一律用 agent 内部配置的模型（用户 2026-10-01 定调），
    与 config 的 model.* 无关。旧实现透传 TokenHub（腾讯云）模型名，hermes 按它自己的
    active provider（小米）解析 → 400 → 静默 fallback 到别家，「配置写 A、实际跑 B」。

    工具面（P1-5）：`-t file` toolset（= patch/read_file/search_files/write_file 四件套）
    —— 子会话不再持有 terminal/browser/kanban/cron/MCP 等全量工具，写盘越权从
    「提示词约束」变成「机制约束」。⚠️ `-t` 收 **toolset 名**（file/web/terminal…），
    传工具名（read_file,…）会得到空集合：模型照旧吐 tool_call 但永不派发，
    result.text 是原始 <longcat_tool_call> 且 exit_code=0 假成功（2026-10-01 实测踩坑）。
    config 可用 hermes.toolsets 覆盖（设为空串恢复全量工具，逃生门）。

    超时（P0-3）：timeout 秒硬超时 → 返回 exit_code=124 结构化失败，走 auto_retry /
    连败止损链，不再抛 TimeoutExpired 穿透 run_stage 把 run 打成 crashed。
    （刻意不用 --run-budget：实测 budget=10 没切掉 40s 任务，语义不是墙钟秒，
    生产杀开关上不猜单位；墙钟上界由本类 timeout 承担。）
    """

    # toolset 名（非工具名）：file = patch/read_file/search_files/write_file 四件套
    DEFAULT_TOOLSETS = "file"

    def __init__(self, hermes_bin="hermes", timeout=900, model=None,
                 max_turns=60, toolsets=None, session_continuation=False,
                 request_max_tokens=None):
        self.hermes_bin = hermes_bin
        self.timeout = timeout
        self.model = model
        self.max_turns = max_turns
        # None → 默认白名单；空串/False → 不加 -t（全量工具逃生门）
        self.toolsets = self.DEFAULT_TOOLSETS if toolsets is None else toolsets
        # B2 会话续接（默认 **关**）：开启后段与段之间可经 `--resume` 接续同一子会话，
        # 而不是每次冷启动。默认关的理由：这是**行为变更**（agent 上下文跨段累积），
        # 真实试写里尚无收益实测证据，不该悄悄改掉既有流水线的行为。
        self.session_continuation = bool(session_continuation)
        # 单请求输出上限（P0 止烧）。⚠️ hermes chat **不接受 max_tokens**（无此旋钮），
        # 故这里只能做**事后比对 + 告警**：见 `note_request_tokens()`。
        # 真正据此熔断的是 CostTracker（config: token_limit.per_request_pause_hermes）。
        try:
            self.request_max_tokens = int(request_max_tokens or 0) or None
        except (TypeError, ValueError):
            self.request_max_tokens = None

    def note_request_tokens(self, tokens_out):
        """单次子会话输出与配置上限比对：超了**大声告警**（不阻断、不丢弃产物）。

        为什么不在这里判失败（2026-10-03 定）：
        - hermes 子会话已用文件工具把章节写进盘了，判失败 = 丢掉真产物 + 重跑更贵；
        - 「是否停机」是**熔断判据**，唯一来源是 CostTracker.status_detail()；
          想要按次熔断就把 token_limit.per_request_pause_hermes 设为 true。
        返回是否超限（供测试与调用方观察）。
        """
        if not self.request_max_tokens:
            return False
        try:
            n = int(tokens_out or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= self.request_max_tokens:
            return False
        print("[llm_client] WARN 单次子会话输出 %d token > 单请求上限 %d"
              "（config/system.yaml 的 budget.token_limit.per_request_max_tokens）。"
              "hermes chat 无 max_tokens 旋钮 → 本次**不阻断**；"
              "要按次熔断请把 budget.token_limit.per_request_pause_hermes 设为 true"
              % (n, self.request_max_tokens))
        return True

    def _build_cmd(self, task_path, model=None, session_id=None):
        cmd = [self.hermes_bin, "chat", "-q",
               "阅读并严格按 " + str(task_path) + " 中的指示执行全部步骤。"
               "完成后简要汇报：产物路径、校验结果、遇到的问题。",
               "--format", "stream-json"]
        if self.toolsets:
            cmd += ["-t", str(self.toolsets)]
        if self.max_turns:
            cmd += ["--max-turns", str(int(self.max_turns))]
        if session_id and self.session_continuation:
            # B2 续接：接上上一段的子会话。**开关关着时即使传了 session_id 也不拼**
            # —— 默认关就是真的不改行为。`--create-if-missing` 让 session 过期/
            # 不存在时**新建**而不是直接失败（续接是优化，不该成为新的失败源）。
            cmd += ["--resume", str(session_id), "--create-if-missing"]
        eff_model = model or self.model
        if eff_model:   # 仅显式指定时；make_client 恒传 None（agent 内部模型）
            cmd += ["-m", eff_model]
        return cmd

    @staticmethod
    def _parse_stream_json(raw):
        """JSONL → (result事件或None, init事件或None, text事件列表, session_id或None)。

        非 JSON 行（stderr 混入 / 收尾 session_id 行）直接忽略，解析永不抛。

        session_id（B2）：事件里能找到就带出来，供 `--resume` 续接；找不到就是
        None —— 续接自然不生效而**不报错**（她是优化项，不该成为新的失败源）。
        """
        result, init, texts, session_id = None, None, [], None
        for line in (raw or "").splitlines():
            s = line.strip()
            if not s.startswith("{"):
                continue
            try:
                ev = json.loads(s)
            except Exception:                               # noqa: BLE001
                continue
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            if t == "result":
                result = ev
            elif t == "system" and ev.get("subtype") == "init":
                init = ev
            elif t == "text":
                texts.append(ev.get("text") or "")
            if not session_id:
                for _k in ("session_id", "sessionId", "session"):
                    _v = ev.get(_k)
                    if isinstance(_v, str) and _v.strip():
                        session_id = _v.strip()
                        break
        return result, init, texts, session_id

    def _finish(self, task_path, rc, raw, stderr="", error=None, stopped=False):
        """把一次子会话执行收敛为 run_task 同构 dict（永不抛异常）。"""
        result, init, texts, session_id = self._parse_stream_json(raw)
        if stopped:
            exit_code = 0          # 用户主动停 ≠ 失败（与 direct 流式停止同语义）
        elif rc:
            exit_code = int(rc)    # 进程非零必失败（result 可能没来得及写）
        elif result is not None:
            exit_code = int(result.get("exit_code") or 0)
        else:
            exit_code = 0
        # stdout_tail：优先干净最终消息；无 result 则用已回放 text；再退原始尾部
        parsed_tail = None
        if result is not None and isinstance(result.get("text"), str):
            parsed_tail = result["text"]
        elif texts:
            parsed_tail = "".join(texts)
        tail = parsed_tail if parsed_tail is not None else (raw or "")[-2000:]
        # usage：result 事件带真实 tokens；否则按任务文件保守估算（标注 estimated）
        toks = (result or {}).get("tokens") or {}
        if result is not None and parsed_tail is not None:
            t_in = int(toks.get("input") or 0)
            t_out = int(toks.get("output") or 0)
            c_read = int(toks.get("cache_read") or 0)
            estimated = False
        else:
            from utils.api_client import estimate_tokens as est
            t_in, t_out = est(task_path)
            c_read = 0
            estimated = True
        out = {
            "exit_code": exit_code,
            "stdout_tail": tail,
            "tokens": t_in,
            "tokens_out": t_out,
            "cache_read": c_read,
            "cost_yuan": 0.0,     # agent 订阅执行无按量成本（estimate 对 provider=hermes 同返 0）
            "estimated": estimated,
            "provider": "hermes",
            "model": (init or {}).get("model") or self.model or "hermes-default",
            "requests": 1,
            "stopped": bool(stopped),
            # B2：子会话 id（供下一章 `--resume` 续接）；事件里拿不到就是 None
            "session_id": session_id,
        }
        if error:
            out["error"] = str(error)
        if stderr and exit_code != 0:
            out["stderr_tail"] = stderr[-600:]
        # P0 止烧：单次子会话输出的比对（hermes 无 max_tokens 旋钮 → 只告警不停机，
        # 「是否熔断」由 CostTracker.status_detail 一元判据决定）。
        if self.note_request_tokens(t_out):
            out["request_tokens_over"] = True
        return out

    @staticmethod
    def _kill_tree(proc):
        """杀整棵进程树（Windows 关键坑，2026-10-01 实测）。

        `hermes` 是 pip 入口 .exe，底下还孵着 python 子进程；`.bat` 桩同理
        （cmd 孵孙进程）。只 kill 直接子进程 → 孤儿继续持有 stdout 管道 →
        communicate()/wait() 等不到 EOF（实测超时阈值 1s 却 19.3s 才返回），
        「停止」后真实子会话还在继续跑。必须 taskkill /F /T 连孙子一起杀。
        """
        import subprocess
        try:
            if proc.poll() is not None:
                # 直接子进程已退出：按 PID taskkill 有 PID 复用误杀风险，
                # 且活树已不在（孤孙进程场景由第二轮 communicate 的 10s 上界兜住）
                return
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
            else:
                proc.kill()
        except Exception:                               # noqa: BLE001
            pass
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:                               # noqa: BLE001
            pass

    def run_task(self, task_file, workdir=None, model=None, session_id=None):
        """执行任务。`session_id` 非空**且**开启了会话续接时走 `--resume`（B2）。

        续接失败（非零退出）会**自动降级**为全新会话重试一次 —— 续接是优化，
        不该成为新的失败源。降级结果里带 `resumed_fallback: true` 供调用方识别。
        """
        res = self._run_once(task_file, workdir, model, session_id)
        if (session_id and self.session_continuation
                and not res.get("stopped") and res.get("exit_code")):
            why = str(res.get("error") or res.get("stderr_tail") or "")[:140]
            print("[llm_client] WARN 续接会话失败（exit=%s）：%s → 降级为全新会话重试"
                  % (res.get("exit_code"), why))
            res = self._run_once(task_file, workdir, model, None)
            res["resumed_fallback"] = True
        return res

    def _run_once(self, task_file, workdir=None, model=None, session_id=None):
        import subprocess
        task_path = Path(task_file)
        cmd = self._build_cmd(task_path, model, session_id)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace",
                                    cwd=workdir)
        except OSError as e:
            return self._finish(task_path, 127, "",
                                error="无法启动 hermes（" + str(self.hermes_bin) +
                                      "）: " + str(e))
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired as e:
            # P0-3：超时抛异常会穿透 run_stage → run 崩为 crashed 且绕过 auto_retry。
            # 先杀整棵进程树，再有限等待管道收尾（防孤儿把 communicate 拖到任务自然结束）。
            self._kill_tree(proc)
            partial = e.output or ""
            if isinstance(partial, bytes):
                partial = partial.decode("utf-8", "replace")
            err_tail = e.stderr or ""
            if isinstance(err_tail, bytes):
                err_tail = err_tail.decode("utf-8", "replace")
            try:
                out2, err2 = proc.communicate(timeout=10)
                partial = out2 or partial
                err_tail = err2 or err_tail
            except Exception:                           # noqa: BLE001
                pass
            return self._finish(task_path, 124, partial, stderr=err_tail or "",
                                error="hermes 子会话超时（%ss）被中止 → 结构化失败，"
                                      "交给自动重试 / 止损" % self.timeout)
        return self._finish(task_path, proc.returncode, stdout or "",
                            stderr=stderr or "")

    def write_task(self, task_dir, name, content):
        task_dir = Path(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / name
        write_text(path, content)
        return path

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None, model=None):
        """真流式（P2）：逐 text 事件回放；stop_flag 置位即杀子进程（P0-4）。

        stderr 并入 stdout 读取（防管道填满死锁）；非 JSON 行由解析层忽略。
        """
        import subprocess
        import threading
        import time
        import queue as _queue
        task_path = Path(task_file)
        cmd = self._build_cmd(task_path, model)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace",
                                    bufsize=1, cwd=workdir)
        except OSError as e:
            return self._finish(task_path, 127, "",
                                error="无法启动 hermes（" + str(self.hermes_bin) +
                                      "）: " + str(e))
        line_q = _queue.Queue()

        def _reader():
            try:
                for line in proc.stdout:
                    line_q.put(line)
            except Exception:                               # noqa: BLE001
                pass
            line_q.put(None)   # EOF 哨兵

        threading.Thread(target=_reader, daemon=True).start()
        raw_parts = []
        stopped = False
        timed_out = False
        deadline = time.time() + self.timeout
        eof = False
        while not eof:
            if stop_flag and stop_flag():
                stopped = True
                break
            if time.time() > deadline:
                timed_out = True
                break
            try:
                line = line_q.get(timeout=0.5)
            except _queue.Empty:
                continue
            if line is None:
                eof = True
                break
            raw_parts.append(line)
            if not line.lstrip().startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except Exception:                               # noqa: BLE001
                continue
            if isinstance(ev, dict) and ev.get("type") == "text":
                piece = ev.get("text") or ""
                if piece:
                    try:
                        on_piece(piece)
                    except Exception:                       # noqa: BLE001
                        pass   # 回调失败绝不打断执行

        def _kill():
            # 杀整棵进程树：只杀直接子进程会让 hermes.exe 的 python 孙进程
            # 成孤儿继续跑（「停止」形同虚设）
            self._kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:                               # noqa: BLE001
                pass

        if stopped or timed_out:
            _kill()
            rc = 124 if timed_out else 0
        else:
            try:
                rc = proc.wait(timeout=30)
            except Exception:                               # noqa: BLE001
                _kill()
                rc = 124
        raw = "".join(raw_parts)
        if timed_out:
            return self._finish(task_path, 124, raw,
                                error="hermes 子会话流式超时（%ss）被中止" % self.timeout)
        if stopped:
            return self._finish(task_path, 0, raw, stopped=True)
        return self._finish(task_path, rc, raw)


# ---------------------------------------------------------------- 工厂

def extract_cache_read_tokens(usage):
    """从 usage 里取「缓存命中的输入 token 数」，兼容多家字段命名。

    **2026-09-23 实测（TokenHub，免费额度）**：返回的是 OpenAI 风格
    `usage.prompt_tokens_details.cached_tokens`；而此处原先读的是
    `usage.cache_read_tokens` —— 该键在 OpenAI / DeepSeek / Anthropic
    **任何一家都不存在**，于是命中数恒为 0：缓存优化永远"看不到效果"
    （静默失效，正是本项目最忌讳的那类失败）。

    实测证据（同一长前缀、连发 3 次，cached_tokens）：
      qwen3.5-flash 0 / 0 / 0（**该模型在 TokenHub 上完全不缓存**）
      deepseek-v4-flash 0 / 2048 / 2048
      glm-5.1 1789 / 1902 / 1902

    按优先级兼容各家：
      1. OpenAI / TokenHub : `prompt_tokens_details.cached_tokens`
      2. DeepSeek 官方      : `prompt_cache_hit_tokens`
      3. Anthropic          : `cache_read_input_tokens`
      4. 个别网关           : 顶层 `cache_read_tokens` / `cached_tokens`
    """
    if not isinstance(usage, dict):
        return 0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        return int(details.get("cached_tokens") or 0)
    for key in ("prompt_cache_hit_tokens", "cache_read_input_tokens",
                "cache_read_tokens", "cached_tokens"):
        value = usage.get(key)
        if value is not None:
            return int(value or 0)
    return 0


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


def _request_max_tokens_from_cfg(cfg):
    """从 config 取「单请求输出上限」（budget.token_limit.per_request_max_tokens）。

    只在这一处解析（两个引擎共用一份判据），返回 int 或 None。
    enabled=false 时返回 None = 不设上限（老行为）。
    """
    tl = ((cfg or {}).get("budget") or {}).get("token_limit") or {}
    if not isinstance(tl, dict) or not tl.get("enabled"):
        return None
    try:
        n = int(tl.get("per_request_max_tokens") or 0)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


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
    # 单请求输出上限（P0 止烧）：直连引擎真的设进 payload；hermes 只能事后比对+告警
    req_cap = _request_max_tokens_from_cfg(cfg)

    if engine == "hermes" or provider_id == "hermes":
        # 2026-10-01 用户定调：agent 模式（hermes 引擎）一律用 **agent 内部配置的模型**，
        # 与本配置的 model.* 完全无关（流量本就走 agent 的订阅）。
        # 历史坑：曾把 model_id 透传成 `-m` —— hermes 按它自己的 active provider 解析
        # 裸模型名，TokenHub（腾讯云）的模型名发到小米端点 → 400 Unsupported model →
        # 静默 fallback 到别家，「配置写 A、实际跑 B」。故此处不传 -m（= hermes 默认模型）。
        if verbose:
            print("[client] 引擎=hermes → 用 agent 内部配置的模型（忽略 model." +
                  str(model_key) + "）")
        hcfg = (cfg or {}).get("hermes") or {}
        return HermesClient(
            model=None,
            timeout=int(hcfg.get("timeout", 900)),
            max_turns=int(hcfg.get("max_turns", 60)),
            toolsets=hcfg.get("toolsets", HermesClient.DEFAULT_TOOLSETS),
            # B2：会话续接默认 **关**（不改既有行为）
            session_continuation=bool(hcfg.get("session_continuation", False)),
            request_max_tokens=req_cap,
        )

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

    # 模型白名单校验（strict=False：只报警不拦，但记录日志）
    # 这是防御性成本框架的一部分：用户免费体验包按模型领取，未经确认不得指定/更换付费模型
    try:
        from utils import model_registry
        root = Path(__file__).resolve().parents[2]
        chk = model_registry.check_model(model_id, cfg, root, strict=False)
        if not chk["ok"] and verbose:
            print("[client] WARN 模型白名单校验: " + chk["reason"])
    except Exception as e:
        if verbose:
            print("[client] WARN 模型白名单校验跳过: " + str(e)[:100])

    if verbose:
        print("[client] 引擎=direct provider=" + provider_id + " 模型=" + model_id +
              " 角色=" + model_key +
              ("｜单请求输出上限=" + str(req_cap) if req_cap else ""))
    return OpenAICompatClient(
        model=model_id, base_url=base_url, api_key=api_key,
        timeout=int(prov.get("timeout", 600)), retries=int(prov.get("retries", 3)),
        provider=provider_id, model_key=model_key, fallback_models=prov.get("fallback", []),
        disable_thinking_models=prov.get("disable_thinking_models", []),
        disable_thinking_payloads=prov.get("disable_thinking_payloads"),
        request_max_tokens=req_cap)
