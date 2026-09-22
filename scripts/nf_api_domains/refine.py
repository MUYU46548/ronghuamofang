# -*- coding: utf-8 -*-
"""精修域（POST 写侧）：大纲精修、节点精修、章节精修、撤销。

## 边界

所有涉及「读用户反馈 → 调 LLM → 写盘 + 备份」的操作。
与 runtime.py 的区别：runtime 只读，refine 只写。

## 安全纪律

- 改稿前必须备份（±20% 铁律由 refine_chapter.py 内部执行）
- 所有路径经 api.ROOT，禁止相对路径
- 写后回读校验
"""
import nf_api as api


def handle_refine_outline(h, body):
    """大纲精修：{feedback, dry_run?} → job_id"""
    fb = str(body.get("feedback") or "")
    if not fb.strip():
        return 400, {"error": "feedback 必填"}
    try:
        cfg, _ = api.load_all()
        api._client_for_env(cfg, "default")  # 测试模式预检
        jid, err = api.start_job("refine_outline",
                                 lambda: api.act_refine_outline(fb, bool(body.get("dry_run"))))
        if err:
            return 409, {"error": err}
        return 202, {"job_id": jid}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_refine_outline_node(h, body):
    """逐节点 AI 精修：{node_id/entry_id, feedback, dry_run?} → job_id"""
    node_id = str(body.get("node_id") or body.get("entry_id") or "")
    fb = str(body.get("feedback") or "")
    if not node_id:
        return 400, {"error": "node_id 必填"}
    if not fb.strip():
        return 400, {"error": "feedback 必填"}
    try:
        cfg, _ = api.load_all()
        api._client_for_env(cfg, "default")
        dry = bool(body.get("dry_run"))
        jid, err = api.start_job("refine_node_" + node_id,
                                 lambda: api.act_outline_refine_node(node_id, fb, dry))
        if err:
            return 409, {"error": err}
        return 202, {"job_id": jid}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_refine_outline_undo(h):
    """撤销上一次节点精修（从 history 最新版本恢复）"""
    try:
        ok, res = api.act_outline_undo()
        if ok:
            # res is dict: {"undo": ..., "structure": ...}
            res["ok"] = True
            return 200, res
        # res is str (error message)
        return 400, {"ok": False, "error": res}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_refine_chapter(h, body):
    """章节精修：{chapter, feedback, dry_run?} → job_id"""
    fb = str(body.get("feedback") or "")
    ch = int(body.get("chapter") or 0)
    if not fb.strip() or not (1 <= ch <= 999):
        return 400, {"error": "chapter(>=1) 与 feedback 必填"}
    try:
        cfg, _ = api.load_all()
        api._client_for_env(cfg, "default")
        jid, err = api.start_job("refine_ch" + str(ch),
                                 lambda: api.act_refine_chapter(ch, fb, bool(body.get("dry_run"))))
        if err:
            return 409, {"error": err}
        return 202, {"job_id": jid}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("POST", "/refine/outline", handle_refine_outline),
    ("POST", "/refine/outline/node", handle_refine_outline_node),
    ("POST", "/refine/outline/undo", handle_refine_outline_undo),
    ("POST", "/refine/chapter", handle_refine_chapter),
)
