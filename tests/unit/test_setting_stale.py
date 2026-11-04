# -*- coding: utf-8 -*-
"""设定引用过期（M10）回归：条目记 sources（章号 + 版本），改章即待核。

| 编号 | 钉什么 |
|---|---|
| S1 | 设定集缺失 → `checked=false` + 原因（空清单不冒充“没问题”） |
| S2 | link 首次登记章号+版本；**长词优先**（“沈砚之”不给“沈砚”记引用）；短名不登记但计入 unlocatable |
| S3 | 改章 → 引用它的条目 stale；没引用它的条目不受影响 |
| S4 | **link 只增不改**：再扫一次不会重复，也不会把待核洗白（否则机制等于摆设） |
| S5 | confirm 后不再待核，`confirmed_at` 落盘，写前备份 setting_vN.json |
| S6 | 章被删 → missing；confirm 拒绝并给出 skipped，drop 能去掉这条来源 |
| S7 | 换层也算变（raw → refined 同一内容）：layer 不同即待核 |
| S8 | 新出现的引用记 `new`，不冒充 stale（没登记过就谈不上“过期”） |
| S9 | `status()` 只读：不改变 setting.json 一个字节 |
| S10 | 接线：路由登记 + review 进 Agent 禁用名单、link 不进 |
| S11 | 参数校验：name 必填 / drop 必须指明章号 / 同名多条要 kind |
| S12 | world 子分组（world.locations）也能登记，kind 带点可定位 |

用法：python tests/unit/test_setting_stale.py（零 LLM、零网络）
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import setting_stale as ss                          # noqa: E402
from utils import agent_guard                                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)[:220]) if extra else ""))


def _mk(root, setting=None, chapters=None):
    """造一个最小项目：data/setting/setting.json + data/chapters/<layer>/NN.md"""
    sp = root / "data" / "setting"
    sp.mkdir(parents=True, exist_ok=True)
    if setting is not None:
        (sp / "setting.json").write_text(
            json.dumps(setting, ensure_ascii=False, indent=1), encoding="utf-8")
    for layer, files in (chapters or {}).items():
        d = root / "data" / "chapters" / layer
        d.mkdir(parents=True, exist_ok=True)
        for n, text in files.items():
            (d / ("%02d.md" % n)).write_text(text, encoding="utf-8")
    return root / "data" / "setting" / "setting.json"


def _base_setting():
    return {
        "characters": [
            {"name": "沈砚", "role": "巡道工"},
            {"name": "沈砚之", "role": "旁支"},
            {"name": "单", "role": "短名"},
        ],
        "world": {"locations": [{"name": "青石站", "description": "旧站台"}],
                  "items": [{"name": "铜牌", "description": "凭证"}]},
        "plot_fragments": [],
        "timeline": [],
    }


def _chapters():
    return {"raw": {
        1: "## 第1章\n\n沈砚走进青石站，摸出铜牌。\n",
        2: "## 第2章\n\n沈砚之来了，没提铜牌。\n",
    }}


# --------------------------------------------------------------------- S1

def case_missing_setting():
    print("\n[S1] 设定集缺失：checked=false，空清单不冒充没有问题")
    td = Path(tempfile.mkdtemp())
    try:
        rep = ss.status(td)
        check("S1 checked=false", rep.get("checked") is False, rep.get("checked"))
        check("S1 给出原因（不是空字符串）", bool(rep.get("reason")), rep.get("reason"))
        check("S1 items 为空但 counts 结构齐全",
              rep.get("items") == [] and "stale" in (rep.get("counts") or {}), rep.get("counts"))
        out = ss.link(td)
        check("S1 没有设定集时 link 明确失败（不静默空跑）",
              out.get("ok") is False and bool(out.get("error")), out)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S2

def case_first_link():
    print("\n[S2] 首次登记：章号 + 版本；长词优先；短名计入 unlocatable")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, _base_setting(), _chapters())
        before = ss.status(td)
        check("S2 登记前：4 条有引用 → state=new", before["counts"]["new"] == 4, before["counts"])
        check("S2 短名“单”不冒充定位成功", before["counts"]["unlocatable"] == 1, before["counts"])

        out = ss.link(td)
        check("S2 link 成功", out.get("ok") is True, out.get("error"))
        check("S2 登记 5 条引用（沈砚1 / 沈砚之2 / 青石站1 / 铜牌1+2）",
              out["added_pairs"] == 5, out["added_pairs"])

        data = json.loads((td / "data/setting/setting.json").read_text(encoding="utf-8"))
        by = {c["name"]: c for c in data["characters"]}
        check("S2 沈砚 只记第 1 章（“沈砚之”不被算成“沈砚”）",
              [s["chapter"] for s in by["沈砚"]["sources"]] == [1],
              by["沈砚"].get("sources"))
        check("S2 沈砚之 只记第 2 章",
              [s["chapter"] for s in by["沈砚之"]["sources"]] == [2],
              by["沈砚之"].get("sources"))
        src = by["沈砚"]["sources"][0]
        check("S2 来源带 version（正文指纹）与 layer",
              len(str(src.get("version") or "")) == ss.VERSION_CHARS
              and src.get("layer") == "raw", src)
        check("S2 世界子分组也登记（world.items 铜牌 2 章）",
              len(data["world"]["items"][0]["sources"]) == 2,
              data["world"]["items"][0].get("sources"))
        after = ss.status(td)
        check("S2 登记后不再有 new / stale",
              after["counts"]["new"] == 0 and after["counts"]["stale"] == 0, after["counts"])
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S3

def case_chapter_edit_marks_stale():
    print("\n[S3] 改章 → 引用它的条目待核，没引用它的不受影响")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, _base_setting(), _chapters())
        ss.link(td)
        p = td / "data/chapters/raw/01.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n他咳了一声。\n", encoding="utf-8")
        rep = ss.status(td)
        names = {it["name"] for it in rep["items"] if it["state"] == "stale"}
        check("S3 引用第 1 章的条目全部待核", names == {"沈砚", "青石站", "铜牌"}, names)
        check("S3 只引用第 2 章的“沈砚之”不受影响", "沈砚之" not in names, names)
        check("S3 计数：3 条待核", rep["counts"]["stale"] == 3, rep["counts"])
        item = [it for it in rep["items"] if it["name"] == "沈砚"][0]
        check("S3 来源行给出“改过”原因与当前版本",
              item["sources"][0]["state"] == "changed"
              and "改过" in item["sources"][0]["reason"]
              and item["sources"][0]["current_version"] != item["sources"][0]["recorded_version"],
              item["sources"][0])
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S4

def case_link_is_additive():
    print("\n[S4] link 只增不改：不重复、不洗白待核")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, _base_setting(), _chapters())
        ss.link(td)
        p = td / "data/chapters/raw/01.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n他咳了一声。\n", encoding="utf-8")
        stale_before = ss.status(td)["counts"]["stale"]

        out = ss.link(td)
        check("S4 第二次 link 没有新增（幂等）", out["added_pairs"] == 0, out["added_pairs"])
        rep = ss.status(td)
        check("S4 待核数没被 link 洗白", rep["counts"]["stale"] == stale_before,
              (stale_before, rep["counts"]["stale"]))
        data = json.loads((td / "data/setting/setting.json").read_text(encoding="utf-8"))
        by = {c["name"]: c for c in data["characters"]}
        check("S4 来源条数没有重复", len(by["沈砚"]["sources"]) == 1, by["沈砚"]["sources"])
        hist = list((td / "data/setting/history").glob("setting_v*.json"))
        check("S4 第二次 link 无需写盘 → 不产生多余备份", len(hist) == 1, [p.name for p in hist])
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S5

def case_confirm():
    print("\n[S5] confirm：不再待核 + confirmed_at 落盘 + 写前备份")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, _base_setting(), _chapters())
        ss.link(td)
        p = td / "data/chapters/raw/01.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n他咳了一声。\n", encoding="utf-8")
        code, rep = ss.review(td, name="沈砚", chapters=[1], action="confirm")
        check("S5 confirm 成功", code == 200 and rep.get("ok") is True, rep.get("error"))
        after = ss.status(td)
        names = {it["name"] for it in after["items"] if it["state"] == "stale"}
        check("S5 沈砚 不再待核", "沈砚" not in names, names)
        check("S5 同章的其它条目仍然待核（逐条核对，不连带）",
              {"青石站", "铜牌"} <= names, names)
        data = json.loads((td / "data/setting/setting.json").read_text(encoding="utf-8"))
        cur = ss.load_chapters(td)[1]["version"]
        src = [c for c in data["characters"] if c["name"] == "沈砚"][0]["sources"][0]
        check("S5 版本更新为当前正文指纹", src["version"] == cur, (src, cur))
        check("S5 confirmed_at 已落盘", bool(src.get("confirmed_at")), src)
        check("S5 写前备份存在", bool(rep.get("backup")) and Path(rep["backup"]).exists(),
              rep.get("backup"))
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S6

def case_missing_chapter():
    print("\n[S6] 章被删：missing；confirm 拒绝；drop 可去掉")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, _base_setting(), _chapters())
        ss.link(td)
        (td / "data/chapters/raw/02.md").unlink()
        rep = ss.status(td)
        item = [it for it in rep["items"] if it["name"] == "沈砚之"][0]
        check("S6 来源状态 missing", item["sources"][0]["state"] == "missing", item["sources"][0])
        check("S6 原因点名“第 2 章已不存在”",
              "已不存在" in item["sources"][0]["reason"], item["sources"][0]["reason"])
        code, res = ss.review(td, name="沈砚之", action="confirm")
        check("S6 confirm 章没了 → 拒绝（不假装核对过）",
              code == 400 and res.get("ok") is False and len(res.get("skipped") or []) == 1, res)
        code, res = ss.review(td, name="沈砚之", chapters=[2], action="drop")
        check("S6 drop 成功", code == 200 and res.get("ok") is True, res.get("error"))
        after = ss.status(td)
        check("S6 去掉来源后不再待核",
              not [it for it in after["items"] if it["name"] == "沈砚之" and it["state"] == "stale"],
              after["counts"])
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S7

def case_layer_switch():
    print("\n[S7] 换层也算变（raw → refined 同一内容）")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, _base_setting(), _chapters())
        ss.link(td)
        text = (td / "data/chapters/raw/01.md").read_text(encoding="utf-8")
        d = td / "data/chapters/refined"
        d.mkdir(parents=True, exist_ok=True)
        (d / "01.md").write_text(text, encoding="utf-8")     # 内容一样，只是换了层
        rep = ss.status(td)
        item = [it for it in rep["items"] if it["name"] == "沈砚"][0]
        check("S7 当前稿换层 → 待核", item["sources"][0]["state"] == "changed", item["sources"][0])
        check("S7 原因说明是换层", "层" in item["sources"][0]["reason"],
              item["sources"][0]["reason"])
        code, res = ss.review(td, name="沈砚", chapters=[1], action="confirm")
        check("S7 核对后版本跟随当前层",
              code == 200 and
              json.loads((td / "data/setting/setting.json").read_text(encoding="utf-8"))
              ["characters"][0]["sources"][0]["layer"] == "refined", res)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S8

def case_new_mention():
    print("\n[S8] 新出现的引用记 new，不冒充 stale")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, _base_setting(), _chapters())
        ss.link(td)
        p = td / "data/chapters/raw/02.md"
        p.write_text(p.read_text(encoding="utf-8") + "\n沈砚也到了。\n", encoding="utf-8")
        rep = ss.status(td)
        item = [it for it in rep["items"] if it["name"] == "沈砚"][0]
        check("S8 沈砚 记为 new（第 2 章新引用）",
              item["state"] == "new" and item["new_chapters"] == [2], item)
        check("S8 new 不计入 stale", rep["counts"]["new"] == 1, rep["counts"])
        out = ss.link(td)
        check("S8 link 补登记新引用", out["added_pairs"] == 1, out["added_pairs"])
        rep2 = ss.status(td)
        check("S8 补登记后回到 ok",
              not [it for it in rep2["items"] if it["name"] == "沈砚"],
              rep2["counts"])
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S9

def case_read_only():
    print("\n[S9] status 只读：不改变设定集一个字节")
    td = Path(tempfile.mkdtemp())
    try:
        sp = _mk(td, _base_setting(), _chapters())
        ss.link(td)
        before = sp.read_bytes()
        ss.status(td)
        ss.status(td)
        check("S9 status 前后 setting.json 字节一致", sp.read_bytes() == before)
        check("S9 status 不产生 history 备份",
              len(list((td / "data/setting/history").glob("setting_v*.json"))) == 1)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S10

def case_wiring():
    print("\n[S10] 接线：路由 + Agent 禁用名单")
    from nf_api_domains import setting_state as domain
    routes = {(m, p) for m, p, _ in domain.ROUTES}
    check("S10 GET /setting/stale 已登记", ("GET", "/setting/stale") in routes, sorted(routes))
    check("S10 POST /setting/stale/link 已登记",
          ("POST", "/setting/stale/link") in routes, sorted(routes))
    check("S10 POST /setting/stale/review 已登记",
          ("POST", "/setting/stale/review") in routes, sorted(routes))
    forbidden = agent_guard.FORBIDDEN_IN_AGENT_MODE
    check("S10 核对动作在 Agent 禁用名单", "/setting/stale/review" in forbidden)
    check("S10 读侧与机械登记不在禁用名单",
          "/setting/stale" not in forbidden and "/setting/stale/link" not in forbidden)
    src = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("S10 nf_api.py 有转发分支",
          "handle_setting_stale(" in src and "handle_setting_stale_review(self, body)" in src)


# --------------------------------------------------------------------- S11

def case_validation():
    print("\n[S11] 参数校验")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, _base_setting(), _chapters())
        ss.link(td)
        code, res = ss.review(td, name="", action="confirm")
        check("S11 name 必填 → 400", code == 400 and "name" in res.get("error", ""), res)
        code, res = ss.review(td, name="沈砚", action="drop")
        check("S11 drop 不给章号 → 400（防一次清空）",
              code == 400 and "章号" in res.get("error", ""), res)
        code, res = ss.review(td, name="不存在的人", action="confirm")
        check("S11 条目不存在 → 404", code == 404, res)
        code, res = ss.review(td, name="沈砚", action="bogus")
        check("S11 action 非法 → 400", code == 400, res)
        code, res = ss.review(td, name="沈砚", chapters=[9], action="confirm")
        check("S11 该条没登记在第 9 章 → 400", code == 400, res)
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------- S12

def case_world_subgroup():
    print("\n[S12] world 子分组可登记、可定位（kind 带点）")
    td = Path(tempfile.mkdtemp())
    try:
        _mk(td, {"characters": [], "world": {"locations": [{"name": "青石站"}]},
                 "plot_fragments": [], "timeline": []},
            {"raw": {1: "## 第1章\n\n青石站下着雨。\n"}})
        out = ss.link(td)
        check("S12 world.locations 已登记", out["added_pairs"] == 1, out)
        data = json.loads((td / "data/setting/setting.json").read_text(encoding="utf-8"))
        check("S12 来源写在正确子分组里",
              data["world"]["locations"][0]["sources"][0]["chapter"] == 1,
              data["world"]["locations"][0])
        code, res = ss.review(td, name="青石站", action="confirm")
        check("S12 不指定 kind 也能定位唯一同名条目", code == 200, res.get("error"))
        code, res = ss.review(td, name="青石站", kind="characters", action="confirm")
        check("S12 kind 不符 → 404", code == 404, res)
    finally:
        shutil.rmtree(td, ignore_errors=True)


def main():
    print("=" * 70)
    print("设定引用过期（M10）回归")
    print("=" * 70)
    case_missing_setting()
    case_first_link()
    case_chapter_edit_marks_stale()
    case_link_is_additive()
    case_confirm()
    case_missing_chapter()
    case_layer_switch()
    case_new_mention()
    case_read_only()
    case_wiring()
    case_validation()
    case_world_subgroup()
    print("\n" + "=" * 70)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        for name in FAIL:
            print("  FAIL: " + name)
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
