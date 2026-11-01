# -*- coding: utf-8 -*-
"""Agent 协作接口：提问 / 提案 / 读正文 / 主动审查（2026-10-31）。

## 这个测试在钉什么

用户三个问题把这批缺口说透了：

> 现在的绒花墨坊真的能发挥外接 agent 的智能吗？agent 的自省和审查有发挥哪怕一点
> 作用吗？能向用户提问吗？

查完的答案是：27 个 MCP 工具全是"操作型"——**能读状态、不能读稿；能操作、不能提案；
不能提问**。四根线补上之后，这里逐根钉住：

- A 组 **能读正文**：`nf_get_chapter` 指向 `/chapters/content`，不是只给分数
- B 组 **能提案**：`nf_plan_propose` 指向 `/plan/propose`，且四态转换仍禁用
- C 组 **能提问**：`POST /ask` → 界面可见 → 人回答 → Agent 读到；**Agent 不能自答**
- D 组 **能主动审查**：`nf_review_chapter` 指向 `/judge/chapter`（零 token 的确定性审查）

用法：python tests/http/test_agent_collab_http.py
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
PORT = 8957
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []


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
    tmp = Path(tempfile.mkdtemp(prefix="nf_agent_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    for d in ("data/state", "data/chapters/raw", "data/outline/chapters",
              "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text(
        "## 第1章 夜雨\n\n他在档案室盖章。他把回执压平，拿起印章。\n\n"
        "他核对回执上的编号。他一行一行往下看，看到第三行停住。\n", encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text(
        "- 核心事件：\n  1. 他在档案室盖章\n- 涉及角色：沈砚\n", encoding="utf-8")
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "协作接口验收"\n  chapters: 1\n', encoding="utf-8")
    import re
    sy = tmp / "config" / "system.yaml"
    txt = sy.read_text(encoding="utf-8")
    sy.write_text(re.sub(r"agent_mode:\s*true", "agent_mode: false", txt),
                  encoding="utf-8")
    return tmp


def static_checks():
    """A/B/D 组：MCP 工具接线（不需要起服务）。"""
    print("\n【A】能读正文（此前 agent 只拿得到分数）")
    sys.path.insert(0, str(ROOT / "scripts"))
    import nf_mcp
    tools = {t["name"]: t for t in nf_mcp.MCP_TOOLS}
    check("有 nf_get_chapter", "nf_get_chapter" in tools)
    check("它指向 /chapters/content（读全文，不是分数）",
          tools.get("nf_get_chapter", {}).get("_http", (None, ""))[1]
          == "/chapters/content", tools.get("nf_get_chapter", {}).get("_http"))
    props = (tools.get("nf_get_chapter", {}).get("inputSchema") or {}).get("properties") or {}
    check("必填章号", "n" in props)
    check("可指定层（raw/checked/refined）", "layer" in props)

    print("\n【B】能提案（后端四态早就有了，此前 MCP 没暴露）")
    check("有 nf_plan_propose", "nf_plan_propose" in tools)
    check("指向 /plan/propose",
          tools.get("nf_plan_propose", {}).get("_http", (None, ""))[1] == "/plan/propose")
    check("有 nf_plan_list（能读回提案状态）", "nf_plan_list" in tools)
    from utils import agent_guard as AG
    for p in ("/plan/select", "/plan/write", "/plan/drop", "/plan/revive"):
        check("%s 仍在 Agent 禁用名单（提案≠拍板）" % p, p in AG.FORBIDDEN_IN_AGENT_MODE)
    check("/plan/propose **不在**名单里（AI 可以提案）",
          "/plan/propose" not in AG.FORBIDDEN_IN_AGENT_MODE)

    print("\n【D】能主动审查（零 token，不跑整个 stage5）")
    check("有 nf_review_chapter", "nf_review_chapter" in tools)
    check("指向 /judge/chapter（确定性缺陷单，不调模型）",
          tools.get("nf_review_chapter", {}).get("_http", (None, ""))[1]
          == "/judge/chapter")


def main():
    static_checks()
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

        print("\n【C】能向用户提问：提问 → 界面可见 → 人回答 → Agent 读到")
        code, d = req("POST", "/ask", {"question": "",
                                       "context": "空问题"})
        check("空问题 400", code == 400, (code, d.get("error")))
        code, d = req("POST", "/ask", {"question": "第 3 章让谁先动手？",
                                       "options": ["沈砚", "老周"],
                                       "context": "两种走向后面十章完全不同"})
        check("提问 200 且拿到 id", code == 200 and d.get("ok") is True and d.get("id"), d)
        qid = d.get("id")
        code, d = req("POST", "/ask", {"question": "要不要把第 2 章并进第 1 章？"})
        check("第二条（无选项）也能提", code == 200, (code, d.get("error")))

        code, pend = req("GET", "/ask/pending")
        check("待回答列表里能看到 2 条", pend.get("count") == 2, pend.get("count"))
        check("问题与选项原样回传（界面要显示）",
              (pend["items"][0].get("question") == "第 3 章让谁先动手？"
               and pend["items"][0].get("options") == ["沈砚", "老周"]),
              pend["items"][0])
        check("落盘不在内存（data/state/questions.json 存在）",
              (tmp / "data" / "state" / "questions.json").is_file())

        code, ans = req("GET", "/ask/answers")
        check("**未回答时 /ask/answers 是空的**（未回答 ≠ 同意）",
              ans.get("count") == 0, ans.get("count"))

        code, d = req("POST", "/ask/answer", {"id": qid, "answer": "老周"})
        check("人回答 200", code == 200 and d.get("ok") is True, d)
        code, ans2 = req("GET", "/ask/answers")
        check("回答后 Agent 读得到", ans2.get("count") == 1, ans2.get("count"))
        check("回答内容正确", ans2["items"][0].get("answer") == "老周", ans2["items"][0])
        check("已答的那条不再出现在 pending",
              all(x.get("id") != qid for x in req("GET", "/ask/pending")[1]["items"]))

        code, d = req("POST", "/ask/answer", {"id": qid, "answer": "又改主意了"})
        check("同一条不能重复回答（400，不静默覆盖）", code == 400, (code, d.get("error")))
        code, d = req("POST", "/ask/answer", {"id": "q_不存在", "answer": "x"})
        check("回答不存在的问题 400/404", code in (400, 404), (code, d.get("error")))

        print("\n【C2】Agent 不得自问自答")
        sys.path.insert(0, str(ROOT / "scripts"))
        from utils import agent_guard as AG2
        check("/ask/answer 在 FORBIDDEN_IN_AGENT_MODE",
              "/ask/answer" in AG2.FORBIDDEN_IN_AGENT_MODE)
        check("/ask（提问端）不在名单（Agent 就该用它）",
              "/ask" not in AG2.FORBIDDEN_IN_AGENT_MODE)

        print("\n【E】读正文与审查真的能出内容")
        code, ch = req("GET", "/chapters/content?n=1")
        check("GET /chapters/content 200", code == 200 and ch.get("ok") is True, code)
        check("带回正文（agent 终于看得到写了什么）",
              "档案室盖章" in (ch.get("content") or ""), (ch.get("content") or "")[:30])
        code, rv = req("GET", "/judge/chapter?n=1")
        check("GET /judge/chapter 200（= nf_review_chapter 的数据源）",
              code == 200 and rv.get("ok") is True, code)
        check("带回结构化缺陷或明确为空",
              isinstance(rv.get("defects"), list), list(rv.keys()))
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
