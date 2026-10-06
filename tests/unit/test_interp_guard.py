# -*- coding: utf-8 -*-
"""解释器守卫回归（2026-10-06 试跑事故）。

## 为什么需要它

外部 Agent 在项目根敲 `python scripts/orchestrator.py`，PATH 上的 python 是宿主环境
（Hermes 自带 3.14，没装 PyYAML）→ `import yaml` 开跑即崩。agent 把病因误诊成
「.venv 缺 yaml」，而 .venv 里 PyYAML 一直好好装着 —— **错的从来不是依赖，是解释器**。
当天连续炸了五次试跑。

守卫（`utils/interp_guard.py`）挂在这两个开跑入口上：

1. 缺 PyYAML 且项目 .venv 存在 → stderr 留痕、切 venv 重跑；
2. 切不动 → 可行动报错（orchestrator 退 1）或转阻塞自检项（nfctl check）；
3. **防拉锯**：`NF_PY_GUARD=1` 只切一次，venv 自身坏时不会无限重exec；
4. **不污染 stdout**：nfctl `--json` 是协议流，守卫一律写 stderr。

判据设计：C/D/E 三条是**反证**（把 yaml 藏掉，看系统是不是真的会闹）——
只测「yaml 在时一切正常」证明不了守卫存在，那是 vacuous truth。

全程离线、零网络、零 LLM 费用。
用法：python tests/unit/test_interp_guard.py
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import interp_guard as G  # noqa: E402  —— import 本身零副作用（守卫只在调用时动作）

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


def test_A_normal_path():
    print("\n[A] 正常态（当前就是项目 venv）：零副作用直通")
    check("A1 has_yaml() 为 True", G.has_yaml())
    check("A2 ensure_working_python 不重exec 直接返回 True",
          G.ensure_working_python("scripts/x.py") is True)


def test_B_paths():
    print("\n[B] 路径与入口挂钩")
    vpy = G.venv_python(ROOT)
    check("B1 venv_python 指向 .venv/Scripts/python.exe 且存在",
          vpy.name == "python.exe" and vpy.parent.name == "Scripts" and vpy.exists(), vpy)
    orch = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    nfctl = (ROOT / "scripts" / "nfctl.py").read_text(encoding="utf-8")
    guard_src = (ROOT / "scripts" / "utils" / "interp_guard.py").read_text(encoding="utf-8")
    check("B2 orchestrator 挂了守卫", 'ensure_working_python("scripts/orchestrator.py")' in orch)
    check("B3 守卫先于 utils.config_io 导入（yaml 崩点在它之前就要拦住）",
          orch.find("interp_guard") < orch.find("from utils.config_io"))
    check("B4 nfctl 挂了守卫", 'ensure_working_python("scripts/nfctl.py")' in nfctl)
    check("B5 nfctl collect_check 有「当前解释器」阻塞分支",
          'add("当前解释器", False' in nfctl and "has_yaml()" in nfctl)
    check("B6 守卫写 stderr（不污染 --json stdout）", "file=sys.stderr" in guard_src)
    check("B7 防拉锯标记在场（NF_PY_GUARD 只切一次）",
          "_GUARD_ENV" in guard_src and "os.environ.get(_GUARD_ENV)" in guard_src)
    check("B8 切解释器留痕（[py-guard]）", "[py-guard]" in guard_src)


def test_C_hint():
    print("\n[C] 切不动时的文案必须可行动")
    tmp = Path(tempfile.mkdtemp(prefix="nf_guard_"))
    try:
        hint_no_venv = G.missing_yaml_hint(tmp)
        check("C1 无 venv：给出建 venv + 装依赖的完整命令",
              "python -m venv" in hint_no_venv and "requirements.txt" in hint_no_venv,
              hint_no_venv)
        (tmp / ".venv" / "Scripts").mkdir(parents=True)
        (tmp / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
        hint_venv = G.missing_yaml_hint(tmp)
        check("C2 有 venv：指向 pip install -r requirements.txt",
              "pip install -r requirements.txt" in hint_venv, hint_venv)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _shim_env():
    """造一个把 yaml 藏掉的 PYTHONPATH 前置 shim（优先级高于 site-packages）。"""
    shim = Path(tempfile.mkdtemp(prefix="nf_yaml_shim_"))
    (shim / "yaml.py").write_text(
        "raise ModuleNotFoundError(\"No module named 'yaml' (guard-test shim)\")\n",
        encoding="utf-8")
    env = dict(os.environ)
    env.pop("NF_PY_GUARD", None)               # 让守卫走完整路径
    env["PYTHONPATH"] = str(shim) + os.pathsep + env.get("PYTHONPATH", "")
    return shim, env


def test_D_fail_loud_orchestrator():
    print("\n[D] 反证：藏掉 yaml → orchestrator 必须闹（切一次、不拉锯、可行动）")
    shim, env = _shim_env()
    try:
        p = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "orchestrator.py"), "--dry-run"],
            cwd=str(ROOT), env=env, capture_output=True, timeout=120)
        err = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
        check("D1 退出码 1（不再裸崩，也不假装成功）", p.returncode == 1, p.returncode)
        check("D2 [py-guard] 切换留痕", "[py-guard]" in err, err[:200])
        check("D3 进程终止 + 可行动报错（防拉锯，非死循环）",
              "启动被解释器守卫拦下" in err and "pip install -r requirements.txt" in err,
              err[:300])
        check("D4 不再是裸 ModuleNotFoundError 堆栈",
              "Traceback (most recent call last)" not in err, err[:200])
    finally:
        shutil.rmtree(shim, ignore_errors=True)


def test_E_fail_loud_nfctl_json():
    print("\n[E] 反证：藏掉 yaml → nfctl check 转阻塞自检项，--json stdout 仍纯净")
    shim, env = _shim_env()
    try:
        p = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "nfctl.py"), "check", "--json"],
            cwd=str(ROOT), env=env, capture_output=True, timeout=120)
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        try:
            data = json.loads(out)
            check("E1 stdout 是合法 JSON（守卫没写进协议流）", True, out[:80])
        except json.JSONDecodeError as e:
            data = {}
            check("E1 stdout 是合法 JSON（守卫没写进协议流）", False, e)
        check("E2 阻塞项含「当前解释器」（不再误导成别的毛病）",
              "当前解释器" in (data.get("blocking") or []),
              data.get("blocking"))
        check("E3 stderr 有切换留痕 + 挡下说明",
              "[py-guard]" in err and "解释器缺 PyYAML" in err, err[:200])
        check("E4 阻塞 detail 给出修法（pip install -r requirements.txt）",
              "requirements.txt" in json.dumps(data, ensure_ascii=False),
              json.dumps([i for i in data.get("items", [])
                          if i["name"] == "当前解释器"], ensure_ascii=False)[:260])
    finally:
        shutil.rmtree(shim, ignore_errors=True)


def main():
    print("=" * 62)
    print("  解释器守卫回归（离线，含藏 yaml 反证）")
    print("=" * 62)
    test_A_normal_path()
    test_B_paths()
    test_C_hint()
    test_D_fail_loud_orchestrator()
    test_E_fail_loud_nfctl_json()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
