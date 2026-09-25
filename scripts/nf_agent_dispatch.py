# -*- coding: utf-8 -*-
"""多 Agent 派发（Phase 4）：绒花墨坊把任务派发给 Hermes 子会话执行。

与方寸 dispatch 同构（参考 fangcun desktop/src/main/ipc.ts 的 buildDispatchPrompt）：
生成自包含任务书 → 拉起 ``hermes chat --query-file <任务书> --oneshot`` 后台子进程
→ 状态落 ``data/state/agent_runs.jsonl``（append-only，同一 run_id 最新一行生效）。

调用链::

    MCP 工具 nf_dispatch_task / nf_get_agent_run (nf_mcp.py)
      │  直接函数调用（_local 标记，不经 HTTP loopback：派发不依赖 nf_api
      │  进程内状态 JOBS/CURRENT，Phase 4 约束不改 nf_api.py）
      ▼
    dispatch_to_hermes() / get_agent_run()    ← 本模块
      ├─ build_task_brief()   任务书 → data/state/agent_briefs/<run_id>.md
      ├─ Popen(...)           子会话日志 → data/state/agent_briefs/<run_id>.log
      ├─ _append_run()        运行记录 → data/state/agent_runs.jsonl
      └─ _watch() 线程        等子进程退出 → 追加终态（done / error，超时杀进程）

状态机：``running → done | error``。**dispatch 失败也只写 error 状态，绝不抛
异常、不阻塞调用方流程** —— 派发是旁路能力，坏了不该连累主干。

产物约定（任务书里同样写明，子会话照此回报）：
  - 结果摘要：``data/state/agent_briefs/<run_id>_result.md``（≤500 字）

Hermes 命令解析：参数 ``hermes_cmd`` > 环境变量 ``NF_HERMES_CMD`` > 默认 ``hermes``。
值可以是可执行名（走 PATH），也可以是带参数的命令行（空格分隔；Windows 路径请用
正斜杠，含空格的路径用双引号包住）。测试/演练可用它指向一个 stub 脚本，零真实调用。

用法（CLI）：
  python scripts/nf_agent_dispatch.py --list [--limit 20]
  python scripts/nf_agent_dispatch.py --get 20260924_120000_ab12cd
  python scripts/nf_agent_dispatch.py --dispatch "任务标题" --context "任务正文"
"""
import argparse
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

# ---- 常量（路径一律经 _root() 解析，禁止相对路径 IO） ----
RUNS_REL = "data/state/agent_runs.jsonl"   # 运行记录（append-only，最新行生效）
BRIEFS_REL = "data/state/agent_briefs"     # 任务书 / 日志 / 结果摘要
DEFAULT_TIMEOUT_S = 3600                   # 子会话默认超时（防失控烧钱）

_CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

_LOCK = threading.Lock()   # agent_runs.jsonl 追加互斥（MCP 多客户端并发派发）
_ACTIVE = {}               # run_id -> Popen（本进程内在跑的子会话；stale 判定用）


def _root():
    """项目根：NF_ROOT 环境变量（nf_api --root 会设置）→ 源码树。

    每次调用现读 —— 与域模块「禁止值拷贝 ROOT」同一条纪律，
    否则 --root / 测试临时根会指向错目录。
    """
    env = os.environ.get("NF_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[1]


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_run_id():
    # 与 nf_api 的 job_id 同风格：时间戳前缀 + 短随机后缀（同秒不撞）
    return time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]


# ---------------------------------------------------------------------------
# 任务书
# ---------------------------------------------------------------------------

def build_task_brief(title, context, run_id=None):
    """生成自包含任务书并落盘。返回 ``(run_id, brief_path: Path)``。

    任务书是子会话的**唯一输入**（--query-file），必须自包含全部上下文 ——
    子会话拿不到派发时的任何其它对话内容。
    """
    run_id = run_id or _new_run_id()
    root = _root()
    brief_dir = root / BRIEFS_REL
    brief_dir.mkdir(parents=True, exist_ok=True)
    result_rel = BRIEFS_REL + "/" + run_id + "_result.md"
    body = [
        "# 绒花墨坊 Sub-Agent 任务书",
        "",
        "- run_id: " + run_id,
        "- 标题: " + title,
        "- 派发时间: " + _now(),
        "- 项目根: " + str(root),
        "",
        "## 任务上下文",
        "",
        str(context or "").strip(),
        "",
        "## 执行约定",
        "",
        "1. 在项目根 " + str(root) + " 下工作；动手前先读 AGENTS.md 的硬性约束。",
        "2. 素材与产物默认只读；需要写草稿时只写沙盒或任务指定的输出路径，不覆盖正典。",
        "3. 审批权属于用户：不得代用户执行审批（approve/reject）类操作。",
        "4. 完成后把结果摘要（≤500 字）写入 " + result_rel + "（本任务书的回报契约）。",
        "",
    ]
    brief_path = brief_dir / (run_id + ".md")
    brief_path.write_text("\n".join(body), encoding="utf-8")
    return run_id, brief_path


# ---------------------------------------------------------------------------
# 运行记录（data/state/agent_runs.jsonl）
# ---------------------------------------------------------------------------

def _append_run(rec):
    """追加一条运行记录（append-only；同一 run_id 最新一行生效）。"""
    path = _root() / RUNS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _read_runs():
    """读全部运行记录（坏行跳过：一条坏记录不该让整个查询失效）。"""
    path = _root() / RUNS_REL
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _latest_run(run_id):
    latest = None          # 未匹配必须返回 None（查询不存在 run 是正常路径）
    for rec in _read_runs():
        if rec.get("run_id") == run_id:
            latest = rec   # 顺序扫描，后行覆盖前行
    return latest


def get_agent_run(run_id):
    """查询单个派发任务（返回最新记录）。找不到返回 ``ok=False``，不抛异常。"""
    if not str(run_id or "").strip():
        return {"ok": False, "error": "run_id 必填"}
    rec = _latest_run(run_id)
    if rec is None:
        return {"ok": False, "error": "run 不存在: " + str(run_id)}
    rec = dict(rec)
    rec["ok"] = True
    # running 但本进程没持有对应子进程 → 服务重启过，状态只是「最后已知值」
    if rec.get("status") == "running" and run_id not in _ACTIVE:
        rec["stale"] = True
        rec["stale_note"] = "本进程未持有该子会话（服务重启过？状态为最后已知值）"
    # 结果摘要顺手带回（存在才带），调用方不必再读文件
    rp = rec.get("result_path")
    if rp:
        p = _root() / rp
        if p.is_file():
            try:
                rec["result_summary"] = p.read_text(encoding="utf-8")[:600].strip()
            except OSError:
                pass
    return rec


def list_agent_runs(limit=50):
    """列出派发任务（每个 run_id 取最新记录，按创建时间倒序）。"""
    latest, order = {}, []
    for rec in _read_runs():
        rid = rec.get("run_id") or ""
        if rid not in latest:
            order.append(rid)
        latest[rid] = rec
    runs = [latest[r] for r in order if r]
    runs.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    try:
        n = max(1, int(limit or 50))
    except (TypeError, ValueError):
        n = 50
    return runs[:n]


# ---------------------------------------------------------------------------
# 派发
# ---------------------------------------------------------------------------

def _resolve_hermes_cmd(hermes_cmd=None):
    """解析 hermes 启动命令 → argv 前缀。

    优先级：参数 > ``NF_HERMES_CMD`` 环境变量 > ``"hermes"``。
    值可以是可执行名（走 PATH），也可以是带参数的命令行（空格分隔）。
    Windows 下反斜杠路径直接支持（整串路径或首个 token 均可；
    shlex POSIX 模式会吃反斜杠 —— ``C:\\Users\\x`` → ``C:Usersx``）。
    含空格的路径用双引号包住。
    找不到可执行文件时抛 FileNotFoundError —— 由 dispatch_to_hermes 兜成
    error 状态（不阻塞流程）。
    """
    raw = str(hermes_cmd or os.environ.get("NF_HERMES_CMD") or "hermes").strip()
    if not raw:
        raise ValueError("hermes 命令为空（NF_HERMES_CMD）")
    if os.name == "nt":
        # 整串就是一个可执行文件路径 → 直接用（不经 shlex，防反斜杠被吃）
        direct = shutil.which(raw.strip('"'))
        if direct:
            return [direct]
        # 带参数的命令行：posix=False 保留反斜杠，再剥每段引号
        parts = [p.strip('"') for p in shlex.split(raw, posix=False)]
    else:
        parts = shlex.split(raw)
    if not parts:
        raise ValueError("hermes 命令为空（NF_HERMES_CMD）")
    exe = shutil.which(parts[0])
    if not exe:
        raise FileNotFoundError(
            "找不到可执行文件: " + parts[0]
            + "（PATH 里没有 hermes？可用环境变量 NF_HERMES_CMD 指定完整命令）")
    return [exe] + parts[1:]


def dispatch_to_hermes(title, context, workdir=None, model=None,
                       max_turns=None, timeout_s=None, hermes_cmd=None,
                       source="mcp"):
    """派发任务给 Hermes 子会话。返回 run 记录 dict（**绝不抛异常**）。

    - 成功拉起：记录 ``status=running`` 并**立即返回**（异步）；watcher 线程
      在子进程退出后追加终态（``done`` / ``error``）。
    - 拉起失败（hermes 不存在 / 参数非法）：记录 ``status=error`` 并返回
      ``ok=False`` —— 派发失败不阻塞调用方流程。
    - 子会话命令：``hermes chat --query-file <任务书> --oneshot``（one-shot
      模式，参考 hermes-agent skill；--query-file 保证任务书原文不经 shell 解释）。
    """
    title = str(title or "").strip()
    context = str(context or "").strip()
    if not title:
        return {"ok": False, "error": "title 必填（任务标题）"}
    if not context:
        return {"ok": False, "error": "context 必填（任务书必须自包含上下文）"}

    root = _root()
    run_id, brief_path = build_task_brief(title, context)
    log_path = brief_path.parent / (run_id + ".log")
    rec = {
        "run_id": run_id,
        "title": title,
        "status": "running",
        "source": str(source or "mcp"),
        "brief_path": BRIEFS_REL + "/" + brief_path.name,
        "log_path": BRIEFS_REL + "/" + log_path.name,
        "result_path": BRIEFS_REL + "/" + run_id + "_result.md",
        "created_at": _now(),
        "pid": None,
        "command": None,
    }

    log_fh = open(log_path, "w", encoding="utf-8", errors="replace")
    try:
        prefix = _resolve_hermes_cmd(hermes_cmd)
        argv = prefix + ["chat", "--query-file", str(brief_path), "--oneshot"]
        if model:
            argv += ["-m", str(model)]
        if max_turns:
            argv += ["--max-turns", str(int(max_turns))]
        if timeout_s is not None:
            timeout_s = int(timeout_s)
        proc = subprocess.Popen(
            argv, stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=str(workdir or root), creationflags=_CREATE_FLAGS)
    except Exception as e:                                  # noqa: BLE001
        log_fh.close()
        rec.update({"status": "error",
                    "error": type(e).__name__ + ": " + str(e)[:300],
                    "finished_at": _now()})
        _append_run(rec)
        rec["ok"] = False
        return rec

    rec["pid"] = proc.pid
    rec["command"] = argv
    _append_run(rec)
    _ACTIVE[run_id] = proc
    threading.Thread(target=_watch, args=(run_id, proc, timeout_s, log_fh),
                     daemon=True).start()
    rec["ok"] = True
    return rec


def _watch(run_id, proc, timeout_s, log_fh):
    """等子会话退出 → 追加终态。超时杀进程记 error。"""
    timeout_s = int(timeout_s or DEFAULT_TIMEOUT_S)
    try:
        try:
            proc.wait(timeout=timeout_s)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            _finish_run(run_id, "error", proc.returncode,
                        "超时（>" + str(timeout_s) + "s）已终止子会话")
            return
        if exit_code == 0:
            _finish_run(run_id, "done", 0, None)
        else:
            _finish_run(run_id, "error", exit_code,
                        "子会话退出码 " + str(exit_code)
                        + "（日志: " + BRIEFS_REL + "/" + run_id + ".log）")
    finally:
        try:
            log_fh.close()
        except OSError:
            pass
        _ACTIVE.pop(run_id, None)


def _finish_run(run_id, status, exit_code, error):
    """把最新记录合并终态后追加（同一 run 的写入由因果顺序串行，无合并竞态）。"""
    rec = _latest_run(run_id) or {"run_id": run_id}
    rec.update({"status": status, "exit_code": exit_code, "error": error,
                "finished_at": _now(), "updated_at": _now()})
    _append_run(rec)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="多 Agent 派发（Phase 4）：把任务派发给 Hermes 子会话")
    ap.add_argument("--list", action="store_true", help="列出派发任务（最新状态）")
    ap.add_argument("--limit", type=int, default=20, help="--list 条数（默认 20）")
    ap.add_argument("--get", metavar="RUN_ID", help="查询单个派发任务")
    ap.add_argument("--dispatch", metavar="TITLE",
                    help="派发新任务（需 --context；异步，返回 run_id）")
    ap.add_argument("--context", help="任务上下文正文（--dispatch 必填）")
    ap.add_argument("--timeout", type=int, default=None,
                    help="子会话超时秒数（默认 3600）")
    args = ap.parse_args()

    if args.get:
        print(json.dumps(get_agent_run(args.get), ensure_ascii=False, indent=2))
        return 0
    if args.dispatch:
        if not (args.context or "").strip():
            print("错误：--dispatch 需要 --context（任务书必须自包含上下文）")
            return 1
        rec = dispatch_to_hermes(args.dispatch, args.context,
                                 timeout_s=args.timeout, source="cli")
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0 if rec.get("ok") else 1
    # 默认 --list
    runs = list_agent_runs(args.limit)
    if not runs:
        print("（暂无派发记录，data/state/agent_runs.jsonl）")
        return 0
    print("%-26s %-8s %-19s %s" % ("run_id", "status", "created_at", "title"))
    for r in runs:
        print("%-26s %-8s %-19s %s" % (
            r.get("run_id", ""), r.get("status", ""),
            r.get("created_at", ""), str(r.get("title", ""))[:40]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
