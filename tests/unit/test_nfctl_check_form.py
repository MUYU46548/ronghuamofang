# -*- coding: utf-8 -*-
"""nfctl check 形态感知回归（2026-10-12）。

## 为什么需要它

`collect_check` 此前对源码态与安装态共用同一套判据，安装态（工作区）被恒假红两条：

1. **venv 缺失 = 阻塞** —— 安装态不建 .venv 是设计（跑在安装自带 runtime 上）；
2. **API Key 空 = 阻塞** —— engine=hermes 时 LLM 走 agent 子会话（订阅流量），
   直连 Key 根本不参与运行。

假红会训练人忽略红灯（这恰是「agent 分不清开发态和客户端」痛点的一半：check 自己
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

from nfctl import collect_check, render_check  # noqa: E402

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
        check("A2 「执行形态」自检项在场且为 ok",
              any(it["name"] == "执行形态" and it["ok"] for it in res["items"]),
              [it["name"] for it in res["items"]][:4])
        check("A3 venv 不再是阻塞（安装态设计如此）",
              "venv python" not in _blocking(res), _blocking(res))
        check("A4 hermes 空 Key 不再是阻塞",
              "API Key 已配置" not in _blocking(res), _blocking(res))
        check("A5 反证：夹具缺 prompts，「提示词模板完整」仍须阻塞（不是整绿）",
              "提示词模板完整" in _blocking(res), _blocking(res))
        rendered = render_check(res)
        check("A6 渲染文案带「安装态」",
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
        check("B3 direct 引擎空 Key 仍是阻塞",
              "API Key 已配置" in _blocking(res), _blocking(res))
        rendered = render_check(res)
        check("B4 渲染文案带「源码态」",
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
        check("C3 同形态改 direct → Key 恢复阻塞",
              "API Key 已配置" in _blocking(res), _blocking(res))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_A_installed_form()
    test_B_source_form()
    test_C_engine_boundary()
    print("\n总计: %d  通过: %d  失败: %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: %s" % "；".join(FAIL))
        return 1
    print("→ 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
