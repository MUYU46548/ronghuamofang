# -*- coding: utf-8 -*-
"""资料工具（①）+ 讨论续做（③）+ 语义审稿窄口（④）—— 2026-11-03。

一条用例钉住三件事（零 token，纯读盘）：

- **① 资料工具**：设定/素材/笔记建摘要索引，按关键词查，返回名字+一句话+出处；
  笔记只进 `data/notes/`，**不许**冒充正典。
- **③ 讨论续做**：讨论断了要知道"轮到你/轮到作者"，并列出还没定稿的讨论。
- **④ 语义审稿**：只对两个焦点开讨论 + 出任务书，**产物是讨论稿不是分数**。

判据（每条都能变红）：L1~L7 / N1~N3 / S1~S4。
用法：python tests/unit/test_lookup_and_semantic.py
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
from utils import lookup, discussion, review_semantic       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def make_project(tmp):
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(json.dumps({
        "characters": [{"name": "叶清和", "desc": "沅口邮局分拣员，负责死信箱"},
                       {"name": "陈姨", "desc": "邮局老职工，知道 1997 年的事"}],
        "locations": [{"name": "沅口邮局", "desc": "临江老楼，二楼分拣室"}],
        "concepts": ["死信箱"],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "materials" / "raw" / "潮汐档案.md").write_text(
        "# 潮汐档案\n\n一份 1997 年的旧卷宗，记着七封信的去向。\n", encoding="utf-8")
    (tmp / "data" / "chapters" / "refined").mkdir(parents=True)
    (tmp / "data" / "chapters" / "refined" / "03.md").write_text(
        "## 第3章 雨夜\n\n叶清和把信摊在柜台上，读到第三遍才折回去。\n\n"
        "陈姨在门口站着，没有进来。\n", encoding="utf-8")


def main():
    print("=" * 66)
    print("资料工具 / 讨论续做 / 语义审稿")
    tmp = Path(tempfile.mkdtemp())
    make_project(tmp)
    root = str(tmp)

    print("\n【1】① 建索引 + 查")
    idx = lookup.build_index(root)
    check("L1 索引落盘且有条目", (tmp / "data/index/lookup.json").exists() and idx["count"] >= 4,
          idx.get("count"))
    kinds = {e["kind"] for e in idx["entries"]}
    check("L1 角色/地点/素材都进索引",
          {"character", "location", "material"} <= kinds, kinds)
    hits = lookup.search("叶清和", root=root)
    check("L2 按人名查到", hits and hits[0]["name"] == "叶清和", hits[:1])
    check("L2 带出处路径", hits and hits[0]["path"].endswith("setting.json"), hits[:1])
    hits = lookup.search("潮汐", root=root)
    check("L3 素材按标题命中", any("潮汐" in h["name"] for h in hits), hits[:2])
    check("L3 kind 过滤生效",
          all(h["kind"] == "material" for h in lookup.search("潮汐", kind="material", root=root)))
    check("L4 查不到 → 空（不硬凑）", lookup.search("完全不存在的东西", root=root) == [])
    blk = lookup.render("陈姨", root=root)
    check("L5 渲染块含名字与出处", "陈姨" in blk and "出处" in blk, blk[:80])

    print("\n【2】① 案头笔记不是正典")
    p = lookup.add_note("沅口的潮汐规律", "每月初三大潮，邮局后院会积水。", root=root)
    check("L6 笔记写进 data/notes", p.exists() and "data" in str(p) and "notes" in str(p), p)
    check("L6 笔记声明不是正典", "不是正典" in p.read_text(encoding="utf-8"))
    check("L6 不碰 setting.json",
          "正在" not in (tmp / "data/setting/setting.json").read_text(encoding="utf-8"))
    lookup.build_index(root)
    check("L7 重建后笔记可被查到",
          any(h["kind"] == "note" for h in lookup.search("潮汐规律", root=root)),
          lookup.search("潮汐规律", root=root)[:2])

    print("\n【3】③ 讨论续做：轮次与待定")
    discussion.open_thread(5, "要不要让陈姨先开口", root=root)
    st = discussion.list_threads(5, root=root)[0]
    check("N1 空讨论 → 轮到作者说清犹豫什么",
          discussion.next_step(st)["role"] == "author", discussion.next_step(st))
    discussion.append_round(5, "要不要让陈姨先开口", "author", "让她先开口，但只问一句。", root=root)
    st = discussion.list_threads(5, root=root)[0]
    check("N2 作者说完 → 轮到 AI", discussion.next_step(st)["role"] == "ai", discussion.next_step(st))
    discussion.append_round(5, "要不要让陈姨先开口", "ai", "那就让她问一句“这信你读了几遍”", root=root)
    st = discussion.list_threads(5, root=root)[0]
    check("N3 AI 说完 → 轮到作者", discussion.next_step(st)["role"] == "author", discussion.next_step(st))
    check("N3 未定稿讨论在 open_threads 里",
          any(s["topic"] == "要不要让陈姨先开口" for s in discussion.open_threads(root=root)))
    discussion.decide(5, "要不要让陈姨先开口", must=["陈姨只问一句"], root=root)
    check("N3b 定稿后不再出现在待定列表",
          all(s["topic"] != "要不要让陈姨先开口" for s in discussion.open_threads(root=root)))
    check("N3c 定稿后的 next_step 说清【要改就开新讨论】",
          "新讨论" in discussion.next_step(discussion.list_threads(5, root=root)[0])["ask"])

    print("\n【4】④ 语义审稿：产物是讨论，不是分数")
    res = review_semantic.build_review(3, root=root)
    check("S1 生成任务书", res.get("ok") and Path(res["task"]).exists(), res)
    check("S1 开了审稿讨论",
          any(s["topic"] == "审稿-情绪曲线" for s in discussion.list_threads(3, root=root)))
    body = Path(res["task"]).read_text(encoding="utf-8")
    check("S2 任务书里有正文与工具指引",
          "叶清和把信摊在柜台上" in body and "nf_discuss_say" in body, body[:60])
    check("S2 任务书要求只谈一个焦点", "只谈" in body and "情绪曲线" in body)
    check("S3 不产分数（正文里没有评分口径）", "分" not in body.split("## 怎么交作业")[0][-40:], body[-200:])
    res2 = review_semantic.build_review(99, root=root)
    check("S4 没有正文的章 → 明确报错", res2.get("ok") is False and "正文" in res2["error"], res2)
    check("S4b 视角漂移是另一个焦点",
          review_semantic.build_review(3, focus="视角漂移", root=root).get("ok") is True)

    print("\n【5】接线")
    mcp = (ROOT / "scripts" / "nf_mcp.py").read_text(encoding="utf-8")
    for name in ("nf_lookup", "nf_note_add", "nf_notes_list", "nf_review_semantic"):
        check("接 MCP：%s" % name, '"name": "%s"' % name in mcp)
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    for path in ("/lookup", "/notes/add", "/review/semantic"):
        check("接 HTTP：%s" % path, '"%s"' % path in api)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
