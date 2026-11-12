# -*- coding: utf-8 -*-
"""vault_links 索引：设定集形状不规范时不许整段静默失败（问题总账第 10 条）。

## 要治的病

2026-11-11 实测：self 下阶段 1 收尾时 `build_vault_links` 抛
`AttributeError: 'list' object has no attribute 'get'`，而调用点只打印一句
"不影响设定集" —— 于是 `materials/vault_links.md` 静默为空，作者看不出少了一条链。
根因：`world` 只按 dict 读（`world.get("locations")`），而模型有时写成列表。

## 判据（每条都会失败）

A. `world` 是 dict → 老行为不变（分类名 world.locations 等）
B. `world` 是 **list** → 不抛异常，条目照样进索引（分类 world 或 world.<type>）
C. timeline 用 `what` 键（设定集口径）也要收（此前只认 `event`）
D. 设定集整个不是 dict → 返回空清单，不抛
E. 端到端：`build_vault_links` 在 world=list 的设定上能写出索引文件，条目不丢
F. 反证：源码里必须留着"world 是 list"这条分支（删掉即红）

零 LLM、零费用，全部在临时目录里造。
用法：python tests/unit/test_vault_links.py
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
                           ("  → " + str(detail)[:220]) if detail else ""))


def main():
    import build_vault_links as bv

    print("【A】world 是 dict：老行为不变")
    setting = {
        "characters": [{"name": "阿禾", "source": "素材甲.md"}],
        "world": {"locations": [{"name": "青石镇"}],
                  "factions": [{"name": "药行"}],
                  "items": [{"name": "铜钥匙"}]},
        "plot_fragments": [{"id": 1, "summary": "暗格里的钥匙"}],
        "timeline": [{"when": "第一年春", "what": "父亲失踪"}],
    }
    got = bv.collect_entries(setting)
    cats = sorted(c for _s, c, _n in got)
    check("三个分类都收到", cats == ["characters", "plot_fragments", "timeline",
                                    "world.factions", "world.items", "world.locations"],
          cats)
    check("timeline 用 what 也收（此前只认 event）",
          any(c == "timeline" and "父亲失踪" in n for _s, c, n in got), got)

    print("\n【B】world 是 list（模型常见写法）→ 不许抛，条目照样收")
    setting_list = dict(setting)
    setting_list["world"] = [{"name": "青石镇", "type": "locations"},
                             {"name": "药行", "category": "factions"},
                             {"name": "无名渡口"}]
    try:
        got2 = bv.collect_entries(setting_list)
        err = None
    except Exception as e:                                    # noqa: BLE001
        got2, err = [], "%s: %s" % (type(e).__name__, e)
    check("不抛异常", err is None, err)
    names = sorted(n for _s, _c, n in got2 if _c.startswith("world"))
    check("三条世界观条目都收进来", names == ["无名渡口", "药行", "青石镇"], names)
    check("type / category 用来分类，缺了记 world",
          ("world.locations" in [c for _s, c, _n in got2]
           and "world.factions" in [c for _s, c, _n in got2]
           and "world" in [c for _s, c, _n in got2]),
          sorted({c for _s, c, _n in got2}))

    print("\n【C】设定集整个不是 dict → 空清单，不抛")
    for weird in ([{"name": "甲"}], "字符串", None):
        try:
            out = bv.collect_entries(weird)
            ok = out == []
        except Exception as e:                                # noqa: BLE001
            ok, out = False, "%s: %s" % (type(e).__name__, e)
        check("形状 %s → 空清单且不抛" % type(weird).__name__, ok, out)

    print("\n【D】端到端：world=list 的设定集能写出索引文件")
    tmp = Path(tempfile.mkdtemp(prefix="nf_vaultlinks_"))
    sp = tmp / "setting.json"
    sp.write_text(json.dumps(setting_list, ensure_ascii=False), encoding="utf-8")
    out = tmp / "vault_links.md"
    try:
        total, srcs = bv.build_vault_links(sp, out)
        err = None
    except Exception as e:                                    # noqa: BLE001
        total, srcs, err = 0, 0, "%s: %s" % (type(e).__name__, e)
    check("不抛异常", err is None, err)
    check("索引文件已写出", out.exists())
    body = out.read_text(encoding="utf-8") if out.exists() else ""
    check("条目数 > 0（不再静默空）", total > 0, total)
    check("世界观条目名出现在索引里",
          all(n in body for n in ("青石镇", "药行", "无名渡口")), body[:160])

    print("\n【F】反证：world 是 list 的分支还在源码里")
    src = (ROOT / "scripts" / "build_vault_links.py").read_text(encoding="utf-8")
    check("源码保留 isinstance(world, list) 分支",
          "isinstance(world, list)" in src)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
