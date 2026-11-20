# -*- coding: utf-8 -*-
"""DSH（DeepSeek Harness）headless 引擎客户端 —— 换内核用的第三个引擎（2026-11-17）。

## 为什么单开文件（不塞进 llm_client.py）

`llm_client.py` 已经 1,900 多行（直连 + hermes 两个引擎）。第三个引擎的差异集中在
**协议**（`dsh --profile headless --json` 的 NDJSON 事件流）与**进程语义**
（启动器 → cmd.exe → Electron-as-node 三层进程树），与两个老引擎几乎不共享代码。
同目录单开文件，谁改谁读，不把主文件再堆厚一层。

## 契约（与 HermesClient 同构，调用点零改动）

    run_task(task_file, workdir=None, model=None, session_id=None) -> dict
    run_task_stream(task_file, on_piece, stop_flag, ...)          -> dict
    _post_chat(messages, temperature=None, max_tokens=None)       -> (text, usage, model, finish)
    _post_chat_stream(messages, ..., on_chunk=None, stop_flag=None)
    write_task(task_dir, name, content) -> Path
    writes_files = True     # 正文由子进程自己的文件工具落盘（与 hermes 同语义）

## 协议要点（真源：`dsh headless --help` 与 DSH 自带的 dsh-headless 说明）

- 命令：`dsh --profile headless --json -` —— 任务从 **stdin** 读（不经 shell 解释，
  引号/换行/中文标点原样保留，也不受 Windows 命令行长度上限约束）。
- stdout = 按行 JSON 事件；stderr = `dsh:` 诊断；**退出码是权威判据**
  （0 = 最终 turn/end 完成；其他 = 中止/出错/没有轮次）。
- 事件词汇：`session`（首，带 sessionId/cwd）、`status`（turn/step 边界，`step_end`
  带 usage）、`text` / `thinking`（提交后的正文/思考块）、`tool_call` / `tool_result`、
  `final`（尾，带无损答案 + `reason.kind`）、`error`（轮次外失败，此时**没有** final）。
- `final.reason.kind`：`completed` / `aborted` / `blocked` / `error` / `max-tokens`
  以及崩溃类扩展值。**`max-tokens` 必须映射成 `finish_reason="length"`** —— 与直连引擎
  同语义（输出被截断 = 正文不完整 = 不许当成品落盘）。
- 凭据：headless profile 默认**禁用** DSH GUI 的登录态存储，按“启动环境 → 工作目录
  `.env` → `$DSH_HOME/.env`”找 provider key。要让 headless 用 GUI 登录态，需在
  `$DSH_HOME/profiles/headless/cordis.patch.yml` 里把 credentials/authorization/
  deepseek-account 三行打开（DSH 官方文档给的做法）。**本类不替用户改那个文件**。
- 模型：headless **没有** `--model` 旋钮，模型由 profile 决定；`model` 参数照旧收下但
  不拼进命令（与 hermes 的“用 agent 内部模型”同口径），结果里的 `model` 如实标
  `dsh-default`，不冒充具体模型名。

## 三条硬判据（会变红的那几条）

1. **dsh 不存在 → 明确失败**（exit 127 + 可行动报错），绝不静默、绝不假 exit 0。
2. 超时 → `exit_code=124` 结构化失败（不抛 TimeoutExpired 穿透 run_stage 打崩整轮）。
3. 杀掉时不留孤儿进程（Windows 上 dsh.cmd 孵 cmd.exe 再孵 Electron，必须整树杀）。

## 已知边界（如实声明，不假装没有）

- headless **每次调用只跑一个任务**，没有交互式后续；多步工作靠多次调用 + `--session-id`。
- `--json` 事件是被投影过的（单条 32 KiB、字符串 8 KiB 上限，`final` 除外），
  所以“事件流里没有某段工具输出”不等于没发生；判成败只看退出码 + `reason.kind`。
- `text` 事件是**提交后**的助手文本，不是逐 token 流；它在步骤提交时到达，
  因此流式回放是“一段一段”而非“一个字一个字”（与 hermes 的 text 事件同级）。
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from utils.console import ensure_utf8_stdout
from utils.file_io import write_text

ensure_utf8_stdout()

#: 显式指定 dsh 可执行文件的环境变量（优先于安装位置探测）
DSH_BIN_ENV = "NF_DSH_BIN"

#: Windows 安装器的 CLI 入口相对路径（vendor 布局，不是本机私有路径）
_WIN_CLI_REL = ("Programs", "DeepSeek Harness", "resources", "runtime", "cli", "bin", "dsh.cmd")

#: 子会话执行边界（与 hermes 同口径，只换掉“谁的目录不该翻”）
DSH_SCOPE_RULE = (
    chr(10) + chr(10) + "【执行边界】" + chr(10)
    + "· 该读的：任务文件里**列出的**输入路径（包括作者配置的设定库 / 参考书 / 范文目录，"
    "它们可能在工作区之外，照读）+ 当前工作区内的文件。" + chr(10)
    + "· 不该翻的：DeepSeek Harness 自己的安装目录与配置目录（%LOCALAPPDATA%/Programs/"
    "DeepSeek Harness、~/.dsh 下的会话与插件）—— 那不是你的资料。" + chr(10)
    + "· 不要安装依赖、不要联网、不要改 .git。"
)

#: 单轮直答前置说明（与 hermes 的 ONE_SHOT_PREFIX 同语义：答案必须在回复里）
ONE_SHOT_PREFIX = (
    "这是一次**单轮直答**调用：请直接在这条回复里输出最终结果（正文 / JSON）。" + chr(10)
    + "不要调用任何工具，不要写文件，不要解释你的思考过程。" + chr(10)
    + "你用写文件工具写出来的东西不会被读取 —— 那等于没有回答。" + chr(10)
    + "本次需要的材料已经全部在下面，不需要去翻任何本地文件。" + chr(10)
    + "------------------------------------------------------------"
)


def _safe_unlink(path):
    """删临时 patch 文件（失败无声：Temp 目录由系统回收，不该因此报错）。"""
    try:
        os.unlink(path)
    except OSError:
        pass


def build_model_patch(spec):
    """把 `{"provider": ..., "model": ...}` 写成 DSH 的 `--patch` 文件。

    ## 为什么需要它（2026-11-20）

    headless **没有** `--model` 旋钮，模型由 profile 的 `agent-default-model`
    插件决定 —— 这正是"角色模型表在 dsh 下形同虚设"的原因。但 `dsh --patch`
    能在 profile 层之后再叠一层 loader patch，覆盖那个插件的 `config`。
    于是"某个角色用某个模型"可以按**每次调用**生效，而不是改全局 profile。

    验证方式（零成本，不发请求）：
        dsh --profile headless --patch <本函数生成的文件> --dump-config
        → `agent-default-model` 的 config.provider / config.model 变成这里写的值

    返回 patch 文件路径；spec 不完整或写盘失败返回 None（= 用 profile 默认）。
    """
    if not isinstance(spec, dict):
        return None
    prov = str(spec.get("provider") or "").strip()
    model = str(spec.get("model") or "").strip()
    if not prov or not model:
        return None
    # YAML 是 JSON 的超集：用 json.dumps 生成**带引号**的标量，省掉自己写转义。
    body = (
        "- id: agent-default-model\n"
        "  name: '@deepseek-ai/dsh-agent-default-model'\n"
        "  config:\n"
        "    provider: " + json.dumps(prov, ensure_ascii=False) + "\n"
        "    model: " + json.dumps(model, ensure_ascii=False) + "\n"
    )
    try:
        fd, path = tempfile.mkstemp(prefix="nf-dsh-model-", suffix=".yml")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
    except OSError:
        return None
    atexit.register(_safe_unlink, path)
    return path


def resolve_dsh_bin(explicit=None):
    """定位 dsh 启动器。顺序：显式参数 → NF_DSH_BIN → Windows 安装位置 → PATH。

    找不到时**返回 "dsh"**（而不是抛异常）：真正的失败留给 Popen 的 OSError，
    由 `_finish` 统一收敛成 `exit_code=127` + 可行动报错 —— 这样“没装 DSH”
    与“DSH 起不来”走同一条可见路径，不会有人在这里静默吞掉。

    `nfctl check` 也调本函数（判据单源：谁都用同一套定位规则）。
    """
    cand = str(explicit or os.environ.get(DSH_BIN_ENV) or "").strip().strip('"')
    if cand:
        return cand
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        p = Path(local).joinpath(*_WIN_CLI_REL)
        if p.exists():
            return str(p)
    for name in ("dsh", "dsh.cmd", "dsh.exe", "dsh.bat"):
        found = shutil.which(name)
        if found:
            return found
    return "dsh"


def dsh_available(bin_path=None):
    """dsh 是否可用（供 `nfctl check` 用）。返回 (ok, 说明)。

    说明里必须是**可行动**的一句话：装什么、或把路径写进哪。
    """
    b = resolve_dsh_bin(bin_path)
    p = Path(b)
    if p.is_absolute():
        if p.exists():
            return True, "dsh 可用：" + str(p)
        return False, ("dsh 不在预期位置：" + str(p) + "。装 DeepSeek Harness，"
                       "或在 config/system.yaml 的 dsh.bin 写绝对路径（也认环境变量 NF_DSH_BIN）")
    found = shutil.which(b)
    if found:
        return True, "dsh 可用：" + found
    return False, ("dsh 不在 PATH，也没找到 Windows 安装位置。要用 engine: dsh 请先装 "
                   "DeepSeek Harness，或在 config/system.yaml 的 dsh.bin 写启动器绝对路径"
                   "（也认环境变量 NF_DSH_BIN）。不用 DSH 就保持 engine: hermes/direct。")


class DshClient:
    """DSH headless 执行层（与 HermesClient 同契约）。"""

    #: 无人值守默认：没有实时审批通道，需要审批的调用一律拒绝（DSH 文档明确）
    DEFAULT_APPROVAL_POLICY = "never"

    #: 正文由子进程自己用文件工具落盘（stage4 据此按产物字数判进度）
    writes_files = True

    def __init__(self, dsh_bin=None, timeout=1800, model=None, permission_mode=None,
                 approval_policy=None, session_continuation=False,
                 request_max_tokens=None, add_dirs=None, model_spec=None):
        self.dsh_bin = resolve_dsh_bin(dsh_bin)
        self.timeout = int(timeout or 1800)
        # 模型由 profile 决定（headless 没有 --model 旋钮）；这里只做记录
        self.model = model
        # 按角色指定模型（2026-11-20）：{"provider":..., "model":...} → --patch。
        # 不传 = 用 profile 默认，行为与之前完全一致。
        self.model_spec = dict(model_spec) if isinstance(model_spec, dict) else None
        self._patch_path = build_model_patch(self.model_spec)
        # None → 不传 --permission-mode，用 profile 自带的 sandbox 模式
        # （headless patch 的默认是 workspace-write，工作区 = 进程 cwd）
        self.permission_mode = str(permission_mode or "").strip() or None
        self.approval_policy = self.DEFAULT_APPROVAL_POLICY if approval_policy is None \
            else (str(approval_policy).strip() or "")
        # 会话续接默认关（与 hermes 同：这是行为变更，要有实测收益才开）
        self.session_continuation = bool(session_continuation)
        # headless 同样没有 max_tokens 旋钮 → 只做事后比对+告警（见 note_request_tokens）
        try:
            self.request_max_tokens = int(request_max_tokens or 0) or None
        except (TypeError, ValueError):
            self.request_max_tokens = None
        self.add_dirs = [str(d) for d in (add_dirs or []) if str(d).strip()]

    # ------------------------------------------------------------ 自检/告警

    def note_request_tokens(self, tokens_out):
        """单次子会话输出与配置上限比对：超了**大声告警**（不阻断、不丢产物）。

        与 HermesClient 同一理由：headless 无 max_tokens 旋钮，拦不住“这一次写太多”，
        但使用者必须看得见；要不要停机由 CostTracker（唯一判据）决定。
        """
        if not self.request_max_tokens:
            return False
        try:
            n = int(tokens_out or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= self.request_max_tokens:
            return False
        print("[dsh_client] WARN 单次子会话输出 %d token > 单请求上限 %d"
              "（config/system.yaml 的 budget.token_limit.per_request_max_tokens）。"
              "headless 无 max_tokens 旋钮 → 本次**不阻断**；"
              "要按次熔断请把 budget.token_limit.per_request_pause_hermes 设为 true"
              % (n, self.request_max_tokens))
        return True

    # ------------------------------------------------------------ 命令构造

    def _build_cmd(self, session_id=None):
        """`dsh --profile headless --json [选项] -`（任务走 stdin）。"""
        cmd = [self.dsh_bin, "--profile", "headless", "--json"]
        if self.approval_policy:
            cmd += ["--approval-policy", self.approval_policy]
        if self.permission_mode:
            cmd += ["--permission-mode", self.permission_mode]
        for d in self.add_dirs:
            cmd += ["--add-dir", d]
        # 角色模型（2026-11-20）：patch 必须排在 profile 层之后（--patch 本身就是），
        # 才能覆盖 agent-default-model。文件不存在时宁可不传，让失败暴露在模型层。
        if self._patch_path:
            cmd += ["--patch", self._patch_path]
        if session_id and self.session_continuation:
            cmd += ["--session-id", str(session_id)]
        cmd.append("-")
        return cmd

    @staticmethod
    def _task_prompt(task_path):
        """任务书的 stdin 提示：绝对路径 + 执行边界。

        路径必须绝对化（同 hermes 的 2026-10-17 根因）：子进程 cwd 由调用方决定，
        相对路径任务书在“从别的目录拉起”时会找不到，什么都没写却 exit 0。
        """
        p = Path(task_path).resolve()
        return ("阅读并严格按 " + str(p) + " 中的指示执行全部步骤。"
                "完成后简要汇报：产物路径、校验结果、遇到的问题。"
                + DSH_SCOPE_RULE)

    @staticmethod
    def _infer_workdir(task_path):
        """从任务书路径反推项目根（`<root>/data/state/tasks/x.md` → `<root>`）。

        为什么必须这么做：headless 的 sandbox 工作区 = **进程 cwd**
        （patch 里 `workspaceRoot: process.cwd()`），而调用点大多不传 workdir。
        一旦 nf_api 用 `--root` 指向另一本书、cwd 却是启动目录，子进程的
        工作区就跟项目根不是同一个 —— 写产物会被 sandbox 拒绝（可见失败，
        但排查起来像“DSH 坏了”）。任务书路径是调用点已经给准的事实，
        从它推根不需要额外配置，也骗不了人。
        推不出来（临时目录里的任务书）返回 None = 用进程 cwd。
        """
        try:
            p = Path(task_path).resolve()
        except Exception:                               # noqa: BLE001
            return None
        for parent in p.parents:
            if parent.name.lower() == "data":
                return str(parent.parent)
        return None

    @staticmethod
    def _render_query(messages):
        """把 chat 消息压成一段提示词（system 在前，user 标角色）。"""
        parts = []
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "user").lower()
            content = str(m.get("content") or "")
            if role == "system":
                parts.append(content)
            else:
                parts.append("【" + ("作者" if role == "user" else role) + "】" + content)
        return ONE_SHOT_PREFIX + chr(10) + chr(10) + (chr(10) * 2).join(parts)

    # ------------------------------------------------------------ 事件流解析

    @staticmethod
    def parse_events(raw):
        """NDJSON → dict。解析永不抛（非 JSON 行直接跳过）。

        返回：
          final       : 终止事件 dict 或 None（`error` 事件时没有 final）
          session_id  : 本次运行/续接的 Session 标识
          texts       : 已提交的 text 事件片段（按序）
          usage       : {input, output, cache_read, reported} —— reported=False 表示
                        事件流里没有可靠的 usage（调用方要标 estimated）
          error       : `error` 事件的 message（轮次外失败）
          step_usage  : 每个 step_end 的原始 usage（诊断用）
        """
        out = {"final": None, "session_id": None, "texts": [], "usage": None,
               "error": None, "step_usage": []}
        t_in = t_out = t_cache = 0
        reported = False
        for line in (raw or "").splitlines():
            s = line.strip()
            if not s.startswith("{"):
                continue
            try:
                ev = json.loads(s)
            except Exception:                            # noqa: BLE001
                continue
            if not isinstance(ev, dict):
                continue
            kind = ev.get("type")
            if kind == "session":
                sid = ev.get("sessionId")
                if isinstance(sid, str) and sid.strip():
                    out["session_id"] = sid.strip()
            elif kind == "text":
                piece = ev.get("text")
                if isinstance(piece, str) and piece:
                    out["texts"].append(piece)
            elif kind == "status":
                if ev.get("phase") != "step_end":
                    continue
                u = ev.get("usage")
                if not isinstance(u, dict):
                    continue
                reported = True
                out["step_usage"].append(u)
                t_in += int(u.get("inputTokens") or 0)
                t_out += int(u.get("outputTokens") or 0)
                t_cache += int(u.get("cacheReadTokens") or 0)
            elif kind == "final":
                out["final"] = ev
                sid = ev.get("sessionId")
                if isinstance(sid, str) and sid.strip():
                    out["session_id"] = sid.strip()
            elif kind == "error":
                msg = ev.get("message")
                if isinstance(msg, str) and msg.strip():
                    out["error"] = msg.strip()
        if reported:
            out["usage"] = {"input": t_in, "output": t_out,
                            "cache_read": t_cache, "reported": True}
        return out

    @staticmethod
    def _reason_kind(final):
        reason = (final or {}).get("reason")
        if isinstance(reason, dict):
            return str(reason.get("kind") or "")
        return ""

    # ------------------------------------------------------------ 收尾收敛

    def _finish(self, task_path, rc, raw, stderr="", error=None, stopped=False):
        """把一次 headless 运行收敛为 run_task 同构 dict（永不抛异常）。

        判成功只认两条：进程退出码 0 **且** `final.reason.kind == "completed"`。
        `max-tokens` 单独映射成 `finish_reason="length"` + exit 2 —— 与直连引擎
        的截断语义对齐（正文不完整不许当成品），这条是 2026-11-17 特意补的：
        DSH 把“撞输出上限”记在 reason 里，不读它就会把半截正文当成功。
        """
        parsed = self.parse_events(raw)
        final = parsed["final"]
        kind = self._reason_kind(final)

        if stopped:
            exit_code = 0                    # 用户主动停 ≠ 失败（与 direct/hermes 同语义）
        elif rc:
            exit_code = int(rc)              # 进程非零必失败（final 可能没来得及写）
        elif kind == "max-tokens":
            exit_code = 2                    # 与直连引擎的“截断不落盘”同码
            error = error or ("输出被模型输出上限截断（final.reason.kind=max-tokens）"
                              "→ 正文不完整，按失败处理")
        elif final is None:
            exit_code = 1                    # 没有 final 却退出 0 = 事件流不完整，fail-closed
            if parsed.get("error"):
                error = error or ("DSH 报告错误：" + parsed["error"])
            else:
                error = error or ("headless 进程退出 0，但事件流里没有 final 事件"
                                  "（DSH 版本不匹配或输出被截）→ 不能当成功")
        elif kind and kind != "completed":
            exit_code = 1
            detail = ""
            if isinstance(final.get("reason"), dict):
                detail = str(final["reason"].get("error") or "")
            error = error or ("这次运行没有完成（final.reason.kind=" + kind + "）"
                              + ("：" + detail[:200] if detail else ""))
        else:
            exit_code = 0

        # stdout_tail：final.text 是无损最终答案；没有 final 就拼已提交的 text 片段；
        # 再退原始尾部（stderr 已单独给出，不混进来）。
        tail = ""
        if isinstance((final or {}).get("text"), str):
            tail = final["text"]
        elif parsed["texts"]:
            tail = "".join(parsed["texts"])
        else:
            tail = (raw or "")[-2000:]
        # 事件流里只有一条 error、没有任何正文时，把 error 原文当 tail：
        # 原始 JSON 行除了噪声什么都不多给（真机实测：空任务被拒时 tail 就是那行 JSON）。
        if final is None and not parsed["texts"] and parsed.get("error"):
            tail = parsed["error"]

        usage = parsed["usage"]
        if usage and usage.get("reported"):
            t_in, t_out, t_cache, estimated = (usage["input"], usage["output"],
                                              usage["cache_read"], False)
        else:
            # 事件流没有 usage（旧版本/异常退出）→ 按任务文件保守估算并标注 estimated，
            # 不冒充真实用量（同 hermes 的兜底口径）。
            from utils.api_client import estimate_tokens as est
            t_in, t_out = est(task_path)
            t_cache, estimated = 0, True

        out = {
            "exit_code": exit_code,
            "stdout_tail": tail,
            "tokens": int(t_in),
            "tokens_out": int(t_out),
            "cache_read": int(t_cache),
            "cost_yuan": 0.0,       # engine: dsh 走订阅执行（金額闸门无效，token 照记）
            "estimated": estimated,
            "provider": "dsh",
            "model": self.model or "dsh-default",
            "requests": 1,
            "stopped": bool(stopped),
            "session_id": parsed["session_id"],
            "finish_reason": ("length" if kind == "max-tokens"
                              else "stopped" if kind == "aborted"
                              else ""),
        }
        if error:
            out["error"] = str(error)
        elif parsed.get("error"):
            out["error"] = parsed["error"]
        if stderr and exit_code != 0:
            out["stderr_tail"] = stderr[-600:]
        if self.note_request_tokens(t_out):
            out["request_tokens_over"] = True
        return out

    # ------------------------------------------------------------ 进程管理

    @staticmethod
    def _kill_tree(proc):
        """杀整棵进程树。

        Windows 上 `dsh` 是 .cmd → cmd.exe → Electron（ELECTRON_RUN_AS_NODE）三层，
        只 kill 直接子进程会留下孤儿持有 stdout 管道：`communicate()` 等不到 EOF，
        “停止”形同虚设（hermes 2026-10-01 的同款坑）。必须 taskkill /F /T。
        """
        try:
            if proc.poll() is not None:
                return          # 已退出：按 PID 再杀有 PID 复用误杀风险
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

    def _spawn(self, cmd, workdir=None):
        """起子进程：stdout/stderr 分开读（--json 的 stdout 是纯事件流）。"""
        return subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                bufsize=1, cwd=workdir)

    @staticmethod
    def _feed_stdin(proc, text):
        """把任务写进 stdin 并关闭（headless 读到 EOF 才开始跑）。"""
        try:
            if proc.stdin is not None:
                proc.stdin.write(text)
                proc.stdin.close()
        except Exception:                               # noqa: BLE001
            pass    # 子进程提前退出（用法错误/凭据缺失）→ 由退出码与 stderr 说话

    # ------------------------------------------------------------ run_task

    def run_task(self, task_file, workdir=None, model=None, session_id=None):
        """执行任务书。`session_id` 非空**且**开了续接时用 `--session-id`。

        续接失败（非零退出）自动降级为全新会话重试一次 —— 续接是优化，
        不该成为新的失败源（与 hermes 同处置），结果带 `resumed_fallback`。
        """
        res = self._run_once(task_file, workdir, model, session_id)
        if (session_id and self.session_continuation
                and not res.get("stopped") and res.get("exit_code")):
            why = str(res.get("error") or res.get("stderr_tail") or "")[:140]
            print("[dsh_client] WARN 续接会话失败（exit=%s）：%s → 降级为全新会话重试"
                  % (res.get("exit_code"), why))
            res = self._run_once(task_file, workdir, model, None)
            res["resumed_fallback"] = True
        return res

    def _run_once(self, task_file, workdir=None, model=None, session_id=None,
                  prompt=None):
        task_path = Path(task_file)
        cmd = self._build_cmd(session_id)
        text = prompt if prompt is not None else self._task_prompt(task_path)
        wd = workdir or self._infer_workdir(task_path)
        try:
            proc = self._spawn(cmd, wd)
        except OSError as e:
            return self._finish(task_path, 127, "", error=self._spawn_error(e))
        self._feed_stdin(proc, text)
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired as e:
            # 超时不抛异常：先杀整棵树，再有限等待管道收尾，最后给结构化 124。
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
                                error="DSH headless 超时（%ss）被中止 → 结构化失败，"
                                      "交给自动重试 / 止损" % self.timeout)
        return self._finish(task_path, proc.returncode, stdout or "",
                            stderr=stderr or "")

    @staticmethod
    def _spawn_error(exc):
        b = str(exc)
        return ("无法启动 dsh（找不到启动器或不可执行）：" + b[:200]
                + "。修法：装 DeepSeek Harness，或在 config/system.yaml 的 dsh.bin "
                  "写启动器绝对路径（也认环境变量 NF_DSH_BIN）。"
                  "若只想继续跑书，把 engine 改回 hermes（订阅）或 direct（按量）。")

    # ------------------------------------------------------------ 单轮直答

    def _post_chat(self, messages, temperature=None, max_tokens=None):
        """单轮直调：只回一段文本、不落盘（走向 / 方向样例 / 成败讨论都走它）。

        契约与两个老引擎一致：返回 4 元组 `(text, usage, model, finish_reason)`，
        usage 键名 `prompt_tokens` / `completion_tokens`，`finish_reason=="length"`
        表示输出不完整、调用方必须判失败。

        ⚠️ 边界：headless 没有“回合上限”旋钮，agent 仍可能调用工具 ——
        靠 ONE_SHOT_PREFIX 的提示词约束（"不要调用任何工具"），不是机制约束。
        """
        import tempfile as _tempfile

        query = self._render_query(messages)
        tmp = Path(_tempfile.mkdtemp(prefix="nf_dsh_oneshot_")) / "q.md"
        try:
            write_text(tmp, query)
            res = self._run_once(tmp, None, None, None, prompt=query)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
            try:
                tmp.parent.rmdir()
            except OSError:
                pass

        if int(res.get("exit_code") or 0) not in (0, 2):
            detail = (res.get("error") or res.get("stderr_tail")
                      or res.get("stdout_tail") or "")
            raise RuntimeError("[dsh_client] DSH 单轮调用失败（exit=%s）：%s"
                               % (res.get("exit_code"), str(detail)[:300]))
        return self._chat_result(res)

    @staticmethod
    def _chat_result(res):
        """run_task 同构 dict → 单轮直答 4 元组（两个 _post_chat* 共用一份口径）。"""
        text = res.get("stdout_tail") or ""
        if not text.strip():
            raise RuntimeError(
                "[dsh_client] DSH 单轮调用没有返回文本（agent 可能把答案写进了文件"
                "而没有回复）。检查 profile 的工具配置，或改用 run_task。")
        usage = {"prompt_tokens": int(res.get("tokens") or 0),
                 "completion_tokens": int(res.get("tokens_out") or 0)}
        fr = str(res.get("finish_reason") or "")
        if res.get("stopped"):
            finish = "stopped"
        elif fr == "length":
            finish = "length"
        else:
            finish = "stop"
        return text, usage, res.get("model") or "dsh-default", finish

    def _post_chat_stream(self, messages, temperature=None, max_tokens=None,
                          on_chunk=None, stop_flag=None):
        """单轮直答的流式版：stop_flag 置位即杀整棵进程树。

        `finish == "stopped"` 表示被停下 —— 已收到的文本照样返回，
        落不落盘由调用方决定（讨论轮不落盘）。
        """
        import tempfile as _tempfile

        query = self._render_query(messages)
        tmp = Path(_tempfile.mkdtemp(prefix="nf_dsh_oneshot_stream_")) / "q.md"
        try:
            write_text(tmp, query)
            res = self._stream_once(tmp, query, on_chunk, stop_flag, prompt=query)
            if not res.get("stopped") and int(res.get("exit_code") or 0) not in (0, 2):
                detail = (res.get("error") or res.get("stderr_tail")
                          or res.get("stdout_tail") or "")
                raise RuntimeError("[dsh_client] DSH 单轮流式调用失败（exit=%s）：%s"
                                   % (res.get("exit_code"), str(detail)[:300]))
            return self._chat_result(res)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
            try:
                tmp.parent.rmdir()
            except OSError:
                pass

    # ------------------------------------------------------------ 流式执行

    def _stream_once(self, task_path, task_text, on_piece, stop_flag, workdir=None,
                     session_id=None, prompt=None):
        """流式跑一次（run_task_stream 与 _post_chat_stream 共用）。"""
        import queue as _queue

        cmd = self._build_cmd(session_id)
        text = prompt if prompt is not None else task_text
        wd = workdir or self._infer_workdir(task_path)
        try:
            proc = self._spawn(cmd, wd)
        except OSError as e:
            return self._finish(task_path, 127, "", error=self._spawn_error(e))
        self._feed_stdin(proc, text)

        line_q = _queue.Queue()

        def _reader(stream, q):
            try:
                for line in stream:
                    q.put(line)
            except Exception:                           # noqa: BLE001
                pass
            q.put(None)                                 # EOF 哨兵

        threading.Thread(target=_reader, args=(proc.stdout, line_q),
                         daemon=True).start()
        # stderr 单独收（--json 下它是纯诊断），避免管道写满把子进程卡死
        err_parts = []

        def _err_reader():
            try:
                for line in proc.stderr:
                    err_parts.append(line)
                    if len(err_parts) > 500:
                        del err_parts[:200]
            except Exception:                           # noqa: BLE001
                pass

        threading.Thread(target=_err_reader, daemon=True).start()

        raw_parts = []
        stopped = timed_out = False
        deadline = time.time() + self.timeout
        while True:
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
                break
            raw_parts.append(line)
            s = line.lstrip()
            if not s.startswith("{"):
                continue
            try:
                ev = json.loads(s)
            except Exception:                           # noqa: BLE001
                continue
            if isinstance(ev, dict) and ev.get("type") == "text":
                piece = ev.get("text") or ""
                if piece and on_piece:
                    try:
                        on_piece(piece)
                    except Exception:                   # noqa: BLE001
                        pass
            else:
                # 非 text 事件（tool_call / status …）也算活动心跳：
                # 写长章时可能几分钟没有一个 text 事件，只按 text 计心跳会误报“卡死”。
                if on_piece:
                    try:
                        on_piece("")
                    except Exception:                   # noqa: BLE001
                        pass

        if stopped or timed_out:
            self._kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except Exception:                           # noqa: BLE001
                pass
            rc = 124 if timed_out else 0
        else:
            try:
                rc = proc.wait(timeout=30)
            except Exception:                           # noqa: BLE001
                self._kill_tree(proc)
                rc = 124
                timed_out = True

        raw = "".join(raw_parts)
        stderr = "".join(err_parts)
        if timed_out:
            return self._finish(Path(task_path), 124, raw, stderr=stderr,
                                error="DSH headless 流式超时（%ss）被中止" % self.timeout)
        if stopped:
            return self._finish(Path(task_path), 0, raw, stopped=True)
        return self._finish(Path(task_path), rc, raw, stderr=stderr)

    # ------------------------------------------------------------ 其它契约

    def write_task(self, task_dir, name, content):
        task_dir = Path(task_dir)
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / name
        write_text(path, content)
        return path

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None, model=None,
                        session_id=None):
        """真流式：逐 text 事件回放；stop_flag 置位即杀整棵进程树。"""
        task_path = Path(task_file)
        res = self._stream_once(task_path, self._task_prompt(task_path), on_piece,
                                stop_flag, workdir=workdir, session_id=session_id)
        # 续接失败自动降级一次（与 run_task 同处置）
        if (session_id and self.session_continuation
                and not res.get("stopped") and res.get("exit_code")):
            why = str(res.get("error") or res.get("stderr_tail") or "")[:140]
            print("[dsh_client] WARN 流式续接会话失败（exit=%s）：%s → 降级为全新会话重试"
                  % (res.get("exit_code"), why))
            res = self._stream_once(task_path, self._task_prompt(task_path), on_piece,
                                    stop_flag, workdir=workdir, session_id=None)
            res["resumed_fallback"] = True
        return res
