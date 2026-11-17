# -*- coding: utf-8 -*-
"""成书提炼摘要检索（2026-11-17，成书提炼 P0）：零 token、只读、缺口要说清。

## 判据

- A 关键词能命中章，并在结果里给出上下文（不是只报“命中”）；
- B 按角色名能筛；没有该角色的章不出现；
- C 还没摘要的章**必须列出来**，不能把“没查过”装成“没有”；
- D 只读：manifest 与摘要文件一个字节不动、不多出任何文件（不建索引）；
- E 找不到这册提炼时明确报错（不返回空结果冒充“里面什么都没有”）。

用法：python tests/unit/test_book_search.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.book_extract.search import load_camp, search_camp   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def _mk_camp():
    tmp = Path(tempfile.mkdtemp(prefix="nf_bs_"))
    camp = tmp / "data" / "book_extract" / "旧稿"
    (camp / "summaries").mkdir(parents=True)
    man = {"chapters": [
        {"seq": 1, "title": "雨夜", "summary": "summaries/0001.json"},
        {"seq": 2, "title": "断桥", "summary": "summaries/0002.json"},
        {"seq": 3, "title": "归途", "summary": "summaries/0003.json"},
    ]}
    (camp / "manifest.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    (camp / "summaries" / "0001.json").write_text(json.dumps({
        "章节": "雨夜", "人物": ["沈砚", "顾川"], "地点": ["城南渡口"],
        "事件": ["沈砚在雨里等到天亮", "夜巡的人换了岗"],
        "时间线": "第三年秋", "专有名词": ["夜巡"],
    }, ensure_ascii=False), encoding="utf-8")
    # 第 3 章有摘要；第 2 章故意缺（缺口清单必须点名）
    (camp / "summaries" / "0003.json").write_text(json.dumps({
        "章节": "归途", "人物": ["顾川"], "地点": [],
        "事件": ["顾川把伞留给守夜的人"], "时间线": "第三年冬", "专有名词": [],
    }, ensure_ascii=False), encoding="utf-8")
    return tmp, camp


def main():
    print("=" * 62)
    print("成书提炼摘要检索（零 token / 只读 / 缺口可见）")
    tmp, camp = _mk_camp()
    before = {p: p.read_bytes() for p in camp.rglob("*") if p.is_file()}

    print("\n【A】关键词命中")
    res = search_camp(str(camp), query="夜巡")
    check("A1 只命中第 1 章", [r["seq"] for r in res["results"]] == [1],
          [r["seq"] for r in res["results"]])
    snip = (res["results"][0]["fields"] or {}).get("事件", "") if res["results"] else ""
    check("A2 给出上下文（不是只报命中）", "夜巡" in snip, snip)
    check("A3 报出扫描了多少章", res["scanned"] == 2, res["scanned"])
    check("A4 没命中的查询返回空但不报错",
          search_camp(str(camp), query="根本不存在的词")["matched"] == 0)

    print("\n【B】按角色筛")
    res2 = search_camp(str(camp), character="顾川")
    check("B1 顾川出现在第 1、3 章", [r["seq"] for r in res2["results"]] == [1, 3],
          [r["seq"] for r in res2["results"]])
    check("B2 没有这个角色 → 空", search_camp(str(camp), character="没有的人")["results"] == [])

    print("\n【C】缺口清单")
    check("C1 第 2 章被点名为“还没摘要”",
          any(m["seq"] == 2 and "还没摘要" in m["why"] for m in res["missing"]),
          res["missing"])
    only = search_camp(str(camp), chapter=2)
    check("C2 只看第 2 章 → 没结果，但缺口仍说清", only["results"] == [] and
          any(m["seq"] == 2 for m in only["missing"]), only)

    print("\n【D】只读（不建索引、不改文件）")
    after = {p: p.read_bytes() for p in camp.rglob("*") if p.is_file()}
    check("D1 文件内容一个字节没变", before == after,
          [str(p) for p in before if before.get(p) != after.get(p)])
    check("D2 没有多出索引文件", set(before) == set(after),
          sorted(str(p) for p in set(after) - set(before)))

    print("\n【E】找不到这册提炼")
    try:
        load_camp("不存在的册", root=str(tmp))
        ok = False
        err = ""
    except FileNotFoundError as e:
        ok, err = True, str(e)
    check("E1 明确报错（不冒充空结果）", ok and "找不到" in err, err)

    print("\n" + "-" * 62)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
