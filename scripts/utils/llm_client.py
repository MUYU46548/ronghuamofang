# -*- coding: utf-8 -*-
"""LLM 执行引擎抽象层（P2 引擎无关化，2026-09-09）。

目标：流水线所有调用点经 make_client() 取客户端，引擎可切换
（OpenAI 兼容直连 / 子会话执行层 / 未来可插拔）。

直连模式设计：
- 无子会话工具循环 → 任务文件“输入文件”段解析后全文内联进 prompt；
- 单请求输出上限 → 多文件输出任务按目标文件拆分请求（segment_requests）；
- 输出协议：===FILE: 路径=== ... ===END===（写入）/ ===APPEND:（追加）/ ===DELETE:（删除）；
- 模型给的路径按任务解析出的期望路径贴齐（snap_to_expected），再过写白名单（data/**、
  logs/runs.db），防越权写；
- usage 取 API 真实返回（estimated=False），单价走 cost_tracker.RATES。

思考模型兼容（2026-09-14，TokenHub glm-5.1 破坏性变更）：
- 部分模型（glm-5.x / kimi 等）默认进 thinking 模式：正文全进 reasoning_content、
  content 为空，且思考 token 会吃掉 max_tokens 预算 → 流水线所有产物空白。三道防线：
  1) 主动：模型名命中 config 的 providers.<id>.disable_thinking_models 时，
     注入“关闭思考”payload 片段，模型直接给 content（名单与片段都不硬编码在代码里）；
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
from pathlib import Path

from utils.file_io import read_text, write_text
from utils import cost_tracker
from utils.console import ensure_utf8_stdout
# 引擎缺省值只有一处判据（2026-10-30）：改前 make_client 缺省 hermes、
# estimate_tokens/nfctl 缺省 direct —— 同一份缺键配置预估与实际跑在两个引擎上。
from utils.engine import resolve_engine
# 解析层容错（件2）：`<think>` 内嵌剥离 + 从思考里捞可解析的 JSON 文档。
# 同包内复用，避免“同一判据写两遍、只修一处”（见 TESTS.md 的复制实现教训）。
from utils.validator import strip_think, extract_json

# 进程级输出安全网（幂等）：stdout 被捕获时是 cp936，而 ¥/⚠️/✅ 等字符不在 GBK 里
# → 一行日志就能 UnicodeEncodeError 打死整轮长跑。放在这里是因为**所有会跑 LLM 的
# 脚本都 import 本模块**（阶段脚本 / 精修 / 审稿 / 校对 / GUI 服务），覆盖面最广。
# 详见 utils/console.py 的实测记录。
ensure_utf8_stdout()

BS = chr(92)          # 反斜杠字符（源码零字面量纪律）
NEWLINE = chr(10)     # 换行字符

# 子会话执行边界（2026-11-05，卡片 -015“AI 仍在查看 Hermes 数据目录”第二次出现）：
# `-t file` 只限制了**工具集**，没限制**路径** —— read_file / search_files 仍能读全局，
# 而子会话的系统提示带的是 Hermes 自己的身份与技能清单，模型就会去翻
# %LOCALAPPDATA%/hermes（配置、会话记录、技能库）。翻一次几十秒 + 一笔 token，且与任务无关。
# 放在 `_build_cmd` 这个唯一必经点上，所有任务书一次覆盖（不去每个模板各写一遍）。
#
# ⚠️ 只禁“无关目录”，**绝不能**写成“不要读本地目录”：作者的设定库 / 参考书 / 范文
# 本来就在工作区之外（如 Obsidian vault 的绝对路径），任务文件里列了就必须能读，
# 禁错了会让模型拿不到设定，写出与设定冲突的正文（比白翻目录更严重）。
SUBAGENT_SCOPE_RULE = (
    NEWLINE + NEWLINE + "【执行边界】" + NEWLINE
    + "· 该读的：任务文件里**列出的**输入路径（包括作者配置的设定库 / 参考书 / 范文目录，"
    "它们可能在工作区之外，照读）+ 当前工作区内的文件。" + NEWLINE
    + "· 不该翻的：Hermes 自己的目录（%LOCALAPPDATA%/hermes 下的配置、会话记录、技能库）——"
    "那不是你的资料，翻一次就是几十秒和一笔 token。" + NEWLINE
    + "· 不要安装依赖、不要联网、不要改 .git。"
)

# 单轮直答（hermes 引擎的 `_post_chat`）前置说明：子会话有文件工具，但**答案必须在回复里**。
# 不写这段时模型会按任务文件的习惯去 write_file，调用方拿到空文本 → 白烧一次子会话。
# 单轮任务的材料由调用方**全部内联**进提示词（见 open_variants / branch_samples），
# 所以这里可以要求"不要翻文件"；它不是给任务书用的那条规则。
ONE_SHOT_PREFIX = (
    "这是一次**单轮直答**调用：请直接在这条回复里输出最终结果（正文 / JSON）。" + NEWLINE
    + "不要调用任何工具，不要写文件，不要解释你的思考过程。" + NEWLINE
    + "你用 write_file 写出来的东西不会被读取 —— 那等于没有回答。" + NEWLINE
    + "本次需要的材料已经全部在下面，不需要去翻任何本地文件。" + NEWLINE
    + "------------------------------------------------------------")

# ---------------------------------------------------------------- 正则（动态构造）

SEP = "[" + re.escape(BS) + "/]"                       # 匹配分隔符：\ 或 /
EXT = "[.](?:md|json|txt|markdown|yaml|yml)"
EXT_MJ = "[.](?:md|json)"

# 统一路径模式：可选盘符前缀 + 路径体（排除分隔标点/冒号）+ 扩展名
PATH_RE = re.compile("(?:[A-Za-z]:" + SEP + ")?[^：（(，,、:" + NEWLINE + "]*?" + EXT)
PATH_RE_MJ = re.compile("(?:[A-Za-z]:" + SEP + ")?[^：（(，,、:" + NEWLINE + "]*?" + EXT_MJ)

# 输入段专用（2026-11-27）：文件名允许带括号（如“浊河灯录（世界书）.md”）。
# 旧 PATH_RE 把 `（`/`(` 当“说明开头”截断，真实素材库里这类文件名会被判缺失。
# 终点由扩展名锚定，所以路径后面的“（仅参考）”不会吃进来。
PATH_RE_INPUT = re.compile(
    "(?:[A-Za-z]:" + SEP + ")?[^：:" + chr(34) + "，,、" + NEWLINE + "]*?" + EXT)
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
    """“输入文件”段里存在**路径引用但格式不被解析器接受**。

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
    """解析任务正文“输入文件”段的路径引用。返回 [(path_str, is_dir)]。

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
            pm = PATH_RE_INPUT.search(line)
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
        pm = PATH_RE_INPUT.search(content)
        if pm:
            p = pm.group(0).strip()
            if p not in seen:
                seen.add(p)
                out.append((p, False))
    return out


def format_input_section_error(diag, task_path=None):
    """把 extract_input_paths 的诊断渲染成**可行动**的报错文案。"""
    head = ("任务“输入文件”段有 %d 处路径引用**不能被解析**（这些输入不会被内联，"
            "模型会收不到它们）：" % len(diag.get("skipped") or []))
    lines = [head]
    for s in diag.get("skipped") or []:
        lines.append("  · 第 %d 行 —— %s：`%s`"
                     % (s.get("line"), s.get("reason"), (s.get("text") or "")[:90]))
    where = ("任务文件: " + str(task_path)) if task_path else "任务正文"
    lines.append("  %s（“输入文件”段起于第 %s 行）" % (where, diag.get("section_line")))
    lines.append("  " + _INPUT_FORMAT_HINT)
    return NEWLINE.join(lines)


def inline_inputs(body, char_limit=220000, task_path=None, note=None):
    """把“输入文件”段引用的文件内容内联进正文。返回 (new_body, [缺失路径])。

    **格式不符 = 明确报错**（P1，2026-10-03）：段落里出现“有路径、但解析器不认”的行
    （最常见的形态就是漏写 `- ` 前缀 —— stage5 空稿事故的同类根因）时，抛
    `InputSectionFormatError` 并指出**行号 + 原文 + 正确写法**，而不是静默返空。

    与既有的“输入文件缺失就抛 FileNotFoundError”是同一处置哲学：
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
                            note or ("（本次调用为直连引擎，无文件工具；下列引用文件的内容已内联于文末【内联输入】区，直接使用）"))
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
       → **整批块被丢掉**，stage5 于是走“复制 raw → checked”兜底，
       “逻辑检查完成”照打，报告 `check_report.md` 根本没落盘；
    ② 模型随手漏写结束标记时同样整块丢 → `_apply_ops` 的兜底把**整段模型输出**
       （含 `===FILE:` 标记行与绝对路径）直接写进章节文件。实测
       `data/chapters/raw/01.md` 首行就是 `===FILE: E:\\...\\01.md===`。

    现改两处：`END_RE` 兼容带 tag 的写法（见其定义）；本函数在找不到 END 时
    **不再丢弃**，改用“下一个 OP 之前”当块尾并打 WARN —— “块尾外扩”的代价
    （最多带一小段收尾说明）远小于“整块丢弃 + 兜底写全文”。
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

    只修解析器只能防新增；老产物里的残留会随“上一章内容 → 提示词 → 新章节”
    和“合并正文 → Word 成品”继续传播。故在**读取侧**再兜一道。
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

    只用于诊断与自检：前缀缓存按“请求开头逐字相同”命中，所以这个数字直接
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
#      变量放最后；旧写法“第 1/3 个”打头，分叉点落在指令第 3 个字符，
#      后半段那句“严禁输出其他文件的内容块”每个子请求各成一份独立前缀。
SEGMENT_HEAD = ("【本批次共 {total} 个输出文件，本次调用**只**处理其中一个："
                "其余文件由其他调用处理，严禁输出其他文件的内容块；"
                "报告类输出只记录与本次输出相关的内容。"
                "本次输出文件（第 {index}/{total} 个）：{path}】")


def segment_requests(body, expected_writes, expected_appends):
    """多文件输出任务 → 拆为逐请求指令。返回 [(sub_prompt, writes, appends)]。

    **指令必须放在 body 之后、且变量只能在最末**（2026-09-29 随行件3 + 2026-10-03 重排）。
    原先把“仅处理第 i/N 个输出”的指令**头插**在 user 消息最前，于是 N 个子请求虽然
    共享 95%+ 的正文与内联输入，却在**首字符**就分叉 → 前缀缓存全部失效（每次都是
    全新前缀）。挪到内联输入之后，N 个子请求共享同一段前缀，只有尾部差异 →
    前缀缓存可命中（实测 deepseek-v4-flash 重排后 57.9%，重排前上限 1.2%）。

    2026-10-03（P1 缓存）：**连指令内部也统一** —— 常量说明在前、`第 i/N 个 + 路径`
    放最末，使那句“严禁输出其他文件的内容块”也落进公共前缀（旧写法白丢）。
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


class MissingCredentialError(RuntimeError):
    """直连引擎的环境变量里没有凭据（2026-11-06，C2：启动失败态）。

    为什么单列一个异常类而不是 `print WARN`：旧实现"构造成功、调用时才 401"，
    于是失败点在**第一轮真请求**，报错埋在某个阶段深处，用户看不出是没配 Key。
    单列类名让 orchestrator / nf_api 能把"配置问题"与"模型胡写"分开报，
    也让前置检查有地方可挂。消息里只写**变量名**与修法，绝不回显密钥值。
    """


def extract_cache_read_tokens(usage):
    """从 usage 里取“缓存命中的输入 token 数”，兼容多家字段命名。

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
    """从 config 取“单请求输出上限”（budget.token_limit.per_request_max_tokens）。

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
    # ---- 许可协议总闸（2026-11-18）----
    # **所有**调用点都经这里取客户端（全仓 31 处），所以"未确认就不许调模型"
    # 只需要落这一处：脚本、流水线、HTTP 端点、MCP 转发一并生效。
    # 放在引擎判定之前 —— self 引擎的错是"接线错"，本闸的错是"人没同意"，
    # 两者给人的下一步完全不同，先答后者。
    from utils import license as _license
    if not _license.is_acked():
        raise RuntimeError("[client] " + _license.refusal_message("调用模型"))

    _load_env_file()
    engine = resolve_engine(cfg)
    if engine == "self":
        # engine: self = **程序不调用任何模型**。走到这里说明有代码路径想发请求，
        # 那是接线错误，必须当场炸掉：静默降级成 direct/hermes 会变成
        # "配了 self，却在后台真花钱跑模型" —— 与本引擎的整个卖点相反。
        # 处置：把那条路径挪进 orchestrator 的 self 分支（self_done / emit_tasks）。
        raise RuntimeError(
            "[client] engine: self 下不允许调用任何模型（本引擎只产任务书）。"
            "发起调用的阶段代码走错了分支 —— 应在 orchestrator 的 self 分支里用 "
            "emit_tasks/self_done，而不是 run_stage。")
    mspec = ((cfg or {}).get("model") or {}).get(model_key) or {}
    if isinstance(mspec, str):
        mspec = {"provider": "hermes", "id": mspec}
    provider_id = mspec.get("provider", "hermes" if engine == "hermes" else "")
    model_id = mspec.get("id", "")
    provs = (cfg or {}).get("providers") or {}
    # 单请求输出上限（P0 止烧）：直连引擎真的设进 payload；hermes 只能事后比对+告警
    req_cap = _request_max_tokens_from_cfg(cfg)

    if engine == "dsh" or provider_id == "dsh":
        # engine: dsh = DeepSeek Harness 的 headless 执行层（2026-11-17，转向形态 B）。
        # 金额恒 0（订阅执行）、token 照记。所有判据在 utils/dsh_client.py 里，
        # 这里只负责装配 —— 换引擎的接线不许散在调用点。
        #
        # 模型是 DSH 的事（2026-11-20 作者定案：连名字都不用传）。墨坊发任务书，
        # headless 用 profile 里 agent-default-model 决定的模型；不传 --model
        # （它本就不存在），也不拿 --patch 去改 DSH 的模型配置 —— 那是越界。
        if verbose:
            print("[client] 引擎=dsh → 角色 " + str(model_key)
                  + " 用 DSH 自己的模型（墨坊不传模型名）")
        dcfg = (cfg or {}).get("dsh") or {}
        from utils.dsh_client import DshClient
        return DshClient(
            dsh_bin=dcfg.get("bin") or None,
            timeout=int(dcfg.get("timeout", 1800)),
            # 默认 headless（2026-11-24 改）：desktop 档命令行调不通
            # （DSH 拒绝 "managed exclusively by the Electron application"）。
            profile=dcfg.get("profile") or DshClient.DEFAULT_PROFILE,
            permission_mode=dcfg.get("permission_mode") or None,
            approval_policy=dcfg.get("approval_policy", DshClient.DEFAULT_APPROVAL_POLICY),
            # 2026-11-25 起默认 true（作者拍板 Q1=A：整卷一会话，按水位 60% 换）。
            # 缺键的老工作区也按 true 走 —— 否则“升级了但会话还是没接上”，
            # 而这正是作者要继续写长书的前提。要退回每章新会话就显式写 false。
            session_continuation=bool(dcfg.get("session_continuation", True)),
            request_max_tokens=req_cap,
            add_dirs=dcfg.get("add_dirs") or None,
            # 传输断连自动重发（2026-11-24）：DSH 长任务会中途 TRANSPORT 断连，
            # 段群已落盘，重发同一份任务书 = 从断点继续。
            transport_retries=int(dcfg.get("transport_retry_max", 2)),
            transport_backoff_sec=float(dcfg.get("transport_retry_wait_sec", 5)),
            # 命令形状（2026-11-25，方案 B）：只在配置里可改，界面不暴露。
            # 不写就是默认 `--profile {profile} --json`，与旧行为逐字一致。
            base_args=dcfg.get("args") or None,
            # 写作任务边界（2026-11-27）：阶段 1–4 的生成任务书正文内联 +
            # 禁 shell/搜索/子代理工具（保留文件读写）。默认开；关掉回旧行为。
            writing_boundary=bool(dcfg.get("writing_boundary", True)),
            # 纯文本写作（2026-11-27）：stage4 正文一次 completion + Python 落盘，
            # 对齐 ainovel-cli 的 draft_chapter / Sudowrite Draft。默认开；
            # 关掉回到“agent 循环 + 工具写盘”（慢，但可作回退）。
            text_writing=bool(dcfg.get("text_writing", True)),
        )

    # 2026-11-25（F8 第三步）：direct / hermes 两条分支已摘除。
    # 墨坊只认 dsh（模型调用统一交给 DSH）；self 在上面已经拦过。
    # 走到这里说明配置里还写着 direct/hermes，或调用点传了未知引擎 —— 直接说清去哪改。
    raise RuntimeError(
        "[client] 引擎 %r 不可用：墨坊已不再自带模型调用（2026-11-25 作者定案，F8）。"
        "请把 config/system.yaml 的 engine 改回 dsh；"
        "engine: self 只产任务书、不调模型。" % str(engine))
