# -*- coding: utf-8 -*-
"""UX 增强（一键工作流 / 错误恢复）新增端点的 HTTP 端到端自检。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起 nf_api
（--allow-fake），所有读写都落在临时目录；同时把子进程的 LOCALAPPDATA/TEMP 指向临时目录，
让「运行日志」分支的路径解析也完全可控。

覆盖：
  GET  /logs/tail        无日志文件 → exists=false + 可行动 hint（不是 500）
  GET  /logs/tail        GBK/UTF-8 混编码日志 → 容错解码，不抛 UnicodeError
  GET  /logs/tail        ?lines= 边界（0 → 1、超大值 → 2000 上限）
  POST /stage/skip       缺 confirm / 非法 stage → 400（防误点放行整条管线）
  POST /stage/skip       正常跳过 → progress.json 写 done+approved+skipped，/state 回读可见
  POST /stage/skip       产物缺失时 message 明确回报缺失路径（下游会不会炸先说清楚）
  POST /reject           跳过之后仍可打回（skipped 标记被清掉，状态回到 pending）
  POST /review/comment   （回归）必须挂在 do_POST 上：GET 版本 body 不存在 → 必然 500

用法：python tests/test_ux_flow_api_http.py
配套静态检查：python tests/test_gui_api_contract.py（前端每个 api() 都有同方法后端分支）
"""
import io
import json
import os
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
PORT = 8933
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


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_uxflow_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "data" / "state").mkdir(parents=True)
    # progress.json：阶段 1-3 已完成并审批，4 失败（错误恢复入口的典型现场）
    (tmp / "data" / "state" / "progress.json").write_text(json.dumps({
        "book": "测试书",
        "stages": {
            "1": {"status": "done", "approved": True},
            "2": {"status": "done", "approved": True},
            "3": {"status": "done", "approved": True},
            "4": {"status": "failed"},
            "5": {"status": "pending"},
            "6": {"status": "pending"},
            "7": {"status": "pending"},
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    # 只造 stage1/2 的产物 → stage3 的产物缺失可被 /stage/skip 回报
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text("{}", encoding="utf-8")
    (tmp / "data" / "outline").mkdir(parents=True)
    (tmp / "data" / "outline" / "global.md").write_text("# 大纲\n", encoding="utf-8")
    return tmp


def main():
    tmp = build_project()
    fake_appdata = tmp / "_appdata"
    (fake_appdata / "Temp").mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(fake_appdata)
    env["TEMP"] = str(fake_appdata / "Temp")
    env["TMP"] = str(fake_appdata / "Temp")
    log = io.open(tmp / "nf_api.log", "w", encoding="utf-8")
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            time.sleep(0.5)
            try:
                code, _d = req("GET", "/health", timeout=3)
                if code == 200:
                    break
            except Exception:
                pass
        else:
            print("服务未能启动，见 " + str(tmp / "nf_api.log"))
            return 1

        print("=== 1. GET /logs/tail ===")
        code, d = req("GET", "/logs/tail?lines=50")
        check("日志文件不存在 → 200 + exists=false（不是 500/断连）",
              code == 200 and d.get("ok") and d.get("exists") is False, (code, str(d)[:200]))
        check("提示里给出路径与替代方案",
              "nf_api_child.log" in str(d.get("path")) and d.get("hint"), str(d)[:200])

        log_path = fake_appdata / "Temp" / "nf_api_child.log"
        # 混编码：GBK 中文 + UTF-8 中文混在一份文件里（真实场景：cmd 的 GBK 输出 + Python 的 UTF-8）
        mixed = b"line-ascii-1\n" + "中文GBK行\n".encode("gbk") + "中文UTF8行\n".encode("utf-8") + b"line-ascii-4\n"
        log_path.write_bytes(mixed)
        code, d = req("GET", "/logs/tail?lines=2")
        check("混编码日志 → 200（容错解码，不再 UnicodeError 断连）",
              code == 200 and d.get("exists"), (code, str(d)[:160]))
        check("只返回末尾 N 行，且 total_lines 为全量行数",
              len(d.get("lines") or []) == 2 and d.get("total_lines") == 4,
              (d.get("total_lines"), d.get("lines")))
        check("最后一行内容正确", (d.get("lines") or [""])[-1].endswith("line-ascii-4"),
              d.get("lines"))
        code, d = req("GET", "/logs/tail?lines=0")
        check("lines=0 → 收敛为 1 行（不返回空、不报错）",
              code == 200 and len(d.get("lines") or []) == 1, (code, len(d.get("lines") or [])))
        code, d = req("GET", "/logs/tail?lines=99999")
        check("lines 超上限 → 钳到 2000 以内", code == 200 and len(d.get("lines") or []) <= 4,
              (code, len(d.get("lines") or [])))
        code, d = req("GET", "/logs/tail?lines=abc")
        check("lines 非数字 → 回退默认值而非 500", code == 200 and d.get("exists"), (code, str(d)[:120]))

        print("\n=== 2. POST /stage/skip 护栏 ===")
        code, d = req("POST", "/stage/skip", {"stage": 4})
        check("缺 confirm → 400（不能一键放行整条管线）",
              code == 400 and "confirm" in str(d.get("error")), (code, str(d)[:200]))
        code, d = req("POST", "/stage/skip", {"stage": 0, "confirm": True})
        check("stage=0 → 400", code == 400, (code, str(d)[:160]))
        code, d = req("POST", "/stage/skip", {"stage": 9, "confirm": True})
        check("stage=9 → 400", code == 400, (code, str(d)[:160]))
        code, d = req("POST", "/stage/skip", {"stage": "abc", "confirm": True})
        check("stage 非数字 → 400（不 500）", code == 400, (code, str(d)[:160]))

        print("\n=== 3. POST /stage/skip 正路径 ===")
        code, d = req("POST", "/stage/skip", {"stage": 3, "confirm": True, "reason": "素材不足先放行"})
        check("合法跳过 → 200 + ok", code == 200 and d.get("ok"), (code, str(d)[:200]))
        check("回报缺失产物（data/outline/chapters 不存在）",
              any("chapters" in m for m in (d.get("missing") or [])), d.get("missing"))
        check("message 里说明「下游阶段可能因此失败」",
              "下游" in str(d.get("message")), str(d.get("message"))[:200])
        prog = json.loads((tmp / "data" / "state" / "progress.json").read_text(encoding="utf-8"))
        st3 = prog["stages"]["3"]
        check("progress.json：status=done + skipped + approved",
              st3.get("status") == "done" and st3.get("skipped") is True and st3.get("approved") is True,
              st3)
        check("跳过原因与时间落盘（可追溯）",
              st3.get("skip_reason") == "素材不足先放行" and st3.get("skipped_at"), st3)
        code, s = req("GET", "/state")
        row = [x for x in s["stages"] if x["stage"] == 3][0]
        check("/state 暴露 skipped（GUI 才能显示「已跳过」徽标）", row.get("skipped") is True, row)

        print("\n=== 4. 跳过之后仍可回退 ===")
        code, d = req("POST", "/reject", {"stage": 3, "reason": "还是想重跑", "dry_run": False})
        check("POST /reject 打回跳过过的阶段 → 200", code == 200 and d.get("ok"), (code, str(d)[:200]))
        prog = json.loads((tmp / "data" / "state" / "progress.json").read_text(encoding="utf-8"))
        st3 = prog["stages"]["3"]
        check("打回后状态变 rejected 且 skipped 标记被清掉（不再同时显示「已跳过」+「已打回」）",
              st3.get("status") == "rejected" and not st3.get("skipped"), st3)
        code, s = req("GET", "/state")
        row = [x for x in s["stages"] if x["stage"] == 3][0]
        check("/state 里 skipped 消失", not row.get("skipped"), row)

        print("\n=== 5. 回归：/review/comment 必须在 do_POST（GET 版本 body 不存在 → 500）===")
        code, d = req("GET", "/review/comment")
        check("GET /review/comment → 404（不再挂在 do_GET 里）",
              code == 404, (code, str(d)[:160]))
        code, d = req("POST", "/review/comment", {"chapter": 1, "finding_id": "f001"})
        check("POST 缺 comment → 400（说明已进 do_POST 分支，拿到 body）",
              code == 400 and "comment" in str(d.get("error")), (code, str(d)[:200]))
        code, d = req("POST", "/review/comment", {"chapter": 1, "finding_id": "f001", "comment": "改一下"})
        check("POST 正常参数 → 404 未找到 finding（报告不存在也走通分支，不 500）",
              code == 404 and not d.get("ok"), (code, str(d)[:200]))

        print("\n=== 6. 未知路径未被破坏 ===")
        code, d = req("GET", "/nope")
        check("未知路径仍 404", code == 404)
        check("404 提示里列出了新端点",
              "/stage/skip" in str(d.get("error")) and "/logs/tail" in str(d.get("error")),
              str(d.get("error"))[:220])

        print("=" * 62)
        print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
        if FAIL:
            print("失败项: " + "、".join(FAIL))
        print("=" * 62)
        return 1 if FAIL else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
