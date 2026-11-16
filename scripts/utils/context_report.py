# -*- coding: utf-8 -*-
"""上下文占用快照 + 单章上下文包（零 token，只读盘）。

## 为什么要有它（试跑第 23 条）

作者原话：“实际 token 用量由于未找到用量查询入口无法获知，根据经验估测为 1 亿左右。”
成本页此前只有金额（`engine: hermes` 下还恒 0），token 计数藏在 `runs.db` 里没人看得见；
而“这一章写的时候到底喂了多少上下文”更是只有日志里一行 `[stage4] 第N章记忆：...`。

两件事都要能**在客户端里看见**，且数字必须来自实测/盘上事实：

- `snapshot()`：本 run 的 token 与磁盘上的上下文来源（设定集 / 大纲 / 滚动摘要 /
  任务书）各有几个字 —— 给成本页的“用量”视图；
- `chapter_pack()`：某一章写作时会注入什么（L2 滚动章数、L3 召回卡数、开着的伏笔、
  字数预算用了多少）—— 给审阅台的“上下文包”块。

## 边界（不许假装精确）

- `snapshot` 里的字数是**盘上原文**，不是实际注入量：写作时 L2/L3 按 `context.memory`
  的预算裁剪，固定层还要按章筛。所以文案写“盘上有多少”，不写“注入了多少”。
- `chapter_pack` 直接复用 `utils.memory_policy.build_memory()` 的 stats ——
  与真正写作时同一份实现，不另算一套。
- 任何一项缺失（没设定集 / 没滚动摘要 / 没章大纲）都返回 0 并给 `hint`，不抛异常，
  也不编数字。
"""
import json
import sys
from pathlib import Path

# 允许 `python scripts/utils/context_report.py` 直跑：此时 sys.path[0] 是 scripts/utils/，
# 而不是 scripts/ —— 不补这一行就会 ModuleNotFoundError: utils（排查很久的那种）。
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.file_io import read_text

# 写作上下文的“固定层”来源（与 stage4 注入的那几块对齐）
SOURCE_FILES = (
    ("设定集", "data/setting/setting.json"),
    ("整体大纲", "data/outline/global.md"),
    ("滚动摘要", "data/summaries/rolling.md"),
    ("一致性账本", "data/state/story_state.json"),
)


def _chars(p):
    try:
        if p.is_file():
            return len(read_text(p) or "")
    except Exception:                                          # noqa: BLE001
        return 0
    return 0


def _task_files(root):
    """任务书（data/state/tasks/）体积：hermes 子会话真正携带的上下文。"""
    base = root / "data" / "state" / "tasks"
    n, size = 0, 0
    if base.is_dir():
        for p in base.rglob("*.md"):
            try:
                size += p.stat().st_size
                n += 1
            except OSError:
                continue
    return n, size


def snapshot(root, cfg=None):
    """上下文来源快照：{sources:[{name, path, chars, exists}], task_book:{files,bytes}, memory_budget:{...}}"""
    root = Path(root)
    sources = []
    for name, rel in SOURCE_FILES:
        p = root / rel
        sources.append({"name": name, "path": rel, "chars": _chars(p),
                        "exists": p.is_file()})
    # 逐章大纲（阶段 3 的产物）：总数与单章平均，不是一次注入量
    ch_dir = root / "data" / "outline" / "chapters"
    ch_n, ch_chars = 0, 0
    if ch_dir.is_dir():
        for p in sorted(ch_dir.glob("*.md")):
            c = _chars(p)
            if c:
                ch_n += 1
                ch_chars += c
    if ch_n:
        sources.append({"name": "逐章大纲", "path": "data/outline/chapters/",
                        "chars": ch_chars, "exists": True,
                        "note": "%d 章 · 单章均 %d 字" % (ch_n, ch_chars // ch_n)})
    files, size = _task_files(root)
    budget = {}
    try:
        from utils.memory_policy import _budget
        budget = _budget(cfg)
    except Exception:                                          # noqa: BLE001
        budget = {}
    return {
        "sources": sources,
        "total_chars": sum(s["chars"] for s in sources),
        "task_book": {"files": files, "bytes": size},
        "memory_budget": budget,
        "note": ("上面是**盘上原文**字数（不是一次请求的注入量）：写作时 L2/L3 按 "
                 "context.memory 的预算裁剪，固定层还要按本章大纲筛。"
                 "单章实际注入见“上下文包”。"),
    }


def _chapter_outline(root, n):
    """第 n 章的章大纲文本（找不到就空串，不猜）。"""
    d = root / "data" / "outline" / "chapters"
    if d.is_dir():
        for pat in ("%02d.md", "%d.md", "chapter_%02d.md", "chapter_%d.md"):
            p = d / (pat % n)
            if p.is_file():
                try:
                    return read_text(p) or ""
                except Exception:                              # noqa: BLE001
                    return ""
    return ""


def open_foreshadows(root, latest_chapter=None, up_to=None):
    """开着的伏笔条数（复用 utils.foreshadow 的台账，不另算）。

    `up_to` 给了章号就只数**该章之前**埋下的（写第 5 章时，第 10 章才埋的钩子
    对作者是剧透、对模型是噪声，不能算进“本章开着的伏笔”）。
    """
    try:
        from utils import foreshadow as fs
        # 显式传绝对路径：默认值是进程 CWD 的相对路径，`--root` 下会读错项目。
        data = fs.collect(
            outline_dir=str(Path(root) / "data" / "outline" / "chapters"),
            latest_chapter=latest_chapter,
            status_path=str(Path(root) / "data" / "state" / "foreshadow_status.json"))
        items = data.get("items") or []
        opened = [i for i in items if i.get("status") == "open"]
        if up_to is not None:
            opened = [i for i in opened if int(i.get("planted") or 0) <= int(up_to)]
        stale = [i for i in opened if i.get("stale")]
        return {"total": len(items), "open": len(opened), "stale": len(stale),
                "items": opened[:8],
                "note": "；".join(data.get("notes") or [])}
    except Exception as e:                                     # noqa: BLE001
        return {"total": 0, "open": 0, "stale": 0, "items": [],
                "error": type(e).__name__ + ": " + str(e)[:160]}


def chapter_pack(root, n, cfg=None):
    """第 n 章写作时的上下文包：L2 滚动 + L3 四维召回 + 开伏笔 + 字数预算。"""
    root = Path(root)
    outline_text = _chapter_outline(root, n)
    pack = {
        "chapter": n,
        "outline_chars": len(outline_text),
        "block": "",
        "stats": {},
        "foreshadow": {},
        "hint": "",
    }
    try:
        from utils.memory_policy import build_memory
        mem = build_memory(
            n, outline_text,
            str(root / "data" / "setting" / "setting.json"),
            str(root / "data" / "summaries" / "rolling.md"),
            str(root / "data" / "state" / "story_state.json"),
            cfg)
        pack["block"] = mem.get("block") or ""
        pack["stats"] = mem.get("stats") or {}
        pack["recalled"] = mem.get("recall") or []
    except Exception as e:                                     # noqa: BLE001
        pack["hint"] = "记忆策略不可用：" + type(e).__name__ + ": " + str(e)[:160]
    pack["foreshadow"] = open_foreshadows(root, latest_chapter=n, up_to=n)
    if not outline_text:
        pack["hint"] = (pack["hint"] + "；" if pack["hint"] else "") + \
            "没有第 %d 章的章大纲（data/outline/chapters/），召回只能按空文本算。" % n
    stats = pack.get("stats") or {}
    recall = stats.get("recall") or {}
    pack["summary"] = {
        "recalled": recall.get("recalled", 0),
        "candidates": recall.get("candidates", 0),
        "dropped": recall.get("dropped", 0),
        "by_dim": recall.get("by_dim", {}),
        "rolling_chapters": len((stats.get("rolling") or {}).get("chapters") or []),
        "rolling_omitted": len((stats.get("rolling") or {}).get("omitted") or []),
        "chars": stats.get("chars", 0),
        "budget": stats.get("budget", {}),
        "allocated": {
            "rolling": (stats.get("rolling") or {}).get("chars", 0),
            "recall": recall.get("chars", 0),
        },
        "open_foreshadows": pack["foreshadow"].get("open", 0),
        "stale_foreshadows": pack["foreshadow"].get("stale", 0),
    }
    return pack


def book_pack(root, cfg=None):
    """整本视角的上下文包（C4，2026-11-16）：讨论/整本判断需要的那一份全景。

    与 `chapter_pack` 的分工：那一份答"写第 N 章时会喂什么"，这一份答
    "这本书现在整体是什么状态"—— 设定多少条、大纲铺了几章、判定记录多少、
    开着的伏笔几条、最近章节摘要、口径/轮次、以及成本快照。

    **全是盘上事实，零 token**。缺什么就报缺什么（`missing` / `hints`），
    不编数字。为什么它不是写作提示词的一部分：整本视角会显著拉长提示词，
    要进写作必须先过缓存核算（`docs/agents/prompt-prefix.md`）。
    """
    root = Path(root)
    pack = {"ok": True, "book": {}, "setting": {}, "outline": {}, "judge": {},
            "foreshadow": {}, "rolling": {}, "discuss": {}, "glossary": {},
            "rounds": {}, "cost": {}, "hints": []}

    # ---- 项目配置：书名 / 章数 ----
    try:
        from utils.config_io import load_config_yaml
        proj = load_config_yaml(root / "config" / "project.yaml", default={}) or {}
        book = proj.get("book") or {}
        pack["book"] = {"name": book.get("name") or "",
                        "chapters": int(book.get("chapters") or 0) or None,
                        "words_target": book.get("words_target") or None}
    except Exception as e:                                     # noqa: BLE001
        pack["hints"].append("project.yaml 读不到：%s" % str(e)[:80])
    try:
        tw = ((cfg or {}).get("chapter") or {}).get("target_words")
        if tw:
            pack["book"]["target_words"] = list(tw)[:2]
    except Exception:                                          # noqa: BLE001
        pass

    # ---- 设定集：四类条目 + locked ----
    sp = root / "data" / "setting" / "setting.json"
    try:
        data = json.loads(read_text(sp)) if sp.is_file() else {}
        world = data.get("world") or {}
        locked = 0
        for item in (data.get("characters") or []):
            if isinstance(item, dict) and item.get("locked"):
                locked += 1
        for slot in (world.values() if isinstance(world, dict) else []):
            for line in (slot or []):
                if isinstance(line, str) and "locked" in line.lower():
                    locked += 1
        pack["setting"] = {
            "file": "data/setting/setting.json", "chars": _chars(sp),
            "characters": len(data.get("characters") or []),
            "world": sum(len(v or []) for v in (world.values() if isinstance(world, dict) else [])),
            "timeline": len(data.get("timeline") or []),
            "plot_fragments": len(data.get("plot_fragments") or []),
            "locked": locked,
        }
    except Exception as e:                                     # noqa: BLE001
        pack["setting"] = {"file": "data/setting/setting.json", "error": str(e)[:120]}
        pack["hints"].append("设定集读不到（先跑阶段 1）")

    # ---- 大纲：全局 + 逐章文件 ----
    gp = root / "data" / "outline" / "global.md"
    ch_dir = root / "data" / "outline" / "chapters"
    chapters = []
    if ch_dir.is_dir():
        chapters = sorted(int(p.stem) for p in ch_dir.glob("*.md") if p.stem.isdigit())
    total = pack["book"].get("chapters") or 0
    missing = [n for n in range(1, total + 1) if n not in chapters] if total else []
    pack["outline"] = {"global_chars": _chars(gp), "chapter_files": len(chapters),
                       "chapters": chapters[:60], "missing": missing[:60]}
    if not gp.is_file():
        pack["hints"].append("没有整体大纲（data/outline/global.md）")

    # ---- 判定记录（零 token 引擎的产物）----
    jp = root / "data" / "judge" / "records.json"
    try:
        recs = json.loads(read_text(jp)) if jp.is_file() else []
        if isinstance(recs, dict):
            recs = recs.get("records") or recs.get("items") or []
        verdicts = {}
        for r in (recs if isinstance(recs, list) else []):
            v = str((r or {}).get("verdict") or (r or {}).get("tier") or "")
            if v:
                verdicts[v] = verdicts.get(v, 0) + 1
        pack["judge"] = {"records": len(recs) if isinstance(recs, list) else 0,
                         "verdicts": verdicts,
                         "file": "data/judge/records.json" if jp.is_file() else ""}
    except Exception as e:                                     # noqa: BLE001
        pack["judge"] = {"records": 0, "error": str(e)[:120]}

    # ---- 伏笔（全书口径）----
    pack["foreshadow"] = open_foreshadows(root)

    # ---- 滚动摘要 ----
    rp = root / "data" / "summaries" / "rolling.md"
    try:
        from utils.summary_chain import load_rolling
        roll = load_rolling(rp) if rp.is_file() else {}
        chs = sorted(int(k) for k in (roll.get("chapters") or {}).keys()) \
            if isinstance(roll.get("chapters"), dict) else []
        pack["rolling"] = {"file": "data/summaries/rolling.md", "chars": _chars(rp),
                           "chapters": chs[-12:], "covered": len(chs),
                           "global_summary_chars": len(str(roll.get("global_summary") or ""))}
    except Exception as e:                                     # noqa: BLE001
        pack["rolling"] = {"chars": _chars(rp), "error": str(e)[:120]}

    # ---- 讨论 / 口径表 / 轮次（2026-11 起的三样新数据）----
    try:
        from utils import discussion
        threads = discussion.list_threads(None, str(root))
        pack["discuss"] = {"threads": len(threads),
                           "decided": len([t for t in threads if t.get("status") == "decided"]),
                           "open": len([t for t in threads if t.get("status") != "decided"])}
    except Exception:                                          # noqa: BLE001
        pass
    try:
        from utils import glossary as gl
        g = gl.load(str(root))
        pack["glossary"] = dict(gl.summary(g, str(root)))
    except Exception:                                          # noqa: BLE001
        pass
    try:
        from utils import rounds as rd
        rd_data = rd.load(str(root))
        pack["rounds"] = {"current": (rd.current(str(root)) or {}).get("name"),
                          "names": [r.get("name") for r in rd_data.get("rounds") or []]}
    except Exception:                                          # noqa: BLE001
        pass

    # ---- 成本快照（runs.db 只读；口径与成本页一致）----
    dbp = root / "logs" / "runs.db"
    if dbp.is_file():
        try:
            from utils.db import RunDB
            db = RunDB(str(dbp))
            try:
                rows = db.conn.execute(
                    "SELECT stage, COUNT(*), SUM(tokens_in), SUM(tokens_out),"
                    " SUM(cache_read), SUM(cost_yuan) FROM cost_log GROUP BY stage"
                ).fetchall()
            finally:
                db.close()
            by_stage, tin, tout, tcache, yuan = {}, 0, 0, 0, 0.0
            for stage, calls, i, o, c, y in rows:
                by_stage[str(stage)] = {"calls": int(calls or 0),
                                        "tokens_in": int(i or 0), "tokens_out": int(o or 0)}
                tin += int(i or 0)
                tout += int(o or 0)
                tcache += int(c or 0)
                yuan += float(y or 0.0)
            pack["cost"] = {"file": "logs/runs.db",
                            "calls": sum(v["calls"] for v in by_stage.values()),
                            "tokens_in": tin, "tokens_out": tout, "cache_read": tcache,
                            "yuan": round(yuan, 4), "by_stage": by_stage,
                            "note": "历史累计（不是本轮）；engine=hermes 下 yuan 恒 0 属正常"}
        except Exception as e:                                 # noqa: BLE001
            pack["cost"] = {"error": str(e)[:120]}
    else:
        pack["cost"] = {"calls": 0, "note": "还没有 logs/runs.db（没跑过带模型的阶段）"}

    pack["hint"] = ("整本视角只给讨论/判断用，**不进写作提示词**（会显著拉长前缀）。"
                    + ("；".join(pack["hints"]) if pack["hints"] else ""))
    return pack


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="上下文占用快照 / 单章上下文包")
    ap.add_argument("--root", default=".")
    ap.add_argument("--chapter", type=int, default=0,
                    help="给出章号则输出该章的上下文包（否则只打快照）")
    ap.add_argument("--book", action="store_true",
                    help="整本视角（设定/大纲/判定/伏笔/摘要/口径/轮次/成本），零 token")
    a = ap.parse_args(argv)
    cfg = None
    try:
        # 配置一律走 utils.config_io（裸 yaml.safe_load 会让 test_config_io 判红：
        # 全仓只允许一个配置入口，否则默认值/校验/注释保护会分叉）。
        from utils.config_io import load_config_yaml
        p = Path(a.root) / "config" / "system.yaml"
        if p.is_file():
            cfg = load_config_yaml(str(p)) or {}
    except Exception:                                          # noqa: BLE001
        cfg = None
    if getattr(a, "book", False):
        print(json.dumps(book_pack(a.root, cfg), ensure_ascii=False, indent=2))
        return 0
    if a.chapter:
        pack = chapter_pack(a.root, a.chapter, cfg)
        pack.pop("block", None)
        print(json.dumps(pack, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(snapshot(a.root, cfg), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
