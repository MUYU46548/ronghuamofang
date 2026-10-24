# -*- coding: utf-8 -*-
"""UX 验收用的假后端：给前端喂确定性的 /state（含失败阶段、跳过标记、待重写章节）。

**这是正式测试资产**（`tests/e2e/e2e_ux_verify.py` 的两个后端之一），必须进 Git ——
否则干净克隆上跑不了这个「发版前必跑」的视觉验收闸门。
（2026-09-29 之前它只躺在 `Temp/mock_nf_api_state.py`，而 `Temp/` 被 .gitignore 忽略，
  AGENTS.md 却早已把路径写成 `tests/e2e/` —— 文档与现实不一致，闸门实际不可复现。）

用法（另需 8091 静态服务 + 8799 真后端，见 e2e_ux_verify.py 的文件头）：
    python tests/e2e/mock_nf_api_state.py --port 8798
    python tests/e2e/mock_nf_api_state.py --port 8797 --cold   # 冷启动状态
"""
import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

# Agent 模式开关的 mock 回显（权限面板会显示当前开关状态）
AGENT_MODE = True

# ---- Agent 权限面板：名单直接消费后端真源，不再手打 ----
# 为什么这么做：此前 mock 手列 6 条 forbidden，而真源是 20 条 —— 就地校验当场
# 抓到「界面承诺 /project/restore 不许 Agent 碰，后端名单却查无此项」的真漂移。
# 手打 mock 必然与真源分叉（改一处漏一处），所以这里从 utils.agent_guard 取全量，
# 人话表（AGENT_WHY）只负责**展示文案**，缺项给通用兜底。真源加条目 → mock 自动跟上。
from utils.agent_guard import FORBIDDEN_IN_AGENT_MODE  # noqa: E402

# 展示文案：只列需要解释"为什么归用户"的条目；真源新增条目走下方通用兜底，
# 不会因为这里漏写就消失（消失才是事故，文案平庸不是）。
AGENT_WHY = {
    "/approve": "审批放行 —— 判定权归用户，Agent 不得代批",
    "/reject": "打回重跑 —— 同审批族，会清下游产物",
    "/project/create": "新建项目 —— 会归档当前项目并清空工作区",
    "/project/archive": "归档当前项目 —— 搬空工作区到书库",
    "/project/restore": "恢复归档书 —— 覆盖当前工作区",
    "/project/init": "首启初始化 —— 写入工作区骨架",
    "/project/archive/delete": "删除归档 —— 抹掉成书数据（进回收站）",
    "/config/agent_mode": "Agent 不得开关自己的模式",
    "/config/token_limit": "止烧阈值 —— Agent 不得抬高烧钱上限",
    "/factory/clean": "清理出厂样例 —— 移动用户素材文件",
    "/factory/preset/apply": "铺设出厂预设 —— 快照+归档+铺素材，同新建项目",
    "/factory/project/reset": "出厂项目还原 —— 清书状态+重铺素材",
    "/stage/skip": "跳过阶段 —— 会把 approved 真写进 progress.json",
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

# 设定集 mock：**必须用真实字段结构**，否则这两处修复等于没验：
#   1. 角色真实字段是 traits[]/relations[]/experience，不是 snippet；
#   2. 世界观众是 locations/items 等**分组数组**，真书的 world 下**没有 all 键** ——
#      旧前端读 world.all 会恒显示「暂无条目」（用户截图里那本 2 地点 3 物品全看不见）。
MOCK_SETTING = {
    "characters": [
        {
            "id": "c_zhou_yanqing", "name": "周砚清",
            "role": "青石站巡道工（主角线索人物）",
            "traits": ["不多问也不多答", "认刻度不认人", "对「补写」有执念"],
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
    "budget": {"paused": False,
               # token 闸门观测面（2026-10-03）：真后端 /state 也带这几个字段，
               # mock 不补的话流水线条上的「token 闸门」一行在视觉验收里恒不渲染。
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
                  "ValueError: 缺少角色「苏芷」的设定"]}


DISCUSS = {"items": [], "stats": {"pending": 1, "approved": 0, "rejected": 0}}
# 换行常量：demo 提案文案里拼真实换行用（对齐真后端的多行 markdown）
NL = "\n"
COLD = False   # --cold：全新工作区（所有阶段 pending + has_work=false）→ 冷启动引导

# Word 成品模板面板（件五）的**有状态**当前值：POST 改、GET 读，
# 这样「选模板 → 应用 → 下拉回读新值」在视觉验收里是真链路而不是静态截图。
DEFAULT_WT = "templates/小说格式模板V1.3.dotx"
WT = {"current": DEFAULT_WT}

# 知识库扫描面（2026-10-24）：**有状态** mock —— 「勾掉目录 → 保存 → 列表状态变化」
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
            # 空 totals 会让它走「数据缺失不显示」分支 —— 那样 NaN 修复等于没被验到。
            return self._send(200, {"by_stage": [], "by_model": [], "totals": {
                "cost_yuan": 3.75, "tokens_in": 128000, "tokens_out": 41000,
                "calls": 26, "run_id": 20261024}})
        if p == "/costs/rates":
            # 定价编辑器需要这个端点；空表 = 全部走源码刊例价
            return self._send(200, {"rates": {}, "source": "mock"})
        if p == "/costs/streaming":
            return self._send(200, {"running": False, "spent_yuan": 12.3456, "limit_yuan": 50.0,
                                    "pct": 24.7, "warn_ratio": 0.7, "tokens_in": 0, "tokens_out": 0})
        if p == "/config/project":
            return self._send(200, {"ok": True, "config": {"book": {"name": "验收用书", "chapters": 12}}})
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
            # 的渲染分支显示「N 篇 · M 字」—— 若 mock 只给文件，验收就永远看不到
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
            # 首跑向导「选预设」（v0.20）：回两套可见预设，让第 0 步有真卡片可点。
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
            # 出厂内容面板（设置 → 诊断与调试）：回一份「有命中」的确定性数据，
            # 让视觉验收能真读到清单文件数 / 匹配数 / 前 20 条路径，
            # 而不是停在「读取中…」（mock 不给 fixture，面板就恒为空壳）。
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
                # 出厂样例徽标（项目页「当前项目」旁，2026-10-11）：mock 书名与样例书名
                # 刻意相同 → 徽标必须点亮；书名不同的反例由 tests/http 的 HTTP 自检覆盖。
                "current": {"title": "验收用书", "sample_title": "验收用书",
                            "is_sample": True},
            })
        if p == "/config/vault_scan":
            # 知识库扫描面（设置 → 风格与提示词）：目录名取自用户库本身，
            # 排除态现算 —— 保存后必须看到列表变化，否则视觉验收是假绿。
            return self._send(200, vault_scan_payload())

        if p == "/config/word_template":
            # Word 成品模板面板（设置 → Word 模板，件五）：确定性清单，
            # 让视觉验收能真读到「下拉有几项 / 当前是哪份 / 默认是哪份」。
            return self._send(200, {
                "ok": True,
                "current": WT["current"],
                "default": DEFAULT_WT,
                "current_exists": True,
                "files": [
                    {"name": "小说格式模板V1.3.dotx", "size": 28815,
                     "mtime": "2026-08-13 19:54", "path": "templates/小说格式模板V1.3.dotx"},
                    {"name": "备用模板.docx", "size": 14336,
                     "mtime": "2026-10-11 08:00", "path": "templates/备用模板.docx"},
                ],
                "dir": "templates",
                "path": "config/project.yaml", "field": "book.word_template",
            })
        if p == "/outline/discussions":
            # 讨论提案队列（P0-1 落地协议）：mock 喂 1 条待审，让视觉验收能
            # 真点到「读提案 / 批准并写回 / 驳回」三个动作。
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
        if p == "/outline/chapters/list":
            # 分章页签（章节蓝图编辑器）的章节清单：喂两章，让视觉验收能真选中
            # 一章再点「讨论此章」（currentN 为空时 openDiscuss 直接 return）。
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
            # 抬头（「这是待审提案」）与正文结构。
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
        if p.startswith("/jobs/"):
            jid = p.split("/")[-1]
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
            # 让视觉验收能真点「讨论此章 → 打包并复制」并读到内容。
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
                "message": "提案已落盘待审 —— 去「审核」页签批准后才会写回章大纲"})

        if p == "/outline/sample/current":
            # 已选定的方向：mock 固定回一条，让「已选定」区在视觉验收里可见
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
                             "「规模不是问题，补给才是。」",
                     "why": "适合内敛主角、慢热铺陈的书"},
                    {"label": "外放热烈",
                     "text": "「不行！」主角甲一掌拍在桌上，星图跳了一下，"
                             "「三倍兵力进去，回来的不到一半！」",
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
                "message": "方向样例已落盘待审 —— 去「审核」页签批准后才会进"
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

        if p == "/config/word_template":
            # Word 模板面板（件五）：**有状态** mock —— 选完再读必须能看到新值，
            # 否则「应用 → 下拉回跳」这种真 bug 在视觉验收里测不出来。
            if not body.get("reset") and not str(body.get("template") or "").strip():
                return self._send(400, {"ok": False,
                                        "error": "未选择模板（要回到出厂模板请点「恢复默认」）"})
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
            # 否则「批准 → 待审归零」这类闭环在视觉验收里测不出来）。
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
            # 向导第 0 步「铺设」：真端点会快照→归档→铺素材→写配置；mock 只回
            # 确定性成功，让视觉验收能走到「已铺设 → 第 2 步不再要求勾归档」这条链。
            if not body.get("id"):
                return self._send(400, {"ok": False, "error": "缺少 id"})
            return self._send(200, {"ok": True,
                                    "message": "预设「%s」已铺设" % body.get("id"),
                                    "log": "· 已快照\n· 已归档\n· 素材 6 张已铺入"})
        if p == "/project/create":
            # preset_applied=true（向导第 0 步已铺设预设）→ 真后端会跳过归档/初始化、
            # 保住刚铺的素材；mock 回同款文案，视觉验收据此断言前端真的带上了这个标志。
            if body.get("preset_applied"):
                return self._send(200, {
                    "ok": True,
                    "message": "沿用已铺设的预设工作区（素材保留）；已更新 name、genre、chapters"})
            return self._send(200, {"ok": True,
                                    "message": "已快照 current；已归档「验收用书」；空工作区骨架已初始化；"
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
