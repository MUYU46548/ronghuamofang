# -*- coding: utf-8 -*-
"""许可协议总闸的 HTTP 侧（2026-11-18）：**未确认时整站只放行三条**。

判据在 `scripts/utils/license.py`，本用例从外面看行为：

  H1 未确认：`GET /license` 200 且 acked=false；`GET /health` 200（探活要活）；
     其余 GET/POST 一律 403 `license_required`（含 `/state`、`/stage/4/run`）
  H2 签字端点不能被代签：普通 POST、伪造 `X-Mofang-Source: gui` 都 403
     （真 GUI 需要主进程签发 token —— 这条与审批门同一份双因子判据）
  H3 缺 `agree: true` → 400，不默认为同意
  H4 服务端一旦有同意记录（此处直接写文件模拟“用户签过”）→ `/state` 立刻 200
     （证明闸读的是那条记录，不是内存里的开关）
  H5 测试豁免：`NF_LICENSE_ACK=1` 时全站放行，且 `/license` 如实标 bypass

反证（会变红）：把 `nf_api._do_GET_raw` / `do_POST` 顶部的许可判定删掉 → H1/H3 红。

用法：python tests/http/test_license_http.py
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
PORT = 8958
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def req(method, path, body=None, extra_headers=None, timeout=30):
    data = (json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    headers = {"Content-Type": "application/json"}
    headers.update(extra_headers or {})
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                    # noqa: BLE001
            payload = {}
        return e.code, payload


def start(env_extra):
    env = dict(os.environ)
    env.pop("NF_ROOT", None)
    env.pop("NF_LICENSE_ACK", None)     # 官方运行器会给测试注入豁免 —— 本用例要的是真行为
    env.update(env_extra)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.Popen([PY, str(ROOT / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--root", str(TMP)],
                            cwd=str(TMP), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def wait_up(seconds=25):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if req("GET", "/health")[0] == 200:
                return True
        except Exception:                                    # noqa: BLE001
            pass
        time.sleep(0.4)
    return False


def stop(proc):
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:                                        # noqa: BLE001
        try:
            proc.kill()
        except Exception:                                    # noqa: BLE001
            pass


TMP = None

def main():
    global TMP
    TMP = Path(os.path.realpath(tempfile.mkdtemp(prefix="nf_lic_http_")))
    (TMP / "config").mkdir(parents=True)
    (TMP / "data" / "state").mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "system.yaml", TMP / "config" / "system.yaml")
    shutil.copy2(ROOT / "config" / "project.yaml", TMP / "config" / "project.yaml")

    proc = None
    try:
        print("\n【H1】未确认：只放行 /health 与 /license")
        proc = start({})
        check("nf_api 起来了", wait_up())
        code, d = req("GET", "/health")
        check("/health 200（探活不能被闸掉）", code == 200, (code, d))
        code, d = req("GET", "/license")
        check("/license 200", code == 200, (code, d))
        check("acked=false", d.get("acked") is False, d)
        check("带上 required 版本与指纹（前端据此显示）",
              (d.get("required") or {}).get("version") and (d.get("required") or {}).get("sha"), d)
        code, d = req("GET", "/state")
        check("/state 403", code == 403, (code, d))
        check("403 带 license_required 标记（前端据此弹窗）",
              d.get("license_required") is True, d)
        check("拒文指向桌面端，不是报错码",
              "桌面端" in str(d.get("error") or ""), d.get("error"))
        code, d = req("POST", "/stage/4/run", {})
        check("/stage/4/run 403（**花钱的入口先被拦**）", code == 403, (code, d))
        code, d = req("POST", "/config/engine", {"engine": "direct"})
        check("/config/engine 403（改引擎也不许）", code == 403, (code, d))

        print("\n【H2】签字端点：不接受代签")
        code, d = req("POST", "/license/ack", {"agree": True})
        check("普通 POST → 403", code == 403, (code, d))
        code, d = req("POST", "/license/ack", {"agree": True},
                      extra_headers={"X-Mofang-Source": "gui"})
        check("伪造来源头 → 仍 403（缺主进程 token，双因子）", code == 403, (code, d))
        code, d = req("POST", "/license/ack", {"agree": False})
        check("缺 agree 也是 403（来源不合格先于业务）", code == 403, (code, d))
        p = TMP / "config" / "license_ack.json"
        check("两次都没写出同意记录", not p.exists(), p)

        print("\n【H3】有同意记录 → 立刻放行（判据是文件，不是内存开关）")
        p.write_text(json.dumps({
            "product": "绒花墨坊", "version": "1.1", "sha": "7e2e1bd3f529",
            "at": "2026-11-18T00:00:00+08:00", "source": "gui",
        }, ensure_ascii=False), encoding="utf-8")
        code, d = req("GET", "/state")
        check("/state 200", code == 200, (code, str(d)[:120]))
        code, d = req("GET", "/license")
        check("acked=true 且非 bypass", d.get("acked") is True and d.get("bypass") is False, d)

        print("\n【H3b】已签之后：外部 Agent 仍不能动三个“开钱闸”的旋钮")
        # 许可协议只决定“能不能开始花钱”，不决定“钱从哪个池走”。
        # 这三条在 agent_mode=true 下是 FORBIDDEN_IN_AGENT_MODE（GUI 有 token 不受影响）。
        for path, body in (
            ("/config/engine", {"engine": "direct"}),
            ("/config/provider", {"role": "writer", "provider": "x"}),
            ("/env/set", {"key": "TOKENHUB_API_KEY", "value": "x"}),
        ):
            code, d = req("POST", path, body)
            check("%s 已签后仍 403（Agent 不得开钱闸）" % path, code == 403, (code, d))

        print("\n【H4】条款改版：记录失效后重新拦")
        p.write_text(json.dumps({"version": "0.9", "sha": "7e2e1bd3f529",
                                 "at": "2026-11-01T00:00:00+08:00", "source": "gui"},
                                ensure_ascii=False), encoding="utf-8")
        code, d = req("GET", "/state")
        check("旧版本记录 → /state 403", code == 403, (code, d))
        check("/license 报 stale=true", req("GET", "/license")[1].get("stale") is True, "")
    finally:
        stop(proc)

    proc = None
    try:
        print("\n【H5】测试豁免：NF_LICENSE_ACK=1 才放行，并如实标 bypass")
        (TMP / "config" / "license_ack.json").unlink(missing_ok=True)
        proc = start({"NF_LICENSE_ACK": "1"})
        check("nf_api 起来了", wait_up())
        code, d = req("GET", "/state")
        check("/state 200", code == 200, code)
        code, d = req("GET", "/license")
        check("acked=true", d.get("acked") is True, d)
        check("bypass=true（不伪装成用户签过）", d.get("bypass") is True, d)
        check("ack 记录仍为空", not d.get("ack"), d.get("ack"))
    finally:
        stop(proc)
        shutil.rmtree(TMP, ignore_errors=True)

    print("\ntest_license_http: %d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
