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

STATE = {
    "book": "验收用书",
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


COLD = False   # --cold：全新工作区（所有阶段 pending + has_work=false）→ 冷启动引导


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
        if p == "/costs":
            return self._send(200, {"entries": []})
        if p == "/costs/summary":
            return self._send(200, {"by_stage": [], "by_model": [], "totals": {}})
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
        if p == "/project/list":
            return self._send(200, {"current": "验收用书",
                                    "archived": [{"name": "旧书", "display_name": "旧书",
                                                  "items": 6, "size_kb": 320.5,
                                                  "archived_at": "2026-09-14 18:20:00"}]})
        if p.startswith("/jobs/"):
            return self._send(200, {"id": p.split("/")[-1], "kind": "stage3", "state": "failed",
                                    "result": "exit=1"})
        return self._send(404, {"ok": False, "error": "mock: 未实现的 " + p})

    def do_POST(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        if p == "/stage/skip":
            return self._send(200, {"ok": True,
                                    "message": "阶段%d 已标记完成（人工跳过，未生成产物）" % body.get("stage", 0),
                                    "missing": ["data/outline/chapters"]})
        if p == "/project/create":
            return self._send(200, {"ok": True,
                                    "message": "已快照 current；已归档「验收用书」；空工作区骨架已初始化；"
                                               "已更新 name、genre、chapters"})
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
