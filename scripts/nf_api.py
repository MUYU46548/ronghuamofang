# -*- coding: utf-8 -*-
"""NovelForge 本地 API 服务（GUI 化 P0 + 实时流式输出）。

零依赖（stdlib http.server），与 orchestrator/审批/打回/精修函数级复用，
不经过 Hermes 子进程。端点总览：

GET  /health              存活 + 当前 job
GET  /state               progress + gates + 成本汇总 + 最近 job
GET  /models              config/system.yaml 的 engine/providers/model 回显
GET  /prompts/list        提示词模板列表（prompts/stage[1-7]_*.md）
GET  /prompts/get         读取模板正文（?name=xxx 或 /prompts/get/xxx）
POST /prompts/save        {name, content} → 备份旧版到 prompts/history/ 后写入
POST /stage/{n}/run       {from_stage?, only_stage?, stream?} → job_id（409=已有任务在跑）
GET  /stream/{job_id}     SSE：实时流式输出（token 级）
POST /stop/{job_id}       中断正在运行的任务
GET  /jobs/{id}           job 状态/结果
POST /approve             {stage, revoke?} → 审批/撤销
POST /reject              {stage, reason, dry_run?} → 打回（默认真执行）
POST /refine/outline      {feedback, dry_run?} → 大纲精修
POST /refine/chapter      {chapter, feedback, dry_run?} → 章节精修
POST /snapshot            {label?} → 手动快照
POST /costs               GET 成本流水
GET  /costs/summary       按阶段/模型聚合
POST /models/switch       {role, model} → 切换模型

安全边界（本地自用）：默认 127.0.0.1；写操作只允许 POST 且 Content-Type=application/json；
除 /stage /refine /snapshot 外均即时执行。NF_API_ALLOW_FAKE=1 时 stage/refine 走
FakeClient（无 LLM 全链验收），生产模式禁用。

用法：
  .venv/Scripts/python.exe scripts/nf_api.py            # 端口 8765
  .venv/Scripts/python.exe scripts/nf_api.py --port 8901 --allow-fake
"""
import argparse
import json
import os
import queue
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from orchestrator import run as orch_run  # noqa: E402
from utils.progress_manager import ProgressManager  # noqa: E402
from utils.db import RunDB  # noqa: E402
from utils.llm_client import _load_env_file  # noqa: E402
import reject as reject_mod  # noqa: E402
import snapshot as snap_mod  # noqa: E402
import switch_book as sb_mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

JOB_TTL = 50           # 内存保留的最近 job 数
LOCK = threading.Lock()
JOBS = {}              # job_id -> dict
JOBS_ORDER = []
CURRENT = {"id": None}
ALLOW_FAKE = os.environ.get("NF_API_ALLOW_FAKE") == "1"

# ---- 流式输出支持 ----
# job_id -> {"queue": Queue, "stop": Event, "text": []}
STREAMERS = {}
STREAMERS_LOCK = threading.Lock()


def _client_for_env(cfg, role):
    """真实模式 → make_client；测试模式（--allow-fake/NF_API_ALLOW_FAKE）→ FakeClient。"""
    if ALLOW_FAKE:
        from utils.fake_client import FakeClient
        return FakeClient()
    from utils.llm_client import make_client, _load_env_file  # noqa: E402
    return make_client(cfg, role)


def _wrap_client_for_streaming(client, job_id):
    """包装客户端：将 run_task 重定向到 run_task_stream，通过队列推送 token。
    Hermes 引擎不支持真流式，降级为整块返回。"""
    orig_run_task = client.run_task
    orig_run_task_stream = getattr(client, "run_task_stream", None)

    streamer = STREAMERS.get(job_id)
    if not streamer:
        return client

    q = streamer["queue"]
    stop = streamer["stop"]

    def wrapped_run_task(*args, **kwargs):
        def on_piece(piece):
            try:
                q.put_nowait({"type": "token", "text": piece})
            except queue.Full:
                pass

        if orig_run_task_stream:
            result = orig_run_task_stream(
                *args, **kwargs,
                on_piece=on_piece,
                stop_flag=stop.is_set
            )
        else:
            # Hermes 降级
            result = orig_run_task(*args, **kwargs)
            if result.get("exit_code") == 0 and result.get("stdout_tail"):
                for ch in result["stdout_tail"]:
                    try:
                        q.put_nowait({"type": "token", "text": ch})
                    except queue.Full:
                        pass

        status = "stopped" if (stop.is_set() and result.get("stopped")) else ("ok" if result.get("exit_code") == 0 else "error")
        try:
            q.put_nowait({
                "type": "done",
                "exit_code": result.get("exit_code", -1),
                "status": status,
                "model": result.get("model", ""),
                "cost_yuan": result.get("cost_yuan", 0),
            })
        except queue.Full:
            pass
        return result

    client.run_task = wrapped_run_task
    return client


def _snapshot_state():
    try:
        return snap_mod.snapshot  # 引用即可（真正快照在 job 内执行）
    except Exception:
        return None


# ---- 提示词模板（prompts/）读写 ----
# 白名单：仅允许 prompts/ 下的一级文件 stage[1-7]_*.md
PROMPTS_DIR = (ROOT / "prompts").resolve()
PROMPTS_HISTORY_DIR = PROMPTS_DIR / "history"
PROMPT_NAME_RE = re.compile(r'^stage[1-7]_.*\.md$')
PROMPT_MAX_BYTES = 1024 * 1024          # 单文件 1MB 上限
PROMPT_HISTORY_KEEP = 50                # 每个模板保留的备份份数
STAGE_LABELS = {1: "素材归并", 2: "整体大纲", 3: "逐章大纲", 4: "逐章写作",
                5: "逻辑检查", 6: "润色", 7: "Word 成品"}


def prompt_name_ok(name):
    """校验模板文件名是否在白名单内（禁止路径遍历与子目录）。

    返回规范化后的文件名，不合法返回 None。
    """
    if not name or not isinstance(name, str):
        return None
    n = unquote(name.strip()).replace("\\", "/")
    if "/" in n or ".." in n or n in (".", ""):
        return None
    if not PROMPT_NAME_RE.match(n):
        return None
    return n


def prompt_path(name):
    """解析为 prompts/ 下的绝对路径；越界返回 None。"""
    n = prompt_name_ok(name)
    if not n:
        return None
    p = (PROMPTS_DIR / n).resolve()
    if p.parent != PROMPTS_DIR or not p.is_file():
        return None
    return p


def prompt_stage(name):
    m = re.match(r'^stage([1-7])_', name or "")
    return int(m.group(1)) if m else 0


def list_prompts():
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(PROMPTS_DIR.glob("*.md")):
        if not PROMPT_NAME_RE.match(p.name):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        items.append({
            "name": p.name,
            "stage": prompt_stage(p.name),
            "stage_name": STAGE_LABELS.get(prompt_stage(p.name), ""),
            "size": st.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            "backups": len(list_prompt_backups(p.name)),
        })
    return items


def list_prompt_backups(name):
    """该模板的历史备份（旧→新）。"""
    n = prompt_name_ok(name)
    if not n or not PROMPTS_HISTORY_DIR.is_dir():
        return []
    stem = n[:-3]
    return sorted(PROMPTS_HISTORY_DIR.glob(stem + "_*.md"))


def trim_prompt_backups(name):
    """只保留最近 PROMPT_HISTORY_KEEP 份备份。"""
    olds = list_prompt_backups(name)
    for p in olds[:-PROMPT_HISTORY_KEEP]:
        try:
            p.unlink()
        except OSError:
            pass


def save_prompt(name, content):
    """写入模板：先备份旧版到 prompts/history/。返回 (ok, 信息)。"""
    n = prompt_name_ok(name)
    if not n:
        return False, "文件名不在白名单（须匹配 prompts/stage[1-7]_*.md）"
    if content is None or not isinstance(content, str):
        return False, "content 必须为字符串"
    data = content.encode("utf-8")
    if len(data) > PROMPT_MAX_BYTES:
        return False, "内容超过 1MB 上限"

    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    target = (PROMPTS_DIR / n).resolve()
    if target.parent != PROMPTS_DIR:
        return False, "路径越界"

    backup = ""
    eol = "\n"
    if target.is_file():
        raw = target.read_bytes()
        eol = "\r\n" if b"\r\n" in raw else "\n"
        PROMPTS_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        bak = PROMPTS_HISTORY_DIR / (n[:-3] + "_" + stamp + ".md")
        suffix = 1
        while bak.exists():
            bak = PROMPTS_HISTORY_DIR / (n[:-3] + "_" + stamp + "_" + str(suffix) + ".md")
            suffix += 1
        try:
            bak.write_bytes(target.read_bytes())
            backup = str(bak.relative_to(ROOT))
            trim_prompt_backups(n)
        except OSError as e:
            return False, "备份失败: " + str(e)
    # 统一换行后按原文件风格回填（LF/CRLF），避免保存一次就整文件 diff
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    if eol == "\r\n":
        text = text.replace("\n", "\r\n")
    try:
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    except OSError as e:
        return False, "写入失败: " + str(e)
    return True, {"name": n, "size": len(text.encode("utf-8")), "backup": backup}


def load_all():
    _load_env_file()
    cfg = yaml.safe_load((ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((ROOT / "config" / "project.yaml").read_text(encoding="utf-8"))
    return cfg, proj


def stage_status(progress, n):
    st = progress.data.get("stages", {}).get(str(n), {})
    out = {
        "stage": n,
        "status": st.get("status", "pending"),
        "approved": st.get("approved"),
        "rejected": st.get("rejected"),
        "rejected_at": st.get("rejected_at"),
    }
    if "needs_rewrite" in st:
        out["needs_rewrite"] = st["needs_rewrite"]
    return out


def build_state():
    progress = ProgressManager("data/state/progress.json")
    stages = [stage_status(progress, n) for n in range(1, 8)]
    gates = load_all()[0].get("gates", {})
    cost_spent, calls, est = 0.0, 0, 0
    db_path = Path("logs/runs.db")
    if db_path.exists():
        db = RunDB(db_path)
        try:
            row = db.conn.execute(
                "SELECT COALESCE(SUM(cost_yuan),0), COUNT(*), COALESCE(SUM(estimated),0)"
                " FROM cost_log").fetchone()
            cost_spent, calls, est = float(row[0]), int(row[1]), int(row[2])
        finally:
            db.close()
    budget = load_all()[0].get("budget", {})
    latest = JOBS[JOBS_ORDER[-1]] if JOBS_ORDER else None
    return {
        "book": load_all()[1].get("book", {}).get("name", ""),
        "project_dir": str(ROOT),
        "allow_fake": ALLOW_FAKE,
        "stages": stages,
        "gates": gates,
        "cost": {"spent_yuan": round(cost_spent, 4),
                 "calls": calls, "estimated_entries": est,
                 "limit_yuan": budget.get("limit_yuan", 300)},
        "latest_job": {k: latest[k] for k in ("id", "kind", "state", "result") if latest and k in latest},
        "current_job": CURRENT["id"],
    }


def start_job(kind, fn):
    """创建并启动一个后台 job。返回 (job_id, error)。"""
    with LOCK:
        if CURRENT["id"]:
            cur = JOBS.get(CURRENT["id"], {})
            if cur.get("state") == "running":
                return None, "已有任务在跑: " + CURRENT["id"] + "（" + cur.get("kind", "") + "）"
        job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        job = {"id": job_id, "kind": kind, "state": "running",
               "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "result": None}
        JOBS[job_id] = job
        JOBS_ORDER.append(job_id)
        while len(JOBS_ORDER) > JOB_TTL:
            JOBS.pop(JOBS_ORDER.pop(0), None)
        CURRENT["id"] = job_id

    def _worker():
        try:
            result = fn()
            ok, detail = result
            with LOCK:
                JOBS[job_id]["state"] = "ok" if ok else "failed"
                JOBS[job_id]["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                JOBS[job_id]["result"] = detail
        except SystemExit as e:
            with LOCK:
                JOBS[job_id]["state"] = "failed"
                JOBS[job_id]["result"] = str(e)
        except Exception as e:
            with LOCK:
                JOBS[job_id]["state"] = "failed"
                JOBS[job_id]["result"] = type(e).__name__ + ": " + str(e)[:300]
        finally:
            with LOCK:
                if CURRENT["id"] == job_id:
                    CURRENT["id"] = None

    threading.Thread(target=_worker, daemon=True).start()
    return job_id, None


# ---- 动作实现（与 CLI 同源） ----

def act_run_stage(cfg, only_stage, from_stage, stream_job_id=None):
    """创建阶段运行动作。stream_job_id 不为 None 时启用流式输出。"""
    client = _client_for_env(cfg, "default")

    if stream_job_id:
        client = _wrap_client_for_streaming(client, stream_job_id)

    def _fn():
        rc = orch_run(from_stage=from_stage, only_stage=only_stage, client=client)
        return (rc == 0 or rc == 3), "exit=" + str(rc) + ("（3=等待审批，属正常门暂停）" if rc == 3 else "")
    return _fn


def act_approve(stage, revoke):
    progress = ProgressManager("data/state/progress.json")
    progress.set_approved(stage, not revoke)
    status = progress.stage_status(stage)
    action = "已撤销" if revoke else "已确认"
    warn = None
    if not revoke and status != "done":
        warn = "阶段" + str(stage) + " 尚未完成（" + status + "），审批在完成后才生效"
    return (warn is None), (warn or ("阶段" + str(stage) + " " + action + "（" + status + "）"))


def act_reject(stage, reason, dry_run):
    progress = ProgressManager("data/state/progress.json")
    ok, msgs = reject_mod.reject_stage(progress, stage, reason or "", dry_run=dry_run)
    return ok, "\n".join(msgs)


def act_refine_outline(feedback, dry_run):
    import refine_outline
    cfg, proj = load_all()
    ok, msg = refine_outline.run_refine(cfg, proj, feedback or "",
                                        client=_client_for_env(cfg, "default"),
                                        dry_run=dry_run)
    return ok, msg


def act_refine_chapter(chapter, feedback, dry_run):
    import refine_chapter
    cfg, proj = load_all()
    ok, msg = refine_chapter.run_refine(cfg, proj, chapter, feedback or "",
                                        client=_client_for_env(cfg, "default"),
                                        dry_run=dry_run)
    return ok, msg


def act_snapshot(label):
    def _fn():
        try:
            snap_mod.snapshot(label or "manual")
            return True, "快照完成: " + (label or "manual")
        except Exception as e:
            return False, type(e).__name__ + ": " + str(e)[:200]
    return _fn


# ---- 审稿→修稿闭环（P0 新增） ----

def act_review_run(stream_job_id=None):
    """执行章节审查。"""
    import chapter_review
    cfg, proj = load_all()
    client = _client_for_env(cfg, "reviewer")

    def _fn():
        ok, msg = chapter_review.run_review(
            scope=None,
            report_path="data/outline/review_report.json",
            dry_run=False,
            client=client,
        )
        return ok, msg
    return _fn


def act_batch_refine_run(decisions_from_file=True):
    """执行批量精修。"""
    import batch_refine

    def _fn():
        ok, msg = batch_refine.run_batch_refine(
            report_path="data/outline/review_report.json",
            decisions_mode="file" if decisions_from_file else "interactive",
            auto_accept=False,
            dry_run=False,
        )
        return ok, msg
    return _fn


class Handler(BaseHTTPRequestHandler):
    server_version = "NovelForgeAPI/0.2"

    # ---- 基础设施 ----
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 静默默认日志
        pass

    def _query(self):
        """解析 URL 查询串（GET /prompts/get?name=xxx）。"""
        if "?" in self.path:
            return parse_qs(self.path.split("?", 1)[1])
        return {}

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ---- SSE 流式输出 ----
    def _stream_sse(self, job_id):
        """SSE 端点：流式推送 token。"""
        with STREAMERS_LOCK:
            streamer = STREAMERS.get(job_id)

        if not streamer:
            self._send(404, {"error": "stream not found: " + job_id})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = streamer["queue"]
        try:
            while True:
                try:
                    event = q.get(timeout=25)
                    data = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(b"data: " + data.encode("utf-8") + b"\n\n")
                    self.wfile.flush()
                    if event.get("type") == "done":
                        break
                except queue.Empty:
                    # 保活注释
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            # 清理 streamer
            with STREAMERS_LOCK:
                STREAMERS.pop(job_id, None)

    # ---- GET ----
    def do_GET(self):
        path_parts = self.path.split("?")
        p = path_parts[0].rstrip("/") or "/"

        # SSE 流式端点
        if p.startswith("/stream/"):
            job_id = p[len("/stream/"):]
            self._stream_sse(job_id)
            return

        if p == "/health":
            cur = CURRENT["id"]
            job = JOBS.get(cur) if cur else None
            self._send(200, {"ok": True, "current_job": cur,
                             "current_kind": (job or {}).get("kind"),
                             "allow_fake": ALLOW_FAKE})
        elif p == "/state":
            try:
                self._send(200, build_state())
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/costs":
            rows = []
            db_path = Path("logs/runs.db")
            if db_path.exists():
                db = RunDB(db_path)
                try:
                    cols = ("id", "run_id", "stage", "chapter", "model", "tokens_in",
                            "tokens_out", "cost_yuan", "called_at", "estimated")
                    rows = [dict(zip(cols, r)) for r in db.conn.execute(
                        "SELECT id, run_id, stage, chapter, model, tokens_in, tokens_out,"
                        " cost_yuan, called_at, estimated FROM cost_log ORDER BY id DESC LIMIT 100")]
                finally:
                    db.close()
            self._send(200, {"entries": rows})
        elif p == "/costs/summary":
            db_path = Path("logs/runs.db")
            if not db_path.exists():
                self._send(200, {"by_stage": [], "by_model": [], "totals": {}})
                return
            db = RunDB(db_path)
            try:
                STAGE_NAMES = {1: "素材归并", 2: "整体大纲", 3: "逐章大纲", 4: "逐章写作",
                               5: "逻辑检查", 6: "润色", 7: "Word"}
                by_stage = []
                for r in db.conn.execute(
                        "SELECT stage, COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),"
                        " COALESCE(SUM(cache_read),0), COALESCE(SUM(cost_yuan),0), COUNT(*),"
                        " COALESCE(SUM(estimated),0)"
                        " FROM cost_log GROUP BY stage ORDER BY stage"):
                    by_stage.append({
                        "stage": r[0], "name": STAGE_NAMES.get(r[0], str(r[0])),
                        "tokens_in": r[1], "tokens_out": r[2], "cache_read": r[3],
                        "cost_yuan": round(r[4], 4), "calls": r[5], "estimated": r[6],
                    })
                by_model = []
                for r in db.conn.execute(
                        "SELECT model, COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),"
                        " COALESCE(SUM(cache_read),0), COALESCE(SUM(cost_yuan),0), COUNT(*),"
                        " COALESCE(SUM(estimated),0)"
                        " FROM cost_log GROUP BY model ORDER BY COALESCE(SUM(cost_yuan),0) DESC"):
                    by_model.append({
                        "model": r[0], "tokens_in": r[1], "tokens_out": r[2], "cache_read": r[3],
                        "cost_yuan": round(r[4], 4), "calls": r[5], "estimated": r[6],
                    })
                tot = db.conn.execute(
                    "SELECT COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),"
                    " COALESCE(SUM(cache_read),0), COALESCE(SUM(cost_yuan),0), COUNT(*),"
                    " COALESCE(SUM(estimated),0)"
                    " FROM cost_log").fetchone()
                totals = {
                    "tokens_in": tot[0], "tokens_out": tot[1], "cache_read": tot[2],
                    "cost_yuan": round(tot[3], 4), "calls": tot[4], "estimated": tot[5],
                }
                self._send(200, {"by_stage": by_stage, "by_model": by_model, "totals": totals})
            finally:
                db.close()
        elif p == "/models":
            cfg, _ = load_all()
            self._send(200, {"engine": cfg.get("engine"),
                             "providers": cfg.get("providers", {}),
                             "model": cfg.get("model", {})})
        elif p == "/models/available":
            # Return available models per provider (without exposing API keys)
            cfg, _ = load_all()
            providers = cfg.get("providers", {})
            avail = {}
            for pid, prov in providers.items():
                base_url = prov.get("base_url", "")
                api_key_env = prov.get("api_key_env", "")
                api_key = os.environ.get(api_key_env, "")
                avail[pid] = {
                    "base_url": base_url,
                    "api_key_env": api_key_env,
                    "has_key": bool(api_key),
                    "key_mask": (api_key[:4] + "..." + api_key[-4:]) if len(api_key) > 8 else "",
                    "available_models": prov.get("available_models", []),
                }
            self._send(200, {"providers": avail, "engine": cfg.get("engine")})
        elif p == "/env/open":
            # Return path to .env file so user can open it externally
            env_path = str(ROOT / ".env")
            self._send(200, {"env_path": env_path, "exists": Path(env_path).exists()})
        elif p == "/batch_refine/progress":
            progress_path = Path("data/state/batch_refine_progress.json")
            if not progress_path.exists():
                self._send(200, {"status": "idle", "message": "无正在进行的批量精修任务"})
            else:
                try:
                    data = json.loads(progress_path.read_text(encoding="utf-8"))
                    self._send(200, data)
                except Exception as e:
                    self._send(500, {"error": str(e)})
        elif p.startswith("/jobs/"):
            jid = p[len("/jobs/"):]
            job = JOBS.get(jid)
            if not job:
                self._send(404, {"error": "job 不存在: " + jid})
            else:
                self._send(200, job)
        elif p == "/review/report":
            # 读取审查报告 JSON
            report_path = Path("data/outline/review_report.json")
            if not report_path.exists():
                self._send(404, {"error": "no report yet"})
            else:
                try:
                    data = json.loads(report_path.read_text(encoding="utf-8"))
                    self._send(200, data)
                except Exception as e:
                    self._send(500, {"error": str(e)})
        elif p == "/review/decisions":
            # 读取用户决策 JSON
            dec_path = Path("data/outline/review_report.decisions.json")
            if not dec_path.exists():
                self._send(200, {"decisions": []})
            else:
                try:
                    data = json.loads(dec_path.read_text(encoding="utf-8"))
                    self._send(200, data)
                except Exception as e:
                    self._send(500, {"error": str(e)})
        elif p == "/project/list":
            try:
                self._send(200, sb_mod.list_books())
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/prompts/list":
            try:
                items = list_prompts()
                self._send(200, {"items": items, "count": len(items),
                                 "dir": "prompts", "history_dir": "prompts/history"})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/prompts/get" or p.startswith("/prompts/get/"):
            name = ((self._query().get("name") or [""])[0] if p == "/prompts/get"
                    else p[len("/prompts/get/"):])
            path = prompt_path(name)
            if not path:
                self._send(404, {"error": "模板不存在或不在白名单: " + str(name)
                                          + "（须匹配 prompts/stage[1-7]_*.md，禁止子目录与 ../）"})
                return
            try:
                content = path.read_text(encoding="utf-8")
                st = path.stat()
                self._send(200, {
                    "name": path.name,
                    "content": content,
                    "size": st.st_size,
                    "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
                    "backups": [b.name for b in list_prompt_backups(path.name)[-8:]],
                })
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/config/style_notes":
            # 用户风格笔记（config/project.yaml 的 book.style_notes）
            try:
                from utils.project_config import get_style_notes
                self._send(200, {"ok": True, "content": get_style_notes(),
                                 "path": "config/project.yaml", "field": "book.style_notes"})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        else:
            self._send(404, {"error": "未知路径 " + p + "（可用: /health /state /models /project/list /stage/{n}/run /stream/{job_id} /jobs/{id}）"})

    # ---- POST ----
    def do_POST(self):
        path_parts = self.path.split("?")
        p = path_parts[0].rstrip("/")
        body = self._body()
        try:
            if p.startswith("/stage/") and p.endswith("/run"):
                rest = p[len("/stage/"):-len("/run")]
                if not rest.isdigit() or not (1 <= int(rest) <= 7):
                    self._send(400, {"error": "阶段号须为 1-7"})
                    return
                cfg, _ = load_all()
                _client_for_env(cfg, "default")  # 测试模式预检（真实模式构造仅告警）
                only = int(rest)
                from_stage = int(body.get("from_stage") or only)
                if body.get("only_stage") is False:
                    only = None

                # 是否启用流式输出
                want_stream = bool(body.get("stream"))

                if want_stream:
                    # 先创建 streamer 占位，再启动 job
                    job_id_preview = time.strftime("%Y%m%d_%H%M%S_") + "pending"
                    stream_q = queue.Queue(maxsize=10000)
                    stop_ev = threading.Event()
                    with STREAMERS_LOCK:
                        STREAMERS[job_id_preview] = {
                            "queue": stream_q,
                            "stop": stop_ev,
                            "text": [],
                        }

                    def _fn_stream():
                        client = _client_for_env(cfg, "default")
                        # 用真实的 job_id 替换占位
                        real_job = CURRENT.get("id", job_id_preview)
                        with STREAMERS_LOCK:
                            STREAMERS.pop(job_id_preview, None)
                            STREAMERS[real_job] = {
                                "queue": stream_q,
                                "stop": stop_ev,
                                "text": [],
                            }
                        client = _wrap_client_for_streaming(client, real_job)
                        rc = orch_run(from_stage=from_stage, only_stage=only_stage, client=client)
                        return (rc == 0 or rc == 3), "exit=" + str(rc) + ("（3=等待审批）" if rc == 3 else "")

                    jid, err = start_job("stage" + rest, _fn_stream)
                    if err:
                        with STREAMERS_LOCK:
                            STREAMERS.pop(job_id_preview, None)
                        self._send(409, {"error": err})
                        return
                    # 将 preview 关联到真实 job
                    with STREAMERS_LOCK:
                        if jid not in STREAMERS and job_id_preview in STREAMERS:
                            STREAMERS[jid] = STREAMERS.pop(job_id_preview)
                    self._send(202, {"job_id": jid, "stage": only, "from_stage": from_stage, "stream": True})
                else:
                    jid, err = start_job("stage" + rest, act_run_stage(cfg, only, from_stage))
                    if err:
                        self._send(409, {"error": err})
                    else:
                        self._send(202, {"job_id": jid, "stage": only, "from_stage": from_stage})
            elif p == "/stop":
                job_id = str(body.get("job_id") or "")
                if not job_id:
                    self._send(400, {"error": "job_id 必填"})
                    return
                with STREAMERS_LOCK:
                    streamer = STREAMERS.get(job_id)
                if streamer:
                    streamer["stop"].set()
                    self._send(200, {"ok": True, "message": "已发送停止信号: " + job_id})
                else:
                    self._send(404, {"error": "stream not found: " + job_id})
            elif p == "/approve":
                stage = int(body.get("stage") or 0)
                if not (1 <= stage <= 7):
                    self._send(400, {"error": "stage 须为 1-7"})
                    return
                ok, msg = act_approve(stage, bool(body.get("revoke")))
                self._send(200 if ok else 409, {"ok": ok, "message": msg})
            elif p == "/reject":
                stage = int(body.get("stage") or 0)
                if stage not in reject_mod.DOWNSTREAM_ARTIFACTS:
                    self._send(400, {"error": "stage 须为 2-7"})
                    return
                ok, msg = act_reject(stage, str(body.get("reason") or ""),
                                     dry_run=bool(body.get("dry_run")))
                self._send(200 if ok else 400, {"ok": ok, "message": msg})
            elif p == "/refine/outline":
                fb = str(body.get("feedback") or "")
                if not fb.strip():
                    self._send(400, {"error": "feedback 必填"})
                    return
                cfg, _ = load_all()
                _client_for_env(cfg, "default")
                jid, err = start_job("refine_outline",
                                     lambda: act_refine_outline(fb, bool(body.get("dry_run"))))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/refine/chapter":
                fb = str(body.get("feedback") or "")
                ch = int(body.get("chapter") or 0)
                if not fb.strip() or not (1 <= ch <= 999):
                    self._send(400, {"error": "chapter(>=1) 与 feedback 必填"})
                    return
                cfg, _ = load_all()
                _client_for_env(cfg, "default")
                jid, err = start_job("refine_ch" + str(ch),
                                     lambda: act_refine_chapter(ch, fb, bool(body.get("dry_run"))))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/snapshot":
                label = str(body.get("label") or "")
                jid, err = start_job("snapshot", act_snapshot(label))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/models/switch":
                role = str(body.get("role") or "")
                model_id = str(body.get("model") or "")
                if role not in ("default", "writer", "checker", "reviewer"):
                    self._send(400, {"error": "role 须为 default/writer/checker/reviewer"})
                    return
                if not model_id:
                    self._send(400, {"error": "model 必填"})
                    return
                cfg, _ = load_all()
                cfg["model"][role]["id"] = model_id
                (ROOT / "config" / "system.yaml").write_text(
                    yaml.dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
                self._send(200, {"ok": True, "role": role, "model": model_id})
            elif p == "/project/archive":
                name = str(body.get("name") or "").strip()
                force = bool(body.get("force"))
                if not name:
                    self._send(400, {"error": "name 必填"})
                    return
                # 护栏：覆盖同名归档前强制快照（防止数据丢失）
                if force:
                    try:
                        sb_mod_will_overwrite = (sb_mod.BOOKS_DIR / sb_mod.sanitize(name)).exists()
                        if sb_mod_will_overwrite:
                            from snapshot import snapshot as _snap
                            _snap(f"archive_overwrite_{name}")
                    except Exception:
                        pass
                ok, msg = sb_mod.archive(name, yes=bool(body.get("yes")), force=force)
                self._send(200 if ok else 400, {"ok": ok, "message": msg})
            elif p == "/project/restore":
                name = str(body.get("name") or "").strip()
                if not name:
                    self._send(400, {"error": "name 必填"})
                    return
                ok, msg = sb_mod.restore(name, yes=bool(body.get("yes")))
                self._send(200 if ok else 400, {"ok": ok, "message": msg})
            elif p == "/project/init":
                # 护栏：初始化前强制快照
                try:
                    from snapshot import snapshot as _snap
                    _snap("init_empty")
                except Exception:
                    pass
                ok, msg = sb_mod.init_empty()
                self._send(200 if ok else 400, {"ok": ok, "message": msg})
            # ---- 审稿→修稿闭环 ----
            elif p == "/review/run":
                jid, err = start_job("review", act_review_run())
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/batch_refine/run":
                from_decisions = bool(body.get("decisions_from_file", True))
                jid, err = start_job("batch_refine", act_batch_refine_run(from_decisions))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/prompts/save":
                name = str(body.get("name") or "")
                content = body.get("content")
                if not prompt_name_ok(name):
                    self._send(400, {"ok": False, "error":
                        "文件名不在白名单: " + name + "（须匹配 prompts/stage[1-7]_*.md，禁止 ../）"})
                    return
                ok, res = save_prompt(name, content)
                if ok:
                    res["ok"] = True
                    self._send(200, res)
                else:
                    self._send(400, {"ok": False, "error": str(res)})
            elif p == "/config/style_notes":
                # 保存用户风格笔记：定向改写 project.yaml（保留注释），写入前备份
                content = body.get("content")
                if content is None:
                    self._send(400, {"ok": False, "error": "缺少 content 字段"})
                    return
                if not isinstance(content, str):
                    self._send(400, {"ok": False, "error": "content 必须为字符串"})
                    return
                if len(content) > 4000:
                    self._send(400, {"ok": False, "error": "风格笔记过长（上限 4000 字）"})
                    return
                try:
                    from utils.project_config import set_style_notes
                    ok, msg = set_style_notes(content)
                except Exception as e:
                    self._send(500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
                    return
                self._send(200 if ok else 400,
                           {"ok": ok, "message": msg} if ok else {"ok": False, "error": msg})
            elif p == "/review/decisions" and self.command == "POST":
                # 保存用户决策（GUI 提交）
                dec_path = Path("data/outline/review_report.decisions.json")
                dec_path.parent.mkdir(parents=True, exist_ok=True)
                dec_path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
                self._send(200, {"ok": True, "message": "决策已保存: " + str(dec_path)})
            else:
                self._send(404, {"error": "未知路径 " + p})
        except (ValueError, TypeError) as e:
            self._send(400, {"error": "参数错误: " + str(e)[:120]})
        except Exception as e:
            self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})


def main():
    parser = argparse.ArgumentParser(description="NovelForge 本地 API（GUI 化 P0 + 流式输出）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--allow-fake", action="store_true",
                        help="测试模式：stage/refine 用 FakeClient（无 LLM）")
    args = parser.parse_args()
    if args.allow_fake:
        os.environ["NF_API_ALLOW_FAKE"] = "1"
        global ALLOW_FAKE
        ALLOW_FAKE = True
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("[nf_api] NovelForge API 服务 → http://" + args.host + ":" + str(args.port)
          + "  (allow_fake=" + str(ALLOW_FAKE) + ")")
    print("[nf_api] 端点: /health /state /models /stage/{n}/run /stream/{job_id} /stop /jobs/{id} /approve /reject /refine/* /snapshot /costs /prompts/* /config/style_notes")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
