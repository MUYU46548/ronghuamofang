# -*- coding: utf-8 -*-
"""吃书编排层：拆章 → ② 摘要 → ③⑥⑧⑨ 聚合 → ① 一致性 → ⑦ 病灶 → 报告 / 并库。

## 为什么自己写一层（不照搬 campaign.py）

SP-1024 的 `campaign.py` 是**人机通道**：出提示词 → 人工贴到外部平台 → 回收文件入账
（因为沙盒零网络）。本仓有 `make_client()`，同一件事可以在进程内一次跑完，
照搬那套文件搬运只会多出「提示词文件、响应文件、命名规约、去重」四份负担。

## 保留了什么

- **分章正则**（`第X回/章/卷`）与 UTF-8 → GB18030 回退读法：老电子书常见的坑；
- **分批**（③⑧⑨ 一次吃 30 章摘要，别把整本塞进一个请求）；
- **断点**：每章摘要落盘即算完成，重跑跳过（`force=True` 才重做）——
  这是「报错自愈断点续跑」那条纪律在吃书上的落地；
- **只增不改**的并库语义（与 `obsidian_integrate.merge_setting_from_vault` 同一条）。

## 不做什么

- 不改写原文、不评价文笔（⑦ 只检测）；
- 不把「吃出来的卡」直接写进正典：`merge` 是显式动作，且带备份 + 回读。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.file_io import read_text, write_text

from . import (character_cards, cliche_scanner, consistency, foreshadow,
               summarizer, timeline, worldview)

DEFAULT_SPLIT_PATTERN = r"^\s*第[0-9０-９零一二两三四五六七八九十百千万]+\s*[回章节卷]"
EATBOOK_ROOT = "data/eatbook"
STAGES = (2, 3, 6, 8, 9, 1, 7)
STAGE_NAMES = {2: "章节摘要", 3: "角色状态卡", 6: "伏笔台账", 8: "时间线",
               9: "世界观设定", 1: "一致性检查", 7: "病灶词扫描"}


# ---------------------------------------------------------------- 拆章

def _read_book(path: Path) -> str:
    """读整本书：UTF-8 失败退 GB18030（老电子书常见编码）。"""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("gb18030", errors="replace")


def split_book(book_path: str | Path, name: str = "", pattern: str = "",
               max_chapters: int = 0, root: str | Path = EATBOOK_ROOT) -> Path:
    """按章回标题拆书，建 `data/eatbook/<name>/`；返回目录。

    - 至少找到 2 个标题才拆（只有 1 个多半是正则不对，硬拆等于把整本当一章）；
    - 同名目录已存在直接报错（不静默覆盖用户上一次的战役）。
    """
    src = Path(book_path)
    if not src.exists():
        raise FileNotFoundError("书稿不存在：%s" % src)
    text = _read_book(src)
    heading = re.compile(pattern or DEFAULT_SPLIT_PATTERN)
    lines = text.splitlines()
    heads = [i for i, line in enumerate(lines) if heading.match(line)]
    if len(heads) < 2:
        raise ValueError(
            "按默认正则只找到 %d 个章回标题（至少 2 个才拆）。"
            "请用 --pattern 指定本书的分章正则。" % len(heads))
    name = (name or src.stem).strip()
    camp = Path(root) / name
    if (camp / "manifest.json").exists():
        raise ValueError("吃书目录已存在：%s（换 --name，或先删掉）" % camp)
    if max_chapters:
        heads = heads[:max_chapters]
    (camp / "chapters").mkdir(parents=True, exist_ok=True)
    (camp / "summaries").mkdir(parents=True, exist_ok=True)
    chapters = []
    for idx, start in enumerate(heads):
        end = heads[idx + 1] if idx + 1 < len(heads) else len(lines)
        title = lines[start].strip()
        rel = "chapters/%04d.txt" % (idx + 1)
        write_text(camp / rel, "\n".join(lines[start:end]).strip() + "\n")
        chapters.append({"seq": idx + 1, "title": title, "file": rel,
                         "summary": "summaries/%04d.json" % (idx + 1),
                         "status": "pending"})
    write_text(camp / "book.txt", text)
    save_manifest(camp, {"name": name, "source": str(src.resolve()),
                         "pattern": pattern or DEFAULT_SPLIT_PATTERN,
                         "created": datetime.now().isoformat(timespec="seconds"),
                         "chapters": chapters})
    return camp


def load_manifest(camp: str | Path) -> Dict[str, Any]:
    p = Path(camp) / "manifest.json"
    if not p.exists():
        raise FileNotFoundError("不是吃书目录（缺 manifest.json）：%s" % camp)
    return json.loads(read_text(p))


def save_manifest(camp: str | Path, manifest: Dict[str, Any]) -> None:
    p = Path(camp) / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(manifest, ensure_ascii=False, indent=2))


def resolve_camp(name_or_path: str) -> Path:
    """`--name` 或直接给目录；两种都认。"""
    p = Path(name_or_path)
    if (p / "manifest.json").exists():
        return p
    return Path(EATBOOK_ROOT) / name_or_path


# ---------------------------------------------------------------- 工具

def _log(msg: str) -> None:
    print(msg)


def _summaries(camp: Path, manifest: Dict[str, Any], *, force: bool,
               call: Callable[[str], str], stats: Dict[str, Any],
               log=_log) -> Dict[str, Any]:
    """② 逐章摘要（断点：已有 JSON 就跳过）。返回 {"done": n, "failed": [(seq, err)]}。"""
    done, failed = 0, []
    for ch in manifest["chapters"]:
        out = camp / ch["summary"]
        if out.exists() and not force:
            done += 1
            continue
        text = read_text(camp / ch["file"])
        try:
            data = summarizer.parse_response(call(summarizer.build_prompt(text)))
        except Exception as e:                               # noqa: BLE001
            failed.append((ch["seq"], "%s: %s" % (type(e).__name__, e)))
            log("  [②] 第 %d 章摘要失败：%s" % (ch["seq"], e))
            continue
        write_text(out, json.dumps(data, ensure_ascii=False, indent=2))
        ch["status"] = "summary"
        done += 1
        log("  [②] 第 %d 章 %s" % (ch["seq"], ch.get("title", "")))
    save_manifest(camp, manifest)
    return {"done": done, "failed": failed}


def load_summaries(camp: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for ch in manifest["chapters"]:
        p = camp / ch["summary"]
        if p.exists():
            rows.append(json.loads(read_text(p)))
    return rows


def _batches(items: List[Any], size: int) -> List[List[Any]]:
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]


def merge_rows(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]],
               key: str = "名字") -> List[Dict[str, Any]]:
    """按 `key` 合并：非空字段覆盖、空字段保旧；未命中即新增（保持首次顺序）。"""
    by_key: Dict[str, Dict[str, Any]] = {}
    ordered: List[Dict[str, Any]] = []
    for row in existing:
        k = str(row.get(key) or "").strip()
        if k and k not in by_key:
            by_key[k] = row
            ordered.append(row)
    for row in incoming:
        k = str(row.get(key) or "").strip()
        if not k:
            continue
        cur = by_key.get(k)
        if cur is None:
            by_key[k] = dict(row)
            ordered.append(by_key[k])
            continue
        for field, value in row.items():
            if value not in ("", None, [], {}):
                cur[field] = value
    return ordered


def _aggregate(camp: Path, manifest: Dict[str, Any], *,
               call: Callable[[str], str], batch_size: int, log=_log) -> Dict[str, Any]:
    """③⑧⑨：摘要分批 → 角色卡 / 时间线 / 设定卡。"""
    summaries = load_summaries(camp, manifest)
    if not summaries:
        log("  [③⑧⑨] 没有摘要可聚合（先跑 ②）")
        return {"characters": 0, "timeline": 0, "settings": 0, "failed": []}
    chars: List[Dict[str, Any]] = []
    rows_tl: List[Dict[str, Any]] = []
    settings: List[Dict[str, Any]] = []
    failed: List[str] = []
    for i, batch in enumerate(_batches(summaries, batch_size), 1):
        log("  [③⑧⑨] 第 %d 批（%d 章摘要）" % (i, len(batch)))
        for mod, key, sink in ((character_cards, "名字", chars),
                               (worldview, "设定名", settings)):
            try:
                got = mod.parse_response(call(mod.build_prompt(batch)))
            except Exception as e:                            # noqa: BLE001
                failed.append("%s 第%d批: %s" % (mod.__name__.rsplit(".", 1)[-1], i, e))
                log("    %s 失败：%s" % (mod.__name__.rsplit(".", 1)[-1], e))
                continue
            if sink is chars:
                chars = merge_rows(chars, got, key)
            else:
                sink[:] = worldview.merge_cards(sink, got)
        try:
            got_tl = timeline.parse_response(call(timeline.build_prompt(batch)))
            seen = {(r.get("时间"), r.get("事件")) for r in rows_tl}
            for row in got_tl:
                if (row.get("时间"), row.get("事件")) not in seen:
                    rows_tl.append(row)
                    seen.add((row.get("时间"), row.get("事件")))
        except Exception as e:                                # noqa: BLE001
            failed.append("timeline 第%d批: %s" % (i, e))
            log("    timeline 失败：%s" % e)

    # 重排时间线顺序（模型每批都从 1 开始编号）
    for idx, row in enumerate(rows_tl, 1):
        row["顺序"] = idx
    write_text(camp / "characters.json",
               json.dumps(chars, ensure_ascii=False, indent=2))
    write_text(camp / "settings.json",
               json.dumps(settings, ensure_ascii=False, indent=2))
    write_text(camp / "timeline.json",
               json.dumps(rows_tl, ensure_ascii=False, indent=2))
    return {"characters": len(chars), "settings": len(settings),
            "timeline": len(rows_tl), "failed": failed}


def _foreshadow(camp: Path, manifest: Dict[str, Any], *,
                call: Callable[[str], str], log=_log) -> Dict[str, Any]:
    """⑥ 伏笔台账：用全部摘要建账（一次调用），落 `foreshadow.json`。"""
    summaries = load_summaries(camp, manifest)
    if not summaries:
        log("  [⑥] 没有摘要可登记（先跑 ②）")
        return {"rows": 0}
    try:
        rows = foreshadow.parse_response(
            call(foreshadow.build_register_prompt(
                json.dumps(summaries, ensure_ascii=False))))
    except Exception as e:                                    # noqa: BLE001
        log("  [⑥] 登记失败：%s" % e)
        return {"rows": 0, "error": str(e)}
    ledger = foreshadow.Ledger(rows)
    write_text(camp / "foreshadow.json", ledger.to_json())
    log("  [⑥] 台账 %d 条" % len(ledger.rows))
    return {"rows": len(ledger.rows)}


def _context_for_consistency(camp: Path, limit: int = 6000) -> str:
    """① 的 A 槽：角色卡 + 设定卡（按预算截断，只放结论不放全文）。"""
    lines: List[str] = []
    chars_p = camp / "characters.json"
    if chars_p.exists():
        for card in json.loads(read_text(chars_p)):
            lines.append("角色：%s（%s）" % (card.get("名字", ""), card.get("身份", "")))
            for rel in card.get("当前关系") or []:
                lines.append("  · 与%s：%s（%s）" % (rel.get("对象", ""),
                                                   rel.get("关系", ""),
                                                   rel.get("依据章节", "")))
    set_p = camp / "settings.json"
    if set_p.exists():
        for card in json.loads(read_text(set_p)):
            lines.append("设定：%s（%s）%s" % (card.get("设定名", ""),
                                              card.get("类别", ""),
                                              card.get("内容", "")))
    text = "\n".join(lines)
    return text[:limit]


def _consistency(camp: Path, manifest: Dict[str, Any], *,
                 call: Callable[[str], str], tail: int, log=_log) -> Dict[str, Any]:
    """① 一致性检查：默认只查后 1/3 章（与原版一致：前段还没铺开，查了也是噪音）。"""
    chapters = manifest["chapters"]
    if not chapters:
        return {"checked": 0, "conflicts": 0}
    tail = tail or max(1, len(chapters) // 3)
    targets = chapters[-tail:]
    ctx = _context_for_consistency(camp)
    if not ctx.strip():
        log("  [①] 没有角色/设定卡可挂（先跑 ③⑨）")
        return {"checked": 0, "conflicts": 0}
    out: Dict[str, Any] = {"checked": 0, "conflicts": 0, "by_chapter": {}}
    for ch in targets:
        body = read_text(camp / ch["file"])
        try:
            found = consistency.parse_response(
                call(consistency.build_prompt(ctx, body)))
        except Exception as e:                                # noqa: BLE001
            log("  [①] 第 %d 章检查失败：%s" % (ch["seq"], e))
            continue
        out["checked"] += 1
        out["conflicts"] += len(found)
        out["by_chapter"]["%04d" % ch["seq"]] = found
        log("  [①] 第 %d 章：%d 条冲突" % (ch["seq"], len(found)))
    write_text(camp / "conflicts.json",
               json.dumps(out, ensure_ascii=False, indent=2))
    return out


def _cliche(camp: Path, manifest: Dict[str, Any], *, wordlist_path: Optional[str],
            log=_log) -> Dict[str, Any]:
    """⑦ 病灶词扫描（纯本地、零 token）：全书 + 逐章。"""
    terms = cliche_scanner.load_wordlist(wordlist_path)
    book = read_text(camp / "book.txt")
    whole = cliche_scanner.scan(book, wordlist=terms)
    per_chapter = {}
    for ch in manifest["chapters"]:
        rep = cliche_scanner.scan(read_text(camp / ch["file"]), wordlist=terms)
        if rep["总命中次数"]:
            per_chapter["%04d" % ch["seq"]] = {
                "命中次数": rep["总命中次数"],
                "密度每千字": rep["平均密度每千字"],
                "排行": rep["排行"][:10],
            }
    payload = {"全书": whole, "逐章": per_chapter}
    write_text(camp / "cliche.json",
               json.dumps(payload, ensure_ascii=False, indent=2))
    write_text(camp / "cliche_report.md",
               cliche_scanner.format_report(whole))
    log("  [⑦] 全书命中 %d 次（%s/千字）"
        % (whole["总命中次数"], whole["平均密度每千字"]))
    return payload


def _report(camp: Path, manifest: Dict[str, Any], result: Dict[str, Any]) -> Path:
    """人可读汇总：每章状态 + 各表条数 + 冲突/病灶 Top。"""
    lines = ["# 吃书报告 · %s" % manifest.get("name", ""), "",
             "来源：`%s` ｜ 章数：%d" % (manifest.get("source", ""),
                                        len(manifest.get("chapters", []))), ""]
    for ch in manifest["chapters"]:
        mark = "✅" if (camp / ch["summary"]).exists() else "⬜"
        lines.append("- %s 第 %d 章 %s" % (mark, ch["seq"], ch.get("title", "")))
    lines.append("")
    lines.append("## 产出")
    for fname, label in (("characters.json", "角色卡"), ("settings.json", "设定卡"),
                         ("timeline.json", "时间线"), ("foreshadow.json", "伏笔台账"),
                         ("conflicts.json", "一致性冲突"), ("cliche.json", "病灶词")):
        p = camp / fname
        if p.exists():
            try:
                data = json.loads(read_text(p))
                n = len(data) if isinstance(data, list) else (
                    data.get("conflicts", 0) if isinstance(data, dict) else "-")
            except Exception:                                 # noqa: BLE001
                n = "?"
            lines.append("- %s：`%s`（%s）" % (label, fname, n))
    lines.append("")
    if result.get("failed"):
        lines.append("## 失败项（可重跑：已有摘要不会重做）")
        for item in result["failed"]:
            lines.append("- %s" % item)
        lines.append("")
    lines.append("## 并库")
    lines.append("`python scripts/eat_book.py merge <name> --yes` —— 只增不改，"
                 "写前备份到 `data/setting/history/`。")
    out = camp / "report.md"
    write_text(out, "\n".join(lines) + "\n")
    return out


def run(camp: str | Path, call: Callable[[str], str], *,
        stages: Optional[List[int]] = None, force: bool = False,
        batch_size: int = 30, consistency_tail: int = 0,
        wordlist_path: Optional[str] = None,
        stats: Optional[Dict[str, Any]] = None,
        log=_log) -> Dict[str, Any]:
    """跑整条吃书管线（默认 2→3→6→8→9→1→7）。返回汇总 dict。"""
    camp = Path(camp)
    manifest = load_manifest(camp)
    stages = list(stages or STAGES)
    result: Dict[str, Any] = {"name": manifest.get("name"), "failed": [], "stages": {}}

    if 2 in stages:
        log("[②] 章节摘要（%d 章）" % len(manifest["chapters"]))
        s = _summaries(camp, manifest, force=force, call=call,
                       stats=stats or {}, log=log)
        result["stages"]["2"] = s
        result["failed"] += ["② 第 %d 章：%s" % (seq, err) for seq, err in s["failed"]]

    if any(x in stages for x in (3, 8, 9)):
        log("[③⑧⑨] 聚合角色 / 时间线 / 设定")
        a = _aggregate(camp, manifest, call=call, batch_size=batch_size, log=log)
        result["stages"]["3/8/9"] = a
        result["failed"] += a.get("failed", [])

    if 6 in stages:
        log("[⑥] 伏笔台账")
        result["stages"]["6"] = _foreshadow(camp, manifest, call=call, log=log)

    if 1 in stages:
        log("[①] 一致性检查")
        result["stages"]["1"] = _consistency(camp, manifest, call=call,
                                             tail=consistency_tail, log=log)

    if 7 in stages:
        log("[⑦] 病灶词扫描（零 token）")
        result["stages"]["7"] = _cliche(camp, manifest,
                                        wordlist_path=wordlist_path, log=log)

    if stats is not None:
        write_text(camp / "cost.json",
                   json.dumps(stats, ensure_ascii=False, indent=2))
    result["report"] = str(_report(camp, manifest, result))
    return result


# ---------------------------------------------------------------- 并库

def _character_entry(card: Dict[str, Any], book: str, idx: int) -> Dict[str, Any]:
    relations = []
    for rel in card.get("当前关系") or []:
        target = str(rel.get("对象") or "").strip()
        if not target:
            continue
        relations.append("%s：%s" % (target, rel.get("关系") or ""))
    notes = []
    for field in ("当前状态", "未决线"):
        if card.get(field):
            notes.append(str(card[field]))
    for item in card.get("矛盾清单") or []:
        notes.append("矛盾：" + str(item))
    return {
        "id": "eat-%03d" % idx,
        "name": card.get("名字", ""),
        "role": card.get("身份", ""),
        "traits": list(card.get("性格特质") or []),
        "relations": relations,
        "records": notes,
        "source": "吃书:%s" % book,
    }


def _setting_lines(cards: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """设定卡 → world 四槽（只认名字，不猜语义；未知类别进 magic_system）。"""
    out = {"locations": [], "factions": [], "magic_system": [], "items": []}
    mapping = {"地理": "locations", "势力": "factions", "物品": "items"}
    for card in cards:
        name = str(card.get("设定名") or "").strip()
        if not name:
            continue
        line = "%s：%s" % (name, card.get("内容") or "")
        slot = mapping.get(str(card.get("类别") or ""), "magic_system")
        out[slot].append(line)
    return out


def merge_into_setting(camp: str | Path, setting_path: str | Path = "data/setting/setting.json",
                       *, dry_run: bool = True) -> Dict[str, Any]:
    """把吃出来的卡**只增不改**并进 setting.json。

    规则（与设定库增量合并同源）：
    - 角色按 `name` 去重：已存在的一律不动（用户/素材写的那份永远优先）；
    - world 四槽按「名字：」前缀去重，只追加不存在的行；
    - 伏笔进 `plot_fragments`（未回收的），时间线进 `timeline`；
    - 写前把旧 `setting.json` 备份到 `data/setting/history/`。
    """
    camp = Path(camp)
    sp = Path(setting_path)
    manifest = load_manifest(camp)
    book = manifest.get("name", "")
    setting: Dict[str, Any] = {"characters": [], "world": {},
                               "plot_fragments": [], "timeline": []}
    if sp.exists():
        setting = json.loads(read_text(sp))

    added_chars: List[str] = []
    existing_names = {str(c.get("name") or "").strip()
                      for c in setting.get("characters") or []}
    chars_p = camp / "characters.json"
    if chars_p.exists():
        for i, card in enumerate(json.loads(read_text(chars_p)), 1):
            name = str(card.get("名字") or "").strip()
            if not name or name in existing_names:
                continue
            setting.setdefault("characters", []).append(
                _character_entry(card, book, i))
            existing_names.add(name)
            added_chars.append(name)

    world = setting.get("world")
    if not isinstance(world, dict):
        world = {}
    for slot in ("locations", "factions", "magic_system", "items"):
        world.setdefault(slot, [])
    set_p = camp / "settings.json"
    added_world: List[str] = []
    if set_p.exists():
        for slot, lines in _setting_lines(json.loads(read_text(set_p))).items():
            have = {str(x).split("：", 1)[0] for x in world[slot]}
            for line in lines:
                name = line.split("：", 1)[0]
                if name and name not in have:
                    world[slot].append(line)
                    have.add(name)
                    added_world.append(name)
    setting["world"] = world

    added_frag: List[str] = []
    fs_p = camp / "foreshadow.json"
    if fs_p.exists():
        have = {str(f.get("summary") or "") for f in setting.get("plot_fragments") or []
                if isinstance(f, dict)}
        for row in json.loads(read_text(fs_p)):
            if str(row.get("状态") or "") == "已回收":
                continue
            summary = "伏笔：%s（埋设于 %s）" % (row.get("伏笔", ""),
                                              row.get("埋设位置", ""))
            if summary in have:
                continue
            setting.setdefault("plot_fragments", []).append({
                "id": "eat-f%03d" % (len(setting["plot_fragments"]) + 1),
                "source": "吃书:%s" % book, "summary": summary, "status": "unused"})
            have.add(summary)
            added_frag.append(row.get("伏笔", ""))

    added_tl = 0
    tl_p = camp / "timeline.json"
    if tl_p.exists():
        have = {str(t.get("event") or "") for t in setting.get("timeline") or []
                if isinstance(t, dict)}
        for row in json.loads(read_text(tl_p)):
            event = "%s%s（%s）" % (row.get("时间", ""), row.get("事件", ""),
                                   row.get("依据章节", ""))
            if event in have:
                continue
            setting.setdefault("timeline", []).append(
                {"event": event, "year": None, "locked": False})
            have.add(event)
            added_tl += 1

    report = {"dry_run": dry_run, "setting": str(sp),
              "added_characters": added_chars, "added_world": added_world,
              "added_fragments": added_frag, "added_timeline": added_tl}
    if dry_run:
        return report

    if sp.exists():
        hist = sp.parent / "history"
        hist.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        write_text(hist / ("setting_v%s.json" % stamp), read_text(sp))
    setting.setdefault("_meta", {})
    setting["_meta"]["eatbook"] = book
    sp.parent.mkdir(parents=True, exist_ok=True)
    write_text(sp, json.dumps(setting, ensure_ascii=False, indent=2))
    return report
