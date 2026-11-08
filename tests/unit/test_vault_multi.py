# -*- coding: utf-8 -*-
"""多库同时读（B3 B 模型，2026-11-07）。

作者原话：“多库同时生效 B（多库一起读），用户本来就可能不止一个库。”

改之前只有 A 模型：书签能登记多个库，但同一时刻只有 `vault_path` 一个参与扫描。
这份用例钉住 B 模型的四条口径：

1. 扫描面 = 当前库 + 已启用的库（停用的不读）；
2. **同名条目以当前库优先**，被覆盖的进 `meta.conflicts` / 索引 `skipped_same_name`，
   **不静默吞**（“检索到的设定来自错的书”是最贵的一类缺陷）；
3. 每个库用自己的排除项，当前库的本地排除项不串台；
4. 目录不在了/书签坏了 → 进 `dead_roots`，不抛、也不假装读到了。

用法：python tests/unit/test_vault_multi.py
"""
import io
import json
import os
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
                           ("  -> " + str(detail)[:260]) if detail else ""))


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _entry(name, desc):
    return "---\nname: %s\n---\n\n# %s\n\n%s\n" % (name, name, desc)


def _make_vault(base, uniq_name, skip_draft=True):
    """造一个带标准五类目录的最小库（名字全部自造，与真实素材无关）。"""
    _write(base / "设定" / "人物" / ("%s.md" % uniq_name), _entry(
        uniq_name, "这是一条用于测试的虚构人物词条，写着他的来历、习惯与做事方式，"
                   "内容足够长，不会被“空骨架页”判据过滤掉。"))
    _write(base / "设定" / "人物" / "岑野.md", _entry(
        "岑野", "两个库里都出现的人物。当前这份来自各自所属的库，用于验证同名冲突取谁。"))
    _write(base / "设定" / "地理" / "旧码头.md", _entry(
        "旧码头", "一个虚构地点词条，写它的位置、潮汐与常见的人，内容足够长。"))
    if skip_draft:
        _write(base / "草稿" / "随便写写.md", _entry(
            "草稿条目", "这个目录应当被排除项挡住，不进检索；内容故意写得足够长。" + "。" * 30))
    return base


def _setup(tmp):
    """临时项目根：config/system.yaml + data/state/vaults.json + 两个库。"""
    _write(tmp / "config" / "system.yaml",
           "obsidian:\n"
           "  vault_path: %s\n"
           "  vault_dirs:\n"
           "    characters: 设定/人物\n"
           "    locations: 设定/地理\n"
           "    factions: 设定/势力\n"
           "    concepts: 设定/概念\n"
           "    timeline: 设定/年表\n"
           "  skip_dirs: []\n" % (tmp / "vaultA").as_posix())
    va = _make_vault(tmp / "vaultA", "商九")
    vb = _make_vault(tmp / "vaultB", "陶三")
    _write(tmp / "data" / "state" / "vaults.json", json.dumps({
        "items": [
            {"path": vb.as_posix(), "name": "乙库", "enabled": True,
             "skip_dirs": ["草稿"]},
        ]
    }, ensure_ascii=False, indent=2))
    return va, vb


def _with_root(tmp, fn):
    """把配置 ROOT 与 CWD 都切到临时项目根，跑完复原。"""
    from utils import config_io, setting_schema
    old_root, old_cwd = getattr(config_io, "ROOT", None), os.getcwd()
    config_io.ROOT = Path(tmp)
    setting_schema.reset_obsidian_config_cache()
    os.chdir(tmp)
    try:
        return fn()
    finally:
        os.chdir(old_cwd)
        config_io.ROOT = old_root
        setting_schema.reset_obsidian_config_cache()


def main():
    import obsidian_bridge as OB
    from utils import kb_index

    print("\n【1】多库扫描：当前库 + 已启用库；同名条目当前库优先并在 conflicts 里点名")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        va, vb = _setup(tmp)

        def run_multi():
            setting_schema_reset = getattr(OB, "_WARNED", None)
            if setting_schema_reset is not None:
                setting_schema_reset.clear()
            roots = OB.get_vault_roots()
            data = OB.scan_vault_multi()
            return roots, data

        roots, data = _with_root(tmp, run_multi)
        check("两个根都参与（A 当前 + B 已启用）", len(roots) == 2,
              [(r["name"], r["primary"]) for r in roots])
        check("当前库排第一（优先级口径）", roots[0]["primary"] is True, roots[0])
        names = [c["name"] for c in data["characters"]]
        check("A 库的人物在", "商九" in names, names)
        check("B 库的人物也在（多库同时读）", "陶三" in names, names)
        check("同名“岑野”只留一条", names.count("岑野") == 1, names)
        conf = data["meta"]["conflicts"]
        check("同名冲突被点名（谁赢谁被覆盖）",
              any(c["name"] == "岑野" and c["kept"] == "vaultA" and c["dropped"] == "乙库"
                  for c in conf), conf)
        check("非当前库的条目路径带库名前缀（防 dict key 相撞）",
              all(str(c["path"]).startswith("乙库/") for c in data["characters"]
                  if c.get("vault") == "乙库"),
              [c["path"] for c in data["characters"] if c.get("vault") == "乙库"])
        check("B 库自己的排除项生效（草稿不进扫描）",
              not any("草稿" in str(c["path"]) for c in data["characters"] + data["world"]),
              [c["path"] for c in data["characters"] + data["world"]])

    print("\n【2】停用 / 目录失效：不读，但要说出来")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp)
        doc = json.loads((tmp / "data" / "state" / "vaults.json").read_text(encoding="utf-8"))
        doc["items"][0]["enabled"] = False
        _write(tmp / "data" / "state" / "vaults.json", json.dumps(doc, ensure_ascii=False))

        def run_disabled():
            OB._WARNED.clear()
            return OB.get_vault_roots(), OB.scan_vault_multi()

        roots, data = _with_root(tmp, run_disabled)
        check("停用后只剩当前库", len(roots) == 1, [r["name"] for r in roots])
        check("停用库里的人物不出现", "陶三" not in [c["name"] for c in data["characters"]],
              [c["name"] for c in data["characters"]])

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp)
        doc = json.loads((tmp / "data" / "state" / "vaults.json").read_text(encoding="utf-8"))
        doc["items"][0]["path"] = (tmp / "不存在的库").as_posix()
        _write(tmp / "data" / "state" / "vaults.json", json.dumps(doc, ensure_ascii=False))

        def run_dead():
            OB._WARNED.clear()
            return OB.scan_vault_multi()

        data = _with_root(tmp, run_dead)
        check("目录不见了进 dead_roots（不静默少读）",
              any("不存在的库" in str(d.get("path")) for d in data["meta"]["dead_roots"]),
              data["meta"]["dead_roots"])

    print("\n【3】单库模型：回到旧行为（只读当前库）")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp)
        local = tmp / "config" / "system.local.yaml"
        _write(local, "obsidian:\n  vault_model: single\n")

        def run_single():
            OB._WARNED.clear()
            return OB.get_vault_roots(), OB.scan_vault_multi()

        roots, data = _with_root(tmp, run_single)
        check("single → 只有一个根", len(roots) == 1, [r["name"] for r in roots])
        check("single → B 库人物不出现", "陶三" not in [c["name"] for c in data["characters"]],
              [c["name"] for c in data["characters"]])

    print("\n【4】多库倒排索引：两个库都能被检索到；同名去重并留痕")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        va, vb = _setup(tmp)

        def run_index():
            OB._WARNED.clear()
            roots = [{"path": r["path"], "skip_dirs": r.get("skip_dirs")}
                     for r in OB.get_vault_roots()]
            return kb_index.build_index(roots, persist=False)

        idx = _with_root(tmp, run_index)
        check("索引里两个库的文件都在", len(idx.get("vaults") or []) == 2, idx.get("vaults"))
        hits_b = kb_index.search("陶三", index=idx, top_k=5, vault_path=str(va))
        check("搜 B 库独有词条能命中", any(h[1] == "陶三" for h in hits_b),
              [(h[0], h[1]) for h in hits_b])
        hits_a = kb_index.search("商九", index=idx, top_k=5, vault_path=str(va))
        check("搜 A 库独有词条能命中", any(h[1] == "商九" for h in hits_a),
              [(h[0], h[1]) for h in hits_a])
        check("同名条目被去重并留痕",
              any(c.get("name") == "岑野" for c in idx.get("skipped_same_name") or []),
              idx.get("skipped_same_name"))

    print("\n【5】书签文件跟项目根走（--root 场景不能读到 CWD 的仓库配置）")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _setup(tmp)
        from utils import config_io
        old_root, old_cwd = getattr(config_io, "ROOT", None), os.getcwd()
        config_io.ROOT = tmp               # 只改 ROOT，**不 chdir**
        try:
            from utils import setting_schema
            setting_schema.reset_obsidian_config_cache()
            OB._WARNED.clear()
            roots = OB.get_vault_roots()
        finally:
            config_io.ROOT = old_root
            setting_schema.reset_obsidian_config_cache()
        check("CWD 是仓库、ROOT 是临时项目时，仍读到临时项目的书签",
              len(roots) == 2, [(r["name"], r["path"]) for r in roots])

    print("\n【6】界面与接口：开关/模型切换/冲突体检都在，且说法不再是“只有一个生效”")
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    api_dom = (ROOT / "scripts" / "nf_api_domains" / "project.py").read_text(encoding="utf-8")
    check("界面有“检查同名冲突”按钮", "检查同名冲突" in app)
    check("界面有启用/停用开关", "toggleVault" in app and "停用" in app)
    check("界面能切模型", "setVaultModel" in app and "多库同时读" in app)
    check("界面不再写“同一时刻只有一个生效”",
          "同一时刻只有一个生效" not in app, "（单库分支的说明是另一句话）")
    check("接口有 /config/vaults/enable", "/config/vaults/enable" in api_dom)
    check("接口有 /config/vaults/model", "/config/vaults/model" in api_dom)
    check("接口有 /config/vaults/scan", "/config/vaults/scan" in api_dom)
    check("后端不再把 model 写死成 single", '`model: \\"single\\"' not in api_dom
          and '"model": "single",      # 一次只有一个库生效' not in api_dom)

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
