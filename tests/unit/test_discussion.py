# -*- coding: utf-8 -*-
"""讨论构件：讨论 = 一份会传导到正文的数据（2026-11-03）。

## 为什么有这份用例

作者判词：墨坊"讨论余地不是没做好，是架构里没有这个构件"。取证（零 token）：

    $ grep -rn "discuss" scripts/stage4_writing.py scripts/stage3_chapter_outline.py
    （零命中）

讨论稿写得再好，写作阶段一个字都不读 —— 2026-10-25“潮汐档案”03/05 章
"讨论过、也重写过，refined 还是旧稿"就是这么来的。本用例钉住三条通路：

1. 讨论有地方落（只追加的轮次稿 + 机读状态）
2. 结论结构化（`constraints.json`：必须发生 / 绝不发生 / 人物口吻 / 伏笔）
3. 结论真的进正文（模板占位符 + 两个写作入口都注入 + 定稿晚于正文则判待重写）

## 判据（每条都能变红）

- D1 开题建 md + json（status=open）
- D2 追加轮次只增不改写，rounds 递增
- D3 **没有一条约束不许定稿**（"我们聊过了"不是结论）
- D4 定稿写入 constraints + md 的【结论】段；重复定稿替换而不是叠加
- D5 多主题约束合并去重
- D6 渲染块：没有已定稿讨论 → 空串；有 → 含各维度；每维有条数上限
- D7 `stale_chapters`：定稿晚于正文 → 报；正文更新 → 不报
- D8 stage4 用 stale_chapters 决定重写
- D9 两个写作模板都有占位符，且占位符真的能渲染出内容
- D10 两个写作入口都把 discussion_block 传进模板变量

用法：python tests/unit/test_discussion.py
"""
import io
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import discussion                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    print("讨论构件（discussion）")

    tmp = Path(tempfile.mkdtemp())
    root = tmp

    print("\n【1】开题 + 追加轮次")
    md, js = discussion.open_thread(3, "结局要不要开信", "第七封信该不该当场打开？", root=root)
    check("D1 md 建出来了", md.exists(), md)
    check("D1 json 建出来了", js.exists(), js)
    st = discussion._read_json(js)
    check("D1 初始状态 open", st.get("status") == "open", st)
    discussion.append_round(3, "结局要不要开信", "author", "先别开，留到最后一章", root=root)
    n2 = discussion.append_round(3, "结局要不要开信", "ai", "可以先让她只摸到信封", root=root)
    body = md.read_text(encoding="utf-8")
    check("D2 轮次递增到 2", n2 == 2, n2)
    check("D2 两轮发言都在稿里",
          "先别开，留到最后一章" in body and "只摸到信封" in body, body[-200:])
    check("D2 作者/AI 都标了身份", "作者" in body and "AI" in body, body)

    print("\n【2】定稿必须带约束")
    ok, msg = discussion.decide(3, "结局要不要开信", root=root)
    check("D3 空结论被拒", ok is False and "不是结论" in msg, msg)
    check("D3 被拒后仍是 open",
          discussion._read_json(js).get("status") == "open")

    ok, msg = discussion.decide(
        3, "结局要不要开信",
        must=["叶清和当场打开第七封信", "信里必须出现她自己的笔迹"],
        must_not=["陈姨说出真相"],
        voice=["叶清和：短句，不解释情绪"],
        foreshadow_plant=["铜钥匙"],
        foreshadow_payoff=["1997 年寄信的女人"],
        summary="决定开信，但把真相留给下一章", root=root)
    check("D4 定稿成功", ok is True, msg)
    st = discussion._read_json(js)
    check("D4 状态 decided", st.get("status") == "decided", st.get("status"))
    check("D4 约束落盘", len(st["constraints"]["must"]) == 2, st["constraints"])
    body = md.read_text(encoding="utf-8")
    check("D4 稿里出现【结论】段", "## 【结论】" in body, body[-300:])
    check("D4 结论段含必须发生项", "叶清和当场打开第七封信" in body)

    ok, _ = discussion.decide(3, "结局要不要开信", must=["改主意：不打开"], root=root)
    body = md.read_text(encoding="utf-8")
    check("D4b 重复定稿替换而不是叠加", body.count("## 【结论】") == 1, body.count("## 【结论】"))
    check("D4b 新结论生效", "改主意：不打开" in body and "叶清和当场打开第七封信" not in body)

    print("\n【3】合并 / 渲染")
    discussion.open_thread(3, "第二处讨论", root=root)
    discussion.decide(3, "第二处讨论", must_not=["出现旁白解说"], root=root)
    cons = discussion.load_constraints(3, root=root)
    check("D5 两处讨论合并", cons["must"] == ["改主意：不打开"], cons)
    check("D5 must_not 也合并", "出现旁白解说" in cons["must_not"], cons)

    block = discussion.render_for_prompt(3, root=root)
    check("D6 渲染块含标题", "本章讨论结论" in block, block[:60])
    check("D6 渲染块含必须发生/绝不发生",
          "必须发生" in block and "绝不发生" in block, block)
    check("D6 渲染块声明它是约束不是建议", "不是建议" in block, block)
    check("D6 没有讨论的章渲染为空", discussion.render_for_prompt(9, root=root) == "")

    many = ["第%d条约束" % i for i in range(20)]
    discussion.open_thread(10, "很多条", root=root)
    discussion.decide(10, "很多条", must=many, root=root)
    blk = discussion.render_for_prompt(10, root=root, cap=5)
    check("D6b 每维有条数上限", blk.count("- 第") == 5, blk.count("- 第"))
    check("D6b 截断时说明还有多少条", "另有 15 条" in blk, blk[-120:])

    print("\n【4】定稿晚于正文 → 待重写")
    raw = tmp / "data" / "chapters" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    old = raw / "03.md"
    old.write_text("旧稿", encoding="utf-8")
    os.utime(old, (time.time() - 3600, time.time() - 3600))     # 一小时前
    stale = discussion.stale_chapters(raw, root=raw.parents[2])
    check("D7 旧稿 → 判待重写", [n for n, _p, _a in stale] == [3], stale)

    new = raw / "03.md"
    new.write_text("新稿", encoding="utf-8")
    future = time.strftime("%Y-%m-%dT%H:%M:%S")
    time.sleep(1.1)                                            # 让 mtime 明确晚于定稿
    os.utime(new, None)
    stale = discussion.stale_chapters(raw, root=raw.parents[2])
    check("D7 正文比定稿新 → 不再判待重写", stale == [], (stale, future))

    print("\n【5】接线：三个入口都要真的带上约束")
    s4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("D8 stage4 用 stale_chapters 决定重写", "stale_chapters(raw_dir)" in s4)
    check("D8 stage4 把 discussion_block 交给模板", '"discussion_block": discussion_block' in s4)
    enh = (ROOT / "scripts" / "utils" / "enhanced_chapter.py").read_text(encoding="utf-8")
    check("D10 加强模式（主路径）也注入", "_discussion_block(cfg, proj, n)" in enh)

    from utils.template_loader import load_template
    for tpl in ("stage4_writing.md", "stage4_group.md"):
        src = (ROOT / "prompts" / tpl).read_text(encoding="utf-8")
        check("D9 %s 有 discussion_block 占位符" % tpl, "{{discussion_block}}" in src)
    _, rendered = load_template("stage4_group.md",
                               {"n": 3, "g": 1, "k": 5, "discussion_block": "【实证块】必须开信"})
    check("D9b 占位符真的能渲染出内容", "【实证块】必须开信" in rendered, rendered[-200:])

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
