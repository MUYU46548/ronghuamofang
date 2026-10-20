# -*- coding: utf-8 -*-
"""设定库闭环的自检（2026-10-20，台账三·「设定库闭环」）。

覆盖三件事：**增量合并**（不再是整体替换）、**大纲注入**（设定集摘要内联）、
**冲突三态**（后端判据，GUI 只是它的显示层）。

## 主判据

**增量合并（merge_setting_from_vault）**
1. `plot_fragments` / `timeline` **保留**（这是与 `scan` 整体替换的分水岭）
2. 新词条追加，带 `source`/`path`
3. 同名条目**空字段**被补全
4. 同名条目**已有非空值不被覆盖**，差异记进 vault_conflicts.json
5. `locked: true` 的条目**完全不碰**（连空字段也不补）
6. 合并前备份到 history/
7. `base_name` 归一：`苏芷（档案室）` 与 `苏芷` 视为同一人（不重复追加）

**大纲注入（setting_brief.build）**
8. 角色 / 地点势力 / 伏笔 / 时间线四段都在
9. 预算硬裁，且**写明裁掉多少行**（不静默截断）
10. 设定集缺失 / 坏 JSON → 返回 error，**不抛异常**
11. 同名不重复出现（角色与 vault 补充去重）

用法：python tests/unit/test_setting_closure.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import obsidian_integrate as OI                                     # noqa: E402
from utils import setting_brief as SB                               # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  → %s" % (detail,)))


SETTING = {
    "characters": [
        {"id": "c001", "name": "苏芷", "role": "档案室整理员",
         "traits": ["记事极细"], "source": "materials/raw/a.md"},
        {"id": "c002", "name": "周砚清", "role": "", "traits": [],
         "source": "materials/raw/b.md"},
        {"id": "c003", "name": "老赵", "role": "巡夜人", "locked": True,
         "source": "materials/raw/c.md"},
    ],
    "world": {"locations": [{"name": "河口三区", "snippet": "旧库房所在"}]},
    "plot_fragments": ["怀表的来历没有交代", "北门的商队每年秋天来一次"],
    "timeline": ["某年冬，封锁开始"],
    "meta": {"source": "stage1"},
}

VAULT_CHARS = [
    {"name": "苏芷", "role": "档案室的整理员", "traits": ["冷静"], "path": "人物/苏芷.md",
     "source": "vault"},
    {"name": "苏芷（档案室）", "role": "整理员", "path": "人物/苏芷（档案室）.md", "source": "vault"},
    {"name": "周砚清", "role": "巡夜人", "path": "人物/周砚清.md", "source": "vault"},
    {"name": "老赵", "role": "巡夜人（旧）", "traits": ["多疑"], "path": "人物/老赵.md",
     "source": "vault"},
    {"name": "新角色·阿岚", "role": "码头记账的", "path": "人物/阿岚.md", "source": "vault"},
]
VAULT_WORLD = [
    {"name": "旧码头", "type": "地点", "snippet": "涨潮时淹掉半截", "path": "地点/旧码头.md"},
    {"name": "盐帮", "type": "势力", "snippet": "控制北门货流", "path": "势力/盐帮.md"},
]


def sandbox():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data" / "setting").mkdir(parents=True)
    p = tmp / "data" / "setting" / "setting.json"
    p.write_text(json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp, p


def with_stub(fn):
    """把 vault 扫描换成固定返回（vault 扫描本身有既有测试，这里只测合并逻辑）。"""
    old_c, old_w = OI.scan_vault_characters, OI.scan_vault_worldbuilding
    OI.scan_vault_characters = lambda vp=None: [dict(x) for x in VAULT_CHARS]
    OI.scan_vault_worldbuilding = lambda vp=None: [dict(x) for x in VAULT_WORLD]
    try:
        return fn()
    finally:
        OI.scan_vault_characters, OI.scan_vault_worldbuilding = old_c, old_w


# ====================================================================== P1
def case_merge():
    print("\n[P1] 增量合并：保留既有内容，只做加法")
    tmp, sp = sandbox()
    try:
        def run():
            import os
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                return OI.merge_setting_from_vault(None, str(sp),
                                                   "data/setting/vault_conflicts.json")
            finally:
                os.chdir(cwd)
        st = with_stub(run)
        data = json.loads(sp.read_text(encoding="utf-8"))

        check("P1 剧情碎片**保留**（不再是整体替换）",
              data["plot_fragments"] == SETTING["plot_fragments"], data["plot_fragments"])
        check("P1 时间线**保留**", data["timeline"] == SETTING["timeline"], data["timeline"])
        check("P1 stats 统计保留条数", st["kept_fragments"] == 3, st["kept_fragments"])

        names = [c["name"] for c in data["characters"]]
        check("P1 新角色被追加", "新角色·阿岚" in names, names)
        check("P1 base_name 归一：不重复追加「苏芷（档案室）」",
              names.count("苏芷") == 1 and "苏芷（档案室）" not in names, names)
        check("P1 地点被追加", any(w["name"] == "旧码头" for w in data["world"]["locations"]),
              data["world"]["locations"])
        check("P1 势力被追加", any(w["name"] == "盐帮" for w in data["world"]["factions"]),
              data["world"].get("factions"))
        check("P1 原有地点仍在", any(w["name"] == "河口三区" for w in data["world"]["locations"]),
              data["world"]["locations"])

        lu = next(c for c in data["characters"] if c["name"] == "苏芷")
        check("P1 已有非空字段**不被覆盖**（role 仍是素材归并那版）",
              lu["role"] == "档案室整理员", lu["role"])
        check("P1 已有非空 traits 不被覆盖", lu["traits"] == ["记事极细"], lu["traits"])

        fm = next(c for c in data["characters"] if c["name"] == "周砚清")
        check("P1 空字段被补全（role 从 vault 补齐）", fm["role"] == "巡夜人", fm["role"])

        zhou = next(c for c in data["characters"] if c["name"] == "老赵")
        check("P1 locked 条目完全不碰（role 未变）", zhou["role"] == "巡夜人", zhou["role"])
        check("P1 locked 条目的空字段也不补（traits 仍空）",
              zhou.get("traits") in ([], None), zhou.get("traits"))
        check("P1 locked 跳过被统计", "老赵" in st["skipped_locked"], st["skipped_locked"])

        check("P1 分歧被记录（苏芷 role 两个说法）",
              any(c["name"] == "苏芷" and c["field"] == "role" for c in st["conflicts"]),
              st["conflicts"])
        cp = tmp / "data" / "setting" / "vault_conflicts.json"
        check("P1 vault_conflicts.json 落盘", cp.exists())
        if cp.exists():
            saved = json.loads(cp.read_text(encoding="utf-8"))
            check("P1 冲突带双方出处",
                  all(v.get("file") for c in saved["conflicts"] for v in c["values"]),
                  saved["conflicts"][:1])

        hist = sorted((tmp / "data" / "setting" / "history").glob("setting_v*.json"))
        check("P1 合并前有备份", bool(hist), hist)
        check("P1 meta 记录了合并模式", data["meta"].get("last_merge_mode") == "merge",
              data["meta"])
        check("P1 meta 保留原有 source 字段", data["meta"].get("source") == "stage1",
              data["meta"])

        # 幂等：再合并一次不该重复追加
        def run_again():
            import os
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                return OI.merge_setting_from_vault(None, str(sp),
                                                   "data/setting/vault_conflicts.json")
            finally:
                os.chdir(cwd)
        st2 = with_stub(run_again)
        data2 = json.loads(sp.read_text(encoding="utf-8"))
        check("P1 二次合并幂等（角色数不变）",
              len(data2["characters"]) == len(data["characters"]),
              (len(data["characters"]), len(data2["characters"])))
        check("P1 二次合并不再新增", not st2["added"], st2["added"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_merge_broken():
    print("\n[P2] 设定集坏了 → 明确报错，不写坏文件")
    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / "bad.json"
        p.write_text("{ 不是 JSON", encoding="utf-8")
        import os
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            try:
                OI.merge_setting_from_vault(None, str(p), "conf.json")
                raised = ""
            except ValueError as e:
                raised = str(e)
        finally:
            os.chdir(cwd)
        check("P2 坏 JSON 抛 ValueError（带原因）", bool(raised) and "解析失败" in raised, raised)
        check("P2 原文件没被改写", p.read_text(encoding="utf-8") == "{ 不是 JSON")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================== P3
def case_brief():
    print("\n[P3] 大纲阶段注入：设定集摘要")
    tmp, sp = sandbox()
    try:
        r = SB.build(str(sp))
        b = r["block"]
        check("P3 无错误", not r["error"], r["error"])
        check("P3 角色段在", "### 角色" in b and "苏芷" in b, b[:200])
        check("P3 性格被内联", "记事极细" in b, b[:400])
        check("P3 地点势力段在", "### 地点 / 势力" in b and "河口三区" in b, b[:400])
        check("P3 伏笔段在（大纲必须看到）", "### 伏笔" in b and "怀表的来历" in b, b[:600])
        check("P3 时间线段在", "### 时间线" in b and "封锁开始" in b, b[:600])
        check("P3 locked 有标记", "【locked】" in b, b[:300])
        check("P3 明确叫模型别再去逐条读 setting.json", "不要" in b and "setting.json" in b)

        # 预算硬裁
        r2 = SB.build(str(sp), budget=120)
        check("P3 超预算被裁", r2["stats"]["trimmed"] > 0, r2["stats"])
        check("P3 裁掉多少行**写出来**（不静默）", "未内联" in r2["block"], r2["block"][-200:])
        check("P3 裁后仍在预算内（含提示行）",
              len(r2["block"]) <= 120 + 120, len(r2["block"]))

        # 缺失 / 坏 JSON
        r3 = SB.build(str(tmp / "nope.json"))
        check("P3 设定集缺失 → error 且不抛", bool(r3["error"]) and r3["block"] == "", r3)
        bad = tmp / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        r4 = SB.build(str(bad))
        check("P3 坏 JSON → error 且不抛", bool(r4["error"]), r4)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_brief_dedup():
    print("\n[P4] vault 补充词条去重（已在设定集里的不再列一遍）")
    tmp, sp = sandbox()
    try:
        old = None
        try:
            import obsidian_bridge
            old = obsidian_bridge.scan_vault
            obsidian_bridge.scan_vault = lambda vp=None: {
                "characters": [{"name": "苏芷", "snippet": "已在设定集里"},
                               {"name": "阿岚", "snippet": "码头记账的年轻人"}],
                "world": [{"name": "旧码头", "snippet": "涨潮时淹掉半截", "type": "地点"}],
            }
            r = SB.build(str(sp), vault_path=str(tmp))
        finally:
            if old is not None:
                obsidian_bridge.scan_vault = old
        b = r["block"]
        check("P4 vault 补充段出现", "尚未归并" in b, b[-300:])
        check("P4 已在设定集的名字不再列（苏芷）",
              b.count("苏芷") == 1, [l for l in b.splitlines() if "苏芷" in l])
        check("P4 新词条被列出（阿岚）", "阿岚" in b, b[-300:])
        check("P4 统计 vault 补充条数", r["stats"]["vault_extra"] >= 1, r["stats"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("  设定库闭环自检（docs/收口台账-20261019.md 三·设定库闭环）")
    print("=" * 62)
    case_merge()
    case_merge_broken()
    case_brief()
    case_brief_dedup()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
