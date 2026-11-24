# -*- coding: utf-8 -*-
"""stage5 改稿裁定闸的 API（2026-11-04，N1=B）。

## 端点

| 端点 | 作用 | 谁可调 |
|---|---|---|
| `GET /review/stage5/list` | 待裁定清单（章 / 状态 / 已定项数） | 读侧不拦 |
| `GET /review/stage5/chapter?n=` | 单章裁定项（逐项 old/new/已定） | 读侧不拦 |
| `POST /review/stage5/decide` | 作者逐条勾"采用 / 不采用" | **仅 GUI**（403） |
| `POST /review/stage5/apply` | 合成 `checked/NN.md`（可 `all:true`） | **仅 GUI**（403） |

## 纪律

1. 零 token：本域不构造模型客户端。
2. 路径一律 `api.ROOT / ...`（`--root` 场景不许读错项目）。
3. 裁定与合成都是**作者动作**，已列入 `agent_guard.FORBIDDEN_IN_AGENT_MODE`。
"""
import nf_api as api
import stage5_gate as G


def _query(h):
    qs = (getattr(h, "path", "") or "").split("?", 1)
    return api.parse_qs(qs[1]) if len(qs) > 1 else {}


def _summary(meta):
    items = meta.get("items") or []
    live = G.actionable(items)
    return {
        "chapter": meta.get("chapter"),
        "status": meta.get("status"),
        "items": len(live),
        "decided": sum(1 for it in live if it.get("accepted") is not None),
        "pending": [it["id"] for it in live if it.get("accepted") is None],
        "folded": len(items) - len(live),
    }


def handle_stage5_list(h):
    rows = G.list_all(root=api.ROOT)
    return 200, {"ok": True, "chapters": rows,
                 "pending": [r["chapter"] for r in rows
                             if r["status"] in ("pending", "ready")]}


def handle_stage5_chapter(h):
    try:
        n = int((_query(h).get("n") or ["0"])[0])
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "n 必须是章号"}
    meta = G.load(n, root=api.ROOT)
    if not meta:
        return 404, {"ok": False, "error": "第 %d 章没有待裁定的改稿" % n}
    return 200, {"ok": True, "chapter": n, "status": meta.get("status"),
                 "raw_sha": meta.get("raw_sha"),
                 "items": meta.get("items") or [],
                 "can_apply": all(it.get("accepted") is not None
                                  for it in G.actionable(meta)),
                 # 阅读态（P2-1）：两稿各排成一串段落，改过的那几段带 item 号。
                 # 前端据此按成书版面显示，并把“列表 ↔ 正文”对起来。
                 "layout": G.layout_for(n, meta, root=api.ROOT),
                 "updated_at": meta.get("updated_at")}


def handle_stage5_decide(h, body):
    body = body or {}
    try:
        n = int(body.get("chapter"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "chapter 必填（整数）"}
    raw = body.get("decisions") or {}
    if not isinstance(raw, dict) or not raw:
        return 400, {"ok": False, "error": "decisions 必填：{项号: true/false}"}
    dec = {}
    for k, v in raw.items():
        try:
            dec[int(k)] = bool(v)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "裁定项号必须是整数：%r" % (k,)}
    ok, msg, meta = G.decide(n, dec, root=api.ROOT)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "message": msg, "chapter": _summary(meta) if meta else None}


def _advance_if_finished():
    """裁定全部合成后，把阶段 5 从 awaiting_review 推进成 done。

    ⚠️ 不这么做就是一个死结（2026-11-24 查实）：`stage5_gate` 合成时**不碰
    progress.json**，于是 orchestrator 每轮都看到 `stages[5].status ==
    "awaiting_review"`，在 `orchestrator.py:498-503` 直接收尾 `return 3`——
    用户在界面点多少次“运行”都原地打转，这本书永远到不了阶段 6。
    判据只看"还有没有没定完/没合成的章"，全部 approved 才算完。
    """
    try:
        meta = G.list_all(root=api.ROOT)
    except Exception:                                        # noqa: BLE001
        return False
    if not meta:
        return False
    if any(str(r.get("status")) in ("pending", "ready", "decided") for r in meta):
        return False
    try:
        from utils.progress_manager import ProgressManager
        pg = ProgressManager(api.ROOT / "data" / "state" / "progress.json")
        if pg.stage_status(5) != "done":
            pg.set_stage(5, "done")
            # stage5_check 写进去的待裁定清单要一起清掉，否则 /state 还会报
            # “还有 N 章等你定”，与刚合成完的事实相反。
            st5 = pg.data.get("stages", {}).get("5", {})
            st5.pop("pending", None)
            pg.save()
        return True
    except Exception:                                        # noqa: BLE001
        return False


def handle_stage5_apply(h, body):
    body = body or {}
    if body.get("all"):
        n, msgs = G.apply_all(root=api.ROOT)
        done = _advance_if_finished()
        return 200, {"ok": True, "applied": n, "messages": msgs,
                     "stage5_done": done,
                     "next": ("阶段 5 已全部合成，阶段 6 可以开跑（点顶栏“下一步”）"
                              if done else "还有章没定完：定完并合成后，阶段 5 才算完成")}
    try:
        n = int(body.get("chapter"))
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "chapter 必填（整数），或传 all:true"}
    ok, msg, path = G.apply_chapter(n, root=api.ROOT)
    if not ok:
        return 400, {"ok": False, "error": msg}
    done = _advance_if_finished()
    return 200, {"ok": True, "message": msg,
                 "path": str(path.relative_to(api.ROOT)) if path else None,
                 "stage5_done": done,
                 "next": ("阶段 5 已全部合成，阶段 6 可以开跑（点顶栏“下一步”）"
                          if done else "这一章已合成；还有章没定完")}


ROUTES = [
    ("GET", "/review/stage5/list", handle_stage5_list),
    ("GET", "/review/stage5/chapter", handle_stage5_chapter),
    ("POST", "/review/stage5/decide", handle_stage5_decide),
    ("POST", "/review/stage5/apply", handle_stage5_apply),
]
