# -*- coding: utf-8 -*-
"""功能—证据对账（2026-11-19）。

## 为什么要有这个脚本

2026-11-19 核账时发现：工作记忆里的“能力边界”表 7 条里 5 条是错的 ——
关系图、大纲阶段注入设定、设定增量补全、沙盒审核界面、开工方案**都已经做完**，
账上却写着“没做”；而账上写“有”的 `outline_advisor.py` 根本不存在。
于是每次盘点都从旧账推剩余量，一次推出 38 条、一次被读成“几百条”，
作者据此判定“内容天天丢”。

病根不是台账缺失，是**功能做完了没回写**（同一事实存多份副本、改一处不传播）。

## 这个脚本守什么

把“声称 → 证据”变成可复跑的判据，两个方向都查：

- `done`：证据必须**全部**存在 —— 缺一条红。防“账上吹牛”（说做了其实没做）。
- `todo`：证据必须**一条都没有** —— 找到就红。防“账本过期”（说没做其实做了）。
- `partial`：至少一条证据在，且允许列 `missing` 说明还缺哪块。

两个方向都红过，这个脚本才有意义。只查一个方向，另一半失真永远看不见。

## 证据写法

    file:相对路径              文件存在
    sym:相对路径::符号          文件里出现该字符串（函数/类/常量/路由串）
    route:METHOD /path          nf_api.py 里有这个动作路由串
    absent:目录::关键词         该目录（递归）里搜不到这个关键词 —— **只给 todo 项用**

## 为什么 todo 项必须用 absent

第一版用 `file:某个路径` 表示“不存在”。2026-11-19 立刻栽了：把
`scripts/utils/outline_advisor.py`（错路径）写成 todo 的证据，而真文件在
`scripts/outline_advisor.py` —— 路径错 → 认为“确实不存在” → 假绿。
`absent:` 扫的是整个目录，路径记错也能抓到东西。
`tests/unit/test_feature_audit.py` 的 C5 钉住这条：todo 项的证据必须全是 absent。

## 用法

    python scripts/feature_audit.py            # 全表对账
    python scripts/feature_audit.py --list     # 只列当前判为 todo/frozen 的条目
    python scripts/feature_audit.py --gaps     # 只列 partial 还缺什么

退出码：0 全绿 / 1 有红。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (id, 名称, 状态, 证据列表, 说明)
# 状态：done 已做完 / partial 有一半 / todo 确实没做 / frozen 冻结不做
# 增加条目时**必须给证据**，没有证据的“做了”等于没做。
FEATURES = [
    # ---------------- 曾被我记错过的（2026-11-19 逐条核实） ----------------
    ("F01", "角色关系图", "done",
     ["file:console/src/RoleGraph.vue",
      "sym:console/src/roleGraphData.js::buildGraph",
      "sym:console/src/App.vue::<RoleGraph"],
     "d3-force 力导向图，设定面板“关系图”标签页；2026-11-19 才发现账上写的是“挂起”。"),

    ("F02", "大纲阶段注入设定集", "done",
     ["sym:scripts/stage2_outline.py::setting_brief.build",
      "file:scripts/utils/setting_brief.py"],
     "2026-10-20 起把设定集压成有预算的摘要内联进 stage2 任务书（角色/地点/伏笔/时间线）。"),

    ("F03", "设定集增量补全", "done",
     ["file:scripts/setting_refine.py",
      "sym:scripts/setting_refine.py::增量"],
     "不重跑 stage1，只按素材推断补全，产物带 llm_inferred 标记。"),

    ("F04", "沙盒审核界面", "done",
     ["file:console/src/SandboxQueue.vue",
      "sym:console/src/App.vue::<SandboxQueue"],
     "队列逐条过、可批准/拒绝；账上曾写“GUI 无审核界面”。"),

    ("F05", "开局方案（N 个走向候选）", "done",
     ["file:scripts/utils/open_variants.py",
      "route:POST /outline/variants/generate",
      "sym:console/src/OutlineView.vue::/outline/variants"],
     "产物 open_variants.json，作者选定后写 open_variant_chosen.md，stage2 会读。"),

    ("F06", "迭代方向建议（零 token）", "done",
     ["file:scripts/outline_advisor.py",
      "sym:scripts/outline_advisor.py::fix_structure"],
     "开工方向建议 400 行，2026-09-21 就有：读 outline_review 的判据，给"
     "“修结构 / 回退 / 补维度”三类可执行方案，确定性生成不调模型。"
     "**位置是 scripts/，不是 scripts/utils/** —— 2026-11-19 我第一次核这条时"
     "按 utils/ 找，判成“全仓不存在”，差点又造一份假账。"),

    ("F07", "文体写法指导（书信/单元）", "done",
     ["file:examples/samples/forms",
      "file:prompts/style"],
     "2026-11-07 改成自造样本，不再需要作者提供真实书稿。"
     "四处旧文档仍写“等真实样本”，2026-11-19 已加日期更正。"),

    # ---------------- 设定与一致性 ----------------
    ("S01", "设定待核（章改了 → 条目待核）", "done",
     ["file:scripts/utils/setting_stale.py",
      "route:GET /setting/stale"],
     "2026-11-04 落地。"),

    ("S02", "设定改过 → 已检查的章要复检", "done",
     ["file:scripts/utils/setting_impact.py",
      "route:GET /setting/impact"],
     "2026-11-19 落地，只标真引用过该条目的章。"),

    ("S03", "并库前冲突清单 + 同名合并", "done",
     ["sym:scripts/utils/book_extract/runner.py::merge_into_setting",
      "sym:scripts/utils/book_extract/runner.py::overwrite"],
     "默认只增不改，内容不同的列为冲突，勾选才覆盖，且 id / locked 永远保留。"
     "注意：`build_setting_from_vault()` 仍是**整体替换**（vault 重建语义），"
     "两条通道不同，别混 —— 记忆里“整体替换非合并”只对后者成立。"),

    ("S04", "跨章硬伤 → 能作答的问题", "done",
     ["sym:scripts/utils/ask.py::collect_story_conflict_questions"],
     "阶段 5 收尾在检查稿层重建账本，按章转成“重写/按现状收/先放着”。"),

    # ---------------- 判据与闸门 ----------------
    ("J01", "章节质量清单没过 → 能作答的问题", "done",
     ["sym:scripts/utils/ask.py::collect_quality_questions",
      "sym:scripts/quality_checklist.py::quality.json"],
     "2026-11-19 落地。"),

    ("J02", "句法病读不通 → 能作答的问题", "done",
     ["sym:scripts/utils/ask.py::collect_unreadable_questions",
      "sym:scripts/utils/ask.py::annotate_unreadable"],
     "2026-11-19 落地，答题后在命中句所在段落挂批注，不调模型。"),

    ("J03", "阶段 5 时代特征回头校验", "todo",
     ["absent:scripts::时代特征"],
     "设定侧 setting_period 能做时期，写作侧不回头校验。11-17 冻结清单内（新判据）。"),

    # ---------------- 改写与审稿（只出任务书，刻意不自动改写） ----------------
    ("W01", "去 AI 腔", "partial",
     ["sym:scripts/utils/rewrite_studio.py::dehumanize"],
     "按检测出的 AI 腔开讨论 + 出任务书（零 token）。**刻意不做一键自动改写** —— "
     "改写是创作决策。缺的是“花 token 真改一遍”，且那是外部 Agent 的活，不是代码缺口。"),

    ("W02", "读者视角审稿", "partial",
     ["sym:scripts/utils/rewrite_studio.py::reader"],
     "站读者位置找弃书点，同样只出任务书。"),

    # ---------------- 流程边界（要作者定，不许自己改） ----------------
    ("P01", "逐章验收开关（每章写完自动停）", "partial",
     ["file:scripts/utils/pause_flag.py",
      "sym:scripts/stage4_writing.py::pause"],
     "“写完这章就停”2026-11-02 就落地并真机验过；差异在形态 —— 现在是跑前设 flag，"
     "不是每章自动停、点一下才继续。后者改暂停语义 + 界面，属流程边界，要作者定。"),

    ("P02", "写作 Skill 导入（materials/skills）", "todo",
     ["absent:scripts::materials/skills",
      "absent:console/src::materials/skills"],
     "确认为零实现：materials/ 下没有 skills/，代码零引用。属新功能，冻结清单内。"),

    # ---------------- 外部阻塞 / 不立项 ----------------
    ("X01", "DSH 插件仓 dsh-novelforge", "frozen",
     ["file:../dsh-novelforge"],
     "2026-11-17 由 Agent 自行创建，作者未立项。按参考资料对待，不推进。"),
]


def _check(ev: str) -> tuple[bool, str]:
    """返回 (是否成立, 人话原因)。"""
    ev = (ev or "").strip()
    if ev.startswith("file:"):
        rel = ev[5:].strip()
        p = (ROOT / rel).resolve()
        ok = p.exists()
        return ok, ("存在" if ok else "缺文件 %s" % rel)
    if ev.startswith("sym:"):
        body = ev[4:]
        if "::" not in body:
            return False, "证据写法错（应为 sym:路径::符号）：%s" % ev
        rel, symbol = body.split("::", 1)
        p = (ROOT / rel.strip()).resolve()
        if not p.is_file():
            return False, "缺文件 %s" % rel.strip()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return False, "读不了 %s：%s" % (rel.strip(), e)
        ok = symbol in text
        return ok, ("找到 %r" % symbol if ok else "%s 里没有 %r" % (rel.strip(), symbol))
    if ev.startswith("route:"):
        spec = ev[6:].strip()
        path = spec.split(None, 1)[-1] if " " in spec else spec
        p = ROOT / "scripts" / "nf_api.py"
        if not p.is_file():
            return False, "缺 scripts/nf_api.py"
        text = p.read_text(encoding="utf-8", errors="replace")
        ok = ('"%s"' % path) in text
        return ok, ("路由在" if ok else "nf_api.py 里没有 %s" % path)
    if ev.startswith("absent:"):
        body = ev[7:]
        if "::" not in body:
            return False, "证据写法错（应为 absent:目录::关键词）：%s" % ev
        where, keyword = body.split("::", 1)
        base = (ROOT / where.strip()).resolve()
        if not base.is_dir():
            return False, "absent 的目录不存在：%s" % where.strip()
        hits = []
        # 跳过本文件自身：absent 的关键词就写在这里（2026-11-19 第一次跑时，
        # 脚本把自己搜出来了 —— “时代特征”这个词是它自己的证据）。这不是特例，
        # 任何“搜不到”型判据都必须排除判据自身的载体。
        self_path = Path(__file__).resolve()
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            if f.resolve() == self_path:
                continue
            if f.suffix not in (".py", ".vue", ".js", ".md", ".yaml", ".yml", ".json"):
                continue
            if "__pycache__" in f.parts or "node_modules" in f.parts:
                continue
            try:
                if keyword in f.read_text(encoding="utf-8", errors="replace"):
                    hits.append(str(f.relative_to(ROOT)))
            except OSError:
                continue
            if len(hits) >= 3:
                break
        ok = not hits
        return ok, ("全目录都没有" if ok else "在 %s 里搜到了" % "、".join(hits))
    return False, "未知证据前缀：%s" % ev


def audit(verbose: bool = False) -> int:
    rows, bad = [], 0
    for fid, name, status, evidence, note in FEATURES:
        results = [(ev, *_check(ev)) for ev in evidence]
        hit = [r for r in results if r[1]]        # 证据成立（absent 的“成立”= 确实没有）
        miss = [r for r in results if not r[1]]
        all_hit = not miss

        if status == "done":
            ok = all_hit
            why = "" if ok else "账上说做了，但 %d 条证据缺失：%s" % (
                len(miss), "；".join(r[0] for r in miss))
        elif status == "todo":
            # todo 项的证据必须是 absent（“全目录搜不到”）。它成立 = 确实没有 = 绿；
            # 不成立 = 找到了 = 账本过期 = 红。
            bad_kind = [r[0] for r in results if not r[0].startswith("absent:")]
            if bad_kind:
                ok = False
                why = "todo 项必须用 absent: 证据（防止路径写错导致假绿）：%s" % "、".join(bad_kind)
            else:
                ok = all_hit
                why = "" if ok else ("账上说没做，但已经搜到了：%s"
                                     % "；".join(r[2] for r in miss))
        elif status == "frozen":
            ok = True
            why = "冻结，不计入"
        else:                                    # partial
            ok = len(hit) >= 1
            why = "" if ok else "标 partial 但一条证据都没有"

        if not ok:
            bad += 1
        rows.append((fid, name, status, ok, why, results))

    if verbose:
        for fid, name, status, ok, why, results in rows:
            mark = "OK  " if ok else "FAIL"
            print("[%s] %s %-32s (%s)" % (mark, fid, name, status))
            for ev, hit, reason in results:
                print("        %s %s — %s" % ("+" if hit else "-", ev, reason))
            if why:
                print("        => " + why)

    total = len(rows)
    done_n = sum(1 for r in rows if r[2] == "done")
    part_n = sum(1 for r in rows if r[2] == "partial")
    todo_n = sum(1 for r in rows if r[2] == "todo")
    print("\n功能对账：%d 条（done %d / partial %d / todo %d），红 %d"
          % (total, done_n, part_n, todo_n, bad))
    if bad:
        print("红了 = 账本与代码不一致，先改账本或补代码，别当没看见。")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="功能—证据对账（双向）")
    ap.add_argument("--list", action="store_true", help="只列 todo/partial 条目与说明")
    ap.add_argument("--gaps", action="store_true", help="只列 partial 条目")
    ap.add_argument("--quiet", action="store_true", help="只输出合计")
    args = ap.parse_args(argv)

    if args.list or args.gaps:
        want = ("partial",) if args.gaps else ("partial", "todo")
        for fid, name, status, evidence, note in FEATURES:
            if status in want:
                print("\n%s %s（%s）" % (fid, name, status))
                print("  " + note)
        return 0

    return audit(verbose=not args.quiet)


if __name__ == "__main__":
    sys.exit(main())
