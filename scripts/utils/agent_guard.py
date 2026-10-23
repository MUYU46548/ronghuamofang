# -*- coding: utf-8 -*-
"""Agent 模式安全守卫（HTTP 与 CLI 的**同一份判据**）。

## 语义（2026-09-21 用户确认，2026-10-03 补齐 CLI 侧，2026-10-04 TTY 加固）

`gates.agent_mode = true` 时：**外部 Agent 可读、可跑流水线，但不能代替用户审批**。
这是设计，不是 bug。

## 方案A（2026-10-15 用户拍板）：加固档下 CLI 审批**无条件拒绝**

原口径是「正路 = 真实交互式终端（TTY 快筛 + 祖先链取证），伪造须留痕可追责」。
用户拍板翻案，原话：**「都用桌面端了，没人会用 CLI」** —— 于是加固档下 CLI 审批
（`approve.py` / `reject.py` / `segment_cli.py` 勾段与撤销）**无论证据多完美一律拒绝**
（判据唯一实现 = `cli_approval_gate()`，策略原因进台账），审批的唯一入口 = 桌面端 GUI 双因子。

- 本文件其余 V1/V2/fail-closed 规格**继续有效**，但语义降级为**证据采集与留痕**：
  `cli_human_evidence` 仍是唯一证据实现，**正判不再等于放行**；
- 祖先链 / TTY 机制保留作纵深防御（2026-10-15 红队深度截断修复 `?` 标记不回退）；
- 标准档（`agent_mode=false`）的 CLI 语义与 HTTP 双因子路径**不受影响**。

## 为什么要有本模块

原先这条守卫只写在 `nf_api.do_POST` 里（HTTP 侧，靠 `X-Mofang-Source: gui` 头区分来源），
而**同样的动作还有一条 CLI 后门**：外部 Agent 完全可以

    python scripts/approve.py --stage 2

绕过 403 直接审批 —— 判据写在 HTTP 层，CLI 层就是敞开的。
现在两侧共用本模块：禁止清单、模式判定、审计日志各只有一份实现。

## 两档取证强度（2026-10-04 TTY 加固版，修复单第三节）

| 档位 | 触发 | CLI 人工来源的证据 |
|------|------|--------------------|
| 标准档 | `agent_mode=false` | ① 环境变量 `MOFANG_SOURCE=gui`；② 双端交互式 TTY；③ 显式 `--human`（历史语义；守卫关闭时调用方根本不进本函数） |
| **加固档** | `agent_mode=true` | **只有「真实交互式终端」一种**：双端 TTY（前置快筛）+ **祖先进程链取证**（主判据，链须落在用户 shell/explorer、不得经过自动化宿主） |

HTTP 侧入口自 2026-10-06 起为**双因子**：来源头 `X-Mofang-Source: gui`（渲染层 `api()` 携带）
**加**主进程签发的 `X-Mofang-Token`，判据唯一实现见 `is_trusted_gui()` —— 单靠来源头
可被任何本机进程伪造（见该函数上方的红队复现）。CLI 没有请求头，加固档改用进程链取证
代替「声明式」证据。

加固档三条硬规格（信息哨兵三缺口钉死，勿回退）：

- **V1/V2 死透**：`--human` 与 `MOFANG_SOURCE=gui` 在 agent_mode=true 下**不构成人工证据**
  （一个 flag / 一个环境变量自证人类不成立）。声明只进台账留痕，不进判定 ——
  这两处排在 TTY 判定之前（旧序 :84-89），只修 isatty = 白修。
- **isatty 只是快筛**：pty 伪终端两端 isatty 同样为 True；Windows Terminal 的 ConPTY
  与 pywinpty 在子进程视角同构 —— **禁止把双端 isatty 当作充分人工证据**
  （照抄旧逻辑，验收第 1 条物理不可实现）。
- **fail-closed**：祖先进程链读不到 = 无法确认人工 = 拒绝，绝不反向放行。

**残余风险如实声明**：进程链可被伪造 —— 按红队放行口径判「伪造必须显式留痕可追责」
（完整链与声明痕迹全部写进 `data/state/agent_audit.jsonl` 台账），不判「伪造不可能」。
**这不是沙箱**：它拦的是「Agent 照着文档跑一条命令就悄悄替用户拍板」，
不要对外宣称更强。
"""
import hmac
import json
import os
import sys
import time
from pathlib import Path

# Agent 模式下**永不允许**非 GUI/非人工来源执行的动作（与 nf_api.do_POST 同源）
FORBIDDEN_IN_AGENT_MODE = frozenset({
    "/approve",              # 审批
    "/reject",               # 打回
    "/project/create",       # 新建项目
    # 铺设出厂预设（2026-10-21）：与 /project/create 完全同族 —— 快照 →
    # 归档当前项目 → 铺素材 + 写 project.yaml。Agent 一条命令就能搬空用户
    # 工作区，故与新建/归档同档。端点自身还要求 GUI 来源头（双因子）。
    "/factory/preset/apply",
    "/project/archive",      # 归档
    "/project/restore",      # 恢复
    "/project/init",         # 首启初始化
    # 删除归档 = 抹掉成书数据（2026-10-06 补）：与归档/恢复同档。
    # 端点自身还要求 X-Mofang-Source: gui，这里是第二道闸。
    "/project/archive/delete",
    "/config/agent_mode",    # Agent 不得自行切换模式
    # Agent 不得抬高自己的闸门（2026-10-03）：止烧阈值 = 成本与安全设置。
    # 不拦的话，外部 Agent 只要 POST 一次把上限调到天上，"止烧"就是摆设。
    "/config/token_limit",
    # 清理出厂样例（0.6.7）：移动用户工作区素材文件 = 破坏性写操作，
    # 只读的 /factory/list 不在此列（外部 Agent 可以盘点，不能动手）。
    "/factory/clean",
    # 出厂测试项目一键还原（0.6.22）：清书状态 + 重铺素材 + 写回配置。
    # 与铺设端点完全同族（铺/还原是一对）。端点自身还要求 is_trusted_gui
    # （双因子）—— 只读的 GET /factory/project 不在此列（Agent 可盘点）。
    "/factory/project/reset",
    # 规划四态状态机（丙0）：AI 可提案，但选择/写进正文/丢弃/复活归用户
    "/plan/select",
    "/plan/write",
    "/plan/drop",
    "/plan/revive",
    # Word 稿导入（活二，2026-10-13）：一次性把**整本外部稿**写进
    # data/chapters/{raw,checked,refined}/，且 --overwrite 能盖掉已有章节
    # —— 与归档/恢复同档的批量写入，归用户点。预览走同一端点（GUI 一体），
    # 只读的 /chapters/*、/outline/chapters/* 不在此列。
    "/import/docx",
    # 大纲分段审批标记（丙4，2026-10-14）：勾一段 = 替用户读了那一段，
    # 而分段的全部意义就是「不降低强度、全过才放行」—— 让 Agent 自己勾满
    # 八段再放行，等于把审阅义务整个绕过。读侧 GET /outline/segments 不拦。
    "/outline/segments/set",
    # 人工跳过阶段（P1 补，2026-10-16）：/stage/skip 把阶段标成 done+已审批+skipped，
    # approved 是**真写进 progress.json** 的 —— 与审批同族，却长期漏在名单外：
    # Agent 一发 POST {stage, confirm:true} 就能替用户放行管线（跳过虽回报缺失
    # 产物，但审批状态已落地）。GUI（真 token）走 trusted_gui，在名单检查之前，
    # 不受影响；纯请求在 agent_mode 下 fail-closed，正是该有的语义。
    "/stage/skip",
    # 讨论提案审批（P0-1 落地协议，2026-10-18）：approve 会把优化点**真正写回
    # 章大纲**（data/outline/chapters/NN.md）—— 与审批同族。提案提交
    # （/outline/discussion/apply）只落草稿区，不在名单里（agent 可提案不可自批）。
    "/outline/discussion/review",
})

# 人工来源的标记值（HTTP 头 / 环境变量通用）
HUMAN_SOURCES = frozenset({"gui", "human"})

AUDIT_LOG_PATH = "data/state/agent_audit.jsonl"

# ---------------------------------------------------------------- 祖先链取证（2026-10-04）
# 规格（修复单第三节主案）：主判据 = 父进程链 / 控制台进程表 ——
# 祖先链须落在**用户 shell / explorer**，而非 node / hermes / pythonw 一类自动化宿主。
# core = 小写去路径去 .exe 的进程名。
#
# 用户交互终端锚点（祖先链上至少命中其一）。
TERMINAL_ANCHOR_CORES = frozenset({
    "explorer", "cmd", "powershell", "pwsh", "bash", "sh", "zsh",
    "mintty", "windowsterminal", "wt",
})
# 自动化宿主（祖先链上出现即拒）。含 hermes（执行者宿主）与 winpty（pty 孵化器）。
AUTOMATION_HOST_CORES = frozenset({"node", "python", "pythonw", "python3", "py"})
AUTOMATION_HOST_PREFIXES = ("hermes", "winpty")

# ancestry 注入哨兵：区分「未提供 → 实测进程表」与「提供了（哪怕 None/空表）→ 用提供的」。
# 测试用注入构造正/反例链；真实运行一律实测。
_ANCESTRY_UNSET = object()


def load_project_config():
    """CLI 守卫读配置：**CWD 项目优先**，缺文件回退脚本仓根（两侧共用的单一实现）。

    判据自 2026-10-05 起统一实现在 `config_io.load_config_yaml`（相对路径 CWD 优先、
    缺失回退仓根）—— 本函数只做「守卫读配置绝不抛」的容错包装。
    为什么必须 CWD 优先：approve/reject 的 progress.json 是 **CWD 相对**（= 所操作
    项目的根），配置若读到另一个仓，就出现「批 A 项目的进度、按 B 项目的 agent_mode 判」
    （2026-10-04 修复单 Step 4 回归发现；测试床与多项目/`--root` 场景同源）。
    """
    try:
        from utils.config_io import load_config_yaml
        return load_config_yaml("config/system.yaml") or {}
    except Exception:                                       # noqa: BLE001
        return {}


def agent_mode_enabled(cfg):
    """`gates.agent_mode` 是否开启。cfg 为 None/异常结构一律按关闭处理。"""
    try:
        return bool(((cfg or {}).get("gates") or {}).get("agent_mode", False))
    except Exception:                                       # noqa: BLE001
        return False


def normalize_source(raw):
    """归一来源标记。返回 ""（未声明）/ "gui" / "human" / 原样小写（如 "agent"）。"""
    return str(raw or "").strip().lower()


def is_forbidden_in_agent_mode(path):
    """该路径在 Agent 模式下是否禁止非人工来源调用。"""
    return str(path or "") in FORBIDDEN_IN_AGENT_MODE


# ---------------------------------------------------- GUI 来源可信度（2026-10-06 红队修复）
#
# 为什么不能只看 `X-Mofang-Source: gui`：那是**客户端自述**，任何本机进程随手可伪造。
# 实测复现（2026-10-06）：gates.agent_mode=true 下对 /project/archive/delete
#   无来源头 → 403；伪造 gui 头 → 200 且真删成功（目录进 _trash）。
# 根因是**两道闸读同一个可伪造的头**：do_POST 的禁用名单靠它跳过、
# 端点内的 _gui_only 靠它放行 —— 同源等于一层没有，全部写入守卫形同虚设。
#
# 修法 = **双因子**：① 来源头（保留，供审计分类）+ ② 主进程签发的 token ——
# Electron 主进程 crypto.randomBytes(32) 生成，spawn nf_api 时经 env 注入，
# 渲染层经 IPC gui-token:get 取得后随请求带上。对**用户正在跑的那个实例**，
# token 不在请求方掌控之内，光伪造头拿不到它（2026-10-06 红队口径）。
#
# 边界如实声明（2026-10-16 红队 A11 补验修正措辞）：同机攻击者可以**自起一份
# nf_api 并自铸 token**（`NF_GUI_TOKEN=<自设>`）整体穿透本名单 —— 但这不比
# 「直接手改 data/state/*.json」多出任何能力，同属已声明残余（这不是沙箱，
# 防线语义 = 拦住照文档跑命令的 Agent）。勿把双因子宣称为同机攻击模型下的保证。
#
# **fail-closed**：nf_api 未被注入 token（独立启动 / CLI / 测试）→ 一律不认 GUI 来源。
# 这是正确语义：这些端点本就只该由桌面端调用，没有桌面端就不该有 GUI 权限。
GUI_TOKEN_ENV = "NF_GUI_TOKEN"
GUI_SOURCE_HEADER = "X-Mofang-Source"
GUI_TOKEN_HEADER = "X-Mofang-Token"


def issued_gui_token():
    """主进程签发的 GUI token（nf_api 启动环境注入）。空串 = 未签发。"""
    try:
        return str(os.environ.get(GUI_TOKEN_ENV, "") or "").strip()
    except Exception:                                       # noqa: BLE001
        return ""


def is_trusted_gui(headers, token=None):
    """该 HTTP 请求是否**可信**为 GUI 本体 —— HTTP 侧唯一的 GUI 判据。

    为什么只有一份：`do_POST`（禁用名单）与域模块 `_gui_only`（端点闸）
    必须共用本函数，否则两边判据漂移就重新裂出后门（与本模块拒绝清单同理）。

    参数：
      - headers：请求头映射（`self.headers` / `h.headers`）
      - token：注入用（测试传值）；缺省取环境变量

    返回 False 的三种情形**都必须拒绝**：未签发 token / 来源头不是 gui / token 不匹配。
    `hmac.compare_digest` 防时序侧信道。
    """
    if token is None:
        token = issued_gui_token()
    if not token:
        return False                                        # 未签发 → 不认
    try:
        if normalize_source(headers.get(GUI_SOURCE_HEADER, "")) != "gui":
            return False
    except Exception:                                       # noqa: BLE001
        return False
    got = str(headers.get(GUI_TOKEN_HEADER, "") or "")
    return hmac.compare_digest(got, token)


def _proc_core(name):
    """进程名 → core（小写、去路径、去 .exe/.com 后缀）。"""
    base = os.path.basename(str(name or "")).strip().lower()
    for ext in (".exe", ".com"):
        if base.endswith(ext):
            base = base[:-len(ext)]
    return base


def _is_automation_host(name):
    """该祖先是否是自动化宿主（node / hermes / python / winpty …）。"""
    core = _proc_core(name)
    if core in AUTOMATION_HOST_CORES:
        return True
    return any(core.startswith(p) for p in AUTOMATION_HOST_PREFIXES)


def _is_terminal_anchor(name):
    """该祖先是否是用户交互终端锚点（shell / explorer / Windows Terminal）。"""
    return _proc_core(name) in TERMINAL_ANCHOR_CORES


def _win_proc_info(pid):
    """Windows：返回 (parent_pid, core_name)；读不到返回 (None, None)。

    同一 OpenProcess 句柄取父链（NtQueryInformationProcess）与镜像名
    （QueryFullProcessImageNameW）——两个查询都要句柄，一次拿全。
    PROCESS_QUERY_LIMITED_INFORMATION 跨完整性级别可读，用户级祖先链
    （shell / explorer / WindowsTerminal）全部可达；读不到 = 链截断，
    由调用方 fail-closed。
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:                                       # noqa: BLE001
        return None, None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ProcessBasicInformation = 0

    class _PBI(ctypes.Structure):
        _fields_ = [
            ("Reserved1", ctypes.c_void_p),
            ("PebBaseAddress", ctypes.c_void_p),
            ("Reserved2", ctypes.c_void_p * 2),
            ("UniqueProcessId", ctypes.c_void_p),
            ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None, None
    try:
        pbi = _PBI()
        status = ntdll.NtQueryInformationProcess(
            handle, ProcessBasicInformation, ctypes.byref(pbi),
            ctypes.sizeof(pbi), None)
        parent = int(pbi.InheritedFromUniqueProcessId or 0) if status == 0 else None
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        name = None
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            name = _proc_core(buf.value)
        return parent, name
    except Exception:                                       # noqa: BLE001
        return None, None
    finally:
        k32.CloseHandle(handle)


def _posix_proc_info(pid):
    """非 Windows 的 /proc 读取（Linux 可用；macOS 读不到 → fail-closed）。"""
    try:
        stat = Path("/proc/%d/stat" % pid).read_text(encoding="utf-8", errors="replace")
        rest = stat[stat.rindex(")") + 1:].split()
        parent = int(rest[1])
        name = _proc_core(Path("/proc/%d/comm" % pid).read_text(
            encoding="utf-8", errors="replace").strip())
        return parent, name
    except Exception:                                       # noqa: BLE001
        return None, None


def probe_ancestry(max_depth=48):
    """实测祖先进程链：返回**从父进程向上**的 core 名列表（近 → 远）。

    读不到任何父链时返回 `None`（取证失败，调用方 fail-closed 拒绝）；
    中途某个祖先读不到名字 → 记 "?" 后截断（截断链同样拒绝）；
    **走满 max_depth 仍未到锚点/链尾 → 同样记 "?"**（深度上限截断与读名失败同款
    fail-closed——2026-10-15 红队实弹 cmd×48 曾在此静默放行，勿回退）。
    """
    try:
        info = _win_proc_info if os.name == "nt" else _posix_proc_info
        names = []
        seen = {os.getpid()}
        pid = info(os.getpid())[0]
        depth = 0
        while pid and pid > 0 and pid not in seen:
            if depth >= max_depth:
                # 深度上限截断 = 与「读不到名字」同款截断标记（2026-10-15 红队实弹：
                # cmd×48 把自动化宿主推出上限外，旧实现静默返回"看似完整"的链 → fail-open）。
                # 走满 max_depth 仍未落到锚点/链尾 = 无法核验，fail-closed。
                names.append("?")
                break
            seen.add(pid)
            parent, name = info(pid)
            if not name:
                names.append("?")          # 截断标记：链在此断掉（截断段先于锚点 = fail-closed）
                break
            names.append(name)
            if name == "explorer" or not parent or parent in seen:
                # explorer = 桌面锚点即停：其上是 winlogon/csrss/smss/System 等系统管线，
                # 继续读只会撞 OpenProcess 失败（真实链顶端必然出 "?"）造成误伤。
                break
            pid = parent
            depth += 1
        return names
    except Exception:                                       # noqa: BLE001
        return None


def cli_approval_gate(explicit_human=False, source_env=None, isatty=None,
                      ancestry=_ANCESTRY_UNSET):
    """CLI 审批闸（**方案A**，2026-10-15 用户拍板：「都用桌面端了，没人会用 CLI」）。

    只应在 `agent_mode=true` 分支调用（调用方已判模式）。返回 `(allowed, evidence)`：

    - `allowed` **恒 False** —— 加固档下 CLI 审批（approve / reject / 分段勾选）
      无论 TTY / 祖先链 / 声明证据多完美，一律拒绝；审批唯一入口 = 桌面端 GUI 双因子。
    - `evidence` = 策略原因 + `cli_human_evidence` 的原证据（含声明痕迹），供台账留痕
      —— 证据照采，只是不再构成放行依据。

    历史：2026-10-04 V5 的正路「真实交互式终端」自此废止；`cli_human_evidence`
    原样保留（标准档语义 + 证据层 + 纵深防御）。**勿把本函数改回「证据好就放行」** ——
    那会让 CLI 重新变成守卫最脆的攻击面（红队 63 向量里一多半攻的就是那条路）。
    """
    _ok, ev = cli_human_evidence(source_env=source_env, isatty=isatty,
                                 explicit_human=explicit_human,
                                 agent_mode=True, ancestry=ancestry)
    return False, ("方案A(2026-10-15): 加固档 CLI 审批一律拒绝、只走桌面端"
                   "（原正路「真实交互终端」废止）；原证据: " + ev)


def cli_human_evidence(source_env=None, isatty=None, explicit_human=False,
                       agent_mode=False, ancestry=_ANCESTRY_UNSET):
    """判定「本次调用是否人工来源」。返回 (is_human, 证据说明)。

    agent_mode=False（标准档，历史语义）：
        MOFANG_SOURCE 声明 > 显式 --human > 双端 TTY。守卫关闭时调用方
        根本不进本函数，此档仅为接口兼容与反证测试保留。

    agent_mode=True（**加固档**，2026-10-04 TTY 加固主案）：
        声明类通道（MOFANG_SOURCE / --human）一律不构成人工证据，只留痕；
        双端 TTY 只是前置快筛；主判据 = 祖先进程链取证（见模块头规格）；
        取证失败 fail-closed。拒绝证据（含声明痕迹与进程链）进台账。

    参数注入（仅测试）：
        isatty: bool 或 (stdin_isatty, stdout_isatty)；默认查真实 stdin/stdout。
            **必须两个都是 TTY** —— 只查 stdout 的话，
            `echo x | python scripts/approve.py ...` 这类管道会被误判成人工。
        ancestry: 祖先链列表（近→远）或 None（模拟取证失败）；
            缺省（哨兵）时实测进程表。
    """
    src = normalize_source(source_env if source_env is not None
                           else os.environ.get("MOFANG_SOURCE"))

    # ---- 加固档（agent_mode=true）：V1/V2 声明死透 + TTY 快筛 + 祖先链主判据 ----
    if agent_mode:
        declared = []
        if src:
            declared.append("声明来源=%s" % src)
        if explicit_human:
            declared.append("声明--human")
        decl_note = ("；附带声明（不构成人工证据）: " + "、".join(declared)) if declared else ""

        if isatty is None:
            try:
                isatty = (bool(sys.stdin.isatty()), bool(sys.stdout.isatty()))
            except Exception:                               # noqa: BLE001
                isatty = (False, False)
        if isinstance(isatty, (tuple, list)):
            stdin_tty, stdout_tty = bool(isatty[0]), bool(isatty[-1])
        else:
            stdin_tty = stdout_tty = bool(isatty)
        if not (stdin_tty and stdout_tty):
            # 前置快筛未过：管道 / 非 TTY / 只有一端 —— 不必查进程表即可拒
            return False, "非交互式（无双端 TTY），非真实人工终端" + decl_note

        chain = probe_ancestry() if ancestry is _ANCESTRY_UNSET else ancestry
        if chain is None:
            return False, "祖先进程链取证失败（fail-closed，无法确认人工终端）" + decl_note
        chain_txt = " → ".join(str(c) for c in chain[:16]) or "（空链）"
        for hop in chain:
            if hop != "?" and _is_automation_host(hop):
                return False, "祖先链含自动化宿主 %s（链: %s）" % (hop, chain_txt) + decl_note
        if "?" in chain:
            # 链在读不到的祖先处截断 → 截断段无法核验，fail-closed（留痕）
            return False, "祖先进程链不完整（截断于未知祖先: %s）" % chain_txt + decl_note
        if not any(_is_terminal_anchor(hop) for hop in chain):
            return False, "祖先链未落在用户交互终端（链: %s）" % chain_txt + decl_note
        return True, "双端 TTY + 祖先链取证通过（链: %s）" % chain_txt + decl_note

    # ---- 标准档（agent_mode=false）：历史语义，逐通道保持原判 ----
    if src in HUMAN_SOURCES:
        return True, "MOFANG_SOURCE=%s" % src
    if src:
        return False, "MOFANG_SOURCE=%s（非人工标记）" % src
    if explicit_human:
        return True, "显式 --human"
    if isatty is None:
        try:
            isatty = (bool(sys.stdin.isatty()), bool(sys.stdout.isatty()))
        except Exception:                                   # noqa: BLE001
            isatty = (False, False)
    if isinstance(isatty, (tuple, list)):
        stdin_tty, stdout_tty = bool(isatty[0]), bool(isatty[-1])
    else:
        stdin_tty = stdout_tty = bool(isatty)
    if stdin_tty and stdout_tty:
        return True, "交互式终端"
    if stdin_tty or stdout_tty:
        return False, "只有一端是终端（另一端被管道接管，视为非人工）"
    return False, "非交互式（无 TTY）、未声明 MOFANG_SOURCE、也未显式 --human"


def refusal_message(action, evidence, entry="CLI"):
    """拒绝执行的可行动文案（单一来源，两侧共用）。

    **evidence 刻意不进拒文**（2026-10-04 V5 拒文泄题修复）：
    拒文本身是攻击面 —— 旧文案主动向挨拒者传授 `--human` / `MOFANG_SOURCE=gui`
    通道与「把 gates.agent_mode 改 false」的退路，挨拒者照方抓药即可绕守卫
    （红队向量 V5：拒文本身是攻击面）。
    agent_mode=true 下拒文**零通道名**：只保留「停手并报告用户」类指引；
    技术证据（evidence，可能含声明痕迹 / 进程链）只写 agent_audit.jsonl 台账。
    """
    return (
        "[guard] 拒绝执行：%s 在 gates.agent_mode=true 下只允许用户本人操作，"
        "而本次调用不是人工来源（入口 %s）。\n"
        "        含义：外部 Agent 可以读、可以跑流水线，但不能代替用户审批"
        "（设计如此，不是故障）。\n"
        "        请立即停手：不要重试，不要更换调用方式，不要改动任何配置。\n"
        "        将本条信息原样报告用户，由用户本人决定下一步。"
        % (action, entry)
    )


def log_audit(path, method="POST", status=200, source="gui", detail="", root=None):
    """审计日志（data/state/agent_audit.jsonl）。**任何异常都不得影响主流程**。

    detail 上限 600 字符（2026-10-05 红队实测修正：原 [:120] 会把祖先链证据之后的
    **声明痕迹**切掉——链文本一长，`声明来源=gui / 声明--human` 就只剩半句
    「；附带声明（不构」，V1/V2 的留痕可追责性被自己截没了）。
    """
    try:
        base = Path(root) if root else Path(".")
        target = base / AUDIT_LOG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "method": method, "path": path,
            "status": status, "source": source, "detail": str(detail)[:600],
        }, ensure_ascii=False)
        with open(target, "a", encoding="utf-8", newline="\n") as f:
            f.write(entry + "\n")
    except Exception:                                       # noqa: BLE001
        pass
