# -*- coding: utf-8 -*-
"""酒馆资料导入（竞品三路之二，2026-11-07）。

## 为什么有这条用例

催更姬的“酒馆资料导入”是把已有角色卡/世界书接进来的能力。这里按**公开格式**
自己实现解析（不抄 GPL 代码），所以要钉住四件事：

1. 角色卡 `.json`（v2 `data` / v1 平铺）与 `.png`（`tEXt` 块里的 `chara` base64）都认；
2. 世界书 `{entries:{...}}` 能拆成条目，`disable: true` 的不导入但**点数**；
3. 只写 `materials/raw`，**同名不覆盖**（素材是作者的数据），要覆盖必须显式；
4. 界面上真有入口（不是只写了个函数）。

用法：python tests/unit/test_tavern_import.py
"""
import base64
import io
import json
import struct
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import tavern_import as TI          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:240]) if detail else ""))


def make_png(text_chunks):
    """造一个只含 tEXt 块的 PNG（解析器只看块结构，不验 CRC/像素）。"""
    out = bytearray(TI.PNG_SIG)
    for k, v in text_chunks.items():
        body = k.encode("latin-1") + b"\x00" + v.encode("latin-1")
        out += struct.pack(">I", len(body)) + b"tEXt" + body + b"\x00" * 4
    out += struct.pack(">I", 0) + b"IEND" + b"\x00" * 4
    return bytes(out)


CARD = {
    "spec": "chara_card_v2",
    "data": {
        "name": "岑野",
        "description": "一个在旧码头修船的年轻人，左手有旧伤。",
        "personality": "话少，认死理。",
        "scenario": "雨季的港口小镇。",
        "first_mes": "“船今晚不走。”",
        "mes_example": "<START>\n{{char}}: 别碰那根绳。",
        "creator_notes": "虚构测试角色。",
        "tags": ["测试", "港口"],
        "character_book": {"entries": [
            {"name": "旧码头", "keys": ["码头", "船坞"], "content": "旧码头在镇子东头，退潮时露出石阶。", "enabled": True},
            {"name": "停用条目", "keys": ["x"], "content": "这条应当被跳过。", "enabled": False},
        ]},
    },
}

WORLDBOOK = {
    "entries": {
        "0": {"comment": "潮汐", "key": ["潮", "涨潮"], "content": "这里一天涨两次潮，早潮在寅时。"},
        "1": {"comment": "旧规", "key": ["规矩"], "content": "夜里不许点灯。", "disable": False},
        "2": {"comment": "废弃", "key": ["废"], "content": "这条停用了。", "disable": True},
    }
}


def case_json_card():
    print("\n【1】角色卡 .json：v2 data 形状、内嵌世界书、作者备注都读进来")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "card.json"
        p.write_text(json.dumps(CARD, ensure_ascii=False), encoding="utf-8")
        card = TI.parse_card(p)
        check("名字", card["name"] == "岑野", card["name"])
        check("设定/性格/开场读出", "旧码头" in card["description"] and card["first_mes"].startswith("“船"), card)
        check("内嵌世界书只留启用的那条", len(card["lorebook"]) == 1 and card["lorebook"][0]["name"] == "旧码头", card["lorebook"])
        md = TI.card_markdown(card)
        check("素材卡带 frontmatter", md.startswith("---\nname: 岑野") and "source: tavern-card" in md, md[:80])
        check("正文分小节", "## 设定" in md and "## 内嵌世界书" in md, md[:200])


def case_png_card():
    print("\n【2】角色卡 .png：tEXt 里的 chara(base64) 能解开；没有卡数据的图要说清楚")
    with tempfile.TemporaryDirectory() as td:
        payload = base64.b64encode(json.dumps(CARD, ensure_ascii=False).encode("utf-8")).decode("latin-1")
        p = Path(td) / "card.png"
        p.write_bytes(make_png({"chara": payload}))
        card = TI.parse_card(p)
        check("PNG 能读出角色", card["name"] == "岑野", card["name"])
        plain = Path(td) / "plain.png"
        plain.write_bytes(make_png({"Software": "not a card"}))
        err = ""
        try:
            TI.parse_card(plain)
        except ValueError as e:
            err = str(e)
        check("普通图片报“没有角色卡数据”", "没有角色卡数据" in err, err)


def case_worldbook():
    print("\n【3】世界书：条目拆开；停用的不导入但点数")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "我的世界书.json"
        p.write_text(json.dumps(WORLDBOOK, ensure_ascii=False), encoding="utf-8")
        wb = TI.parse_worldbook(p)
        check("启用条目 2 条", len(wb["entries"]) == 2, wb["entries"])
        check("停用条目被计数（1 条）", wb["disabled"] == 1, wb["disabled"])
        md = TI.worldbook_markdown(wb)
        check("世界书素材卡按条目分节", "## 潮汐" in md and "## 旧规" in md, md[:200])


def case_write_rules():
    print("\n【4】落盘纪律：dry_run 不写、同名不覆盖、显式 overwrite 才覆盖")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "card.json"
        src.write_text(json.dumps(CARD, ensure_ascii=False), encoding="utf-8")
        out = Path(td) / "materials" / "raw"
        dry = TI.import_file(src, out_dir=out, dry_run=True)
        check("dry_run 报告会写哪张卡", dry["ok"] and dry["written"], dry)
        check("dry_run 真的没落盘", not out.exists() or not list(out.glob("*.md")),
              list(out.glob("*.md")) if out.exists() else [])
        first = TI.import_file(src, out_dir=out)
        files = list(out.glob("*.md"))
        check("真导入写出一张素材卡", first["ok"] and len(files) == 1, first)
        body = files[0].read_text(encoding="utf-8")
        check("文件名带来源、正文是读得懂的素材", "酒馆角色卡" in files[0].name and "## 设定" in body,
              files[0].name)
        second = TI.import_file(src, out_dir=out)
        check("同名第二次导入：不覆盖、明确报告", not second["ok"] and second["skipped"], second)
        third = TI.import_file(src, out_dir=out, overwrite=True)
        check("显式 overwrite 才重写", third["ok"] and third["written"], third)


def case_sanitize():
    print("\n【5】名字里的路径分隔符不会写出目录（防穿越）")
    bad = dict(CARD)
    bad = json.loads(json.dumps(CARD))
    bad["data"]["name"] = "../外面/危险:名字"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "c.json"
        src.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        out = Path(td) / "raw"
        r = TI.import_file(src, out_dir=out)
        name = Path(r["written"][0]["file"]).name if r.get("written") else ""
        check("文件名里没有分隔符", "/" not in name and "\\" not in name and ".." not in name, name)
        check("落在目标目录内", Path(r["written"][0]["file"]).parent == out, r.get("written"))


def case_wiring():
    print("\n【6】入口真接上：界面按钮 + 接口路由 + 只写 materials/raw")
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    dom = (ROOT / "scripts" / "nf_api_domains" / "materials.py").read_text(encoding="utf-8")
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("素材页有“导入酒馆资料”按钮", "导入酒馆资料" in app and "importTavern" in app)
    check("按钮调的是 /materials/import_tavern", "/materials/import_tavern" in app)
    check("接口已登记到 ROUTES", '"/materials/import_tavern"' in dom)
    check("nf_api 有转发", "/materials/import_tavern" in api)
    check("后端写死只落 materials/raw", 'api.ROOT / "materials" / "raw"' in dom, None)


def main():
    case_json_card()
    case_png_card()
    case_worldbook()
    case_write_rules()
    case_sanitize()
    case_wiring()
    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("    -", f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
