# -*- coding: utf-8 -*-
"""vault 词条进设定集：全文注入，不再只留前 200~300 字。

## 为什么有这条

用户 2026-11-11 的原话：vault 词条进设定集时“只保留正文前 300 字纯文本、换行压成空格，
这样等于几十万字资料毫无价值，有效信息 0 个字”。查下来链条上有**四处**截断：

1. 扫描（`obsidian_bridge`）：角色无 desc 时 `plain[:200]`、世界/时间线 `plain[:300]`，
   并且 `.replace('\n', ' ')` 把列表与分行压成一段；
2. 拼卡（`setting_schema.build_character_card`）：`snippet[:200].replace('\n',' ')`；
3. 世界条目注入（`stage4_writing._render`）：`desc_cap=300`；
4. 总预算：`budget=3000` / `4500`，超了**静默 return**（整块丢，没人知道）。

现在：条目带 `body`（原文，保留换行），注入以 `body` 为准；超预算会打印，不再静默。

## 判据（每条都会失败）

A. 扫描保留 `body` 全文（含换行/列表），`snippet` 仍只是索引摘要
B. 角色卡注入原文，不再是 200 字
C. 单条超过 `FULL_TEXT_CAP` → 截断并**写明还有多少字没进**（不静默）
D. 世界条目注入原文（`_render` 不再卡 300）
E. 总预算超了会出声（不静默丢块）

全程离线、零 token。
用法：python tests/unit/test_full_text_injection.py
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
LONG = "细节" * 400          # 800 字，远超旧的 200/300 截断


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def main():
    import obsidian_bridge as ob
    from utils.setting_schema import FULL_TEXT_CAP, build_character_card, normalize_character

    print("【A】扫描保留全文（body），snippet 仍只是索引")
    tmp = Path(tempfile.mkdtemp(prefix="nf_fulltext_"))
    vault = tmp / "vault"
    d = vault / "设定" / "人物"
    d.mkdir(parents=True, exist_ok=True)
    body = "阿禾是药铺学徒。\n\n他怕水。\n\n- 左手有疤\n- 怕黑\n\n" + LONG
    (d / "阿禾.md").write_text("---\nname: 阿禾\n---\n\n" + body, encoding="utf-8")
    chars = ob.scan_vault(vault, dirs={"characters": "设定/人物"})["characters"]
    check("扫到 1 个角色", len(chars) == 1, len(chars))
    c = chars[0] if chars else {}
    check("条目带 body", bool(c.get("body")), list(c.keys())[:8])
    check("body 是全文（含第 400 字之后的内容）", LONG[:100] in (c.get("body") or ""), len(c.get("body") or ""))
    check("body 保留换行（列表没被压平）", "\n" in (c.get("body") or ""))
    check("body 里还看得到列表项", "左手有疤" in (c.get("body") or ""), (c.get("body") or "")[:40])
    check("snippet 仍只是摘要（<=300 字）", len(c.get("snippet") or "") <= 300, len(c.get("snippet") or ""))

    print("\n【B】角色卡注入原文，不是 200 字")
    card = build_character_card(normalize_character(c))
    check("卡里出现全文内容", LONG[:60] in card, card[:80])
    check("卡里标了字数", "原文（" in card, card[:80])
    check("卡不再把换行压成空格", "\n" in card)

    print("\n【C】超上限 → 截断并说明还有多少字没进")
    huge = dict(c)
    huge["body"] = "头" * (FULL_TEXT_CAP + 500)
    card2 = build_character_card(normalize_character(huge))
    check("写明共多少字", ("共 %d 字" % (FULL_TEXT_CAP + 500)) in card2, card2[:120])
    check("写明余下多少字没进", "余 500 字没进" in card2, card2[:160])
    check("确实截断了（不再增长）", len(card2) < FULL_TEXT_CAP + 2000, len(card2))

    print("\n【D】世界条目注入原文（_render 不再卡 300）")
    import stage4_writing as s4
    setting = {
        "characters": [],
        "world": {"locations": [{"name": "旧钟楼", "body": "钟停在三点十七分。\n\n" + LONG}],
                  "factions": [], "concepts": []},
        "plot_fragments": [], "timeline": [],
    }
    sp = tmp / "setting.json"
    sp.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")
    ctx = s4._extract_world_context(str(sp), "本章在旧钟楼前发生。", involved=[])
    check("注入里出现旧钟楼", "旧钟楼" in ctx, ctx[:60])
    check("注入的是原文（第 400 字之后也在）", LONG[:60] in ctx, len(ctx))
    check("注入保留了换行", "三点十七分。\n" in ctx)

    print("\n【E】总预算超了会出声（不静默丢块）")
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        s4._extract_world_context(str(sp), "本章在旧钟楼前发生。", involved=[], budget=10)
    check("打印了超预算告警", "超预算" in buf.getvalue(), buf.getvalue()[:120])

    print("\n【F】抽双向链接 + 一跳扩散（“举一反三”的物理前提）")
    check("抽 [[目标]]", ob.extract_wikilinks("见 [[回春堂]]。") == ["回春堂"],
          ob.extract_wikilinks("见 [[回春堂]]。"))
    check("抽 [[目标|别名]] 取目标名",
          ob.extract_wikilinks("[[阿禾的师父|师父]]") == ["阿禾的师父"],
          ob.extract_wikilinks("[[阿禾的师父|师父]]"))
    check("抽 [[目标#小节]] 取目标名", ob.extract_wikilinks("[[药铺#账房]]") == ["药铺"],
          ob.extract_wikilinks("[[药铺#账房]]"))
    check("去重且保序", ob.extract_wikilinks("[[A]] [[B]] [[A]]") == ["A", "B"],
          ob.extract_wikilinks("[[A]] [[B]] [[A]]"))

    (d / "阿禾.md").write_text(
        "---\nname: 阿禾\n---\n\n阿禾是学徒，师从 [[阿禾的师父]]，常在 [[回春堂]] 抓药。\n",
        encoding="utf-8")
    chars2 = ob.scan_vault(vault, dirs={"characters": "设定/人物"})["characters"]
    ah = [c for c in chars2 if c.get("name") == "阿禾"]
    check("扫描出的条目带 links",
          bool(ah) and "阿禾的师父" in (ah[0].get("links") or []) and "回春堂" in (ah[0].get("links") or []),
          ah[0].get("links") if ah else None)

    sp2 = tmp / "setting_rel.json"
    sp2.write_text(json.dumps({
        "characters": [{"name": "阿禾", "links": ["回春堂"], "snippet": "药铺学徒"}],
        "world": {"locations": [{"name": "回春堂", "body": "城中药铺，柜台有一道裂缝。"}],
                  "factions": [], "concepts": []},
        "plot_fragments": [], "timeline": [],
    }, ensure_ascii=False), encoding="utf-8")
    ctx2 = s4._extract_world_context(str(sp2), "阿禾在夜里走过渡口。", involved=[])
    check("大纲没点名的关联条目被带进来", "回春堂" in ctx2, ctx2[:120])
    check("关联项有明确标注（不冒充本章出场）", "关联项" in ctx2, ctx2[:160])

    sp3 = tmp / "setting_2hop.json"
    sp3.write_text(json.dumps({
        "characters": [{"name": "甲", "links": ["乙"], "snippet": "起点"}],
        "world": {"locations": [{"name": "乙", "links": ["丙"], "body": "中间"},
                                {"name": "丙", "body": "两跳之外"}],
                  "factions": [], "concepts": []},
        "plot_fragments": [], "timeline": [],
    }, ensure_ascii=False), encoding="utf-8")
    ctx3 = s4._extract_world_context(str(sp3), "甲出现。", involved=[])
    check("只跳一跳：两跳之外的丙不进", "丙" not in ctx3, ctx3[:160])

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
