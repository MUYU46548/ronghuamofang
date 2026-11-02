# -*- coding: utf-8 -*-
"""判定引擎域（0.7.17）：回测 / 报告 / 单章缺陷单 / 事件勾选 / 逐句裁定 / 口径。

## 端点

| 端点 | 作用 | 花钱 |
|---|---|---|
| `GET /judge/report` | 最近一次回测的总账（不重跑、不写盘） | 否 |
| `POST /judge/run` | 全量回测（只读 raw）→ 写 records.json | 否 |
| `GET /judge/chapter?n=` | 单章：缺陷单 + 原文 + 档位 + 三稿对照 | 否 |
| `GET /judge/events?chapter=` | 作者勾选记录 | 否 |
| `POST /judge/events` | 作者勾“这章发生了几件事”（**唯一硬门，GUI-only**） | 否 |
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
        # B2：整本否掉是**作者判断**，与有没有跑过回测无关 —— 两个分支都要带，
        # 否则“没跑过回测就否掉整本”时界面看不到横幅（第一版就这么漏了）。
        _author = doc.get("author") or {}
        _br = {"book_reject": _author.get("book_reject"),
               "book_reject_history": _author.get("book_reject_history") or []}
        if not run:
            return 200, {"ok": True, "exists": False, "caliber": cal_info,
                         "notices": [B.NOTICE_BACKTEST, B.NOTICE_NO_ARTIFACT],
                         "message": "还没有回测记录 —— 点“重跑判定”生成第一份",
                         **_br}
        return 200, {
            "ok": True, "exists": True, "run": run, "caliber": cal_info,
            "notices": [B.NOTICE_BACKTEST, B.NOTICE_NO_ARTIFACT],
            "evidence": run.get("evidence") or {},
            "object_changed": run.get("object_changed") or [],
            "author_events": (doc.get("author") or {}).get("events") or {},
            # B2：整本否掉的记录（含历史）。报告页据此显示横幅 + 撤销按钮。
            "book_reject": (doc.get("author") or {}).get("book_reject"),
            "book_reject_history":
                (doc.get("author") or {}).get("book_reject_history") or [],
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
        _a_checked = set((author or {}).get("checked") or [])
        _a_rejected = set((author or {}).get("rejected") or [])
        _a_cls = (author or {}).get("classes") or {}
        _a_ev = (author or {}).get("evidences") or {}
        ev_rows = []
        for i, ev in enumerate(events):
            ev_rows.append({"n": i + 1, "text": ev,
                            "checked": bool(i in _a_checked),
                            "rejected": bool(i in _a_rejected),
                            "class": _a_cls.get(str(i)),
                            "evidence": _a_ev.get(str(i)) or "",
                            # 计入下限 = 勾了 + 分了类 + 有证据句（R24 AC-24-2）
                            "counted": bool(i in _a_checked and _a_cls.get(str(i))
                                            and _a_ev.get(str(i)))})
        return 200, {
            "ok": True, "chapter": n, "verdict": verdict, "author": author,
            "defects": analysis["defects"], "counts": analysis["counts"],
            "metrics": analysis["metrics"], "outline_events": ev_rows,
            "event_hint": RU.similar_events_hint(events),
            "event_min": int(vals.get("J12", 2)),
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
    """作者勾选记录。`?chapter=n` 单章；`?chapter=n&outline=1` 附带**章纲事件**（给分章页用）。

    ## 为什么要 `outline=1`

    分章页（`ChapterBlueprint`）只想知道“这章章纲有哪几条事件、我勾了没”，
    不该为此跑一遍完整分析（`/judge/chapter` 会算三稿对照、骨架命中、缺陷单）。
    所以这里给一条**轻读**：解析章纲 + 读作者记录，零分析。
    """
    try:
        q = _query(h)
        ch = (q.get("chapter") or [None])[0]
        want_outline = str((q.get("outline") or ["0"])[0]).strip() in ("1", "true", "yes")
        if ch:
            n = int(ch)
            payload = {"ok": True, "chapter": n,
                       "events": R.get_events(api.ROOT, n)}
            if want_outline:
                outline_p = api.ROOT / B.OUTLINE_DIR / ("%02d.md" % n)
                text = outline_p.read_text(encoding="utf-8") if outline_p.exists() \
                    else ""
                evs = RU.outline_events(text)
                author = payload["events"] or {}
                checked = set(author.get("checked") or [])
                cls = author.get("classes") or {}
                evd = author.get("evidences") or {}
                rejected = set(author.get("rejected") or [])
                payload["outline_events"] = [
                    {"n": i + 1, "text": e, "checked": i in checked,
                     # “明确判定不算一件事”（R24 AC-24-5 的"4 条全部有归属"）
                     "rejected": i in rejected,
                     "class": cls.get(str(i)), "evidence": evd.get(str(i)) or "",
                     "counted": bool(i in checked and cls.get(str(i))
                                     and evd.get(str(i)))}
                    for i, e in enumerate(evs)]
                payload["judged"] = len(set(checked) | rejected)
                payload["total_events"] = len(evs)
                payload["event_hint"] = RU.similar_events_hint(evs)
                payload["event_min"] = int(
                    C.values(C.load(api.ROOT)).get("J12", 2))
            return 200, payload
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
                          (body or {}).get("evidence") or "",
                          (body or {}).get("evidences") or {},
                          (body or {}).get("rejected") or [],
                          (body or {}).get("total"))
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
        raw_text = raw_p.read_text(encoding="utf-8")
        chk_text = cp.read_text(encoding="utf-8") if cp.exists() else None
        ref_text = rp.read_text(encoding="utf-8") if rp.exists() else None
        d = D.compare(raw_text, chk_text, ref_text, C.values(cal) if cal else {})
        out = {"ok": True, "chapter": n, "destroy": d}
        # I6 三栏对照（2026-10-31）：只有 `text=1` 才回正文全文。
        # 默认不带：一页三份 6000 字正文 ≈ 18000 字符，而绝大多数调用只想知道
        # "删了多少字"（`shrink` 那几个数）。要正文的界面才付这份体积。
        if str((q.get("text") or ["0"])[0]).lower() in ("1", "true", "yes"):
            out["texts"] = {"raw": raw_text, "checked": chk_text, "refined": ref_text}
        return 200, out
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_caliber_preview(h, body):
    """R06：改口径前的影响面预览（受影响章数 / 预计转不合格 / 需重录条数）。"""
    try:
        changes = (body or {}).get("set") or {}
        return 200, B.preview(api.ROOT, changes)
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_artifact(h):
    """R07：成品 docx 与源稿一致性（字数 + 时间两项凭据）。"""
    try:
        from utils.judge import artifact as A
        return 200, dict({"ok": True}, **A.status(api.ROOT))
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_book_reject_set(h, body):
    """B2：作者否掉整本（`{reason}`）。理由必填 —— 这是要留档的决定。

    GUI-only 的同类：它和事件勾选、逐句裁定一样是**作者的判断**，
    外部 Agent 可以读（report 里带），不可以代替签字。
    """
    try:
        ok, payload = R.set_book_reject(api.ROOT, (body or {}).get("reason"))
        if not ok:
            return 400, {"ok": False, "error": payload.get("error")}
        return 200, {"ok": True, "book_reject": payload,
                     "message": "已记下：整本否掉（理由已留档，可撤销）"}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_book_reject_clear(h, body):
    """撤销否掉（作者改主意）。旧记录进 history，不删除。"""
    try:
        ok, payload = R.clear_book_reject(api.ROOT)
        if not ok:
            return 400, {"ok": False, "error": payload.get("error")}
        return 200, {"ok": True, "cleared": payload, "message": "已撤销“整本否掉”"}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_book_archive(h, body):
    """B2 延伸（2026-10-31）：“整本否掉”之后的一键**归档** —— 不是一键清除。

    ## 为什么是归档而不是清理

    用户原话：“直接一键清理产物风险太高。归档之后是用户自己的事情，
    我们已经做到了保护措施。”——完全同意，而且这个仓库里早有同样的教训
    （方寸那次：把\"清 release 目录\"顺手改成搬出工作区，理由是\"顺手治本\"）。

    所以这里只做一件事：把当前工作区整本搬进 `data/books/<书名>/`
    （复用 `switch_book.archive`：先打 before-import 快照 → 搬 → 失败可回退），
    工作区变空。**不删任何东西**。想彻底删掉，是作者在“项目”页对某份归档
    自己点删除（那条路径还进系统回收站）。归档之后怎么办是人的决定。

    归档原因写进 `_meta.json` 的 `why`：优先取本次传入的理由，
    其次取 `records.json` 里“整本否掉”的那条理由 —— 半年后翻归档列表时，
    看得见“这本当初为什么被放进来”，而不是只有一个书名和日期。
    """
    try:
        body = body or {}
        # 归档会搬空工作区（含 config/ 与 materials/），必须显式确认。
        # 这不是"确认弹窗的形式主义"：漏了它，任何一次误点都会让工作区看着"全没了"。
        if not body.get("confirm"):
            return 400, {"ok": False,
                         "error": "归档会搬空当前工作区，需带 confirm=true"}
        import switch_book as sb
        name = str(body.get("name") or "").strip() or _book_name()
        if not name:
            return 400, {"ok": False,
                         "error": "无法确定书名（project.yaml 的 book.name 为空）"}
        why = str(body.get("reason") or "").strip()
        if not why:
            br = R.get_book_reject(api.ROOT) or {}
            why = str(br.get("reason") or "").strip()
        if not why:
            why = "作者在判定页一键归档"
        ok, msg = sb.archive(name, yes=True,
                             force=bool(body.get("force")), reason=why)
        rel = "data/books/" + sb.sanitize(name)
        if not ok:
            return 400, {"ok": False, "error": msg}
        return 200, {"ok": True, "name": name, "path": rel, "why": why,
                     "message": msg,
                     "next": "工作区已清空。这本书是“搬走”不是“删掉”——"
                             "在“项目”页可以恢复，或由你决定是否删除归档。"}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_judge_export(h, body):
    """导出缺陷单（显式动作）：重跑一次判定并落 output/judge_report.md。"""
    try:
        rep = B.run(api.ROOT, book_name=_book_name())
        if not rep.get("ok"):
            return 400, {"ok": False, "error": rep.get("error")}
        out = B.export_markdown(rep, api.ROOT)
        rel = str(out.relative_to(api.ROOT)).replace("\\", "/")
        return 200, {"ok": True, "path": rel, "run_id": rep.get("run_id")}
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
    ("POST", "/judge/caliber/preview", handle_judge_caliber_preview),
    ("GET", "/judge/destroy", handle_judge_destroy),
    ("GET", "/judge/artifact", handle_judge_artifact),
    ("POST", "/judge/export", handle_judge_export),
    ("POST", "/judge/book_reject", handle_book_reject_set),
    ("POST", "/judge/book_reject/clear", handle_book_reject_clear),
    ("POST", "/judge/book_archive", handle_book_archive),
]
