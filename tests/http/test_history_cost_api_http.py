# -*- coding: utf-8 -*-
"""本轮新增端点 / 改动的 HTTP 端到端自检。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起
nf_api（--allow-fake），所有读写都落在临时目录。

覆盖：
  GET  /chapters/history   参数校验 / 空历史 / 有历史（带字数与 quality）
  POST /chapters/restore   参数校验 / 版本不存在（400 + 列出可用版本）/
                           正常回退（正文被替换）/ 回退前当前稿另存一版（可回退）
  GET  /costs/streaming    字段完整 + pct/budget_left 口径自洽
  POST /stop               缺 job_id → 400；未知 job_id → 200（幂等、不 500）
  AST 静态检查：do_GET / do_POST 内不得有本地名遮蔽模块级名

说明：停止按钮"真的中断"（退出码 4、runs.db 落 stopped）由
tests/stop_and_mingjian.py 在同进程内确定性验证；此处只验证 HTTP 管线的
参数与幂等行为，不做竞态断言（避免假绿）。

用法：python tests/test_history_cost_api_http.py
"""
import ast
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8941
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=60):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {}
        return e.code, payload


CH = """## 第{n}章 测试章节

他推门进来，把伞靠在门边，看了一眼窗外的天色。

“回来了？”她问。

他应了一声，把炉火拨得更旺了些，才慢慢说起路上遇见的那些人和事。
"""


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_hc_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    refined = tmp / "data" / "chapters" / "refined"
    refined.mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    for n in (1, 2, 3):
        (refined / ("%02d.md" % n)).write_text(CH.format(n=n), encoding="utf-8")
    return tmp


def ast_shadow_check():
    """do_GET / do_POST 内不得有本地名遮蔽模块级名（2026-09-14 UnboundLocalError 复盘）。"""
    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    mod_names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                mod_names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                mod_names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            mod_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    mod_names.add(t.id)
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in ("do_GET", "do_POST")):
            continue
        local = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Import):
                for a in n.names:
                    local.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    local.add(a.asname or a.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    for sub in ast.walk(t):
                        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                            local.add(sub.id)
        hit = sorted(local & mod_names)
        if hit:
            bad.append((node.name, hit))
    return bad


def main():
    tmp = build_project()
    proc = subprocess.Popen(
        [str(PY), "scripts/nf_api.py", "--port", str(PORT), "--allow-fake"],
        cwd=str(tmp), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    try:
        # 等端口就绪
        ready = False
        for _ in range(60):
            try:
                code, _d = req("GET", "/health", timeout=3)
                if code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.3)
        if not ready:
            print("服务未就绪，进程输出：")
            proc.kill()
            print(proc.stdout.read()[:3000])
            return 1

        print("=== 1. GET /chapters/history ===")
        code, d = req("GET", "/chapters/history")
        check("缺 n → 400", code == 400, (code, d.get("error")))
        code, d = req("GET", "/chapters/history?n=abc")
        check("n 非数字 → 400", code == 400, (code, d.get("error")))
        code, d = req("GET", "/chapters/history?n=1")
        check("首次查询 → 200 且历史为空", code == 200 and d.get("count") == 0, (code, d))
        check("回传当前稿路径", str(d.get("current", "")).endswith("01.md"), d.get("current"))
        check("回传备份目录", "chapters" in str(d.get("history_dir")), d.get("history_dir"))

        print("\n=== 2. POST /chapters/restore 参数校验 ===")
        code, d = req("POST", "/chapters/restore", {})
        check("缺 n → 400", code == 400, (code, d.get("error")))
        code, d = req("POST", "/chapters/restore", {"n": 1})
        check("缺 version → 400", code == 400, (code, d.get("error")))
        code, d = req("POST", "/chapters/restore", {"n": 1, "version": 1})
        check("无该版本 → 400 且提示可用版本", code == 400 and "无 v1" in str(d.get("message")),
              (code, d.get("message")))

        print("\n=== 3. 回退流程（造一个 v1 备份） ===")
        hist = tmp / "data" / "chapters" / "history"
        hist.mkdir(parents=True, exist_ok=True)
        old_text = CH.format(n=1).replace("炉火拨得更旺了些", "炉火拨得旺了些（v1 旧稿）")
        (hist / "ch01_v1.md").write_text(old_text, encoding="utf-8")
        code, d = req("GET", "/chapters/history?n=1")
        check("列出 1 个历史版本", code == 200 and d.get("count") == 1, (code, d.get("count")))
        v = (d.get("versions") or [{}])[0]
        check("版本条目含 version/words/mtime/quality",
              all(k in v for k in ("version", "words", "mtime", "quality")), v)
        check("版本字数已统计（>0）", (v.get("words") or 0) > 0, v.get("words"))

        code, d = req("POST", "/chapters/restore", {"n": 1, "version": 1})
        check("回退 → 200", code == 200 and d.get("ok") is True, (code, d))
        cur = (tmp / "data" / "chapters" / "refined" / "01.md").read_text(encoding="utf-8")
        check("正文已替换为 v1 内容", "v1 旧稿" in cur)
        check("回退前当前稿已另存一版（v2）", (hist / "ch01_v2.md").exists())
        v2 = (hist / "ch01_v2.md").read_text(encoding="utf-8") if (hist / "ch01_v2.md").exists() else ""
        check("v2 保存的是回退前的正文", "炉火拨得更旺了些" in v2 and "v1 旧稿" not in v2)
        code, d = req("GET", "/chapters/history?n=1")
        check("回退后历史变 2 条", d.get("count") == 2, d.get("count"))

        code, d = req("POST", "/chapters/restore", {"n": 1, "version": 99})
        check("回退到不存在版本 → 400 且列出 v1/v2",
              code == 400 and "v1" in str(d.get("message")) and "v2" in str(d.get("message")),
              (code, d.get("message")))
        code, d = req("POST", "/chapters/restore", {"n": 3, "version": 1})
        check("无备份的章 → 400（不是 500）", code == 400, (code, d.get("message")))

        print("\n=== 4. GET /costs/streaming ===")
        code, d = req("GET", "/costs/streaming")
        check("→ 200", code == 200, (code, d.get("error")))
        need = ("running", "run_id", "spent_yuan", "tokens_in", "tokens_out", "calls",
                "limit_yuan", "budget_left", "pct", "elapsed_s", "estimated_calls", "warn_ratio")
        check("字段完整", all(k in d for k in need), sorted(set(need) - set(d)))
        check("pct 与 spent/limit 口径自洽",
              abs(d["pct"] - round(d["spent_yuan"] / d["limit_yuan"] * 100, 2)) < 0.02,
              (d["pct"], d["spent_yuan"], d["limit_yuan"]))
        check("budget_left = limit - spent",
              abs(d["budget_left"] - (d["limit_yuan"] - d["spent_yuan"])) < 1e-6,
              (d["budget_left"], d["limit_yuan"], d["spent_yuan"]))
        check("无 job 在跑时 elapsed_s 归零（不报假时长）",
              d["running"] or d["elapsed_s"] == 0, (d["running"], d["elapsed_s"]))

        print("\n=== 5. POST /stop 幂等与参数 ===")
        code, d = req("POST", "/stop", {})
        check("缺 job_id → 400", code == 400, (code, d.get("error")))
        code, d = req("POST", "/stop", {"job_id": "no-such-job"})
        check("未知 job_id → 200（幂等，不 500）", code == 200 and d.get("ok") is True, (code, d))
        check("响应提示「停止」语义已生效", "停止" in str(d.get("message")), d.get("message"))

        print("\n=== 6. 静态：do_GET/do_POST 无本地名遮蔽 ===")
        bad = ast_shadow_check()
        check("无本地名遮蔽模块级名", not bad, bad)

        print("\n=== 7. 未知路径与已存在端点未被破坏 ===")
        code, _d = req("GET", "/nope")
        check("未知路径仍 404", code == 404)
        code, d = req("GET", "/book/pacing?source=current")
        check("GET /book/pacing 仍可用", code == 200 and len(d.get("chapters") or []) == 3,
              (code, len(d.get("chapters") or [])))
        code, d = req("GET", "/costs/summary")
        check("GET /costs/summary 仍可用", code == 200, code)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
