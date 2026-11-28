# -*- coding: utf-8 -*-
"""nfctl check 形态感知回归（2026-10-12）。

## 为什么需要它

`collect_check` 此前对源码态与安装态共用同一套判据，安装态（工作区）被恒假红两条：

1. **venv 缺失 = 阻塞** —— 安装态不建 .venv 是设计（跑在安装自带 runtime 上）；
2. **API Key 空 = 阻塞** —— engine=hermes 时 LLM 走 agent 子会话（订阅流量），
   直连 Key 根本不参与运行。

假红会训练人忽略红灯（这恰是\u300cagent 分不清开发态和客户端\u300d痛点的一半：check 自己
不先说清形态，人和 agent 都会把安装态误读成坏环境）。本文件把两种形态的语义钉死。

## 判据设计

- A 组（安装态）：无 .git/.venv + 有 scripts/nf_api.py → form=installed，
  venv / Key 两条都**不得**进阻塞清单（反证：不是"整个 check 都绿"——
  夹具故意缺 prompts，"提示词模板完整"仍须阻塞）。
- B 组（源码态）：有 .git → form=source，缺 venv / direct 空 Key 照旧阻塞
  （新逻辑不许把源码态的真红也一起放掉）。
- C 组（引擎分界）：源码态 + venv 齐 + engine=hermes + Key 空 → Key 不阻塞；
  同样配置 engine=direct → Key 阻塞。引擎是 Key 判据的唯一开关。

全程离线零 LLM。用法：python tests/unit/test_nfctl_check_form.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from nfctl import collect_check, render_check, run_tests, render_test_results  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def _mk_root(engine="hermes", git=False, venv=False, key=""):
    tmp = Path(tempfile.mkdtemp(prefix="nf_check_form_"))
    (tmp / "scripts").mkdir()
    (tmp / "scripts" / "nf_api.py").write_text("# fixture\n", encoding="utf-8")
    (tmp / "config").mkdir()
    (tmp / "config" / "system.yaml").write_text(
        "engine: %s\n"
        "providers:\n"
        "  tokenhub:\n"
        "    type: openai-compat\n"
        "    api_key_env: TOKENHUB_API_KEY\n" % engine, encoding="utf-8")
    if git:
        (tmp / ".git").mkdir()
    if venv:
        (tmp / ".venv" / "Scripts").mkdir(parents=True)
        (tmp / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    (tmp / ".env").write_text("TOKENHUB_API_KEY=%s\n" % key, encoding="utf-8")
    return tmp


def _blocking(res):
    return res["blocking"]


def test_A_installed_form():
    print("\n[A] 安装态：venv / Key 两条假红必须消失，且真红不许被顺手放掉")
    tmp = _mk_root(engine="hermes", git=False, venv=False, key="")
    try:
        res = collect_check(tmp)
        check("A1 form 标为 installed", res.get("form") == "installed", res.get("form"))
        check("A2 \u300c执行形态\u300d自检项在场且为 ok",
              any(it["name"] == "执行形态" and it["ok"] for it in res["items"]),
              [it["name"] for it in res["items"]][:4])
        check("A3 venv 不再是阻塞（安装态设计如此）",
              "venv python" not in _blocking(res), _blocking(res))
        check("A4 hermes 空 Key 不再是阻塞",
              "API Key 已配置" not in _blocking(res), _blocking(res))
        check("A5 反证：夹具缺 prompts，\u300c提示词模板完整\u300d仍须阻塞（不是整绿）",
              "提示词模板完整" in _blocking(res), _blocking(res))
        rendered = render_check(res)
        check("A6 渲染文案带\u300c安装态\u300d",
              "安装态" in rendered, rendered.splitlines()[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_B_source_form():
    print("\n[B] 源码态：原语义保留（缺 venv / direct 空 Key 照旧真红）")
    tmp = _mk_root(engine="direct", git=True, venv=False, key="")
    try:
        res = collect_check(tmp)
        check("B1 form 标为 source", res.get("form") == "source", res.get("form"))
        check("B2 源码态缺 venv 仍是阻塞",
              "venv python" in _blocking(res), _blocking(res))
        check("B3 direct 已下线 → Key 判据不再阻塞（2026-11-25 F8）",
              "API Key 已配置" not in _blocking(res), _blocking(res))
        rendered = render_check(res)
        check("B4 渲染文案带\u300c源码态\u300d",
              "源码态" in rendered, rendered.splitlines()[0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_C_engine_boundary():
    print("\n[C] 引擎分界：Key 判据只认 engine，不认形态")
    tmp = _mk_root(engine="hermes", git=True, venv=True, key="")
    try:
        res = collect_check(tmp)
        check("C1 源码态+venv齐+hermes+Key空 → Key 不阻塞",
              "API Key 已配置" not in _blocking(res), _blocking(res))
        check("C2 同形态 venv 齐 → venv 不阻塞（有就是有）",
              "venv python" not in _blocking(res), _blocking(res))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = _mk_root(engine="direct", git=True, venv=True, key="")
    try:
        res = collect_check(tmp)
        check("C3 同形态改 direct → Key 判据已随 direct 下线移除（循环论证消掉）",
              "API Key 已配置" not in _blocking(res), _blocking(res))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_D_installed_no_source():
    """[D] 安装态缺 console/src 与 tests/：不得假红、不得假绿、不得把"没跑"当"通过"（2026-11-28）。

    0.9.15 安装态实测三条：
      ① `check` 报"直角引号：6 个文件"（全在 config/ 注释与 history 备份里，
         用户根本看不到）—— 假红；
      ② `check` 报"白名单过期 26 条"，同因扫描根 `console/src` 不存在 →
         0 条文案 → NECESSARY 全判过期；同时"界面文案无黑话"变成假绿；
      ③ `nfctl test` 报 0/0 并退 0 —— "一个用例都没跑"被读成"全绿"。
    """
    print("\n[D] 安装态无源码/无测试：三条假红假绿必须消失")
    tmp = _mk_root(engine="dsh", git=False, venv=False, key="")
    try:
        # 造一个"像安装态"的 config：带直角引号的注释（用户看不到的那种）
        (tmp / "config" / "system.yaml").write_text(
            "engine: dsh\n"
            "# 注释里用了\u300c直角引号\u300d—— 这是开发注释，用户永远看不到\n"
            "providers:\n"
            "  tokenhub:\n"
            "    api_key_env: TOKENHUB_API_KEY\n", encoding="utf-8")
        res = collect_check(tmp)
        detail = {it["name"]: it["detail"] for it in res["items"]}
        check("D1 无 git 索引时直角引号不阻塞（安装态无源码）",
              "直角引号" not in _blocking(res), _blocking(res))
        check("D2 直角引号 detail 说清是“不适用”而不是“零命中”",
              "不适用" in detail.get("直角引号", ""), detail.get("直角引号"))
        check("D3 无 console/src 时界面对内部路径不阻塞",
              "界面文案里的内部路径" not in _blocking(res), _blocking(res))
        check("D4 无 console/src 时“无黑话”不许报 0 条命中（假绿）",
              "不适用" in detail.get("界面文案无黑话", ""), detail.get("界面文案无黑话"))

        # nfctl test：无 tests/ 必须 not_run，且不是“通过”
        from nfctl import run_tests
        t = run_tests(tmp)
        check("D5 无 tests/ 时 run_tests 报 not_run（不再是 total=0 就绿）",
              t.get("not_run") is True, t)
        check("D6 not_run 时 total/passed/failed 都是 0 且带说明",
              t["total"] == 0 and t["passed"] == 0 and t["failed"] == 0
              and any("未执行" in e for e in t["errors"]), t.get("errors"))
        check("D7 not_run 的渲染明说“未执行”",
              "未执行" in render_test_results(t), render_test_results(t).splitlines()[:3])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_E_source_still_strict():
    """[E] 反证：源码态（有 git 索引）这三条仍必须严格 —— 别把真红也一起放掉。"""
    print("\n[E] 反证：源码态这三条仍然严")
    tmp = _mk_root(engine="dsh", git=False, venv=False, key="")
    try:
        (tmp / "note.md").write_text("# 文档\n这条写了\u300c直角引号\u300d\n", encoding="utf-8")
        # 造一个可用的 git 索引（init + add，不提交也行，ls-files 认暂存区）
        import subprocess
        try:
            subprocess.run(["git", "init", "-q"], cwd=str(tmp), capture_output=True, timeout=20)
            subprocess.run(["git", "add", "-A"], cwd=str(tmp), capture_output=True, timeout=20)
            git_ok = True
        except Exception:                                    # noqa: BLE001
            git_ok = False
        if not git_ok:
            check("E0 git 不可用 → 本组跳过", True, "跳过")
            return
        res = collect_check(tmp)
        check("E1 源码态有 git 索引时直角引号仍阻塞",
              "直角引号" in _blocking(res), _blocking(res))
        check("E2 且 detail 点名了违规文件",
              any("note.md" in it["detail"] for it in res["items"]
                  if it["name"] == "直角引号"),
              [it["detail"] for it in res["items"] if it["name"] == "直角引号"])

        t = run_tests(tmp)
        check("E3 有 tests/ 但匹配不到时也是 not_run（--pattern 打错不许绿）",
              t.get("not_run") is True, t)
        (tmp / "tests" / "unit").mkdir(parents=True, exist_ok=True)
        (tmp / "tests" / "unit" / "test_a.py").write_text(
            "import sys\nprint('PASS a')\nsys.exit(0)\n", encoding="utf-8")
        t2 = run_tests(tmp)
        check("E4 有真用例时 not_run 消失", not t2.get("not_run"), t2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_A_installed_form()
    test_B_source_form()
    test_C_engine_boundary()
    test_D_installed_no_source()
    test_E_source_still_strict()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
