# -*- coding: utf-8 -*-
"""vault 目录配置化（A1）回归 —— 直接打在**配置层**，不依赖 history/ 真实快照。

## 为什么单独建这个文件

2026-10-02 审查发现：A1 把 `path_kind` 的判据锚点从写死的（某套私人数字前缀分类法）
改成 `obsidian.vault_dirs.characters` 推导。而**能证明这个判据该有的那条用例
（`test_setting_schema` F 节，480 条真实归档）依赖 `history/20260917_191317_stage2_done`，
该快照已不在任何机器上 → 恒 SKIP**。也就是说：patch 号称「40/0 持平」，实际
根本没验到 path 判据是否还活着（**假绿**）。

本文件用**自造夹具**（临时 vault + 临时 config）把这条判据钉死，永不 SKIP。
同时钉住三个当下修掉的缺陷：

| 编号 | 缺陷 | 钉法 |
|---|---|---|
| G1 | 默认值应为通用虚构布局、五键齐全 | 直读 get_vault_dirs/get_skip_dirs |
| G2 | 自定义 vault_dirs 要真的改变**扫描目标** | 造 vault 后 scan_vault 数命中 |
| G3 | `skip_dirs` 是**追加**不是替换（旧实现点一下就把 `.git` 放进来） | local 只写一项，断言默认四项仍在 |
| G4 | skip_dirs 曾经**两处各写一份**且已漂移（bridge 有 '99 模板'、kb_index 没有） | 断言两处是**同一个函数对象** |
| G5 | 锚点一改，既有「旧布局」数据**静默退回 name 词表** | `extra_character_anchors` 多锚点三态 |
| G6 | 目录落空曾**静默 continue**（下游注入恒空无提示） | 断言打告警 |
| G7 | 配置解析失败曾 `except: pass` 静默降级 | 坏 YAML → 必须打告警 |

隔离策略与 `test_obsidian_integrity` 一致：复制 scripts/config 到**临时项目根**跑，
真实仓库零污染、零 LLM 调用、零网络。
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []

NEEDED = ("obsidian_bridge.py",)


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def build_sandbox(prefix="nf_vd_"):
    """临时项目根：scripts/（含 utils）+ config/。

    必须复制 config/system.yaml —— 新用例断言的**默认值**就来自它。
    """
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    (root / "data" / "state").mkdir(parents=True, exist_ok=True)
    for n in NEEDED:
        s = REPO / "scripts" / n
        if s.exists():
            shutil.copy2(s, root / "scripts" / n)
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"),
                    dirs_exist_ok=True)
    for n in ("system.yaml", "project.yaml"):
        s = REPO / "config" / n
        if s.exists():
            shutil.copy2(s, root / "config" / n)
    return root


def run_py(root, code, env=None, timeout=60):
    """在临时根里跑一段 python，返回 (__RESULT__ 载荷 or None, stdout, stderr)。"""
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


def _write_local(root, text):
    (root / "config" / "system.local.yaml").write_text(text, encoding="utf-8")


def _reset_local(root):
    p = root / "config" / "system.local.yaml"
    if p.exists():
        p.unlink()


# ====================================================================== G1

def case_defaults():
    print("\n[G1] 默认值=通用虚构布局，五键齐全；skip_dirs 默认四项")
    root = build_sandbox()
    code = PRELUDE + '''
from utils.setting_schema import get_vault_dirs, get_skip_dirs
_ticket(vd=get_vault_dirs(), sd=sorted(get_skip_dirs()))
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("G1 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    vd = p["vd"]
    check("G1 vault_dirs 五键齐全（characters/locations/factions/concepts/timeline）",
          set(vd.keys()) == {"characters", "locations", "factions", "concepts",
                             "timeline"}, f"→ {sorted(vd.keys())}")
    check("G1 默认布局是通用虚构（不含任何数字前缀/私人目录名）",
          vd["characters"] == "设定/人物" and vd["timeline"] == "年表", f"→ {vd}")
    check("G1 skip_dirs 默认四项",
          p["sd"] == [".agent_context", ".git", ".hermes", ".obsidian"],
          f"→ {p['sd']}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== G2

def case_custom_vault_dirs_actually_scans():
    print("\n[G2] 自定义 vault_dirs 要真的改变扫描目标（不是只改了配置）")
    root = build_sandbox()
    v = root / "MyVault"
    # 只造「用户自己的」结构，不造默认的 设定/人物
    (v / "我的库" / "角色").mkdir(parents=True, exist_ok=True)
    (v / "我的库" / "角色" / "阿丙.md").write_text(
        "---\nname: 阿丙\ntype: 人物\nlocked: false\n---\n一个测试角色。\n",
        encoding="utf-8")
    (v / "设定" / "人物").mkdir(parents=True, exist_ok=True)
    (v / "设定" / "人物" / "默认布局角色.md").write_text(
        "---\nname: 默认布局角色\n---\n不该被扫到。\n", encoding="utf-8")
    _write_local(root, "obsidian:\n  vault_dirs:\n    characters: \"我的库/角色\"\n")
    code = PRELUDE + '''
import os, obsidian_bridge as ob
sc = ob.scan_vault(os.environ["V"])
_ticket(names=[c["name"] for c in sc["characters"]])
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    _reset_local(root)
    if p is None:
        check("G2 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("G2 自定义 vault_dirs 生效（扫到 我的库/角色/阿丙）",
          "阿丙" in p["names"], f"→ {p['names']}")
    check("G2 默认布局不再被扫（配置是唯一事实源）",
          "默认布局角色" not in p["names"], f"→ {p['names']}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== G3

def case_skip_dirs_is_append():
    print("\n[G3] skip_dirs 是**追加**：用户只写一项，默认四项必须仍在")
    root = build_sandbox()
    _write_local(root, "obsidian:\n  skip_dirs: ['99 模板']\n")
    code = PRELUDE + '''
from utils.setting_schema import get_skip_dirs
_ticket(sd=sorted(get_skip_dirs()))
'''
    p, out, err = run_py(root, code)
    _reset_local(root)
    if p is None:
        check("G3 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    sd = set(p["sd"])
    missing = {".obsidian", ".git", ".hermes", ".agent_context"} - sd
    check("G3 用户追加项生效", "99 模板" in sd, f"→ {sorted(sd)}")
    check("G3 **默认四项没被顶掉**（旧实现此处会把 .git 放进扫描面）",
          not missing, f"→ 丢失 {sorted(missing)}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== G4

def case_two_sources_same_object():
    print("\n[G4] bridge 与 kb_index 的 skip_dirs 必须同一事实源（曾漂移）")
    root = build_sandbox()
    code = PRELUDE + '''
import obsidian_bridge as ob
from utils import setting_schema as ss
from utils import kb_index as kb
_ticket(has_const=hasattr(ob, "SKIP_DIRS"),
        same_kb=kb.get_skip_dirs is ss.get_skip_dirs,
        same_bridge=set(getattr(ob, "_skip_dirs", ())) == set(ss.get_skip_dirs()))
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("G4 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("G4 obsidian_bridge 里写死的 SKIP_DIRS 常量已删除（防第二份清单复活）",
          p["has_const"] is False, f"→ {p}")
    check("G4 kb_index 与 setting_schema 用同一个 get_skip_dirs 函数",
          p["same_kb"] is True, f"→ {p}")
    check("G4 bridge 实际生效的 _skip_dirs 与单源一致",
          p["same_bridge"] is True, f"→ {p}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== G5

def case_path_kind_multi_anchor():
    print("\n[G5] path_kind：锚点可配 + 旧锚点兼容（三态）")
    root = build_sandbox()
    # 刻意用**中性的旧布局占位**（测试只关心「锚点不同」这件事，
    # 不关心真实目录叫什么；真实结构不进发布树）
    OLD = r"旧库\人物\子类目\角色甲.md"
    NEW = r"设定\人物\子类目\角色甲.md"
    WORLD = r"旧库\地点\乙城.md"
    INDEX = r"旧库\人物\索引页.md"
    OLD_ANCHOR = "旧库/人物"

    code = PRELUDE + '''
import os
from utils.setting_schema import path_kind
def k(p): return path_kind({"path": p})
_ticket(old=k(os.environ["OLD"]), new=k(os.environ["NEW"]),
        world=k(os.environ["WORLD"]), index=k(os.environ["INDEX"]),
        nopath=path_kind({"name": "x"}), nodict=path_kind(None))
'''
    env = {"OLD": OLD, "NEW": NEW, "WORLD": WORLD, "INDEX": INDEX}
    p, out, err = run_py(root, code, env)
    if p is None:
        check("G5 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("G5 默认锚点：新布局人物 → True", p["new"] is True, f"→ {p['new']}")
    check("G5 默认锚点：旧布局 → None（不表态，交回其它判据）",
          p["old"] is None, f"→ {p['old']}")
    check("G5 无 path / 非 dict → None（这个函数不猜）",
          p["nopath"] is None and p["nodict"] is None, f"→ {p['nopath']},{p['nodict']}")

    # 加兼容锚点后：旧布局恢复 True，且新布局不受影响
    _write_local(root, "obsidian:\n  extra_character_anchors: ['" + OLD_ANCHOR + "']\n")
    p2, out2, err2 = run_py(root, code, env)
    _reset_local(root)
    if p2 is None:
        check("G5b 子进程产出结果", False, f"{out2[-300:]} {err2[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("G5 **配了兼容锚点后，旧布局人物恢复 True**（这才是防静默退化的关键）",
          p2["old"] is True, f"→ {p2['old']}")
    check("G5 兼容锚点不干扰新布局", p2["new"] is True, f"→ {p2['new']}")
    check("G5 同一根下非人物目录 → False（仍能明确否决）",
          p2["world"] is False, f"→ {p2['world']}")
    check("G5 人物目录下无子目录的索引页 → None（交回名字判据）",
          p2["index"] is None, f"→ {p2['index']}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== G6

def case_missing_dir_warns():
    print("\n[G6] vault_dirs 指向不存在的目录 → 必须打告警（曾静默 continue）")
    root = build_sandbox()
    v = root / "EmptyVault"
    v.mkdir(parents=True, exist_ok=True)
    code = PRELUDE + '''
import os, obsidian_bridge as ob
sc = ob.scan_vault(os.environ["V"])
_ticket(n=len(sc["characters"]))
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    if p is None:
        check("G6 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("G6 目录不存在有告警（提示去改 vault_dirs）",
          "目录不存在" in out, f"→ {out[-300:]}")
    check("G6 三类全空有汇总告警（下游注入恒空的可见化）",
          "扫描结果全空" in out, f"→ {out[-300:]}")
    check("G6 空 vault 不炸、返回空列表", p["n"] == 0, f"→ {p}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== G7

def case_broken_local_yaml_warns():
    print("\n[G7] system.local.yaml 解析失败 → 必须打告警，不许静默降级")
    root = build_sandbox()
    _write_local(root, "obsidian:\n  vault_dirs: [未闭合\n")
    code = PRELUDE + '''
from utils.setting_schema import get_vault_dirs, get_skip_dirs
_ticket(vd=get_vault_dirs(), sd=sorted(get_skip_dirs()))
'''
    p, out, err = run_py(root, code)
    _reset_local(root)
    if p is None:
        check("G7 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("G7 坏配置有告警（用户能发现自己的覆盖没生效）",
          "解析 config/system.local.yaml 失败" in out, f"→ {out[-300:]}")
    check("G7 坏配置时安全回退默认值（不炸）",
          p["vd"]["characters"] == "设定/人物" and len(p["sd"]) == 4, f"→ {p}")
    shutil.rmtree(root, ignore_errors=True)


def main():
    print("=" * 70)
    print("vault 目录配置化（A1）回归 —— 不依赖 history 快照")
    print("=" * 70)
    case_defaults()
    case_custom_vault_dirs_actually_scans()
    case_skip_dirs_is_append()
    case_two_sources_same_object()
    case_path_kind_multi_anchor()
    case_missing_dir_warns()
    case_broken_local_yaml_warns()
    print("\n" + "=" * 70)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
