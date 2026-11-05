# -*- coding: utf-8 -*-
"""走向 / 方向样例的 HTTP 层自检（零 LLM、零真实模型）。

## 为什么要这一层（2026-11-05 真机复现）

作者原话：“无法生成走向，无法生成多方案对比。”真机复现（临时 root + 18791 端口，
engine=hermes）两条 job 全部 failed 于
`AttributeError: 'HermesClient' object has no attribute '_post_chat'` ——
根因在客户端（已补 `_post_chat`）。本文件钉的是**闸与界面之间那段路**：

- self 引擎下两条“要模型”的端点必须 400 + 人话（3E 以前是 500 + 开发者文案）；
- 参数校验：chapter / kind 非法一律 400；
- 生成闭环：202 → job ok → 三张卡（FakeClient 的 `_post_chat` 回固定形状）；
- 选定闭环：submit 落提案进 `data/discuss/drafts/`，`current` 能读回，
  **章大纲一个字节不动**（选定 ≠ 改正文，这是架构红线）；
- 开局方案：生成 3 条 → 每条都带 `risk`（没代价的方案是假选项）→ pick 写 chosen。

不碰真实仓库、不调模型：复制 scripts/config/prompts 到临时根，第二段用
`NF_API_ALLOW_FAKE=1`（FakeClient 现已有 `_post_chat`）。

用法：python tests/http/test_outline_branches_api_http.py
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
PORT_SELF = 8941
PORT_FAKE = 8942
TEST_TOKEN = "nf-test-gui-token-" + "0123456789abcdef" * 2
PASS, FAIL = [], []

CHAPTER_MD = """# 第三章 暗格

- 标题：暗格
- 核心事件：阿禾在药柜夹层发现父亲的账本
- 涉及角色：阿禾
- 功能：把父亲的失踪从传言变成证据
- 衔接：承接第二章的药铺盘账
"""

SETTING = {
    "characters": [{"name": "阿禾", "identity": "药铺学徒", "goal": "查清父亲失踪"}],
    "world": {"locations": [{"name": "青石镇", "description": "山道尽头的旧镇。"}]},
}


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:220]) if detail else ""))


def req(port, method, path, body=None, timeout=60):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    headers = {"Content-Type": "application/json", "X-Mofang-Source": "gui",
               "X-Mofang-Token": TEST_TOKEN}
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                               data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                       # noqa: BLE001
            payload = {}
        return e.code, payload


def _set_engine(system_yaml, engine):
    p = Path(system_yaml)
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    for ln in lines:
        if ln.startswith("engine:"):
            out.append("engine: " + engine)
        elif ln.strip().startswith("agent_mode:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append(indent + "agent_mode: false   # 验收环境强制标准模式")
        else:
            out.append(ln)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")


def build_project(engine):
    tmp = Path(tempfile.mkdtemp(prefix="nf_branchhttp_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history", "system.local.yaml"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    for d in ("data/state", "data/outline/chapters", "data/setting", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "outline" / "chapters" / "03.md").write_text(CHAPTER_MD, encoding="utf-8")
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    _set_engine(tmp / "config" / "system.yaml", engine)
    return tmp


def _port_free(port):
    """端口必须是空的：上一次崩溃留下的 nf_api 还占着端口时，健康检查会打到
    **旧进程**（旧 root 可能已被删/被初始化向导清空）→ 用例全绿但验的是别的实例。
    本轮真机目检就撞过一次（18797 上挂着上一次的 nf_api，章列表恒空）。"""
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start(root, port, allow_fake=False):
    if not _port_free(port):
        raise RuntimeError("端口 %d 已被占用（多半是上一次异常退出留下的 nf_api，"
                           "换端口或先按 PID 结束它）" % port)
    env = dict(os.environ, NF_GUI_TOKEN=TEST_TOKEN)
    if allow_fake:
        env["NF_API_ALLOW_FAKE"] = "1"
    proc = subprocess.Popen([str(PY), str(root / "scripts" / "nf_api.py"),
                             "--port", str(port), "--root", str(root)],
                            cwd=str(root), stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=env)
    for _ in range(60):
        try:
            code, _h = req(port, "GET", "/health", timeout=3)
            if code == 200:
                return proc, True
        except Exception:                                       # noqa: BLE001
            time.sleep(0.4)
    return proc, False


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def poll_job(port, jid, tries=60):
    for _ in range(tries):
        time.sleep(0.3)
        code, d = req(port, "GET", "/jobs/" + jid)
        if code == 200 and d.get("state") != "running":
            return d
    return {"state": "timeout"}


def main():
    print("=" * 66)
    print("走向 / 方向样例 HTTP 自检（self 前置拒绝 + 生成闭环 + 选定留痕）")
    print("=" * 66)

    # ---------------------------------------------------------------- self
    root = build_project("self")
    proc, ready = start(root, PORT_SELF)
    try:
        check("nf_api 起来了（engine=self）", ready)
        if not ready:
            return 1

        print("\n【1】engine=self：要模型的端点必须 400 + 人话（不是 500）")
        code, d = req(PORT_SELF, "POST", "/outline/sample/candidates",
                      {"chapter": 3, "kind": "tone"})
        check("方向样例 400（不是 500）", code == 400, (code, d.get("error")))
        check("拒文说得出原因（只出任务书）", "只出任务书" in (d.get("error") or ""),
              d.get("error"))
        check("拒文给出下一步（设置 → 运行引擎）", "设置" in (d.get("error") or ""),
              d.get("error"))

        code, d = req(PORT_SELF, "POST", "/outline/variants/generate", {"note": ""})
        check("开局方案 400（不是 500）", code == 400, (code, d.get("error")))
        check("开局方案拒文同样是人话", "只出任务书" in (d.get("error") or ""),
              d.get("error"))

        print("\n【2】读侧零 token：没内容时给空列表，不是 404")
        code, d = req(PORT_SELF, "GET", "/outline/variants")
        check("GET /outline/variants 200", code == 200 and d.get("ok"), (code, d))
        check("还没生成时 variants=[]（不是 404）", d.get("variants") == [], d.get("variants"))
        code, d = req(PORT_SELF, "POST", "/outline/sample/current", {"chapter": 3})
        check("POST /outline/sample/current 200", code == 200 and d.get("ok"), (code, d))
        check("这章还没选过 → samples=[]", d.get("samples") == [], d.get("samples"))
    finally:
        stop(proc)
        shutil.rmtree(root, ignore_errors=True)

    # ------------------------------------------------------- hermes + fake
    root = build_project("hermes")
    before = (root / "data" / "outline" / "chapters" / "03.md").read_bytes()
    proc, ready = start(root, PORT_FAKE, allow_fake=True)
    try:
        check("nf_api 起来了（engine=hermes + FakeClient）", ready)
        if not ready:
            return 1

        print("\n【3】参数校验：非法值一律 400，且说清是哪一项")
        code, d = req(PORT_FAKE, "POST", "/outline/sample/candidates",
                      {"chapter": 0, "kind": "tone"})
        check("chapter=0 → 400", code == 400 and "chapter" in (d.get("error") or ""),
              (code, d.get("error")))
        code, d = req(PORT_FAKE, "POST", "/outline/sample/candidates",
                      {"chapter": 3, "kind": "vibes"})
        check("未知 kind → 400 且列出可选值",
              code == 400 and "kind" in (d.get("error") or ""), (code, d.get("error")))

        print("\n【4】方向样例：202 → job ok → 三张卡")
        code, d = req(PORT_FAKE, "POST", "/outline/sample/candidates",
                      {"chapter": 3, "kind": "tone", "note": "冷一点"})
        check("提交 202 + job_id", code == 202 and d.get("job_id"), (code, d))
        job = poll_job(PORT_FAKE, d.get("job_id") or "")
        check("job 收尾 ok", job.get("state") == "ok", job)
        samples = (job.get("result") or {}).get("samples") or []
        check("出 3 个方向", len(samples) == 3, len(samples))
        check("每条都带 label/text/why",
              all(s.get("label") and s.get("text") and s.get("why") for s in samples),
              samples)
        check("记账字段回传（成本页不再凭空少一笔）",
              "cost_yuan" in (job.get("result") or {}), list(job.get("result") or {}))

        print("\n【5】选定：落提案 → 批准才改章大纲（选定 ≠ 改正文）")
        one = samples[0]
        code, d = req(PORT_FAKE, "POST", "/outline/sample/submit",
                      {"chapter": 3, "kind": "tone", "label": one.get("label"),
                       "text": one.get("text"), "why": one.get("why")})
        check("submit 200", code == 200 and d.get("ok"), (code, d))
        rel = str(d.get("proposal") or "")
        check("回传提案相对路径（data/outline/drafts）",
              rel.startswith("data/outline/drafts/"), rel)
        proposal = root / rel if rel else None
        check("提案真的落盘", bool(proposal) and proposal.exists(), rel)
        if proposal and proposal.exists():
            body = proposal.read_text(encoding="utf-8")
            check("提案标题写明是方向样例", "方向样例" in body, body[:120])
            check("锚点 kind=sample（stage4 只认这类）", '"kind": "sample"' in body
                  or "'kind': 'sample'" in body, body[-300:])
        code, d = req(PORT_FAKE, "POST", "/outline/sample/current", {"chapter": 3})
        check("未批准时 current 为空（提案 ≠ 生效）",
              code == 200 and d.get("samples") == [], (code, d))
        after = (root / "data" / "outline" / "chapters" / "03.md").read_bytes()
        check("章大纲未被改动（选定 ≠ 改正文）", before == after)

        print("\n【6】批准 = 真正写回章大纲（带备份），并且能读回")
        code, d = req(PORT_FAKE, "POST", "/outline/discussion/review",
                      {"path": rel, "action": "approve", "note": "验收"})
        check("approve 200", code == 200 and d.get("ok"), (code, d))
        now = (root / "data" / "outline" / "chapters" / "03.md").read_text(encoding="utf-8")
        check("章大纲出现优化点（真的落成大纲修订）", "优化点" in now, now[:160])
        check("写回前留了备份（可回退）",
              len(list((root / "data" / "outline" / "chapters").glob("03_v*.bak"))) >= 1)
        code, d = req(PORT_FAKE, "POST", "/outline/sample/current", {"chapter": 3})
        check("批准后 current 能读回（下一轮写作会注入）",
              code == 200 and len(d.get("samples") or []) >= 1, (code, d))

        print("\n【7】开局方案：三条都写代价 → 选定写 chosen")
        code, d = req(PORT_FAKE, "POST", "/outline/variants/generate", {"note": ""})
        check("提交 202 + job_id", code == 202 and d.get("job_id"), (code, d))
        job = poll_job(PORT_FAKE, d.get("job_id") or "")
        check("job 收尾 ok", job.get("state") == "ok", job)
        variants = (job.get("result") or {}).get("variants") or []
        check("出 3 条走向", len(variants) == 3, len(variants))
        check("每条都写了 risk（没代价的方案 = 假选项）",
              all(v.get("risk") for v in variants), variants)
        code, d = req(PORT_FAKE, "POST", "/outline/variants/pick", {"id": 2})
        check("pick 200", code == 200 and d.get("ok"), (code, d))
        chosen = root / "data" / "outline" / "open_variant_chosen.md"
        check("选定落成 open_variant_chosen.md（stage2 会读它）", chosen.exists(), chosen)
        code, d = req(PORT_FAKE, "GET", "/outline/variants")
        check("再次读取时 chosen=true（前端能标出已选定）",
              code == 200 and d.get("chosen") is True, (code, d))

        print("\n【8】规划四态：候选=idea，选定=selected（留痕在 plan_state）")
        code, d = req(PORT_FAKE, "GET", "/plan/list")
        items = d.get("items") or []
        variants_in_plan = [i for i in items if i.get("kind") == "branch_variant"]
        check("三条走向进了规划板（kind=branch_variant）",
              len(variants_in_plan) >= 3, len(items))
        check("选定的那条转成 selected",
              any(i.get("status") == "selected" for i in variants_in_plan),
              [(i.get("title"), i.get("status")) for i in variants_in_plan][:5])
        check("规划本落在本项目根（不是 cwd 相对写飞）",
              (root / "data" / "state" / "plan_state.json").exists())
    finally:
        stop(proc)
        shutil.rmtree(root, ignore_errors=True)

    print("-" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  ✗", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
