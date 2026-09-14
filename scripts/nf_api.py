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
POST /stop                {job_id} → 中断：流式置 streamer.stop；非流式置 STOP_EVENTS
                          （orchestrator 在阶段边界轮询 should_stop()，退出码 4）
GET  /jobs/{id}           job 状态/结果
POST /approve             {stage, revoke?} → 审批/撤销
POST /reject              {stage, reason, dry_run?} → 打回（默认真执行）
POST /refine/outline      {feedback, dry_run?} → 大纲精修
POST /refine/outline/node {node_id, feedback, dry_run?} → 逐节点 AI 精修
POST /refine/outline/undo 撤销上一次节点精修
POST /refine/chapter      {chapter, feedback, dry_run?} → 章节精修（改稿前自动备份）
GET  /chapters/history    ?n=3 → 第 3 章的版本列表（data/chapters/history/chNN_vM.md）
POST /chapters/restore    {n, version} → 回退到历史版本（回退前再存一版当前稿）
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
GET  /costs/streaming     当前 run 实时消耗 + 预算进度（GUI 成本页轮询）
POST /models/switch       {role, model} → 切换模型
GET  /models/cache        动态拉取的模型缓存（含手动添加的 _manual）
POST /models/add          {name} → 手动把一个模型名写进缓存（GUI「手动添加」用）
GET  /scraps/list         原始碎片：按簇分组 + 时间轴 + 前瞻备忘（确定性，零 LLM）
GET  /scraps/read         ?name= → 读取单个碎片正文
POST /scraps/save         {name, content} → 写前备份后保存碎片
POST /scraps/delete       {name, confirm:true} → 先快照再删除碎片（破坏性操作）
POST /scraps/promote      {card_name, card_type, points[], open_questions[], sources[]}
                          → 由 Python 落盘成 materials/raw/<名>_<类型>.md（覆盖前备份）
POST /auto_rewrite/run    {threshold?, max_rounds?, chapters?, dry_run?} → job_id
                          质量自评闭环：重写 quality<阈值 章节（dry_run 默认 true，防误改稿）
GET  /estimate            ?stage=4 / ?stages=1,2 / ?no_history=1 → token+费用预估（确定性，零 LLM）
GET  /proofread/report    校对报告（stage 5.5；schema 与 review_report 对齐，GUI 可复用审稿组件）
POST /proofread/run       {scope?, llm?, dry_run?, report?} → job_id（确定性+可选 LLM 语义校对）
POST /style/analyze       {source: reference|path|chapter, path?, n?, scope?, compare?}
                          → 文风特征；带 compare 时附带逐维度风格偏差
POST /book/split          {path, emit?} → 拆书/章节节奏（同步返回，落盘 data/state/book_pacing.json）
GET  /book/pacing         拆书节奏结果（供 GUI 画图）
POST /models/switch       {role, model} → 切换模型
POST /export/markdown     {per_vol?, book_name?} → Markdown 分卷导出

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
import shutil
import threading
import time
import uuid
from datetime import datetime
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
# 校对 / 预估 / 拆书：模块级导入（绝不在 do_GET/do_POST 内写裸 import，
# 否则该名会被判定为整个函数的局部名，同函数其它分支一用就 UnboundLocalError）
import proofread as proofread_mod  # noqa: E402
import estimate_tokens as estimate_mod  # noqa: E402
import book_split as book_split_mod  # noqa: E402
import refine_chapter as refine_ch_mod  # noqa: E402
# 模块级导入文件读写（勿在 do_GET/do_POST 内写 `import json` 这类裸导入：
# 函数内任意位置出现 `import json` 都会把 json 变成该函数的局部名，
# 导致同一函数内其它分支的 json.xxx 抛 UnboundLocalError —— 2026-09-14 修）
from utils.file_io import read_text as nf_read_text, write_text as nf_write_text  # noqa: E402

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

# ---- 停止标志（非流式 job）----
STOP_EVENTS = {}  # job_id -> threading.Event（set 表示请求停止）
STOP_LOCK = threading.Lock()


def should_stop(job_id=None):
    """检查是否有停止请求。返回 True/False。

    job_id 为 None 时检查「当前在跑的 job」（CURRENT["id"]），
    这样 orchestrator 无需自己持有 job_id —— 它是被 API 的 worker 线程调用的。

    停止是**协作式**的：orchestrator 在每个阶段开始前轮询此函数，
    置位后不会再启动新阶段（正在跑的 LLM 子会话无法中途打断）。
    """
    if job_id is None:
        job_id = CURRENT["id"]
    if not job_id:
        return False
    with STOP_LOCK:
        ev = STOP_EVENTS.get(job_id)
    return ev is not None and ev.is_set()


def clear_stop(job_id=None):
    """清除停止标志（job 结束时调用，避免 STOP_EVENTS 无界增长）。"""
    if job_id is None:
        job_id = CURRENT["id"]
    if not job_id:
        return
    with STOP_LOCK:
        STOP_EVENTS.pop(job_id, None)

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


# ---------------------------------------------------------------- 原始碎片（GUI）

SCRAPS_DIR_DEFAULT = "materials/original_scraps"
RAW_DIR = "materials/raw"
CARD_KINDS = {"角色卡": "角色", "场景卡": "场景", "概念卡": "概念",
              "组织卡": "组织", "物品卡": "物品"}


def scraps_dir_path():
    """碎片目录（config/project.yaml 的 materials.scraps_dir，默认 materials/original_scraps）。"""
    try:
        _, proj = load_all()
        d = (proj.get("materials") or {}).get("scraps_dir") or SCRAPS_DIR_DEFAULT
    except Exception:
        d = SCRAPS_DIR_DEFAULT
    p = Path(d)
    return p if p.is_absolute() else (ROOT / p)


def _safe_member(name):
    """校验文件名（禁止路径穿越）。返回 (ok, name or error)。"""
    n = (name or "").strip()
    if not n or "/" in n or "\\" in n or ".." in n or n in (".", ".."):
        return False, "非法文件名: %r" % name
    return True, n


def _backup_copy(target, backup_root):
    """覆盖写前备份。返回备份相对路径或 None。"""
    target = Path(target)
    if not target.exists():
        return None
    try:
        backup_root = Path(backup_root)
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        bak = backup_root / ("%s_%s%s" % (target.stem, stamp, target.suffix))
        n = 1
        while bak.exists():
            bak = backup_root / ("%s_%s_%d%s" % (target.stem, stamp, n, target.suffix))
            n += 1
        bak.write_bytes(target.read_bytes())
        return str(bak.relative_to(ROOT)).replace("\\", "/")
    except Exception as e:      # noqa: BLE001
        print("[nf_api] 备份失败: " + str(e))
        return None


def _point_line(p):
    """把信息点渲染成一行（兼容字符串与结构化 dict）。"""
    if isinstance(p, str):
        return p.strip()
    content = str(p.get("content") or "").strip()
    tags = []
    if p.get("ts"):
        tags.append(str(p["ts"]))
    if p.get("cluster"):
        tags.append(str(p["cluster"]))
    if p.get("confidence") == "low":
        tags.append("低置信")
    return content + ("（%s）" % "｜".join(tags) if tags else "")


def build_card_markdown(card_name, card_type, points, open_questions=(),
                        sources=(), book_name=""):
    """生成素材卡 Markdown（与 materials/raw 既有卡片风格一致）。"""
    kind = CARD_KINDS.get(card_type, "条目")
    out = ["# %s：%s" % (kind, card_name), ""]
    out.append("> 来源：原始碎片提炼（materials/original_scraps/，用户私人碎片，自由命名）")
    out.append("> 用途：《%s》素材。下列信息点提炼自用户原始碎片；"
               "【待定区】为作者尚未确定的事，不得当作正典约束引用。"
               % (book_name or "未命名"))
    out.append("")
    out.append("## 信息点")
    out.append("")
    body = [_point_line(p) for p in (points or [])]
    body = [b for b in body if b]
    out.extend(["- " + b for b in body] or ["- （无）"])
    out.append("")
    if open_questions:
        out.append("## 待定区（来源碎片中尚未确定，禁止当作正典）")
        out.append("")
        out.extend(["- " + str(q).strip() for q in open_questions if str(q).strip()])
        out.append("")
    out.append("## 来源溯源")
    out.append("")
    src = [s for s in (sources or []) if s]
    if src:
        out.extend(["- 碎片：%s" % s for s in src])
    else:
        out.append("- （未记录来源碎片，建议在 GUI 里勾选后重新提炼）")
    out.append("")
    return "\n".join(out)


def promote_to_card(card_name, card_type, points, open_questions=(), sources=(),
                    book_name="", overwrite=True):
    """把信息点落盘成 materials/raw/<名>_<类型>.md。返回 (rel_path, backup_rel)。

    模型写不了 materials/（llm_client 白名单只有 data/**），所以卡片一律由 Python 写。
    """
    ok, err = _safe_member(card_name)
    if not ok:
        raise ValueError(err)
    stem = Path(card_name).stem
    if card_type not in CARD_KINDS:
        raise ValueError("未知卡片类型: %r（可选 %s）"
                         % (card_type, "、".join(CARD_KINDS)))
    suffix = "_" + card_type
    if stem.endswith(suffix):
        stem = stem[:-len(suffix)]
    display = stem or card_name          # 标题只用名字，类型单独展示
    file_stem = stem + suffix
    target = ROOT / RAW_DIR / (file_stem + ".md")
    if target.exists() and not overwrite:
        raise FileExistsError("卡片已存在: %s" % file_stem)
    backup = _backup_copy(target, ROOT / RAW_DIR / "_backup")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_card_markdown(display, card_type, points, open_questions,
                                         sources, book_name), encoding="utf-8")
    return str(target.relative_to(ROOT)).replace("\\", "/"), backup


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
    if "auto_rewritten" in st:
        out["auto_rewritten"] = st["auto_rewritten"]
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
        "budget": {
          "paused": progress.data.get("budget", {}).get("paused", False),
        },
    }


def build_streaming_cost():
    """当前 run 的实时消耗 + 预算进度（GUI 成本页「当前运行」卡片）。

    「当前 run」= runs 表里 status='running' 的最新一条；没有则退化为最近一条 run
    （跑完后仍能看最后一次 run 的账）。数据全部取自 cost_log 实测记账；
    estimated_calls 是其中「按字符折算、尚未回填实测」的调用数，单独透出，
    让前端能标注「含估算」而不是把它混进实测数字里。
    """
    cfg, _proj = load_all()
    budget = cfg.get("budget", {}) or {}
    limit_yuan = float(os.environ.get("BUDGET_LIMIT_YUAN") or budget.get("limit_yuan", 300))
    cur_id = CURRENT["id"]
    cur_job = (JOBS.get(cur_id) or {}) if cur_id else {}
    out = {
        "ok": True,
        "running": bool(cur_id),
        "job_id": cur_id,
        "kind": cur_job.get("kind"),
        "job_started_at": cur_job.get("started_at"),
        "run_id": None, "run_status": None, "run_started_at": None,
        "spent_yuan": 0.0, "tokens_in": 0, "tokens_out": 0, "cache_read": 0,
        "calls": 0, "estimated_calls": 0,
        "limit_yuan": limit_yuan, "warn_ratio": budget.get("warn_ratio", 0.7),
        "pct": 0.0, "budget_left": limit_yuan,
        "elapsed_s": 0, "yield_yuan_per_min": 0.0,
    }

    db_path = Path("logs/runs.db")
    if not db_path.exists():
        return out
    db = RunDB(db_path)
    try:
        # 取**最新一条 run**：有 job 在跑时最新 run 就是当前 run；
        # 不按 status='running' 过滤 —— 崩溃残留的旧 running 行会盖掉真实结果。
        row = db.conn.execute(
            "SELECT id, started_at, status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return out
        run_id, started_at, status = row[0], row[1], row[2]
        agg = db.conn.execute(
            "SELECT COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),"
            " COALESCE(SUM(cache_read),0), COALESCE(SUM(cost_yuan),0), COUNT(*),"
            " COALESCE(SUM(estimated),0) FROM cost_log WHERE run_id=?",
            (run_id,)).fetchone()
        out.update({
            "run_id": run_id, "run_status": status, "run_started_at": started_at,
            "tokens_in": int(agg[0]), "tokens_out": int(agg[1]), "cache_read": int(agg[2]),
            "spent_yuan": round(float(agg[3]), 6),
            "calls": int(agg[4]), "estimated_calls": int(agg[5]),
        })
        # 已耗时只在真有 job 在跑时给：否则「最后一次 run 至今」会算出几十小时的假时长
        t0 = None
        if out["running"]:
            for cand in (out["job_started_at"], started_at):
                if not cand:
                    continue
                try:
                    dt = datetime.strptime(cand, "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    continue
                t0 = dt if t0 is None else max(t0, dt)
        if t0 is not None:
            out["elapsed_s"] = max(0, int((datetime.now() - t0).total_seconds()))
    finally:
        db.close()

    out["budget_left"] = round(limit_yuan - out["spent_yuan"], 6)
    out["pct"] = round(out["spent_yuan"] / limit_yuan * 100, 2) if limit_yuan > 0 else 0.0
    if out["elapsed_s"] >= 30:
        out["yield_yuan_per_min"] = round(out["spent_yuan"] / (out["elapsed_s"] / 60.0), 6)
    return out


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
            clear_stop(job_id)

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
        # 退出码：0=完成 1=阶段失败 2=预算熔断 3=等待审批 4=用户中断
        # 3 与 4 都不是「失败」：3 是审批门正常暂停，4 是用户主动停止 → 不标红
        if rc == 4:
            return True, "用户中断（exit=4）"
        if rc == 3:
            return True, "exit=3（等待审批，属正常门暂停）"
        return (rc == 0), "exit=" + str(rc)
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
    """执行批量精修（决策从 review_report.decisions.json 读取）。"""
    import batch_refine
    cfg, proj = load_all()
    # 与 act_review_run 一致：经 _client_for_env，测试模式（--allow-fake）下走 FakeClient，
    # 否则批量精修会绕过 fake 直连真实 API。
    client = _client_for_env(cfg, "polisher")

    def _fn():
        ok, msg = batch_refine.run_batch_refine(
            report_path="data/outline/review_report.json",
            decisions_mode="file" if decisions_from_file else "interactive",
            auto_accept=False,
            dry_run=False,
            client=client,
        )
        return ok, msg
    return _fn


def act_auto_rewrite_run(threshold=None, dry_run=False, max_rounds=None, chapters=None):
    """执行质量自评闭环：自动重写 quality<阈值 的章节（P2 方向3）。"""
    import auto_rewrite
    from utils.cost_tracker import CostTracker

    cfg, proj = load_all()
    progress = ProgressManager("data/state/progress.json")
    client = _client_for_env(cfg, "writer")

    def _fn():
        db, cost, run_id, ok = None, None, None, False
        if not dry_run:
            db = RunDB("logs/runs.db")
            budget = cfg.get("budget", {}) or {}
            cost = CostTracker(db, limit_yuan=budget.get("limit_yuan", 300),
                               warn_ratio=budget.get("warn_ratio", 0.7))
            run_id = db.start_run(plan_json="auto_rewrite_api")
        try:
            ok, msg, _ = auto_rewrite.run_auto_rewrite(
                cfg, proj, progress, db=db, cost=cost, run_id=run_id,
                threshold=threshold, max_rounds=max_rounds, dry_run=dry_run,
                client=client, chapters=chapters)
        finally:
            if db is not None:
                db.finish_run(run_id, "done" if ok else "failed")
                db.close()
        return ok, msg
    return _fn


def act_appearances_refresh():
    """重算角色出场统计（确定性、零 LLM、零成本）并回传。"""
    import appearances as ap_mod

    def _fn():
        stats, total, new = ap_mod.count_appearances()
        if not stats:
            return False, "未找到角色或章节（先跑 stage3/4）"
        out = ap_mod.write_appearances(stats, total, new)
        ap_mod.print_summary(stats, total, new)
        return True, f"已更新 {out}（{len(stats)} 角色 / {total} 章）"
    return _fn


# ---- 校对（stage 5.5）与文风分析（P1） ----

def act_proofread_run(scope=None, report_path="data/outline/proofread_report.json",
                      use_llm=False, dry_run=False):
    """执行校对。确定性部分零 token；use_llm=True 时追加 LLM 语义校对。"""
    cfg, _ = load_all()
    # 与其它 LLM 调用点一致：经 _client_for_env，测试模式（--allow-fake）走 FakeClient
    client = _client_for_env(cfg, "checker") if (use_llm and not dry_run) else None

    def _fn():
        return proofread_mod.run_proofread(scope=scope, report_path=report_path,
                                           use_llm=use_llm, dry_run=dry_run, client=client)
    return _fn


def act_style_analyze(body):
    """文风分析：对范文 / 任意文本 / 指定章节抽特征，可选与某章算偏差。

    body:
      source: "reference"（配置的范文）| "path"（任意文件）| "chapter"（data/chapters/*/NN.md）
      path:   source=path 时的文件路径
      n:      source=chapter 时的章号
      scope:  source=chapter 时的目录（raw|checked|refined，默认自动）
      compare: 可选，{scope, n} → 与该章输出对比求偏差
    """
    from utils import style_analyzer as sa

    src = str(body.get("source") or "reference").strip()
    text = ""
    label = ""
    if src == "reference":
        _, proj = load_all()
        ref = ((proj.get("book") or {}).get("style_reference") or "").strip()
        if not ref:
            return {"ok": False, "configured": False, "soft": True,
                    "hint": "未配置 book.style_reference（config/project.yaml）；"
                            "也可用 source=path 直接指定文件"}
        if not Path(ref).exists():
            return {"ok": False, "configured": True, "soft": True,
                    "hint": "范文文件不存在: " + ref}
        # 注意：style_analyzer.load_style_reference 返回的是**风格指令文本**，
        # 这里要的是范文原文（用于抽特征/画图），故直接读文件。
        text = nf_read_text(ref)
        label = ref
    elif src == "path":
        raw = str(body.get("path") or "").strip()
        if not raw:
            return {"ok": False, "error": "source=path 时 path 必填"}
        p = Path(raw)
        # 安全边界：只读，且限制大小，防误选巨型文件
        if not p.exists() or not p.is_file():
            return {"ok": False, "error": "文件不存在: " + raw}
        if p.stat().st_size > 8 * 1024 * 1024:
            return {"ok": False, "error": "文件超过 8MB 上限"}
        text = nf_read_text(p)
        label = raw
    elif src == "chapter":
        try:
            n = int(body.get("n"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "source=chapter 时 n 必须为整数"}
        scope = str(body.get("scope") or "").strip()
        cands = ([scope] if scope in ("raw", "checked", "refined")
                 else ["refined", "checked", "raw"])
        found = None
        for sc in cands:
            cand = Path("data/chapters") / sc / ("%02d.md" % n)
            if cand.exists():
                found = cand
                break
        if not found:
            return {"ok": False, "error": "第 %d 章产物不存在（先跑 stage4/5/6）" % n}
        text = nf_read_text(found)
        label = str(found)
    else:
        return {"ok": False, "error": "source 只能是 reference / path / chapter"}

    feats = sa.extract_style_features(text)
    payload = {
        "ok": True,
        "source": src,
        "label": label,
        "chars": len(text),
        "features": feats,
        "sufficient": bool(feats),
        "hint": "" if feats else "文本过短（<100 字符），特征不可靠",
    }
    cmp_spec = body.get("compare")
    if isinstance(cmp_spec, dict):
        try:
            cn = int(cmp_spec.get("n"))
        except (TypeError, ValueError):
            payload["drift_error"] = "compare.n 必须为整数"
            return payload
        cscope = str(cmp_spec.get("scope") or "").strip()
        cands = ([cscope] if cscope in ("raw", "checked", "refined")
                 else ["refined", "checked", "raw"])
        cpath = None
        for sc in cands:
            cand = Path("data/chapters") / sc / ("%02d.md" % cn)
            if cand.exists():
                cpath = cand
                break
        if not cpath:
            payload["drift_error"] = "对比章节不存在: 第 %d 章" % cn
            return payload
        ctext = nf_read_text(cpath)
        cfeats = sa.extract_style_features(ctext)
        drift = sa.compute_style_drift(feats, cfeats,
                                      threshold=float(cmp_spec.get("threshold") or 0.3))
        payload["drift"] = drift
        payload["drift_chapter"] = str(cpath)
        payload["drift_report"] = sa.format_drift_report(
            drift, title="与《%s》对比（第 %d 章）" % (Path(label).name, cn))
    return payload


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
        elif p == "/models/fetched":
            # 动态拉取 provider /models 端点 + 缓存到 data/state/fetched_models.json
            import urllib.request as _ur
            cfg, _ = load_all()
            providers = cfg.get("providers", {})
            cache_path = ROOT / "data" / "state" / "fetched_models.json"
            result = {}
            for pid, prov in providers.items():
                base_url = (prov.get("base_url") or "").rstrip("/")
                api_key_env = prov.get("api_key_env", "")
                api_key = os.environ.get(api_key_env, "")
                if not base_url or not api_key:
                    result[pid] = {"models": [], "error": "missing base_url or api_key"}
                    continue
                try:
                    req = _ur.Request(
                        base_url + "/models",
                        headers={"Authorization": "Bearer " + api_key},
                    )
                    with _ur.urlopen(req, timeout=30) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
                    result[pid] = {"models": models, "count": len(models)}
                except Exception as e:
                    result[pid] = {"models": [], "error": str(e)[:200]}
            # 缓存落盘（合并所有 provider 的模型 + 保留手动添加的）
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            # 读取旧缓存中手动添加的模型
            manual_models = set()
            if cache_path.exists():
                try:
                    old = json.loads(cache_path.read_text(encoding="utf-8"))
                    manual_models = set(old.get("_manual", []))
                except Exception:
                    pass
            # 合并：动态拉取 + 手动添加
            all_models = set()
            for info in result.values():
                all_models.update(info.get("models", []))
            all_models.update(manual_models)
            cache_payload = result.copy()
            cache_payload["_manual"] = sorted(manual_models)
            cache_path.write_text(json.dumps(cache_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._send(200, {"providers": result, "cache": str(cache_path)})
        elif p == "/models/cache":
            # 读取缓存的模型列表
            cache_path = ROOT / "data" / "state" / "fetched_models.json"
            if not cache_path.exists():
                self._send(200, {"models": []})
            else:
                try:
                    data = json.loads(cache_path.read_text(encoding="utf-8"))
                    # 收集所有 provider 的模型 + 手动添加的
                    all_models = set()
                    for key, info in data.items():
                        if key.startswith("_"):
                            continue
                        if isinstance(info, dict):
                            all_models.update(info.get("models", []))
                    all_models.update(data.get("_manual", []))
                    self._send(200, {"models": sorted(all_models)})
                except Exception as e:
                    self._send(500, {"error": str(e)})
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
        elif p == "/config/project":
            # 读取 config/project.yaml（GUI 启动向导 / 素材空目录提醒都靠它）。
            # 注意：这里以前没有 GET 分支，而 GUI 用的是 GET → 404，导致初始化向导
            # 与「素材为空」提醒永远不触发。读操作必须挂在 do_GET。
            try:
                from utils.project_config import get_project_config
                self._send(200, {"ok": True, "config": get_project_config()})
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
        elif p == "/scraps/list":
            # 原始碎片列表：按簇分组 + 时间轴 + 前瞻备忘（确定性，零 LLM）
            try:
                from utils.scrap_cluster import collect as _scollect
                sdir = scraps_dir_path()
                index, warnings = _scollect(str(sdir))
                stale = True
                idx = ROOT / "data" / "setting" / "scraps_index.json"
                if idx.exists():
                    try:
                        stale = (json.loads(idx.read_text(encoding="utf-8"))
                                 .get("content_fingerprint")
                                 != index["content_fingerprint"])
                    except Exception:
                        stale = True
                rel = str(sdir.relative_to(ROOT)).replace("\\", "/") \
                    if str(sdir).startswith(str(ROOT)) else str(sdir)
                self._send(200, {
                    "ok": True,
                    "dir": rel,
                    "count": index["stats"]["scrap_count"],
                    "stats": index["stats"],
                    "clusters": index["clusters"],
                    "timeline": index["timeline"],
                    "lookaheads": index["lookaheads"],
                    "warnings": warnings + index["warnings"],
                    "index_stale": stale,
                    "index_path": "data/setting/scraps_index.json",
                })
            except Exception as e:
                self._send(500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/scraps/read":
            name = (self._query().get("name") or [""])[0]
            try:
                from utils.materials_manager import MaterialsManager
                mm = MaterialsManager(scraps_dir_path())
                self._send(200, {"ok": True, "name": name, "content": mm.read_content(name)})
            except Exception as e:
                self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/setting/current":
            try:
                setting_path = ROOT / "data" / "setting" / "setting.json"
                if setting_path.exists():
                    self._send(200, {"ok": True, "setting": json.loads(setting_path.read_text(encoding="utf-8"))})
                else:
                    self._send(404, {"error": "setting.json 不存在"})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/setting/appearances":
            # 角色出场统计（只读）：供关系图定节点大小/描边
            try:
                import json as _json
                ap = ROOT / "data" / "state" / "appearances.json"
                if not ap.exists():
                    self._send(200, {"ok": False,
                                     "hint": "先跑 python scripts/appearances.py"
                                             "（或 POST /appearances/refresh）"})
                    return
                data = _json.loads(ap.read_text(encoding="utf-8"))
                alias = {}
                alias_path = ROOT / "data" / "setting" / "alias.json"
                if alias_path.exists():
                    try:
                        alias = _json.loads(alias_path.read_text(encoding="utf-8"))
                    except Exception:
                        alias = {}
                self._send(200, {"ok": True, "alias": alias, **data})
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
                    chapters.append({"file": f.name, "n": int(f.stem), "title": title, "size": f.stat().st_size})
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
                chapter_path = ROOT / "data" / "outline" / "chapters" / f"{int(n):02d}.md"
                if not chapter_path.exists():
                    self._send(404, {"error": f"第 {n} 章大纲不存在"})
                    return
                content = chapter_path.read_text(encoding="utf-8")
                self._send(200, {"ok": True, "n": int(n), "content": content})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/kb/search":
            # 知识库检索（供写作时注入上下文）
            try:
                from utils import kb_index
                q = self._query()
                query = (q.get("q") or [""])[0]
                top = int((q.get("top") or ["5"])[0])
                chars = int((q.get("chars") or ["300"])[0])
                idx = kb_index.load_index()
                if not idx:
                    self._send(200, {"ok": False, "hint": "索引不存在，请先 POST /kb/build"})
                    return
                results = kb_index.search(query, index=idx, top_k=top, vault_path="E:/图书馆/ROSA")
                items = []
                for path, name, snippet, score in results:
                    items.append({"path": path, "name": name, "snippet": snippet[:chars], "score": score})
                self._send(200, {"ok": True, "query": query, "results": items})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/kb/build":
            # 构建知识库索引（后台任务）
            try:
                from utils import kb_index
                idx = kb_index.build_index("E:/图书馆/ROSA", "data/state/kb_index.pkl")
                self._send(200, {"ok": True, "total_files": idx["total_files"], "terms": len(idx["terms"])})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/proofread/report":
            # 校对报告（stage 5.5）。schema 与 review_report 对齐（含 suggested_action），
            # 故 GUI 可直接复用审稿界面组件与决策链路。
            pp = Path("data/outline/proofread_report.json")
            if not pp.exists():
                self._send(404, {"error": "暂无校对报告",
                                 "hint": "先运行校对：POST /proofread/run，"
                                         "或 python scripts/proofread.py"})
            else:
                try:
                    self._send(200, json.loads(nf_read_text(pp)))
                except Exception as e:      # noqa: BLE001
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/estimate":
            # 生成前 token / 费用预估（确定性，零 LLM 调用）。
            # ?stage=4 只估阶段4；?stages=1,2,3 估指定阶段；?no_history=1 强制字符折算
            try:
                q = self._query()
                stage_raw = (q.get("stage") or [""])[0].strip()
                stages_raw = (q.get("stages") or [""])[0].strip()
                stages = None
                if stage_raw:
                    stages = [int(stage_raw)]
                elif stages_raw:
                    stages = [int(x) for x in stages_raw.split(",") if x.strip().isdigit()]
                no_hist = (q.get("no_history") or ["0"])[0].lower() in ("1", "true", "yes")
                self._send(200, estimate_mod.estimate(stages, use_history=not no_hist))
            except Exception as e:      # noqa: BLE001
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/book/pacing":
            # 章节节奏：?source=current 实时算本书（读 data/chapters/*）；
            # 缺省读拆书产物 data/state/book_pacing.json
            try:
                q = self._query()
                src = (q.get("source") or [""])[0].strip()
                if src == "current":
                    scope = (q.get("scope") or [""])[0].strip() or None
                    self._send(200, book_split_mod.analyze_project_chapters(scope))
                    return
                bp = Path("data/state/book_pacing.json")
                if not bp.exists():
                    self._send(404, {"ok": False, "error": "尚无拆书结果",
                                     "hint": "POST /book/split {path} 导入参考书，"
                                             "或用 /book/pacing?source=current 看本书节奏"})
                    return
                self._send(200, json.loads(nf_read_text(bp)))
            except Exception as e:      # noqa: BLE001
                self._send(500, {"ok": False,
                                 "error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/chapters/history":
            # 章节历史版本：?n=3 → 第 3 章的备份列表（新 → 旧）
            try:
                q = self._query()
                raw_n = (q.get("n") or q.get("chapter") or [""])[0]
                if not str(raw_n).strip().isdigit():
                    self._send(400, {"ok": False, "error": "n 必填且为章节号（如 ?n=3）"})
                    return
                n = int(raw_n)
                versions = refine_ch_mod.list_versions(n)
                target = refine_ch_mod._pick_chapter_path(n)
                self._send(200, {
                    "ok": True, "n": n,
                    "current": str(target) if target else None,
                    "history_dir": str(refine_ch_mod.HISTORY_DIR),
                    "count": len(versions),
                    "versions": versions,
                })
            except Exception as e:      # noqa: BLE001
                self._send(500, {"ok": False,
                                 "error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/costs/streaming":
            # 实时花费：当前 run 的累计消耗 + 预算进度（GUI 成本页轮询）
            try:
                self._send(200, build_streaming_cost())
            except Exception as e:      # noqa: BLE001
                self._send(500, {"ok": False,
                                 "error": type(e).__name__ + ": " + str(e)[:200]})
        else:
            self._send(404, {"error": "未知路径 " + p + "（可用: /health /state /models "
                                      "/project/list /stage/{n}/run /stream/{job_id} /jobs/{id} "
                                      "/materials/* /scraps/* /setting/current /outline/chapters/* "
                                      "/kb/search /kb/build /auto_rewrite/run /review/* "
                                      "/proofread/report /estimate /book/pacing "
                                      "/chapters/history /costs/streaming）"})

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
                        if rc == 4:
                            return True, "用户中断（exit=4）"
                        if rc == 3:
                            return True, "exit=3（等待审批）"
                        return (rc == 0), "exit=" + str(rc)

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
                # 先尝试流式停止
                with STREAMERS_LOCK:
                    streamer = STREAMERS.get(job_id)
                if streamer:
                    streamer["stop"].set()
                    self._send(200, {"ok": True, "message": "已发送停止信号: " + job_id})
                    return
                # 非流式：设置全局停止标志
                with STOP_LOCK:
                    STOP_EVENTS[job_id] = threading.Event()
                    STOP_EVENTS[job_id].set()
                self._send(200, {"ok": True, "message": "已设置停止标志: " + job_id + "（当前阶段完成后停止）"})
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
            elif p == "/outline/chapters/save":
                # 结构化编辑器保存单章大纲（验证+备份+写盘）。
                # 注意：这一行 elif 曾经丢失，导致本段代码变成 /outline/restore 分支的
                # 裸尾随代码 —— GUI 的「保存本章大纲」404，而 restore 会二次发送响应
                # 触发 headers already sent。新增分支务必确认 elif 守卫存在。
                try:
                    n = int(body.get("n") or 0)
                    content = body.get("content") or ""
                    if not (1 <= n <= 999):
                        self._send(400, {"error": "n 须为 1-999"})
                        return
                    if not content.strip():
                        self._send(400, {"error": "内容为空"})
                        return
                    if "核心事件" not in content:
                        self._send(400, {"error": "缺少「核心事件」字段"})
                        return
                    if "涉及角色" not in content:
                        self._send(400, {"error": "缺少「涉及角色」字段"})
                        return
                    # 注：shutil / datetime 均已在模块顶层导入（勿在 do_POST 内再写裸 import，
                    # 否则该名会成为整个函数的局部名，遮蔽模块级同名对象 —— 本项目踩过此坑）
                    chapters_dir = ROOT / "data" / "outline" / "chapters"
                    chapters_dir.mkdir(parents=True, exist_ok=True)
                    path = chapters_dir / f"{n:02d}.md"
                    if path.exists():
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        bak = chapters_dir / f"{n:02d}_v{ts}.bak"
                        shutil.copy2(path, bak)
                    path.write_text(content, encoding="utf-8")
                    self._send(200, {"ok": True, "n": n, "file": path.name})
                except Exception as e:
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
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
            elif p == "/setting/save":
                try:
                    data = body.get("setting")
                    if not data or not isinstance(data, dict):
                        self._send(400, {"error": "setting 必须为对象"})
                        return
                    setting_path = ROOT / "data" / "setting" / "setting.json"
                    if setting_path.exists():
                        backup_dir = ROOT / "data" / "setting" / "history"
                        backup_dir.mkdir(parents=True, exist_ok=True)
                        stamp = time.strftime("%Y%m%d_%H%M%S")
                        backup = backup_dir / f"setting_{stamp}.json"
                        backup.write_text(setting_path.read_text(encoding="utf-8"), encoding="utf-8")
                    nf_write_text(str(setting_path), json.dumps(data, ensure_ascii=False, indent=2))
                    self._send(200, {"ok": True})
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
            elif p == "/chapters/restore":
                # 章节回退：body {n, version} → 从 data/chapters/history/ 恢复
                # 确定性文件操作，同 /outline/restore，同步返回。
                raw_n = body.get("n", body.get("chapter"))
                raw_v = body.get("version")
                if not str(raw_n or "").strip().isdigit():
                    self._send(400, {"error": "n 必填且为章节号"})
                    return
                if not str(raw_v or "").strip().isdigit():
                    self._send(400, {"error": "version 必填且为版本号"})
                    return
                n, ver = int(raw_n), int(raw_v)
                ok_r, msg_r = refine_ch_mod.restore_version(n, ver)
                self._send(200 if ok_r else 400, {"ok": ok_r, "message": msg_r})
            elif p == "/snapshot":
                label = str(body.get("label") or "")
                jid, err = start_job("snapshot", act_snapshot(label))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/config/provider":
                # 切换某阶段的 provider
                role = str(body.get("role") or "")
                new_provider = str(body.get("provider") or "")
                if role not in ("default", "architect", "outliner", "writer", "checker", "reviewer", "polisher"):
                    self._send(400, {"error": "role 须为 default/architect/outliner/writer/checker/reviewer/polisher"})
                    return
                if not new_provider:
                    self._send(400, {"error": "provider 必填"})
                    return
                cfg, _ = load_all()
                # 验证 provider 存在
                if new_provider not in cfg.get("providers", {}):
                    self._send(400, {"error": f"provider {new_provider} 不存在于 config/system.yaml"})
                    return
                cfg["model"][role]["provider"] = new_provider
                (ROOT / "config" / "system.yaml").write_text(
                    yaml.dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
                self._send(200, {"ok": True, "role": role, "provider": new_provider})
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
                if not from_decisions:
                    # 服务端无 TTY，交互式决策会挂住线程；GUI 一律走 decisions.json
                    self._send(400, {"error": "API 不支持交互式决策（无 TTY）：请用 "
                                              "decisions_from_file=true（先在审稿页保存决策）"})
                    return
                # 护栏：文件缺失时给出可行动的提示，而不是起一个必然失败的 job
                report_p = Path("data/outline/review_report.json")
                dec_p = Path("data/outline/review_report.decisions.json")
                if not report_p.exists():
                    self._send(400, {"error": "无审查报告 data/outline/review_report.json"
                                              "（先运行审查）"})
                    return
                if not dec_p.exists():
                    self._send(400, {"error": "无决策文件 data/outline/review_report.decisions.json"
                                              "（先在审稿页保存决策）"})
                    return
                try:
                    import batch_refine as br
                    n_dec = len(br.normalize_decisions(
                        json.loads(nf_read_text(dec_p)) if dec_p.stat().st_size else {}))
                except Exception as e:      # noqa: BLE001
                    n_dec = 0
                    print("[nf_api] 决策文件解析失败: " + type(e).__name__ + ": " + str(e)[:120])
                if not n_dec:
                    self._send(400, {"error": "决策文件无有效条目（action 须为 accept/ignore，"
                                              "且带 finding_id）；请在审稿页重新保存决策"})
                    return
                jid, err = start_job("batch_refine", act_batch_refine_run(from_decisions))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/auto_rewrite/run":
                def _int_or_none(v):
                    try:
                        return int(v) if v not in (None, "") else None
                    except (TypeError, ValueError):
                        return None
                chapters = body.get("chapters")
                if isinstance(chapters, str):
                    chapters = [int(x) for x in chapters.split(",") if x.strip().isdigit()]
                elif isinstance(chapters, list):
                    chapters = [int(x) for x in chapters if str(x).isdigit()]
                else:
                    chapters = None
                # 安全默认：未显式传 dry_run=false 时只预演，不改稿
                dry_run = bool(body.get("dry_run", True))
                jid, err = start_job("auto_rewrite", act_auto_rewrite_run(
                    _int_or_none(body.get("threshold")), dry_run,
                    _int_or_none(body.get("max_rounds")), chapters))
                self._send(202 if not err else 409, {"error": err} if err else {"job_id": jid})
            elif p == "/appearances/refresh":
                # 确定性重算，零成本，可放心给按钮
                jid, err = start_job("appearances", act_appearances_refresh())
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
                # 保存用户决策（GUI 提交）：统一规范化为 batch_refine 能直接吃下的标准格式
                dec_path = Path("data/outline/review_report.decisions.json")
                try:
                    import batch_refine as br
                    decisions = br.normalize_decisions(body)
                    dec_path.parent.mkdir(parents=True, exist_ok=True)
                    dec_path.write_text(json.dumps({
                        "decisions": decisions,
                        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as e:      # noqa: BLE001
                    self._send(500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
                    return
                if decisions:
                    self._send(200, {"ok": True, "count": len(decisions), "path": str(dec_path),
                                     "message": "已保存 " + str(len(decisions)) + " 条决策"})
                else:
                    self._send(200, {"ok": True, "count": 0, "path": str(dec_path),
                                     "warning": "未收到有效决策（action 须为 accept/ignore）："
                                                "批量精修前请至少接受一条审查发现"})
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
                    # 真正清掉指纹，否则"素材无变化 → 复用设定集"会让按钮名不副实
                    try:
                        import json as _json
                        from utils.file_io import read_text as _rt, write_text as _wt
                        mp = ROOT / "data" / "setting" / "materials_manifest.json"
                        if mp.exists():
                            _m = _json.loads(_rt(mp))
                            if _m.get("_fingerprint"):
                                _m["_fingerprint"] = None
                                _wt(mp, _json.dumps(_m, ensure_ascii=False, indent=2))
                    except Exception as _e:
                        print("[nf_api] 清指纹失败（不影响重归并）: " + str(_e))
                    jid, err = start_job("remerge", act_run_stage(cfg, 1, 1))
                    if err:
                        self._send(409, {"error": err})
                    else:
                        self._send(202, {"job_id": jid, "message": "已触发素材重归并（阶段1）"})
                except Exception as e:
                    self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/scraps/save":
                # 保存碎片正文（写前自动备份到 materials/original_scraps/_backup/）
                name = str(body.get("name") or "").strip()
                content = body.get("content")
                okn, err = _safe_member(name)
                if not okn:
                    self._send(400, {"ok": False, "error": err})
                    return
                if not isinstance(content, str):
                    self._send(400, {"ok": False, "error": "content 必须为字符串"})
                    return
                try:
                    target = scraps_dir_path() / name
                    if not target.is_file():
                        self._send(404, {"ok": False, "error": "碎片不存在: " + name})
                        return
                    bak = _backup_copy(target, scraps_dir_path() / "_backup")
                    target.write_text(content, encoding="utf-8")
                    self._send(200, {"ok": True, "name": name,
                                     "size": target.stat().st_size, "backup": bak})
                except Exception as e:
                    self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/scraps/delete":
                # 删除碎片：破坏性操作 → 必须 confirm + 先快照
                name = str(body.get("name") or "").strip()
                if not body.get("confirm"):
                    self._send(400, {"ok": False,
                                     "error": "删除碎片属破坏性操作，需传 confirm=true"})
                    return
                okn, err = _safe_member(name)
                if not okn:
                    self._send(400, {"ok": False, "error": err})
                    return
                try:
                    target = scraps_dir_path() / name
                    if not target.is_file():
                        self._send(404, {"ok": False, "error": "碎片不存在: " + name})
                        return
                    snap_label = "before_scrap_delete_" + Path(name).stem[:20]
                    snap_err = None
                    try:
                        snap_mod.snapshot(snap_label)   # 返回快照目录路径
                    except Exception as e:      # noqa: BLE001
                        snap_err = str(e)[:200]
                    bak = _backup_copy(target, scraps_dir_path() / "_backup")
                    target.unlink()
                    self._send(200, {"ok": True, "name": name, "backup": bak,
                                     "snapshot_error": snap_err})
                except Exception as e:
                    self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/scraps/promote":
                # 勾选信息点 → 落盘成 materials/raw/<名>_<类型>.md
                # 由 Python 写盘（模型写入白名单只有 data/**），覆盖前自动备份
                card_name = str(body.get("card_name") or "").strip()
                card_type = str(body.get("card_type") or "").strip()
                points = body.get("points") or []
                if not card_name:
                    self._send(400, {"ok": False, "error": "card_name 必填"})
                    return
                if not isinstance(points, list) or not points:
                    self._send(400, {"ok": False, "error": "points 必须是非空数组"})
                    return
                try:
                    _, proj = load_all()
                    path, bak = promote_to_card(
                        card_name, card_type, points,
                        open_questions=body.get("open_questions") or [],
                        sources=body.get("sources") or [],
                        book_name=(proj.get("book") or {}).get("name", ""),
                        overwrite=body.get("overwrite", True))
                    self._send(200, {"ok": True, "path": path, "backup": bak,
                                     "hint": "已写入素材卡；下次跑 stage1 时进入设定集归并"})
                except Exception as e:
                    self._send(400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/proofread/run":
                # 校对（stage 5.5，确定性 + 可选 LLM）。默认 dry_run=false（确定性部分零成本）
                scope = body.get("scope")
                use_llm = bool(body.get("llm") or body.get("use_llm"))
                dry_run = bool(body.get("dry_run", False))
                if scope not in (None, "", "raw", "checked", "refined"):
                    self._send(400, {"error": "scope 只能是 raw / checked / refined"})
                    return
                report_path = str(body.get("report")
                                  or "data/outline/proofread_report.json")
                jid, err = start_job("proofread", act_proofread_run(
                    scope or None, report_path, use_llm, dry_run))
                self._send(202 if not err else 409,
                           {"error": err} if err else {"job_id": jid, "message": "校对已提交"})
            elif p == "/style/analyze":
                # 文风分析：对范文 / 指定文本 / 指定章节求特征，可选与某章对比求偏差。
                # 「未配置范文」「范文文件缺失」属正常状态（soft），返回 200 让 GUI 展示引导，
                # 只有请求本身不合法（source 非法 / 缺 path / 章号不存在）才 400。
                try:
                    payload = act_style_analyze(body)
                    ok = payload.get("ok") or payload.get("soft")
                    self._send(200 if ok else 400, payload)
                except Exception as e:      # noqa: BLE001
                    self._send(500, {"ok": False,
                                     "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/book/split":
                # 拆书 / 章节节奏：确定性，直接同步返回（大文件有上限保护）
                path = str(body.get("path") or "").strip()
                if not path:
                    self._send(400, {"ok": False, "error": "path 必填（待拆分的文本文件路径）"})
                    return
                try:
                    ok, msg, result = book_split_mod.analyze_file(
                        path, emit=bool(body.get("emit")))
                    if not ok:
                        self._send(400, {"ok": False, "error": msg})
                        return
                    self._send(200, {"ok": True, "message": msg,
                                     "path": "data/state/book_pacing.json",
                                     "result": result})
                except Exception as e:      # noqa: BLE001
                    self._send(500, {"ok": False,
                                     "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/models/add":
                # 手动把一个模型名写进缓存（GUI「手动添加」按钮）。
                # 曾经写在 do_GET 里且用了 body.get —— GET 会 500、POST 会 404。写操作必须走 POST。
                name = str(body.get("name") or "").strip()
                if not name:
                    self._send(400, {"error": "缺少 name 参数"})
                elif "/" in name or "\\" in name or len(name) > 120:
                    self._send(400, {"error": "模型名不合法（不得含路径分隔符，长度 ≤120）"})
                else:
                    cache_path = ROOT / "data" / "state" / "fetched_models.json"
                    manual_models, providers_data = set(), {}
                    if cache_path.exists():
                        try:
                            old = json.loads(nf_read_text(cache_path))
                            manual_models = set(old.get("_manual", []))
                            providers_data = {k: v for k, v in old.items()
                                              if not k.startswith("_")}
                        except Exception:      # noqa: BLE001
                            manual_models, providers_data = set(), {}
                    manual_models.add(name)
                    providers_data["_manual"] = sorted(manual_models)
                    nf_write_text(cache_path,
                                  json.dumps(providers_data, ensure_ascii=False, indent=2))
                    self._send(200, {"ok": True, "added": name,
                                     "manual_count": len(manual_models)})
            elif p == "/models/switch":
                # 切换某角色的模型（写入 config/system.yaml 的 model.<role>.id）
                role = str(body.get("role") or "").strip()
                model_id = str(body.get("model") or "").strip()
                if not role or not model_id:
                    self._send(400, {"ok": False, "error": "role 与 model 均必填"})
                    return
                try:
                    cfg_path = ROOT / "config" / "system.yaml"
                    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                    models = cfg.get("model") or {}
                    if role not in models:
                        self._send(400, {"ok": False, "error":
                                         "未知角色: " + role + "（可用: "
                                         + ", ".join(sorted(models)) + "）"})
                        return
                    old = models[role].get("id")
                    models[role]["id"] = model_id
                    cfg["model"] = models
                    from utils.file_io import write_text as _wt
                    _wt(cfg_path, yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
                    self._send(200, {"ok": True, "role": role, "model": model_id,
                                     "previous": old,
                                     "message": "已切换 " + role + " → " + model_id
                                                + "（下次运行生效）"})
                except Exception as e:      # noqa: BLE001
                    self._send(500, {"ok": False,
                                     "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/export/markdown":
                # Markdown 分卷导出（P3 多平台发布）
                try:
                    import stage8_markdown_export as s8
                    per_vol = int(body.get("per_vol") or 5)
                    book_name = body.get("book_name") or None
                    ok, msg, path = s8.export_markdown(book_name=book_name,
                                                      chapters_per_vol=per_vol)
                    self._send(200 if ok else 400,
                               {"ok": ok, "message": msg, "path": path})
                except Exception as e:      # noqa: BLE001
                    self._send(500, {"ok": False,
                                     "error": type(e).__name__ + ": " + str(e)[:200]})
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
