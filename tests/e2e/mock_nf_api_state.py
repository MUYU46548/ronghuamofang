# -*- coding: utf-8 -*-
"""UX 验收用的假后端：给前端喂确定性的 /state（含失败阶段、跳过标记、待重写章节）。

**这是正式测试资产**（`tests/e2e/e2e_ux_verify.py` 的两个后端之一），必须进 Git ——
否则干净克隆上跑不了这个\u300c发版前必跑\u300d的视觉验收闸门。
（2026-09-29 之前它只躺在 `Temp/mock_nf_api_state.py`，而 `Temp/` 被 .gitignore 忽略，
  AGENTS.md 却早已把路径写成 `tests/e2e/` —— 文档与现实不一致，闸门实际不可复现。）

用法（另需 8091 静态服务 + 8799 真后端，见 e2e_ux_verify.py 的文件头）：
    python tests/e2e/mock_nf_api_state.py --port 8798
    python tests/e2e/mock_nf_api_state.py --port 8797 --cold   # 冷启动状态
"""
import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

# Agent 模式开关的 mock 回显（权限面板会显示当前开关状态）
AGENT_MODE = True

# ---- Agent 权限面板：名单直接消费后端真源，不再手打 ----
# 为什么这么做：此前 mock 手列 6 条 forbidden，而真源是 20 条 —— 就地校验当场
# 抓到“界面承诺 /project/restore 不许 Agent 碰，后端名单却查无此项”的真漂移。
# 手打 mock 必然与真源分叉（改一处漏一处），所以这里从 utils.agent_guard 取全量，
# 人话表（AGENT_WHY）只负责**展示文案**，缺项给通用兜底。真源加条目 → mock 自动跟上。
from utils.agent_guard import FORBIDDEN_IN_AGENT_MODE  # noqa: E402

# 展示文案：只列需要解释"为什么归用户"的条目；真源新增条目走下方通用兜底，
# 不会因为这里漏写就消失（消失才是事故，文案平庸不是）。
AGENT_WHY = {
    "/approve": "审批放行 —— 判定权归用户，Agent 不得代批",
    "/reject": "打回重跑 —— 同审批族，会清掉后面阶段生成的文件",
    "/project/create": "新建项目 —— 会归档当前项目并清空工作区",
    "/project/archive": "归档当前项目 —— 搬空工作区到书库",
    "/project/restore": "恢复归档书 —— 覆盖当前工作区",
    "/project/init": "首启初始化 —— 写入工作区的基础文件",
    "/project/archive/delete": "删除归档 —— 抹掉成书数据（进回收站）",
    "/config/agent_mode": "Agent 不得开关自己的模式",
    "/config/token_limit": "花超就停的上限 —— Agent 不得抬高它",
    "/factory/clean": "清理出厂样例 —— 移动用户素材文件",
    "/factory/preset/apply": "铺设出厂预设 —— 快照+归档+铺素材，同新建项目",
    "/factory/project/reset": "出厂项目还原 —— 清书状态+重铺素材",
    "/stage/skip": "跳过阶段 —— 会把该阶段标成已审批（写进 progress.json）",
    "/import/docx": "导入整本 Word 稿 —— 批量覆盖章节文件",
    "/outline/segments/set": "分段审批标记 —— 勾一段等于替用户读一段",
    "/outline/discussion/review": "讨论提案审批 —— 会写回章大纲",
    "/plan/select": "规划拍板四态 —— AI 可提案，拍板归用户",
    "/plan/write": "规划拍板四态 —— 同上",
    "/plan/drop": "规划拍板四态 —— 同上",
    "/plan/revive": "规划拍板四态 —— 同上",
}


def agent_forbidden():
    """[{path, why}]：全量取自真源，文案查 AGENT_WHY，缺项兜底。"""
    return [{"path": p, "why": AGENT_WHY.get(p, "用户拍板类操作 —— 判定权归用户")}
            for p in sorted(FORBIDDEN_IN_AGENT_MODE)]


# ---- 设定引用过期（M10）：确定性喂“两条待核 + 一条新引用” ----
# review / link 都会改这份状态，好让 e2e 能验到"点完之后界面确实变了"。
STALE_STATE = {"linked": False, "reviewed": []}


def stale_payload():
    """GET /setting/stale 的 mock 回包（形状与 utils/setting_stale.status 一致）。"""
    items = []
    if "周砚清" not in STALE_STATE["reviewed"]:
        items.append({
            "kind": "characters", "group": "characters", "label": "角色",
            "name": "周砚清", "state": "stale", "excerpt": "青石站巡道工，认刻度不认人",
            "sources": [{
                "chapter": 3, "state": "changed",
                "recorded_version": "aa11bb22cc33", "recorded_layer": "refined",
                "current_version": "dd44ee55ff66", "current_layer": "refined",
                "mentioned_now": True, "mtime": 1793766000.0,
                "reason": "第 3 章的正文改过（润色稿）",
            }],
            "new_chapters": [],
        })
    items.append({
        "kind": "world.locations", "group": "locations", "label": "世界观",
        "name": "青石站", "state": "stale", "excerpt": "两站台，二站台已封闭",
        "sources": [{
            "chapter": 7, "state": "missing",
            "recorded_version": "0123456789ab", "recorded_layer": "checked",
            "current_version": "", "current_layer": "",
            "mentioned_now": False, "reason": "第 7 章已不存在",
        }],
        "new_chapters": [],
    })
    if not STALE_STATE["linked"]:
        items.append({
            "kind": "world.items", "group": "items", "label": "世界观",
            "name": "停了的怀表", "state": "new", "excerpt": "停在 03:20 的怀表",
            "sources": [], "new_chapters": [5],
        })
    stale = sum(1 for i in items if i["state"] == "stale")
    new = sum(1 for i in items if i["state"] == "new")
    return {
        "ok": True, "checked": True, "reason": "", "chapters": 12,
        "setting_path": "data/setting/setting.json", "setting_mtime": 1793766100.0,
        "items": items,
        "counts": {"entries": 26, "quoted": 9, "stale": stale, "new": new,
                   "ok": 9 - stale, "absent": 16, "unlocatable": 1, "unnamed": 0},
        "hint": ("有 %d 条设定的引用章改过了：逐条看一遍，确认还成立就点“已核对”；"
                 "不成立的去改设定集本身。" % stale) if stale else "引用全部对得上当前正文。",
    }

# 设定集 mock：**必须用真实字段结构**，否则这两处修复等于没验：
#   1. 角色真实字段是 traits[]/relations[]/experience，不是 snippet；
#   2. 世界观众是 locations/items 等**分组数组**，真书的 world 下**没有 all 键** ——
#      旧前端读 world.all 会恒显示“暂无条目”（用户截图里那本 2 地点 3 物品全看不见）。
MOCK_SETTING = {
    "characters": [
        {
            "id": "c_zhou_yanqing", "name": "周砚清",
            "role": "青石站巡道工（主角线索人物）",
            "traits": ["不多问也不多答", "认刻度不认人", "对\u300c补写\u300d有执念"],
            "relations": [
                {"target": "韩拙", "type": "上下级", "note": "知道韩拙有事瞒他，二十年没问"},
                {"target": "林小满", "type": "陌生到同盟", "note": "因同一件旧事站到一边"},
            ],
            "experience": "四十五岁（2018年），工龄十七年。不带手机，带一块停了的表。"
                          "能凭听觉判断轨距偏差（误差半毫米以内）。",
            # records（2026-10-24）：多行证词体，验空行分条不切碎内部换行
            "records": [
                "我到现在也没搞懂，一首诗怎么就能变成安防指令。\n但自从他接手，"
                "青石站再没丢过一卷巡查簿。",
                "那个小姑娘看上去很乖。\n——不愿透露姓名的老站工",
            ],
            "source": "周砚清_角色卡", "status": "adopted", "status_reason": "无分歧",
        },
    ],
    "world": {
        "locations": [
            {"id": "loc_qingshizhan", "name": "青石站", "type": "四等小站",
             "detail": "县城往北十二公里。两站台，一条地下通道，候车室冬天烧煤炉。",
             "locked": True,
             "locked_facts": ["站牌是水泥的", "二站台已封闭", "铁栏杆锈死"],
             "source": "青石站_场景卡", "status": "adopted", "status_reason": "无分歧"},
        ],
        "items": [
            {"id": "item_watch", "name": "停了的怀表", "detail": "事故后被登记为遗失物品。",
             "locked": False, "source": "1998年调车事故_概念卡",
             "status": "adopted", "status_reason": "无分歧"},
        ],
        "factions": [],
    },
    "plot_fragments": [],
    "timeline": [],
}

STATE = {
    "book": "验收用书",
    "form": "source",
    "runtime_python": "C:/fake/venv/Scripts/python.exe",
    "current_job": None,
    "has_work": True,
    "archived": ["旧书", "试稿"],
    "stages": [
        {"stage": 1, "status": "done", "approved": True},
        {"stage": 2, "status": "done", "approved": True},
        {"stage": 3, "status": "failed", "approved": None, "rejected": None},
        {"stage": 4, "status": "done", "approved": True, "skipped": True,
         "needs_rewrite": [2, 5]},
        {"stage": 5, "status": "pending", "approved": None},
        {"stage": 6, "status": "done", "approved": False},
        {"stage": 7, "status": "pending", "approved": None},
    ],
    "cost": {"spent_yuan": 12.3456, "limit_yuan": 50.0, "calls": 42, "estimated_entries": 3},
    # 有效审批门（2026-11-05）：前端只认这个字段；阶段 3 有产物但**没有门**，
    # mock 里保留这个组合，验收时才能看出“阶段 3 不会长出确认按钮”。
    "approval_stages": [2, 6],
    "gates": {"agent_mode": False, "require_approval": [2, 6], "material_autonomy": True},
    "budget": {"paused": False,
               # token 闸门观测面（2026-10-03）：真后端 /state 也带这几个字段，
               # mock 不补的话流水线条上的“token 闸门”一行在视觉验收里恒不渲染。
               "tokens_used": 116599, "tokens_run_id": 7,
               "token_limit": {"enabled": True, "per_request_max_tokens": 50000,
                               "per_request_pause_hermes": False,
                               "max_total_tokens": 10000000, "warn_ratio": 0.7},
               "token_pct": 1.17},
}

ESTIMATE = {
    "totals": {"calls": 12, "tokens_in": 26544, "tokens_out": 58380, "cost_yuan": 0.2717},
    "stages": [{"stage": 5, "name": "逻辑检查", "calls": 12, "tokens_in": 26544,
                "tokens_out": 58380, "cost_yuan": 0.2717, "rate_known": True, "basis": ["历史实测均值"]}],
    "basis_source": "history",
    "budget": {"projected_yuan": 12.62, "limit_yuan": 50.0, "projected_pct": 25.2, "exceeds": False},
    "unknown_rate_models": [],
    "disclaimer": "预估仅供参考。",
}

LOGS = {"ok": True, "exists": True,
        "path": "C:/Users/<user>/AppData/Local/Temp/nf_api_child.log",
        "total_lines": 936,
        "lines": ["[orchestrator] ==== 开始阶段 3 ====",
                  "[orchestrator] 阶段3 结果: 逐章大纲生成失败",
                  "[orchestrator] 阶段3 结果: exit=1",
                  "Traceback (most recent call last):",
                  "  File \"scripts/stage3_outline.py\", line 88, in run",
                  "ValueError: 缺少角色\u300c苏芷\u300d的设定"]}


DISCUSS = {"items": [], "stats": {"pending": 1, "approved": 0, "rejected": 0}}

# 规划四态（2026-11-04，task-20261104-004）：画布视图的验收要有真数据。
# **有状态**：POST select/drop/write/revive 改这里，GET /plan/list 读同一份 ——
# 这样“在画布上点采用 → 卡片真的换列”才是真链路，而不是静态截图。
PLAN_ITEMS = [
    {"id": "idea-a1", "title": "让主角在第三章前拿到旧信", "status": "idea",
     "summary": "把线索前置，避免到第五十章才解释这封信是谁留下的。",
     "source": "ashi", "kind": "proposal", "updated_at": "2026-11-04 13:10", "ref_path": ""},
    {"id": "idea-a2", "title": "把反派动机改成“保全妹妹”", "status": "idea",
     "summary": "现在的动机太单薄；改成亲情动机后，第三卷的对峙才有重量。",
     "source": "hermes", "kind": "proposal", "updated_at": "2026-11-04 13:20", "ref_path": ""},
    {"id": "idea-b1", "title": "第一章结尾加一次夜巡", "status": "selected",
     "summary": "为第三章的失踪案先埋一个目击者。",
     "source": "ashi", "kind": "proposal", "updated_at": "2026-11-04 12:40", "ref_path": ""},
    {"id": "idea-c1", "title": "让师父在第二卷就退场", "status": "written",
     "summary": "已写进第二章结尾。",
     "source": "hermes", "kind": "proposal", "updated_at": "2026-11-03 09:05",
     "ref_path": "data/chapters/raw/02.md"},
    {"id": "idea-d1", "title": "加入一段双线并叙", "status": "dropped",
     "summary": "试写后节奏散了，先放弃。",
     "source": "ashi", "kind": "proposal", "updated_at": "2026-11-02 18:30", "ref_path": ""},
]
PLAN_STATUS = ["idea", "selected", "written", "dropped"]


def plan_payload():
    stats = {k: 0 for k in PLAN_STATUS}
    for it in PLAN_ITEMS:
        stats[it["status"]] = stats.get(it["status"], 0) + 1
    return {"ok": True,
            "items": sorted(PLAN_ITEMS, key=lambda x: x.get("updated_at") or "", reverse=True),
            "stats": stats}


def plan_move(pid, new_status, ref_path=""):
    """按状态机改一条；不在允许来源态 → None（调用方回 400，与真后端同一语义）。"""
    allow = {"selected": ("idea",), "written": ("selected",),
             "dropped": ("idea", "selected"), "idea": ("dropped",)}
    for it in PLAN_ITEMS:
        if it["id"] != pid:
            continue
        if it["status"] not in allow.get(new_status, ()):
            return None
        it["status"] = new_status
        if ref_path:
            it["ref_path"] = ref_path
        it["updated_at"] = "2026-11-04 14:50"
        return it
    return None
# 换行常量：demo 提案文案里拼真实换行用（对齐真后端的多行 markdown）
NL = "\n"
COLD = False   # --cold：全新工作区（所有阶段 pending + has_work=false）→ 冷启动引导

# ---- 章节正文（B7，2026-11-06）----
# 审阅台“一行多少字”只能靠真渲染量出来。假后端没有工作区目录，只能给确定性的中文正文；
# 与 e2e 垫片 `_nfChapterText` 同形（左栏章列表来自 IPC 假桥、中栏正文来自 /chapters/content，
# 两边内容一致才不会让人怀疑“打开的不是同一章”）。
_MOCK_PARA = ("他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。"
              "她停下脚步，侧过脸朝窗外的天色看了一眼，随即收回视线。")


def chapter_text(n):
    """第 n 章的假正文：一个章标题 + 40 个自然段（每段 66 个汉字，足够量行宽）。"""
    return "## 第%d章 验收章\n\n%s" % (n, "\n\n".join([_MOCK_PARA] * 40))
MULTI_STARTED = {}   # 多方案 mock job 的起始时刻（逐版进度按秒推进）

# Word 成品模板面板（件五）的**有状态**当前值：POST 改、GET 读，
# 这样“选模板 → 应用 → 下拉回读新值”在视觉验收里是真链路而不是静态截图。
DEFAULT_WT = "templates/出厂预设模板.docx"
WT = {"current": DEFAULT_WT}

# 词表（B1，2026-11-06）：**有状态** mock —— “保存 → 回读生效档位”必须是真链路。
# 无状态的话界面永远显示“内置 20 条”，保存了也看不出来，视觉验收等于没验。
WL = {"cliche": "", "compliance": ""}
_WL_BUILTIN = 20

# Best-of-N（B2，2026-11-06）：有状态，才能验“三栏并排 → 用这版 → 已采用”是活链路。
CAND = {"applied": ""}
BON = {"value": 0}

# 段落批注（B5）：一条正常、一条“原句已改”（漂移）、一条“已失效”（标红）——
# 三种状态同时在场，界面才验证得了“红/黄/正常”分得开。
ANN = {"items": [
    {"id": "a1", "note": "开头要再慢一点", "para_index": 0, "status": "open",
     "created_at": "2026-11-06 10:00", "quote": "他推门进来的时候", "drift": False,
     "current_text": "他推门进来的时候，屋檐上的水线正连成了串。"},
    {"id": "a2", "note": "这段收尾太急", "para_index": 2, "status": "open",
     "created_at": "2026-11-06 10:05", "quote": "她停下脚步", "drift": True,
     "why": "段落位置变了（从第 3 段移到第 4 段）",
     "current_text": "她停下脚步，侧过脸朝窗外的天色看了一眼。"},
    {"id": "a3", "note": "这句话我删了，别再提", "para_index": 5, "status": "missing",
     "created_at": "2026-11-06 10:09", "quote": "早已删掉的一句", "drift": False,
     "why": "引用的原句在这一层里找不到（已被改写或删除）", "current_text": ""},
]}


def _cand_text(tag, chars):
    para = ("他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。"
            "她停下脚步，侧过脸朝窗外的天色看了一眼，随即收回视线。")
    body = "\n\n".join([para] * max(1, chars // 66))
    return "## 第1章 验收章（%s）\n\n%s" % (tag, body)


def candidates_payload(n):
    if int(n) != 1:
        return {"ok": True, "exists": False, "n": int(n), "candidates": [],
                "note": "第 %s 章还没做过“多写几版再挑”" % n}
    return {
        "ok": True, "exists": True, "n": 1, "chosen": "cand2", "chosen_score": 84.5,
        "target_words": [4000, 7200],
        "applied_by_author": CAND["applied"],
        "candidates": [
            {"id": "cand1", "status": "ok", "score": 71.0, "words": 3180,
             "reasons": ["字数低于区间 4,000 汉字（3,180 汉字，差 20.5%）",
                         "病灶词 6 次（1.9 次/千字）", "复读偏高（12-gram 重复覆盖 18.0%，上限 35%）"],
             "text": _cand_text("cand1", 3180)},
            {"id": "cand2", "status": "ok", "score": 84.5, "words": 4260,
             "reasons": ["字数在区间内（4,260 汉字，区间 4,000–7,200）",
                         "没命中病灶词", "平均段长偏短（62 汉字/段，舒适区 80–250）"],
             "text": _cand_text("cand2", 4260)},
            {"id": "cand3", "status": "ok", "score": 66.5, "words": 5240,
             "reasons": ["字数在区间内（5,240 汉字，区间 4,000–7,200）",
                         "病灶词 14 次（2.7 次/千字）", "离目标中值（5,600 汉字）6%"],
             "text": _cand_text("cand3", 5240)},
        ],
    }


def wordlists_payload():
    cl_terms = len([l for l in WL["cliche"].splitlines()
                    if l.strip() and not l.strip().startswith("#")])
    co_terms = len([l for l in WL["compliance"].splitlines()
                    if l.strip() and not l.strip().startswith("#")])
    if cl_terms:
        cl_src, cl_label, cl_warn = "gui", "界面词表", ""
    else:
        cl_src, cl_label = "builtin", "内置 20 条"
        cl_warn = "已回落内置 %d 条病灶词" % _WL_BUILTIN
    return {
        "ok": True,
        "cliche": {"text": WL["cliche"], "path": "data/wordlists/cliche.txt",
                   "exists": True, "terms": cl_terms or _WL_BUILTIN,
                   "source": cl_src, "source_label": cl_label, "warning": cl_warn,
                   "error": ""},
        "compliance": {"text": WL["compliance"], "path": "data/wordlists/compliance.txt",
                       "exists": True, "terms": co_terms,
                       "source": "gui" if co_terms else "unconfigured",
                       "source_label": "界面词表" if co_terms else "未配置（不检查）",
                       "warning": "", "error": ""},
        "format_note": "一行一条；# 开头是注释；regex: 前缀表示正则。",
        "limits": {"max_chars": 200000, "max_line_chars": 200},
    }

# 知识库扫描面（2026-10-24）：**有状态** mock —— “勾掉目录 → 保存 → 列表状态变化”
# 必须是真链路。排除态由 exclude 现算（与真后端同构），不写死在条目里，
# 否则保存后界面不变，视觉验收看到的是假绿。
VAULT_DIRS = [
    {"name": "设定集", "files": 12},
    {"name": "参考书架", "files": 5},
    {"name": "草稿箱", "files": 3},
]
VAULT = {"exclude": ["草稿箱"], "defaults": [".obsidian"]}


def vault_scan_payload():
    skip = set(VAULT["defaults"]) | set(VAULT["exclude"])
    dirs = [dict(d, excluded=(d["name"] in skip)) for d in VAULT_DIRS]
    total = sum(d["files"] for d in dirs)
    scanned = sum(d["files"] for d in dirs if not d["excluded"])
    return {
        "ok": True, "configured": True, "vault_path": "C:/fake/知识库",
        "top_dirs": dirs, "root_files": 1,
        "scanned": scanned, "total": total,
        "skip_dirs": sorted(skip),
        "user_skip_dirs": sorted(VAULT["exclude"]),
        "default_skip_dirs": sorted(VAULT["defaults"]),
    }
# 第 3 件（C 兜底定向改写）：候选生成走 job 轮询（与真后端同构）。
# 这里按 job_id 记住候选，/jobs/<id> 才能回 state=ok + result ——
# mock 的 /jobs/ 默认恒回 failed，会让候选流程走失败分支。
FIELD_JOBS = {}
# 第 4 件（选项可视化）：分支样例同样走 job 轮询，理由同 FIELD_JOBS。
SAMPLE_JOBS = {}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        # 前端从静态服务（8091）跨源访问，必须放行 CORS + 预检（与 nf_api._cors 同口径）。
        # ⚠️ Allow-Headers 必须含 `X-Mofang-Source` —— 前端 api() 每次都发这个头，
        # 漏了它预检必失败、所有请求被浏览器拦掉，页面只剩骨架（2026-09-29 实测踩到）。
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Mofang-Source")
        self.send_header("Access-Control-Max-Age", "600")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _query(self):
        """解析 query string 成 {key: [values]}（对齐 nf_api.Handler._query 契约）。

        新端点的 mock 分支若要读 ?status= / ?path= 这类查询参数，用这个方法 ——
        直接调 self._query() 在这里曾经 AttributeError（真后端才有该方法），
        do_GET 中途崩掉 → 浏览器只见 ERR_EMPTY_RESPONSE，前端 console 报错成片。
        """
        from urllib.parse import parse_qs, urlparse
        return parse_qs(urlparse(self.path).query)

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p == "/health":
            return self._send(200, {"ok": True, "current_job": None, "allow_fake": False})
        if p == "/whoami":
            # 服务端认为“你是谁”（2026-11-04）。e2e 只关心形状，固定返回“非 GUI”。
            return self._send(200, {"ok": True, "trusted_gui": False,
                                    "refusal_code": "NOT_GUI",
                                    "refusal_reason": "（mock：e2e 不看文案）",
                                    "source_claim": "", "token_present": False,
                                    "agent_mode": False, "engine": "direct",
                                    "project_root": "<mock>", "note": "mock"})
        if p == "/providers":
            # 只读端点：e2e 只验页面能渲染，不验真实配置。
            return self._send(200, {"ok": True, "items": [], "presets": [],
                                    "active": {}})
        if p == "/state":
            if COLD:
                st = json.loads(json.dumps(STATE))
                st["book"] = ""
                st["has_work"] = False
                st["archived"] = []
                for s in st["stages"]:
                    s.clear()
                st["stages"] = [{"stage": n, "status": "pending", "approved": None} for n in range(1, 8)]
                return self._send(200, st)
            return self._send(200, STATE)
        if p == "/models":
            return self._send(200, {"engine": "direct", "model": {"default": "glm-5.3"},
                                    "providers": ["tokenhub"], "gates": {}})
        if p == "/models/available":
            return self._send(200, {"engine": "direct",
                                    "available_models": ["glm-5.3", "mock-model-b"],
                                    "providers": ["tokenhub"], "has_key": True})
        if p == "/costs":
            return self._send(200, {"entries": []})
        if p == "/costs/summary":
            # totals 必须带真实字段：前端 estimatedRemainingCost 读 totals.cost_yuan，
            # 空 totals 会让它走“数据缺失不显示”分支 —— 那样 NaN 修复等于没被验到。
            return self._send(200, {"by_stage": [], "by_model": [], "totals": {
                "cost_yuan": 3.75, "tokens_in": 128000, "tokens_out": 41000,
                "calls": 26, "run_id": 20261024,
                # 计价口径（F-4）：缺这一段，成本页就只剩一个数字，
                # 看不出“这是按量计价还是订阅流量”
                "billing": {
                    "engine": "direct", "money_metered": True,
                    "note": "engine=direct：按 provider 刊例价估算（未收录单价的模型记 0）。",
                    "io_ratio": 3.1,
                    "io_note": "输入:输出 比值偏大是正常的 —— 每次请求都要重发设定、大纲与前文；这个数不是产出效率。",
                },
            }})
        if p == "/costs/rates":
            # 定价编辑器需要这个端点；空表 = 全部走源码刊例价
            return self._send(200, {"rates": {}, "source": "mock"})
        if p == "/costs/streaming":
            # 形状与真后端一致（2026-11-05）：token 合计 / 占闸门比例 / 单次最大输入 /
            # 上下文来源 —— 少一个，成本页的“用量”视图在验收截图里就是空白。
            return self._send(200, {
                "ok": True, "running": False, "spent_yuan": 12.3456, "limit_yuan": 50.0,
                "pct": 24.7, "warn_ratio": 0.7,
                "tokens_in": 412345, "tokens_out": 98765, "cache_read": 301122,
                "tokens_total": 511110, "max_call_tokens_in": 48211,
                "token_limit": {"enabled": True, "max_total_tokens": 10000000,
                                "per_request_max_tokens": 50000},
                "token_pct": 5.11, "engine": "hermes",
                "context": {
                    "sources": [
                        {"name": "设定集", "path": "data/setting/setting.json", "chars": 43984, "exists": True},
                        {"name": "整体大纲", "path": "data/outline/global.md", "chars": 3030, "exists": True},
                        {"name": "滚动摘要", "path": "data/summaries/rolling.md", "chars": 3531, "exists": True},
                        {"name": "一致性账本", "path": "data/state/story_state.json", "chars": 0, "exists": False},
                    ],
                    "total_chars": 50545,
                    "task_book": {"files": 32, "bytes": 816298},
                    "note": "上面是**盘上原文**字数（不是一次请求的注入量）。",
                },
                "calls": 12, "estimated_calls": 0,
            })
        if p == "/chapters/context":
            # 上下文包（2026-11-05）：审阅台右栏的读数（形状与 /chapters/context 一致）
            return self._send(200, {
                "ok": True, "chapter": int((self._query().get("n") or ["1"])[0]),
                "outline_chars": 455, "hint": "",
                "summary": {
                    "recalled": 6, "candidates": 25, "dropped": 19,
                    "by_dim": {"foreshadow": 5, "character": 1},
                    "rolling_chapters": 4, "rolling_omitted": 0,
                    "chars": 2105,
                    "budget": {"rolling_chars": 2600, "recall_chars": 2400,
                               "rolling_window": 8, "recall_top_k": 6},
                    "allocated": {"rolling": 971, "recall": 1109},
                    "open_foreshadows": 4, "stale_foreshadows": 0,
                },
                "foreshadow": {"total": 12, "open": 4, "stale": 0, "items": [], "note": ""},
                "recalled": [
                    {"dim": "foreshadow", "text": "结局三案：岑砚把自己的声音存进叠景", "why": "命中：岑砚"},
                    {"dim": "character", "chapter": 1, "text": "第1章：岑砚夜里在渡口接到第三只沉木箱", "why": "命中：岑砚"},
                ],
            })
        if p in ("/chapters/layers", "/chapters/content", "/chapters/paragraphs"):
            # 三层正文 / 单层全文 / 段落列表（B7，2026-11-06）：缺这三条时审阅台中栏
            # 恒停在“读取中”或直接报错，**列宽与行宽在 e2e 里根本量不到**。
            try:
                n = int((self._query().get("n") or ["1"])[0])
            except (TypeError, ValueError):
                return self._send(400, {"ok": False, "error": "n 必填且为章节号"})
            if n < 1:
                return self._send(400, {"ok": False, "error": "n 必填且为章节号"})
            text = chapter_text(n)
            words = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
            if p == "/chapters/paragraphs":
                paras = [{"index": i, "text": _MOCK_PARA, "chars": len(_MOCK_PARA)}
                         for i in range(40)]
                return self._send(200, {"ok": True, "chapter": n, "layer": "refined",
                                        "count": len(paras), "paragraphs": paras})
            meta = {"exists": True, "chars": len(text),
                    "bytes": len(text.encode("utf-8")), "words": words,
                    "title": "第%d章 验收章" % n,
                    "mtime": 1762300000.0, "mtime_text": "2026-11-06 10:00"}
            if p == "/chapters/content":
                want = ((self._query().get("layer") or ["refined"])[0] or "refined").strip()
                if want not in ("raw", "checked", "refined"):
                    return self._send(400, {"ok": False,
                                            "error": "layer 须为 raw / checked / refined"})
                body = dict(meta, ok=True, n=n, layer=want, content=text)
                return self._send(200, body)
            layers = {}
            for lay in ("raw", "checked", "refined"):
                layers[lay] = dict(meta, layer=lay,
                                   path="data/chapters/%s/%02d.md" % (lay, n))
            return self._send(200, {
                "ok": True, "n": n, "layers": layers, "current_layer": "refined",
                "last_modified_layer": "refined", "target_words": [4000, 7200],
                "note": "raw=原稿 · checked=逻辑检查后 · refined=润色后（成品层，编辑器默认打开）",
            })
        if p == "/config/project":
            return self._send(200, {"ok": True, "config": {"book": {"name": "验收用书", "chapters": 12}}})
        if p == "/setting/stale":
            # 设定引用过期（M10）：两条待核 + 一条新引用（形状与真后端一致）
            return self._send(200, stale_payload())
        if p == "/plan/list":
            # 规划四态（丙0/丙1）：清单版与画布版读同一份（2026-11-04 画布验收）
            return self._send(200, plan_payload())
        if p == "/writing_rules":
            # 作品写作约束（M12）：有内容（让界面渲染出真文本与字数）
            txt = ("必须：全篇第三人称限知，跟随主视角\n"
                   "禁止：现代科技名词\n"
                   "禁止：解释人物心理（用动作与对白代替）")
            return self._send(200, {"ok": True, "exists": True, "text": txt,
                                    "chars": len(txt), "max_chars": 6000,
                                    "mtime": 1793766000.0,
                                    "path": "data/writing_rules.md",
                                    "note": "作品级写作约束（mock）"})
        if p == "/onboarding/status":
            # 新手序列引导（I-1）：真后端按盘上事实算，mock 按固定剧本喂同一形状。
            # 关键：COLD 下必须只有 project 是 current（序列严格），
            # 非 cold 下模拟 stage6 done 未审批 → gate 抢 current（审批门优先于继续烧 token）。
            steps = []

            def _step(sid, title, how, action, state, evidence=""):
                steps.append({"id": sid, "title": title, "done": state == "done",
                              "state": state, "evidence": evidence, "how": how,
                              "action": action})

            if COLD:
                _step("project", "建项目", "“项目”页签 → ＋ 新建项目；填书名 / 类型 / 章数",
                      {"kind": "new_project"}, "current", "还没有项目")
                _step("materials", "放素材", "“素材”页签 → 放素材卡到 materials/raw/",
                      {"kind": "tab", "tab": "materials"}, "todo", "素材卡 0 张 / 碎片 0 条")
                for sid, title, how, action in (
                        ("model", "配模型", "“设置”→ 模型与引擎", {"kind": "tab", "tab": "settings", "settings_tab": "models"}),
                        ("stage1", "阶段 1 · 素材 → 设定集", "“流水线”→ 执行下一步", {"kind": "tab", "tab": "pipeline", "spot": "flow-run"}),
                        ("stage2", "阶段 2 · 整体大纲", "“大纲”", {"kind": "tab", "tab": "outline"}),
                        ("gate", "审批放行", "“收件箱”→ 通过", {"kind": "tab", "tab": "inbox", "spot": "gate-list"}),
                        ("stage4", "阶段 4 · 逐章写作", "“流水线”", {"kind": "tab", "tab": "pipeline", "spot": "flow-run"}),
                        ("export", "导出成品", "“导出”页签", {"kind": "tab", "tab": "export"})):
                    _step(sid, title, how, action, "todo",
                          "阶段 2 / 6 完成时会在这里停一次（阶段 3 不设门）" if sid == "gate" else "")
                done_n = 0
            else:
                _step("project", "建项目", "“项目”页签 → ＋ 新建项目", {"kind": "new_project"},
                      "done", "书名：验收用书")
                _step("materials", "放素材", "“素材”页签", {"kind": "tab", "tab": "materials"},
                      "done", "素材卡 6 张 / 碎片 2 条")
                _step("model", "配模型", "“设置”→ 模型与引擎",
                      {"kind": "tab", "tab": "settings", "settings_tab": "models"},
                      "done", "direct / glm-5.3")
                _step("stage1", "阶段 1 · 素材 → 设定集", "“流水线”→ 执行下一步",
                      {"kind": "tab", "tab": "pipeline", "spot": "flow-run"}, "done", "设定集已生成")
                _step("stage2", "阶段 2 · 整体大纲", "“大纲”",
                      {"kind": "tab", "tab": "outline"}, "done", "大纲已生成")
                _step("gate", "审批放行", "“收件箱”→ 逐个点“确认放行”；不满意就“打回”并写原因",
                      {"kind": "tab", "tab": "inbox", "spot": "gate-list"},
                      "current", "有 1 处等你审：阶段 6（阶段 3 不设门）")
                _step("stage4", "阶段 4 · 逐章写作", "“流水线”",
                      {"kind": "tab", "tab": "pipeline", "spot": "flow-run"}, "done", "已落盘 12 章")
                _step("export", "导出成品", "“导出”页签", {"kind": "tab", "tab": "export"},
                      "todo", "还没有 Word 成品")
                done_n = 6
            return self._send(200, {"ok": True, "book": "" if COLD else "验收用书",
                                    "steps": steps,
                                    "next": next((s for s in steps if s["state"] == "current"), None),
                                    "progress": {"done": done_n, "total": len(steps)},
                                    "gates": [{"stage": 6}] if not COLD else [],
                                    "has_progress": not COLD, "target_chapters": 12})
        if p == "/config/target_words":
            # 单章字数区间（2026-11-03）：流水线页现在常驻一行“每章字数”，
            # 缺这条就在验收里显示“读取失败”，把新入口误判成坏入口。
            return self._send(200, {"ok": True, "current": [5000, 9000],
                                    "preset": [5000, 9000],
                                    "bounds": {"low": [500, 20000], "high": [500, 30000]}})
        if p == "/config/token_limit":
            # 止烧阈值面板（设置页）：回一份**与出厂预设一致**的数据，
            # 让视觉验收能真读到值，而不是只看到"读取失败"。
            preset = {"enabled": True, "per_request_max_tokens": 50000,
                      "per_request_pause_hermes": False, "max_total_tokens": 10000000,
                      "warn_ratio": 0.7}
            return self._send(200, {
                "ok": True, "current": preset, "preset": preset,
                "bounds": {"per_request_max_tokens": [1000, 500000],
                           "max_total_tokens": [100000, 200000000],
                           "warn_ratio": [0.1, 1.0]},
                "path": "config/system.yaml", "field": "budget.token_limit",
                "note": "mock：engine: hermes 下金额阈值无效；止烧靠 token 上限。",
            })
        if p == "/chapters/quality":
            # 确定性质量数据（验收用）
            return self._send(200, {"ok": True, "total": 12, "chapters": [
                {"n": 1, "quality": 7.5}, {"n": 2, "quality": 4.0},
                {"n": 3, "quality": 8.0}, {"n": 4, "quality": 6.5},
                {"n": 5, "quality": 3.0}, {"n": 6, "quality": 7.0},
                {"n": 7, "quality": 8.5}, {"n": 8, "quality": 5.5},
                {"n": 9, "quality": 7.0}, {"n": 10, "quality": 6.0},
                {"n": 11, "quality": 4.5}, {"n": 12, "quality": 8.0},
            ]})
        if p == "/gate/artifacts":
            # 审批门产物概况（2026-10-24 加，配 utils.stage_artifacts.summarize_artifacts）。
            # 故意喂**混合形态**：stage 6 的产物是目录（润色稿），必须走 kind="dir"
            # 的渲染分支显示“N 篇 · M 字”—— 若 mock 只给文件，验收就永远看不到
            # 目录分支，等于没验（前端正是因目录数不出来才加的这个端点）。
            stage = str((self._query().get("stage") or ["6"])[0])
            table = {
                "1": [{"path": "data/setting/setting.json", "exists": True,
                       "kind": "file", "lines": 214, "files": 0, "chars": 8932}],
                "2": [{"path": "data/outline/global.md", "exists": True,
                       "kind": "file", "lines": 388, "files": 0, "chars": 15204},
                      {"path": "data/outline/review_report.md", "exists": True,
                       "kind": "file", "lines": 96, "files": 0, "chars": 3120}],
                "6": [{"path": "data/chapters/refined", "exists": True,
                       "kind": "dir", "lines": 0, "files": 12, "chars": 148930}],
            }
            if stage not in table:
                return self._send(400, {"ok": False, "error": "stage 须为 1-8"})
            return self._send(200, {"ok": True, "stage": int(stage),
                                    "artifacts": table[stage]})
        if p == "/materials/list":
            return self._send(200, {"count": 3, "items": [], "dir": "materials/raw"})
        if p == "/materials/reconcile":
            # 素材页顶部横幅的数据源（零 token 对账）。mock 环境没有登记表 →
            # no_meta，前端按设计不显示横幅。缺它会让每次切素材页都留一条 404。
            return self._send(200, {"ok": True, "verdict": "no_meta",
                                    "consistent": False,
                                    "summary": "素材目录里没有登记表（索引/清单），无需对账",
                                    "checked_at": "2026-01-01 00:00:00",
                                    "actual": {"posts": {"raw": 0, "scraps": 0},
                                               "dates": {}, "dates_total": 0},
                                    "meta_files": [], "warnings": []})
        if p == "/sandbox/queue":
            return self._send(200, {"ok": True, "pending": [], "items": [], "orphans": []})
        if p == "/estimate":
            return self._send(200, ESTIMATE)
        if p == "/logs/tail":
            return self._send(200, LOGS)
        if p == "/about":
            return self._send(200, {
                "ok": True, "app": "绒花墨坊", "internal_name": "NovelForge",
                "version": "0.1.0-mock", "python": "3.11.16", "platform": "win32",
                "project_root": "C:/mock/NovelForge", "data_dir": "C:/mock/NovelForge/data",
                "books_dir": "C:/mock/NovelForge/data/books", "output_dir": "C:/mock/NovelForge/output",
                "config": {"name": "验收用书", "chapters": 12},
                "repo": "https://github.com/MYU46548/ronghuamofang",
            })
        if p == "/factory/presets":
            # 首跑向导“选预设”（v0.20）：回两套可见预设，让第 0 步有真卡片可点。
            # 与真实端点同构：hidden 用例不出现在清单里。
            return self._send(200, {"ok": True, "presets": [
                {"id": "rail-end", "name": "铁轨尽头", "genre": "悬疑", "chapters": 6,
                 "summary": "巡道工与一块还在走的怀表", "caps": [], "hidden": False},
                {"id": "salt-town", "name": "盐镇纪事", "genre": "奇幻", "chapters": 10,
                 "summary": "mock 第二套预设", "caps": [], "hidden": False},
            ], "note": ""})
        if p == "/project/list":
            return self._send(200, {"current": "验收用书",
                                    "archived": [{"name": "旧书", "display_name": "旧书",
                                                  "items": 6, "size_kb": 320.5,
                                                  "archived_at": "2026-09-14 18:20:00"}]})
        if p == "/factory/list":
            # 出厂内容面板（设置 → 诊断与调试）：回一份“有命中”的确定性数据，
            # 让视觉验收能真读到清单文件数 / 匹配数 / 前 20 条路径，
            # 而不是停在“读取中…”（mock 不给 fixture，面板就恒为空壳）。
            return self._send(200, {
                "ok": True,
                "manifest": {"seed_version": 22, "count": 214,
                             "generated_at": "2026-10-07T09:00:00"},
                "matches": [
                    {"path": "materials/raw/潮神_角色卡.md",
                     "sample_rel": "materials/潮神_角色卡.md", "sha256": "a" * 64},
                    {"path": "materials/raw/回声_角色卡.md",
                     "sample_rel": "materials/回声_角色卡.md", "sha256": "b" * 64},
                ],
                "match_count": 2,
                "sample_root": "C:/mock/NovelForge/examples",
                "sample_root_exists": True,
                # 出厂样例徽标（项目页“当前项目”旁，2026-10-11）：mock 书名与样例书名
                # 刻意相同 → 徽标必须点亮；书名不同的反例由 tests/http 的 HTTP 自检覆盖。
                "current": {"title": "验收用书", "sample_title": "验收用书",
                            "is_sample": True},
            })
        if p == "/config/vault_scan":
            # 知识库扫描面（设置 → 风格与提示词）：目录名取自用户库本身，
            # 排除态现算 —— 保存后必须看到列表变化，否则视觉验收是假绿。
            return self._send(200, vault_scan_payload())

        if p == "/chapters/candidates":
            n = (self._query().get("n") or ["1"])[0]
            return self._send(200, candidates_payload(n))

        if p == "/chapters/annotations":
            # 段落批注（B5）：三种状态同时给（正常 / 位置变了 / 已失效），
            # 界面才验得出“红黄分得开、失效的确实标红”。
            return self._send(200, {"ok": True,
                                    "chapter": int((self._query().get("n") or ["1"])[0]),
                                    "total": len(ANN["items"]),
                                    "open": len([a for a in ANN["items"]
                                                 if a["status"] == "open"]),
                                    "drifted": len([a for a in ANN["items"] if a.get("drift")]),
                                    "missing": len([a for a in ANN["items"]
                                                    if a["status"] == "missing"]),
                                    "annotations": ANN["items"], "relocated": True})

        if p == "/config/best_of_n":
            # Best-of-N 开关（B2）：有状态 —— 改完再读必须看到新值。
            return self._send(200, {"ok": True, "current": BON["value"], "presets": [0, 2, 3, 5],
                                    "bounds": {"min": 0, "max": 5},
                                    "path": "config/system.yaml", "field": "gates.best_of_n",
                                    "note": "0 = 关闭；N = 低分章重写时多写 N 版再挑。"})

        if p == "/config/wordlists":
            # 词表（B1）：有状态回显，保存后“生效档位”必须真的变。
            return self._send(200, wordlists_payload())

        if p == "/config/word_template":
            # Word 成品模板面板（设置 → Word 模板，件五）：确定性清单，
            # 让视觉验收能真读到“下拉有几项 / 当前是哪份 / 默认是哪份”。
            return self._send(200, {
                "ok": True,
                "current": WT["current"],
                "default": DEFAULT_WT,
                "current_exists": True,
                "files": [
                    {"name": "出厂预设模板.docx", "size": 36780,
                     "mtime": "2026-11-07 15:43", "path": "templates/出厂预设模板.docx"},
                    {"name": "备用模板.docx", "size": 14336,
                     "mtime": "2026-10-11 08:00", "path": "templates/备用模板.docx"},
                ],
                "dir": "templates",
                "path": "config/project.yaml", "field": "book.word_template",
            })
        if p == "/outline/discussions":
            # 讨论提案队列（P0-1 落地协议）：mock 喂 1 条待审，让视觉验收能
            # 真点到“读提案 / 批准并写回 / 驳回”三个动作。
            if not DISCUSS["items"]:
                DISCUSS["items"] = [{
                    "path": "data/outline/drafts/ch03_20261018_101010.md",
                    "kind": "outline_discussion", "source": "agent 讨论",
                    "status": "pending",
                    "created_at": "2026-10-18 10:10:10",
                    "updated_at": "2026-10-18 10:10:10", "reviewed_at": "", "note": "",
                }]
            return self._send(200, {
                "ok": True,
                "items": DISCUSS["items"] if str(
                    (self._query().get("status") or ["pending"])[0]) != "pending"
                    else [i for i in DISCUSS["items"] if i["status"] == "pending"],
                "status": str((self._query().get("status") or ["pending"])[0]),
                "stats": DISCUSS["stats"],
            })
        if p == "/agent/permissions":
            # Agent 模式权限面板（2026-10-24）：mock 回一份**与后端真源同形状**的
            # 数据，让视觉验收能真渲染两列 + 运行锁说明，而不是只看到"读取中"。
            return self._send(200, {
                "ok": True,
                "agent_mode_enabled": AGENT_MODE,
                # 名单 = 后端真源全量（agent_forbidden()），不是手打子集
                "forbidden": agent_forbidden(),
                "internal": [
                    {"path": "/stage/{n}/run", "why": "跑阶段 —— 生成类，Agent 可驱动"},
                    {"path": "/outline/save", "why": "保存大纲 —— 落草稿/沙盒，可回退"},
                    {"path": "/refine/*", "why": "AI 精修 —— 改前自动备份 + 可撤销"},
                ],
                "run_locked": [
                    {"what": "全自动 / 流式全自动 / Word 成品 / 快速运行",
                     "why": "运行入口统一锁：Agent 模式下运行由外部 Agent 驱动，"
                            "GUI 再点一次会双跑（两条流水线各烧一倍 token）"},
                    {"what": "审批 / 打回 / 停止 / 跳过",
                     "why": "不受影响 —— 这几件事本来就只归桌面端"},
                ],
                "note": "判据唯一来源 utils.agent_guard.FORBIDDEN_IN_AGENT_MODE；"
                        "本端点只做展示，不改判据。",
            })
        if p == "/setting/current":
            # 喂**真实字段结构**（2026-10-24）：角色用 traits/relations/experience，
            # 世界观用 locations.items 等**分组数组**（真书的 world 下没有 all 键，
            # 旧前端读 world.all 会恒显示"暂无条目"）。空骨架验不到这两个修复。
            return self._send(200, {"ok": True, "path": "data/setting/setting.json",
                                    "setting": MOCK_SETTING})
        if p == "/setting/names":
            # F21：条目名（界面补全）
            return self._send(200, {"ok": True, "exists": True,
                                    "characters": ["主角甲", "主角乙", "沈砚", "程砧"],
                                    "world": ["雾港", "风神城"]})
        if p == "/setting/versions":
            # F21 展示需要：给两版历史，让“版本历史”能说清“从哪版起”
            return self._send(200, {"ok": True,
                                    "live": {"exists": True,
                                             "counts": {"characters": 4, "world": 2}},
                                    "items": [
                                        {"v": 2, "file": "setting_v2.json",
                                         "at": "2026-11-03 10:12", "size": 4096,
                                         "counts": {"characters": 4, "world": 2},
                                         "readable": True},
                                        {"v": 1, "file": "setting_v1.json",
                                         "at": "2026-11-02 18:40", "size": 3900,
                                         "counts": {"characters": 3, "world": 2},
                                         "readable": True},
                                    ]})
        if p == "/setting/diff":
            return self._send(200, {"from": "v2", "to": "当前",
                                    "added": ["沈砚"], "removed": [],
                                    "changed": [], "world": {"added": [], "removed": [], "changed": []},
                                    "counts": {"character": {"added": 1, "removed": 0,
                                                             "changed": 0, "same": 3},
                                               "world": {"added": 0, "removed": 0,
                                                         "changed": 0, "same": 2}}})
        if p == "/setting/provenance":
            nm = str((self._query().get("name") or ["主角甲"])[0])
            return self._send(200, {
                "ok": True, "name": nm, "found": True, "traceable": True,
                "kind": "characters",
                "current": {"fields": {"角色定位": "机关术师", "性格": ["寡言", "手稳"],
                                       "关系": "与主角乙同门", "简介": "边城出身的机关术师",
                                       "锁定": False},
                            "source": "materials/raw/主角甲_角色卡.md", "status": ""},
                "first_seen": {"v": 1, "label": "v1", "at": "2026-11-02 18:40"},
                "field_since": [
                    {"field": "角色定位", "value": "机关术师", "since": "v1", "since_v": 1},
                    {"field": "性格", "value": ["寡言", "手稳"], "since": "v2", "since_v": 2},
                    {"field": "关系", "value": "与主角乙同门", "since": "v1", "since_v": 1},
                    {"field": "简介", "value": "边城出身的机关术师", "since": "v2", "since_v": 2},
                    {"field": "锁定", "value": False, "since": "v1", "since_v": 1},
                ],
                "changed_in": [
                    {"v": 2, "label": "v2", "at": "2026-11-03 10:12",
                     "fields": ["性格", "简介"]},
                ],
                "imports": [{"source": "materials/raw/主角甲_角色卡.md",
                             "note": "设定集条目的 source 字段"}],
                "versions": [{"v": 1, "label": "v1", "at": "2026-11-02 18:40"},
                             {"v": 2, "label": "v2", "at": "2026-11-03 10:12"},
                             {"v": None, "label": "当前", "at": ""}],
                "why": "",
            })
        if p == "/setting/interaction":
            q = self._query()
            a = str((q.get("a") or ["主角甲"])[0])
            b = str((q.get("b") or ["主角乙"])[0])
            return self._send(200, {
                "ok": True, "a": a, "b": b, "found": True, "tone": "亲近",
                "evidence": ["与主角乙是同门师兄弟，称其为“师兄”",
                             "与主角甲同门，遇事挡在前面"],
                "basis": [{"who": a, "text": "与主角乙是同门师兄弟，称其为“师兄”"},
                          {"who": b, "text": "与主角甲同门，遇事挡在前面"}],
                "guide": {
                    "address": "用亲称或省姓（“师兄”），正式场合才连名带姓",
                    "avoid": "全程敬语、生硬的“您”；也避免腻到失真",
                    "writing": ["允许打断、抢话、把话说半句；",
                                "关心落在具体动作（塞干粮、披衣），不宣言；",
                                "吵架也不撤掉称呼的亲近。"],
                },
                "missing": [], "note": "",
            })
        if p == "/remedy":
            # 诊断抽屉（C 组）：运行态 + 全量体检 + 建议。旧版只有建议，
            # 新结构缺一块，界面上就会出现“体检 0/0 通过”的假空。
            return self._send(200, {
                "ok": True,
                "doctor": {
                    "items": [
                        {"name": "全局大纲", "ok": True, "detail": "8,000 字"},
                        {"name": "逐章大纲", "ok": True, "detail": "12 个文件"},
                        {"name": "raw 章节文件", "ok": True, "detail": "12 个文件"},
                        {"name": "checked 章节文件", "ok": True, "detail": "raw 12 / checked 12"},
                        {"name": "refined 章节文件", "ok": False, "detail": "raw 12 / refined 10"},
                    ],
                    "blocking": [], "warnings": ["refined 章节文件"], "checked": 5,
                },
                "runner": {"alive": True, "stale": False, "pid": 4242,
                           "since": "2026-11-03T20:12:00", "stage": 4,
                           "why": "正在跑（pid 4242）"},
                "facts": {"engine": "direct",
                          "python": "C:/fake/venv/Scripts/python.exe",
                          "root": "C:/fake/repo",
                          "logs": "C:/fake/repo/logs/runs.db",
                          "progress": "C:/fake/repo/data/state/progress.json",
                          "progress_exists": True},
                "issues": [{"name": "refined 章节文件",
                            "detail": "raw 12 / refined 10", "blocking": False}],
                "remedies": [{"issue": "refined 章节文件",
                              "action": "重新运行对应阶段",
                              "command": "python scripts/orchestrator.py --from 6"}],
            })
        if p == "/outline/chapters/list":
            # 分章页签（章节蓝图编辑器）的章节清单：喂两章，让视觉验收能真选中
            # 一章再点“讨论此章”（currentN 为空时 openDiscuss 直接 return）。
            return self._send(200, {"ok": True, "chapters": [
                {"n": 1, "title": "相聚风神城", "path": "data/outline/chapters/01.md"},
                {"n": 2, "title": "兵棋展巧思", "path": "data/outline/chapters/02.md"},
            ]})
        if p == "/outline/chapters/get":
            n = str((self._query().get("n") or ["1"])[0])
            demo = ("## 第 " + n + " 章 相聚风神城" + NL + NL +
                    "- 核心事件：" + NL +
                    "  1. 主角甲与主角乙争论出征规模" + NL +
                    "  2. 以旧创案例说服主角乙" + NL +
                    "- 涉及角色：主角甲；主角乙" + NL +
                    "- 功能：转折" + NL +
                    "- 衔接：承接第二章；钩子——远征队异常信号" + NL)
            return self._send(200, {"ok": True, "n": int(n) if n.isdigit() else 1,
                                    "content": demo})
        if p == "/outline/discussion/draft":
            # 提案全文预览：mock 喂一段带待审声明的提案，让视觉验收能真读到
            # 抬头（“这是待审提案”）与正文结构。
            demo = ("# 讨论提案：第3章优化点" + NL + NL +
                    "> **这是待审提案，不是成品。** 批准后优化点会追加进 "
                    "data/outline/chapters/03.md，不改正文。" + NL + NL +
                    "## 理由" + NL + NL + "关键段缺可感知的代价" + NL + NL +
                    "## 提案正文" + NL + NL + "说服力不足，补故人旧创案例" + NL)
            return self._send(200, {
                "ok": True,
                "path": str((self._query().get("path") or [""])[0]),
                "content": demo,
                "chars": len(demo),
            })
        if p == "/outline/drafts":
            # 多方案清单（I-2）：三份可比方案 —— 字数 / 节点数 / 章规划 / 推荐。
            def _nd(txt, lv):
                return {"text": txt, "level": lv}

            demo = [
                {"id": 1, "name": "global_draft_1.md",
                 "summary": {"verdict": "可开工", "ok": 6, "warn": 1, "thin": 1},
                 "expected_chapters": 12, "chars": 5820, "node_count": 6,
                 "plan_count": 12, "created_at": "2026-11-03 11:20", "score": 17,
                 "nodes": [_nd("第1-2章 沈砚在边城营地被程砧找上门", "OK"),
                           _nd("第3-4章 两人前往雾港遗址", "OK"),
                           _nd("第5-6章 遗址深处异常封印", "WARN")],
                 "plan": [_nd("第1章 边城营地", "OK"), _nd("第2章 冲突起", "OK"),
                          _nd("第3章 出发", "THIN")],
                 "acts": [{"name": "起", "text": "边城营地相识"},
                          {"name": "承", "text": "雾港遗址探查"},
                          {"name": "转", "text": "暗卫围杀"},
                          {"name": "合", "text": "追查真相"}]},
                {"id": 2, "name": "global_draft_2.md",
                 "summary": {"verdict": "可开工", "ok": 9, "warn": 0, "thin": 0},
                 "expected_chapters": 14, "chars": 7420, "node_count": 9,
                 "plan_count": 14, "created_at": "2026-11-03 11:26", "score": 27,
                 "nodes": [_nd("第1-2章 沈砚被程砧找上门", "OK"),
                           _nd("第3-5章 雾港遗址封印", "OK"),
                           _nd("第6-8章 暗卫围杀与援手", "OK")],
                 "plan": [_nd("第1章 边城营地", "OK"), _nd("第2章 冲突", "OK"),
                          _nd("第3章 出发", "OK")],
                 "acts": [{"name": "起", "text": "营地相识"},
                          {"name": "承", "text": "遗址探查"},
                          {"name": "转", "text": "围杀与援手"},
                          {"name": "合", "text": "真相初现"}]},
                {"id": 3, "name": "global_draft_3.md",
                 "summary": {"verdict": "需修订", "ok": 3, "warn": 2, "thin": 2},
                 "expected_chapters": 9, "chars": 3100, "node_count": 3,
                 "plan_count": 9, "created_at": "2026-11-03 11:31", "score": 7,
                 "nodes": [_nd("第1-2章 出发", "WARN"),
                           _nd("第3-4章 遗址", "THIN")],
                 "plan": [_nd("第1章 出发", "WARN"), _nd("第2章 遗址", "THIN")],
                 "acts": [{"name": "起", "text": "营地"},
                          {"name": "承", "text": "出发"},
                          {"name": "转", "text": "遗址"},
                          {"name": "合", "text": "归来"}]},
            ]
            return self._send(200, {"drafts": demo,
                                    "files": [d["name"] for d in demo],
                                    "recommended": 2})
        if p.startswith("/jobs/"):
            jid = p.split("/")[-1]
            if jid.startswith("mock_multi"):
                # 逐版进度（I-2）：每 2 秒推进一版，永远 running —— 前端轮询
                # 只能靠 progress 说话（state 全程不变），这正是要验的东西。
                el = time.time() - MULTI_STARTED.get(jid, time.time())
                idx = min(3, int(el // 2) + 1)
                return self._send(200, {
                    "id": jid, "kind": "stage2_multi", "state": "running",
                    "progress": {"phase": "running", "index": idx, "count": 3,
                                 "done": list(range(1, idx)),
                                 "latest": ({"id": idx - 1, "chars": 5000}
                                            if idx > 1 else None),
                                 "message": "正在生成第 %d / 3 版" % idx},
                })
            if jid in FIELD_JOBS:
                return self._send(200, {"id": jid, "kind": "field_refine",
                                        "state": "ok", "result": FIELD_JOBS[jid]})
            if jid in SAMPLE_JOBS:
                return self._send(200, {"id": jid, "kind": "branch_sample",
                                        "state": "ok", "result": SAMPLE_JOBS[jid]})
            return self._send(200, {"id": jid, "kind": "stage3", "state": "failed",
                                    "result": "exit=1"})
        if p == "/outline/discussion/brief":
            # 讨论任务书打包（P0-1 简报投递）：mock 喂一份自包含任务书，
            # 让视觉验收能真点“讨论此章 → 打包并复制”并读到内容。
            n = str((self._query().get("n") or ["3"])[0])
            note = str((self._query().get("note") or [""])[0])
            demo = ("# 讨论任务：第 " + n + " 章" + NL + NL +
                    "- 项目根：C:/mock/NovelForge" + NL +
                    "- 讨论对象：data/outline/chapters/" + n.zfill(2) + ".md（单章大纲）" + NL + NL +
                    "## 用户意见" + NL + NL + (note or "（用户未填具体意见）") + NL + NL +
                    "## 要做的事" + NL + NL +
                    "1. 读上面那份章大纲（相对项目根）。" + NL +
                    "2. 结合下方材料，就用户意见展开**多轮讨论**。" + NL +
                    "3. 讨论收敛后，把结论整理成一条**优化点提案**提交。" + NL + NL +
                    "## 提交方式" + NL + NL +
                    "POST http://127.0.0.1:8765/outline/discussion/apply" + NL + NL +
                    "## 红线" + NL + NL +
                    "- **不要直接写** data/outline/chapters/*.md。" + NL +
                    "- **不要代替用户批准**。" + NL)
            return self._send(200, {
                "ok": True, "message": "已打包第 " + n + " 章讨论任务书",
                "n": int(n) if n.isdigit() else 3,
                "brief": demo, "chars": len(demo),
            })

        return self._send(404, {"ok": False, "error": "mock: 未实现的 " + p})


    def do_POST(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        if p in ("/plan/select", "/plan/drop", "/plan/revive", "/plan/write"):
            # 规划四态写侧（2026-11-04）：允许来源态与真后端同一套 ——
            # 清单与画布点按钮都走这里，“两个视图是否同源”在验收里看得见。
            target = {"/plan/select": "selected", "/plan/drop": "dropped",
                      "/plan/revive": "idea", "/plan/write": "written"}[p]
            pid = str(body.get("id") or "")
            ref = str(body.get("ref_path") or "")
            if target == "written" and not ref:
                return self._send(400, {"ok": False, "error": "写进正文必须给 ref_path"})
            it = plan_move(pid, target, ref)
            if not it:
                return self._send(400, {"ok": False,
                                        "error": "状态不允许：%s → %s（或 id 不存在）"
                                                 % (pid, target)})
            return self._send(200, {"ok": True, "item": it,
                                    "stats": plan_payload()["stats"]})

        if p == "/config/target_words":
            # 保存单章字数区间（2026-11-03）：真实后端会校验区间并落列表，
            # mock 只回显 —— 但必须**回读同一份形状**，否则前端保存后回显空。
            lo = int(body.get("low") or 0)
            hi = int(body.get("high") or 0)
            if not (500 <= lo < hi <= 30000):
                return self._send(400, {"ok": False, "error": "区间不合法"})
            return self._send(200, {"ok": True, "current": [lo, hi],
                                    "message": "已保存：单章 %d–%d 字" % (lo, hi)})

        if p == "/writing_rules":
            txt = str(body.get("text") or "").strip()
            if len(txt) > 6000:
                return self._send(400, {"ok": False, "error": "太长：超过上限 6000 字"})
            if not txt:
                return self._send(200, {"ok": True, "message": "已清空作品写作约束", "cleared": True})
            return self._send(200, {"ok": True, "chars": len(txt),
                                    "message": "已保存（%d 字）" % len(txt)})

        if p == "/setting/stale/link":
            dry = bool(body.get("dry_run"))
            added = [{"kind": "world.items", "group": "items",
                      "name": "停了的怀表", "chapters": [5]}]
            if dry:
                return self._send(200, {"ok": True, "dry_run": True, "chapters": 12,
                                        "entries": 26, "added": added, "added_pairs": 1,
                                        "backup": "",
                                        "message": "预演：会新增 1 条引用登记（未写盘）"})
            STALE_STATE["linked"] = True
            return self._send(200, {"ok": True, "dry_run": False, "chapters": 12,
                                    "entries": 26, "added": added, "added_pairs": 1,
                                    "backup": "data/setting/history/setting_v9.json",
                                    "message": "已登记 1 条引用（涉及 1 个条目）"})

        if p == "/setting/stale/review":
            name = str(body.get("name") or "").strip()
            if not name:
                return self._send(400, {"ok": False, "error": "name 必填（要核对的条目名）"})
            action = str(body.get("action") or "confirm")
            if action == "confirm":
                if name not in STALE_STATE["reviewed"]:
                    STALE_STATE["reviewed"].append(name)
                return self._send(200, {"ok": True, "name": name, "action": "confirm",
                                        "chapters": [3], "skipped": [],
                                        "message": "已核对第 3 章（这条设定不再待核）"})
            return self._send(200, {"ok": True, "name": name, "action": "drop",
                                    "chapters": list(body.get("chapters") or []), "skipped": [],
                                    "message": "已移除该来源登记"})

        if p == "/stage/2/run-multi":
            # 多方案生成（I-2 视觉验收）：返回一个会递进的 mock job。
            jid = "mock_multi_1"
            MULTI_STARTED[jid] = time.time()
            return self._send(202, {"job_id": jid,
                                    "count": int(body.get("count") or 3)})

        if p == "/outline/field/current":
            # 定向改写（C 兜底）读当前字段值：喂一段固定字段，让 GUI 预填可见。
            # 复用 do_POST 开头已解析的 body —— rfile 是一次性流，二次读会挂死
            # （本项目记着的坑：请求体只能读一次）。
            fld = str(body.get("field") or "events")
            demo_val = {
                "events": ("1. 主角甲与主角乙争论出征规模" + NL +
                           "2. 以旧创案例说服主角乙"),
                "roles": "主角甲；主角乙",
                "function": "转折",
                "carryover": "承接第二章",
                "hook": "远征队异常信号",
                "title": "相聚风神城",
            }.get(fld, "（空）")
            return self._send(200, {"ok": True, "chapter": int(body.get("chapter") or 1),
                                    "field": fld, "value": demo_val})

        if p == "/outline/field/candidates":
            # 定向改写候选生成（C 兜底）：喂 3 个方向不同的候选，走 job 轮询。
            jid = "fieldjob_" + str(body.get("chapter") or 1) + "_" + str(body.get("field") or "events")
            FIELD_JOBS[jid] = {
                "field": str(body.get("field") or "events"),
                "candidates": [
                    {"text": "主角乙拿出星图指出航道风险", "why": "用具体物件代替抽象争论"},
                    {"text": "主角甲列举三场败绩说明必要", "why": "用往事支撑论点"},
                    {"text": "两人以兵棋推演验证规模差异", "why": "把争论落到可演算的行动"},
                ],
                "cost_yuan": 0.0035, "model": "mock-model",
                "tokens_in": 1200, "tokens_out": 300,
            }
            return self._send(202, {"job_id": jid})

        if p == "/outline/field/submit":
            # 提交所选 → 落提案。空 text 拒绝（护栏照旧：mock 也验必填）。
            if not str(body.get("text") or "").strip():
                return self._send(400, {"ok": False, "error": "text 必填（选定的候选内容）"})
            return self._send(200, {
                "ok": True,
                "proposal": "data/outline/drafts/ch%02d_mock.md" % int(body.get("chapter") or 1),
                "message": "提案已落盘待审 —— 去\u300c审核\u300d页签批准后才会写回章大纲"})

        if p == "/outline/sample/current":
            # 已选定的方向：mock 固定回一条，让“已选定”区在视觉验收里可见
            ch = int(body.get("chapter") or 1)
            return self._send(200, {"ok": True, "chapter": ch, "samples": [
                {"n": 1, "title": "克制隐忍", "kind": "tone",
                 "body": "主角甲没有立刻回答。他把星图推回去……"}]})

        if p == "/outline/sample/candidates":
            # 分支样例生成（选项可视化）：喂 3 个方向明显不同的样例，走 job 轮询。
            ch = int(body.get("chapter") or 1)
            kind = str(body.get("kind") or "tone")
            jid = "samplejob_%d_%s" % (ch, kind)
            SAMPLE_JOBS[jid] = {
                "kind": kind,
                "samples": [
                    {"label": "克制隐忍",
                     "text": "主角甲没有立刻回答。他把星图推回去，很久才开口："
                             "\u300c规模不是问题，补给才是。\u300d",
                     "why": "适合内敛主角、慢热铺陈的书"},
                    {"label": "外放热烈",
                     "text": "\u300c不行！\u300d主角甲一掌拍在桌上，星图跳了一下，"
                             "\u300c三倍兵力进去，回来的不到一半！\u300d",
                     "why": "适合冲突驱动、节奏明快的书"},
                    {"label": "冷峻疏离",
                     "text": "星图在两人之间发着冷光。主角甲看着它，"
                             "像在看一份与自己无关的报表。",
                     "why": "适合硬科幻、旁观视角的书"},
                ],
                "cost_yuan": 0.0043, "model": "mock-model",
                "tokens_in": 1500, "tokens_out": 900,
            }
            return self._send(202, {"job_id": jid})

        if p == "/outline/sample/submit":
            # 提交所选方向 → 落样例类提案。空 text 拒绝（护栏照旧）。
            if not str(body.get("text") or "").strip():
                return self._send(400, {"ok": False,
                                        "error": "text 必填（选定方向的样例正文）"})
            return self._send(200, {
                "ok": True,
                "proposal": "data/outline/drafts/ch%02d_sample_mock.md"
                            % int(body.get("chapter") or 1),
                "message": "方向样例已落盘待审 —— 去\u300c审核\u300d页签批准后才会进"
                           "第 %d 章的写作参照" % int(body.get("chapter") or 1)})

        if p == "/config/vault_skip_dirs":
            # 扫描排除项：判据与真后端 _validate_skip_dirs 同构 ——
            # 只收目录名，路径穿越一律 400（前端只回显错误，不自己写判据）
            raw = body.get("exclude")
            if not isinstance(raw, list):
                return self._send(400, {"ok": False, "error": "exclude 必须是目录名列表（数组）"})
            bad = [str(s) for s in raw
                   if any(c in str(s) for c in ("/", "\\", ".."))]
            if bad:
                return self._send(400, {"ok": False,
                                        "error": "只接受目录名、不接受路径（收到 %r）" % bad[0]})
            VAULT["exclude"] = sorted({str(s).strip() for s in raw if str(s).strip()})
            return self._send(200, {"ok": True, "exclude": VAULT["exclude"],
                                    "message": "已更新扫描排除项",
                                    "scan": vault_scan_payload(),
                                    "note": "对下一次检索生效。"})

        if p == "/config/best_of_n":
            # Best-of-N 开关：只收 0 / 2~5，与真后端同口径（越界 400，不静默截断）。
            try:
                v = int((body or {}).get("value"))
            except (TypeError, ValueError):
                return self._send(400, {"ok": False, "error": "value 必须是整数（0 或 2~5）"})
            if v != 0 and not (2 <= v <= 5):
                return self._send(400, {"ok": False,
                                        "error": "只能设 0（关）或 2~5 版；%d 超出范围" % v})
            BON["value"] = v
            return self._send(200, {"ok": True, "current": v,
                                    "message": "已设为 %s" % ("关闭" if v == 0 else "%d 版" % v)})

        if p == "/chapters/annotations":
            # 段落批注（B5）写入口：add / resolve / delete（与真后端同三种 action）。
            b = body or {}
            act = str(b.get("action") or "")
            if act == "add":
                note = str(b.get("note") or "").strip()
                if not note:
                    return self._send(400, {"ok": False, "error": "批注内容不能为空"})
                item = {"id": "a%d" % (len(ANN["items"]) + 1), "note": note,
                        "para_index": int(b.get("para_index") or 0), "status": "open",
                        "created_at": "2026-11-06 12:00", "quote": "（新增）", "drift": False,
                        "current_text": ""}
                ANN["items"].append(item)
                return self._send(200, {"ok": True, "annotation": item,
                                        "message": "已记下这条批注"})
            if act == "resolve":
                for a in ANN["items"]:
                    if a["id"] == str(b.get("id")):
                        a["status"] = "resolved"
                        return self._send(200, {"ok": True, "id": a["id"],
                                                "message": "已标记为处理完"})
                return self._send(404, {"ok": False, "error": "没有这条批注"})
            return self._send(400, {"ok": False, "error": "action 须为 add / resolve / delete"})

        if p == "/chapters/candidates/apply":
            # Best-of-N（B2）：采用某一版 —— 有状态，回读要能看出“你在用哪版”。
            cid = str((body or {}).get("id") or "").strip()
            if not cid:
                return self._send(400, {"ok": False, "error": "id 必填（cand1 / cand2 / …）"})
            CAND["applied"] = cid
            return self._send(200, {"ok": True, "n": int((body or {}).get("n") or 1),
                                    "id": cid, "words": 4260,
                                    "message": "已采用 %s（旧稿备份：ch01_v3.md）" % cid})

        if p == "/config/wordlists":
            # 词表（B1）：收 cliche / compliance 两个字符串，未知字段 400（与真后端同口径）。
            unknown = [k for k in body if k not in ("cliche", "compliance")]
            if unknown:
                return self._send(400, {"ok": False,
                                        "error": "未知字段 %s（只接受 cliche / compliance）"
                                                 % "、".join(sorted(unknown))})
            if "cliche" not in body and "compliance" not in body:
                return self._send(400, {"ok": False, "error": "至少要传 cliche 或 compliance"})
            for k in ("cliche", "compliance"):
                if k in body:
                    WL[k] = str(body.get(k) or "")
            payload = wordlists_payload()
            payload["saved"] = [{"label": k, "path": "data/wordlists/%s.txt" % k,
                                 "terms": payload[k]["terms"], "warning": ""}
                                for k in ("cliche", "compliance")]
            payload["message"] = "；".join("%s %d 条" % (k, payload[k]["terms"])
                                          for k in ("cliche", "compliance"))
            return self._send(200, payload)

        if p == "/config/word_template":
            # Word 模板面板（件五）：**有状态** mock —— 选完再读必须能看到新值，
            # 否则“应用 → 下拉回跳”这种真 bug 在视觉验收里测不出来。
            if not body.get("reset") and not str(body.get("template") or "").strip():
                return self._send(400, {"ok": False,
                                        "error": "未选择模板（要回到出厂模板请点\u300c恢复默认\u300d）"})
            WT["current"] = (DEFAULT_WT if body.get("reset")
                             else str(body.get("template")))
            return self._send(200, {"ok": True, "current": WT["current"],
                                    "message": "已更新 word_template（book.word_template，"
                                               "旧版已备份到 config/history/）",
                                    "path": "config/project.yaml",
                                    "field": "book.word_template"})
        if p in ("/export/markdown", "/export/word"):
            # 导出面板分流验收（2026-10-17 复盘修）：确定性回显收到的参数 ——
            # book_name 显式 null / include_frontmatter 未透传这类回归直接在 toast 里露馅。
            kind = "Markdown" if p.endswith("markdown") else "Word"
            name = body.get("book_name") or "夹具书"
            fm = bool(body.get("include_frontmatter"))
            return self._send(200, {
                "ok": True,
                "message": f"{kind} mock 导出 2 章（book_name={name}, frontmatter={fm}）",
                "path": f"output/{name}_{kind.lower()}_mock",
                "chapters": 2})
        if p == "/outline/discussion/review":
            # 讨论提案审批：mock 只改内存状态（有状态 —— 批准后队列要真变化，
            # 否则“批准 → 待审归零”这类闭环在视觉验收里测不出来）。
            action = str(body.get("action") or "")
            rel = str(body.get("path") or "")
            for it in DISCUSS["items"]:
                if it["path"] == rel:
                    if action == "approve":
                        it["status"] = "approved"
                        DISCUSS["stats"]["pending"] = max(
                            0, DISCUSS["stats"]["pending"] - 1)
                        DISCUSS["stats"]["approved"] += 1
                        return self._send(200, {
                            "ok": True,
                            "message": "已写回第 3 章（新增优化点，原稿备份 03_v2026.bak）",
                            "action": "approve", "status": "approved",
                            "stats": DISCUSS["stats"], "items": DISCUSS["items"]})
                    if action == "reject":
                        if not str(body.get("note") or "").strip():
                            return self._send(400, {"ok": False, "error": "驳回必须填原因"})
                        it["status"] = "rejected"
                        it["note"] = str(body.get("note"))
                        DISCUSS["stats"]["pending"] = max(
                            0, DISCUSS["stats"]["pending"] - 1)
                        DISCUSS["stats"]["rejected"] += 1
                        return self._send(200, {
                            "ok": True, "message": "已驳回", "action": "reject",
                            "status": "rejected",
                            "stats": DISCUSS["stats"], "items": DISCUSS["items"]})
            return self._send(400, {"ok": False, "error": "提案不存在: " + rel})
        if p == "/stage/skip":
            return self._send(200, {"ok": True,
                                    "message": "阶段%d 已标记完成（人工跳过，未生成产物）" % body.get("stage", 0),
                                    "missing": ["data/outline/chapters"]})
        if p == "/factory/preset/apply":
            # 向导第 0 步“铺设”：真端点会快照→归档→铺素材→写配置；mock 只回
            # 确定性成功，让视觉验收能走到“已铺设 → 第 2 步不再要求勾归档”这条链。
            if not body.get("id"):
                return self._send(400, {"ok": False, "error": "缺少 id"})
            return self._send(200, {"ok": True,
                                    "message": "预设\u300c%s\u300d已铺设" % body.get("id"),
                                    "log": "· 已快照\n· 已归档\n· 素材 6 张已铺入"})
        if p == "/project/create":
            # preset_applied=true（向导第 0 步已铺设预设）→ 真后端会跳过归档/初始化、
            # 保住刚铺的素材；mock 回同款文案，视觉验收据此断言前端真的带上了这个标志。
            if body.get("preset_applied"):
                return self._send(200, {
                    "ok": True,
                    "message": "沿用已铺设的预设工作区（素材保留）；已更新 name、genre、chapters"})
            return self._send(200, {"ok": True,
                                    "message": "已快照 current；已归档\u300c验收用书\u300d；空工作区骨架已初始化；"
                                               "已更新 name、genre、chapters"})
        if p == "/factory/clean":
            # 出厂样例清理：mock 只回确定性结果（不真动文件），但**护栏照旧**——
            # 缺 confirm 必须 400，否则确认框的勾选护栏在视觉验收里形同虚设。
            if not body.get("confirm"):
                return self._send(400, {"ok": False,
                                        "error": "清理出厂样例是破坏性操作，必须显式确认"
                                                 "（请求体需 confirm: true）。"})
            return self._send(200, {"ok": True, "count": 2, "moved": [
                "materials/raw/潮神_角色卡.md", "materials/raw/回声_角色卡.md"],
                "trash_dir": "C:/mock/NovelForge/data/books/_trash/factory__20261007_090000",
                "message": "已归档 2 个出厂样例文件到 "
                           "data/books/_trash/factory__20261007_090000（原相对路径保留，可手工捞回）"})
        if p == "/costs/rates/import":
            # 定价批量导入：**复用真实解析器**（解析是纯函数、零状态），
            # mock 只负责补一个确定性的落盘语义，避免在这里复制一份解析判据。
            from utils.cost_tracker import parse_rates_text
            text = str(body.get("text") or "")
            confirm = bool(body.get("confirm"))
            parsed, errors, warnings, fmt = parse_rates_text(text)
            plan = [{"model": n, "action": "new", "from": None, "to": d}
                    for n, d in parsed.items()]
            rep = {"ok": not errors, "format": fmt, "count": len(plan), "plan": plan,
                   "errors": errors, "warnings": warnings, "custom_total": 0,
                   "applied": bool(confirm and not errors),
                   "saved": len(plan) if confirm and not errors else 0}
            if confirm and errors:
                return self._send(400, dict(rep, error="mock：有解析错误，拒绝写入"))
            return self._send(200, rep)
        return self._send(200, {"ok": True, "message": "mock", "job_id": "mockjob1"})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8798)
    ap.add_argument("--cold", action="store_true", help="全新工作区状态（测冷启动引导）")
    a = ap.parse_args()
    COLD = a.cold
    print("mock nf_api on 127.0.0.1:%d (cold=%s)" % (a.port, COLD), flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
