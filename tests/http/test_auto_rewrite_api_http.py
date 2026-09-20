# -*- coding: utf-8 -*-
"""质量自评闭环端点 HTTP 端到端自检（/state 暴露 + POST /auto_rewrite/run）。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起 nf_api，
所有读写都落在临时目录，真实 data/ 完全不受影响。

覆盖：
- /state 暴露 stage4 的 needs_rewrite / auto_rewritten
- POST /auto_rewrite/run 默认 dry_run（防误改稿）→ 章节 quality 不变
- 显式 dry_run=false → 章节 quality 提升 + auto_rewritten / needs_rewrite 更新
- 达 max_rounds → 跳过（幂等，不二次改稿）

用法：python tests/test_auto_rewrite_api_http.py
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
PORT = 8919
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=20):
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


def wait_job(jid, timeout=60):
    for _ in range(int(timeout / 0.25)):
        code, d = req("GET", "/jobs/" + jid)
        if code == 200 and d.get("state") in ("ok", "failed"):
            return d
        time.sleep(0.25)
    return {"state": "timeout"}


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="auto_rewrite_api_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True)
    (tmp / "data" / "outline").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "logs").mkdir()
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text(
        "## 第1章 测试章节\n\n" + "测试正文。" * 80 + "\n\n"
        "<!-- quality: 4/10 -->\n<!-- summary: 测试摘要。 -->\n", encoding="utf-8")
    # 模拟 stage4 已标记 needs_rewrite（验证重写后能真正清空该标记）
    (tmp / "data" / "state" / "progress.json").write_text(json.dumps({
        "project": "测试书",
        "stages": {"4": {"status": "done", "needs_rewrite": [1]}},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp


def stage4_state(tmp):
    code, d = req("GET", "/state")
    for s in d.get("stages", []):
        if s.get("stage") == 4:
            return s
    return {}


def quality_of(tmp):
    txt = (tmp / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
    import re
    return int(re.search(r"quality:\s*(\d+)", txt).group(1))


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                if req("GET", "/health")[0] == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            print("  [FAIL] 服务未起来")
            return 1

        print("\n=== 1. GET /state 暴露 auto_rewritten 字段位 ===")
        s4 = stage4_state(tmp)
        check("/state 含 stage4", bool(s4), s4)
        check("stage4 已带 needs_rewrite（stage4 标记）",
              s4.get("needs_rewrite") == [1], s4.get("needs_rewrite"))
        check("初始无 auto_rewritten（字段按需出现）",
              "auto_rewritten" not in s4, list(s4.keys()))

        print("\n=== 2. POST /auto_rewrite/run 默认 dry-run（防误改稿）===")
        code, d = req("POST", "/auto_rewrite/run", {})
        check("202 + job_id", code == 202 and d.get("job_id"), str(d)[:160])
        job = wait_job(d.get("job_id")) if d.get("job_id") else {}
        check("dry-run job 成功", job.get("state") == "ok", str(job)[:200])
        check("dry-run 后章节 quality 仍为 4（未改稿）", quality_of(tmp) == 4, quality_of(tmp))
        s4 = stage4_state(tmp)
        check("dry-run 未写 auto_rewritten", "auto_rewritten" not in s4, list(s4.keys()))
        check("dry-run 未动 needs_rewrite", s4.get("needs_rewrite") == [1], s4.get("needs_rewrite"))

        print("\n=== 3. 显式 dry_run=false → 真正重写 ===")
        code, d = req("POST", "/auto_rewrite/run",
                      {"dry_run": False, "threshold": 6, "chapters": "1"})
        check("202 + job_id", code == 202 and d.get("job_id"), str(d)[:160])
        job = wait_job(d.get("job_id")) if d.get("job_id") else {}
        check("重写 job 成功", job.get("state") == "ok", str(job)[:200])
        check("章节 quality 提升至 8（fake 语义）", quality_of(tmp) == 8, quality_of(tmp))
        s4 = stage4_state(tmp)
        check("/state 反映 auto_rewritten = {1:1}",
              s4.get("auto_rewritten") == {"1": 1}, s4.get("auto_rewritten"))
        check("/state 反映 needs_rewrite 已清空",
              s4.get("needs_rewrite") == [], s4.get("needs_rewrite"))
        check("生成了机器可读报告",
              (tmp / "data/outline/auto_rewrite_report.json").exists())

        print("\n=== 4. 幂等：达 max_rounds 后不再重写 ===")
        stamp = (tmp / "data/chapters/raw/01.md").stat().st_mtime_ns
        # 人为把 quality 打回 4 模拟「重写后仍不达标」，但轮次已用尽
        p = tmp / "data/chapters/raw/01.md"
        p.write_text(p.read_text(encoding="utf-8").replace("quality: 8", "quality: 4"),
                     encoding="utf-8")
        code, d = req("POST", "/auto_rewrite/run",
                      {"dry_run": False, "max_rounds": 1, "chapters": "1"})
        job = wait_job(d.get("job_id")) if d.get("job_id") else {}
        check("job 成功（跳过不算失败）", job.get("state") == "ok", str(job)[:200])
        check("达上限未再改稿（quality 保持 4）", quality_of(tmp) == 4, quality_of(tmp))
        check("auto_rewritten 轮次未叠加",
              stage4_state(tmp).get("auto_rewritten") == {"1": 1},
              stage4_state(tmp).get("auto_rewritten"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
