# -*- coding: utf-8 -*-
"""B2 整本否掉的端点契约（2026-10-31）。

用户拍板「B2 加按钮」后，这里钉住四件事：

- A1 理由太短 → 400（否掉整本是留档决定，不是随手一点）
- A2 理由够 → 200，落盘；`/judge/report` 回传（界面横幅靠它）
- A3 撤销 → 200，当前记录清空、历史留档
- A4 两个端点都在 `FORBIDDEN_IN_AGENT_MODE` 里 —— 与事件勾选、逐句裁定同族：
     外部 Agent 可以读（report 带 book_reject），不可以代替作者签字

用法：python tests/http/test_judge_b2_api_http.py
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
PY = sys.executable
PORT = 8955
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []

BODY = """## 第1章 夜雨

他在档案室盖章。他把回执压平，拿起印章，在纸角上按下去。

他核对回执上的编号。他一行一行往下看，看到第三行停住。

他抽出一张字条。他把字条摊在桌上，压在回执底下。
"""

OUTLINE = """- 核心事件：
  1. 他在档案室盖章
  2. 他核对回执编号
- 涉及角色：沈砚
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=60, headers=None):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    h = {"Content-Type": "application/json", "X-Mofang-Source": "gui"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                    # noqa: BLE001
            payload = {}
        return e.code, payload


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_b2http_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    for d in ("data/state", "data/setting", "data/chapters/raw",
              "data/outline/chapters", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text(BODY, encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text(OUTLINE, encoding="utf-8")
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "B2端点验收"\n  chapters: 1\n', encoding="utf-8")
    import re
    sys_yaml = tmp / "config" / "system.yaml"
    txt = sys_yaml.read_text(encoding="utf-8")
    sys_yaml.write_text(re.sub(r"agent_mode:\s*true", "agent_mode: false", txt),
                        encoding="utf-8")
    return tmp


def main():
    tmp = build_project()
    proc = None
    try:
        env = dict(os.environ)
        env["NF_ROOT"] = str(tmp)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [PY, str(ROOT / "scripts" / "nf_api.py"), "--port", str(PORT),
             "--root", str(tmp)],
            cwd=str(tmp), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        ready = False
        for _ in range(80):
            try:
                if req("GET", "/health")[0] == 200:
                    ready = True
                    break
            except Exception:                                # noqa: BLE001
                time.sleep(0.5)
        check("nf_api 起来了", ready)
        if not ready:
            return 1

        print("\n【A1】理由太短 → 400")
        for bad in ("", "太平", "   "):
            code, d = req("POST", "/judge/book_reject", {"reason": bad})
            check("拒绝 %r" % (bad,), code == 400, (code, d.get("error")))
        code, d = req("POST", "/judge/book_reject", {})
        check("缺 reason 字段也拒", code == 400, (code, d.get("error")))

        print("\n【A2】理由够 → 落盘 + report 回传")
        code, d = req("POST", "/judge/book_reject",
                      {"reason": "整本没有冲突也没有推进，重写比改划算"})
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        check("回执带 message（用户要知道发生了什么）",
              "整本否掉" in (d.get("message") or ""), d.get("message"))
        code, rep = req("GET", "/judge/report")
        br = rep.get("book_reject") or {}
        check("report 回传 book_reject（界面横幅靠它）",
              br.get("reason") == "整本没有冲突也没有推进，重写比改划算", br)
        check("带时间与署名", bool(br.get("at")) and br.get("by") == "author", br)

        print("\n【A3】撤销 → 清当前、留历史")
        code, d = req("POST", "/judge/book_reject/clear", {})
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        code, rep2 = req("GET", "/judge/report")
        check("当前记录已清空", not rep2.get("book_reject"), rep2.get("book_reject"))
        hist = rep2.get("book_reject_history") or []
        check("历史里留了一条（曾经否过不消失）", len(hist) == 1, hist)
        code, d = req("POST", "/judge/book_reject/clear", {})
        check("再撤销 → 400（明确说没有记录，不静默成功）", code == 400, (code, d))

        print("\n【A4】Agent 禁用名单")
        sys.path.insert(0, str(ROOT / "scripts"))
        from utils import agent_guard as AG
        check("/judge/book_reject 在 FORBIDDEN_IN_AGENT_MODE 里",
              "/judge/book_reject" in AG.FORBIDDEN_IN_AGENT_MODE)
        check("/judge/book_reject/clear 也在",
              "/judge/book_reject/clear" in AG.FORBIDDEN_IN_AGENT_MODE)
        check("只读的 /judge/report 不在名单里（Agent 可以读作者判断）",
              "/judge/report" not in AG.FORBIDDEN_IN_AGENT_MODE)
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
