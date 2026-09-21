# -*- coding: utf-8 -*-
"""obsidian 联动 + 大纲精修的**完整性**自检（2026-09-21 修复配套）。

## 为什么需要这套用例

2026-09-21 就绪度审计挖出 5 个缺陷，全部有实证复现。它们的共同特征是
**静默失败 / 假成功** —— 表面上一切正常，实际功能从未生效或已损坏数据：

| 编号 | 缺陷 | 危害 |
|---|---|---|
| F1 | `scan_vault` 父目录 `rglob` 跨类目污染 + 重复 | 4 个文件的 vault 扫出 6 个"角色"；归档数据「480 条只有 84 条真人物」的生产者侧根因 |
| F2 | `build_setting_from_vault` 整体替换**且不备份** | 实测把 stage1 从素材归并的角色/剧情碎片/时间线**全部删掉且不可恢复** |
| F3 | 同函数失败返回 `(False, msg)`、成功返回 dict | 调用方 `setting['meta']` 在失败路径抛 TypeError |
| F4 | `stage4` KB 注入裸 `except Exception: pass` | vault 未配置/配错时注入恒为空，**无任何提示** |
| F5 | `check_consistency` 恒返回全零 | 打印「违例 0 个」像是"检查通过"，实际 `conflicts`/`locked_violations` 硬编码空列表 |
| F6 | `refine_outline` 体检 FAIL 仍报「精修完成」 | 子会话产出 0 节点的废稿，用户看到的是成功 |

## 隔离策略

在**临时项目根**跑（与项目既有策略一致）：复制 `scripts/` + `config/`，
真实仓库零污染。全部零 LLM 调用。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []

NEEDED = ("obsidian_bridge.py", "obsidian_integrate.py", "obsidian_postprocess.py",
          "stage4_writing.py", "refine_outline.py", "outline_review.py",
          "stage2_outline.py")


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def build_sandbox(prefix="nf_oi_"):
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "scripts" / "utils").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    for sub in ("data/state", "data/setting", "data/outline/history",
                "data/chapters/raw"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for n in NEEDED:
        s = REPO / "scripts" / n
        if s.exists():
            shutil.copy2(s, root / "scripts" / n)
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"),
                    dirs_exist_ok=True)
    shutil.copytree(REPO / "prompts", root / "prompts",
                    ignore=shutil.ignore_patterns("__pycache__", "history"),
                    dirs_exist_ok=True)
    for n in ("system.yaml", "project.yaml"):
        s = REPO / "config" / n
        if s.exists():
            shutil.copy2(s, root / "config" / n)
    return root


def make_vault(root):
    """按 docstring 约定造一个 vault：
    01 人物 下 2 个（含 1 个 locked）、02 地点 1 个、04 概念 1 个、06 年表 1 个。
    """
    v = root / "MyVault"
    for d in ("03 设定/01 人物/01 主要角色", "03 设定/02 地点",
              "03 设定/04 概念", "06 年表"):
        (v / d).mkdir(parents=True, exist_ok=True)
    (v / "03 设定/01 人物/01 主要角色" / "露汐.md").write_text(
        "---\nname: 露汐\ntags: [主角]\ntype: 人物\nlocked: true\n"
        "description: 绒花科学院研究员。\n---\n露汐擅长冰封术。\n", encoding="utf-8")
    (v / "03 设定/01 人物/01 主要角色" / "罗霄.md").write_text(
        "---\nname: 罗霄\ntags: [配角]\ntype: 人物\n---\n罗霄管档案室。\n",
        encoding="utf-8")
    (v / "03 设定/02 地点" / "沙都.md").write_text(
        "---\nname: 沙都\ntype: 地点\n---\n沙都分七区。\n", encoding="utf-8")
    (v / "03 设定/04 概念" / "绒花帝国魔法管制法.md").write_text(
        "---\nname: 绒花帝国魔法管制法\ntype: 概念\n---\n法规正文。\n",
        encoding="utf-8")
    (v / "06 年表" / "大事记.md").write_text(
        "---\nname: 大事记\n---\n某年冬，封锁开始。\n", encoding="utf-8")
    return v


def run_py(root, code, env_extra=None, timeout=180):
    script = root / "_case.py"
    script.write_text(code, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run([sys.executable, str(script)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           cwd=str(root), timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT"
    out = p.stdout or ""
    if "__RESULT__" in out:
        payload = out.split("__RESULT__", 1)[1].splitlines()[0]
        try:
            return json.loads(payload), out, p.stderr or ""
        except Exception:                          # noqa: BLE001
            return None, out, p.stderr or ""
    return None, out, p.stderr or ""


PRELUDE = '''
import sys, os, json
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
from utils.file_io import read_text, write_text

def _ticket(**kw):
    print("__RESULT__" + json.dumps(kw, ensure_ascii=False))
'''


# ====================================================================== F1

def case_scan_no_pollution_no_dup():
    """F1：scan_vault 不得跨类目污染、不得重复。"""
    print("\n【F1】scan_vault 无跨类目污染 / 无重复")
    root = build_sandbox("nf_oi_f1_")
    v = make_vault(root)
    code = PRELUDE + '''
import obsidian_bridge as ob
sc = ob.scan_vault(os.environ["V"])
_ticket(char_names=[c["name"] for c in sc["characters"]],
        world_names=[w["name"] for w in sc["world"]],
        timeline_names=[t["name"] for t in sc["timeline"]],
        locked=[c["name"] for c in sc["characters"] if c["locked"]],
        counts=sc["meta"])
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    if p is None:
        check("F1 子进程产出结果", False, f"stdout={out[-400:]} stderr={err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    chars, world = p["char_names"], p["world_names"]
    print(f"  characters={chars}\n  world={world}  timeline={p['timeline_names']}")
    check("F1 角色恰为 2 个（露汐/罗霄）", len(chars) == 2 and set(chars) == {"露汐", "罗霄"},
          f"→ {chars}")
    check("F1 **无重复**（每个名字只出现一次）",
          len(chars) == len(set(chars)), f"→ {chars}")
    check("F1 **地点未被当成角色**（沙都不在 characters）", "沙都" not in chars,
          f"→ 污染: {chars}")
    check("F1 **概念未被当成角色**（管制法不在 characters）",
          "绒花帝国魔法管制法" not in chars, f"→ 污染: {chars}")
    check("F1 地点正确归入 world", "沙都" in world, f"→ {world}")
    check("F1 概念正确归入 world", "绒花帝国魔法管制法" in world, f"→ {world}")
    check("F1 时间线独立归类（不混入 characters/world）",
          p["timeline_names"] == ["大事记"]
          and "大事记" not in chars and "大事记" not in world,
          f"→ {p['timeline_names']}")
    check("F1 locked 检出且不重复", p["locked"] == ["露汐"], f"→ {p['locked']}")
    check("F1 meta 计数与列表一致",
          p["counts"]["character_count"] == len(chars)
          and p["counts"]["world_count"] == len(world),
          f"→ {p['counts']}")
    shutil.rmtree(root, ignore_errors=True)


def case_scan_parent_dir_glob_only():
    """F1 反向验证：父目录 `03 设定` 只 glob，不递归；直接子文件仍收编。

    这是修复的核心机制 —— 若把父目录改回 rglob，污染立刻回来。
    """
    print("\n【F1b】父目录只 glob（直接子文件收编，子类目不递归）")
    root = build_sandbox("nf_oi_f1b_")
    v = make_vault(root)
    # 直接放在 03 设定 下的松散文件（应被兜底收编为 world，而不是被漏掉）
    (v / "03 设定" / "散装说明.md").write_text(
        "---\nname: 散装说明\n---\n直接放在父目录下的条目。\n", encoding="utf-8")
    code = PRELUDE + '''
import obsidian_bridge as ob
sc = ob.scan_vault(os.environ["V"])
_ticket(char_names=[c["name"] for c in sc["characters"]],
        world_names=[w["name"] for w in sc["world"]])
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    if p is None:
        check("F1b 子进程产出结果", False, f"{out[-300:]} {err[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("F1b 父目录下的松散文件被收编为 world（用 glob 兜底）",
          "散装说明" in p["world_names"], f"→ {p['world_names']}")
    check("F1b 松散文件**不**被误判为角色（父目录不进 char_dirs）",
          "散装说明" not in p["char_names"], f"→ {p['char_names']}")
    check("F1b 收编松散文件的同时**没有**把子类目角色/地点卷进来",
          len(p["char_names"]) == 2 and len(p["world_names"]) == 3,
          f"→ chars={p['char_names']} world={p['world_names']}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== F2/F3

def case_import_backs_up_before_overwrite():
    """F2：整体替换前必须备份，且备份里保留原内容。"""
    print("\n【F2】vault 导入前自动备份（防静默数据丢失）")
    root = build_sandbox("nf_oi_f2_")
    v = make_vault(root)
    original = {
        "characters": [{"name": "素材角色", "role": "从素材归并来的重要角色设定文本"}],
        "world": {"地理": "素材地理"},
        "plot_fragments": ["素材碎片：丢失的信"],
        "timeline": ["素材时间线：某年春"],
    }
    (root / "data" / "setting" / "setting.json").write_text(
        json.dumps(original, ensure_ascii=False, indent=1), encoding="utf-8")
    code = PRELUDE + '''
import os, json
import obsidian_integrate as oi
oi.build_setting_from_vault(os.environ["V"], "data/setting/setting.json")
hist = sorted(os.listdir("data/setting/history"))
bk = json.loads(read_text("data/setting/history/" + hist[0])) if hist else {}
now = json.loads(read_text("data/setting/setting.json"))
_ticket(history=hist,
        backup_chars=[c["name"] for c in bk.get("characters", [])],
        backup_frags=len(bk.get("plot_fragments", [])),
        backup_timeline=len(bk.get("timeline", [])),
        now_chars=[c["name"] for c in now.get("characters", [])],
        now_frags=len(now.get("plot_fragments", [])))
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    if p is None:
        check("F2 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  history={p['history']} backup_chars={p['backup_chars']}")
    check("F2 覆盖前产生了备份", len(p["history"]) == 1, f"→ {p['history']}")
    check("F2 备份命名沿用 setting_v{N}.json 约定",
          p["history"] and p["history"][0].startswith("setting_v"),
          f"→ {p['history']}")
    check("F2 **备份保留了被替换的原角色**", p["backup_chars"] == ["素材角色"],
          f"→ {p['backup_chars']}")
    check("F2 备份保留了剧情碎片与时间线",
          p["backup_frags"] == 1 and p["backup_timeline"] == 1,
          f"→ frags={p['backup_frags']} tl={p['backup_timeline']}")
    # 语义未变（刻意保守）：仍是整体替换，素材角色不在新设定集里
    check("F2 覆盖语义未变（仍是整体替换，素材角色被换掉）",
          "素材角色" not in p["now_chars"], f"→ {p['now_chars']}")
    check("F2 覆盖后能用备份恢复（备份内容自洽可解析）",
          p["backup_chars"] and p["backup_frags"] == 1)
    shutil.rmtree(root, ignore_errors=True)


def case_import_return_type_consistent():
    """F3：失败路径必须抛异常，不能返回与成功不同的类型。

    旧实现失败返回 `(False, msg)`、成功返回 dict → 调用方 `setting['meta']`
    在失败时抛 `TypeError: tuple indices must be integers`，错误信息毫无指向性。
    """
    print("\n【F3】导入的返回类型一致（失败抛 ValueError）")
    root = build_sandbox("nf_oi_f3_")
    v = make_vault(root)
    code = PRELUDE + '''
import obsidian_integrate as oi
out = {}
# 合法路径 → 返回 dict
ok_val = oi.build_setting_from_vault(os.environ["V"], "data/setting/setting.json")
out["ok_is_dict"] = isinstance(ok_val, dict)
out["ok_has_meta"] = "meta" in ok_val
# 越界路径 → 必须抛 ValueError（而不是返回 tuple）
try:
    oi.build_setting_from_vault(os.environ["V"], "C:/outside/setting.json")
    out["bad_raises"] = None
except ValueError as e:
    out["bad_raises"] = "ValueError"
    out["bad_msg"] = str(e)[:60]
except Exception as e:
    out["bad_raises"] = type(e).__name__
_ticket(**out)
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    if p is None:
        check("F3 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("F3 成功路径返回 dict", p["ok_is_dict"] is True, f"→ {p['ok_is_dict']}")
    check("F3 成功路径 dict 含 meta", p["ok_has_meta"] is True)
    check("F3 **失败路径抛 ValueError**（不再返回二元组）",
          p["bad_raises"] == "ValueError", f"→ {p['bad_raises']}")
    check("F3 报错文案可行动", "data/" in (p.get("bad_msg") or ""),
          f"→ {p.get('bad_msg')}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== F4

def case_kb_injection_not_silent():
    """F4：KB 注入的三种状态都要有可见输出，不能裸 except 吞掉。"""
    print("\n【F4】KB 注入不再静默降级")
    src = (REPO / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("F4 不再有裸 `except Exception: pass` 吞掉 KB 注入",
          "except Exception:\n        pass" not in src, "→ 仍有裸 pass")
    check("F4 区分「未配置 vault」（未启用，不是失败）",
          "is_vault_configured" in src, "→ 未做未配置判断")
    check("F4 未命中时有提示", "未匹配到相关词条" in src, "→ 未命中静默")
    check("F4 失败时打印（不再吞）", "知识库注入失败" in src, "→ 失败静默")
    check("F4 有「一次性提示」机制（避免逐章刷屏）",
          "_kb_notice_once" in src and "_KB_NOTICE" in src, "→ 缺失")

    # 运行时验证：提示只打印一次
    root = build_sandbox("nf_oi_f4_")
    code = PRELUDE + '''
import io, contextlib
import stage4_writing as s4
buf = io.StringIO()
s4._KB_NOTICE["shown"] = False
with contextlib.redirect_stdout(buf):
    s4._kb_notice_once("提示A")
    s4._kb_notice_once("提示A")
    s4._kb_notice_once("提示A")
_ticket(printed=buf.getvalue(), count=buf.getvalue().count("提示A"))
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("F4 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    check("F4 同一提示每进程只打印一次", p["count"] == 1, f"→ {p['count']}")
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== F5

def case_check_consistency_is_honest():
    """F5：不得再假装「检查通过」；且滑动窗口应能召回未被贪心切片吃掉的名字。"""
    print("\n【F5】check_consistency 诚实化 + 召回修复")
    root = build_sandbox("nf_oi_f5_")
    v = make_vault(root)
    code = PRELUDE + '''
import obsidian_bridge as ob
sc = ob.scan_vault(os.environ["V"])
txt = ("露汐与暮雨在沙都对峙，暮雨质问封锁法令。暮雨说这里不该有封锁。"
       "露汐沉默，暮雨继续追问，暮雨的声音在沙都的回廊里回荡。")
cc = ob.check_consistency(txt, vault_data=sc)
_ticket(keys=sorted(cc.keys()),
        implemented=cc.get("implemented"),
        unimplemented=cc.get("unimplemented"),
        caveat=cc.get("caveat", ""),
        warn_names=[w["entry_name"] for w in cc["warnings"]],
        conflicts=cc["conflicts"], locked=cc["locked_violations"])
'''
    p, out, err = run_py(root, code, {"V": str(v)})
    if p is None:
        check("F5 子进程产出结果", False, f"{out[-400:]} {err[-400:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  warn_names={p['warn_names']}")
    check("F5 返回值显式声明已实现的检查",
          isinstance(p["implemented"], list) and p["implemented"],
          f"→ {p['implemented']}")
    check("F5 返回值显式声明**未实现**的检查",
          isinstance(p["unimplemented"], list)
          and any("locked" in u for u in p["unimplemented"])
          and any("conflict" in u for u in p["unimplemented"]),
          f"→ {p['unimplemented']}")
    check("F5 带 caveat 说明（空结果 ≠ 无问题）",
          "不代表" in p["caveat"] or "未实现" in p["caveat"], f"→ {p['caveat'][:60]}")
    check("F5 **滑动窗口召回了未登记角色「暮雨」**（旧贪心切片恒为空）",
          "暮雨" in p["warn_names"], f"→ {p['warn_names']}")
    check("F5 已知角色未被误报为疑似新角色",
          not any(n in ("露汐", "罗霄") for n in p["warn_names"]),
          f"→ 误报 {p['warn_names']}")
    check("F5 已知地点未被误报", "沙都" not in p["warn_names"], f"→ {p['warn_names']}")
    check("F5 conflicts/locked_violations 仍为空（诚实标注未实现，不伪造结果）",
          p["conflicts"] == [] and p["locked"] == [])
    shutil.rmtree(root, ignore_errors=True)


# ====================================================================== F6

def case_refine_outline_no_false_success():
    """F6：大纲体检 FAIL 时不得回报「精修完成」。"""
    print("\n【F6】大纲精修不再假成功")
    root = build_sandbox("nf_oi_f6_")
    # 一个**结构合法但内容为空**的大纲：四节齐全、有关键节点标题、
    # 有预计章节数 —— 能过 check_global_outline，但体检判 FAIL。
    bad = """# 《测试书》整体大纲

## 起
开端。

## 承
发展。

## 转
高潮。

## 合
结局。

## 关键节点
（无）

## 预计章节数
12

## 章节规划
（无）
"""
    (root / "data" / "outline" / "global.md").write_text(bad, encoding="utf-8")
    (root / "data" / "setting" / "setting.json").write_text(
        json.dumps({"characters": [], "world": {}, "plot_fragments": [],
                    "timeline": []}, ensure_ascii=False), encoding="utf-8")
    code = PRELUDE + '''
import yaml
from utils.fake_client import FakeClient
import refine_outline as ro

cfg = yaml.safe_load(read_text("config/system.yaml"))
proj = yaml.safe_load(read_text("config/project.yaml"))

import utils.fake_client as fc
_orig = fc.FakeClient.run_task
def patched(self, task_file, workdir=None, model=None):
    name = str(task_file).split(os.sep)[-1]
    if name.startswith("stage2"):
        # 子会话产出「0 节点」的废稿（体检必 FAIL）
        write_text("data/outline/global.md",
                   "# 《测试书》整体大纲\\n\\n## 起\\n开端。\\n\\n## 承\\n发展。\\n\\n"
                   "## 转\\n高潮。\\n\\n## 合\\n结局。\\n\\n## 关键节点\\n（无）\\n\\n"
                   "## 预计章节数\\n12\\n\\n## 章节规划\\n（无）\\n")
        return {"exit_code": 0, "stdout_tail": "", "tokens": 10, "tokens_out": 5,
                "cost_yuan": 0.0, "estimated": True, "provider": "fake",
                "model": "fake", "requests": 1}
    return _orig(self, task_file, workdir=workdir, model=model)
fc.FakeClient.run_task = patched

ok, msg = ro.run_refine(cfg, proj, "随便提个意见", client=FakeClient(),
                        task_dir="data/state/tasks")
_ticket(ok=ok, msg=msg)
'''
    p, out, err = run_py(root, code)
    if p is None:
        check("F6 子进程产出结果", False, f"stdout={out[-500:]} stderr={err[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} msg={p['msg'][:110]}")
    check("F6 **体检 FAIL 时不再返回成功**（旧行为：ok=True「精修完成」）",
          p["ok"] is False, f"→ ok={p['ok']}")
    check("F6 消息点明「体检未通过」而非「完成」",
          "体检未通过" in p["msg"], f"→ {p['msg'][:80]}")
    check("F6 消息给出问题数或问题内容",
          "结构性" in p["msg"] or "项" in p["msg"], f"→ {p['msg'][:80]}")
    check("F6 消息给出回溯方式（备份可恢复）",
          "history" in p["msg"] or "restore" in p["msg"], f"→ {p['msg'][:80]}")
    check("F6 未谎报「精修完成」", "精修完成" not in p["msg"], f"→ {p['msg'][:80]}")
    shutil.rmtree(root, ignore_errors=True)


def case_refine_outline_ok_path():
    """F6 对照：体检 PASS 的正常路径仍须返回成功（别把修复做成 Always-False）。"""
    print("\n【F6b】对照组：体检 PASS 时仍返回成功")
    root = build_sandbox("nf_oi_f6b_")
    good = """# 《测试书》整体大纲

## 起
露汐在绒花帝国魔法学院授课时收到一封来源不明的信，信纸上有旧案编号。

## 承
她前往沙都档案馆调查，在回廊遭遇阻拦，与档案员罗霄发生对峙，冲突升级。

## 转
调查触及高层利益，露汐被停职，学院内传出关于她的谣言，危机达到顶点。

## 合
她在旧友帮助下取得关键证据，真相揭开后选择公开并留在学院继续任教。

## 关键节点
- 节点1：第1-3章 露汐在学院授课时收到匿名信，发现信纸上有旧案编号，决定私下追查。
- 节点2：第4-6章 她前往沙都档案馆调查，在档案室遭遇罗霄阻拦并发生对峙。
- 节点3：第7-9章 调查触及高层利益，露汐被停职，学院内传出关于她的谣言。
- 节点4：第10-11章 她在旧友帮助下拿到关键证据，但证据在移交途中被夺。
- 节点5：第12-13章 真相揭开，露汐选择公开证据并留在学院继续任教。

## 预计章节数
5

## 章节规划
- 第1-3章（铺垫）：露汐日常与匿名信登场，交代学院与沙都的地理关系。
- 第4-6章（推进）：沙都档案馆调查与罗霄对峙，冲突第一次升级。
- 第7-9章（转折）：露汐被停职与孤立，学院内部谣言四起。
- 第10-11章（推进）：关键证据的得与失，旧友关系受到考验。
- 第12-13章（收束）：真相揭开与露汐的最终选择。
"""
    (root / "data" / "outline" / "global.md").write_text(good, encoding="utf-8")
    (root / "data" / "setting" / "setting.json").write_text(json.dumps({
        "characters": [{"name": "露汐"}, {"name": "罗霄"}],
        "world": {}, "plot_fragments": [], "timeline": []}, ensure_ascii=False),
        encoding="utf-8")
    code = PRELUDE + '''
import yaml
from utils.fake_client import FakeClient
import refine_outline as ro

cfg = yaml.safe_load(read_text("config/system.yaml"))
proj = yaml.safe_load(read_text("config/project.yaml"))

import utils.fake_client as fc
GOOD = read_text("_good.md")
_orig = fc.FakeClient.run_task
def patched(self, task_file, workdir=None, model=None):
    name = str(task_file).split(os.sep)[-1]
    if name.startswith("stage2"):
        write_text("data/outline/global.md", GOOD)
        return {"exit_code": 0, "stdout_tail": "", "tokens": 10, "tokens_out": 5,
                "cost_yuan": 0.0, "estimated": True, "provider": "fake",
                "model": "fake", "requests": 1}
    return _orig(self, task_file, workdir=workdir, model=model)
fc.FakeClient.run_task = patched

ok, msg = ro.run_refine(cfg, proj, "加强节点3的冲突", client=FakeClient(),
                        task_dir="data/state/tasks")
_ticket(ok=ok, msg=msg)
'''
    (root / "_good.md").write_text(good, encoding="utf-8")
    p, out, err = run_py(root, code)
    if p is None:
        check("F6b 子进程产出结果", False, f"{out[-500:]} {err[-500:]}")
        shutil.rmtree(root, ignore_errors=True)
        return
    print(f"  ok={p['ok']} msg={p['msg'][:110]}")
    check("F6b 体检 PASS → 返回成功（修复未做成 Always-False）",
          p["ok"] is True, f"→ ok={p['ok']} msg={p['msg'][:80]}")
    check("F6b 成功消息含版本号", "v" in p["msg"], f"→ {p['msg'][:60]}")
    shutil.rmtree(root, ignore_errors=True)


def main():
    print("=" * 70)
    print("obsidian 联动 + 大纲精修完整性自检（临时项目根，零 LLM 调用）")
    print("=" * 70)
    for fn in (case_scan_no_pollution_no_dup, case_scan_parent_dir_glob_only,
               case_import_backs_up_before_overwrite, case_import_return_type_consistent,
               case_kb_injection_not_silent, case_check_consistency_is_honest,
               case_refine_outline_no_false_success, case_refine_outline_ok_path):
        try:
            fn()
        except Exception as e:                     # noqa: BLE001
            import traceback
            FAIL.append(fn.__name__)
            print(f"  [FAIL] {fn.__name__} 抛异常: {e}")
            traceback.print_exc()

    print("\n" + "=" * 70)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        for f in FAIL:
            print(f"  FAIL: {f}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
