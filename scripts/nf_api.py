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
POST /refine/outline/node {node_id, feedback, dry_run?} → 逐节点 AI 精修
POST /refine/outline/undo 撤销上一次节点精修
POST /refine/chapter      {chapter, feedback, dry_run?} → 章节精修
POST /stage/2/run-multi   {count} → 阶段2 多方案生成（2-5 份 draft）
GET  /outline/structure   结构化大纲视图（acts/nodes/plan/评分）
POST /outline/save        {content} → 保存整份大纲（格式校验+备份）
POST /outline/restore     {version} → 恢复历史版本
GET  /outline/history     版本列表（含评分摘要）
GET  /outline/diff        ?v1=&v2= → 结构化节点级 diff
GET  /outline/drafts      多方案 draft 列表
POST /outline/compose     {selections, act_source} → 拼合为最终 global.md
POST /outline/drafts/cleanup 清理临时 draft
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

# 大纲路径常量（与 refine_outline.py / utils.outline_panel 保持一致）
GLOBAL = "data/outline/global.md"
HISTORY_DIR = "data/outline/history"

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


# ---- 大纲结构化面板（任务1/2/3/4） ----

def act_outline_save(content):
    """保存整份大纲：先校验格式完整性，通过才写盘（写前备份）。"""
    import stage2_outline as s2
    import refine_outline as ro
    from utils.file_io import write_text as _wt, read_text as _rt

    if not isinstance(content, str) or not content.strip():
        return False, "内容为空"
    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        return False, "内容超过 2MB 上限"

    version = ro.next_version(HISTORY_DIR)
    backup = ro.backup_current(GLOBAL, HISTORY_DIR, version)
    _wt(GLOBAL, content)
    ok, errors = s2.check_global_outline(GLOBAL)
    if not ok:
        # 校验失败：回滚到备份（A2 红线：不满足格式即拒绝保存）
        import shutil as _sh
        _sh.copy2(backup, GLOBAL)
        return False, "格式校验失败，已拒绝保存并回滚: " + "; ".join(errors)

    from utils import outline_panel as op
    return True, {"version": version, "backup": str(backup),
                  "structure": op.build_structure()}


def act_outline_restore(version):
    """把某历史版本恢复到 global.md（写前备份当前版）。"""
    import shutil as _sh
    from utils import outline_panel as op
    import refine_outline as ro

    src = op.resolve_version(version)
    if not src:
        return False, "版本不存在: " + str(version)
    if not Path(GLOBAL).exists():
        return False, "当前无 global.md"
    new_v = ro.next_version(HISTORY_DIR)
    _sh.copy2(GLOBAL, Path(HISTORY_DIR) / ("global_v%d.md" % new_v))
    _sh.copy2(src, GLOBAL)
    return True, {"restored": src.name, "saved_current_as": new_v,
                  "structure": op.build_structure()}


def act_outline_refine_node(node_id, feedback, dry_run=False):
    """节点级 AI 精修（任务2）。"""
    from utils import outline_panel as op
    cfg, proj = load_all()
    return op.apply_node_refine(cfg, proj, node_id, feedback,
                                client=_client_for_env(cfg, "default"),
                                dry_run=dry_run)


def act_outline_undo():
    """撤销上一次节点精修（从 history 最新版本恢复）。"""
    from utils import outline_panel as op
    ok, res = op.undo_last_refine()
    if not ok:
        return False, res
    return True, {"undo": res, "structure": op.build_structure()}


def act_outline_compose(selections, act_source, cleanup=True):
    """拼合多方案（任务3）。"""
    from utils import outline_panel as op
    ok, res = op.compose_from_drafts(selections, act_source=act_source)
    if not ok:
        return False, res
    if cleanup:
        op.cleanup_drafts()
    return True, res


def act_outline_drafts_cleanup():
    from utils import outline_panel as op
    removed = op.cleanup_drafts()
    return True, {"removed": removed}


def act_stage2_run_multi(count=3, interval=2.0):
    """阶段2 多方案生成（任务3）：串行生成 N 份 draft，**全程不触碰 global.md**。

    做法：把任务模板里的输出路径 {{path_global_outline}} 逐份指向独立的 draft 文件，
    因此模型的产物直接落盘到 data/outline/global_draft_N.md，原始 global.md 原封不动。
    """
    import stage2_outline as s2
    import outline_review as ov
    from utils.file_io import write_text as _wt

    def _fn():
        cfg, proj = load_all()
        client = _client_for_env(cfg, "default")
        drafts = []
        for i in range(1, count + 1):
            draft = Path("data/outline/global_draft_%d.md" % i)
            try:
                draft.unlink()
            except OSError:
                pass
            # 关键：把输出路径改指到 draft 文件，模型产物不会覆盖 global.md
            body = s2.build_task(cfg, proj).replace(
                str(Path(GLOBAL).resolve()), str(draft.resolve()))
            task = client.write_task("data/state/tasks",
                                     "stage2_global_outline.md", body)
            result = client.run_task(task)
            if result.get("exit_code") != 0:
                return False, "第 %d 份方案生成失败" % i
            if not draft.exists():
                return False, "第 %d 份方案未产出 %s" % (i, draft.name)
            tmp = draft.read_text(encoding="utf-8")
            ok, errors = _check_text(tmp)
            rv = ov.review(str(draft), "data/setting/setting.json")
            drafts.append({
                "id": i, "name": draft.name,
                "summary": rv.get("summary", {}),
                "issues": rv.get("issues", []),
                "valid": ok, "errors": errors,
            })
            if i < count:
                time.sleep(interval)          # 串行 + 间隔，避免限流
        return True, {"drafts": drafts, "count": len(drafts)}

    return _fn


def _check_text(text):
    """对内存文本做结构校验（复用 check_global_outline 的规则）。"""
    import stage2_outline as s2
    errors = []
    for sec in s2.REQUIRED_SECTIONS:
        if not re.search(rf"^##\s*{sec}", text, re.M):
            errors.append("缺少章节: ## " + sec)
    if len(re.findall(r"^##\s*关键节点", text, re.M)) == 0:
        errors.append("缺少 ## 关键节点")
    m = re.search(r"^##\s*预计章节数\s*\n\s*(\d+)", text, re.M)
    if not m or int(m.group(1)) <= 0:
        errors.append("缺少有效的 ## 预计章节数")
    return (not errors), errors


class Handler(BaseHTTPRequestHandler):
    server_version = "NovelForgeAPI/0.2"

    # 仅本机来源（Electron file:// 与本地预览端口）；不开放 *，避免暴露给任意网页
    ALLOW_ORIGIN_RE = re.compile(r"^(https?://(127\.0\.0\.1|localhost)(:\d+)?|file://.*|null)$")

    def _cors(self):
        """本机来源放行 CORS（GUI 从 file:// 或本地预览端口访问 API 时必需）。"""
        origin = self.headers.get("Origin") or ""
        if origin and self.ALLOW_ORIGIN_RE.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        elif not origin:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")

    def do_OPTIONS(self):
        """CORS 预检。"""
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- 基础设施 ----
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
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
        self._cors()
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
        elif p == "/outline/structure":
            # 结构化大纲视图（任务1）：解析 global.md + 贴 outline_review 评分
            try:
                from utils import outline_panel as op
                q = self._query()
                only = (q.get("review") or ["1"])[0] not in ("0", "false", "no")
                self._send(200, op.build_structure(run_review=only))
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/outline/history":
            # 版本列表（任务4）
            try:
                from utils import outline_panel as op
                self._send(200, {"versions": op.list_versions()})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/outline/diff":
            # 结构化节点级 diff（任务4）
            try:
                from utils import outline_panel as op
                q = self._query()
                v1 = (q.get("v1") or [""])[0]
                v2 = (q.get("v2") or [""])[0]
                if not v1:
                    self._send(400, {"error": "缺少参数 v1（对比基准版本）"})
                    return
                f1 = op.resolve_version(v1)
                f2 = op.resolve_version(v2) or op.resolve_version("0")
                if not f1 or not f2:
                    self._send(404, {"error": "版本不存在: v1=" + str(v1) + " v2=" + str(v2)})
                    return
                data = op.diff_outlines(f1, f2)
                data["v1"] = int(v1) if str(v1).isdigit() else 0
                data["v2"] = int(v2) if str(v2).isdigit() else 0
                self._send(200, data)
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/outline/drafts":
            # 多方案 draft 列表（任务3）
            try:
                from utils import outline_panel as op
                drafts = op.list_drafts()
                df = op.draft_files()
                # 给每份 draft 附上四节原文（前端拼合时预览用）
                for d in drafts:
                    try:
                        from utils.file_io import read_text as _rt
                        import utils.outline_struct as _osr
                        d["acts"] = _osr.parse_global(_rt(op.draft_path(d["id"])))["acts"]
                    except Exception:
                        pass
                self._send(200, {"drafts": drafts, "files": df})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
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
        elif p == "/project/status":
            # 返回项目结构树（用于左侧导航）
            try:
                import json
                project_status = {
                    "book": load_all()[1].get("book", {}),
                    "stages": build_state().get("stages", []),
                    "setting_exists": (ROOT / "data" / "setting" / "setting.json").exists(),
                    "outline_exists": (ROOT / "data" / "outline" / "global.md").exists(),
                    "chapters_count": len(list((ROOT / "data" / "outline" / "chapters").glob("*.md"))) if (ROOT / "data" / "outline" / "chapters").exists() else 0,
                    "drafts_exist": (ROOT / "data" / "chapters" / "raw").exists() and any((ROOT / "data" / "chapters" / "raw").glob("*.md")),
                    "refined_exist": (ROOT / "data" / "chapters" / "refined").exists() and any((ROOT / "data" / "chapters" / "refined").glob("*.md")),
                    "word_exists": bool(list((ROOT / "output").glob("*.docx"))),
                    "materials_count": len(list((ROOT / "materials" / "raw").glob("*"))) if (ROOT / "materials" / "raw").exists() else 0,
                }
                self._send(200, project_status)
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
        elif p == "/materials/list":
            try:
                from utils.materials_manager import MaterialsManager
                mm = MaterialsManager(ROOT / "materials" / "raw")
                self._send(200, mm.list_materials())
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p.startswith("/materials/read/"):
            name = unquote(p[len("/materials/read/"):])
            try:
                from utils.materials_manager import MaterialsManager
                mm = MaterialsManager(ROOT / "materials" / "raw")
                content = mm.read_content(name)
                self._send(200, {"ok": True, "name": name, "content": content})
            except Exception as e:
                self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
        else:
            self._send(404, {"error": "未知路径 " + p + "（可用: /health /state /models /project/list /stage/{n}/run /stream/{job_id} /jobs/{id} /materials/*）"})

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
            # ---- 大纲结构化面板（任务1/2/3/4）----
            elif p == "/outline/save":
                ok, res = act_outline_save(body.get("content"))
                if ok:
                    res["ok"] = True
                    self._send(200, res)
                else:
                    self._send(400, {"ok": False, "error": res})
            elif p == "/outline/restore":
                ok, res = act_outline_restore(body.get("version"))
                if ok:
                    res["ok"] = True
                    self._send(200, res)
                else:
                    self._send(400, {"ok": False, "error": res})
            elif p == "/refine/outline/node":
                node_id = str(body.get("node_id") or body.get("entry_id") or "")
                fb = str(body.get("feedback") or "")
                if not node_id:
                    self._send(400, {"error": "node_id 必填"})
                    return
                if not fb.strip():
                    self._send(400, {"error": "feedback 必填"})
                    return
                cfg, _ = load_all()
                _client_for_env(cfg, "default")
                dry = bool(body.get("dry_run"))
                jid, err = start_job("refine_node_" + node_id,
                                     lambda: act_outline_refine_node(node_id, fb, dry))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/refine/outline/undo":
                ok, res = act_outline_undo()
                if ok:
                    res["ok"] = True
                    self._send(200, res)
                else:
                    self._send(400, {"ok": False, "error": res})
            elif p == "/stage/2/run-multi":
                count = int(body.get("count") or 3)
                if not (2 <= count <= 5):
                    self._send(400, {"error": "count 须为 2-5"})
                    return
                cfg, _ = load_all()
                _client_for_env(cfg, "default")
                interval = float(body.get("interval") or 2.0)
                jid, err = start_job("stage2_multi",
                                     act_stage2_run_multi(count, interval))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid, "count": count})
            elif p == "/outline/compose":
                sels = body.get("selections")
                if not isinstance(sels, list):
                    self._send(400, {"error": "selections 须为数组"})
                    return
                try:
                    act_src = int(body.get("act_source") or 1)
                except (TypeError, ValueError):
                    act_src = 1
                ok, res = act_outline_compose(sels, act_src,
                                              cleanup=bool(body.get("cleanup", True)))
                if ok:
                    res["ok"] = True
                    self._send(200, res)
                else:
                    self._send(400, {"ok": False, "error": res})
            elif p == "/outline/drafts/cleanup":
                ok, res = act_outline_drafts_cleanup()
                res["ok"] = True
                self._send(200, res)
            elif p == "/config/project":
                try:
                    from utils.project_config import get_project_config
                    self._send(200, {"ok": True, "config": get_project_config()})
                except Exception as e:
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/setting/current":
                try:
                    import json
                    setting_path = ROOT / "data" / "setting" / "setting.json"
                    if setting_path.exists():
                        self._send(200, {"ok": True, "setting": json.loads(setting_path.read_text(encoding="utf-8"))})
                    else:
                        self._send(404, {"error": "setting.json 不存在"})
                except Exception as e:
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/setting/save":
                try:
                    import json
                    from utils.file_io import write_text
                    data = body.get("setting")
                    if not data or not isinstance(data, dict):
                        self._send(400, {"error": "setting 必须为对象"})
                        return
                    setting_path = ROOT / "data" / "setting" / "setting.json"
                    if setting_path.exists():
                        backup_dir = ROOT / "data" / "setting" / "history"
                        backup_dir.mkdir(parents=True, exist_ok=True)
                        import time
                        stamp = time.strftime("%Y%m%d_%H%M%S")
                        backup = backup_dir / f"setting_{stamp}.json"
                        backup.write_text(setting_path.read_text(encoding="utf-8"), encoding="utf-8")
                    write_text(str(setting_path), json.dumps(data, ensure_ascii=False, indent=2))
                    self._send(200, {"ok": True})
                except Exception as e:
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/outline/chapters/list":
                try:
                    chapters_dir = ROOT / "data" / "outline" / "chapters"
                    if not chapters_dir.exists():
                        self._send(200, {"chapters": []})
                        return
                    chapters = []
                    for f in sorted(chapters_dir.glob("*.md")):
                        content = f.read_text(encoding="utf-8")
                        title = content.split("\n")[0].lstrip("#").strip() if content else f.stem
                        chapters.append({"file": f.name, "title": title, "size": f.stat().st_size})
                    self._send(200, {"chapters": chapters})
                except Exception as e:
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/outline/chapters/get":
                try:
                    q = self._query()
                    n = (q.get("n") or [""])[0]
                    if not n.isdigit():
                        self._send(400, {"error": "n 必须为数字"})
                        return
                    chapter_path = ROOT / "data" / "outline" / "chapters" / f"{n}.md"
                    if not chapter_path.exists():
                        self._send(404, {"error": f"第 {n} 章大纲不存在"})
                        return
                    content = chapter_path.read_text(encoding="utf-8")
                    self._send(200, {"ok": True, "n": int(n), "content": content})
                except Exception as e:
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
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
            elif p == "/materials/add":
                # 添加素材：从外部路径复制到 raw/  (body: {src_path, overwrite?})
                src_path = str(body.get("src_path") or "").strip()
                overwrite = bool(body.get("overwrite", False))
                if not src_path:
                    self._send(400, {"ok": False, "error": "src_path 必填"})
                    return
                try:
                    from utils.materials_manager import MaterialsManager
                    mm = MaterialsManager(ROOT / "materials" / "raw")
                    res = mm.add_from_path(src_path, overwrite=overwrite)
                    res["ok"] = True
                    self._send(200, res)
                except Exception as e:
                    self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/materials/new":
                # 新建素材文件  (body: {name, content?})
                name = str(body.get("name") or "").strip()
                content = str(body.get("content") or "")
                if not name:
                    self._send(400, {"ok": False, "error": "name 必填"})
                    return
                try:
                    from utils.materials_manager import MaterialsManager
                    mm = MaterialsManager(ROOT / "materials" / "raw")
                    res = mm.create_new(name, content)
                    res["ok"] = True
                    self._send(200, res)
                except Exception as e:
                    self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/materials/save":
                # 保存素材文本内容  (body: {name, content})
                name = str(body.get("name") or "").strip()
                content = body.get("content")
                if not name:
                    self._send(400, {"ok": False, "error": "name 必填"})
                    return
                if content is None or not isinstance(content, str):
                    self._send(400, {"ok": False, "error": "content 必须为字符串"})
                    return
                try:
                    from utils.materials_manager import MaterialsManager
                    mm = MaterialsManager(ROOT / "materials" / "raw")
                    res = mm.save_content(name, content)
                    res["ok"] = True
                    self._send(200, res)
                except Exception as e:
                    self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/materials/delete":
                # 删除素材（body: {name}）
                name = str(body.get("name") or "").strip()
                if not name:
                    self._send(400, {"ok": False, "error": "name 必填"})
                    return
                try:
                    from utils.materials_manager import MaterialsManager
                    mm = MaterialsManager(ROOT / "materials" / "raw")
                    res = mm.delete(name)
                    res["ok"] = True
                    self._send(200, res)
                except Exception as e:
                    self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/materials/remerge":
                # 触发 stage1 重归并（清 fingerprint + 跑 stage1）
                try:
                    cfg, _ = load_all()
                    jid, err = start_job("remerge", act_run_stage(cfg, 1, 1))
                    if err:
                        self._send(409, {"error": err})
                    else:
                        self._send(202, {"job_id": jid, "message": "已触发素材重归并（阶段1）"})
                except Exception as e:
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
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
    print("[nf_api] 端点: /health /state /models /stage/{n}/run /stream/{job_id} /stop /jobs/{id} /approve /reject /refine/* /outline/* /snapshot /costs /prompts/* /config/style_notes /materials/* /project/*")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
