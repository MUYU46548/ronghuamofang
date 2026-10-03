# -*- coding: utf-8 -*-
"""B1 进料节流回归 —— stage1 只喂「未定稿」素材。

## 为什么需要它

stage1 素材正文进 prompt 的机制**不在提示词模板里**，而在 `llm_client.inline_inputs()`：
它对 body 里以**目录**形式引用的路径做 `glob("*.md")` **全量内联**。所以历史行为是
「多回合迭代时 adopted 素材每轮重贴一遍，越跑越贵」（B1 缺口）。

| 编号 | 钉什么 |
|---|---|
| I1 | **防回归（最关键）**：body 里不得残留目录引用 —— 留着它就等于节流零效果（改了模板也白改） |
| I2 | adopted 素材**正文**不进 prompt（真实 inline 后文本零命中） |
| I3 | uncertain 素材正文**照旧进**（存疑要重看原文） |
| I4 | 纯新增素材正文**必须进**（防「把新增且无冲突的卡误判成已定稿」→ 丢素材） |
| I5 | 第 2 轮 prompt 显著短于第 1 轮（节流真的发生，而非「看起来改了」） |
| I6 | canon 存在时注入快照引用行 |
| I7 | 无 status / 设定集缺失 → 退回全量（宁多勿漏，绝不丢素材） |
| I8 | `plot_fragments.status`（`unused` 那套语义）**不得**被当成定稿判据 |

用法：python tests/unit/test_stage1_intake.py（零 LLM、零网络）
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils.llm_client import DIR_SPEC_RE, inline_inputs      # noqa: E402
from stage1_consolidate import (                             # noqa: E402
    _pending_material_paths, build_setting_task)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


# --------------------------------------------------------------------- 夹具

def build_sandbox(cards):
    """cards: [(素材名, 正文独有串, 体量重复次数)]。返回 (root, normalized_dir)。"""
    root = Path(tempfile.mkdtemp(prefix="nf_intake_"))
    nd = root / "normalized"
    nd.mkdir(parents=True, exist_ok=True)
    for name, marker, pad in cards:
        body = f"## 正文\n\n{marker}\n\n" + ("测试段落内容。" * pad) + "\n"
        (nd / f"{name}.md").write_text(
            f"# 角色：{name}\n\n## 基本信息\n- 身份：测试\n\n{body}",
            encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"count": len(cards), "materials": []}, ensure_ascii=False),
        encoding="utf-8")
    return root, nd


def write_setting(root, entries, name="setting.json"):
    p = root / name
    p.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def render(nd, root, setting_path, canon_path=None, make_canon=True):
    """走真实链路：build_setting_task → inline_inputs。返回 (body, inlined)。

    默认**造一个 canon**：节流的前提就是「定稿有承载」，没有 canon 时不该节流
    （见 `_pending_material_paths` 的说明）。要测「无 canon」就传 make_canon=False。
    """
    cp = Path(canon_path) if canon_path else (root / "canon.json")
    if make_canon and not cp.exists():
        cp.write_text(json.dumps({"_meta": {"canon_frozen_at": "2026-01-01T00:00:00"}},
                                 ensure_ascii=False), encoding="utf-8")
    body = build_setting_task(
        {}, str(root / "manifest.json"), str(nd),
        canon_path=str(cp), setting_path=str(setting_path))
    inlined, _missing = inline_inputs(body)
    return body, inlined


def adopted_entry(name, source):
    return {"name": name, "source": source, "status": "adopted"}


# --------------------------------------------------------------------- I1

def case_no_dir_ref():
    print("\n[I1] 防回归：body 里不得残留目录引用（否则节流零效果）")
    root, nd = build_sandbox([("甲_角色卡", "UNIQK_AAA", 2)])
    try:
        sp = write_setting(root, {"characters": []})
        body, _ = render(nd, root, sp)
        check("I1 body 不匹配 DIR_SPEC_RE（`下的 *.md`）",
              DIR_SPEC_RE.search(body) is None, f"→ {DIR_SPEC_RE.search(body)}")
        check("I1 body 里没有「归一化素材目录」字样",
              "归一化素材目录" not in body, "→ 目录引用没删干净")
        check("I1 模板用的是逐文件占位符 pending_inputs",
              "{{pending_inputs}}" not in body and (nd / "甲_角色卡.md").resolve().as_posix()
              in body.replace("\\", "/"), "→ 未列出具体文件")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------- I2/I3/I4

def case_adopted_out_pending_in():
    print("\n[I2/I3/I4] adopted 排除、uncertain 与新增保留（真实 inline 文本）")
    root, nd = build_sandbox([
        ("甲_角色卡", "UNIQK_ADOPTED_AAA", 3),
        ("乙_角色卡", "UNIQK_UNCERTAIN_BBB", 3),
        ("丙_全新卡", "UNIQK_NEW_CCC", 3),
    ])
    try:
        sp = write_setting(root, {"characters": [
            adopted_entry("甲", "甲_角色卡"),                       # 已定稿 → 排除
            {"name": "乙", "source": "乙_角色卡", "status": "uncertain"},  # 存疑 → 保留
            # 丙：设定集里**没有任何条目**引用 → 纯新增 → 必须保留
        ]})
        _body, inlined = render(nd, root, sp)
        check("I2 adopted 素材正文**零命中**",
              "UNIQK_ADOPTED_AAA" not in inlined, "→ 已定稿素材仍被贴进 prompt")
        check("I3 uncertain 素材正文仍进 prompt（防过杀）",
              "UNIQK_UNCERTAIN_BBB" in inlined, "→ 存疑素材被误杀")
        check("I4 纯新增素材正文**必须进**（防把新增无冲突卡误判成已定稿）",
              "UNIQK_NEW_CCC" in inlined, "→ 新增素材被误排除 = 丢素材")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------- I5

def case_round2_shorter():
    print("\n[I5] 第 2 轮 prompt 显著短于第 1 轮（节流真的发生）")
    cards = [(f"素材{i}_角色卡", f"UNIQK_CARD_{i}", 40) for i in range(10)]
    root, nd = build_sandbox(cards)
    try:
        # 第 1 轮：设定集不存在（或没有 status）→ 全量
        _b1, r1 = render(nd, root, str(root / "not_yet.json"))
        # 第 2 轮：8 个 adopted + 2 个新增
        sp = write_setting(root, {"characters": [
            adopted_entry(f"素材{i}", f"素材{i}_角色卡") for i in range(8)
        ]})
        _b2, r2 = render(nd, root, sp)
        ratio = len(r2) / max(len(r1), 1)
        check("I5 第 2 轮明显更短（≤ 第 1 轮的 50%）",
              ratio <= 0.5, f"→ 比率 {ratio:.0%}（r1={len(r1)}, r2={len(r2)}）")
        check("I5 被排除的 8 个素材正文均零命中",
              all(f"UNIQK_CARD_{i}" not in r2 for i in range(8)), "→ 有残留")
        check("I5 新增的 2 个素材正文仍在（素材8/素材9）",
              "UNIQK_CARD_8" in r2 and "UNIQK_CARD_9" in r2, "→ 新增被误杀")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------- I6

def case_canon_ref():
    print("\n[I6] canon 存在时注入快照引用")
    root, nd = build_sandbox([("甲_角色卡", "UNIQK_AAA", 2)])
    try:
        sp = write_setting(root, {"characters": [adopted_entry("甲", "甲_角色卡")]})
        canon = root / "canon.json"
        canon.write_text(json.dumps({"characters": []}, ensure_ascii=False),
                         encoding="utf-8")
        body_with, _ = render(nd, root, sp, canon_path=canon)
        canon.unlink()
        body_without, _ = render(nd, root, sp, canon_path=canon, make_canon=False)
        check("I6 有 canon → body 含其路径", "canon.json" in body_with,
              "→ 未注入 canon 引用")
        check("I6 无 canon → 不注入（不引不存在的文件）",
              "canon.json" not in body_without, "→ 凭空引用了 canon")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------- I7

def case_fail_open():
    print("\n[I7] 无 status / 异常 / 无 canon → 退回全量（宁多勿漏）")
    root, nd = build_sandbox([("甲_角色卡", "UNIQK_AAA", 2),
                              ("乙_角色卡", "UNIQK_BBB", 2)])
    try:
        canon = root / "canon.json"
        canon.write_text(json.dumps({"_meta": {}}, ensure_ascii=False), encoding="utf-8")
        # 条目存在但**没有** status 字段（首轮 / 旧数据）
        sp = write_setting(root, {"characters": [
            {"name": "甲", "source": "甲_角色卡"},
            {"name": "乙", "source": "乙_角色卡"},
        ]})
        kept, note = _pending_material_paths(str(nd), sp, str(canon))
        check("I7 无 status → 全量保留（2 个）", len(kept) == 2,
              f"→ {note} / {[f.name for f in kept]}")
        # 设定集损坏 → 退回全量而非抛错
        bad = root / "bad.json"
        bad.write_text("{ 这不是 JSON", encoding="utf-8")
        kept2, note2 = _pending_material_paths(str(nd), str(bad), str(canon))
        check("I7 设定集损坏 → 退回全量且不抛错", len(kept2) == 2, f"→ {note2}")
        # 设定集缺失 → 全量
        kept3, note3 = _pending_material_paths(str(nd), str(root / "nope.json"), str(canon))
        check("I7 设定集不存在 → 全量", len(kept3) == 2, f"→ {note3}")
        # **无 canon → 不节流**：定稿无承载时剔除 adopted 等于丢上下文
        sp2 = write_setting(root, {"characters": [adopted_entry("甲", "甲_角色卡")]},
                            name="setting2.json")
        kept4, note4 = _pending_material_paths(str(nd), sp2, str(root / "no_canon.json"))
        check("I7 无 canon → 不节流（adopted 仍全量进，防丢上下文）",
              len(kept4) == 2, f"→ {note4}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------------- I8

def case_plot_fragments_not_a_verdict():
    print("\n[I8] plot_fragments.status（unused 语义）不得当定稿判据")
    from utils.material_state import adopted_sources
    marked = {
        "characters": [{"name": "甲", "source": "甲_角色卡", "status": "adopted"}],
        "world": {"locations": [{"name": "某地", "source": "某地_场景卡",
                                 "status": "uncertain"}]},
        "plot_fragments": [
            # 剧情碎片的 status 是「用没用过」，不是「素材是否定稿」——
            # 它**不能**让 甲 之外的东西进 adopted 集
            {"id": "f001", "source": "碎片_录", "status": "used"},
            {"id": "f002", "source": "碎片2_录", "status": "unused"},
        ],
    }
    got = adopted_sources(marked)
    check("I8 adopted 集只含 characters/world 的 adopted 条目",
          got == {"甲_角色卡"}, f"→ {got}")
    check("I8 plot_fragments 的 source 一律不入 adopted 集（语义隔离）",
          "碎片_录" not in got and "碎片2_录" not in got, f"→ {got}")


def main():
    print("=" * 72)
    print("B1 进料节流回归（stage1 只喂未定稿素材）")
    print("=" * 72)
    case_no_dir_ref()
    case_adopted_out_pending_in()
    case_round2_shorter()
    case_canon_ref()
    case_fail_open()
    case_plot_fragments_not_a_verdict()
    print("\n" + "=" * 72)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 72)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
