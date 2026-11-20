# -*- coding: utf-8 -*-
"""审稿动作域（2026-11-17 拆壳第五批）：批注 / 存决策 / 批量精修 / 自动重写。

为什么这四个一组：它们是一条链上的四个动作 ——
**写批注 → 存决策 → 批量精修（吃决策文件）→ 自动重写（按阈值）**。
链上任何一环的"护栏"都是给上一个环节兜底的，分散在 1300 行 elif 里，
改一个必然漏另一个。

三条护栏原样保留（都是踩过事故加的）：
- `/review/comment` 必须挂在 **POST**：原先误挂在 do_GET，do_GET 没有 body → 必然 500；
- `/batch_refine/run` 拒绝交互式决策（服务端无 TTY 会挂住线程），且文件缺失时
  给可行动提示，而不是起一个必然失败的 job；
- `/auto_rewrite/run` **默认 dry_run=true**：不显式传 false 就只预演、不改稿。

协议同其它域：`handle_*(h, body)` 返回 `(status, payload)`。
"""
import json
import time

import nf_api as api


def handle_review_comment(h, body):
    """给指定 finding 添加评论（GUI 审稿面板用）。"""
    try:
        import chapter_review as cr
        chapter_no = int(body.get("chapter"))
        finding_id = str(body.get("finding_id", "")).strip()
        comment = str(body.get("comment", "")).strip()
        user = str(body.get("user", "reviewer")).strip() or "reviewer"
        if not finding_id or not comment:
            return 400, {"ok": False, "error": "finding_id 和 comment 必填"}
        ok = cr.add_comment_to_finding(
            str(api.ROOT / "data" / "outline" / "review_report.json"),
            chapter_no, finding_id, comment, user)
        if ok:
            return 200, {"ok": True, "message": "评论已添加"}
        return 404, {"ok": False, "error": "未找到指定的 finding"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_review_decisions_save(h, body):
    """保存用户决策：统一规范化为 batch_refine 能直接吃下的标准格式。

    ## 指纹闸（2026-11-20）
    决策是**整批覆盖写**（一份文件装全书所有 accept/ignore）。界面提交的是它
    打开报告那一刻的副本；中间另一个窗口 / CLI 存过决策时，无条件覆盖会把
    对方那一批整份抹掉，而界面上写着"下次打开还在" —— 那句就成了假话。
    带 `expected_mtime` 且对不上 → 409、一个字都不写；不传 = 老行为。
    """
    dec_path = api.ROOT / "data" / "outline" / "review_report.decisions.json"
    try:
        expected = body.get("expected_mtime") if isinstance(body, dict) else None
        if expected is not None:
            disk = dec_path.stat().st_mtime if dec_path.exists() else None
            try:
                same = (disk is not None
                        and abs(float(disk) - float(expected)) <= 0.001)
            except (TypeError, ValueError):
                return 400, {"ok": False, "error": "expected_mtime 必须是数字"}
            if not same:
                return 409, {
                    "ok": False,
                    "error": "盘上决策已被改过（另一个窗口 / 外部 Agent / CLI），"
                             "本次未写入",
                    "mtime": disk,
                    "hint": "重新打开审查报告看盘上那批勾选，再决定要不要保存",
                }
        import batch_refine as br
        decisions = br.normalize_decisions(body)
        dec_path.parent.mkdir(parents=True, exist_ok=True)
        dec_path.write_text(json.dumps({
            "decisions": decisions,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    if decisions:
        return 200, {"ok": True, "count": len(decisions), "path": str(dec_path),
                     "mtime": dec_path.stat().st_mtime,
                     "message": "已保存 " + str(len(decisions)) + " 条决策"}
    return 200, {"ok": True, "count": 0, "path": str(dec_path),
                 "mtime": dec_path.stat().st_mtime,
                 "warning": "未收到有效决策（action 须为 accept/ignore）："
                            "批量精修前请至少接受一条审查发现"}


def handle_batch_refine_run(h, body):
    """按已保存的决策文件批量精修。"""
    from_decisions = bool(body.get("decisions_from_file", True))
    if not from_decisions:
        # 服务端无 TTY，交互式决策会挂住线程；GUI 一律走 decisions.json
        return 400, {"error": "API 不支持交互式决策（无 TTY）：请用 "
                              "decisions_from_file=true（先在审稿页保存决策）"}
    # 护栏：文件缺失时给出可行动的提示，而不是起一个必然失败的 job
    report_p = api.ROOT / "data" / "outline" / "review_report.json"
    dec_p = api.ROOT / "data" / "outline" / "review_report.decisions.json"
    if not report_p.exists():
        return 400, {"error": "无审查报告 data/outline/review_report.json（先运行审查）"}
    if not dec_p.exists():
        return 400, {"error": "无决策文件 data/outline/review_report.decisions.json"
                              "（先在审稿页保存决策）"}
    try:
        import batch_refine as br
        n_dec = len(br.normalize_decisions(
            json.loads(api.nf_read_text(dec_p)) if dec_p.stat().st_size else {}))
    except Exception as e:                                  # noqa: BLE001
        n_dec = 0
        print("[nf_api] 决策文件解析失败: " + type(e).__name__ + ": " + str(e)[:120])
    if not n_dec:
        return 400, {"error": "决策文件无有效条目（action 须为 accept/ignore，"
                              "且带 finding_id）；请在审稿页重新保存决策"}
    jid, err = api.start_job("batch_refine", api.act_batch_refine_run(from_decisions))
    return (202, {"job_id": jid}) if not err else (409, {"error": err})


def _int_or_none(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def handle_auto_rewrite_run(h, body):
    """按同质化阈值自动重写（**默认只预演**）。"""
    chapters = body.get("chapters")
    if isinstance(chapters, str):
        chapters = [int(x) for x in chapters.split(",") if x.strip().isdigit()]
    elif isinstance(chapters, list):
        chapters = [int(x) for x in chapters if str(x).isdigit()]
    else:
        chapters = None
    # 安全默认：未显式传 dry_run=false 时只预演，不改稿
    dry_run = bool(body.get("dry_run", True))
    jid, err = api.start_job("auto_rewrite", api.act_auto_rewrite_run(
        _int_or_none(body.get("threshold")), dry_run,
        _int_or_none(body.get("max_rounds")), chapters))
    return (202, {"job_id": jid}) if not err else (409, {"error": err})


ROUTES = (
    ("POST", "/review/comment", handle_review_comment),
    ("POST", "/review/decisions", handle_review_decisions_save),
    ("POST", "/batch_refine/run", handle_batch_refine_run),
    ("POST", "/auto_rewrite/run", handle_auto_rewrite_run),
)
