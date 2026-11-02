# -*- coding: utf-8 -*-
"""事件勾选（F03b / I3）的后端契约：轻读、两个计数、字面提示的边界。

## 为什么单独立一条

I3 把\u300c这一章发生了几件事\u300d从判定详情搬到**分章页**（04-交互定义的原位置）。
搬家带来一个具体性能要求：分章页每次切章都要知道\u300c本章章纲有几条事件、我勾了没\u300d，
**不能**为此跑 `/judge/chapter`（那会算三档、三稿对照、骨架命中、缺陷单）。
所以 `/judge/events?outline=1` 必须是一条轻读路径。

同时钉住两个容易被"顺手统一"掉的判据：

1. **勾了几条 ≠ 计入下限几条**（R24 AC-24-2：没有证据句的落\u300c无举证\u300d，不计入）。
   界面必须能同时拿到 `events` 与 `counted_events`，否则用户看到一个数字被悄悄改了语义。
2. **\u300c像同一场戏\u300d只按字面重合提示**，抓不到就不报 —— 这条能力**没有**语义理解，
   文档和 UI 都不许写成"系统看得出是不是同一场戏"（见 E5 的负例）。

用法：python tests/http/test_judge_events_http.py
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
PORT = 8953
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []

BODY = """## 第1章 夜雨

他在档案室盖章。他把回执压平，拿起印章，在纸角上按下去。

他核对回执上的编号。他一行一行往下看，看到第三行停住。

他在日志上划改。他把笔尖朝上，放回笔槽。

他抽出一张字条。他把字条摊在桌上，压在回执底下。
"""

# 章 1：四条互不相似的事件；章 2：两条字面高度重合（用来验证提示的**正例**）；
# 章 3：没有“核心事件”段（验证 0 事件的空态）
OUTLINE_1 = """## 第1章 夜雨
- 核心事件：
  1. 他在档案室盖章
  2. 他核对回执编号
  3. 他在日志上划改
  4. 他发现一张字条
- 涉及角色：沈砚
"""

OUTLINE_2 = """## 第2章 井台
- 核心事件：
  1. 他在井台投下一枚铜钱
  2. 他在井台投下一枚铜钱又捞起来
  3. 他去码头找船
- 涉及角色：沈砚
"""

OUTLINE_3 = """## 第3章 归途
- 涉及角色：沈砚
- 功能：收束
"""

MIN_YAML = """book:
  name: "事件勾选验收"
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
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        "X-Mofang-Source": "gui"})
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
    tmp = Path(tempfile.mkdtemp(prefix="nf_ev_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    for d in ("data/state", "data/setting", "data/chapters/raw",
              "data/outline/chapters", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    outlines = {1: OUTLINE_1, 2: OUTLINE_2, 3: OUTLINE_3}
    for n in range(1, 4):
        (tmp / "data" / "chapters" / "raw" / ("%02d.md" % n)).write_text(
            BODY.replace("第1章", "第%d章" % n), encoding="utf-8")
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            outlines[n], encoding="utf-8")
    (tmp / "config" / "project.yaml").write_text(MIN_YAML, encoding="utf-8")
    # 纯 HTTP 请求没有来源头 → 关掉 agent_mode，否则 /judge/events 会被
    # agent_guard 正确拦成 403（那是设计，另有专门用例）
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

        print("\n【E1】轻读：分章页要的数据一次拿全（不跑完整分析）")
        code, d = req("GET", "/judge/events?chapter=1&outline=1")
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        evs = d.get("outline_events") or []
        check("解析出 4 条章纲事件", len(evs) == 4, [e.get("text") for e in evs])
        check("未勾选时 checked 全为 false",
              all(e.get("checked") is False for e in evs), evs)
        check("带上 event_min（下限口径，界面要显示 N/2）",
              d.get("event_min") == 2, d.get("event_min"))

        print("\n【E2】两个计数：勾了几条 / 计入下限几条")
        code, d = req("POST", "/judge/events", {
            "chapter": 1,
            "checked": [0, 1, 3],
            "classes": {"0": "信息占有", "1": "信息占有", "3": "目标"},
            "evidences": {"0": "他在档案室盖章", "3": "他抽出一张字条"},
        })
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        ev = d.get("events") or {}
        check("events=3（勾了三条）", ev.get("events") == 3, ev.get("events"))
        check("counted_events=2（其中一条没填证据句，不计入）",
              ev.get("counted_events") == 2, ev.get("counted_events"))
        check("unproven 记下没举证的那条（下标 1）",
              ev.get("unproven") == [1], ev.get("unproven"))

        print("\n【E3】回读一致：勾选/分类/证据/计入标记都落盘了")
        code, d = req("GET", "/judge/events?chapter=1&outline=1")
        evs = d.get("outline_events") or []
        by_n = {e["n"]: e for e in evs}
        check("第 1 条：勾了、分类=信息占有、有证据、计入",
              by_n[1]["checked"] and by_n[1]["class"] == "信息占有"
              and by_n[1]["evidence"] and by_n[1]["counted"] is True, by_n.get(1))
        check("第 2 条：勾了、分类有、无证据 → **不计入**",
              by_n[2]["checked"] and by_n[2]["class"]
              and not by_n[2]["evidence"] and by_n[2]["counted"] is False,
              by_n.get(2))
        check("第 3 条：没勾（系统不代勾）", by_n[3]["checked"] is False, by_n.get(3))

        print("\n【E4】全章 0 事件 → 空列表（界面据此\u300c不可勾选，也不出结论\u300d）")
        code, d = req("GET", "/judge/events?chapter=3&outline=1")
        check("200 且 outline_events 为空数组",
              code == 200 and d.get("outline_events") == [], d.get("outline_events"))

        print("\n【E5】\u300c像同一场戏\u300d：只按字面重合提示，抓不到就不报")
        code, d = req("GET", "/judge/events?chapter=2&outline=1")
        hint = d.get("event_hint") or ""
        check("字面高度重合时给一句提示（第 2 章两条都在井台投铜钱）",
              "同一场戏" in hint and "系统不替你合并" in hint, hint)
        code, d = req("GET", "/judge/events?chapter=1&outline=1")
        hint1 = d.get("event_hint") or ""
        check("字面不重合时**不报**（四条各说一件事）", hint1 == "", hint1)
        check("提示语里写清\u300c系统不替你合并\u300d（能力边界，不许写成系统判得出）",
              "系统不替你合并" in hint, hint)

        print("\n【E6】事件数变了，单章判定要跟着变（不是只存不用）")
        code, d = req("GET", "/judge/chapter?n=1")
        if code == 200:
            v = d.get("verdict") or {}
            check("单章详情里能读到 author.events / counted_events",
                  (d.get("author") or {}).get("events") == 3, d.get("author"))
            check("档位不是 null（已经勾过、分类齐）", v.get("verdict") is not None, v)
        else:
            check("单章详情可读（口径未就绪时接受 400 并说明原因）",
                  code in (200, 400) and (d.get("error") or d.get("ok") is True),
                  (code, d.get("error")))
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
