# -*- coding: utf-8 -*-
"""设定集状态域：素材冲突清单 / canon 快照 / 人工拍板。

## 为什么单列一个域

这三条是 A3（素材库状态机）的**读侧 + 写侧**入口，服务对象是**外部 Agent**
（经 `nf_mcp.py` 暴露成 MCP 工具）。它们只碰 `data/setting/` 下的**既有产物**
（`merge_audit.json` / `canon.json` / `tombstones.json` / `setting.json`），
**不新造数据层** —— 与域模块“薄适配”的本分一致。

## 语义红线

1. **`checked=false` ≠“没有冲突”**：设定集不存在 / 解析失败时 audit 给的是
   `checked=false`。必须原样透出，让调用方看见“**没查**”而不是“一致”
   （A3 的 W7 教训：空结果被读成通过）。
2. **reject 必须写原因**：无原因的否决，三天后自己也看不懂（同沙盒审核域）。
3. **人工拍板 > 机器判定**：`set_entry_status` 打 `status_source="human"` 标记，
   下一轮 stage1 的 `apply_status` 会跳过它 —— 否则拍板被机器判定抹掉 = 白拍。
4. **`body` 由 `do_POST` 传入**：handler 里**绝不**再调 `h._body()`（请求体是
   一次性流，二次读会挂到客户端超时，表现为“点了没反应”且服务端日志空白）。
"""
import nf_api as api


def _bad(msg):
    return 400, {"ok": False, "error": msg}


def _paths():
    """本域路径**全部**从 `api.ROOT` 现取，不用相对路径。

    属性访问（而非 `from nf_api import ROOT`）是硬要求：`--root` 与测试的临时
    项目根会**重设** `nf_api.ROOT`，`from ... import` 会把值拷死。
    另外这些路径**绝不能**写成 CWD 相对 —— nf_api 不再 chdir，相对路径在
    `--root` 下会读写到启动目录而不是项目根。
    """
    base = api.ROOT / "data" / "setting"
    return {
        "setting": base / "setting.json",
        "canon": base / "canon.json",
        "tombstones": base / "tombstones.json",
        "audit": base / "merge_audit.json",
        "materials": api.ROOT / "materials" / "raw",
    }


def _slim_conflict(c, limit=200):
    """冲突条目瘦身：MCP 的返回值会整段进模型上下文，超长字段要截断。"""
    vals = []
    for v in (c.get("values") or []):
        if isinstance(v, dict):
            vals.append({"value": str(v.get("value") or "")[:limit],
                         "file": v.get("file") or v.get("source") or ""})
        else:
            vals.append({"value": str(v)[:limit], "file": ""})
    return {"name": c.get("name"), "field": c.get("field"),
            "severity": c.get("severity"), "values": vals}


def _count_entries(data):
    """各顶层键的条目数（world 取子类目合计）。"""
    out = {}
    for k, v in (data or {}).items():
        if k == "_meta":
            continue
        if isinstance(v, list):
            out[k] = len(v)
        elif isinstance(v, dict):
            out[k] = sum(len(x) if isinstance(x, list) else 1 for x in v.values())
    return out


def handle_setting_conflicts(h):
    """GET /setting/conflicts → 素材冲突清单 + 状态分布 + 墓碑（零 token）。

    调用方（外部 Agent）应重点看两条：
      · `review_conflicts` —— **机器裁不了、必须人拍板**的分歧（带双方出处）；
      · `checked` —— false 表示**根本没查**（设定集缺失/损坏），此时
        `review_conflicts` 为空**不代表没有冲突**。
    """
    from utils import material_state as ms
    p = _paths()
    try:
        report = ms.audit(str(p["setting"]), str(p["materials"]), str(p["tombstones"]))
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    rev = report.get("review_conflicts") or []
    return 200, {
        "ok": True,
        "checked": bool(report.get("checked")),
        "reason": report.get("reason") or "",
        "review_conflicts": [_slim_conflict(c) for c in rev],
        "review_count": len(rev),
        "counts": report.get("counts"),
        "tombstones": report.get("tombstones"),
        "card_count": report.get("card_count"),
        "audited_at": report.get("audited_at"),
        "audit_path": str(p["audit"]),
    }


def handle_setting_canon(h):
    """GET /setting/canon[?full=1] → canon 快照元信息（默认**不含全文**）。

    为什么默认不给全文：canon 是全量设定集，几万字符，直接塞进 Agent 上下文
    既贵又容易淹没重点。需要全文时显式 `?full=1`。
    """
    q = h._query()
    full = str((q.get("full") or ["0"])[0]).lower() in ("1", "true", "yes")
    p = _paths()
    cp = p["canon"]
    if not cp.exists():
        return 200, {
            "ok": True, "exists": False, "path": str(cp),
            "hint": "尚未冻结：stage1 完成后 `python scripts/approve.py --stage 1` "
                    "会自动冻结 canon 快照",
        }
    try:
        data = api.json.loads(cp.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": "canon 解析失败: " + repr(e)}
    meta = data.get("_meta") or {}
    out = {
        "ok": True, "exists": True, "path": str(cp),
        "frozen_at": meta.get("canon_frozen_at"),
        "counts": _count_entries(data),
    }
    if full:
        out["content"] = data
    else:
        out["hint"] = "全文可能很大，需要时加 ?full=1"
    return 200, out


def handle_setting_status(h, body):
    """POST /setting/status {name, action: adopt|reject|uncertain, reason?} → 人工拍板。

    行为：
      · `reject` → **先写墓碑**（墓碑按名字生效，不依赖设定集条目），再把条目
        标为 rejected。无条目也成功（“不得复活”立即生效）。
      · `adopt` / `uncertain` → 改设定集条目的 status；**条目不存在则 404**
        （这两种状态只能挂在条目上，凭空记没有意义）。
      · 拍板后刷新一次审计（`merge_audit.json`），让冲突清单反映最新状态；
        这一步失败**不影响**拍板本身。
    """
    from utils import material_state as ms
    p = _paths()
    body = body if isinstance(body, dict) else {}
    name = str(body.get("name") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    reason = str(body.get("reason") or "").strip()
    if not name:
        return _bad("name 必填（要拍板的条目名）")
    mapping = {"adopt": ms.STATUS_ADOPTED,
               "reject": ms.STATUS_REJECTED,
               "uncertain": ms.STATUS_UNCERTAIN}
    if action not in mapping:
        return _bad("action 必须是 adopt / reject / uncertain 之一，收到: "
                    + (action or "(空)"))
    if action == "reject" and not reason:
        return _bad("reject 必须填 reason（说明为什么否决）——"
                    "无原因的否决，三天后自己也看不懂")
    status = mapping[action]
    try:
        if action == "reject":
            ms.add_tombstone(name, reason, source="mcp", path=str(p["tombstones"]))
        hit, msg = ms.set_entry_status(name, status, reason,
                                       setting_path=str(p["setting"]))
        if not hit and action != "reject":
            return 404, {
                "ok": False, "error": msg,
                "hint": "设定集里没有该条目（可能尚未归并）。若只想阻止它复活，"
                        "用 action=reject（写墓碑，不依赖条目）。",
            }
        try:
            report = ms.audit(str(p["setting"]), str(p["materials"]),
                              str(p["tombstones"]))
            if report.get("checked"):
                ms.write_audit(report, str(p["audit"]))
        except Exception:                                     # noqa: BLE001
            pass
        tombs = sorted((ms.load_tombstones(str(p["tombstones"])).get("entries") or {}).keys())
        return 200, {"ok": True, "message": msg, "name": name, "status": status,
                     "entry_hit": hit, "tombstones": tombs}
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_setting_provenance(h):
    """GET /setting/provenance?name=X&kind=characters → 条目来源链路（F21）。

    回答三件事：最早哪一版进来、现在这个值从哪版起、来自哪个导入文件。
    没有历史版本时 `traceable=false` 并说明为什么 —— 不编造“第 1 版就有”。
    """
    q = h._query()
    name = str((q.get("name") or [""])[0]).strip()
    kind = str((q.get("kind") or ["characters"])[0]).strip() or "characters"
    if not name:
        return _bad("name 必填（要追溯的条目名）")
    try:
        from utils import setting_provenance as sp
        res = sp.trace(name, root=str(api.ROOT), kind=kind)
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    return (200 if res.get("ok") else 400), res


def handle_setting_names(h):
    """GET /setting/names → 当前设定集里的条目名（供界面下拉/补全，零 token）。"""
    p = _paths()
    sp = p["setting"]
    if not sp.exists():
        return 200, {"ok": True, "exists": False, "characters": [], "world": [],
                     "hint": "还没有 setting.json（阶段 1 尚未完成）"}
    try:
        data = api.json.loads(sp.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": "setting.json 解析失败: " + repr(e)}

    def names(key):
        out = []
        for e in (data.get(key) or []):
            if not isinstance(e, dict):
                continue
            n = str(e.get("name") or e.get("title") or e.get("名称") or "").strip()
            if n:
                out.append(n)
        return out

    return 200, {"ok": True, "exists": True,
                 "characters": names("characters"), "world": names("world")}


def handle_setting_interaction(h):
    """GET /setting/interaction?a=&b= → 两人同场的互动约束（C 组，零 token）。

    只读设定集里已有的关系原文；关系词命中不了就明说“未知”，
    称呼没写就明说没写 —— 给的是**按关系基调的写作建议**，不是正典。
    """
    q = h._query()
    a = str((q.get("a") or [""])[0]).strip()
    b = str((q.get("b") or [""])[0]).strip()
    if not a or not b:
        return _bad("a / b 必填（两个角色名）")
    try:
        from utils import interaction
        res = interaction.build(str(api.ROOT), a, b)
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    return (200 if res.get("ok") else 400), res


# ---------------------------------------------------------------------------
# 设定引用过期（M10，2026-11-04）：条目记 sources（章号 + 版本），改章即待核
# ---------------------------------------------------------------------------

def handle_setting_stale(h):
    """GET /setting/stale → 待核清单（零 token，**读时现算**，不写任何文件）。

    `checked=false` 表示设定集缺失/损坏 = **根本没查**，此时 `items` 为空
    不代表没有问题 —— 与冲突清单同一条语义红线。
    """
    try:
        from utils import setting_stale as ss
        return 200, ss.status(str(api.ROOT))
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_setting_stale_link(h, body):
    """POST /setting/stale/link {dry_run?} → 扫描章节，把现有引用补登记进条目。

    **只增不改**：已登记的那一条哪怕章改过也不刷新版本 —— 否则“点一下扫描”
    就把待核全洗白，机制等于摆设。机械动作，Agent 可调（不是作者的判断）。
    """
    body = body if isinstance(body, dict) else {}
    try:
        from utils import setting_stale as ss
        rep = ss.link(str(api.ROOT), dry_run=bool(body.get("dry_run")))
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    return (200 if rep.get("ok") else 400), rep


def handle_setting_stale_review(h, body):
    """POST /setting/stale/review {name, kind?, chapters?[], action: confirm|drop}
    → 作者逐条核对后的恢复（确认还成立 / 移除这条来源）。

    **GUI-only（双因子）**：这是作者的判断 —— 外部 Agent 一条 POST 就能把
    “引用可能已不成立”洗成“已核对”，与代签审批同族。读侧 GET /setting/stale
    不拦（Agent 该看得见待核清单）。
    """
    from nf_api_domains.project import _gui_only
    guard = _gui_only(h, "核对设定引用")
    if guard:
        return guard
    body = body if isinstance(body, dict) else {}
    try:
        from utils import setting_stale as ss
        return ss.review(str(api.ROOT), name=body.get("name"),
                         kind=body.get("kind"), chapters=body.get("chapters"),
                         action=body.get("action") or "confirm")
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/setting/conflicts", handle_setting_conflicts),
    ("GET", "/setting/canon", handle_setting_canon),
    ("POST", "/setting/status", handle_setting_status),
    ("GET", "/setting/provenance", handle_setting_provenance),
    ("GET", "/setting/names", handle_setting_names),
    ("GET", "/setting/interaction", handle_setting_interaction),
    ("GET", "/setting/stale", handle_setting_stale),
    ("POST", "/setting/stale/link", handle_setting_stale_link),
    ("POST", "/setting/stale/review", handle_setting_stale_review),
)
