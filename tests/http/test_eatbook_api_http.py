# -*- coding: utf-8 -*-
"""吃书端点 HTTP 自检（0.7.13）：预演/建目录/状态/并库 + 花钱那一步的引擎护栏。

## 判据

A. 预演：只数标题与估算调用数，**一个文件都不写**
B. 建目录：同名已存在 → 409（不覆盖用户上一次的战役）；标题不足 → 400 说清
C. 状态：章数/已摘要/各表条数/运行态（GUI 轮询靠它）
D. 并库：默认只预演（setting.json 一个字节不动）；`yes:true` 才写 + 留备份
E. 引擎护栏：`engine: hermes`（或 fake 客户端）下 `/run` 启动即失败，
   错误里明确说「需要 direct / 用 --dry-run」，**不静默空跑**
F. 目录名越界（`../`）一律 400

全程离线、零 token（A–D 不调模型；E 走 FakeClient，启动即失败）。
用法：python tests/http/test_eatbook_api_http.py
"""
import io
import json
import os
import shutil
import socket
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
PORT = 8947
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []

BOOK = """第一回 夜雨
他推开门，屋檐上的水线连成了串。

第二回 旧信
信上只有半个名字。

第三回 桥头
桥头蹲着两只石兽。
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


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
        except Exception:                                      # noqa: BLE001
            payload = {}
        return e.code, payload


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_eathttp_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    for d in ("data/state", "data/setting", "data/chapters/raw", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "吃书接口验收书"\n  chapters: 3\n', encoding="utf-8")
    (tmp / "old.txt").write_text(BOOK, encoding="utf-8")
    (tmp / "data" / "setting" / "setting.json").write_text(json.dumps({
        "characters": [{"name": "苏芷", "role": "（用户自己写的身份）"}],
        "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
        "plot_fragments": [], "timeline": []}, ensure_ascii=False), encoding="utf-8")
    return tmp


def wait_port(timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def main():
    tmp = build_project()
    proc = subprocess.Popen(
        [PY, "scripts/nf_api.py", "--port", str(PORT), "--root", str(tmp)],
        cwd=str(ROOT),
        env=dict(os.environ, NF_ROOT=str(tmp), NF_API_ALLOW_FAKE="1",
                 PYTHONIOENCODING="utf-8"),
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        ready = wait_port() and req("GET", "/health")[0] == 200
        check("nf_api 起来了", ready)
        if not ready:
            return 1

        print("\n【A】预演：不写盘")
        code, d = req("POST", "/eatbook/preview", {"path": "old.txt"})
        check("预演 200", code == 200 and d.get("ok"), (code, d.get("error")))
        check("数出 3 章", d.get("chapter_count") == 3, d.get("chapter_count"))
        check("给出预计调用数", int(d.get("est_calls") or 0) >= 3, d.get("est_calls"))
        check("预演之后 data/eatbook 还不存在（一个文件都不写）",
              not (tmp / "data" / "eatbook").exists())
        code, d = req("POST", "/eatbook/preview", {"path": "nope.txt"})
        check("书稿不存在 → 404", code == 404, (code, d.get("error")))

        print("\n【B】建目录：拒绝同名、拒绝拆不动")
        code, d = req("POST", "/eatbook/init", {"path": "old.txt", "name": "probe"})
        check("建目录 200", code == 200 and d.get("ok"), (code, d.get("error")))
        check("目录与 manifest 真实存在",
              (tmp / "data" / "eatbook" / "probe" / "manifest.json").exists())
        check("章文件按序落盘",
              (tmp / "data" / "eatbook" / "probe" / "chapters" / "0001.txt").exists())
        code, d = req("POST", "/eatbook/init", {"path": "old.txt", "name": "probe"})
        check("同名再建 → 409（不覆盖）", code == 409, (code, d.get("error")))
        (tmp / "bad.txt").write_text("只有一行\n", encoding="utf-8")
        code, d = req("POST", "/eatbook/init", {"path": "bad.txt", "name": "bad"})
        check("标题不足 → 400 且说清正则", code == 400 and "正则" in d.get("error", ""),
              (code, d.get("error")))
        code, d = req("POST", "/eatbook/init", {"path": "old.txt", "name": "../evil"})
        check("目录名越界 → 400", code == 400, (code, d.get("error")))

        print("\n【C】状态：GUI 轮询靠它")
        code, d = req("GET", "/eatbook/status?name=probe")
        check("状态 200", code == 200 and d.get("ok"), (code, d.get("error")))
        check("章数 3 / 已摘要 0", d.get("chapters") == 3 and d.get("summarized") == 0, d)
        check("各表条数键都在", set(d.get("counts") or {}) >=
              {"characters.json", "settings.json", "timeline.json", "foreshadow.json"}, d.get("counts"))
        code, d = req("GET", "/eatbook/status?name=nope")
        check("没有的目录 → 404", code == 404, code)
        code, d = req("GET", "/eatbook/list")
        check("清单里有 probe", any(i.get("name") == "probe" for i in d.get("items") or []),
              d.get("items"))

        print("\n【D】并库：默认只预演，yes 才写且留备份")
        camp = tmp / "data" / "eatbook" / "probe"
        (camp / "characters.json").write_text(json.dumps([{
            "名字": "苏芷", "身份": "吃书抽的身份", "性格特质": ["冷静"],
            "当前关系": [{"对象": "周砚清", "关系": "旧识", "依据章节": "第一回"}],
            "当前状态": "在查信", "出场章节": ["第一回"], "未决线": "信的另一半",
            "矛盾清单": []}], ensure_ascii=False), encoding="utf-8")
        (camp / "settings.json").write_text(json.dumps([{
            "设定名": "客栈", "类别": "地理", "内容": "河口镇唯一一家",
            "确立章节": ["第一回"], "补充章节": [], "矛盾点": []}], ensure_ascii=False),
            encoding="utf-8")
        setting_p = tmp / "data" / "setting" / "setting.json"
        before = setting_p.read_text(encoding="utf-8")
        code, d = req("POST", "/eatbook/merge", {"name": "probe"})
        check("预演 200", code == 200 and d.get("written") is False, (code, d))
        check("预演不动 setting.json", setting_p.read_text(encoding="utf-8") == before)
        check("预演也如实报新增（同名苏芷不该被算新增）",
              d.get("added_characters") == [] and "客栈" in (d.get("added_world") or []), d)
        code, d = req("POST", "/eatbook/merge", {"name": "probe", "yes": True})
        check("确认合并 200 且 written=true", code == 200 and d.get("written") is True, (code, d))
        after = json.loads(setting_p.read_text(encoding="utf-8"))
        check("同名角色一个字都没动",
              after["characters"][0]["role"] == "（用户自己写的身份）", after["characters"])
        check("新设定进了 world.locations",
              any("客栈" in x for x in after["world"]["locations"]), after["world"])
        hist = tmp / "data" / "setting" / "history"
        check("写前留了备份", hist.is_dir() and any(hist.glob("setting_v*.json")),
              list(hist.glob("*")) if hist.is_dir() else None)

        print("\n【E】花钱那一步的引擎护栏（FakeClient 启动即失败，零 token）")
        code, d = req("POST", "/eatbook/run", {"name": "probe", "stages": [2]})
        check("启动返回 202", code == 202 and d.get("job_id"), (code, d))
        jid = d.get("job_id")
        state = {}
        for _ in range(40):
            time.sleep(0.25)
            c2, j = req("GET", "/jobs/%s" % jid)
            if c2 == 200 and (j.get("state") or "") != "running":
                state = j
                break
        check("任务结束且判失败（不静默空跑）", state.get("state") == "failed", state)
        code, st = req("GET", "/eatbook/status?name=probe")
        check("失败原因写进 run_state（GUI 能看见）",
              (st.get("state") or {}).get("state") == "failed",
              st.get("state"))
        check("失败原因说清要 direct / 预演",
              ("direct" in ((st.get("state") or {}).get("error") or ""))
              or ("_post_chat" in ((st.get("state") or {}).get("error") or "")),
              (st.get("state") or {}).get("error"))
    finally:
        try:
            proc.terminate()
        except Exception:                                      # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 64)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
