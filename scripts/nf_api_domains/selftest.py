# -*- coding: utf-8 -*-
"""自检域：回答“这东西现在到底能不能跑出一章”。

## 为什么有这个文件（2026-11-24，作者原话）

> “我不可能去看 data/state/progress.json 和 logs/runs.db 是否真出现 chapter_log 行，
>  也不会去看，那是你应该测的东西。”

所以“测”的结论必须出现在界面上，而不是留在两个我才会打开的文件里。本域三个端点：

| 端点 | 花不花钱 | 作用 |
|---|---|---|
| `GET /selftest/preflight` | 零 | 跑之前把 5 件事一次说清：许可 / 引擎 / DSH 可执行 / 书与素材 / 前置阶段 |
| `POST /selftest/chapter` | **真跑 1 次** | 试写第一章（唯一会消耗额度的自检入口，仅 GUI） |
| `GET /selftest/receipt` | 零 | 读盘出验收收据：正文汉字数 / 耗时 / token / chapter_log 有没有落行 |

## 纪律

1. 前两个端点区分得很死：preflight 与 receipt **不构造任何模型客户端**。
2. `POST /selftest/chapter` 会花掉一次调用，**只允许可信 GUI** —— 外部 Agent
   不得替作者开始花钱（与 `/config/engine` 同一条红线）。
3. 路径一律走 `api.ROOT`（`--root` 场景不许读错项目）；日志库只读打开。
"""
import json
import sqlite3
import subprocess
import time
from pathlib import Path

import nf_api as api

STAGE_NAMES = {1: "素材归并", 2: "整体大纲", 3: "分章大纲"}


# ---------------------------------------------------------------- 小工具

def _read_progress():
    p = api.ROOT / "data" / "state" / "progress.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _stage_state(prog, n):
    st = ((prog or {}).get("stages") or {}).get(str(n)) or {}
    return str(st.get("status") or "pending")


def _raw_dir():
    return api.ROOT / "data" / "chapters" / "raw"


def _find_next_chapter(total, prog):
    """第一个还没写的章号；全书都齐了返回 None。

    判据取“盘上文件存在”与“进度标记”的**并集**：任何一边说写过，就不算没写。
    试跑不该覆盖已有产物 —— 要覆盖得走“重写本章”（那条路会先备份）。
    """
    marked = set()
    st4 = ((prog or {}).get("stages") or {}).get("4") or {}
    for x in (st4.get("completed_chapters") or []):
        try:
            marked.add(int(x))
        except (TypeError, ValueError):
            continue
    d = _raw_dir()
    for n in range(1, int(total or 0) + 1):
        if n in marked:
            continue
        if (d / ("%02d.md" % n)).exists():
            continue
        return n
    return None


def _hanzi(text):
    from utils.word_count import count_cn_words
    try:
        return int(count_cn_words(text))
    except Exception:                                        # noqa: BLE001
        return len(text)


def _run_db():
    p = api.ROOT / "logs" / "runs.db"
    if not p.exists():
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % p.as_posix(), uri=True, timeout=3)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------- preflight

def _dsh_probe(cfg):
    """DSH 启动器：能不能找到 + 能不能起。返回 (ok, level, detail)。"""
    from utils.dsh_client import dsh_available, resolve_dsh_bin
    dcfg = (cfg or {}).get("dsh") or {}
    ok, msg = dsh_available(dcfg.get("bin") or None)
    if not ok:
        return False, "block", msg
    bin_path = resolve_dsh_bin(dcfg.get("bin") or None)
    # 找到不等于能跑：用 --version 起一次（零 token、不读工作区）。
    # 起不来**不阻塞**：有些版本不认 --version，误判成“不能跑”比不查更糟。
    try:
        r = subprocess.run([bin_path, "--version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        first = out[0][:120] if out else ""
        if r.returncode == 0:
            return True, "info", (msg + ("（" + first + "）" if first else ""))
        return True, "warn", (msg + "；--version 返回 %d，先按可用处理" % r.returncode)
    except subprocess.TimeoutExpired:
        return True, "warn", msg + "；--version 20 秒未返回（可能只是它不支持这个参数）"
    except OSError as e:
        return False, "block", msg + "；启动失败：" + str(e)[:120]


def build_preflight(cfg=None):
    """体检清单。`can_run` = 所有 block 级项目都通过。"""
    if cfg is None:
        try:
            cfg = api.load_all()[0]
        except Exception:                                    # noqa: BLE001
            cfg = {}
    prog = _read_progress()
    items = []

    # ① 许可协议：这条不过，任何模型调用都被 make_client 直接拒（31 个调用点）
    try:
        from utils.license import state as lic_state
        ls = lic_state()
        acked = bool(ls.get("acked"))
        detail = "已确认"
        if ls.get("stale"):
            detail = "条款改过版，需要重新确认"
        elif not acked:
            detail = "还没确认 —— 客户端启动时会弹窗，确认后才能开始"
        elif ls.get("ack"):
            detail = "已确认（%s）" % (ls["ack"].get("at") or "")
        items.append({"key": "license", "label": "许可协议", "ok": acked and not ls.get("stale"),
                      "level": "block", "detail": detail})
    except Exception as e:                                   # noqa: BLE001
        items.append({"key": "license", "label": "许可协议", "ok": False,
                      "level": "block", "detail": "读不到状态：" + str(e)[:120]})

    # ② 引擎
    try:
        from utils.engine import resolve_engine
        eng = resolve_engine(cfg)
    except Exception:                                        # noqa: BLE001
        eng = "direct"
    if eng == "dsh":
        items.append({"key": "engine", "label": "运行引擎", "ok": True,
                      "level": "block", "detail": "dsh（DeepSeek Harness 后台执行）"})
    else:
        items.append({"key": "engine", "label": "运行引擎", "ok": False, "level": "block",
                      "detail": "当前是 %s —— 本项目只支持 DSH；去“设置 → 引擎与模型”切回 DSH" % eng})

    # ③ DSH 启动器
    ok_dsh, lvl, d = _dsh_probe(cfg)
    items.append({"key": "dsh", "label": "DSH 启动器", "ok": ok_dsh, "level": lvl, "detail": d})

    # ④ 配置档：**headless 才是能跑的**（2026-11-24 实测）。desktop 被 DSH 拒绝
    #    命令行调用，填它等于跑不起来 —— 这条必须红，不能给绿。
    prof = str(((cfg or {}).get("dsh") or {}).get("profile") or "headless")
    items.append({"key": "profile", "label": "DSH 配置档",
                  "ok": prof != "desktop", "level": "block" if prof == "desktop" else "info",
                  "detail": ("headless（provider 已从桌面端搬过来，走你那三家渠道）"
                             if prof != "desktop" else
                             "desktop —— DSH 拒绝命令行调用（managed exclusively by the "
                             "Electron application），改成 headless 才能跑")})

    # ⑤ 书与素材
    book = ((cfg or {}).get("book") or {})
    name = str(book.get("name") or "").strip()
    total = int(book.get("chapters") or 0)
    if not name or name == "示例书名（待填写）" or total <= 0:
        items.append({"key": "book", "label": "书名与章数", "ok": False, "level": "block",
                      "detail": "还没填 —— 启动时的初始化向导里填书名和章数"})
    else:
        items.append({"key": "book", "label": "书名与章数", "ok": True, "level": "block",
                      "detail": "%s · %d 章" % (name, total)})
    raw_mat = api.ROOT / "materials" / "raw"
    n_mat = 0
    if raw_mat.is_dir():
        try:
            n_mat = sum(1 for f in raw_mat.iterdir() if f.is_file())
        except OSError:
            n_mat = 0
    items.append({"key": "materials", "label": "原始素材", "ok": n_mat > 0, "level": "warn",
                  "detail": ("materials/raw 有 %d 个文件" % n_mat) if n_mat
                            else "materials/raw 是空的 —— 阶段 1 会无米下锅"})

    # ⑥ 前置阶段：试写第 N 章要 1/2/3 都过了
    missing = [n for n in (1, 2, 3)
               if _stage_state(prog, n) not in ("done",)]
    if missing:
        items.append({"key": "prereq", "label": "前置阶段（1-3）", "ok": False, "level": "block",
                      "detail": "阶段 %s 还没跑完 —— 试写之前先把它们跑完"
                                % "、".join(str(n) for n in missing)})
    else:
        items.append({"key": "prereq", "label": "前置阶段（1-3）", "ok": True,
                      "level": "block", "detail": "素材 / 整体大纲 / 分章大纲 都已完成"})

    nxt = _find_next_chapter(total, prog) if total else None
    can_run = all(it["ok"] for it in items if it["level"] == "block")
    if missing:
        hint = "先跑完阶段 1-3，再回来试写。"
    elif total <= 0:
        # 别把“没填章数”说成“全书都写完了”：界面上两者都是“点了没反应”，
        # 但原因和下一步完全不同（2026-11-24，e2e 截图里真看到过这句错话）。
        hint = "先把书名和章数填上（项目页或启动时的初始化向导）。"
    elif nxt is None:
        hint = "全书都写完了，没有可试写的章；要重写某一章请去“章节”页。"
    elif not can_run:
        hint = "上面标红的项先解决，否则点了也会失败。"
    else:
        hint = "可以直接试写第 %d 章（一次调用）。" % nxt
    return {"ok": True, "engine": eng, "can_run": can_run,
            "next_chapter": nxt, "items": items, "hint": hint}


def handle_selftest_preflight(h):
    return 200, build_preflight()


# ---------------------------------------------------------------- 试跑一章

def handle_selftest_chapter(h, body):
    """试写一章。只允许 GUI —— 这是唯一会花额度的自检入口。"""
    from utils.gui_guard import gui_only
    guard = gui_only(h, "试跑一章（会消耗一次模型调用）")
    if guard:
        return guard
    body = body or {}
    pf = build_preflight()
    if not pf["can_run"]:
        bad = [it["label"] + "：" + it["detail"] for it in pf["items"]
               if it["level"] == "block" and not it["ok"]]
        return 400, {"ok": False, "error": "还不能试跑", "reasons": bad,
                     "hint": pf["hint"], "preflight": pf}
    try:
        n = int(body.get("chapter") or pf.get("next_chapter") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return 400, {"ok": False, "error": "全书都写完了，没有可试写的章。要重写某一章请去“章节”页。"}
    try:
        cfg = api.load_all()[0]
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": "读配置失败：" + str(e)[:160]}

    client = api._client_or_none_for_self(cfg, "writer")

    def _fn():
        # only_stage=4 + chapters="N" —— 与界面“章范围”走同一条路，不另造一条。
        # NF_SKIP_POST_STAGE=1：**必须**。否则 stage4 一完成就会跑
        # `review_after_stage4`（审稿，reviewer 逐章调模型），自检从“1 次调用”
        # 变成“1 + N 次”，与按钮上的承诺不符，也不该让一次自检烧掉那么多额度。
        import os
        prev = os.environ.get("NF_SKIP_POST_STAGE")
        os.environ["NF_SKIP_POST_STAGE"] = "1"
        try:
            return api._rc_to_result(api.orch_run(from_stage=4, only_stage=4,
                                                  client=client, chapters=str(n)))
        finally:
            if prev is None:
                os.environ.pop("NF_SKIP_POST_STAGE", None)
            else:
                os.environ["NF_SKIP_POST_STAGE"] = prev

    job_id, err = api.start_job("selftest", _fn)
    if err:
        return 409, {"ok": False, "error": err}
    return 202, {"ok": True, "job_id": job_id, "chapter": n,
                 "note": "正在试写第 %d 章。跑完回来自动出验收收据。" % n}


# ---------------------------------------------------------------- 收据

def build_receipt(chapter=None):
    """从盘上与日志库读事实，回答“刚才那一跑到底成了没有”。"""
    con = _run_db()
    run = None
    rows = []
    dsh_calls = 0
    if con is not None:
        try:
            r = con.execute("SELECT id, started_at, finished_at, status FROM runs "
                            "ORDER BY id DESC LIMIT 1").fetchone()
            if r:
                run = {"id": r["id"], "started_at": r["started_at"],
                       "finished_at": r["finished_at"], "status": r["status"]}
                rows = [dict(x) for x in con.execute(
                    "SELECT chapter, status, quality, tokens_in, tokens_out, cost_yuan, error "
                    "FROM chapter_log WHERE run_id=? AND stage=4 ORDER BY chapter",
                    (run["id"],)).fetchall()]
                dsh_calls = int(con.execute(
                    "SELECT COUNT(*) FROM cost_log WHERE run_id=? AND model LIKE '%dsh%'",
                    (run["id"],)).fetchone()[0])
        except sqlite3.Error:
            pass
        finally:
            con.close()

    if chapter is None:
        cand = [r.get("chapter") for r in rows if r.get("chapter")]
        chapter = cand[-1] if cand else None

    file_info = None
    ch_n = None
    try:
        ch_n = int(chapter) if chapter else None
    except (TypeError, ValueError):
        ch_n = None
    if ch_n:
        p = _raw_dir() / ("%02d.md" % ch_n)
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            title = (text.lstrip().splitlines() or [""])[0][:60]
            file_info = {"chapter": ch_n, "path": p.relative_to(api.ROOT).as_posix(),
                         "hanzi": _hanzi(text), "bytes": p.stat().st_size,
                         "mtime": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                time.localtime(p.stat().st_mtime)),
                         "title": title}
        else:
            file_info = {"chapter": ch_n, "exists": False}

    duration = None
    if run and run.get("started_at") and run.get("finished_at"):
        try:
            from datetime import datetime
            fmt = "%Y-%m-%dT%H:%M:%S"
            duration = int((datetime.strptime(run["finished_at"], fmt)
                            - datetime.strptime(run["started_at"], fmt)).total_seconds())
        except (TypeError, ValueError):
            duration = None

    ok_rows = [r for r in rows if str(r.get("status")) == "ok"]
    if not rows:
        verdict = {"level": "none",
                   "text": "这一轮没有章节记账（chapter_log 还没有 stage4 的行）—— 说明模型调用没走到落盘那一步。"}
    elif ok_rows and file_info and file_info.get("hanzi"):
        verdict = {"level": "good",
                   "text": "跑通了：第 %d 章 %d 汉字，%d 次调用%s。"
                           % (ok_rows[-1]["chapter"], file_info["hanzi"], len(rows),
                              ("，耗时 %d 分 %d 秒" % (duration // 60, duration % 60))
                              if duration else "")}
    elif ok_rows:
        verdict = {"level": "bad",
                   "text": "记账说有章完成，但盘上找不到对应正文 —— 这是安静失败，请把这条发给维护者。"}
    else:
        err = (rows[-1].get("error") or "").strip() if rows else ""
        verdict = {"level": "bad",
                   "text": "这一章没成：%s" % (err[:200] or "模型调用失败（没有 error 文本，看运行日志）")}

    return {"ok": True, "run": run, "duration_sec": duration,
            "chapters": rows, "dsh_calls": dsh_calls,
            "file": file_info, "verdict": verdict}


def handle_selftest_receipt(h):
    qs = (getattr(h, "path", "") or "").split("?", 1)
    q = api.parse_qs(qs[1]) if len(qs) > 1 else {}
    ch = (q.get("chapter") or [None])[0]
    return 200, build_receipt(ch)
