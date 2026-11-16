# -*- coding: utf-8 -*-
"""成稿 → 词条回流：抽实体 → 对照已有词条 → 三分清单 → 沙盒任务书（R7，2026-11-16）。

## 缺的是哪一步

墨坊早就有了“把一本书提炼成正典”的管线（`book_extract`：摘要 → 角色卡 / 设定卡），
也有 Obsidian 读写桥（`obsidian_bridge.scan_vault`）。缺的是**中间那一步**：

    book_extract 产出的角色/设定   ×   vault 里已有的词条
    → 这份清单该“新建 / 补充 / 不建”？

OB37 施工回执 §三 第 8 条：那 20 个沙盒文件全是手写的，20 个文件里还要
逐条核对库内已有的 10 个空壳角色词条。**先出清单、再写词条**这个顺序，
是那本书唯一没返工的一步。

## 四条纪律

1. **只算不写 vault**：默认输出清单（`data/setting/entity_plan.md|json`），
   词条草稿只出**任务书**给外部执行方（`--tasks`），落到项目内沙盒目录，**不写 vault 本体**。
2. **重名必须报**：库里两个同名条目会让链接解析歧义（回执点名的坑）。
3. **每一条都要能点回出处**：清单里的 `evidence` 来自 book_extract 卡片的
   `出场章节` / `确立章节`，不是模型的印象。
4. **没有成书提炼产物就不装样子**：直接说"先跑 book_extract"，不出空清单。

用法：

    python scripts/entity_extract.py --camp 划名            # 三分清单
    python scripts/entity_extract.py --camp 划名 --tasks     # 顺带出沙盒词条任务书
    python scripts/entity_extract.py --camp 划名 --json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 只在当脚本跑时改 stdout —— 被测试 import 时改它会把这个 wrapper 关掉，
# 调用方的 print 立刻 "I/O operation on closed file"（2026-11-16 实测）。
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PLAN_REL = Path("data") / "setting" / "entity_plan"
TASK_REL = Path("data") / "state" / "tasks" / "self" / "entities"
SANDBOX_REL = Path("data") / "sandbox" / "entity_drafts"  # 项目内沙盒：不碰 vault

KIND_NEW, KIND_FILL, KIND_SKIP = "新建", "补充", "不建"


def _norm(name):
    from utils.kb_index import normalize_term
    try:
        return normalize_term(name)
    except Exception:                                          # noqa: BLE001
        return str(name or "").strip().lower()


# ---------------------------------------------------------------- 读成书提炼
def resolve_camp(camp, root="."):
    """接受目录或名字：`划名` → `data/book_extract/划名`。"""
    p = Path(camp)
    if p.is_dir():
        return p
    q = Path(root) / "data" / "book_extract" / str(camp)
    return q


def load_cards(camp):
    """读 book_extract 的角色卡与设定卡。返回 `{characters: [...], settings: [...]}`。"""
    camp = Path(camp)
    out = {"characters": [], "settings": []}
    for fname, key in (("characters.json", "characters"), ("settings.json", "settings")):
        p = camp / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        out[key] = [c for c in (data or []) if isinstance(c, dict)]
    return out


def _card_name(card, category):
    if category == "character":
        return str(card.get("名字") or card.get("name") or "").strip()
    return str(card.get("设定名") or card.get("name") or "").strip()


def _card_evidence(card, category):
    if category == "character":
        return [str(x) for x in (card.get("出场章节") or []) if str(x).strip()]
    return [str(x) for x in ((card.get("确立章节") or []) + (card.get("补充章节") or []))
            if str(x).strip()]


def _card_facts(card, category):
    """卡片里"值得写进词条"的事实条数 —— 用来区分“补充”和“不建”。"""
    # 注意：`类别` 不算事实 —— "水站是个地点" 这句话对词条没有增量。
    # 这条判据决定“补充 / 不建”，错了会让清单变长而没用（作者还得自己筛一遍）。
    if category == "character":
        keys = ("身份", "性格特质", "当前关系", "当前状态", "未决线")
    else:
        keys = ("内容", "补充章节")
    n = 0
    for k in keys:
        v = card.get(k)
        if isinstance(v, list):
            n += len([x for x in v if str(x).strip()])
        elif str(v or "").strip():
            n += 1
    return n


# ---------------------------------------------------------------- 对照 vault
def existing_entries(root="."):
    """库里已有的词条名 → 路径列表（多库扫描；未配置 vault 时返回空 + 标记）。"""
    try:
        import obsidian_bridge
        data = obsidian_bridge.scan_vault_multi()
    except Exception as e:                                     # noqa: BLE001
        return {}, {"configured": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    index = {}
    for section in ("characters", "world", "timeline"):
        for entry in data.get(section) or []:
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            index.setdefault(_norm(name), []).append({
                "name": name, "path": entry.get("path") or "",
                "section": section, "locked": bool(entry.get("locked")),
            })
    meta = data.get("meta") or {}
    return index, {"configured": True, "entries": sum(len(v) for v in index.values()),
                   "vault": meta.get("vault_path") or meta.get("vaults") or "",
                   "conflicts": meta.get("conflicts") or []}


def plan(cards, index):
    """三分清单。返回 rows：[{kind, name, category, evidence, facts, existing, why}]。"""
    rows = []
    seen = {}
    for category, key in (("character", "characters"), ("setting", "settings")):
        for card in cards.get(key) or []:
            name = _card_name(card, category)
            if not name:
                continue
            norm = _norm(name)
            if norm in seen:
                rows[seen[norm]]["why"] += "；成书提炼里同名出现两次（合并前先看是不是同一实体）"
                continue
            seen[norm] = len(rows)
            have = index.get(norm) or []
            facts = _card_facts(card, category)
            row = {"name": name, "category": category, "facts": facts,
                   "evidence": _card_evidence(card, category),
                   "existing": have}
            if not have:
                row["kind"] = KIND_NEW
                row["why"] = "库里没有同名条目"
            elif len(have) > 1:
                row["kind"] = KIND_FILL if facts else KIND_SKIP
                row["why"] = "⚠ 库里有 %d 个同名条目（%s）—— 先合并，否则链接解析有歧义" % (
                    len(have), "、".join(h.get("path") or "?" for h in have))
            elif facts:
                row["kind"] = KIND_FILL
                row["why"] = "已有条目，成书提炼带来 %d 条事实可补" % facts
            else:
                row["kind"] = KIND_SKIP
                row["why"] = "已有条目且成书提炼没有新事实"
            rows.append(row)
    order = {KIND_NEW: 0, KIND_FILL: 1, KIND_SKIP: 2}
    return sorted(rows, key=lambda r: (order.get(r["kind"], 9), r["category"], r["name"]))


def summary(rows):
    return {k: len([r for r in rows if r["kind"] == k])
            for k in (KIND_NEW, KIND_FILL, KIND_SKIP)}


# ---------------------------------------------------------------- 输出
def render_md(rows, meta):
    lines = ["# 成稿 → 词条清单", "",
             "> 生成于 %s。**这份清单只决定“建不建”，不写词条**；"
             "词条草稿落 `%s/`，vault 本体一个字不动。"
             % (time.strftime("%Y-%m-%d %H:%M"), SANDBOX_REL.as_posix()), ""]
    s = summary(rows)
    lines += ["- 新建 %d · 补充 %d · 不建 %d" % (s[KIND_NEW], s[KIND_FILL], s[KIND_SKIP])]
    if not meta.get("vault", {}).get("configured"):
        lines += ["- ⚠ 没有配置 Obsidian 库（或扫描失败）：**全部按“新建”处理**，"
                  "补充/不建这两种判断这次不成立。%s"
                  % (meta.get("vault", {}).get("error") or "")]
    lines.append("")
    for kind in (KIND_NEW, KIND_FILL, KIND_SKIP):
        group = [r for r in rows if r["kind"] == kind]
        if not group:
            continue
        lines += ["## %s（%d）" % (kind, len(group)), "",
                  "| 名称 | 类别 | 成书里的出处 | 库里已有 | 说明 |", "|---|---|---|---|---|"]
        for r in group:
            ev = "、".join(r["evidence"][:4]) or "（卡片没写章节）"
            have = "、".join(h.get("name") or "?" for h in (r["existing"] or [])) or "—"
            lines.append("| %s | %s | %s | %s | %s |"
                         % (r["name"], "人物" if r["category"] == "character" else "设定",
                            ev, have, r["why"]))
        lines.append("")
    conflicts = [r for r in rows if "同名条目" in r["why"]]
    if conflicts:
        lines += ["## ⚠ 重名冲突（必须先解决）", ""]
        for r in conflicts:
            lines.append("- **%s**：%s" % (r["name"], r["why"]))
        lines.append("")
    lines += ["> 下一步：看这份清单 → 逐条点“要/不要”（新建那批可以直接派）→ "
              "`--tasks` 出沙盒词条任务书 → 外部模型按模板写 → 你贴进库。", ""]
    return "\n".join(lines)


def emit_tasks(rows, root=".", run_stamp=None):
    """给“新建 / 补充”出沙盒词条任务书（零 token，不调模型）。

    任务书写清四件事：写到哪个沙盒文件、用哪份模板、从哪几条章节取材料、
    以及**禁止写 vault 本体**（回执 §三：vault 本体只读是硬边界）。
    """
    stamp = run_stamp or time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(root) / TASK_REL
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for r in rows:
        if r["kind"] == KIND_SKIP:
            continue
        slug = "".join(ch for ch in r["name"] if ch not in '\\/:*?"<>|').strip() or "未命名"
        target = Path(root) / SANDBOX_REL / ("%s_%s_%s.md" % (
            "角色" if r["category"] == "character" else "设定", slug, stamp))
        tpl = ("prompts/obsidian_role_page.md" if r["category"] == "character"
               else "prompts/obsidian_book_page.md")
        body = [
            "# 词条草稿任务：%s（%s）" % (r["name"], r["kind"]),
            "",
            "> `engine: self` 风格的派单：墨坊**没有调用任何模型**。",
            "> 你把下面的要求交给任意模型，产物写到第 3 节指定路径。",
            "",
            "## 一、要写什么",
            "",
            "- 词条名：**%s**" % r["name"],
            "- 类别：%s" % ("人物" if r["category"] == "character" else "设定"),
            "- 为什么建：%s" % r["why"],
            "- 成书里的出处：%s" % ("、".join(r["evidence"]) or "（卡片没写章节，按全书检索）"),
            "",
            "## 二、怎么写",
            "",
            "1. 先读模板 `%s`（它规定了 frontmatter 与段落结构）；" % tpl,
            "2. 再从上面那几章正文里取材料 —— **每一条陈述都要能在正文里找到**，",
            "   找不到的一律不写（这是词条，不是设定创作）；",
            "3. 与库里已有条目同名时（%s），只补缺的字段，不要把已有的改掉。"
            % ("、".join(h.get("name") or "?" for h in (r["existing"] or [])) or "—"),
            "",
            "## 三、写到哪",
            "",
            "```",
            target.as_posix(),
            "```",
            "",
            "**禁止**写 vault 本体（`obsidian.vault_path` 之下的任何路径）—— 沙盒是唯一可写位置，",
            "入库由作者手工粘贴（回执 §三：vault 本体只读是硬边界）。",
            "",
            "## 四、写完怎么算完成",
            "",
            "- frontmatter 齐全（name / tags / publish: false）；",
            "- 每条事实后面带章节出处；",
            "- 给这个文件跑一遍 `python scripts/agent_receipt.py --emit <run_id> --outputs %s`，"
            % target.as_posix(),
            "  再把回执交给主控复检（R6：自报不算数，脚本实测才算）。",
            "",
        ]
        p = out_dir / ("entity_%s_%s.md" % (slug, stamp))
        p.write_text("\n".join(body), encoding="utf-8")
        made.append({"name": r["name"], "kind": r["kind"], "task": p.as_posix(),
                     "target": target.as_posix()})
    return made


def build(camp, root=".", want_tasks=False, index=None, vault_meta=None):
    """主入口：返回 `{rows, meta, plan_path, tasks}`。

    `index` / `vault_meta` 可注入（测试与"库里已有条目"已由调用方算好时用）——
    否则每次都要扫一遍 vault，而扫描依赖全局配置，与 `--root` 无关。
    """
    camp_path = resolve_camp(camp, root)
    if not camp_path.is_dir():
        raise FileNotFoundError(
            "没有这份成书提炼产物：%s。先跑 "
            "`python scripts/book_extract.py run <书> ` 或 `init` + `run`。" % camp_path.as_posix())
    cards = load_cards(camp_path)
    if not cards["characters"] and not cards["settings"]:
        raise FileNotFoundError(
            "%s 里没有 characters.json / settings.json —— 成书提炼还没跑到角色/设定那一步。"
            % camp_path.as_posix())
    if index is None:
        index, vmeta = existing_entries(root)
    else:
        vmeta = vault_meta or {"configured": True, "entries": sum(len(v) for v in index.values())}
    rows = plan(cards, index)
    meta = {"camp": camp_path.as_posix(), "vault": vmeta,
            "counts": summary(rows), "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    out = Path(root) / PLAN_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    md_path = out.with_suffix(".md")
    json_path = out.with_suffix(".json")
    from utils.file_io import write_text
    write_text(md_path, render_md(rows, meta))
    write_text(json_path, json.dumps(dict(meta, rows=rows), ensure_ascii=False, indent=2))
    tasks = emit_tasks(rows, root) if want_tasks else []
    return {"rows": rows, "meta": meta, "plan_path": md_path.as_posix(),
            "json_path": json_path.as_posix(), "tasks": tasks}


def main():
    ap = argparse.ArgumentParser(description="成稿 → 词条三分清单（R7，零 token）")
    ap.add_argument("--camp", required=True, help="成书提炼目录或名字（data/book_extract/<名字>）")
    ap.add_argument("--tasks", action="store_true", help="顺带出沙盒词条任务书")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()
    try:
        res = build(args.camp, ".", args.tasks)
    except FileNotFoundError as e:
        print(str(e))
        return 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    m = res["meta"]
    print("=" * 66)
    print("成稿 → 词条清单：新建 %d · 补充 %d · 不建 %d"
          % (m["counts"][KIND_NEW], m["counts"][KIND_FILL], m["counts"][KIND_SKIP]))
    if not m["vault"].get("configured"):
        print("⚠ 没配置 Obsidian 库：全部按“新建”处理（%s）"
              % (m["vault"].get("error") or ""))
    for kind in (KIND_NEW, KIND_FILL, KIND_SKIP):
        group = [r for r in res["rows"] if r["kind"] == kind]
        if not group:
            continue
        print("-" * 66)
        print("%s（%d）" % (kind, len(group)))
        for r in group[:20]:
            print("  %-12s %s" % (r["name"], r["why"][:60]))
        if len(group) > 20:
            print("  ……另有 %d 条，见清单文件" % (len(group) - 20))
    print("-" * 66)
    print("清单：%s" % res["plan_path"])
    if res["tasks"]:
        print("沙盒任务书：%d 份 → %s/（没有写 vault）"
              % (len(res["tasks"]), TASK_REL.as_posix()))
        for t in res["tasks"][:10]:
            print("  %s → %s" % (t["name"], t["target"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
