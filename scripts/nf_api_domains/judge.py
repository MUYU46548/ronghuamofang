# -*- coding: utf-8 -*-
"""判定引擎域（0.7.17）：回测 / 报告 / 单章缺陷单 / 事件勾选 / 逐句裁定 / 口径。

## 端点

| 端点 | 作用 | 花钱 |
|---|---|---|
| `GET /judge/report` | 最近一次回测的总账（不重跑、不写盘） | 否 |
| `POST /judge/run` | 全量回测（只读 raw）→ 写 records.json | 否 |
| `GET /judge/chapter?n=` | 单章：缺陷单 + 原文 + 档位 + 三稿对照 | 否 |
| `GET /judge/events?chapter=` | 作者勾选记录 | 否 |
| `POST /judge/events` | 作者勾「这章发生了几件事」（**唯一硬门，GUI-only**） | 否 |
| `POST /judge/sentence` | 逐句裁定（打中了/没打中/拿不准 + 理由） | 否 |
| `GET /judge/caliber` | 口径登记表 + 版本 + 消费方 | 否 |
| `POST /judge/caliber` | 改口径（产新版本，旧版进 history） | 否 |
| `GET /judge/destroy?n=` | 三稿对照（哪一步把 6000 字削到 2000） | 否 |

## 三条纪律

1. **零 token**：本域不构造任何模型客户端；回测只读盘。
2. 路径一律 `api.ROOT / ...`（`--root` 场景不许读错项目）。
3. 写判定结果只走 `records.save()` 一个出口（F13）。
"""
import json

import nf_api as api
from utils.judge import backtest as B
from utils.judge import caliber as C
from utils.judge import destroy as D
from utils.judge import records as R
from utils.judge import rules as RU
from utils.judge import verdict as V


def _book_name():
    try:
        from utils.project_config import get_project_config
        cfg = get_project_config() or {}
        return ((cfg.get("book") or {}).get("name") or "").strip()
    except Exception:                                          # noqa: BLE001
        return ""


def _query(h):
    """查询串（与 nf_api 分发器同一份 parse_qs）。"""
    qs = h.path.split("?", 1)[1] if "?" in h.path else ""
    return api.parse_qs(qs)


def handle_judge_report(h):
    """最近一次回测的总账（GUI 首屏）。没跑过 → exists:false，不假装有结论。"""
    try:
        doc = R.load(api.ROOT)
        run = R.latest_run(doc)
        cal = C.load(api.ROOT)
        cal_info = {"version": (cal or {}).get("version"),
                    "fingerprint": (cal or {}).get("fingerprint"),
                    "updated_at": (cal or {}).get("updated_at"),
                    "calibrated": (cal or {}).get("calibrated", False)}
        if not run:
            return 200, {"ok": True, "exists": False, "caliber": cal_info,
                         "notices": [B.NOTICE_BACKTEST, B.NOTICE_NO_ARTIFACT],
                         "message": "还没有回测记录 —— 点「重跑判定」生成第一份"}
        return 200, {
            "ok": True, "exists": True, "run": run, "caliber": cal_info,
            "notices": [B.NOTICE_BACKTEST, B.NOTICE_NO_ARTIFACT],
            "author_events": (doc.get("author") or {}).get("events") or {},
            "stale": bool(run.get("caliber_fingerprint")
                          and cal_info["fingerprint"]
                          and run["caliber_fingerprint"] != cal_info["fingerprint"]),
        }
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_run(h, body):
    """全量回测（零 token）。跑完写 records.json 一个文件。"""
    try:
        rep = B.run(api.ROOT, book_name=_book_name())
        if not rep.get("ok"):
            return 400, {"ok": False, "error": rep.get("error")}
        return 200, rep
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_chapter(h):
    """单章详情：缺陷单（可定位）+ 原文分段 + 档位 + 三稿对照。"""
    try:
        q = _query(h)
        n = int((q.get("n") or ["0"])[0])
        root = api.ROOT
        raw_p = root / B.RAW_DIR / ("%02d.md" % n)
        if not raw_p.exists():
            return 404, {"ok": False, "error": "没有第 %d 章的 raw 稿（%s）" % (n, B.RAW_DIR)}
        body_text = raw_p.read_text(encoding="utf-8")
        outline_p = root / B.OUTLINE_DIR / ("%02d.md" % n)
        outline = outline_p.read_text(encoding="utf-8") if outline_p.exists() else ""
        cal_doc = C.load(root)
        ok, why = C.require_ready(cal_doc)
        if not ok:
            return 400, {"ok": False, "error": why}
        vals = C.values(cal_doc)
        analysis = RU.analyze_chapter(body_text, outline, n, vals)
        cp = root / B.CHECKED_DIR / ("%02d.md" % n)
        rp = root / B.REFINED_DIR / ("%02d.md" % n)
        d = D.compare(body_text,
                      cp.read_text(encoding="utf-8") if cp.exists() else None,
                      rp.read_text(encoding="utf-8") if rp.exists() else None, vals)
        author = R.get_events(root, n)
        verdict = V.decide(analysis, d, author, vals)
        sentences = R.sentence_verdicts(root, n)
        paras = [{"paragraph_index": i + 1, "text": t}
                 for i, t in enumerate(RU.T.paragraphs(body_text))]
        events = analysis["outline"]["events"]
        ev_rows = []
        for i, ev in enumerate(events):
            ev_rows.append({"n": i + 1, "text": ev,
                            "checked": bool(author and (i in (author.get("checked") or []))),
                            "class": ((author or {}).get("classes") or {}).get(str(i))})
        return 200, {
            "ok": True, "chapter": n, "verdict": verdict, "author": author,
            "defects": analysis["defects"], "counts": analysis["counts"],
            "metrics": analysis["metrics"], "outline_events": ev_rows,
            "density": analysis["outline"]["density"],
            "landing": analysis["outline"]["landing"],
            "destroy": d, "paragraphs": paras,
            "sentence_verdicts": sentences,
            "notices": [B.NOTICE_BACKTEST, B.NOTICE_NO_ARTIFACT],
            "caliber": {"version": cal_doc.get("version"),
                        "fingerprint": cal_doc.get("fingerprint")},
        }
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_events_get(h):
    try:
        q = _query(h)
        ch = (q.get("chapter") or [None])[0]
        if ch:
            return 200, {"ok": True, "chapter": int(ch),
                         "events": R.get_events(api.ROOT, int(ch))}
        return 200, {"ok": True,
                     "events": (R.load(api.ROOT).get("author") or {}).get("events") or {}}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_events_set(h, body):
    """作者勾选（F03b）：唯一硬门。GUI-only —— 外部 Agent 不得替作者数事件。"""
    try:
        ch = int((body or {}).get("chapter") or 0)
        if ch <= 0:
            return 400, {"ok": False, "error": "chapter 必填"}
        checked = (body or {}).get("checked") or []
        classes = (body or {}).get("classes") or {}
        ev = R.set_events(api.ROOT, ch, checked, classes,
                          (body or {}).get("evidence") or "")
        return 200, {"ok": True, "events": ev}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_sentence(h, body):
    try:
        ok, payload = R.set_sentence(
            api.ROOT, int((body or {}).get("chapter") or 0),
            int((body or {}).get("paragraph_index") or 0),
            (body or {}).get("sentence") or "", (body or {}).get("feeling") or "",
            (body or {}).get("reason") or "")
        if not ok:
            return 400, {"ok": False, "error": payload.get("error")}
        return 200, {"ok": True, "verdict": payload}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_caliber(h):
    try:
        doc = C.load(api.ROOT)
        if not doc:
            return 500, {"ok": False, "error": "口径文件读不出来"}
        return 200, {"ok": True, "version": doc.get("version"),
                     "fingerprint": doc.get("fingerprint"),
                     "updated_at": doc.get("updated_at"),
                     "calibrated": doc.get("calibrated", False),
                     "criteria": C.describe(doc),
                     "empty": C.empty_criteria(doc),
                     "history": [{"version": x.get("version"),
                                  "superseded_at": x.get("superseded_at")}
                                 for x in (doc.get("history") or [])]}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_caliber_set(h, body):
    try:
        changes = (body or {}).get("set") or {}
        if not isinstance(changes, dict) or not changes:
            return 400, {"ok": False, "error": "set 必须是 {判据id: 新值}"}
        ok, d = C.update(api.ROOT, changes, by=(body or {}).get("by") or "GUI")
        if not ok:
            return 400, {"ok": False, "error": d.get("error")}
        return 200, d
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_destroy(h):
    try:
        q = _query(h)
        n = int((q.get("n") or ["0"])[0])
        root = api.ROOT
        raw_p = root / B.RAW_DIR / ("%02d.md" % n)
        if not raw_p.exists():
            return 404, {"ok": False, "error": "没有第 %d 章的 raw 稿" % n}
        cp = root / B.CHECKED_DIR / ("%02d.md" % n)
        rp = root / B.REFINED_DIR / ("%02d.md" % n)
        cal = C.load(root)
        d = D.compare(raw_p.read_text(encoding="utf-8"),
                      cp.read_text(encoding="utf-8") if cp.exists() else None,
                      rp.read_text(encoding="utf-8") if rp.exists() else None,
                      C.values(cal) if cal else {})
        return 200, {"ok": True, "chapter": n, "destroy": d}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = [
    ("GET", "/judge/report", handle_judge_report),
    ("POST", "/judge/run", handle_judge_run),
    ("GET", "/judge/chapter", handle_judge_chapter),
    ("GET", "/judge/events", handle_judge_events_get),
    ("POST", "/judge/events", handle_judge_events_set),
    ("POST", "/judge/sentence", handle_judge_sentence),
    ("GET", "/judge/caliber", handle_judge_caliber),
    ("POST", "/judge/caliber", handle_judge_caliber_set),
    ("GET", "/judge/destroy", handle_judge_destroy),
]
