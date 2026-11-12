# -*- coding: utf-8 -*-
"""产物闸的 API：候选清单 + 作者批准 / 驳回。

## 端点

| 端点 | 作用 | 谁可调 |
|---|---|---|
| `GET /review/products/pending` | 已登记阶段的候选与状态 | 读侧不拦 |
| `GET /review/products/content` | 某一份候选的正文（只读、有上限） | 读侧不拦 |
| `POST /review/products/decide` | 作者批准或驳回某一版 | **仅 GUI**（403） |

## 为什么批准必须在界面

用户 2026-11-11 拍板：**界面决定、对话干活**。agent 可以写候选，
但"这一版算不算"只能由作者点。所以批准端点进 `agent_guard.FORBIDDEN_IN_AGENT_MODE`
—— 这是那条决定在代码里的物理落点，不是文案。

## 三条纪律

1. **只批登记表里的产物**（`product_gate.STAGE_PRODUCTS` / `STAGE_PRODUCT_GLOBS`）。
   不接受任意路径：否则一个越权的调用就能把任何文件"批准"成正式产物。
2. **看一眼也只给登记表里的候选**（同一个判据，不另开一个读文件的口子）。
3. 零 token：本域不构造模型客户端。
"""
import nf_api as api
from utils import product_gate as pg

#: 单次预览的字符上限。候选正文可能几十万字，一次全吐给前端没有意义
#: （界面上是个滚动框），也容易把 HTTP 响应撑爆。
CONTENT_MAX = 20000


def _query(h):
    qs = h.path.split("?", 1)[1] if "?" in h.path else ""
    return api.parse_qs(qs) if qs else {}


def _state_counts(rows):
    out = {"pending": 0, "stale": 0, "approved": 0, "rejected": 0}
    for r in rows:
        if r.get("state") in out:
            out[r["state"]] += 1
    return out


def handle_products_list(h):
    rows = []
    for stage in sorted(pg.REGISTERED_STAGES):
        # 目录型产物（阶段 4 的逐章正文）要现场展开 —— 清单不是常量
        for rel in pg.product_rels(api.ROOT, stage):
            st = pg.status(api.ROOT, stage, rel)
            if st["state"] == "no_pending":
                continue          # 没有候选就不占界面位置
            rows.append({
                "stage": stage,
                "label": pg.stage_label(stage),
                "rel": rel,
                "state": st["state"],
                "candidate": st["pending"],
                "sha256": st["sha"],
                "why": st["why"],
            })
    return 200, {"ok": True, "items": rows, "counts": _state_counts(rows)}


def handle_products_content(h):
    """候选正文（只读）。给"批准之前先看一眼"这条路用。

    为什么必须有：批准一份章纲或一章正文却看不到内容，那不叫作主 —— 只能靠猜。
    这里只读候选区，不碰正式路径。

    阶段 5 额外带一个**段落级改动条数**（复用 `stage5_gate.diff_items`，零 token）：
    作者最想知道的就是"检查稿把我哪几段改了"。
    """
    q = _query(h)
    try:
        stage = int((q.get("stage") or [""])[0])
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "stage 必填（整数）"}
    rel = str((q.get("rel") or [""])[0]).strip()
    if not rel:
        return 400, {"ok": False, "error": "rel 必填（产物相对路径）"}
    if rel not in pg.product_rels(api.ROOT, stage):
        # 与批准同一个判据：只认登记表里、候选区真有东西的那一份
        return 400, {"ok": False,
                     "error": "阶段 %d 的候选清单里没有 %s" % (stage, rel)}
    st = pg.status(api.ROOT, stage, rel)
    p = pg.pending_path(api.ROOT, stage, rel)
    if not p.is_file():
        return 404, {"ok": False, "error": "候选文件不见了：%s" % st.get("pending")}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return 500, {"ok": False, "error": "读不出来：%s" % e}
    out = {
        "ok": True,
        "stage": stage,
        "label": pg.stage_label(stage),
        "rel": rel,
        "candidate": st.get("pending") or "",
        "state": st.get("state") or "",
        "chars": len(text),
        "text": text[:CONTENT_MAX],
        "truncated": len(text) > CONTENT_MAX,
    }
    formal = api.ROOT / rel
    if formal.is_file():
        try:
            out["formal_chars"] = len(formal.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    if stage == 5:
        out["changed_paragraphs"] = _changed_paragraphs(api.ROOT, rel, text)
    return 200, out


def _changed_paragraphs(root, rel, candidate_text):
    """检查稿相对原稿改了**几段**（算不出来就返回 None，不编数字）。"""
    try:
        from stage5_gate import diff_items
        raw_p = root / "data" / "chapters" / "raw" / rel.rsplit("/", 1)[-1]
        if not raw_p.is_file():
            return None
        return len(diff_items(raw_p.read_text(encoding="utf-8", errors="replace"),
                              candidate_text))
    except Exception:                                        # noqa: BLE001
        return None


def handle_products_decide(h, body):
    body = body or {}
    try:
        stage = int(body.get("stage"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "stage 必填（整数）"}
    rel = str(body.get("rel") or "").strip()
    if not rel:
        return 400, {"ok": False, "error": "rel 必填（产物相对路径）"}
    if rel not in pg.product_rels(api.ROOT, stage):
        # 只批登记表里的产物 —— 不做任意路径批准
        return 400, {"ok": False,
                     "error": "阶段 %d 的产物清单里没有 %s" % (stage, rel)}
    action = str(body.get("action") or "").strip().lower()
    note = str(body.get("note") or "")
    if action in ("approve", "accept"):
        ok, info = pg.record_approval(api.ROOT, stage, rel, by="gui", note=note)
        if not ok:
            return 400, {"ok": False, "error": info.get("error") or "批准失败"}
        return 200, {"ok": True, "message": "已批准：下次运行时会换进你的稿子",
                     "stage": stage, "rel": rel, "sha256": info.get("sha256")}
    if action in ("reject", "refuse"):
        ok, info = pg.record_rejection(api.ROOT, stage, rel, note=note)
        if not ok:
            return 400, {"ok": False, "error": info.get("error") or "驳回失败"}
        return 200, {"ok": True, "message": "已驳回：你的稿子没动，这份还留着",
                     "stage": stage, "rel": rel}
    return 400, {"ok": False, "error": "action 只能是 approve / reject"}


ROUTES = [
    ("GET", "/review/products/pending", handle_products_list),
    ("GET", "/review/products/content", handle_products_content),
    ("POST", "/review/products/decide", handle_products_decide),
]
