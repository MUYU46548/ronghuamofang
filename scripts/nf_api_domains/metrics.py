# -*- coding: utf-8 -*-
"""质量度量域：节级字数、分布型文风指标、口径表、轮次（R1/R2/R4/R5/R8，2026-11-16）。

这一组端点回答四个问题（都是 OB37 施工回执点名的结构性缺口）：

| 问题 | 端点 |
|---|---|
| 这一章分几节、每节多少字、哪一节空了 | `GET /chapters/sections` |
| 这一章的四个分布型指标是多少、过不过线 | `GET /chapters/metrics` |
| 把"连排对白/短对白/短段末"变成可直接改的批注 | `POST /chapters/metrics/annotate` |
| 术语口径（名册 32/44/47）与历法写法有没有冲突 | `GET/POST /glossary`、`POST /glossary/check` |
| 这一轮该验什么、达标没有 | `GET/POST /rounds`、`POST /rounds/evaluate` |

纪律：**全部零 token、只读正文**，不写 `data/chapters/` 以外的任何东西
（`/chapters/metrics/annotate` 写的是批注库 `data/state/annotations/`，本来就是它的地盘）。
"""
from __future__ import annotations


def _bad(msg, code=400):
    return code, {"ok": False, "error": str(msg)}


def _int(body, key, default=None):
    try:
        v = body.get(key) if isinstance(body, dict) else default
        return int(v) if v is not None and v != "" else default
    except (TypeError, ValueError):
        return default


def _chapter_body(api, n):
    from pathlib import Path
    for layer in ("refined", "checked", "raw"):
        p = Path(api.ROOT) / "data" / "chapters" / layer / ("%02d.md" % int(n))
        if p.exists():
            try:
                return p.read_text(encoding="utf-8"), layer, p
            except OSError:
                continue
    return "", "", None


def _outline_text(api, n):
    from pathlib import Path
    p = Path(api.ROOT) / "data" / "outline" / "chapters" / ("%02d.md" % int(n))
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        return ""


def _cfg(api):
    try:
        return api.load_all()[0] or {}
    except Exception:                                          # noqa: BLE001
        return {}


# ---------------------------------------------------------------- 节级（R1）
def handle_chapters_sections(h):
    """`GET /chapters/sections?n=6` —— 逐节字数与目标（零 token）。

    没有正文就 404 说清楚；有正文但大纲没声明节 → 200 + `declared:false`
    （**不是错误**：老工作区的大纲都长这样）。
    """
    import nf_api as api
    from utils import sections as sec

    try:
        n = int((h._query().get("n") or ["0"])[0])
    except (TypeError, ValueError):
        return _bad("n 必须是章号整数")
    if n < 1:
        return _bad("n 必须是章号整数")
    body, layer, _p = _chapter_body(api, n)
    if not body:
        return 404, {"ok": False, "error": "第 %d 章还没有正文（raw/checked/refined 都是空的）" % n}
    cfg = _cfg(api)
    rep = sec.measure_and_save(n, body, _outline_text(api, n), cfg, root=str(api.ROOT))
    # ⚠️ 不要在这里加 `ok=True` 覆盖报告自己的 ok：报告的 ok 是"这一章节级算不算完成"，
    # 覆盖掉以后界面上就会出现"判据说 ✗、标题写达标"（2026-11-16 真机验收抓到的）。
    return 200, dict(rep, layer=layer)


def handle_chapters_metrics(h):
    """`GET /chapters/metrics?n=6` —— 四个分布型指标 + 阈值判定（零 token）。"""
    import nf_api as api
    from utils import prose_metrics as pm

    try:
        n = int((h._query().get("n") or ["0"])[0])
    except (TypeError, ValueError):
        return _bad("n 必须是章号整数")
    if n < 1:
        return _bad("n 必须是章号整数")
    body, layer, _p = _chapter_body(api, n)
    if not body:
        return 404, {"ok": False, "error": "第 %d 章还没有正文" % n}
    cfg = _cfg(api)
    rep = pm.measure_and_save(n, body, cfg, root=str(api.ROOT))
    return 200, dict(rep, layer=layer,
                     summary=pm.summary_line(rep),
                     note="只报不改：要改就点“把命中项变成批注”，改哪一处由你定。")


def handle_chapters_metrics_annotate(h, body):
    """`POST /chapters/metrics/annotate {n, codes?, limit?}` —— 命中项变批注（R3）。

    这一步是"指标→定位→重写"闭环的前半段：批注带段号与原句，
    作者在段落精修里点"按批注改这一处"，走的是已有的候选机制（正文不动）。
    """
    import nf_api as api
    from utils import prose_metrics as pm

    body = body if isinstance(body, dict) else {}
    n = _int(body, "n")
    if not n or n < 1:
        return _bad("n 必须是章号整数")
    body_text, _layer, _p = _chapter_body(api, n)
    if not body_text:
        return 404, {"ok": False, "error": "第 %d 章还没有正文" % n}
    codes = body.get("codes")
    if isinstance(codes, str):
        codes = [c.strip() for c in codes.split(",") if c.strip()]
    try:
        made = pm.to_annotations(n, body_text, root=str(api.ROOT),
                                 codes=codes or None, limit=_int(body, "limit", 40) or 40)
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    return 200, {"ok": True, "chapter": n, "created": len(made), "annotations": made,
                 "message": ("已把 %d 处结构问题变成批注（正文没动）。"
                             "到“精修 → 批注”点一条，就能按它改这一段。" % len(made))}


# ---------------------------------------------------------------- 口径表（R4/R8）
def handle_glossary(h):
    """`GET /glossary` —— 读口径表（术语 + 历法）。"""
    import nf_api as api
    from utils import glossary as gl

    try:
        data = gl.load(str(api.ROOT))
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    return 200, {"ok": True, "glossary": data, "summary": gl.summary(data, str(api.ROOT)),
                 "note": "改一个数只改这里一处；locked = 作者已拍板，draft = 待定。"}


def handle_glossary_set(h, body):
    """`POST /glossary {terms?: [...], calendar?: {...}}` —— 写口径表（作者拍板）。"""
    import nf_api as api
    from utils import glossary as gl

    body = body if isinstance(body, dict) else {}
    try:
        data = gl.load(str(api.ROOT))
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    if isinstance(body.get("terms"), list):
        cleaned = []
        for t in body["terms"]:
            if not isinstance(t, dict) or not str(t.get("term") or "").strip():
                return _bad("terms 里每一条都要有 term")
            cleaned.append(t)
        data["terms"] = cleaned
    if isinstance(body.get("calendar"), dict):
        cal = dict(data.get("calendar") or {})
        cal.update({k: v for k, v in body["calendar"].items()
                    if k in ("era", "anchor", "style", "relative_forbidden", "prefer_absolute")})
        data["calendar"] = cal
    try:
        gl.save(data, str(api.ROOT))
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    return 200, {"ok": True, "glossary": data,
                 "summary": gl.summary(data, str(api.ROOT)),
                 "message": "口径表已更新（旧版备份在 data/setting/history/）"}


def handle_glossary_check(h, body=None):
    """`POST /glossary/check {n?}` —— 查一章或全书的口径冲突。

    不传 n = 全书扫描，额外给出**跨章**汇总：
    “第 8 章写 47，第 10 章写 32”这种话只有全书口径能给。
    """
    import nf_api as api
    from utils import glossary as gl

    body = body if isinstance(body, dict) else {}
    n = _int(body, "n")
    try:
        data = gl.load(str(api.ROOT))
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    if not data.get("terms") and not data.get("calendar"):
        return 200, {"ok": True, "empty": True, "findings": [], "count": 0,
                     "message": "还没有口径表 —— 先在 data/setting/glossary.yaml 里写下"
                                "“哪些数/哪些写法是定死的”，再查才有意义。"}
    try:
        if n:
            text, layer, _p = _chapter_body(api, n)
            if not text:
                return 404, {"ok": False, "error": "第 %d 章还没有正文" % n}
            rep = gl.check(text, n, data)
            return 200, dict(rep, ok=True, layer=layer)
        rep = gl.scan_book(None, str(api.ROOT), data)
        return 200, dict(rep, ok=True)
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 轮次（R5）
def handle_rounds(h):
    """`GET /rounds` —— 轮次表（一轮 = 一张判据表）+ 当前轮。"""
    import nf_api as api
    from utils import rounds as rd

    try:
        data = rd.load(str(api.ROOT))
        cur = rd.current(str(api.ROOT))
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    return 200, {"ok": True, "rounds": data.get("rounds") or [], "current": cur,
                 "seeded": data.get("seeded"),
                 "known_checks": list(rd.KNOWN_CHECKS),
                 "note": "切轮次 = 切判据表；每条判据都是“量出来的数 vs 阈值”。"}


def handle_rounds_set(h, body):
    """`POST /rounds {rounds?: [...], current?: "润色"}` —— 改判据表 / 切当前轮。"""
    import nf_api as api
    from utils import rounds as rd

    body = body if isinstance(body, dict) else {}
    try:
        if isinstance(body.get("rounds"), list):
            rd.save(body["rounds"], body.get("current"), str(api.ROOT))
        elif body.get("current"):
            rd.set_current(str(body["current"]), str(api.ROOT))
        else:
            return _bad("要么给 rounds（整张表），要么给 current（只切轮次）")
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    data = rd.load(str(api.ROOT))
    return 200, {"ok": True, "rounds": data.get("rounds") or [],
                 "current": rd.current(str(api.ROOT)),
                 "message": "轮次表已更新（旧版备份在 data/state/history/）"}


def handle_rounds_evaluate(h, body):
    """`POST /rounds/evaluate {n?, round?}` —— 这一轮达标了吗、差在哪几节。"""
    import nf_api as api
    from utils import rounds as rd

    body = body if isinstance(body, dict) else {}
    n = _int(body, "n")
    rnd = str(body.get("round") or "").strip() or None
    try:
        # 同 sections：`ok` 是判定结论本身，不许被响应包装覆盖
        if n:
            return 200, dict(rd.evaluate(n, rnd, str(api.ROOT)))
        return 200, dict(rd.evaluate_book(rnd, None, str(api.ROOT)))
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/chapters/sections", handle_chapters_sections),
    ("GET", "/chapters/metrics", handle_chapters_metrics),
    ("POST", "/chapters/metrics/annotate", handle_chapters_metrics_annotate),
    ("GET", "/glossary", handle_glossary),
    ("POST", "/glossary", handle_glossary_set),
    ("POST", "/glossary/check", handle_glossary_check),
    ("GET", "/rounds", handle_rounds),
    ("POST", "/rounds", handle_rounds_set),
    ("POST", "/rounds/evaluate", handle_rounds_evaluate),
)
