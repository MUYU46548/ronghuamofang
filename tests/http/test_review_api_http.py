# -*- coding: utf-8 -*-
"""审稿→修稿闭环 HTTP 端到端自检（任务2：GUI 审稿标签页接通）。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ templates/ 复制到临时目录当 ROOT 起
nf_api（--allow-fake），所有读写都落在临时目录，真实 data/ 与 output/ 完全不受影响。

覆盖 GUI 的真实调用序列：
  无报告时 GET /review/report 不崩 → POST /batch_refine/run 给可行动的 400（不是 500）
  → POST /review/run（fake 审查）→ GET /jobs/{id} 轮询 → GET /review/report 有 findings
  → POST /review/decisions（GUI 标准格式）→ GET /review/decisions 回读
  → POST /batch_refine/run → 轮询 → 章节被改（quality 8/10）+ history 备份 + 进度文件
  → 决策真的被 batch_refine 读到（只有被 accept 的章节被改）
  → 兼容键值格式决策 / 空决策给 400 / 交互式决策被拒

用法：python tests/test_review_api_http.py
"""
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
PORT = 8918
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def req(method, path, body=None, timeout=30):
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


def wait_job(job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        code, d = req("GET", "/jobs/" + job_id)
        if code == 200 and d.get("state") != "running":
            return d
        time.sleep(0.4)
    return {"state": "timeout"}


CHAPTER_BODY = (
    "## 第{n}章 测试章节\n\n"
    "他推门进来，把伞放在门边，看了一眼窗外的雨，然后点起了灯。"
    "巷口的灯在雨里晃，晃成一片模糊的黄。\n\n"
    "<!-- quality: 5/10 -->\n"
    "<!-- summary: 测试摘要：主角推进主线。 -->\n"
)


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="review_api_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "materials" / "raw").mkdir(parents=True)
    raw = tmp / "data" / "chapters" / "raw"
    raw.mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    for n in (1, 2, 3):
        (raw / ("%02d.md" % n)).write_text(CHAPTER_BODY.format(n=n), encoding="utf-8")
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件推进\n- 涉及角色：测试角色（主角）\n" % n,
            encoding="utf-8")
    return tmp


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                code, _h = req("GET", "/health")
                if code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            print("  [FAIL] 服务未起来")
            return 1

        print("\n=== 1. 无报告/无决策时不得崩（GUI 首次进入）===")
        code, d = req("GET", "/review/report")
        check("GET /review/report 无报告 → 404（不是 500；UI 走“暂无报告”分支）",
              code == 404 and "no report" in str(d.get("error", "")).lower(), (code, str(d)[:140]))
        code, d = req("GET", "/review/decisions")
        check("GET /review/decisions → 200 且 decisions 为空",
              code == 200 and d.get("decisions") == [], (code, str(d)[:140]))
        code, d = req("GET", "/batch_refine/progress")
        check("GET /batch_refine/progress → 200 idle（进度条不报错）",
              code == 200 and d.get("status") == "idle", (code, str(d)[:140]))
        code, d = req("POST", "/batch_refine/run", {"decisions_from_file": True})
        check("无报告直接跑批量精修 → 400 + 可行动提示（不是 500/挂起）",
              code == 400 and "审查报告" in str(d.get("error")), (code, str(d)[:200]))

        print("\n=== 2. 运行审查（fake 走 chapter_review 分支）===")
        code, d = req("POST", "/review/run", {"stream": False})
        check("POST /review/run → 202 + job_id", code == 202 and d.get("job_id"), (code, str(d)[:160]))
        job = wait_job(d.get("job_id"))
        check("审查 job 成功结束", job.get("state") == "ok", str(job)[:200])

        report_file = tmp / "data" / "outline" / "review_report.json"
        check("review_report.json 已生成", report_file.exists())
        code, d = req("GET", "/review/report")
        chaps = d.get("chapters") or []
        check("GET /review/report 返回章节数组", code == 200 and len(chaps) == 3, (code, len(chaps)))
        findings = [(c["n"], f["id"]) for c in chaps for f in (c.get("findings") or [])]
        check("报告含 findings（偶数章各 1 条）", len(findings) >= 1, findings)
        check("report 带 deterministic（GUI 汇总表要用）", bool(d.get("deterministic")), list(d)[:8])

        print("\n=== 3. 保存决策（GUI 标准格式）===")
        payload = {"decisions": [
            {"finding_id": findings[0][1], "chapter": findings[0][0],
             "action": "accept", "feedback": "补写关键事件，注意衔接"},
            {"finding_id": "f999", "chapter": 1, "action": "ignore", "feedback": ""},
        ]}
        code, d = req("POST", "/review/decisions", payload)
        check("POST /review/decisions → 200 + count=2",
              code == 200 and d.get("count") == 2, (code, str(d)[:180]))
        dec_file = tmp / "data" / "outline" / "review_report.decisions.json"
        check("决策落到 review_report.decisions.json", dec_file.exists())
        saved = json.loads(dec_file.read_text(encoding="utf-8"))
        check("落盘为 batch_refine 期望的标准格式 {'decisions': [...]}",
              isinstance(saved.get("decisions"), list) and
              saved["decisions"][0].get("finding_id") == findings[0][1], str(saved)[:200])
        code, d = req("GET", "/review/decisions")
        check("GET /review/decisions 回读一致",
              code == 200 and len(d.get("decisions") or []) == 2, (code, str(d)[:160]))

        print("\n=== 4. 批量精修（决策真的被读到）===")
        code, d = req("POST", "/batch_refine/run", {"decisions_from_file": True})
        check("POST /batch_refine/run → 202 + job_id", code == 202 and d.get("job_id"),
              (code, str(d)[:200]))
        job = wait_job(d.get("job_id"))
        check("批量精修 job 成功结束", job.get("state") == "ok", str(job)[:200])

        target = tmp / "data" / "chapters" / "raw" / ("%02d.md" % findings[0][0])
        body = target.read_text(encoding="utf-8")
        check("被 accept 的章节真的被改写（fake 把 quality 置 8/10）",
              "quality: 8/10" in body, body[-80:])
        other = tmp / "data" / "chapters" / "raw" / ("%02d.md" % (1 if findings[0][0] != 1 else 3))
        check("未被 accept 的章节保持原样（决策确实驱动了范围）",
              "quality: 5/10" in other.read_text(encoding="utf-8"))
        check("精修前有 history 备份",
              (tmp / "data" / "chapters" / "history").glob("ch*_v*.md").__next__() is not None
              if any((tmp / "data" / "chapters" / "history").glob("ch*_v*.md")) else False,
              [p.name for p in (tmp / "data" / "chapters" / "history").glob("ch*_v*.md")])
        code, d = req("GET", "/batch_refine/progress")
        check("进度文件收尾（status done，GUI 进度条收工）",
              code == 200 and d.get("status") == "done", (code, str(d)[:200]))

        print("\n=== 5. 兼容性 / 护栏 ===")
        # 键值格式（GUI 直传形态）也能规范化
        code, d = req("POST", "/review/decisions",
                      {"f001": {"action": "ACCEPT", "feedback": "大写也要认"}})
        saved2 = json.loads(dec_file.read_text(encoding="utf-8"))
        check("键值格式 + 大写 action 被规范化",
              code == 200 and saved2["decisions"][0].get("action") == "accept"
              and saved2["decisions"][0].get("finding_id") == "f001", str(saved2)[:200])
        # 空决策
        code, d = req("POST", "/review/decisions", {"decisions": []})
        check("空决策保存 → 200 但带 warning（不静默成功）",
              code == 200 and d.get("warning"), (code, str(d)[:200]))
        code, d = req("POST", "/batch_refine/run", {"decisions_from_file": True})
        check("决策文件无有效条目 → 400 且提示如何修",
              code == 400 and "决策" in str(d.get("error")), (code, str(d)[:200]))
        # 交互式（服务端无 TTY）
        code, d = req("POST", "/batch_refine/run", {"decisions_from_file": False})
        check("交互式决策被拒（避免挂住服务线程）", code == 400, (code, str(d)[:200]))

        print("\n=== 6. 未知路径未被破坏 ===")
        code, _d = req("GET", "/nope")
        check("未知路径仍 404", code == 404)

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
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
