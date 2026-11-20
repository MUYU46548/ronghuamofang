# -*- coding: utf-8 -*-
"""C5 花销闸的 HTTP 端到端自检（2026-11-20）。

判的是**入口接线**：`do_POST` 里那道闸到底挂上没有。单测（`tests/unit/
test_agent_spend_gate.py`）判的是判据本身；两件事都验才叫“装上并且生效”。

刻意**不真跑花钱端点**：本例只验“没授权 → 403”“授权 → 状态变有效”“撤销 → 又 403”。
从没有一次进到 start_job，所以零额度消耗（作者 2026-11-12 立过规矩：
任何消耗额度的调用先报备）。

临时根：复制 scripts/config/prompts，引擎改 dsh、agent_mode 改 true，
许可记录直接由本进程写（`utils.license.record`）—— 否则总闸 403 会盖住花销闸的断言。

用法：python tests/http/test_agent_spend_http.py
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
PORT = 8963
BASE = "http://127.0.0.1:%d" % PORT
GUI_TOKEN = "test-gui-token-spend"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "http"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def req(method, path, body=None, timeout=30, gui=False):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    headers = {"Content-Type": "application/json"}
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
        except Exception:
            payload = {}
        return e.code, payload


def _set_yaml(path: Path, key: str, value: str):
    """按行定向替换顶层键（保留注释，不用 yaml.dump）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    out, done = [], False
    for ln in lines:
        if ln.startswith(key + ":"):
            out.append("%s: %s" % (key, value))
            done = True
        else:
            out.append(ln)
    if not done:
        out.insert(0, "%s: %s" % (key, value))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_spend_http_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    sysyaml = tmp / "config" / "system.yaml"
    _set_yaml(sysyaml, "engine", "dsh")
    txt = sysyaml.read_text(encoding="utf-8")
    txt = txt.replace("  agent_mode: true", "  agent_mode: true")
    if "agent_mode:" not in txt:
        txt += "\ngates:\n  agent_mode: true\n"
    sysyaml.write_text(txt, encoding="utf-8")
    # 许可记录：由本进程直接写（服务端版本与指纹由 utils.license 决定）
    from utils import license as lic
    ok, res = lic.record(source="gui", root=tmp)
    if not ok:
        raise SystemExit("许可记录写入失败: %r" % (res,))
    return tmp


def main():
    tmp = build_project()
    log = io.open(tmp / "nf_api.log", "w", encoding="utf-8")
    env = dict(os.environ)
    env.pop("NF_ROOT", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["NF_GUI_TOKEN"] = GUI_TOKEN          # 主进程签发令牌（本例由测试扮演主进程）
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--root", str(tmp)],
                            cwd=str(tmp), stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        for _ in range(60):
            time.sleep(0.5)
            try:
                if req("GET", "/health", timeout=3)[0] == 200:
                    break
            except Exception:
                pass
        else:
            print("服务未能启动，见 " + str(tmp / "nf_api.log"))
            return 1

        print("=== 1. 授权状态可读（读侧不拦：Agent 该知道还剩几次）===")
        code, d = req("GET", "/config/agent_spend")
        check("GET → 200", code == 200, (code, d))
        check("引擎读出 dsh", d.get("engine") == "dsh", d.get("engine"))
        check("初始未授权", (d.get("status") or {}).get("valid") is False,
              (d.get("status") or {}).get("reason"))
        check("回传被闸住的端点清单（可核对）",
              any("/stage/" in x for x in (d.get("guarded_endpoints") or [])),
              len(d.get("guarded_endpoints") or []))

        print("\n=== 2. 没授权 + 外部 Agent 跑阶段 → 403（钱闸生效）===")
        code, d = req("POST", "/stage/4/run", {"chapters": "1"})
        check("POST /stage/4/run → 403", code == 403, (code, d.get("reason")))
        check("标记 spend_blocked（不是许可/审批那种 403）", d.get("spend_blocked") is True, d)
        check("拒因 no-grant", d.get("reason") == "no-grant", d.get("reason"))
        check("拒文说明“花的是作者的钱、去桌面端授权”",
              "[spend]" in (d.get("error") or "") and "桌面端" in (d.get("error") or ""))
        check("没有创建 job（被闸挡在入口，不是跑起来才失败）",
              not list((tmp / "data" / "jobs").glob("*")) if (tmp / "data" / "jobs").exists() else True)

        print("\n=== 3. 其他端点不受牵连 ===")
        code, d = req("POST", "/discuss/say",
                      {"chapter": 1, "topic": "测试", "role": "ai", "text": "零 token 的一轮"})
        check("零 token 的 /discuss/say 不被花销闸挡（403 也只会来自别的闸）",
              not (code == 403 and d.get("spend_blocked")), (code, d.get("error", "")[:60]))

        print("\n=== 4. Agent 不能给自己签额度 ===")
        code, d = req("POST", "/config/agent_spend", {"action": "grant", "max_calls": 10, "hours": 2})
        check("无 GUI 双因子 → 拒绝", code in (400, 403), (code, str(d.get("error"))[:80]))
        check("且没写出授权文件", not (tmp / "data" / "state" / "agent_spend_grant.json").exists())

        print("\n=== 5. GUI 授权 → 状态变有效（限次数/限时）===")
        code, d = req("POST", "/config/agent_spend",
                      {"action": "grant", "max_calls": 3, "hours": 1}, gui=True)
        check("GUI 授权 → 200", code == 200 and d.get("ok"), (code, d.get("error")))
        st = d.get("status") or {}
        check("授权后 valid=true", st.get("valid") is True, st)
        check("次数/到期都落进状态", st.get("max_calls") == 3 and bool(st.get("expires_at")), st)
        check("越界次数 → 400（不静默截断）",
              req("POST", "/config/agent_spend", {"action": "grant", "max_calls": 0, "hours": 1},
                  gui=True)[0] == 400)

        print("\n=== 6. 撤销 → 立刻回到 403 ===")
        code, d = req("POST", "/config/agent_spend", {"action": "revoke"}, gui=True)
        check("撤销 → 200", code == 200 and d.get("ok"), (code, d.get("error")))
        check("撤销后 valid=false", (d.get("status") or {}).get("valid") is False, d.get("status"))
        code, d = req("POST", "/stage/4/run", {"chapters": "1"})
        check("再跑阶段 → 又 403", code == 403 and d.get("spend_blocked") is True, (code, d.get("reason")))

        print("\n=== 7. 审计台账留痕（事后可追）===")
        audit = tmp / "data" / "state" / "agent_audit.jsonl"
        raw = audit.read_text(encoding="utf-8") if audit.exists() else ""
        check("agent_audit.jsonl 里有 spend-blocked 记录", "spend-blocked" in raw,
              "共 %d 行" % len(raw.splitlines()))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log.close()

    print("\n" + "=" * 60)
    print("HTTP 花销闸：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
