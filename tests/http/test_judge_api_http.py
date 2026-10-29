# -*- coding: utf-8 -*-
"""判定引擎端点自检（0.7.17）：回测 / 报告 / 单章 / 勾选 / 裁定 / 口径 / 三稿。

钉什么：
- J1 没跑过时 `exists:false`，不假装有结论
- J2 回测跑通且只有 raw 参与；报告头带两句固定串
- J3 单章详情：缺陷条目五项齐备（chapter/paragraph_index/sentence/rule_id/count）
- J4 未勾事件 → 结论 null + 待作者勾选；勾完 → 有档位
- J5 逐句裁定：无理由 400，有理由 200
- J6 口径：版本/指纹/消费方；改值产新版本
- J7 三稿对照在缺 checked/refined 时明说缺版，不记 0
- J8 零 token：进程内没有模型客户端构造（端点不 import 客户端）

用法：python tests/http/test_judge_api_http.py
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
sys.path.insert(0, str(ROOT / "scripts"))
PY = sys.executable
PORT = 8936
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []

BODY = """## 第1章 夜雨

他在档案室盖章。他把回执压平，拿起印章，在纸角上按下去。

他核对回执上的编号。他一行一行往下看，看到第三行停住。

他在日志上划改。他把笔尖朝上，放回笔槽。

他抽出一张字条。他把字条摊在桌上，压在回执底下。
"""

OUTLINE = """## 第1章 夜雨
- 核心事件：
  1. 他在档案室盖章
  2. 他核对回执编号
  3. 他在日志上划改
  4. 他发现一张字条
- 涉及角色：沈砚
- 功能：推进
- 衔接：承接上一章
"""

MIN_YAML = """book:
  name: "判定验收书"
  chapters: 3
  style_reference: ""
materials:
  dir: "materials/raw"
"""


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
        except Exception:                                    # noqa: BLE001
            payload = {}
        return e.code, payload


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_judge_http_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    for d in ("data/state", "data/setting", "data/chapters/raw",
              "data/outline/chapters", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    for n in range(1, 4):
        (tmp / "data" / "chapters" / "raw" / ("%02d.md" % n)).write_text(
            BODY.replace("第1章", "第%d章" % n), encoding="utf-8")
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            OUTLINE.replace("第1章", "第%d章" % n), encoding="utf-8")
    (tmp / "config" / "project.yaml").write_text(MIN_YAML, encoding="utf-8")
    # 本用例走纯 HTTP（没有人来源头）：把 agent_mode 关掉，否则 /judge/events
    # 会被 agent_guard 正确拦成 403 —— 那是设计，不该被测试当成缺陷（另有专门用例）。
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
        env = dict(__import__("os").environ)
        env["NF_ROOT"] = str(tmp)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [PY, "scripts/nf_api.py", "--port", str(PORT), "--root", str(tmp)],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        ready = False
        for _ in range(60):
            try:
                if req("GET", "/health")[0] == 200:
                    ready = True
                    break
            except Exception:                                # noqa: BLE001
                time.sleep(0.5)
        check("nf_api 起来了", ready)
        if not ready:
            return 1

        print("\n【J1】没跑过 → exists:false")
        code, d = req("GET", "/judge/report")
        check("200 且 exists=false", code == 200 and d.get("exists") is False, d)
        check("口径信息照样回（不逼用户先跑）", (d.get("caliber") or {}).get("version") == 1,
              d.get("caliber"))

        print("\n【J2】回测：只读 raw、总账、固定串")
        code, d = req("POST", "/judge/run", {})
        check("回测 200", code == 200 and d.get("ok"), (code, d.get("error")))
        check("覆盖 3 章", (d.get("summary") or {}).get("total") == 3, d.get("summary"))
        check("固定串①②都在",
              "存量回测判定，非新跑拦截" in (d.get("notices") or [])
              and "本报告不改变现有成品文件" in (d.get("notices") or []), d.get("notices"))
        check("口径版本+指纹进报告头",
              d["caliber"]["version"] and d["caliber"]["fingerprint"], d.get("caliber"))
        check("判据矩阵每行齐备",
              all(all(c["cells"].get(k) not in (None, "") for k in ("J01", "J10", "J12"))
                  for c in d.get("matrix") or []) and bool(d.get("matrix")),
              (d.get("matrix") or [{}])[0])

        print("\n【J3】单章详情：缺陷五项齐备")
        code, ch = req("GET", "/judge/chapter?n=1")
        check("200 且带原文分段", code == 200 and len(ch.get("paragraphs") or []) >= 1,
              len(ch.get("paragraphs") or []))
        bad = [x for x in (ch.get("defects") or [])
               if not all(k in x for k in ("chapter", "paragraph_index", "sentence",
                                           "rule_id", "count"))]
        check("每条缺陷都有 chapter/段号/原句/rule_id/count", not bad, bad[:1])
        check("未勾事件时结论为 null", ch["verdict"]["verdict"] is None
              and ch["verdict"]["status"] == "待作者勾选", ch["verdict"]["status"])

        print("\n【J4】作者勾选 → 结论出现档位")
        code, ev = req("POST", "/judge/events",
                       {"chapter": 1, "checked": [1, 2, 3, 4],
                        "classes": {"1": "信息占有", "2": "关系"},
                        "evidence": "第 12 段"})
        check("勾选 200 且记 4 件事", code == 200 and ev["events"]["events"] == 4, ev)
        code, ch2 = req("GET", "/judge/chapter?n=1")
        check("勾完结论不再是 null", ch2["verdict"]["verdict"] is not None,
              ch2["verdict"]["status"])
        check("事件行带勾选与分类", any(r["checked"] for r in ch2["outline_events"]),
              ch2["outline_events"][:2])

        print("\n【J5】逐句裁定：理由必填")
        code, d = req("POST", "/judge/sentence",
                      {"chapter": 1, "paragraph_index": 3,
                       "sentence": "他把笔尖朝上，放回笔槽。", "feeling": "打中了", "reason": ""})
        check("无理由 → 400", code == 400, (code, d.get("error")))
        code, d = req("POST", "/judge/sentence",
                      {"chapter": 1, "paragraph_index": 3,
                       "sentence": "他把笔尖朝上，放回笔槽。", "feeling": "打中了",
                       "reason": "读到这儿停了"})
        check("有理由 → 200 且落库", code == 200 and d["verdict"]["paragraph_index"] == 3, d)

        print("\n【J6】口径：版本 / 消费方 / 改值产新版本")
        code, cal = req("GET", "/judge/caliber")
        check("每条判据都有消费方", code == 200 and all(c["consumer"] for c in cal["criteria"]),
              [c for c in cal["criteria"] if not c["consumer"]][:1])
        v0 = cal["version"]
        code, d = req("POST", "/judge/caliber", {"set": {"J10": 0.25}})
        check("改阈值 → 版本 +1", code == 200 and d["version"] == v0 + 1, d.get("version"))
        code, cal2 = req("GET", "/judge/caliber")
        check("历史里留着老版本", cal2["history"][-1]["version"] == v0, cal2["history"])

        print("\n【J7】三稿对照：缺版明说，不记 0")
        code, d = req("GET", "/judge/destroy?n=1")
        check("200 且缺版语义", code == 200 and d["destroy"]["present"]["checked"] is False
              and d["destroy"]["words"]["checked"] is None, d.get("destroy", {}).get("present"))

        print("\n【J9】改口径影响面预览（一个字都不写）")
        code, pv = req("POST", "/judge/caliber/preview", {"set": {"J10": 0.99}})
        check("200 且三行分列", code == 200 and pv.get("ok")
              and all(k in pv for k in ("affected_count", "to_fail_count", "manual_count")), pv)
        code, pv2 = req("POST", "/judge/caliber/preview", {"set": {"J12": 3}})
        check("改不可自动判定判据 → 需你重录 1 条", pv2.get("manual_count") == 1, pv2)

        print("\n【J10】成品过期检测（没成品 → 未生成，不是已过期）")
        code, art = req("GET", "/judge/artifact")
        check("200 且 status=未生成", code == 200 and art.get("exists") is False
              and art.get("status") == "未生成", art)

        print("\n【J11】导出清单（显式动作）")
        code, ex = req("POST", "/judge/export", {})
        check("导出 200 且文件落盘",
              code == 200 and (tmp / "output" / "judge_report.md").exists(), ex)

        print("\n【J8】零 token：端点源码不含模型客户端")
        src = (tmp / "scripts" / "nf_api_domains" / "judge.py").read_text(encoding="utf-8")
        check("judge 域不 import 模型客户端",
              "llm_client" not in src and "make_client(" not in src)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for n in FAIL:
        print("    -", n)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
