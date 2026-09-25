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
POST /stage/skip          {stage, confirm:true, reason?} → 人工跳过该阶段（标记 done+已审批，
                          不改动产物；缺失产物在 message 中回报）
GET  /logs/tail           ?lines=200 → nf_api + orchestrator 标准输出尾部（失败排障用）
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
GET  /outline/trend        迭代趋势：每轮指标序列 + 与上一版对比 + 收敛判定（确定性，零 token）
GET  /outline/advise       开工方向建议：按严重度排序的候选方案 + 可执行 next_step（确定性，零 token）
GET  /sandbox/queue        沙盒审核队列：待审条目 + 状态统计 + 孤儿文件（确定性，零 token）
GET  /sandbox/file         ?path=<相对路径> → 只读预览沙盒产物正文（审核前必须看得见内容）
POST /sandbox/review       {path, action: approve|reject|reset, note?} → 变更沙盒审核状态
                           （只改状态库，不写不删沙盒文件，绝不碰 vault）
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
POST /project/init        首次向导：初始化空工作区 + 写 project.yaml 基本信息（书名/类型/章数）
POST /project/create      {name, genre?, chapters?, style_notes?, archive_current?}
                          一键新建项目：归档当前 → 初始化空工作区 → 写 project.yaml
GET  /about               版本 / 运行环境 / 路径（GUI「关于」弹窗数据）
GET  /config/style_notes  book.style_notes 读取（GUI 设置页签）

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

# ---- 把 __main__ 别名为规范模块名 `nf_api` ----
# 为什么必须有这段：以 `python scripts/nf_api.py` 启动时，本文件的模块名是
# `__main__`；而 nf_api_domains/* 里的 `import nf_api as api` 会**再加载一份**，
# 于是进程里同时存在两个 nf_api：__main__（真正在处理请求的那个）与 nf_api
# （域模块看到的那个）。两边各有独立的 JOBS / CURRENT / ROOT 等模块级可变状态。
#
# 后果是**静默的**：从磁盘读文件的端点（/state、/costs/…）看起来一切正常，
# 只有依赖进程内状态的端点会坏 —— 例如 /jobs/{id} 永远 404（JOBS 是空字典），
# 前端轮询后台任务全部超时。这类 bug 只会在真机跑 HTTP 时暴露，
# 纯函数单测（直接 import nf_api）反而全绿，所以必须在源头掐掉。
#
# 惯用做法：把规范模块名指向自己，让后续 `import nf_api` 命中同一个模块对象。
if __name__ == "__main__":
    sys.modules.setdefault("nf_api", sys.modules["__main__"])

import yaml  # noqa: E402

from orchestrator import run as orch_run  # noqa: E402
from utils.progress_manager import ProgressManager  # noqa: E402
from utils.db import RunDB  # noqa: E402
from utils.llm_client import _load_env_file  # noqa: E402
# project.yaml 定向读写（模块级导入：do_GET/do_POST 多个分支共用，禁止在函数内裸 import）
from utils.project_config import get_project_config as pc_get_config  # noqa: E402
from utils.project_config import set_book_fields as pc_set_book  # noqa: E402
from utils.project_config import get_style_notes as pc_get_style_notes  # noqa: E402
import reject as reject_mod  # noqa: E402
import snapshot as snap_mod  # noqa: E402
import switch_book as sb_mod  # noqa: E402
# 审计日志（Agent 调用记录）
from utils.file_io import append_text as nf_append_text  # noqa: E402
# 校对 / 预估 / 拆书：模块级导入（绝不在 do_GET/do_POST 内写裸 import，
# 否则该名会被判定为整个函数的局部名，同函数其它分支一用就 UnboundLocalError）
import proofread as proofread_mod  # noqa: E402
import estimate_tokens as estimate_mod  # noqa: E402
import book_split as book_split_mod  # noqa: E402
import refine_chapter as refine_ch_mod  # noqa: E402
from utils.verify_chapter import is_chapter_complete  # noqa: E402
# 模型准入登记处（白名单纪律的唯一实现点，2026-09-19 审查 S8 修复）
from utils import model_registry  # noqa: E402
# 模块级导入文件读写（勿在 do_GET/do_POST 内写 `import json` 这类裸导入：
# 函数内任意位置出现 `import json` 都会把 json 变成该函数的局部名，
# 导致同一函数内其它分支的 json.xxx 抛 UnboundLocalError —— 2026-09-14 修）
from utils.file_io import read_text as nf_read_text, write_text as nf_write_text  # noqa: E402

# ---- 域模块（P2 拆分）：实现从 nf_api.py 搬到 nf_api_domains/ ----
# 薄转发范式：do_GET/do_POST 的分支体只做 `self._send(*_dom(fn(self)))`，
# 业务实现住在域模块里。域模块通过 `import nf_api as api` 反向取模块级名
# （**不能** `from nf_api import ROOT` —— 那会把 ROOT 拷成死值，
#  导致 `--root` 与测试的临时项目根失效）。
#
# `_dom()` 是容错包装：域模块导入失败（老 workspace 未被 seedWorkspace 刷到
# 新包时）绝不能整站 500 —— 退化为可行动 500 而不是崩在 import 期。
try:
    from nf_api_domains import materials as dom_materials  # noqa: E402
    from nf_api_domains import misc as dom_misc            # noqa: E402
    from nf_api_domains import models as dom_models        # noqa: E402
    from nf_api_domains import outline as dom_outline      # noqa: E402
    from nf_api_domains import post_misc as dom_post_misc  # noqa: E402
    from nf_api_domains import project as dom_project      # noqa: E402
    from nf_api_domains import refine as dom_refine        # noqa: E402
    from nf_api_domains import runtime as dom_runtime      # noqa: E402
    from nf_api_domains import sandbox as dom_sandbox      # noqa: E402
except Exception as _dom_err:                            # noqa: BLE001
    dom_materials = None
    dom_misc = None
    dom_models = None
    dom_outline = None
    dom_post_misc = None
    dom_project = None
    dom_runtime = None
    dom_sandbox = None
    dom_refine = None
    print("[nf_api] 域模块加载失败（端点将返回可行动错误）: " + str(_dom_err))


def _dom(result):
    """把域模块返回的 (status, payload) 展开给 `self._send(*...)`。

    额外支持 STREAM_RESPONSES 哨兵：域模块自行接管响应（如 SSE），
    nf_api 不再调用 _send。
    """
    from nf_api_domains.contract import STREAM_RESPONSES
    status, payload = result
    if payload is STREAM_RESPONSES:
        return (status, {})          # 占位；实际响应已由域模块写完
    return status, payload


def norm_path(h):
    """把请求路径归一化为 **分发用的 p**：去 query、去尾部 `/`、空则 `/`。

    这个函数是「p 是怎么来的」的**唯一定义**：`do_GET` / `do_POST` 开头调它，
    域模块要解析子路径时也调它（域模块里另写一份 `split("?")[0].rstrip("/")`
    会静默漂移——日后归一化规则一改，就只有域模块里的那份是旧的）。
    """
    return h.path.split("?")[0].rstrip("/") or "/"


def _resolve_root():
    """解析 ROOT：优先 NF_ROOT 环境变量 → 打包态自动检测 → 源码树。"""
    env_root = os.environ.get("NF_ROOT")
    if env_root:
        return Path(env_root)
    computed = Path(__file__).resolve().parents[1]
    return computed

ROOT = _resolve_root()

# 不再 chdir：安装态时代码目录不可写；所有路径走绝对路径拼接

# 大纲路径常量（与 refine_outline.py / utils.outline_panel 保持一致）
GLOBAL = "data/outline/global.md"
HISTORY_DIR = "data/outline/history"

# ---- Agent 审计日志 ----
AUDIT_LOG_PATH = "data/state/agent_audit.jsonl"

def _log_audit(path, method="POST", status=200, source="gui", detail=""):
    """记录 API 调用审计日志（Agent 调用时 source=agent）"""
    try:
        import json as _json
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry = json.dumps({
            "ts": ts, "method": method, "path": path,
            "status": status, "source": source, "detail": str(detail)[:120]
        }, ensure_ascii=False)
        nf_append_text(str(Path(ROOT) / AUDIT_LOG_PATH), entry + "\n")
    except Exception:
        pass

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
    from utils.llm_client import make_client
    return make_client(cfg, role)


# ---- Obsidian vault 联动（kb 端点共用） ----

def _kb_vault_path():
    """读取配置的 vault 路径。未配置时为 None（调用方须先过 _vault_ready）。

    延迟导入 obsidian_bridge：它读 config/system.yaml，而 nf_api 的 import 期
    ROOT 可能尚未按 --root 重设（测试用临时项目根），延迟到调用时才解析。
    """
    from obsidian_bridge import get_vault_path, is_vault_configured
    return get_vault_path() if is_vault_configured() else None


def _vault_ready(handler):
    """vault 未配置时回 400 可行动提示并返回 False（调用方直接 return）。"""
    from obsidian_bridge import is_vault_configured
    if is_vault_configured():
        return True
    handler._send(400, {"ok": False, "error":
                        "未配置 Obsidian vault 路径（config/system.yaml 的 "
                        "obsidian.vault_path）。该功能需指向你自己的 Obsidian 知识库目录；"
                        "不使用知识库联动可忽略此端点。"})
    return False


def _wrap_client_for_streaming(client, job_id):
    """包装客户端：将 run_task 重定向到 run_task_stream，通过队列推送 token。
    Hermes 引擎不支持真流式，降级为整块返回。
    支持 pause/resume：通过 streamer["pause"] 事件控制。"""
    orig_run_task = client.run_task
    orig_run_task_stream = getattr(client, "run_task_stream", None)

    streamer = STREAMERS.get(job_id)
    if not streamer:
        return client

    q = streamer["queue"]
    stop = streamer["stop"]

    def wrapped_run_task(*args, **kwargs):
        def on_piece(piece):
            # 检查暂停
            pause = streamer.get("pause")
            if pause and pause.is_set():
                # 等待恢复或停止
                while pause.is_set() and not stop.is_set():
                    time.sleep(0.2)
                if stop.is_set():
                    return
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
                    on_piece(ch)

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
    sys_yaml = ROOT / "config" / "system.yaml"
    proj_yaml = ROOT / "config" / "project.yaml"
    # Retry up to 3 times with delay (handles race with seedWorkspace on slow disks)
    for attempt in range(3):
        try:
            cfg = yaml.safe_load(sys_yaml.read_text(encoding="utf-8"))
            proj = yaml.safe_load(proj_yaml.read_text(encoding="utf-8"))
            return cfg, proj
        except FileNotFoundError:
            if attempt < 2:
                import time
                time.sleep(0.2)
            else:
                raise


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
    if "skipped" in st:
        out["skipped"] = st["skipped"]
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
    db_path = ROOT / "logs" / "runs.db"
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
    try:
        archived = [p.name for p in (ROOT / "data" / "books").iterdir() if p.is_dir()]
    except (FileNotFoundError, OSError):
        archived = []
    return {
        "book": load_all()[1].get("book", {}).get("name", ""),
        "project_dir": str(ROOT),
        "allow_fake": ALLOW_FAKE,
        "has_work": sb_mod.has_work(),          # 新建项目向导用：工作区是否有数据
        "has_progress": (ROOT / "data" / "state" / "progress.json").exists(),  # 冷启动引导用：是否跑过流水线
        "archived": archived,                   # 已归档书列表（新建项目向导展示用）
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

    db_path = ROOT / "logs" / "runs.db"
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

def _rc_to_result(rc):
    """把 orchestrator 退出码翻译成 job 结果元组 (ok, detail)。

    退出码语义（orchestrator 模块 docstring）：
      0=完成 1=阶段失败 2=预算熔断 3=等待审批/审阅 4=用户中断
    3 与 4 都不是「失败」：3 是审批门正常暂停，4 是用户主动停止 → 不标红。
    """
    if rc == 4:
        return True, "用户中断（exit=4）"
    if rc == 3:
        return True, "exit=3（等待审批，属正常门暂停）"
    return (rc == 0), "exit=" + str(rc)


def act_run_stage(cfg, only_stage, from_stage):
    """创建阶段运行动作（非流式路径）。"""
    client = _client_for_env(cfg, "default")

    def _fn():
        return _rc_to_result(orch_run(from_stage=from_stage,
                                      only_stage=only_stage, client=client))
    return _fn


def act_run_stage_streamed(cfg, only_stage, from_stage, preview_job_id,
                           stream_q, stop_ev):
    """创建阶段运行动作（流式路径）。

    与 act_run_stage 共用 _rc_to_result，**唯一差异**是套一层流式客户端包装。
    两条路径的退出码翻译逻辑不再各写一份 —— 这是 2026-09-19 修复 only_stage
    未定义缺陷（S2）时消除的结构性温床：平行分支必然「改一处漏一处」。
    """
    def _fn():
        client = _client_for_env(cfg, "default")
        # 用真实的 job_id 替换占位
        real_job = CURRENT.get("id", preview_job_id)
        with STREAMERS_LOCK:
            STREAMERS.pop(preview_job_id, None)
            STREAMERS[real_job] = {
                "queue": stream_q,
                "stop": stop_ev,
                "text": [],
            }
        client = _wrap_client_for_streaming(client, real_job)
        return _rc_to_result(orch_run(from_stage=from_stage,
                                      only_stage=only_stage, client=client))
    return _fn


# 各阶段「应该有」的产物（/stage/skip 回报缺失项用；目录只判是否为空）
STAGE_ARTIFACTS = {
    1: ["data/setting/setting.json"],
    2: ["data/outline/global.md"],
    3: ["data/outline/chapters"],
    4: ["data/chapters/raw"],
    5: ["data/chapters/checked"],
    6: ["data/chapters/refined"],
    7: ["output"],
}


def missing_artifacts(stage):
    """返回该阶段缺失/为空的产物路径列表（确定性，不读内容）。"""
    missing = []
    for rel in STAGE_ARTIFACTS.get(stage, []):
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
        elif p.is_dir() and not any(p.glob("*")):
            missing.append(rel + "（空目录）")
    return missing


def act_skip_stage(stage, reason=""):
    """人工跳过阶段：只改 progress.json 状态（done + 已审批），不动任何产物文件。

    存在的意义：某阶段因素材/上游问题反复失败时，用户需要能手工放行后续阶段继续跑，
    而不是只能反复重试或整条管线卡死。跳过后仍可用 /reject 打回回到原状态。
    """
    progress = ProgressManager("data/state/progress.json")
    progress.set_stage(stage, "done",
                       skipped=True,
                       skipped_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                       skip_reason=reason or "")
    progress.set_approved(stage, True)
    missing = missing_artifacts(stage)
    msg = "阶段" + str(stage) + " 已标记完成（人工跳过，未生成产物）"
    if missing:
        msg += "；该阶段产物缺失：" + "、".join(missing) + " —— 下游阶段可能因此失败"
    return True, msg


def act_project_create(name, genre=None, chapters=None, style_notes=None,
                       author=None, archive_current=True, force=False):
    """新建项目：归档当前工作区 → 初始化空工作区 → 写入 project.yaml 基本信息。

    这是「一键新建项目」的完整语义（此前必须手工改 project.yaml + 手动归档）。
    护栏：
      · 书名必填、章节数 1-999，非法值直接退回，不落地任何改动
      · 当前工作区有内容且 archive_current=False 时**拒绝执行**（绝不静默清空书稿）
      · 动手前先 snapshot，归档沿用 switch_book.archive（先移后删，失败可回滚）
    返回 (ok, message)。
    """
    name = (name or "").strip()
    if not name:
        return False, "书名不能为空"
    if any(ch in name for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|")):
        return False, "书名不能包含 \\ / : * ? \" < > | 这些字符"
    try:
        n_ch = int(chapters) if chapters not in (None, "") else None
    except (TypeError, ValueError):
        return False, "章节数必须是整数"
    if n_ch is not None and not (1 <= n_ch <= 999):
        return False, "章节数须在 1-999 之间"

    cur = None
    try:
        cur = sb_mod.current_book_name()
    except Exception:
        cur = None
    has_work = False
    try:
        has_work = sb_mod.has_work()
    except Exception:
        has_work = False

    notes = []
    if has_work:
        if not archive_current and not force:
            return False, ("当前工作区还有「%s」的数据。新建项目会清空工作区，"
                           "请先勾选「归档当前项目」，或用「项目」页签手动归档。"
                           % (cur or "未命名"))
        # 破坏性操作前先快照（与 /project/init 同口径），失败只记一笔不阻断
        try:
            snap_mod.snapshot("project_create")
            notes.append("已快照 current")
        except Exception as e:
            notes.append("快照失败（不阻断）: " + str(e)[:60])
        target = cur or name
        ok, msg = sb_mod.archive(target, yes=True, force=True)
        if not ok:
            return False, "归档当前项目失败，已中止（未做任何改动）: " + str(msg)
        notes.append("已归档「%s」" % target)

    ok, msg = sb_mod.init_empty()
    if not ok:
        return False, "初始化空工作区失败: " + str(msg)
    notes.append(msg)

    ok2, msg2 = pc_set_book({
        "name": name,
        "genre": genre if (genre or "").strip() else None,
        "chapters": n_ch,
        "style_notes": style_notes if (style_notes or "").strip() else None,
        "author": author if (author or "").strip() else None,
    })
    if not ok2:
        return False, ("工作区已就绪，但写入 config/project.yaml 失败: " + str(msg2)
                       + "（可手工填写后重试）")
    notes.append(msg2)
    return True, "；".join(notes)


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
    from utils.db import RunDB
    from utils.cost_tracker import CostTracker
    cfg, proj = load_all()
    # 成本记账（与 `act_auto_rewrite_run` 同范式）：不记账的话从控制台迭代
    # 也查不到花了多少钱 —— 那正是「迭代成本」这一维度缺失的原因。
    # 记账初始化失败不阻断精修（记一条日志即可，不能因为记账把活停了）。
    db, cost, run_id = None, None, None
    if not dry_run:
        try:
            budget = cfg.get("budget", {}) or {}
            db = RunDB("logs/runs.db")
            cost = CostTracker(db, limit_yuan=budget.get("limit_yuan", 300),
                               warn_ratio=budget.get("warn_ratio", 0.7))
            run_id = db.start_run(plan_json="outline_refine_api")
        except Exception as e:                              # noqa: BLE001
            print(f"[api] ⚠ 大纲精修记账初始化失败（本轮费用不进账本）: {e}")
            db, cost, run_id = None, None, None
    ok = False
    try:
        ok, msg = refine_outline.run_refine(cfg, proj, feedback or "",
                                            client=_client_for_env(cfg, "default"),
                                            dry_run=dry_run,
                                            db=db, cost=cost, run_id=run_id)
    finally:
        if db is not None and run_id is not None:
            try:
                db.finish_run(run_id, "done" if ok else "failed")
            except Exception as e:                          # noqa: BLE001
                print(f"[api] ⚠ 大纲精修运行记录收尾失败: {e}")
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
            cand = ROOT / "data" / "chapters" / sc / ("%02d.md" % n)
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
            cand = ROOT / "data" / "chapters" / sc / ("%02d.md" % cn)
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
    from utils.file_io import write_text as _wt

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

    def _fn():
        cfg, proj = load_all()
        client = _client_for_env(cfg, "default")
        drafts = []
        for i in range(1, count + 1):
            draft = ROOT / "data" / "outline" / ("global_draft_%d.md" % i)
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
        p = norm_path(self)

        # SSE 流式端点
        if p.startswith("/stream/"):
            job_id = p[len("/stream/"):]
            self._stream_sse(job_id)
            return

        elif p == "/health":
            # 实现已迁至 nf_api_domains.project（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_project.handle_health(self)))
        elif p == "/state":
            # 实现已迁至 nf_api_domains.project（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_project.handle_state(self)))
        elif p == "/costs":
            rows = []
            db_path = ROOT / "logs" / "runs.db"
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
            # 实现已迁至 nf_api_domains.misc（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_misc.handle_costs_summary(self)))
        elif p == "/models":
            # 实现已迁至 nf_api_domains.models（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_models.handle_models(self)))
        elif p == "/models/available":
            # 实现已迁至 nf_api_domains.models（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_models.handle_models_available(self)))
        elif p == "/models/fetched":
            # 实现已迁至 nf_api_domains.models（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_models.handle_models_fetched(self)))
        elif p == "/models/cache":
            # 实现已迁至 nf_api_domains.models（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_models.handle_models_cache(self)))
        elif p == "/env/open":
            # 实现已迁至 nf_api_domains.misc（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_misc.handle_env_open(self)))
        elif p == "/batch_refine/progress":
            # 实现已迁至 nf_api_domains.misc（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_misc.handle_batch_refine_progress(self)))
        elif p == "/outline/drafts":
            # 实现已迁至 nf_api_domains.outline（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_outline.handle_outline_drafts(self)))
        elif p == "/outline/diff":
            # 实现已迁至 nf_api_domains.outline（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_outline.handle_outline_diff(self)))
        elif p == "/outline/history":
            # 实现已迁至 nf_api_domains.outline（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_outline.handle_outline_history(self)))
        elif p == "/outline/structure":
            # 实现已迁至 nf_api_domains.outline（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_outline.handle_outline_structure(self)))
        elif p == "/outline/trend":
            # 迭代趋势 + 上一版对比 + 收敛判断（确定性，零 token）。
            # 与 /outline/diff 互补：diff 是「哪几行变了」，trend 是「每轮有没有实质进展」。
            self._send(*_dom(dom_outline.handle_outline_trend(self)))
        elif p == "/outline/advise":
            # 开工方向建议：候选方案 + 依据 + 可执行 next_step（确定性，零 token）。
            # trend 说「有没有进展」，advise 说「接下来能做什么」。
            self._send(*_dom(dom_outline.handle_outline_advise(self)))
        elif p == "/sandbox/queue":
            # 沙盒产物审核队列（待审/已通过/已驳回 + 孤儿检测）。
            self._send(*_dom(dom_outline.handle_sandbox_queue(self)))
        elif p == "/sandbox/file":
            # 沙盒产物只读预览（审核「通过」前必须能看清真实内容，否则是盲签）。
            self._send(*_dom(dom_sandbox.handle_sandbox_file(self)))
        elif p == "/review/decisions":
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_review_decisions(self)))
        elif p == "/review/report":
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_review_report(self)))
        elif p.startswith("/jobs/"):
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_jobs(self)))
        elif p == "/project/status":
            # 实现已迁至 nf_api_domains.project（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_project.handle_project_status(self)))
        elif p == "/project/list":
            # 实现已迁至 nf_api_domains.project（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_project.handle_project_list(self)))
        elif p == "/prompts/get" or p.startswith("/prompts/get/"):
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_prompts_get(self)))
        elif p == "/prompts/list":
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_prompts_list(self)))
        elif p == "/config/project":
            # 实现已迁至 nf_api_domains.project（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_project.handle_config_project(self)))
        elif p == "/config/style_notes":
            # 实现已迁至 nf_api_domains.project（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_project.handle_config_style_notes(self)))
        elif p == "/config/agent_mode":
            # Agent 模式开关读取（config/system.yaml 的 gates.agent_mode）。
            self._send(*_dom(dom_project.handle_config_agent_mode(self)))
        elif p == "/materials/list":
            # 实现已迁至 nf_api_domains.materials（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_materials.handle_materials_list(self)))
        elif p.startswith("/materials/read/"):
            # 实现已迁至 nf_api_domains.materials（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_materials.handle_materials_read(
                self, unquote(p[len("/materials/read/"):]))))
        elif p == "/scraps/list":
            # 实现已迁至 nf_api_domains.materials（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_materials.handle_scraps_list(self)))
        elif p == "/scraps/read":
            # 实现已迁至 nf_api_domains.materials（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_materials.handle_scraps_read(self)))
        elif p == "/setting/current":
            # 实现已迁至 nf_api_domains.materials（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_materials.handle_setting_current(self)))
        elif p == "/setting/appearances":
            # 实现已迁至 nf_api_domains.materials（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_materials.handle_setting_appearances(self)))
        elif p == "/outline/chapters/list":
            # 实现已迁至 nf_api_domains.outline（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_outline.handle_chapters_list(self)))
        elif p == "/outline/chapters/get":
            # 实现已迁至 nf_api_domains.outline（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_outline.handle_chapters_get(self)))
        elif p == "/kb/search":
            # 知识库检索（供写作时注入上下文）
            # 2026-09-19 修复：原先直接引用 `get_vault_path()`，但该名字**从未导入**
            # → 该端点一旦被调用必抛 NameError（`pyflakes` 首次接入即报出）。
            try:
                from utils import kb_index
                if not _vault_ready(self):
                    return
                q = self._query()
                query = (q.get("q") or [""])[0]
                top = int((q.get("top") or ["5"])[0])
                chars = int((q.get("chars") or ["300"])[0])
                idx = kb_index.load_index()
                if not idx:
                    self._send(200, {"ok": False, "hint": "索引不存在，请先 POST /kb/build"})
                    return
                _vault = str(_kb_vault_path())
                results = kb_index.search(query, index=idx, top_k=top, vault_path=_vault)
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
                if not _vault_ready(self):
                    return
                vault_path = str(_kb_vault_path())
                idx = kb_index.build_index(vault_path, "data/state/kb_index.pkl")
                self._send(200, {"ok": True, "total_files": idx["total_files"], "terms": len(idx["terms"])})
            except Exception as e:
                self._send(500, {"error": type(e).__name__ + ": " + str(e)[:200]})
        elif p == "/proofread/report":
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_proofread_report(self)))
        elif p == "/estimate":
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_estimate(self)))
        elif p == "/book/pacing":
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_book_pacing(self)))
        elif p == "/chapters/history":
            # 实现已迁至 nf_api_domains.runtime（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_runtime.handle_chapters_history(self)))
        elif p == "/chapters/quality":
            # 实现已迁至 nf_api_domains.misc（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_misc.handle_chapters_quality(self)))
        elif p == "/chapters/verify":
            # 实现已迁至 nf_api_domains.misc（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_misc.handle_chapters_verify(self)))
        elif p == "/logs/tail":
            # 实现已迁至 nf_api_domains.misc（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_misc.handle_logs_tail(self)))
        elif p == "/about":
            # 实现已迁至 nf_api_domains.misc（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_misc.handle_about(self)))
        elif p == "/costs/streaming":
            # 实现已迁至 nf_api_domains.misc（P2 拆分）；此处只做转发。
            self._send(*_dom(dom_misc.handle_costs_streaming(self)))
        elif p == "/costs/rates":
            # 定价表读取（GUI 定价编辑器）。
            self._send(*_dom(dom_misc.handle_costs_rates(self)))
        else:
            self._send(404, {"error": "未知路径 " + p + "（可用: /health /state /models "
                                      "/project/list /stage/{n}/run /stream/{job_id} /jobs/{id} "
                                      "/materials/* /scraps/* /setting/current /outline/chapters/* "
                                      "/kb/search /kb/build /auto_rewrite/run /review/* "
                                      "/proofread/report /estimate /book/pacing "
                                      "/chapters/history /chapters/quality /costs/streaming "
                                      "/logs/tail /stage/skip /about /project/create）"})

    # ---- POST ----
    def do_POST(self):
        p = norm_path(self)
        body = self._body()

        # Agent 模式安全守卫：禁止外部 Agent 调用敏感操作
        # 仅当 agent_mode=true 且请求路径在禁止列表中时生效
        # GUI 请求（带 X-Mofang-Source: gui 头）不受限制
        request_source = self.headers.get("X-Mofang-Source", "").strip().lower()
        if request_source != "gui":
            cfg_check, _ = load_all()
            if bool(cfg_check.get("gates", {}).get("agent_mode", False)):
                # Agent 模式下禁止的 POST 操作
                _FORBIDDEN_POST = {
                    "/approve", "/reject",
                    "/project/create", "/project/archive", "/project/restore", "/project/init",
                    "/config/agent_mode",  # Agent 不得自行切换模式
                }
                if p in _FORBIDDEN_POST:
                    _log_audit(p, "POST", 403, source="agent", detail="blocked")
                    self._send(403, {"ok": False,
                                     "error": "Agent 模式下禁止此操作（仅 GUI 可执行）"})
                    return
                # 非禁止操作也记录审计日志
                _log_audit(p, "POST", 0, source="agent", detail="allowed")

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

                    jid, err = start_job(
                        "stage" + rest,
                        act_run_stage_streamed(cfg, only, from_stage,
                                              job_id_preview, stream_q, stop_ev))
                    if err:
                        with STREAMERS_LOCK:
                            STREAMERS.pop(job_id_preview, None)
                        self._send(409, {"error": err})
                        return
                    # 将 preview 关联到真实 job
                    with STREAMERS_LOCK:
                        if jid not in STREAMERS and job_id_preview in STREAMERS:
                            STREAMERS[jid] = STREAMERS.pop(job_id_preview)
                    self._send(202, {"job_id": jid, "stage": only,
                                     "from_stage": from_stage, "stream": True})
                else:
                    jid, err = start_job("stage" + rest,
                                         act_run_stage(cfg, only, from_stage))
                    if err:
                        self._send(409, {"error": err})
                    else:
                        self._send(202, {"job_id": jid, "stage": only,
                                         "from_stage": from_stage})
            elif p == "/costs/rates":
                # 定价表保存（GUI 定价编辑器写入）。
                self._send(*_dom(dom_misc.handle_costs_rates_save(self, body)))
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
            elif p == "/stream/pause":
                job_id = str(body.get("job_id") or "")
                if not job_id:
                    self._send(400, {"error": "job_id 必填"})
                    return
                with STREAMERS_LOCK:
                    streamer = STREAMERS.get(job_id)
                if streamer:
                    if "pause" not in streamer:
                        streamer["pause"] = threading.Event()
                    streamer["pause"].set()
                    self._send(200, {"ok": True, "message": "已暂停流式输出: " + job_id})
                else:
                    self._send(404, {"error": "stream not found: " + job_id})
            elif p == "/stream/resume":
                job_id = str(body.get("job_id") or "")
                if not job_id:
                    self._send(400, {"error": "job_id 必填"})
                    return
                with STREAMERS_LOCK:
                    streamer = STREAMERS.get(job_id)
                if streamer and "pause" in streamer:
                    streamer["pause"].clear()
                    self._send(200, {"ok": True, "message": "已恢复流式输出: " + job_id})
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
            elif p == "/stage/skip":
                # 人工跳过阶段（GUI 错误恢复一级入口用）。必须显式 confirm:true，
                # 避免误点把整条管线放行。
                stage = int(body.get("stage") or 0)
                if not (1 <= stage <= 7):
                    self._send(400, {"error": "stage 须为 1-7"})
                    return
                if not body.get("confirm"):
                    self._send(400, {"error": "缺少 confirm:true（跳过阶段会标记为已完成，需显式确认）"})
                    return
                ok, msg = act_skip_stage(stage, str(body.get("reason") or ""))
                self._send(200 if ok else 400, {"ok": ok, "message": msg,
                                                "missing": missing_artifacts(stage)})
            elif p == "/refine/outline":
                # 实现已迁至 nf_api_domains.refine（P2 拆分）；此处只做转发。
                self._send(*_dom(dom_refine.handle_refine_outline(self, body)))
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
                # 实现已迁至 nf_api_domains.refine（P2 拆分）；此处只做转发。
                self._send(*_dom(dom_refine.handle_refine_outline_node(self, body)))
            elif p == "/refine/outline/undo":
                # 实现已迁至 nf_api_domains.refine（P2 拆分）；此处只做转发。
                self._send(*_dom(dom_refine.handle_refine_outline_undo(self)))
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
            elif p == "/sandbox/review":
                # 沙盒审核动作（通过/驳回/退回）。实现已迁至 nf_api_domains.sandbox。
                # 语义红线：只改 data/state/sandbox_manifest.json 的状态，
                # 不写不删沙盒文件、绝不碰 vault —— 决策权始终在用户手里。
                # 必须把已读的 body 传进去：请求体是一次性流，handler 再读会挂死。
                self._send(*_dom(dom_sandbox.handle_sandbox_review(self, body)))
            elif p == "/config/project":
                try:
                    self._send(200, {"ok": True, "config": pc_get_config()})
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
                # 实现已迁至 nf_api_domains.refine（P2 拆分）；此处只做转发。
                self._send(*_dom(dom_refine.handle_refine_chapter(self, body)))
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
            elif p == "/config/agent_mode":
                # Agent 模式开关设置（仅 GUI 手动切换，Agent 不得调用）。
                self._send(*_dom(dom_project.handle_config_agent_mode_set(self, body)))
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
            elif p == "/project/create":
                # 一键新建项目：归档当前 → 初始化空工作区 → 写 project.yaml
                # 护栏：写前快照（与 /project/init 一致），失败不落地
                ok, msg = act_project_create(
                    body.get("name"), body.get("genre") or body.get("type"),
                    body.get("chapters"), body.get("style_notes"),
                    body.get("author"),
                    archive_current=bool(body.get("archive_current", True)),
                    force=bool(body.get("force")),
                )
                self._send(200 if ok else 400, {"ok": ok, "message": msg}
                           if ok else {"ok": False, "error": msg})
            elif p == "/project/init":
                # 首次启动向导用的初始化：现在也真正写入 project.yaml 的书名/类型/章数
                # （此前只 init_empty，向导填的信息被静默丢弃 → 用户以为存了其实没存）
                try:
                    from snapshot import snapshot as _snap
                    _snap("init_empty")
                except Exception:
                    pass
                if str(body.get("name") or "").strip():
                    ok, msg = act_project_create(
                        body.get("name"), body.get("genre") or body.get("type"),
                        body.get("chapters"), body.get("style_notes"),
                        body.get("author"),
                        archive_current=bool(body.get("archive_current", True)),
                        force=True,
                    )
                else:
                    ok, msg = sb_mod.init_empty()
                self._send(200 if ok else 400, {"ok": ok, "message": msg}
                           if ok else {"ok": False, "error": msg})
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
                report_p = ROOT / "data" / "outline" / "review_report.json"
                dec_p = ROOT / "data" / "outline" / "review_report.decisions.json"
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
            elif p == "/review/comment":
                # 给指定 finding 添加评论（GUI 审稿面板用；原先误挂在 do_GET 里，
                # do_GET 没有 body → 必然 500，2026-09-15 移到 do_POST）
                try:
                    import chapter_review as cr
                    chapter_no = int(body.get("chapter"))
                    finding_id = str(body.get("finding_id", "")).strip()
                    comment = str(body.get("comment", "")).strip()
                    user = str(body.get("user", "暮雨")).strip() or "暮雨"
                    if not finding_id or not comment:
                        self._send(400, {"ok": False, "error": "finding_id 和 comment 必填"})
                        return
                    ok = cr.add_comment_to_finding("data/outline/review_report.json",
                                                   chapter_no, finding_id, comment, user)
                    if ok:
                        self._send(200, {"ok": True, "message": "评论已添加"})
                    else:
                        self._send(404, {"ok": False, "error": "未找到指定的 finding"})
                except Exception as e:
                    self._send(500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]})
            elif p == "/review/decisions" and self.command == "POST":
                # 保存用户决策（GUI 提交）：统一规范化为 batch_refine 能直接吃下的标准格式
                dec_path = ROOT / "data" / "outline" / "review_report.decisions.json"
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
                # 实现已迁至 nf_api_domains.models（P2 拆分）；此处只做转发。
                self._send(*_dom(dom_models.handle_models_add(self, body)))
            elif p == "/models/switch":
                # 实现已迁至 nf_api_domains.models（P2 拆分）；此处只做转发。
                self._send(*_dom(dom_models.handle_models_switch(self, body)))
            elif p == "/export/markdown":
                # 实现已迁至 nf_api_domains.post_misc（P2 拆分）；此处只做转发。
                self._send(*_dom(dom_post_misc.handle_export_markdown(self, body)))
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
    parser.add_argument("--root", default=os.environ.get("NF_ROOT"),
                        help="数据根目录（覆盖 NF_ROOT）")
    parser.add_argument("--allow-fake", action="store_true",
                        help="测试模式：stage/refine 用 FakeClient（无 LLM）")
    args = parser.parse_args()
    if args.root:
        os.environ["NF_ROOT"] = str(args.root)
        global ROOT
        ROOT = Path(args.root)
    if args.allow_fake:
        os.environ["NF_API_ALLOW_FAKE"] = "1"
        global ALLOW_FAKE
        ALLOW_FAKE = True
    # 日志轮转（config.system.yaml → logging.rotate_days，此前是死配置）。
    # 必须在服务启动前跑：Electron 以 append 模式无界追加 nf_api 的 stdout，
    # 长跑会把日志撑到几百 MB。轮转失败绝不阻断启动（run_log 内部已兜底）。
    try:
        from utils.run_log import rotate_if_needed, load_logging_cfg
        # 复用 load_all()：它已带 seedWorkspace 竞态重试，比裸读更稳
        _cfg, _ = load_all()
        _lvl, _days = load_logging_cfg(_cfg)
        _rr = rotate_if_needed(_cfg)
        print(f"[nf_api] 日志: level={_lvl} rotate_days={_days} → {_rr['reason']}")
    except Exception as _e:                                 # noqa: BLE001
        print(f"[nf_api] 日志轮转检查跳过（不影响服务）: {_e}")
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print("[nf_api] NovelForge API 服务 → http://" + args.host + ":" + str(args.port)
          + "  (allow_fake=" + str(ALLOW_FAKE) + ")")
    # ---- MCP Server (P3: MCP agent layer) ----
    mcp_started = False
    try:
        from nf_mcp import MCPServer as _MCPServer
        mcp_port = int(os.environ.get("NF_MCP_PORT", "8766"))
        mcp_srv = _MCPServer(host=args.host, port=mcp_port,
                              http_host=args.host, http_port=args.port)
        mcp_srv.start()
        mcp_started = True
        print(f"[nf_api] MCP 服务 → tcp://{args.host}:{mcp_port}")
    except Exception as _e:                                 # noqa: BLE001
        print(f"[nf_api] MCP 服务启动跳过（不影响 HTTP）: {_e}")
    # ---- 端点列表 ----
    print("[nf_api] 端点: /health /state /about /models /stage/{n}/run /stage/skip /logs/tail /stream/{job_id} /stop /jobs/{id} /approve /reject /refine/* /outline/* /snapshot /costs /prompts/* /config/style_notes /materials/* /project/*")
    if mcp_started:
        print(f"[nf_api] MCP 工具: {', '.join(t['name'] for t in __import__('nf_mcp').MCP_TOOLS)}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    if mcp_started:
        mcp_srv.stop()


if __name__ == "__main__":
    main()
