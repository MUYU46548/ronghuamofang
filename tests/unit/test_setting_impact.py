# -*- coding: utf-8 -*-
"""设定改了 → 哪些已检查的章要复检（P1-5，2026-11-19）。

与 `test_setting_stale.py` 是一对反向判据：
前者管"章变了，条目待核"，本文件管"条目变了，章待复检"。两个方向都缺一不可。

| 编号 | 钉什么 |
|---|---|
| I1 | 没基线 → `checked=false` + 原因（不冒充"全都对得上"） |
| I2 | 记录后设定不动 → 0 章待复检 |
| I3 | 改一个条目 → 只有引用它的章待复检，理由写明哪个条目 |
| I4 | 没引用它的章不受影响（不搞"全书红"） |
| I5 | 长词优先：改“沈砚之”不牵连只提“沈砚”的那一章 |
| I6 | 条目被删 → 理由写“已从设定集移除” |
| I7 | 只改章正文（设定没动）→ 不误报（那是 setting_stale 的活） |
| I8 | 章没了 → missing，不算待复检 |
| I9 | 坏基线 JSON → checked=false，不炸 |
| I10 | 重跑 record 覆盖该章基线 → 复检完不再挂红 |
| I11 | 设定集读不到 → checked=false（不是"没问题"） |
| I12 | 接线：`GET /setting/impact` 路由登记 + MCP 只读工具在 |

用法：python tests/unit/test_setting_impact.py（零 LLM、零网络）
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import setting_impact as si                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)[:220]) if extra else ""))


def _mk(root, setting=None, checked=None):
    sp = root / "data" / "setting"
    sp.mkdir(parents=True, exist_ok=True)
    if setting is not None:
        (sp / "setting.json").write_text(
            json.dumps(setting, ensure_ascii=False, indent=1), encoding="utf-8")
    if checked:
        d = root / "data" / "chapters" / "checked"
        d.mkdir(parents=True, exist_ok=True)
        for n, text in checked.items():
            (d / ("%02d.md" % n)).write_text(text, encoding="utf-8")


def _setting(role="巡道工"):
    return {
        "characters": [{"name": "沈砚", "role": role},
                       {"name": "沈砚之", "role": "旁支"}],
        "world": {"locations": [{"name": "青石站", "description": "旧站台"}]},
        "plot_fragments": [],
        "timeline": [],
    }


CH = {1: "## 第1章\n\n沈砚走进青石站。\n",
      2: "## 第2章\n\n沈砚之来了，没进门。\n"}


def main():
    print("【I1】没有基线 → checked=false（“没查过”不是“没问题”）")
    r = Path(tempfile.mkdtemp(prefix="nf_impact_"))
    _mk(r, _setting(), CH)
    st = si.status(r)
    check("checked=false", st["checked"] is False, st)
    check("给出原因", "没有基线" in st["reason"], st["reason"])
    check("待复检清单为空但不算通过", st["stale"] == [], st["stale"])

    print("\n【I2】记录基线后设定不动 → 0 章待复检")
    rec = si.record(r)
    check("记录了 2 章", rec["recorded"] == 2, rec)
    st = si.status(r)
    check("checked=true", st["checked"] is True, st)
    check("没有待复检", st["stale"] == [], st["stale"])
    check("基线只有章 → 条目指纹，不存正文",
          "沈砚走进" not in si.baseline_path(r).read_text(encoding="utf-8"))

    print("\n【I3】改一个条目 → 只有引用它的章待复检")
    _mk(r, _setting(role="巡道工（已升为工头）"), CH)
    st = si.status(r)
    names = [x["chapter"] for x in st["stale"]]
    check("第 1 章待复检", names == [1], names)
    check("理由写明哪个条目", bool(st["stale"]) and "沈砚" in st["stale"][0]["reasons"][0],
          st["stale"][:1])

    print("\n【I4】没引用它的章不受影响（不搞全书红）")
    check("第 2 章不在待复检里", 2 not in names, names)

    print("\n【I5】长词优先：改“沈砚之”不牵连只提“沈砚”的章")
    r2 = Path(tempfile.mkdtemp(prefix="nf_impact_long_"))
    _mk(r2, _setting(), CH)
    si.record(r2)
    s2 = _setting()
    s2["characters"][1]["role"] = "旁支（改成主支）"
    _mk(r2, s2, CH)
    st = si.status(r2)
    check("只有第 2 章待复检", [x["chapter"] for x in st["stale"]] == [2],
          [x["chapter"] for x in st["stale"]])

    print("\n【I6】条目被删 → 理由写“已从设定集移除”")
    r3 = Path(tempfile.mkdtemp(prefix="nf_impact_del_"))
    _mk(r3, _setting(), CH)
    si.record(r3)
    s3 = _setting()
    s3["world"]["locations"] = []          # 删掉“青石站”
    _mk(r3, s3, CH)
    st = si.status(r3)
    hit = [x for x in st["stale"] if x["chapter"] == 1]
    check("第 1 章待复检", bool(hit), st["stale"])
    check("理由是移除", bool(hit) and any("移除" in x for x in hit[0]["reasons"]),
          hit and hit[0]["reasons"])

    print("\n【I7】只改章正文（设定没动）→ 不误报")
    r4 = Path(tempfile.mkdtemp(prefix="nf_impact_text_"))
    _mk(r4, _setting(), CH)
    si.record(r4)
    _mk(r4, _setting(), {1: "## 第1章\n\n沈砚走进青石站，把门关上。\n",
                         2: CH[2]})
    st = si.status(r4)
    check("正文变了但设定没动 → 0 待复检（那是 setting_stale 的活）",
          st["stale"] == [], st["stale"])

    print("\n【I8】章没了 → missing，不算待复检")
    r5 = Path(tempfile.mkdtemp(prefix="nf_impact_miss_"))
    _mk(r5, _setting(), CH)
    si.record(r5)
    (r5 / "data" / "chapters" / "checked" / "02.md").unlink()
    st = si.status(r5)
    check("missing 里有第 2 章", st["missing"] == [2], st["missing"])
    check("不把缺失章混进待复检", all(x["chapter"] != 2 for x in st["stale"]), st["stale"])

    print("\n【I9】坏基线不炸")
    r6 = Path(tempfile.mkdtemp(prefix="nf_impact_bad_"))
    _mk(r6, _setting(), CH)
    p = si.baseline_path(r6)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ 不是 JSON", encoding="utf-8")
    try:
        st = si.status(r6)
        err = None
    except Exception as e:                                    # noqa: BLE001
        st, err = None, "%s: %s" % (type(e).__name__, e)
    check("当“没查”处理，不抛", err is None and st and st["checked"] is False, (err, st))

    print("\n【I10】复检完重记基线 → 不再挂红")
    r7 = Path(tempfile.mkdtemp(prefix="nf_impact_re_"))
    _mk(r7, _setting(), CH)
    si.record(r7)
    _mk(r7, _setting(role="巡道工（已升为工头）"), CH)
    check("先红", si.status(r7)["stale"] != [])
    si.record(r7, chapters=[1])
    check("重记第 1 章后不再待复检", si.status(r7)["stale"] == [], si.status(r7)["stale"])
    check("第 2 章基线仍在", "2" in (si.status(r7) and
                                 json.loads(si.baseline_path(r7).read_text(encoding="utf-8"))
                                 ["chapters"]))

    print("\n【I11】设定集读不到 → checked=false")
    r8 = Path(tempfile.mkdtemp(prefix="nf_impact_noset_"))
    _mk(r8, _setting(), CH)
    si.record(r8)
    (r8 / "data" / "setting" / "setting.json").unlink()
    st = si.status(r8)
    check("checked=false", st["checked"] is False, st)
    check("原因指向设定集", "设定集" in st["reason"], st["reason"])

    print("\n【I12】接线：路由 + MCP 只读工具")
    api = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("GET /setting/impact 登记", "/setting/impact" in api)
    mcp = (REPO / "scripts" / "nf_mcp.py").read_text(encoding="utf-8")
    check("MCP 有 nf_get_setting_impact", "nf_get_setting_impact" in mcp)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
