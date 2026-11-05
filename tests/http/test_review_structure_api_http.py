# -*- coding: utf-8 -*-
"""结构指标端点自检（GET /review/structure，HTTP 端到端）。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起 nf_api。

覆盖：
  - 没有 structure.json → 200 + exists:false + 一句“跑过阶段 5 之后才有”（不是 404 报错）；
  - 有 structure.json → 六项指标、需抽样章节、口径缺项原样回传；
  - 端点只读：请求前后文件不变。

用法：python tests/http/test_review_structure_api_http.py
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
PY = sys.executable
PORT = 8947
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, timeout=20):
    r = urllib.request.Request(BASE + path, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                   # noqa: BLE001
            payload = {}
        return e.code, payload


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="review_structure_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    if not (tmp / "config" / "project.yaml").exists():
        shutil.copy2(ROOT / "config" / "project.yaml.example",
                     tmp / "config" / "project.yaml")
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "data").mkdir()
    return tmp


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                code, _h = req("GET", "/health")
                if code == 200:
                    break
            except Exception:                               # noqa: BLE001
                time.sleep(0.25)
        else:
            print("  [FAIL] 服务未起来")
            return 1

        print("\n=== 1. 还没算过 → exists:false（不是 404） ===")
        code, d = req("GET", "/review/structure")
        check("200", code == 200, code)
        check("ok 且 exists=false", d.get("ok") is True and d.get("exists") is False,
              str(d)[:160])
        check("给出下一步提示", "阶段 5" in (d.get("hint") or ""), d.get("hint"))

        print("\n=== 2. 有账本 → 六项指标 + 需抽样章节 ===")
        rdir = tmp / "data" / "review"
        rdir.mkdir(parents=True)
        fixture = {
            "ok": True, "caliber_version": 3, "missing_criteria": [],
            "dimensions": [
                {"code": "question_ratio", "label": "问号占对白", "unit": "比例",
                 "direction": "min", "criterion": "J16"},
                {"code": "single_turn_streak", "label": "连续单回合问答", "unit": "组",
                 "direction": "max", "criterion": "J17"},
            ],
            "chapters": [
                {"chapter": 2, "metrics": {"question_ratio": 0.02,
                                           "single_turn_streak": 19},
                 "triggered": [{"code": "question_ratio", "label": "问号占对白",
                                "value": 0.02, "limit": 0.15, "direction": "min"}],
                 "na": [], "needs_sampling": True},
                {"chapter": 3, "metrics": {"question_ratio": 0.4,
                                           "single_turn_streak": 1},
                 "triggered": [], "na": [], "needs_sampling": False},
            ],
            "summary": {"chapters": 2, "needs_sampling": [2],
                        "triggered_count": {"question_ratio": 1,
                                            "single_turn_streak": 0},
                        "metrics_mean": {"question_ratio": 0.21,
                                         "single_turn_streak": 10}},
            "note": "只提示人工抽样，不判合格、不合成总分",
        }
        (rdir / "structure.json").write_text(
            json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        before = (rdir / "structure.json").read_bytes()

        code, d2 = req("GET", "/review/structure")
        check("200 且 exists=true", code == 200 and d2.get("exists") is True, code)
        check("需抽样章节回传（第 2 章）", d2.get("summary", {}).get("needs_sampling") == [2],
              d2.get("summary"))
        check("六项定义回传（界面按它排版）", len(d2.get("dimensions") or []) == 2,
              d2.get("dimensions"))
        check("口径版本回传", d2.get("caliber_version") == 3, d2.get("caliber_version"))
        check("明说“不合成总分”", "不合成总分" in (d2.get("note") or ""), d2.get("note"))
        check("端点只读（文件字节未变）", (rdir / "structure.json").read_bytes() == before)

        print("\n=== 3. 口径缺项原样回传 ===")
        fixture["missing_criteria"] = ["J16"]
        (rdir / "structure.json").write_text(
            json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        code, d3 = req("GET", "/review/structure")
        check("缺判据清单回传（前端据此警示）",
              d3.get("missing_criteria") == ["J16"], d3.get("missing_criteria"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
