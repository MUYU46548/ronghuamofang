# -*- coding: utf-8 -*-
"""止烧阈值端点自检（GET/POST /config/token_limit，2026-10-03）。零 LLM。

为什么这些断言重要（用户第 1 条指令："参数可由用户在设置中手动配置，
并保留一套默认预设，防止用户乱改改坏"）：

- **看得见**：hermes 下金额阈值恒不生效，止烧全靠这几个 token 上限 ——
  调阈值的前提是先能读到当前值、预设值与合法区间；
- **改不坏**：把 `max_total_tokens` 改成 0/负数会让闸门**静默消失**
  （0 在今天语义里是"不限制"），与本轮修掉的「¥ 记账恒 0」是同一类坑 → 必须 400 拒绝；
- **改不动别人的闸门**：外部 Agent 若能 POST 抬高上限，"止烧"就是摆设 →
  写入口要求 GUI 来源 + 列入 agent_guard 禁止清单。

**不碰真实仓库**：复制 scripts/config/prompts 到临时根起 nf_api。

用法：python tests/http/test_token_limit_api_http.py
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
sys.path.insert(0, str(ROOT / "scripts"))
PY = sys.executable
PORT = 8933
BASE = "http://127.0.0.1:%d" % PORT
# GUI 来源双因子（2026-10-06 红队修复）：起服务时注入的会话 token。
# 只认来源头的话，任何本机进程加一个 `X-Mofang-Source: gui` 就能绕过全部写入守卫。
TEST_TOKEN = "nf-test-gui-token-" + "0123456789abcdef" * 2
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=60, gui=False, source=None, token=None):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    headers = {"Content-Type": "application/json"}
    if gui:
        # gui=True = 「可信 GUI」：来源头 + 正确 token（双因子齐备）
        headers["X-Mofang-Source"] = "gui"
        headers["X-Mofang-Token"] = TEST_TOKEN if token is None else token
    if source:
        headers["X-Mofang-Source"] = source
        if token is not None:
            headers["X-Mofang-Token"] = token
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


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_toklim_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    for d in ("data/state", "data/outline", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    return tmp


def cfg_text(tmp):
    return (tmp / "config" / "system.yaml").read_text(encoding="utf-8")


def token_values(tmp):
    import yaml  # noqa: F401  (临时根里的配置，直接读文本足够)
    sys.path.insert(0, str(tmp / "scripts"))
    from utils.config_io import load_config_yaml
    return ((load_config_yaml(tmp / "config" / "system.yaml") or {})
            .get("budget") or {}).get("token_limit")


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    from utils import cost_tracker                     # 真实仓库的判据（预设/边界）
    from utils import agent_guard
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            # 会话 token：模拟 Electron 主进程 spawn 时的 env 注入。
                            # 没有它 → nf_api 不认任何 GUI 来源（fail-closed），
                            # 下面【3】的「合法写入」会全挂，所以必须给。
                            env=dict(os.environ, NF_GUI_TOKEN=TEST_TOKEN))
    try:
        ready = False
        for _ in range(60):
            try:
                code, _h = req("GET", "/health")
                if code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.5)
        check("nf_api 起来了", ready)
        if not ready:
            return 1

        print("\n【1】GET：当前值 / 出厂预设 / 合法区间 / 说明")
        code, d = req("GET", "/config/token_limit")
        check("GET 200", code == 200 and d.get("ok"), d)
        check("当前值 = 出厂预设（出厂即预设，不许漂）",
              d.get("current") == cost_tracker.TOKEN_LIMIT_PRESET, d.get("current"))
        check("预设回显（设置页的「恢复默认」用它）",
              d.get("preset") == cost_tracker.TOKEN_LIMIT_PRESET, d.get("preset"))
        check("区间回显（前端可据此提示）",
              set(d.get("bounds") or {}) == set(cost_tracker.TOKEN_LIMIT_BOUNDS),
              d.get("bounds"))
        check("说明点明 hermes 下金额阈值无效（否则用户不知道该调哪）",
              "hermes" in (d.get("note") or ""), d.get("note"))

        print("\n【2】写入口只认 GUI 来源（外部 Agent 不得抬高自己的闸门）")
        before = cfg_text(tmp)
        code, d = req("POST", "/config/token_limit", {"max_total_tokens": 2000000})
        check("无 GUI 头 → 403", code == 403, (code, d))
        check("被拒绝时**配置文件一个字没动**", cfg_text(tmp) == before)
        code, _d = req("POST", "/config/token_limit", {"max_total_tokens": 2000000},
                       source="agent")
        check("显式 agent 来源 → 403", code == 403, code)
        check("/config/token_limit 在 agent_guard 禁止清单里",
              agent_guard.is_forbidden_in_agent_mode("/config/token_limit"))
        # --- 2026-10-06 红队修复回归：来源头是**客户端自述**，光有它不算数 ---
        code, d = req("POST", "/config/token_limit", {"max_total_tokens": 2000000},
                      source="gui")
        check("**伪造 gui 来源头但无 token → 403**（端点闸 _gui_only 层）",
              code == 403, (code, d))
        check("伪造被拒时配置文件仍未被动过", cfg_text(tmp) == before)
        code, d = req("POST", "/config/token_limit", {"max_total_tokens": 2000000},
                      gui=True, token="wrong-token-value")
        check("来源头齐 + token 错误 → 403（token 才是真凭据）",
              code == 403, (code, d))

        print("\n【3】合法写入：部分提交用预设补齐 + 注释保留 + 回读一致")
        comments_before = [ln for ln in before.splitlines() if ln.strip().startswith("#")]
        code, d = req("POST", "/config/token_limit", {"max_total_tokens": 2000000}, gui=True)
        check("POST 200", code == 200 and d.get("ok"), (code, d))
        after = cfg_text(tmp)
        vals = token_values(tmp)
        check("目标值落盘", vals.get("max_total_tokens") == 2000000, vals)
        check("未提交的项按预设补齐（不是 0）",
              vals.get("per_request_max_tokens") == 50000
              and vals.get("per_request_pause_hermes") is True, vals)
        comments_after = [ln for ln in after.splitlines() if ln.strip().startswith("#")]
        check("**注释与说明段落逐行保留**（定向改写而非 dump）",
              comments_before == comments_after,
              "%d → %d 行" % (len(comments_before), len(comments_after)))
        code, d2 = req("GET", "/config/token_limit")
        check("GET 回读到新值", d2.get("current", {}).get("max_total_tokens") == 2000000, d2)

        print("\n【4】越界/坏值一律 400（防「乱改改坏」= 闸门静默消失）")
        before_bad = cfg_text(tmp)
        for bad_body, why in (
                ({"max_total_tokens": 0}, "0 会让累计闸门消失"),
                ({"max_total_tokens": -1}, "负数"),
                ({"max_total_tokens": 999999999999}, "超上界"),
                ({"per_request_max_tokens": 1}, "低于下界"),
                ({"max_total_tokens": "abc"}, "非数字"),
                ({"warn_ratio": 0}, "预警比越界"),
                ({"不存在的键": 1}, "未知字段")):
            code, d = req("POST", "/config/token_limit", bad_body, gui=True)
            check("拒绝 %s（%s）" % (bad_body, why), code == 400 and not d.get("ok"),
                  (code, d.get("error")))
        check("全程坏值都拒绝后，配置文件仍未被动过", cfg_text(tmp) == before_bad)

        print("\n【5】enabled=false 与恢复预设")
        code, d = req("POST", "/config/token_limit", {"enabled": False}, gui=True)
        check("可关闭（enabled=false）", code == 200 and token_values(tmp)["enabled"] is False,
              (code, token_values(tmp)))
        code, d = req("POST", "/config/token_limit", dict(cost_tracker.TOKEN_LIMIT_PRESET),
                      gui=True)
        check("恢复默认预设成功", code == 200, (code, d))
        check("恢复后与预设逐项一致",
              token_values(tmp) == cost_tracker.TOKEN_LIMIT_PRESET, token_values(tmp))

        print("\n【6】agent_mode=true 时也被守卫拦住（双保险）")
        import re
        txt = cfg_text(tmp)
        txt = re.sub(r"(agent_mode:\s*)(false|true)", r"\1true", txt)
        (tmp / "config" / "system.yaml").write_text(txt, encoding="utf-8")
        before_am = cfg_text(tmp)
        code, d = req("POST", "/config/token_limit", {"max_total_tokens": 3000000})
        check("agent_mode=true + 非 GUI → 403（agent_guard 层拦下）", code == 403, (code, d))
        check("仍未改动配置", cfg_text(tmp) == before_am)

        print("\n【7】观测面：/state 必须带上 token 用量与上限（否则用户盲调阈值）")
        code, st = req("GET", "/state")
        b = (st or {}).get("budget") or {}
        check("/state 200", code == 200, code)
        check("budget.tokens_used 存在（数字，不是 None）",
              isinstance(b.get("tokens_used"), int), b)
        check("budget.token_limit 回显当前阈值",
              (b.get("token_limit") or {}).get("max_total_tokens") == 3000000
              or (b.get("token_limit") or {}).get("max_total_tokens") == 10000000, b.get("token_limit"))
        check("budget.token_pct 存在（有上限时不是 None）",
              b.get("token_pct") is not None, b)
        check("空账本 → tokens_used = 0（不谎报）", b.get("tokens_used") == 0, b)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
