# -*- coding: utf-8 -*-
"""设定条目来源链路（F21，2026-11-03）。

## 判据（每条都能变红）

- P1 最早出现是哪一版（first_seen）
- P2 每个字段“从哪一版起是现在这个值”（field_since）
- P3 逐版变化清单（changed_in，带字段名）
- P4 导入文件（source）能指认；没有 source 就明说
- P5 没有历史版本 → traceable=false + 原因（不冒充“第 1 版就有”）
- P6 条目不存在 → found=false，不返回半截结论
- P7 角色找不到时能回落到世界条目
- P8 端点是精确匹配（/setting/provenance 与 /setting/names 已接线）

用法：python tests/unit/test_setting_provenance.py
"""
import io
import json
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
                           ("  → " + str(detail)[:240]) if detail else ""))


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


V1 = {"characters": [
    {"name": "甲", "role": "铁匠", "traits": ["寡言"], "source": "materials/raw/甲.md"},
    {"name": "乙", "role": "掌柜", "traits": ["贪"]},
], "world": [{"name": "旧城", "snippet": "砖墙"}]}

V2 = {"characters": [
    {"name": "甲", "role": "铁匠", "traits": ["寡言", "手稳"], "source": "materials/raw/甲.md"},
    {"name": "乙", "role": "掌柜", "traits": ["贪"]},
    {"name": "丙", "role": "船工", "traits": []},
], "world": [{"name": "旧城", "snippet": "砖墙与吊桥"}]}

LIVE = {"characters": [
    {"name": "甲", "role": "铸剑师", "traits": ["寡言", "手稳"],
     "source": "materials/raw/甲.md"},
    {"name": "乙", "role": "掌柜", "traits": ["贪"]},
], "world": [{"name": "旧城", "snippet": "砖墙与吊桥"}]}


def main():
    print("=" * 66)
    print("设定条目来源链路（F21）")
    print("=" * 66)

    from utils import setting_provenance as sp

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "data" / "setting" / "history" / "setting_v1.json", V1)
        _write(root / "data" / "setting" / "history" / "setting_v2.json", V2)
        _write(root / "data" / "setting" / "setting.json", LIVE)

        print("\n[P1] 最早出现")
        t = sp.trace("甲", root=str(root))
        check("找到条目", t["found"] is True, t.get("why"))
        check("first_seen = v1", t.get("first_seen", {}).get("label") == "v1",
              t.get("first_seen"))
        check("出现过的版本有 3 个（v1/v2/当前）", len(t["versions"]) == 3,
              t["versions"])

        print("\n[P2] 字段从哪版起")
        since = {x["field"]: x for x in t["field_since"]}
        check("性格从 v2 起（v1 只有寡言）", since["性格"]["since"] == "v2",
              since["性格"])
        check("角色定位从“”起（v1/v2 都是铁匠）",
              since["角色定位"]["since"] == "当前", since["角色定位"])
        check("字段带上当前值", since["角色定位"]["value"] == "铸剑师",
              since["角色定位"]["value"])

        print("\n[P3] 逐版变化")
        labels = [c["label"] for c in t["changed_in"]]
        check("v2 有变化记录", "v2" in labels, labels)
        v2c = [c for c in t["changed_in"] if c["label"] == "v2"][0]
        check("v2 变的是性格", "性格" in v2c["fields"], v2c["fields"])
        cur = [c for c in t["changed_in"] if c["label"] == "当前"][0]
        check("当前版变的是角色定位", "角色定位" in cur["fields"], cur["fields"])

        print("\n[P4] 导入来源")
        check("能找到 source", t["imports"] and t["imports"][0]["source"].endswith("甲.md"),
              t["imports"])
        t2 = sp.trace("乙", root=str(root))
        check("没有 source 时明说",
              not t2["imports"] and "source" in t2.get("imports_note", ""),
              t2.get("imports_note"))

        print("\n[P5] 没有历史版本 → 如实说")
        with tempfile.TemporaryDirectory() as tmp2:
            r2 = Path(tmp2)
            _write(r2 / "data" / "setting" / "setting.json", LIVE)
            t3 = sp.trace("甲", root=str(r2))
            check("traceable=false", t3["traceable"] is False)
            check("说明为什么", "历史版本" in t3.get("why", ""), t3.get("why"))
            check("仍给出当前值与导入来源", t3["current"]["fields"]["角色定位"] == "铸剑师")

        print("\n[P6] 条目不存在")
        t4 = sp.trace("查无此人", root=str(root))
        check("found=false", t4["found"] is False)
        check("不返回半截 field_since", t4["field_since"] == [])

        print("\n[P7] 回落到世界条目")
        t5 = sp.trace("旧城", root=str(root), kind="characters")
        check("能找到世界条目", t5["found"] is True and t5["kind"] == "world",
              (t5["found"], t5["kind"]))
        check("世界条目的描述有版本变化（v2 改过简介）",
              any(x["label"] == "v2" and "简介" in x["fields"]
                  for x in t5["changed_in"]), t5["changed_in"])

        print("\n[P8] 空名字不编答案")
        check("空 name → ok=false", sp.trace("")["ok"] is False)

    print("\n[P9] 端点接线")
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("GET /setting/provenance 有转发", 'elif p == "/setting/provenance":' in api_src)
    check("GET /setting/names 有转发", 'elif p == "/setting/names":' in api_src)
    dom = (ROOT / "scripts" / "nf_api_domains" / "setting_state.py").read_text(encoding="utf-8")
    check("域模块声明 ROUTES", '"/setting/provenance"' in dom and '"/setting/names"' in dom)

    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
