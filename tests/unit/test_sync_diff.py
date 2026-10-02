# -*- coding: utf-8 -*-
"""A4 桥层接线回归：`character_dirs` 恒假 + vault↔设定集 内容级对账。

## 为什么需要这套用例

两个缺陷都属于「**从来没真正跑过**」那一类，最容易被"全绿"掩盖：

| 编号 | 缺陷 | 危害 |
|---|---|---|
| D1 | `obsidian_templates.yaml` 的 `character_dirs` 默认 `[]` → `has_obsidian_entry` **恒假** | 「已有词条的角色只出出场记录」这条分支从未执行；后处理对每个角色都造设定草稿（重复造词条） |
| D2 | 没有 vault↔设定集的**内容级对账** | canon 被 stage1 归并吞掉、条目被改名绕过 locked、locked 标志丢失，全靠人工东查西查 |
| D3 | 对账/检查的「空结果」易被读成「通过」 | 没查 ≠ 通过（本仓栽过：`locked_violations` 曾恒为空列表） |

全部零 LLM、零网络，跑在**临时项目根**（复制 scripts/config），真实仓库零污染。
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []

NEEDED = ("obsidian_bridge.py", "obsidian_postprocess.py", "appearances.py")


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def build_sandbox(prefix="nf_sd_"):
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    (root / "data" / "setting").mkdir(parents=True, exist_ok=True)
    for n in NEEDED:
        s = REPO / "scripts" / n
        if s.exists():
            shutil.copy2(s, root / "scripts" / n)
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"),
                    dirs_exist_ok=True)
    for n in ("system.yaml", "system.local.yaml", "project.yaml"):
        s = REPO / "config" / n
        if s.exists():
            shutil.copy2(s, root / "config" / n)
    for n in ("obsidian_templates.yaml",):
        s = REPO / "config" / n
        if s.exists():
            shutil.copy2(s, root / "config" / n)
    return root


def run_py(root, code, env=None, timeout=60):
    import os
    script = root / "_probe.py"
    script.write_text(code, encoding="utf-8")
    e = dict(os.environ)
    e.update(env or {})
    try:
        p = subprocess.run([sys.executable, str(script)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           cwd=str(root), timeout=timeout, env=e)
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT"
    out = p.stdout or ""
    if "__RESULT__" in out:
        payload = out.split("__RESULT__", 1)[1].splitlines()[0]
        try:
            return json.loads(payload), out, p.stderr or ""
        except Exception:                                      # noqa: BLE001
            return None, out, p.stderr or ""
    return None, out, p.stderr or ""


PRELUDE = '''
import sys, os, json
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))

def _ticket(**kw):
    print("__RESULT__" + json.dumps(kw, ensure_ascii=False))
'''


def make_vault(root, chars=("阿丙",), layout="设定/人物"):
    """造一个 vault。默认用 A1 的通用布局；`layout` 可换成旧布局（中性占位）。

    返回 `(vault 根, 角色根, 角色子目录)`。**文件刻意放在角色根下的子类目里**
    —— 真实 vault 就是这样，而 `has_obsidian_entry` 必须能递归找到（A4 修复点）。
    """
    v = root / "MyVault"
    char_root = v.joinpath(*[s for s in layout.split("/") if s])
    d = char_root / "主要角色"
    d.mkdir(parents=True, exist_ok=True)
    for n in chars:
        (d / (n + ".md")).write_text(
            f"---\nname: {n}\ntype: 人物\nlocked: false\n---\n{n}的设定。\n",
            encoding="utf-8")
    return v, char_root, d


def rel(layout, name):
    """vault 内相对路径（**scan_vault 的 `path` 字段就是相对路径**，不是绝对路径）。"""
    return layout + "\\主要角色\\" + name + ".md"


def set_vault(root, v, vault_dirs=None):
    """把 vault_path（可选 vault_dirs）写进本地覆盖 —— 等价于用户在设置页填路径。"""
    lines = ["obsidian:", "  vault_path: " + json.dumps(str(v))]
    if vault_dirs is not None:
        lines.append("  vault_dirs:")
        for k, val in vault_dirs.items():
            lines.append(f"    {k}: " + json.dumps(val))
    (root / "config" / "system.local.yaml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def write_setting(root, chars):
    (root / "data" / "setting" / "setting.json").write_text(
        json.dumps({"characters": chars, "world": [], "timeline": []},
                   ensure_ascii=False), encoding="utf-8")


# ====================================================================== D1 恒假修复

def case_character_dirs_derived():
    print("\n[D1] character_dirs 留空 → 从 vault_dirs.characters 推导（修恒假）")
    root = build_sandbox()
    v, char_root, _ = make_vault(root, chars=("阿丙",))
    set_vault(root, v)
    code = PRELUDE + '''
import os
from obsidian_postprocess import load_obsidian_template_config, has_obsidian_entry
cfg = load_obsidian_template_config()
dirs = cfg["character_dirs"]
_ticket(dirs=dirs,
        hit=has_obsidian_entry("阿丙", dirs),
        miss=has_obsidian_entry("查无此人", dirs))
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("D1 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("D1 character_dirs 不再恒空（推导出 vault 内角色目录）",
          len(p["dirs"]) == 1, f"→ {p['dirs']}")
    check("D1 推导目录指向 vault 的**角色根**",
          p["dirs"] and Path(p["dirs"][0]).samefile(char_root), f"→ {p['dirs']}")
    check("D1 **has_obsidian_entry 真的能返回 True 了**（修复前恒假 + 只查同层）",
          p["hit"] is True, f"→ {p['hit']}")
    check("D1 不存在的角色仍为 False（没变成恒真）", p["miss"] is False, f"→ {p['miss']}")
    check("D1 推导有提示（用户能看出它自动接上了）",
          "从 obsidian.vault_dirs.characters 推导" in out, f"→ {out[-200:]}")
    shutil.rmtree(root, ignore_errors=True)


def case_derivation_follows_config():
    print("\n[D1c] 旧布局用户填了 vault_dirs 后，推导要跟着配置走（A1×A4 复合）")
    root = build_sandbox()
    v, char_root, _ = make_vault(root, chars=("阿丙",), layout="旧库/人物")
    set_vault(root, v, vault_dirs={"characters": "旧库/人物"})
    code = PRELUDE + '''
from obsidian_postprocess import load_obsidian_template_config, has_obsidian_entry
cfg = load_obsidian_template_config()
_ticket(dirs=cfg["character_dirs"], hit=has_obsidian_entry("阿丙", cfg["character_dirs"]))
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("D1c 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("D1c 推导跟随 vault_dirs 配置（旧布局也能接上）",
          p["dirs"] and Path(p["dirs"][0]).samefile(char_root), f"→ {p['dirs']}")
    check("D1c 旧布局下 has_obsidian_entry 同样能返回 True",
          p["hit"] is True, f"→ {p['hit']}")
    shutil.rmtree(root, ignore_errors=True)


def case_explicit_dirs_not_overridden():
    print("\n[D1b] 显式配了 character_dirs 时不得被推导覆盖")
    root = build_sandbox()
    v, _, _ = make_vault(root, chars=("阿丙",))
    set_vault(root, v)
    (root / "config" / "obsidian_templates.yaml").write_text(
        "obsidian_templates:\n  character_dirs: ['/nonexistent/我的词条']\n",
        encoding="utf-8")
    code = PRELUDE + '''
from obsidian_postprocess import load_obsidian_template_config
_ticket(dirs=load_obsidian_template_config()["character_dirs"])
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("D1b 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("D1b 显式配置优先，不被推导覆盖",
          p["dirs"] == ["/nonexistent/我的词条"], f"→ {p['dirs']}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== D2 对账

def case_sync_diff_happy_path():
    print("\n[D2] 对账：vault 有、设定集没有 → missing；设定集有、vault 没有 → added")
    root = build_sandbox()
    v, _, _ = make_vault(root, chars=("阿丙", "阿丁"))
    set_vault(root, v)
    write_setting(root, [
        {"name": "阿丙", "path": rel("设定/人物", "阿丙"), "type": "人物"},  # 对得上
        {"name": "阿戊", "path": "", "type": "人物"},                        # 新增
    ])
    code = PRELUDE + '''
import obsidian_bridge as ob
r = ob.sync_diff()
_ticket(checked=r["checked"], vt=r["vault_total"], st=r["setting_total"],
        missing=[m["name"] for m in r["missing"]],
        added=[a["name"] for a in r["added"]],
        renamed=len(r["renamed"]), lock_lost=len(r["lock_lost"]))
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    if p is None:
        check("D2 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("D2 对账生效", p["checked"] is True, f"→ {p}")
    check("D2 vault 有、设定集没有 → 报 missing（阿丁）",
          p["missing"] == ["阿丁"], f"→ {p['missing']}")
    check("D2 设定集有、vault 没有 → 报 added（阿戊）",
          p["added"] == ["阿戊"], f"→ {p['added']}")
    shutil.rmtree(root, ignore_errors=True)


def case_sync_diff_not_checked():
    print("\n[D2b] 空结果 ≠ 一致：vault 没配 / 设定集不存在 都要显式回报「没查」")
    root = build_sandbox()
    code = PRELUDE + '''
import obsidian_bridge as ob
r = ob.sync_diff(vault_data={"characters": []})
_ticket(checked=r["checked"], reason=r["reason"])
'''
    p, out, err = run_py(root, code)
    check("D2b vault 无角色 → checked=False 且给出原因（不冒充「一致」）",
          p is not None and p["checked"] is False and bool(p["reason"]),
          f"→ {p if p else out[-200:]}")

    v, _, _ = make_vault(root, chars=("阿丙",))
    code2 = PRELUDE + '''
import os, obsidian_bridge as ob
sc = ob.scan_vault(os.environ["V"])
r = ob.sync_diff(vault_data=sc, setting_path="data/setting/does_not_exist.json")
_ticket(checked=r["checked"], reason=r["reason"])
'''
    p2, out2, err2 = run_py(root, code2, {"V": str(v)})
    check("D2b 设定集不存在 → checked=False 且给出原因",
          p2 is not None and p2["checked"] is False and "不存在" in p2["reason"],
          f"→ {p2 if p2 else out2[-200:]}")

    code3 = PRELUDE + '''
import os, obsidian_bridge as ob
sc = ob.scan_vault(os.environ["V"])
r = ob.sync_diff(vault_data=sc, setting_path="nonexistent.json")
_ticket(checked=r["checked"])
'''
    p3, out3, err3 = run_py(root, code3, {"V": str(v)})
    check("D2b **未生效时 CLI 必须非零退出**（不能静默当通过）",
          p3 is not None and p3["checked"] is False, f"→ {p3}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== D3 locked 复用

def case_sync_diff_reuses_locked_check():
    print("\n[D3] 改名 / 丢 locked 复用同一份 locked 判据（不另写一份匹配逻辑）")
    root = build_sandbox()
    v, _, d = make_vault(root, chars=("阿丙", "阿丁"))
    set_vault(root, v)
    # 两个条目在 vault 里都是 locked=true 的硬约束
    for n in ("阿丙", "阿丁"):
        (d / (n + ".md")).write_text(
            f"---\nname: {n}\ntype: 人物\nlocked: true\n---\n硬约束。\n",
            encoding="utf-8")
    write_setting(root, [
        # 同 path 被改名（阿丙 → 阿丙改）
        {"name": "阿丙改", "path": rel("设定/人物", "阿丙"), "type": "人物",
         "locked": True},
        # 名字没变，但 locked 标志丢了
        {"name": "阿丁", "path": rel("设定/人物", "阿丁"), "type": "人物"},
    ])
    code = PRELUDE + '''
import os, obsidian_bridge as ob
sc = ob.scan_vault(os.environ["V"])
r = ob.sync_diff(vault_data=sc)
_ticket(types=sorted({x["type"] for x in r["renamed"] + r["lock_lost"]}),
        lock_checked=r["locked"]["checked"],
        lock_total=r["locked"]["locked_total"],
        missing=[m["name"] for m in r["missing"]])
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    if p is None:
        check("D3 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("D3 locked 检查被复用且生效", p["lock_checked"] is True, f"→ {p}")
    check("D3 vault 内 locked 条目计数正确（2 个）", p["lock_total"] == 2, f"→ {p}")
    check("D3 同 path 改名 → renamed", "renamed" in p["types"], f"→ {p['types']}")
    check("D3 locked 标志丢失 → lock_lost", "lock_lost" in p["types"], f"→ {p['types']}")
    check("D3 匹配上的条目不应被误报为 missing", p["missing"] == [], f"→ {p['missing']}")
    shutil.rmtree(root, ignore_errors=True)


def main():
    print("=" * 70)
    print("A4 桥层接线回归：character_dirs 恒假 + vault↔设定集 对账")
    print("=" * 70)
    case_character_dirs_derived()
    case_derivation_follows_config()
    case_explicit_dirs_not_overridden()
    case_sync_diff_happy_path()
    case_sync_diff_not_checked()
    case_sync_diff_reuses_locked_check()
    print("\n" + "=" * 70)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
