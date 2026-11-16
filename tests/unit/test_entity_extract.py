# -*- coding: utf-8 -*-
"""成稿 → 词条回流：三分清单 + 沙盒任务书（R7，2026-11-16）。

## 缺的是中间那一步

`book_extract` 已经能把一本书提炼成角色卡/设定卡，`obsidian_bridge` 能读库里已有词条；
缺的是“这份清单该新建 / 补充 / 不建”。OB37 那 20 个词条全是手写、还要逐条核对
库内已有的 10 个空壳角色 —— 先出清单再写，是那本书唯一没返工的一步。

## 判据（每条都能变红）

- R7a 三分判对：库里没有→新建；有同名且带新事实→补充；有同名无新事实→不建
- R7b 库里同名两条 → 标“重名冲突”（链接解析歧义，回执点名的坑）
- R7c 清单落盘（md + json），每条带成书里的出处
- R7d `--tasks` 出沙盒任务书，写清目标路径与“禁止写 vault”
- R7e 没有成书提炼产物 → 直接说先跑 book_extract（不出空清单）
- R7f vault 没配置 → 全部按新建，并如实标注（不假装知道库里有什么）

用法：python tests/unit/test_entity_extract.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import entity_extract as ee                                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


CHARACTERS = [
    {"名字": "何望", "身份": "机房的记录员", "性格特质": ["寡言"],
     "当前关系": [{"对象": "白榆", "关系": "同事"}], "出场章节": ["第1章", "第3章"]},
    {"名字": "白榆", "身份": "老工人", "出场章节": ["第2章"]},
    {"名字": "阿戊", "身份": "", "出场章节": []},
]
SETTINGS = [
    {"设定名": "机房", "类别": "地点", "内容": "",
     "确立章节": [], "补充章节": []},
    {"设定名": "工牌", "类别": "制度", "内容": "记录在册人数", "确立章节": ["第1章"]},
]


def make_camp(tmp, name="测试书"):
    camp = tmp / "data" / "book_extract" / name
    camp.mkdir(parents=True, exist_ok=True)
    (camp / "characters.json").write_text(json.dumps(CHARACTERS, ensure_ascii=False),
                                          encoding="utf-8")
    (camp / "settings.json").write_text(json.dumps(SETTINGS, ensure_ascii=False),
                                        encoding="utf-8")
    return camp


def main():
    print("=" * 66)
    print("成稿 → 词条三分清单（R7）")
    tmp = Path(tempfile.mkdtemp())
    make_camp(tmp)

    # 库里已有：何望（1 条）、白榆（2 条同名 → 冲突）、机房（1 条）
    index = {
        ee._norm("何望"): [{"name": "何望", "path": "roles/何望.md", "locked": True}],
        ee._norm("白榆"): [{"name": "白榆", "path": "roles/白榆.md"},
                           {"name": "白榆", "path": "archive/白榆.md"}],
        ee._norm("机房"): [{"name": "机房", "path": "places/机房.md"}],
    }

    print("\n【1】三分判定")
    res = ee.build("测试书", root=str(tmp), index=index,
                   vault_meta={"configured": True, "entries": 4})
    rows = {r["name"]: r for r in res["rows"]}
    check("R7a 库里没有 → 新建", rows["阿戊"]["kind"] == ee.KIND_NEW, rows["阿戊"])
    check("R7a 有同名且带新事实 → 补充", rows["何望"]["kind"] == ee.KIND_FILL, rows["何望"])
    check("R7a 有同名无新事实 → 不建", rows["机房"]["kind"] == ee.KIND_SKIP, rows["机房"])
    check("R7b 同名两条 → 标重名冲突",
          "同名条目" in rows["白榆"]["why"], rows["白榆"]["why"])
    check("R7a 计数对得上",
          res["meta"]["counts"] == {ee.KIND_NEW: 2, ee.KIND_FILL: 2, ee.KIND_SKIP: 1},
          res["meta"]["counts"])

    print("\n【2】清单落盘与出处")
    md = Path(res["plan_path"])
    js = Path(res["json_path"])
    check("R7c 清单 md 落盘", md.exists(), md)
    check("R7c 清单 json 落盘", js.exists(), js)
    text = md.read_text(encoding="utf-8")
    check("R7c md 里有三分小标题",
          "## 新建" in text and "## 补充" in text and "## 不建" in text, text[:160])
    check("R7c 出处来自卡片的出场章节", "第1章" in text and "第3章" in text, text[:400])
    check("R7c 重名冲突单独一段", "重名冲突" in text)
    data = json.loads(js.read_text(encoding="utf-8"))
    check("R7c json 里带 rows 与 counts",
          isinstance(data.get("rows"), list) and data.get("counts"), list(data)[:6])

    print("\n【3】沙盒任务书（不许写 vault）")
    res2 = ee.build("测试书", root=str(tmp), want_tasks=True, index=index,
                    vault_meta={"configured": True, "entries": 4})
    check("R7d 出了任务书", len(res2["tasks"]) >= 3, res2["tasks"])
    check("R7d 不建的那条不出任务书",
          all(t["name"] != "机房" for t in res2["tasks"]), [t["name"] for t in res2["tasks"]])
    t0 = Path(res2["tasks"][0]["task"]).read_text(encoding="utf-8")
    check("R7d 任务书写清目标路径", "sandbox/entity_drafts" in t0, t0[:200])
    check("R7d 任务书禁止写 vault", "禁止" in t0 and "vault" in t0, t0[-300:])
    check("R7d 任务书指向模板",
          "obsidian_role_page.md" in t0 or "obsidian_book_page.md" in t0)
    check("R7d 目标文件确实在沙盒目录下（还没写词条）",
          all("sandbox/entity_drafts" in t["target"] for t in res2["tasks"]))
    check("R7d 没有真的写出词条草稿",
          not any(Path(t["target"]).exists() for t in res2["tasks"]),
          [t["target"] for t in res2["tasks"]])

    print("\n【4】边界")
    try:
        ee.build("根本没有这本书", root=str(tmp), index={}, vault_meta={"configured": True})
        check("R7e 没有提炼产物 → 报错并指路", False, "没抛错")
    except FileNotFoundError as e:
        check("R7e 没有提炼产物 → 报错并指路",
              "book_extract" in str(e), str(e)[:120])
    res3 = ee.build("测试书", root=str(tmp), index={},
                    vault_meta={"configured": False, "error": "未配置 vault"})
    check("R7f vault 未配置 → 全部新建",
          res3["meta"]["counts"][ee.KIND_NEW] == 5, res3["meta"]["counts"])
    check("R7f 如实标注没扫描库", res3["meta"]["vault"]["configured"] is False)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
