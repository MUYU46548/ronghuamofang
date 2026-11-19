# -*- coding: utf-8 -*-
"""全部 GET 端点在一个**空种子工作区**上都不许 500。

## 这条判据守的是什么

500 = 程序故障。缺参数、没配 vault、目录不存在、还没跑过阶段 —— 这些是
**使用者能行动的前置条件**，该给 400 + 一句能照做的话，或 200 + 空数据。
把它们包成 500 的后果是：前端只能显示“服务器错误”，而真正的原因（该去哪儿填）
就丢在响应体里没人看。

2026-11-19 把 301 条 GET 全扫一遍，逮到两条：
  · `/outline/discussion/draft`（不带 path）—— `Path(root) / ""` 就是根目录本身，
    `exists()` 为 True，接着 `read_text(目录)` 抛 PermissionError → 500。
  · `/config/vaults/scan` —— 没配 `obsidian.vault_path` 时 ValueError 被兜成 500，
    而那句报错里正写着“请在 config/system.yaml 的 obsidian.vault_path 填写”。
两条都改成 400 后，同一轮扫描 500 归零。

## 为什么是“全扫”而不是“点名几个”

界面上真正的首屏端点会变。点名写死的清单只会测到它自己；从源码抓路由，
新增端点自动进扫描面 —— 新写的 handler 一返回 500 就会被这条拦住。

## 会假绿的地方（都做了防）

1. 正则失效 → 抓到 0 条 → 空列表恒通过。所以先断言“抓到 ≥100 条”。
2. 服务没起来 → 每个请求都是连接错误，若只统计 500 也会通过。所以连接错误单独算红。
3. 阶段没跑过时某些端点本来就该 400/404 —— 那不是问题；本用例**只管 500**。

用法：python tests/http/test_get_endpoints_no_500.py
"""
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
API_PY = ROOT / "scripts" / "nf_api.py"
DOM_DIR = ROOT / "scripts" / "nf_api_domains"

PASS, FAIL = [], []

# 必须放行的 500（留空 = 一条都不许）。要加就写清“为什么这里 500 是对的”，
# 否则下一个人只会复制这行把它当惯例。
ALLOW_500 = {}

# 明显会写盘 / 起 job 的路径（它们本该是 POST；出现在 GET 面里说明抓错了）
WRITE_HINTS = ("/save", "/ack", "/stop", "/run", "/delete", "/archive", "/write",
               "/resolve", "/switch", "/apply", "/merge")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def collect_get_routes():
    """从源码抓 GET 路由：域表里的 ("GET", "/x") + nf_api 的 elif p == "/x" / startswith。"""
    paths = set()
    pat_tbl = re.compile(r'\(\s*"GET"\s*,\s*"([^"]+)"')
    pat_eq = re.compile(r'elif\s+p\s*==\s*"([^"]+)"')
    pat_sw = re.compile(r'elif\s+p\.startswith\(\s*"([^"]+)"')
    for f in [API_PY] + sorted(DOM_DIR.glob("*.py")):
        try:
            src = f.read_text(encoding="utf-8")
        except OSError:
            continue
        paths.update(pat_tbl.findall(src))
        paths.update(pat_eq.findall(src))
        for pre in pat_sw.findall(src):
            paths.add(pre if pre.endswith("/") else pre + "/")
    out = []
    for p in sorted(paths):
        if any(h in p for h in WRITE_HINTS):
            continue
        p = re.sub(r"\{[^}]+\}", "1", p)
        if not p.startswith("/"):
            continue
        if p.endswith("/"):
            p += "1"
        out.append(p)
    return sorted(set(out))


def req(base, path, method="GET", timeout=25):
    r = urllib.request.Request(base + path, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]
    except Exception as e:                                   # noqa: BLE001
        return -1, ("%s: %s" % (type(e).__name__, e)).encode()[:200]


def main():
    tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="nf_get_sweep_")))
    (tmp / "config").mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    shutil.copy2(ROOT / "config" / "project.yaml", tmp / "config" / "project.yaml")

    routes = collect_get_routes()
    print("\n【P1】路由抓取本身有效")
    # ≥100 是“正则还认得出这批代码”的下限；数量掉了说明抓取逻辑该修了，
    # 而不是“端点变少了所以全绿”。
    check("抓到 ≥100 条 GET 路由（实际 %d）" % len(routes), len(routes) >= 100,
          "抓取正则可能失效 —— 那样后面的检查全是空转")

    port = free_port()
    base = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env.pop("NF_ROOT", None)
    env["NF_LICENSE_ACK"] = "1"        # 只测 GET 面，许可闸有它自己的用例
    env["NF_GUI_TOKEN"] = "sweep-token"
    env["PYTHONIOENCODING"] = "utf-8"
    log = tmp / "api.log"
    lf = open(log, "wb")
    proc = subprocess.Popen([PY, str(API_PY), "--port", str(port), "--root", str(tmp)],
                            cwd=str(tmp), env=env, stdout=lf, stderr=subprocess.STDOUT)
    try:
        up = False
        end = time.time() + 25
        while time.time() < end:
            if req(base, "/health")[0] == 200:
                up = True
                break
            time.sleep(0.3)
        print("\n【P2】服务起来了（空种子工作区）")
        check("nf_api 在空 config 上起得来（/health 200）", up,
              log.read_text(encoding="utf-8", errors="replace")[-400:] if not up else "")

        print("\n【P3】%d 条 GET 全扫：不许 500" % len(routes))
        results = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(req, base, p): p for p in routes}
            for fu, p in futs.items():
                results[p] = fu.result()

        codes = {}
        for p, (c, _b) in results.items():
            codes[c] = codes.get(c, 0) + 1
        print("      状态分布: %s" % sorted(codes.items(), key=lambda x: -x[1]))

        conn_err = [(p, b.decode("utf-8", "replace")[:120])
                    for p, (c, b) in results.items() if c == -1]
        check("没有连不上的路由（%d 条）" % len(conn_err), not conn_err, conn_err[:6])

        bad = []
        for p, (c, b) in sorted(results.items()):
            if c != 500:
                continue
            if p in ALLOW_500:
                continue
            bad.append((p, b.decode("utf-8", "replace").replace("\n", " ")[:180]))
        check("没有 500（%d 条）" % len(bad), not bad,
              "\n".join("  %s  %s" % (p, t) for p, t in bad[:10]))

        print("\n【P4】两条已知的“配置缺失”不再包成 500")
        c, b = req(base, "/config/vaults/scan")
        check("/config/vaults/scan 没配 vault 时是 400（带该填哪儿）",
              c == 400 and "未配置" in b.decode("utf-8", "replace"),
              (c, b.decode("utf-8", "replace")[:120]))
        c, b = req(base, "/outline/discussion/draft")
        check("/outline/discussion/draft 不带 path 时是 400（不是 500）",
              c == 400, (c, b.decode("utf-8", "replace")[:120]))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:                                    # noqa: BLE001
            try:
                proc.kill()
            except Exception:                                # noqa: BLE001
                pass
        lf.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n======== %d 通过 / %d 失败 ========" % (len(PASS), len(FAIL)))
    if FAIL:
        for n in FAIL:
            print("  失败：" + n)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
