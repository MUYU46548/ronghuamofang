# -*- coding: utf-8 -*-
"""多库的五类目录按库独立（2026-11-08，0.9.1 验收第 2 条 / 账本 P1-3）。

## 要治的病

多库能同时读了，但“角色 / 地点 / 势力 / 概念 / 年表”五类目录一直是**全局一套**
（`obsidian.vault_dirs`），所有库都用它扫。两个库结构不一样时，辅库必然扫出 0 条 ——
而且**不报错**：界面上“检查同名冲突”一条都没有，作者只会以为冲突检查坏了。

## 判据（每条都能变红）

- V1 一个库用**它自己的**目录能扫到条目（另一套目录扫不到）
- V2 没配目录的库：如实回报 `dirs_configured=false`，**不假装扫到了**
- V3 `scan_vault_multi` 用两个结构不同的库 → 两边条目都在，且各自计数正确
- V4 一库配了、一库没配 → 配的那个照常出条目，没配的进 `unconfigured_vaults`（点名叫得出来）
- V5 目录入参校验：未知键 / 绝对路径 / 盘符 / `..` 一律拒，不静默忽略

用法：python tests/unit/test_vault_dirs_per_vault.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


import obsidian_bridge as OB                                        # noqa: E402
from nf_api_domains import project as dom_proj                       # noqa: E402


def _entry(path, name, kind="人物"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nname: %s\ntype: %s\n---\n\n%s 的一段正文，够长以免被判成空骨架页。\n"
                    % (name, kind, name), encoding="utf-8")


def make_vault_a(base):
    """库 A：设定/人物、设定/地点、设定/概念。"""
    _entry(base / "设定" / "人物" / "甲.md", "甲")
    _entry(base / "设定" / "地点" / "旧码头.md", "旧码头", "地点")
    return base


def make_vault_b(base):
    """库 B：角色、场景、体系（与 A 完全不同的结构）。"""
    _entry(base / "角色" / "乙.md", "乙")
    _entry(base / "场景" / "西堤.md", "西堤", "地点")
    return base


DIRS_A = {"characters": "设定/人物", "locations": "设定/地点",
          "factions": "", "concepts": "设定/概念", "timeline": ""}
DIRS_B = {"characters": "角色", "locations": "场景",
          "factions": "体系", "concepts": "", "timeline": ""}


def case_own_dirs():
    print("\n【V1】每个库用自己的目录")
    tmp = Path(tempfile.mkdtemp(prefix="nf_vd_"))
    a = make_vault_a(tmp / "A")
    b = make_vault_b(tmp / "B")

    ra = OB.scan_vault(a, dirs=DIRS_A)
    check("V1 库 A 用自己的目录扫到角色", [c["name"] for c in ra["characters"]] == ["甲"],
          [c["name"] for c in ra["characters"]])
    check("V1 库 A 扫到地点", [w["name"] for w in ra["world"]] == ["旧码头"],
          [w["name"] for w in ra["world"]])

    rb = OB.scan_vault(b, dirs=DIRS_B)
    check("V1 库 B 用自己的目录扫到角色", [c["name"] for c in rb["characters"]] == ["乙"],
          [c["name"] for c in rb["characters"]])
    check("V1 库 B 扫到地点", [w["name"] for w in rb["world"]] == ["西堤"],
          [w["name"] for w in rb["world"]])

    # 反向：拿 A 的目录去扫 B —— 必须扫空（这正是旧实现的实际效果）
    rb_wrong = OB.scan_vault(b, dirs=DIRS_A)
    check("V1 反向：用别的库的目录扫 B → 一条都扫不到（旧行为就是这个）",
          not rb_wrong["characters"] and not rb_wrong["world"],
          ([c["name"] for c in rb_wrong["characters"]],
           [w["name"] for w in rb_wrong["world"]]))


def case_unconfigured():
    print("\n【V2】没配目录的库：如实说没配，不假装扫到了")
    tmp = Path(tempfile.mkdtemp(prefix="nf_vd2_"))
    b = make_vault_b(tmp / "B")
    r = OB.scan_vault(b, dirs={})
    check("V2 一条都没扫到", not r["characters"] and not r["world"])
    check("V2 meta.dirs_configured = False",
          (r.get("meta") or {}).get("dirs_configured") is False, r.get("meta"))
    r2 = OB.scan_vault(b, dirs=DIRS_B)
    check("V2 配了以后 dirs_configured = True",
          (r2.get("meta") or {}).get("dirs_configured") is True)


def _multi(roots):
    old = OB.get_vault_roots
    OB.get_vault_roots = lambda: roots
    try:
        return OB.scan_vault_multi()
    finally:
        OB.get_vault_roots = old


def case_multi_two_structures():
    print("\n【V3】两个结构不同的库同时读：各自都扫到")
    tmp = Path(tempfile.mkdtemp(prefix="nf_vd3_"))
    a = make_vault_a(tmp / "A")
    b = make_vault_b(tmp / "B")
    roots = [
        {"path": str(a), "name": "A", "primary": True, "skip_dirs": None,
         "enabled": True, "dirs": DIRS_A},
        {"path": str(b), "name": "B", "primary": False, "skip_dirs": [],
         "enabled": True, "dirs": DIRS_B},
    ]
    r = _multi(roots)
    names = sorted(c["name"] for c in r["characters"])
    check("V3 两个库的角色都在", names == ["乙", "甲"], names)
    world = sorted(w["name"] for w in r["world"])
    check("V3 两个库的地点都在", set(world) == {"西堤", "旧码头"}, world)
    per = {x["name"]: x for x in r["meta"]["vaults"]}
    check("V3 A 计数正确", per["A"]["characters"] == 1 and per["A"]["world"] == 1, per["A"])
    check("V3 B 计数正确", per["B"]["characters"] == 1 and per["B"]["world"] == 1, per["B"])
    check("V3 每库都标了 dirs_configured",
          per["A"]["dirs_configured"] is True and per["B"]["dirs_configured"] is True)
    check("V3 非当前库的条目带库名前缀（不撞 key）",
          any(str(c.get("path", "")).startswith("B/") for c in r["characters"]),
          [c.get("path") for c in r["characters"]])
    check("V3 没有 unconfigured", r["meta"]["unconfigured_vaults"] == [])


def case_one_configured():
    print("\n【V4】一库配了、一库没配")
    tmp = Path(tempfile.mkdtemp(prefix="nf_vd4_"))
    a = make_vault_a(tmp / "A")
    b = make_vault_b(tmp / "B")
    roots = [
        {"path": str(a), "name": "A", "primary": True, "skip_dirs": None,
         "enabled": True, "dirs": DIRS_A},
        {"path": str(b), "name": "B", "primary": False, "skip_dirs": [],
         "enabled": True, "dirs": {}},
    ]
    r = _multi(roots)
    check("V4 配了的库照常出条目", [c["name"] for c in r["characters"]] == ["甲"],
          [c["name"] for c in r["characters"]])
    un = r["meta"]["unconfigured_vaults"]
    check("V4 没配的库被点名", [x["name"] for x in un] == ["B"], un)
    per = {x["name"]: x for x in r["meta"]["vaults"]}
    check("V4 没配的库计数为 0 且标 False",
          per["B"]["characters"] == 0 and per["B"]["dirs_configured"] is False, per["B"])


def case_validate():
    print("\n【V5】目录入参校验（不静默忽略）")
    ok, msg, dirs = dom_proj._clean_vault_dirs({"characters": "设定/人物",
                                                "locations": "/abs/path"})
    check("V5 绝对路径被拒", not ok and "相对目录" in msg, msg)
    ok2, msg2, _ = dom_proj._clean_vault_dirs({"characters": "设定/../别人的库"})
    check("V5 .. 被拒", not ok2, msg2)
    ok3, msg3, _ = dom_proj._clean_vault_dirs({"characters": "D:/vault/人物"})
    check("V5 盘符被拒", not ok3, msg3)
    ok4, msg4, _ = dom_proj._clean_vault_dirs({"character": "人物"})
    check("V5 未知键被拒且点名合法键", not ok4 and "characters" in msg4, msg4)
    ok5, _, good = dom_proj._clean_vault_dirs({"characters": "设定/人物/",
                                               "timeline": None})
    check("V5 尾部斜杠去掉、缺键补空串、None 当空",
          ok5 and good["characters"] == "设定/人物" and good["timeline"] == "", good)


def case_inherit():
    print("\n【V7】书签库没单独配 → 沿用当前库（老用户结构相同不能突然扫空）")
    import json as _json
    from pathlib import Path as _P
    from utils import config_io as _cio

    tmp = _P(tempfile.mkdtemp(prefix="nf_vd7_"))
    a = make_vault_a(tmp / "A")
    b = make_vault_b(tmp / "B")
    (tmp / "data" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "state" / "vaults.json").write_text(_json.dumps({"items": [
        {"path": b.as_posix(), "name": "B", "enabled": True, "skip_dirs": []},
        {"path": (tmp / "C").as_posix(), "name": "C", "enabled": True,
         "skip_dirs": [], "dirs": {"characters": "角色"}},
    ]}, ensure_ascii=False), encoding="utf-8")
    olds = (OB._get_vault_path, OB.get_vault_model, OB.get_vault_dirs, _cio.ROOT)
    OB._get_vault_path = lambda: a
    OB.get_vault_model = lambda: "multi"
    OB.get_vault_dirs = lambda: dict(DIRS_A)
    _cio.ROOT = tmp
    try:
        roots = OB.get_vault_roots()
    finally:
        (OB._get_vault_path, OB.get_vault_model, OB.get_vault_dirs, _cio.ROOT) = olds
    byname = {r["name"]: r for r in roots}
    check("V7 当前库标 local", byname.get("A", {}).get("dirs_source") == "local", byname.get("A"))
    check("V7 没配的库标 inherited", byname.get("B", {}).get("dirs_source") == "inherited",
          byname.get("B"))
    check("V7 inherited 的库拿到当前库那套目录",
          byname.get("B", {}).get("dirs", {}).get("characters") == "设定/人物",
          byname.get("B", {}).get("dirs"))
    check("V7 自己配过的库标 bookmark",
          byname.get("C", {}).get("dirs_source") == "bookmark", byname.get("C"))


def case_suspect():
    print("\n【V8】沿用当前库目录却一条没扫到 → 点名（不再“扫空且不报错”）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_vd8_"))
    a = make_vault_a(tmp / "A")
    b = make_vault_b(tmp / "B")
    roots = [
        {"path": str(a), "name": "A", "primary": True, "skip_dirs": None,
         "enabled": True, "dirs": DIRS_A, "dirs_source": "local"},
        # 结构不同的库 + 沿用 A 的目录 → 扫不到
        {"path": str(b), "name": "B", "primary": False, "skip_dirs": [],
         "enabled": True, "dirs": DIRS_A, "dirs_source": "inherited"},
    ]
    r = _multi(roots)
    sus = r["meta"]["suspect_inherited_vaults"]
    check("V8 结构不同的库被点名", [x["name"] for x in sus] == ["B"], sus)
    check("V8 配了目录的库照常出条目", [c["name"] for c in r["characters"]] == ["甲"],
          [c["name"] for c in r["characters"]])


def case_wiring():
    print("\n【V6】接线：路由 + 来源标注")
    src = (ROOT / "scripts" / "nf_api_domains" / "project.py").read_text(encoding="utf-8")
    check("V6 端点已注册",
          any(r[1] == "/config/vaults/dirs" for r in dom_proj.ROUTES))
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("V6 nf_api 有转发",
          "handle_config_vaults_dirs_set(self, body)" in api_src)
    check("V6 每库带 dirs_source（local / bookmark）",
          '"dirs_source"' in src)
    ob_src = (ROOT / "scripts" / "obsidian_bridge.py").read_text(encoding="utf-8")
    check("V6 多库扫描把 dirs 传进 scan_vault", 'dirs=r.get("dirs")' in ob_src)
    check("V6 scan_vault 接受 dirs 参数", "def scan_vault(vault_path=None, skip_dirs=None, "
          "tag_root=None, dirs=None)" in ob_src)


def main():
    print("=" * 66)
    print("多库：五类目录按库独立")
    case_own_dirs()
    case_unconfigured()
    case_multi_two_structures()
    case_one_configured()
    case_validate()
    case_inherit()
    case_suspect()
    case_wiring()
    print("\n" + "-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
