# -*- coding: utf-8 -*-
"""丙4 大纲分段审批的 HTTP 层自检（GET /outline/segments + POST /outline/segments/set）。零 LLM。

为什么要单独一层（与 `tests/unit/test_outline_segments.py` 不重复）：
单测验的是**判据**（切分/记账/gate_status/CLI），本文件验的是**闸与界面之间的那段路**——
上一轮交付时闸已经开着、GUI 却没做，用户在界面上点「确认放行」只吃到一个哑巴 409。
这里把端点行为钉死：

- 读侧**不设闸**：不带任何 GUI 头也能读（它只回显 global.md 本就存在的正文）；
- 写侧**审批级**：GUI 双因子（来源头 + 主进程签发 token）缺一即 403，
  且被拦时状态文件**一个字节都不许动**（先判后写）；
- 写侧在 `agent_guard.FORBIDDEN_IN_AGENT_MODE` 里：Agent 模式下外部 Agent
  可读不可勾 —— 否则自己勾满八段再放行，分段审阅等于没有；
- 放行闸与 CLI **同一份判据**：未全过时 POST /approve → 409 且文案说得出差哪几段，
  全过后 → 200。

**不碰真实仓库**：复制 scripts/config/prompts 到临时根起 nf_api。

用法：python tests/http/test_segment_approval_api_http.py
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
sys.path.insert(0, str(ROOT / "scripts"))
PY = sys.executable
PORT = 8934
BASE = "http://127.0.0.1:%d" % PORT
# GUI 来源双因子（2026-10-06 红队修复）：起服务时注入的会话 token。
TEST_TOKEN = "nf-test-gui-token-" + "0123456789abcdef" * 2
PASS, FAIL = [], []

# 与 prompts/stage2_global_outline.md 的固定输出格式一致（8 段）
TEMPLATE_OUTLINE = """# 《分段之书》整体大纲

## 起
开端：主角登场，冲突埋下。

## 承
承接：冲突升级，势力登场。

## 转
转折：真相揭露，关系翻转。

## 合
收束：主线闭合，余韵留白。

## 关键节点
- 第 5 章 码头对峙

## 预计章节数
24 章

## 章节规划
分三卷，每卷 8 章。

## 要求
保持冷峻文风。
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           (  "  -> " + str(detail)[:220]) if detail else ""))


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
    tmp = Path(tempfile.mkdtemp(prefix="nf_seghttp_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    for d in ("data/state", "data/outline", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "outline" / "global.md").write_text(TEMPLATE_OUTLINE, encoding="utf-8")
    (tmp / "data" / "state" / "progress.json").write_text(
        json.dumps({"project": "分段之书",
                    "stages": {"2": {"status": "done", "approved": False}}},
                   ensure_ascii=False), encoding="utf-8")
    return tmp


def _force_agent_mode_off(system_yaml: Path):
    """把临时根的 gates.agent_mode 改 false（按行定向替换，保留注释）。"""
    p = Path(system_yaml)
    if not p.exists():
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    out, replaced = [], False
    for ln in lines:
        if ln.strip().startswith("agent_mode:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append(indent + "agent_mode: false   # 验收环境强制标准模式")
            replaced = True
        else:
            out.append(ln)
    if replaced:
        p.write_text("\n".join(out) + "\n", encoding="utf-8")


def _ensure_segment_switch(system_yaml: Path):
    """临时根必须显式开启分段审批，否则闸不拦 —— 断言就没有意义。"""
    p = Path(system_yaml)
    txt = p.read_text(encoding="utf-8")
    if "segment_approval:" in txt:
        return True
    lines = txt.splitlines()
    out = []
    for ln in lines:
        out.append(ln)
        if ln.strip() == "gates:":
            out.append("  segment_approval: true   # 验收环境显式开启（丙4）")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return "segment_approval:" in p.read_text(encoding="utf-8")


def state_path(tmp):
    return tmp / "data" / "state" / "outline_segment_approval.json"


def state_text(tmp):
    p = state_path(tmp)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    from utils import agent_guard
    _force_agent_mode_off(tmp / "config" / "system.yaml")
    sw_on = _ensure_segment_switch(tmp / "config" / "system.yaml")
    check("夹具显式开启 segment_approval（否则闸不拦，断言没意义）", sw_on)

    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--root", str(tmp)],
                            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            # 会话 token：模拟 Electron 主进程 spawn 时的 env 注入。
                            # 没有它 → nf_api 不认任何 GUI 来源（fail-closed），
                            # 下面【3】的合法勾选会全挂，所以必须给。
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

        print("\n【1】读侧：不设闸，回显分段 + 正文 + 放行判据")
        code, d = req("GET", "/outline/segments")
        check("GET 200（读侧不要求 GUI 来源）", code == 200 and d.get("ok"), (code, d.get("error")))
        check("切成 8 段", d.get("total") == 8, d.get("total"))
        check("enabled=true（开关开 + 能切出 >=2 段）", d.get("enabled") is True, d.get("enabled"))
        check("每段带回正文（不给正文 = 让人盲签）",
              all(s.get("text") for s in d.get("segments") or []))
        check("首段正文含卷首（H1 大标题并进第一段，不落空）",
              "整体大纲" in (d.get("segments") or [{}])[0].get("text", ""))
        check("放行判据回显为未通过", d.get("gate_ok") is False, d.get("gate_ok"))
        check("判据文案说得出差哪几段",
              "段未通过" in (d.get("gate_message") or ""), d.get("gate_message"))
        check("拦它的同时给出下一条命令（GUI 与 CLI 两条路都在）",
              "segment_cli" in (d.get("gate_message") or "")
              and "分段审阅" in (d.get("gate_message") or ""), d.get("gate_message"))

        print("\n【2】写侧是审批级：GUI 双因子缺一即 403，且状态文件不许动")
        before = state_text(tmp)
        code, d = req("POST", "/outline/segments/set", {"id": "s01", "approved": True})
        check("无 GUI 头 → 403", code == 403, (code, d))
        check("被拒时状态文件一个字节没动", state_text(tmp) == before)
        code, _d = req("POST", "/outline/segments/set", {"id": "s01", "approved": True},
                       source="agent")
        check("显式 agent 来源 → 403", code == 403, code)
        code, d = req("POST", "/outline/segments/set", {"id": "s01", "approved": True},
                      source="gui")
        check("**伪造 gui 来源头但无 token → 403**（token 才是真凭据）", code == 403, (code, d))
        code, d = req("POST", "/outline/segments/set", {"id": "s01", "approved": True},
                      gui=True, token="wrong-token-value")
        check("来源头齐 + token 错误 → 403", code == 403, (code, d))
        check("全程被拒后状态文件仍未被动过", state_text(tmp) == before)
        check("写侧在 FORBIDDEN_IN_AGENT_MODE 内（Agent 模式下可读不可勾）",
              agent_guard.is_forbidden_in_agent_mode("/outline/segments/set") is True)
        check("读侧不在此列（外部 Agent 要能看懂差哪几段）",
              agent_guard.is_forbidden_in_agent_mode("/outline/segments") is False)

        print("\n【3】未全过时放行被拦，且文案与 CLI 同一份判据")
        code, d = req("POST", "/approve", {"stage": 2}, gui=True)
        check("POST /approve 409（闸真拦）", code == 409 and d.get("ok") is False, (code, d))
        msg = d.get("message") or ""
        check("409 文案说得出差哪几段", "段未通过" in msg and "还有" in msg, msg[:200])
        check("409 文案给出下一条命令", "segment_cli" in msg, msg[:240])
        prog = json.loads((tmp / "data" / "state" / "progress.json").read_text(encoding="utf-8"))
        check("先判后写：approved 一个字节都没动",
              prog["stages"]["2"].get("approved") is False, prog["stages"]["2"])

        print("\n【4】合法勾选：进度真推进，勾满后放行真通")
        code, d = req("POST", "/outline/segments/set", {"id": "s01", "approved": True}, gui=True)
        check("GUI 勾选 200", code == 200 and d.get("ok"), (code, d))
        check("回显进度 1/8", d.get("approved") == 1 and d.get("total") == 8, d)
        code, d = req("GET", "/outline/segments")
        check("读侧回读到已通过的 s01",
              [s for s in d.get("segments") or [] if s.get("id") == "s01"][0].get("status")
              == "approved", d.get("segments"))
        code, d = req("POST", "/outline/segments/set", {"id": "s99", "approved": True}, gui=True)
        check("乱写段号 → 400 且回显现有段号（不静默）",
              code == 400 and "s01" in (d.get("error") or ""), (code, d.get("error")))

        for i in range(2, 9):
            code, d = req("POST", "/outline/segments/set",
                          {"id": "s%02d" % i, "approved": True}, gui=True)
            if not (code == 200 and d.get("ok")):
                check("勾 s%02d 成功" % i, False, (code, d))
                break
        code, d = req("GET", "/outline/segments")
        check("八段全过 → all_approved", d.get("all_approved") is True, d.get("all_approved"))
        check("全过后 gate_ok 翻真（放行判据同一份）", d.get("gate_ok") is True, d.get("gate_ok"))
        check("全过后拦它的文案消失", not (d.get("gate_message") or ""), d.get("gate_message"))

        code, d = req("POST", "/approve", {"stage": 2}, gui=True)
        check("全过后放行 200", code == 200 and d.get("ok") is True, (code, d))
        prog = json.loads((tmp / "data" / "state" / "progress.json").read_text(encoding="utf-8"))
        check("放行后 approved=true", prog["stages"]["2"].get("approved") is True,
              prog["stages"]["2"])

        print("\n【5】撤销勾选：退回未全过，闸重新拦（不许一次勾选永久放行）")
        code, d = req("POST", "/outline/segments/set", {"id": "s03", "approved": False}, gui=True)
        check("撤销 200", code == 200 and d.get("ok"), (code, d))
        check("撤销后 all_approved=false", d.get("all_approved") is False, d)
        code, d = req("POST", "/approve", {"stage": 2, "revoke": True}, gui=True)
        check("撤销阶段2 审批（回到待审）", code == 200, (code, d))
        code, d = req("POST", "/approve", {"stage": 2}, gui=True)
        check("撤一段后放行再次被拦", code == 409, (code, d))

        print("\n【6】agent_mode=true 时写侧被第一层拦下（双保险）")
        cfgp = tmp / "config" / "system.yaml"
        cfgp.write_text(re.sub(r"(agent_mode:\s*)(false|true)", r"\1true",
                               cfgp.read_text(encoding="utf-8")), encoding="utf-8")
        before_am = state_text(tmp)
        code, d = req("POST", "/outline/segments/set", {"id": "s04", "approved": True})
        check("agent_mode=true + 非 GUI → 403（FORBIDDEN 名单拦下）", code == 403, (code, d))
        code, d = req("POST", "/outline/segments/set", {"id": "s04", "approved": True},
                      source="gui")
        check("agent_mode=true + 只有伪造来源头 → 仍 403", code == 403, (code, d))
        check("被拦期间状态文件未被动过", state_text(tmp) == before_am)
        code, d = req("GET", "/outline/segments")
        check("Agent 模式下读侧照常可用（可读不可勾）", code == 200 and d.get("ok"), code)
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
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
