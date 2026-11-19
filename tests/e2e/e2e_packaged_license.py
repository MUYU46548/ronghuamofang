# -*- coding: utf-8 -*-
"""打包态许可协议真机一次（不启动 Electron 窗口）。

钉什么（问题总账 A-1 那一半能在无人值守下跑完的部分）：
P1. `console/dist/win-unpacked` 里的**打包产物**确实存在（exe / payload / runtime python）
P2. 打包 payload 的许可条款版本与源码同值 —— 抓"改了没重打包"
P3. 打包 runtime 的 python 能把打包 payload 的 `nf_api.py` 起起来（真进程，不是 mock）
P4. 未签状态：`/health` 通、`/license` 说 acked=false、其余端点 403 license_required
P5. Agent 代签被拒（无 GUI 双因子 → 403）；有头但不带 agree → 400
P6. 带 GUI 双因子 + agree → 200，且同意记录落在 **workspace/config/license_ack.json**
P7. 签后同一个进程里 `/state` 不再 403
P8. 重启 nf_api（同一个 workspace）→ 仍然 acked（同意是服务端的事，不跟浏览器 profile）
P9. 换一个空 workspace → acked=false（不串库）

**不覆盖**：Electron 主进程发令牌、弹窗点击。那两步要真窗口，见脚本末尾提示。

用法：python tests/e2e/e2e_packaged_license.py
前置：先在 console/ 里 `npm run pack`。
"""
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

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "console" / "dist" / "win-unpacked"
PAYLOAD = PKG / "resources" / "payload"
PY = PKG / "resources" / "runtime" / "python" / "python.exe"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def req(url, method="GET", body=None, headers=None, timeout=15):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:                                    # noqa: BLE001
            return e.code, {}


def wait_port(port, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_api(ws, port, token):
    env = dict(os.environ)
    env["NF_ROOT"] = str(ws)
    env["NF_GUI_TOKEN"] = token          # 与主进程下发同款：子进程环境变量
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("NF_LICENSE_ACK", None)      # 测试豁免不能混进来
    pr = subprocess.Popen(
        [str(PY), str(PAYLOAD / "scripts" / "nf_api.py"),
         "--port", str(port), "--root", str(ws)],
        cwd=str(PAYLOAD), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return pr


def stop(pr):
    try:
        pr.terminate()
        pr.wait(timeout=10)
    except Exception:                                        # noqa: BLE001
        try:
            pr.kill()
        except Exception:                                    # noqa: BLE001
            pass


def main():
    print("=== P1. 打包产物在位 ===")
    exe = PKG / "绒花墨坊.exe"
    check("装了 win-unpacked（exe 在）", exe.exists(), exe)
    check("payload 在", (PAYLOAD / "scripts" / "nf_api.py").exists(), PAYLOAD)
    check("打包 runtime 的 python 在", PY.exists(), PY)
    if not (exe.exists() and (PAYLOAD / "scripts" / "nf_api.py").exists() and PY.exists()):
        print("\n[SKIP] 没有打包产物 —— 先在 console/ 里 npm run pack")
        return 0

    print("\n=== P2. 打包 payload 与源码同源（防“改了没重打包”）===")
    sys.path.insert(0, str(ROOT / "scripts"))
    from utils import license as src_lic                          # noqa: E402
    pkg_lic = (PAYLOAD / "scripts" / "utils" / "license.py").read_text(encoding="utf-8")
    same_ver = ('LICENSE_VERSION = "%s"' % src_lic.LICENSE_VERSION) in pkg_lic
    same_sha = ('LICENSE_CONTENT_SHA = "%s"' % src_lic.LICENSE_CONTENT_SHA) in pkg_lic
    check("payload 的条款版本与源码同值", same_ver,
          "源码 %s" % src_lic.LICENSE_VERSION)
    check("payload 的正文指纹与源码同值", same_sha,
          "源码 %s" % src_lic.LICENSE_CONTENT_SHA)

    token = "pkge2e-gui-token"
    ws = Path(tempfile.mkdtemp(prefix="nf_pkg_license_"))
    # 复刻主进程 seedWorkspace 的**最小**结果：config/ 逐文件补缺（data/ 不造）。
    # 不这么做的话 `/state` 会因为读不到 config/system.yaml 抛 500 —— 那是"裸目录"
    # 的事，不是打包态真实首启的样子（首启时主进程先播种再起 API）。
    shutil.copytree(PAYLOAD / "config", ws / "config",
                    ignore=shutil.ignore_patterns("history"))
    procs = []
    port = free_port()
    try:
        print("\n=== P3. 真进程起得来（打包 runtime + 打包 payload）===")
        procs.append(start_api(ws, port, token))
        up = wait_port(port)
        check("nf_api 在打包 runtime 下起来了", up, "port=%d" % port)
        if not up:
            return 1
        api = "http://127.0.0.1:%d" % port

        print("\n=== P4. 未签状态 = 全站关闸 ===")
        code, res = req(api + "/health")
        check("/health 通（探活不被闸拦）", code == 200, (code, res))
        code, lic = req(api + "/license")
        check("/license 说未签（acked=false）",
              code == 200 and lic.get("acked") is False, (code, lic))
        check("/license 报出要求的版本与源码同值",
              (lic.get("required") or {}).get("version") == src_lic.LICENSE_VERSION,
              lic.get("required"))
        code, res = req(api + "/state")
        check("业务端点被闸住（/state 403 license_required）",
              code == 403 and res.get("license_required") is True, (code, res))

        print("\n=== P5. 代签被拒（双因子 + 显式 agree，缺一不可）===")
        code, res = req(api + "/license/ack", "POST", {"agree": True})
        check("无 GUI 头 → 403（外部 Agent 不能代签）",
              code == 403 and "桌面端" in str(res.get("error")), (code, res))
        code, res = req(api + "/license/ack", "POST", {}, {
            "X-Mofang-Source": "gui", "X-Mofang-Token": token})
        check("有 GUI 头但不带 agree → 400（不默认识为同意）",
              code == 400, (code, res))

        print("\n=== P6. 签字落盘到 workspace ===")
        code, res = req(api + "/license/ack", "POST", {"agree": True}, {
            "X-Mofang-Source": "gui", "X-Mofang-Token": token})
        check("带双因子 + agree → 200", code == 200 and res.get("ok") is True,
              (code, res))
        ackf = ws / "config" / "license_ack.json"
        check("同意记录落在 workspace/config/license_ack.json", ackf.exists(), ackf)
        ack = {}
        if ackf.exists():
            ack = json.loads(ackf.read_text(encoding="utf-8"))
        check("记录字段齐（product/version/sha/at/source=gui）",
              ack.get("product") == "绒花墨坊"
              and ack.get("version") == src_lic.LICENSE_VERSION
              and ack.get("sha") == src_lic.LICENSE_CONTENT_SHA
              and bool(ack.get("at")) and ack.get("source") == "gui", ack)

        print("\n=== P7. 签后放行 ===")
        code, res = req(api + "/state")
        check("同进程 /state 不再 403", code == 200, (code, str(res)[:120]))

        print("\n=== P8. 重启后仍然有效（服务端语义，不跟浏览器走）===")
        stop(procs.pop())
        time.sleep(0.5)
        port2 = free_port()
        procs.append(start_api(ws, port2, token))
        ok2 = wait_port(port2)
        check("重启后 nf_api 起来", ok2, "port=%d" % port2)
        if ok2:
            code, lic2 = req("http://127.0.0.1:%d/license" % port2)
            check("重启后 /license 仍 acked=true（且不 stale）",
                  code == 200 and lic2.get("acked") is True
                  and lic2.get("stale") is False, (code, lic2))

        print("\n=== P9. 不串库 ===")
        ws2 = Path(tempfile.mkdtemp(prefix="nf_pkg_license_other_"))
        port3 = free_port()
        procs.append(start_api(ws2, port3, token))
        ok3 = wait_port(port3)
        check("另一个空工作区能起", ok3, "port=%d" % port3)
        if ok3:
            code, lic3 = req("http://127.0.0.1:%d/license" % port3)
            check("另一个工作区仍是未签（同意不跟着进程/用户走）",
                  code == 200 and lic3.get("acked") is False, (code, lic3))
        shutil.rmtree(ws2, ignore_errors=True)
    finally:
        for pr in procs:
            stop(pr)
        shutil.rmtree(ws, ignore_errors=True)

    print("\n" + "=" * 66)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for n in FAIL:
        print("    -", n)
    print("=" * 66)
    print("\n未覆盖（要真窗口，2 步）：启动包装好的 exe → 弹窗滚到底勾选同意 → 看界面上"
          "\n章节页能点开、且 %APPDATA%\\绒花墨坊\\workspace\\config\\license_ack.json 出现。")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
