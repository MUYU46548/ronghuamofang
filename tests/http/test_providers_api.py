# -*- coding: utf-8 -*-
"""服务商端点 HTTP 端到端自检（/providers | /providers/save | /providers/remove）。

**不碰真实仓库数据**：把 `scripts/ config/ prompts/` 复制到临时目录当 ROOT 起 nf_api，
所有读写都落在临时目录，真实 `data/` 与 `config/system.yaml` 完全不受影响。

## 为什么必须真机起服务（而不是只测 handler）

按开发卡“新增端点 / GUI 入口的标准链路”，handler 级用例测不出这四件事：

1. `api.ROOT` 有没有被拷死（`--root` 换根后还写旧目录）；
2. `do_POST` 有没有把 body 传进去；
3. 路由有没有真的接上（GET 链 / POST 链各一处）；
4. **双因子闸**：无令牌 403、带对了令牌 200 —— 这条只有真 HTTP 才验得出。

## 判据

- S1 `GET /providers` → 200，带 items 与 ≥10 家预设
- S2 只读视图不吐明文 Key（只给变量名 + 掩码）
- S3 `POST /providers/save` 无令牌 → 403，且 `code` 是三种里的某一种
- S4 带双因子 → 200，且 `config/system.yaml` 里真出现该 provider
- S5 **注释一个都没丢**（`yaml.dump` 整份覆盖会全没，这是本条的存在意义）
- S6 非法参数（大写 id / 裸域名 / 带 /chat/completions）→ 400
- S7 删在用的 provider → 400（要先切模型）
- S8 删没在用的 → 200，盘上真消失
- S9 agent_mode 下：非 GUI 写 → 403（禁用名单）；只读 `GET /providers` 仍 200

用法：python tests/http/test_providers_api.py
"""
import io
import json
import os
import re
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
PORT = 8919
BASE = "http://127.0.0.1:%d" % PORT
GUI_TOKEN = "providers-selftest-token"
FAKE_KEY = "sk-providerselftest0123456789"

PASS, FAIL = [], []

# 换行符常量化：本文件要拼多行字符串，直接写反斜杠 n 会被层层转义吃掉
NL = chr(10)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("" if cond else "  → %s" % (str(detail)[:180],))))


def req(method, path, body=None, timeout=20, gui=False, source="agent"):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    headers = {"Content-Type": "application/json", "X-Mofang-Source": source}
    if gui:
        headers["X-Mofang-Source"] = "gui"
        headers["X-Mofang-Token"] = GUI_TOKEN
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                     # noqa: BLE001
            payload = {}
        return e.code, payload
    except Exception as e:                                    # noqa: BLE001
        return 0, {"error": type(e).__name__ + ": " + str(e)[:80]}


def force_agent_mode(path, on):
    """把 gates.agent_mode 显式设成指定值（夹具不许继承仓库配置）。

    用户试跑时把它开成 true 了；照抄过来会让 S3 的 403 来自“禁用名单”
    而不是“双因子闸”，断言 code 就变成假红。做法刻意用逐行文本替换而不是
    yaml 解析重写 —— S5 要靠 system.yaml 里的注释，整份 dump 会把它们全丢掉。
    那行行尾带注释（agent_mode: true   # 外部 Agent 模式…），所以按行重建并
    原样保留尾部注释。
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    want = "true" if on else "false"
    hit = False
    for i, ln in enumerate(lines):
        st = ln.strip()
        if not st.startswith("agent_mode:"):
            continue
        indent = ln[:len(ln) - len(ln.lstrip())]
        rest = st[len("agent_mode:"):].strip()
        tail = ""
        if "#" in rest:
            tail = "   " + rest[rest.index("#"):]
        lines[i] = indent + "agent_mode: " + want + tail
        hit = True
    if not hit:
        for i, ln in enumerate(lines):
            if ln.strip() == "gates:":
                lines.insert(i + 1, "  agent_mode: " + want)
                break
    path.write_text(NL.join(lines) + NL, encoding="utf-8")


def build_root(agent_mode=False):
    tmp = Path(tempfile.mkdtemp(prefix="providers_api_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "materials" / "raw" / "01.md").write_text("夹具素材。" + NL, encoding="utf-8")
    (tmp / "logs").mkdir()
    force_agent_mode(tmp / "config" / "system.yaml", agent_mode)
    return tmp

def start(root):
    env = dict(os.environ)
    env["NF_GUI_TOKEN"] = GUI_TOKEN
    env["TOKENHUB_API_KEY"] = FAKE_KEY   # S2 要验掩码，必须在**服务进程**里可见
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("NF_ROOT", None)
    p = subprocess.Popen(
        [PY, str(root / "scripts" / "nf_api.py"), "--root", str(root), "--port", str(PORT)],
        cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        code, _b = req("GET", "/health")
        if code == 200:
            return p
        time.sleep(0.3)
    p.terminate()
    raise RuntimeError("nf_api 起不来（端口 %d 被占？）" % PORT)


def main():
    print("=" * 66)
    print("服务商端点 HTTP 自检")
    root = build_root()
    proc = None
    try:
        proc = start(root)
        sy = root / "config" / "system.yaml"
        before = sy.read_text(encoding="utf-8")
        comments_before = [ln for ln in before.splitlines() if ln.strip().startswith("#")]

        print("\n【S1】GET /providers（只读，Agent 也能盘点）")
        code, d = req("GET", "/providers")
        check("S1 200", code == 200, (code, d))
        items = {it["id"]: it for it in (d.get("items") or [])}
        check("S1 现有服务商在清单里", "tokenhub" in items and "longcat" in items,
              sorted(items))
        check("S1 预设 ≥10 家", len(d.get("presets") or []) >= 10,
              len(d.get("presets") or []))
        th = items.get("tokenhub") or {}
        check("S1 带 in_use_by（形状：字符串列表）",
              isinstance(th.get("in_use_by"), list)
              and all(isinstance(x, str) for x in th["in_use_by"]),
              th.get("in_use_by"))
        check("S1 带 active 映射（谁在用谁，由 provider_store 单一真源给）",
              isinstance(d.get("active"), dict), d.get("active"))

        print("\n【S2】只读视图不吐明文 Key")
        os.environ["TOKENHUB_API_KEY"] = FAKE_KEY
        code, d2 = req("GET", "/providers")
        items2 = {it["id"]: it for it in (d2.get("items") or [])}
        check("S2 有 key 就标 has_key", (items2.get("tokenhub") or {}).get("has_key") is True,
              items2.get("tokenhub"))
        check("S2 响应里没有明文 Key", FAKE_KEY not in json.dumps(d2, ensure_ascii=False))

        print("\n【S3】无 GUI 双因子 → 403（并说清是哪一种拒）")
        code3, d3 = req("POST", "/providers/save", {
            "id": "httpvendor", "base_url": "https://http.example.com/v1",
            "api_key_env": "HTTPVENDOR_API_KEY"}, source="agent")
        check("S3 403", code3 == 403, (code3, d3))
        check("S3 带 code（三态之一）",
              d3.get("code") in ("NOT_GUI", "GUI_TOKEN_MISSING", "GUI_TOKEN_STALE"), d3)
        check("S3 被拒时没写盘", "  httpvendor:" not in sy.read_text(encoding="utf-8"))

        print("\n【S4】带双因子 → 200，且真写进 config/system.yaml")
        code4, d4 = req("POST", "/providers/save", {
            "id": "httpvendor", "base_url": "https://http.example.com/v1",
            "api_key_env": "HTTPVENDOR_API_KEY", "models": "hv-1, hv-2"}, gui=True)
        check("S4 200", code4 == 200 and d4.get("ok"), (code4, d4))
        text4 = sy.read_text(encoding="utf-8")
        check("S4 配置里出现该块", "  httpvendor:" in text4, text4[:120])
        check("S4 模型清单按逗号拆成列表", "hv-1" in text4 and "hv-2" in text4)
        code4b, d4b = req("GET", "/providers")
        check("S4 列表里能看到它",
              any(it["id"] == "httpvendor" for it in (d4b.get("items") or [])), d4b)

        print("\n【S5】注释一个都没丢（yaml.dump 整份覆盖就会全没）")
        comments_after = [ln for ln in text4.splitlines() if ln.strip().startswith("#")]
        check("S5 注释行数没变少", len(comments_after) >= len(comments_before),
              (len(comments_before), len(comments_after)))
        check("S5 tokenhub 段里那条 ⚠️ 注释还在",
              any("刻意留空" in ln for ln in comments_after), comments_after[:3])

        print("\n【S6】非法参数 → 400")
        for label, body in [
            ("大写 id", {"id": "HttpVendor", "base_url": "https://a.com/v1",
                         "api_key_env": "A_OK_KEY"}),
            ("裸域名", {"id": "okone", "base_url": "a.com/v1", "api_key_env": "A_OK_KEY"}),
            ("带 /chat/completions", {"id": "oktwo",
                                      "base_url": "https://a.com/v1/chat/completions",
                                      "api_key_env": "A_OK_KEY"}),
            ("env 小写", {"id": "okthree", "base_url": "https://a.com/v1",
                          "api_key_env": "my_key"}),
        ]:
            c, dd = req("POST", "/providers/save", body, gui=True)
            check("S6 拒收：%s" % label, c == 400 and dd.get("error"), (c, dd))

        print("\n【S7】删在用的 → 400")
        c7, d7 = req("POST", "/providers/remove", {"id": "tokenhub"}, gui=True)
        check("S7 400 且说清被谁用", c7 == 400 and "正在被" in str(d7.get("error")), (c7, d7))

        print("\n【S8】删没在用的 → 200，盘上真消失")
        c8, d8 = req("POST", "/providers/remove", {"id": "httpvendor"}, gui=True)
        check("S8 200", c8 == 200 and d8.get("ok"), (c8, d8))
        check("S8 盘上不留残块", "  httpvendor:" not in sy.read_text(encoding="utf-8"))
        c8b, d8b = req("POST", "/providers/remove", {"id": "nope"}, gui=True)
        check("S8 删不存在的 → 400", c8b == 400, (c8b, d8b))

        print("\n【S10】GET /whoami：服务端认为你是谁")
        c10, d10 = req("GET", "/whoami", source="agent")
        check("S10 200", c10 == 200 and d10.get("ok"), (c10, d10))
        check("S10 无令牌 → trusted_gui False 且给出拒因",
              d10.get("trusted_gui") is False and d10.get("refusal_code"), d10)
        check("S10 不吐令牌值（只报有无）",
              GUI_TOKEN not in json.dumps(d10, ensure_ascii=False))
        c10b, d10b = req("GET", "/whoami", gui=True)
        check("S10 带对令牌 → trusted_gui True",
              d10b.get("trusted_gui") is True and d10b.get("refusal_code") is None, d10b)
        check("S10 报出引擎与 agent_mode（现场可核）",
              isinstance(d10b.get("engine"), str)
              and isinstance(d10b.get("agent_mode"), bool),
              {k: d10b.get(k) for k in ("engine", "agent_mode")})
    finally:
        os.environ.pop("TOKENHUB_API_KEY", None)
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(root, ignore_errors=True)

    print("\n【S9】agent_mode 下：写端点被禁用名单拦，只读端点照旧")
    root2 = build_root(agent_mode=True)
    proc2 = None
    try:
        proc2 = start(root2)
        c9, d9 = req("POST", "/providers/save", {
            "id": "agentvendor", "base_url": "https://ag.example.com/v1",
            "api_key_env": "AGENTVENDOR_API_KEY"}, source="agent")
        check("S9 非 GUI 写 → 403", c9 == 403, (c9, d9))
        check("S9 被拦时没写盘",
              "  agentvendor:" not in (root2 / "config" / "system.yaml").read_text(encoding="utf-8"))
        c9b, d9b = req("GET", "/providers", source="agent")
        check("S9 只读 GET 仍 200（Agent 可盘点）", c9b == 200, (c9b, d9b))
    finally:
        if proc2:
            proc2.terminate()
            try:
                proc2.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc2.kill()
        shutil.rmtree(root2, ignore_errors=True)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
