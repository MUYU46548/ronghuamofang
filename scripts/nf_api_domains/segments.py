# -*- coding: utf-8 -*-
"""分段审批域（丙4 · 大纲分段审阅的读写两侧）。

## 为什么单独开一个域模块

`/outline/segments`（读）与 `/outline/segments/set`（写）是**同一个状态机**的两半
——判据与记账全在 `utils.outline_segments`（切分 → 记账 → `gate_status` 放行）。
放不进现有任何一块地：`outline.py` 的宪章是「只承载读端点、写留在 do_POST」，
`sandbox.py` 管沙盒审核，`plan.py` 管规划四态。与其把一个功能拆到三个文件，
不如让读写同居一处，边界清楚。

## 红线：读随便，勾不行

「把某段标为已读通过」是**审批级**动作 —— 分段不降低强度（全过才放行 stage2），
所以勾一段 = 替用户读了那一段。因此：

- 写侧要求 **GUI 双因子**（来源头 + 主进程签发 token），实现复用
  `nf_api_domains.project._gui_only`（其判据是 `agent_guard.is_trusted_gui`，
  与 `do_POST` 禁用名单同一份）—— 不在这里另写一份判据，两份必漂移；
- 写侧列入 `agent_guard.FORBIDDEN_IN_AGENT_MODE`：Agent 模式下外部 Agent 可**读**
  （知道自己该审什么、差哪几段），不可**勾**；
- 读侧不设闸：它只回显 `global.md` 本就存在正文与分段状态，不泄露任何密钥。

放行判据仍然只有 `gate_status()` 一份（CLI / HTTP 共用），本模块不复述它。
"""
import nf_api as api


def _segments_root():
    """本模块操作的项目根（属性访问，不能值拷贝 —— 见 nf_api_domains.__init__）。"""
    return str(api.ROOT)


def handle_segments_get(h):
    """分段审阅读模型：分段清单 + 每段正文 + 放行判据回显（丙4 GUI 侧）。

    返回 `enabled` = 这道闸**现在真在管事**（开关开着 且 能切出 >=2 段）——
    前端据此决定要不要渲染分段面板：切不出两段时面板毫无意义（契约 2）。

    `gate_ok` / `gate_message` 与 CLI 是同一份判据的回显，让用户在勾之前就看见
    「还差哪几段」，而不是点「确认放行」吃一个 409 才知道。
    """
    try:
        from utils import outline_segments as seg
        root = _segments_root()
        sm = seg.summary(root)                      # 内部先 sync（改过的大纲对齐）
        doc = seg.doc_path(root)
        text = seg.read_text(doc) if doc.exists() else ""
        bounds = {s.get("id"): s for s in seg.split_segments(text)}
        items = []
        for s in sm.get("segments") or []:
            b = bounds.get(s.get("id")) or {}
            it = dict(s)
            it["text"] = text[b["start"]:b["end"]] if b else ""
            items.append(it)
        cfg = api.load_all()[0] or {}
        gates = cfg.get("gates") or {}
        sw = ("segment_approval" in gates) and bool(gates.get("segment_approval"))
        gate_ok, gate_msg = seg.gate_status(root, cfg=cfg, stage=2)
        return 200, {
            "ok": True,
            "enabled": bool(sw and len(items) >= 2),
            "segments": items,
            "total": sm.get("total", 0),
            "approved": sm.get("approved", 0),
            "pending": sm.get("pending", 0),
            "pending_ids": sm.get("pending_ids") or [],
            "all_approved": bool(sm.get("all_approved")),
            "doc_sha256": sm.get("doc_sha256", ""),
            "invalidations": sm.get("invalidations", 0),
            "updated_at": sm.get("updated_at", ""),
            "doc_exists": bool(sm.get("doc_exists")),
            "doc_path": "data/outline/global.md",    # 供前端「预览原文」按钮直开
            "gate_ok": bool(gate_ok),
            "gate_message": gate_msg or "",
            "switch_on": bool(sw),
        }
    except Exception as e:                           # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_segments_set(h, body):
    """标记某段已读通过 / 撤销标记（写侧 · 审批级，要求 GUI 双因子）。

    拦下时不碰状态文件一个字节（先判后写），403 文案零通道名（V5）。
    段号不存在返回 400 并回显现有段号 —— 静默失败会让用户以为自己勾上了。
    """
    guard = _gui_only(h, "大纲分段审批标记")
    if guard:
        return guard
    try:
        from utils import outline_segments as seg
        root = _segments_root()
        b = body or {}
        sid = str(b.get("id") or "").strip()
        if not sid:
            return 400, {"ok": False, "error": "缺少分段 id（用 GET /outline/segments 取）"}
        approved = bool(b.get("approved", True))
        ok, msg = seg.set_segment(root, sid, approved)
        if not ok:
            return 400, {"ok": False, "error": msg}
        sm = seg.summary(root)
        # 进度与下一步直接回显：GUI 勾完一段不用再拉一次就知道还差几段。
        return 200, {
            "ok": True,
            "message": msg,
            "total": sm.get("total", 0),
            "approved": sm.get("approved", 0),
            "pending": sm.get("pending", 0),
            "pending_ids": sm.get("pending_ids") or [],
            "all_approved": bool(sm.get("all_approved")),
            "next": ("分段已全过，可以放行阶段2" if sm.get("all_approved")
                     else "还差 %s 段" % sm.get("pending", 0)),
        }
    except Exception as e:                           # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def _gui_only(h, what):
    """写侧闸：复用 project 域的 `_gui_only`（判据 = agent_guard.is_trusted_gui）。

    **函数内延迟 import**：域模块之间在 nf_api 导入期互相 import 会形成
    「nf_api → 域A → 域B → nf_api」的半初始化环；调用期再取就没有环。
    为什么不另写一份：双因子判据只能有一份，抄一份就等于给自己开后门。
    """
    from nf_api_domains.project import _gui_only as _impl
    return _impl(h, what)


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/outline/segments", handle_segments_get),
    ("POST", "/outline/segments/set", handle_segments_set),
)
