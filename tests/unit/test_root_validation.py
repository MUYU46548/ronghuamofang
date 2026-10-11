# -*- coding: utf-8 -*-
"""项目根存在性校验（2026-10-11 工单 P0-3）。

## 为什么需要它

`--root` / `NF_ROOT` 指向不存在的目录时，旧实现**照常起服务**：`/state` 到处
`AttributeError` 裸抛 500，排障的人看不到"根就不对"这个真因（2026-10-11 实测：
`/about` 报 `project_root=C:\\c\\Users\\...` 这种半截路径、`/state` 只回一句内部错误）。
nfctl 更隐蔽：每条子命令都在不存在的根上"成功"返回空数据，看起来像"这本书没数据"。

## 判据

A. `nf_api --root <不存在>` → 退出非零，stderr/stdout 含**路径**与**来源**（--root 参数）。
B. `NF_ROOT=<不存在>`（不带 --root）→ 同上，来源标注 NF_ROOT 环境变量。
C. `nfctl --root <不存在> status` → 退出 2 + 人话；`--json` 下给机器可读错误对象。
D. root **存在**但 `data/` 缺失 → `/about` 带 `empty_root=true` + `empty_hint` 非空。
E. 反证：root 下有 `data/` → `empty_root=false`、`empty_hint=None`（防空真阴）。

全程离线、零网络、零 LLM、零费用；临时目录建在系统临时区，跑完即弃。
用法：python tests/unit/test_root_validation.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


def run(args, env_extra=None, timeout=90):
    env = {k: v for k, v in os.environ.items() if k != "NF_ROOT"}
    env.update(env_extra or {})
    return subprocess.run([sys.executable] + args, cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def main():
    missing = Path(tempfile.mkdtemp(prefix="nf_root_missing_")) / "no_such_dir"
    assert not missing.exists()

    print("【A】nf_api --root 指向不存在目录 → 人话报错退出（来源=--root 参数）")
    r = run(["scripts/nf_api.py", "--root", str(missing)])
    out = (r.stdout or "") + (r.stderr or "")
    check("退出非零", r.returncode != 0, f"rc={r.returncode}")
    check("报出路径", str(missing) in out, out[:200])
    check("报出来源", "--root 参数" in out, out[:200])
    check("人话处置提示", "处置" in out, out[:200])

    print("【B】NF_ROOT 指向不存在目录（无 --root）→ 同上（来源=环境变量）")
    r = run(["scripts/nf_api.py"], env_extra={"NF_ROOT": str(missing)})
    out = (r.stdout or "") + (r.stderr or "")
    check("退出非零", r.returncode != 0, f"rc={r.returncode}")
    check("报出来源=NF_ROOT 环境变量", "NF_ROOT 环境变量" in out, out[:200])

    print("【C】nfctl --root 指向不存在目录 → 同款校验")
    r = run(["scripts/nfctl.py", "--root", str(missing), "status"])
    out = (r.stdout or "") + (r.stderr or "")
    check("退出码=2", r.returncode == 2, f"rc={r.returncode}")
    check("报出路径", str(missing) in out, out[:200])
    check("报出来源", "--root 参数" in out, out[:200])
    r = run(["scripts/nfctl.py", "--root", str(missing), "status", "--json"])
    try:
        d = json.loads((r.stdout or "").strip().splitlines()[-1])
        ok = d.get("ok") is False and d.get("source") == "--root 参数"
    except Exception as e:                                    # noqa: BLE001
        ok, d = False, str(e)
    check("--json 给机器可读错误", ok, d)

    print("【D】root 存在但 data/ 缺失 → /about 空根提示")
    empty_root = Path(tempfile.mkdtemp(prefix="nf_root_empty_"))
    full_root = Path(tempfile.mkdtemp(prefix="nf_root_full_"))
    (full_root / "data").mkdir()
    for label, target, want_empty in (("空根", empty_root, True), ("有 data", full_root, False)):
        code = (
            "import json, sys; sys.path.insert(0, r'%s');"
            "import nf_api as api; api._set_root(r'%s');"
            "from nf_api_domains.misc import handle_about;"
            "st, pay = handle_about(None);"
            "print(json.dumps({'st': st, 'empty_root': pay.get('empty_root'),"
            " 'empty_hint': pay.get('empty_hint'), 'root': pay.get('project_root')},"
            " ensure_ascii=False))"
        ) % (SCRIPTS, target)
        r = run(["-c", code])
        try:
            pay = json.loads((r.stdout or "").strip().splitlines()[-1])
        except Exception:                                     # noqa: BLE001
            pay = {"_parse_fail": (r.stdout or "") + (r.stderr or "")}
        check(f"/about status=200（{label}）", pay.get("st") == 200, pay)
        check(f"empty_root={want_empty}（{label}）", pay.get("empty_root") is want_empty, pay)
        if want_empty:
            check("空根给非空提示", bool(pay.get("empty_hint")), pay)
        else:
            check("有 data 时无提示", pay.get("empty_hint") is None, pay)

    print("\n  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
