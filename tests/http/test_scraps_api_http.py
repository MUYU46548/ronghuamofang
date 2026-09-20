# -*- coding: utf-8 -*-
"""碎片端点 HTTP 端到端自检（/scraps/*）。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起 nf_api，
所有读写都落在临时目录，真实 materials/raw 与 data/ 完全不受影响。

覆盖：list（含空目录/不存在目录）、read、save（含写前备份）、delete（确认门 + 快照 + 备份）、
promote（落盘 materials/raw + 覆盖前备份 + 非法类型/非法文件名拦截）。

用法：python tests/test_scraps_api_http.py
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8917
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def req(method, path, body=None, timeout=15):
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
    tmp = Path(tempfile.mkdtemp(prefix="scraps_api_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "materials" / "original_scraps").mkdir(parents=True)
    (tmp / "data").mkdir()
    d = tmp / "materials" / "original_scraps"
    (d / "2024年3月5日 月神那点事.md").write_text(
        "月神这个人，沉默到让人害怕。他造了月兔，却没给他们同类。\n"
        "待定：他左耳那道抓痕是谁留的？以后再想。\n", encoding="utf-8")
    (d / "月兔密室随手写.md").write_text(
        "月兔密室在月兔之城下面。月神几乎不去正殿。\n", encoding="utf-8")
    (d / "买菜找零.txt").write_text("今天买菜，找零少了两块五。\n", encoding="utf-8")
    return tmp


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
                code, _h = req("GET", "/health")
                if code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            print("  [FAIL] 服务未起来")
            return 1

        print("\n=== 1. GET /scraps/list ===")
        code, d = req("GET", "/scraps/list")
        check("200 且 ok", code == 200 and d.get("ok") is True, str(d)[:160])
        check("统计到 3 个碎片", d.get("count") == 3, d.get("count"))
        check("按簇分组返回", len(d.get("clusters") or []) >= 2, d.get("clusters") and
              [c["name"] for c in d["clusters"]])
        check("月神碎片被聚成一簇（自由命名也能聚）",
              any(c["count"] == 2 for c in d.get("clusters") or []),
              [c["count"] for c in d.get("clusters") or []])
        check("前瞻备忘已带出（GUI 提示作者待定事项）",
              d.get("stats", {}).get("lookahead_count", 0) >= 1, d.get("stats"))

        print("\n=== 2. GET /scraps/read ===")
        code, r = req("GET", "/scraps/read?name=" + urllib.parse.quote("月兔密室随手写.md"))
        check("200 且正文可读", code == 200 and "月兔密室" in (r.get("content") or ""),
              str(r)[:120])
        code, r2 = req("GET", "/scraps/read?name=" + urllib.parse.quote("../config/project.yaml"))
        check("路径穿越被拦截", code == 400, (code, str(r2)[:120]))

        print("\n=== 3. POST /scraps/save（写前备份）===")
        target = tmp / "materials" / "original_scraps" / "买菜找零.txt"
        code, s1 = req("POST", "/scraps/save",
                       {"name": "买菜找零.txt", "content": "今天买菜，找零少了三块。\n"})
        check("200 且写入生效", code == 200 and "三块" in target.read_text(encoding="utf-8"),
              str(s1)[:160])
        code, s2 = req("POST", "/scraps/save",
                       {"name": "买菜找零.txt", "content": "又改了一次。\n"})
        check("覆盖写产生备份", code == 200 and s2.get("backup"), str(s2)[:160])
        check("备份文件真实存在", bool(s2.get("backup")) and (tmp / s2["backup"]).exists(),
              s2.get("backup"))
        code, s3 = req("POST", "/scraps/save", {"name": "不存在.md", "content": "x"})
        check("不存在的碎片 → 404", code == 404, str(s3)[:120])

        print("\n=== 4. POST /scraps/promote（信息点 → 素材卡）===")
        code, p1 = req("POST", "/scraps/promote", {
            "card_name": "月神", "card_type": "角色卡",
            "points": [{"content": "月神造了月兔，却没给他们同类",
                        "ts": "2024-03-05", "cluster": "月兔", "confidence": "high"},
                       {"content": "月兔密室在月兔之城下面", "confidence": "low"}],
            "open_questions": ["他左耳那道抓痕是谁留的（来自 月神那点事.md）"],
            "sources": ["2024年3月5日 月神那点事.md（2024-03-05 / filename）"],
        })
        check("200 且返回落盘路径", code == 200 and p1.get("ok"), str(p1)[:200])
        card = tmp / "materials" / "raw" / "月神_角色卡.md"
        check("卡片落到 materials/raw/", card.exists(), str(p1)[:200])
        body = card.read_text(encoding="utf-8") if card.exists() else ""
        check("标题格式与原卡片一致（# 角色：月神）", body.startswith("# 角色：月神"), body[:40])
        check("信息点写进正文", "月神造了月兔" in body)
        check("前瞻备忘进【待定区】而不是被当成事实", "待定区" in body and "抓痕" in body)
        check("来源可溯源", "来源溯源" in body and "月神那点事" in body)
        check("不含类型后缀重复（# 角色：月神_角色卡）", "月神_角色卡" not in body[:40], body[:40])

        code, p2 = req("POST", "/scraps/promote", {
            "card_name": "月神", "card_type": "角色卡", "points": ["改了一条"]})
        check("同名卡片覆盖 → 自动备份", code == 200 and p2.get("backup"), str(p2)[:200])
        check("覆盖后内容已更新",
              "改了一条" in card.read_text(encoding="utf-8"), card.read_text(encoding="utf-8")[:60])

        code, p3 = req("POST", "/scraps/promote",
                       {"card_name": "x", "card_type": "不存在的类型", "points": ["y"]})
        check("非法卡片类型被拦截", code == 400, str(p3)[:160])
        code, p4 = req("POST", "/scraps/promote",
                       {"card_name": "../evil", "card_type": "角色卡", "points": ["y"]})
        check("路径穿越被拦截", code == 400, str(p4)[:160])
        code, p5 = req("POST", "/scraps/promote",
                       {"card_name": "y", "card_type": "角色卡", "points": []})
        check("空信息点被拦截", code == 400, str(p5)[:160])

        print("\n=== 5. POST /scraps/delete（破坏性操作确认门）===")
        code, d1 = req("POST", "/scraps/delete", {"name": "买菜找零.txt"})
        check("未确认 → 400（防误删）", code == 400, str(d1)[:160])
        code, d2 = req("POST", "/scraps/delete", {"name": "买菜找零.txt", "confirm": True})
        check("确认后删除成功", code == 200 and d2.get("ok"), str(d2)[:160])
        check("删除前已另存备份", bool(d2.get("backup")) and (tmp / d2["backup"]).exists(),
              d2.get("backup"))
        check("文件确已移除",
              not (tmp / "materials" / "original_scraps" / "买菜找零.txt").exists())

        print("\n=== 6. 空目录 / 未知路径 ===")
        for f in (tmp / "materials" / "original_scraps").glob("*"):
            if f.is_file():
                f.unlink()
        code, e = req("GET", "/scraps/list")
        check("空目录 → 200 且 count=0（GUI 显示引导文案）",
              code == 200 and e.get("count") == 0, str(e)[:140])
        code, nf = req("GET", "/nope")
        check("未知路径仍 404（未破坏既有路由）", code == 404, str(nf)[:120])

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
