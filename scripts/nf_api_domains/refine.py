# -*- coding: utf-8 -*-
"""精修域（POST 写侧 + GET 读取）：大纲精修、节点精修、章节精修、段落精修、段落历史、风格偏差。

## 边界

所有涉及“读用户反馈 → 调 LLM → 写盘 + 备份”的操作，以及段落级读取和风格分析。
与 runtime.py 的区别：runtime 只读状态，refine 负责写操作 + 段落查询 + 风格分析。

## 安全纪律

- 改稿前必须备份（±20% 铁律由 refine_chapter.py 内部执行）
- 所有路径经 api.ROOT，禁止相对路径
- 写后回读校验
"""
import nf_api as api
from utils.branch_samples import BRANCH_KINDS
from utils.field_refine import FIELD_LABELS


# ---------------------------------------------------------------- 大纲精修（self 模式下出任务书）
# 2026-11-08（0.9.1 验收第 9 条）：作者在 self 模式下点“AI 精修大纲”，得到的是
# 一句“用不了”——因为 self 不调用模型。可他的真实用法就是**让外部 agent 改大纲**：
# “绒花墨坊和外部 agent 很多时候是断开的”。这里把这条路接上：
#   self → 备份当前大纲 + 写一份任务书（含当前大纲全文与你的意见）→ 外部写回
#          data/outline/global.md → 再跑阶段 2 即按体检判据校验。
#   其余引擎 → 原样走模型精修，一个字节不改。
def build_self_refine_task(cfg, fb):
    """self 模式的大纲精修任务书（零 token）。返回 (task_path, backup_path) 或抛错。"""
    from utils import self_tasks as st
    from pathlib import Path
    import refine_outline as ro
    from utils.file_io import read_text
    version = ro.next_version(ro.HISTORY_DIR)
    backup = ro.backup_current(ro.GLOBAL, ro.HISTORY_DIR, version)
    current = read_text(ro.GLOBAL)
    body = "\n".join([
        "## 这一份要干什么",
        "",
        "按下面这条意见改**整本书的大纲**（不是某一章），改完写回同一个文件。",
        "",
        "## 你的意见",
        "",
        fb.strip(),
        "",
        "## 写回哪里",
        "",
        "- 目标文件：`data/outline/global.md`（**原地覆盖**；旧版已备份到 `%s`）" % backup.as_posix(),
        "- 不要新建文件、不要改文件名；改完不要再复制一份到别处。",
        "",
        "## 硬约束（违反任何一条，阶段 2 的体检会报出来）",
        "",
        "1. **章节数不变**：`config/project.yaml` 的 `book.chapters` 是几章，就还是几章；",
        "2. **每个章节节点都要有具体事件**（不是“继续调查 / 遇到阻力”这类空话）；",
        "3. **保留卷 / 章的结构标题**，不要删卷、不要把多章合并成一章；",
        "4. 中文标点：双引号用 “ ”，不用直角引号；不要写 `---` 分隔线；",
        "5. 只改大纲，**不要动** `data/setting/`、`data/chapters/` 里的任何文件。",
        "",
        "## 改完之后",
        "",
        "- 回到绒花墨坊点一次“运行”（阶段 2）：程序只看文件，会按体检判据校验结构、",
        "  锚点与章节数；不合格会说清是哪一条，并允许再改一版。",
        "- 旧版可回退：`%s`。" % backup.as_posix(),
        "",
        "## 当前大纲全文（改这份）",
        "",
        "````markdown",
        current.rstrip(),
        "````",
        "",
    ])
    crit = ("data/outline/global.md 已按意见改写；章节节点数 = book.chapters；"
            "每个章节节点有具体事件；结构标题与卷划分保留；旧版在 data/outline/history/"
            "global_v%d.md" % version)
    task = st.write_task(2, "refine_outline_v%d.md" % version, body,
                         ["data/outline/global.md"], crit,
                         note="外部改写；写完回绒花墨坊点“运行”（阶段 2）复核。",
                         stage_name="大纲精修")
    # write_task 返回的是 path / target / check 三个字段的描述（不是 Path 对象）——
    # 对外只给路径串：把 dict 直接 str() 出去，接口里就会把这个描述当文件名传出去
    # （第一版踩过，界面上显示成一串花括号）。
    task_path = Path(task["path"]) if isinstance(task, dict) else Path(str(task))
    return task_path, backup


def handle_refine_outline(h, body):
    """大纲精修：{feedback, dry_run?} → job_id"""
    fb = str(body.get("feedback") or "")
    if not fb.strip():
        return 400, {"error": "feedback 必填"}
    try:
        cfg, _ = api.load_all()
        from utils.engine import resolve_engine
        if resolve_engine(cfg) == "self":
            task, backup = build_self_refine_task(cfg, fb)
            return 202, {"mode": "self", "task": str(task), "backup": str(backup),
                         "message": "自任务模式：已出大纲精修任务书（不调用模型）。"
                                    "把 %s 交给外部执行方，写完回这里点运行复核。" % task,
                         "hint": "任务书含当前大纲全文与你的意见；旧版已备份到 " + str(backup)}
        api._client_for_env(cfg, "default")
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
    """撤销上一次节点精修"""
    try:
        ok, res = api.act_outline_undo()
        if ok:
            res["ok"] = True
            return 200, res
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
        cfg, proj = api.load_all()
        from utils.engine import resolve_engine
        if resolve_engine(cfg) == "self":
            # self：不调模型 → 出任务书（走按钮级派单，草稿 + 你采用才落盘）。
            from utils import self_actions as sa
            try:
                info = sa.write_chapter_task(cfg, proj, ch, fb, kind="refine")
            except (ValueError, FileNotFoundError) as e:
                return 400, {"ok": False, "error": str(e)}
            return 202, dict(info, ok=True, mode="self",
                             message="自任务模式：已出第 %d 章的精修任务书（不调用模型）：%s。"
                                     "交给外部执行方写到 %s，写完回来点“采用草稿”。"
                                     % (ch, info["task"], info["draft"]))
        api._client_for_env(cfg, "default")
        jid, err = api.start_job("refine_ch" + str(ch),
                                 lambda: api.act_refine_chapter(ch, fb, bool(body.get("dry_run"))))
        if err:
            return 409, {"error": err}
        return 202, {"job_id": jid}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_refine_paragraph(h, body):
    """段落级定点重生成：{chapter, paragraph_index, feedback, dry_run?} → job_id"""
    fb = str(body.get("feedback") or "")
    ch = int(body.get("chapter") or 0)
    pi = int(body.get("paragraph_index") or -1)
    if not fb.strip() or not (1 <= ch <= 999) or pi < 0:
        return 400, {"error": "chapter(>=1)、paragraph_index(>=0) 与 feedback 必填"}
    try:
        cfg, _ = api.load_all()
        api._client_for_env(cfg, "polisher")
        jid, err = api.start_job("refine_para_ch" + str(ch) + "_p" + str(pi),
                                 lambda: api.act_refine_paragraph(ch, pi, fb, bool(body.get("dry_run"))))
        if err:
            return 409, {"error": err}
        return 202, {"job_id": jid}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_field_candidates(h, body):
    """定向改写候选生成（第 3 件 · C 兜底）：{chapter, field, note?} → job_id

    单轮产出 2-3 个候选，**不碰章大纲**；挑中哪个、要不要改由用户在 GUI 决定，
    选定后走 /outline/field/submit 落提案（进待审队列，批准才写回）。
    """
    try:
        ch = int(body.get("chapter") or 0)
    except (TypeError, ValueError):
        return 400, {"error": "chapter 须为整数"}
    field = str(body.get("field") or "").strip()
    if not (1 <= ch <= 999):
        return 400, {"error": "chapter(>=1) 必填"}
    if field not in FIELD_LABELS:
        return 400, {"error": "field 必填且须是 " + "/".join(FIELD_LABELS) + " 之一"}
    note = str(body.get("note") or "")
    try:
        cfg, _ = api.load_all()
        api._client_for_env(cfg, "outliner")
        jid, err = api.start_job(f"field_refine_ch{ch}_{field}",
                                 lambda: api.act_field_candidates(ch, field, note))
        if err:
            return 409, {"error": err}
        return 202, {"job_id": jid}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_field_current(h, body):
    """读某章某字段当前值（GUI 打开改写面板时预填）。"""
    try:
        ch = int((body or {}).get("chapter") or 0)
    except (TypeError, ValueError):
        return 400, {"error": "chapter 须为整数"}
    field = str((body or {}).get("field") or "").strip()
    if not (1 <= ch <= 999):
        return 400, {"error": "chapter(>=1) 必填"}
    if field not in FIELD_LABELS:
        return 400, {"error": "field 必填且须是 " + "/".join(FIELD_LABELS) + " 之一"}
    try:
        from utils import field_refine
        ok, err, val = field_refine.current_field(ch, field, root=str(api.ROOT))
        if not ok:
            return 404, {"ok": False, "error": err}
        return 200, {"ok": True, "chapter": ch, "field": field, "value": val}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_field_submit(h, body):
    """把选定的候选落成提案（**不直接写章大纲**）：{chapter, field, text, why?, note?}

    写回归用户在“审核”页签批准（与讨论提案同一队列、同一守卫）。
    """
    try:
        ch = int(body.get("chapter") or 0)
    except (TypeError, ValueError):
        return 400, {"error": "chapter 须为整数"}
    field = str(body.get("field") or "").strip()
    text = str(body.get("text") or "")
    if not (1 <= ch <= 999):
        return 400, {"error": "chapter(>=1) 必填"}
    if field not in FIELD_LABELS:
        return 400, {"error": "field 必填且须是 " + "/".join(FIELD_LABELS) + " 之一"}
    if not text.strip():
        return 400, {"error": "text 必填（选定的候选内容）"}
    try:
        cfg, _ = api.load_all()
        ok, detail = api.act_field_submit(ch, field, text,
                                          why=str(body.get("why") or ""),
                                          note=str(body.get("note") or ""))
        status = 200 if ok else 400
        return status, ({"ok": True, **detail} if ok else {"ok": False, "error": detail})
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_branch_candidates(h, body):
    """分支样例生成（第 4 件 · 选项可视化）：{chapter, kind, note?} → job_id

    同一章出 2-3 个方向明显不同的开头，让用户在动笔前先比“同一件事，三种写法”。
    产物不碰章大纲；选定后走 /outline/sample/submit 落提案（批准才写回）。
    """
    try:
        ch = int(body.get("chapter") or 0)
    except (TypeError, ValueError):
        return 400, {"error": "chapter 须为整数"}
    kind = str(body.get("kind") or "tone").strip() or "tone"
    if not (1 <= ch <= 999):
        return 400, {"error": "chapter(>=1) 必填"}
    if kind not in BRANCH_KINDS:
        return 400, {"error": "kind 须是 " + "/".join(BRANCH_KINDS) + " 之一"}
    note = str(body.get("note") or "")
    try:
        cfg, _ = api.load_all()
        api._client_for_env(cfg, "writer")
        jid, err = api.start_job(f"branch_sample_ch{ch}_{kind}",
                                 lambda: api.act_branch_samples(ch, kind, note))
        if err:
            return 409, {"error": err}
        return 202, {"job_id": jid}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_branch_submit(h, body):
    """把选定的写作方向落成**样例类**提案（不直接写章大纲）：
    {chapter, kind, label, text, why?, note?}
    """
    try:
        ch = int(body.get("chapter") or 0)
    except (TypeError, ValueError):
        return 400, {"error": "chapter 须为整数"}
    kind = str(body.get("kind") or "tone").strip() or "tone"
    label = str(body.get("label") or "").strip()
    text = str(body.get("text") or "")
    if not (1 <= ch <= 999):
        return 400, {"error": "chapter(>=1) 必填"}
    if kind not in BRANCH_KINDS:
        return 400, {"error": "kind 须是 " + "/".join(BRANCH_KINDS) + " 之一"}
    if not text.strip():
        return 400, {"error": "text 必填（选定方向的样例正文）"}
    try:
        cfg, _ = api.load_all()
        ok, detail = api.act_branch_submit(ch, kind, label, text,
                                           why=str(body.get("why") or ""),
                                           note=str(body.get("note") or ""))
        status = 200 if ok else 400
        return status, ({"ok": True, **detail} if ok else {"ok": False, "error": detail})
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_branch_current(h, body):
    """读某章已选定的写作方向（GUI 面板显示“这一章已经选了什么”）。"""
    try:
        ch = int((body or {}).get("chapter") or 0)
    except (TypeError, ValueError):
        return 400, {"error": "chapter 须为整数"}
    if not (1 <= ch <= 999):
        return 400, {"error": "chapter(>=1) 必填"}
    try:
        ok, detail = api.act_branch_current(ch)
        return 200, {"ok": ok, "chapter": ch, **detail}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_paragraph_restore(h, body):
    """回退段落：{chapter, paragraph_index, history_id} → 结果"""
    ch = int(body.get("chapter") or 0)
    pi = int(body.get("paragraph_index") or -1)
    hid = int(body.get("history_id") or 0)
    if ch < 1 or pi < 0 or hid < 1:
        return 400, {"error": "chapter(>=1)、paragraph_index(>=0)、history_id(>=1) 必填"}
    try:
        import refine_paragraph
        ok, msg = refine_paragraph.restore_paragraph_version(ch, pi, hid)
        return (200 if ok else 400), {"ok": ok, "message": msg}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_paragraph_save(h, body):
    """手改一段并保存：{chapter, paragraph_index, text}（2026-11-02，清单第 8 条）。

    与 `/refine/paragraph`（提意见让模型重写）**并列**的第二条路。
    以前只有许愿那一条 —— 想改一个字也得让模型重跑一遍。
    """
    try:
        ch = int(body.get("chapter") or 0)
        pi = int(body.get("paragraph_index") if body.get("paragraph_index") is not None else -1)
    except (TypeError, ValueError):
        return 400, {"error": "chapter / paragraph_index 须为整数"}
    text = body.get("text")
    if ch < 1 or pi < 0 or not isinstance(text, str):
        return 400, {"error": "chapter(>=1)、paragraph_index(>=0)、text 必填"}
    try:
        import refine_paragraph
        ok, msg = refine_paragraph.save_paragraph_text(ch, pi, text)
        return (200 if ok else 400), {"ok": ok, "message": msg,
                                      "chapter": ch, "paragraph_index": pi}
    except Exception as e:                                # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_paragraphs(h):
    """获取章节的段落列表（带索引）。

    `layer`（可选，2026-10-26）：raw / checked / refined。
    不传时保持旧行为（refined > checked > raw 逐层回退）。
    为什么要加：章节编辑器里用户**看得见哪一层**，批注就必须作用在同一层上 ——
    否则他打开原稿、批注第 3 段，后端却去改润色稿的第 3 段（三层段落数常常不同，
    错位是必然的）。带 layer 时**只认那一层**，不存在就 404，不悄悄回退。
    """
    from urllib.parse import parse_qs
    qs = parse_qs(h.path.split("?", 1)[1] if "?" in h.path else "")
    ch = int(qs.get("n", ["0"])[0])
    if ch < 1:
        return 400, {"error": "n 必填且为章节号"}
    layer = (qs.get("layer") or [""])[0]
    try:
        if layer:
            if layer not in ("raw", "checked", "refined"):
                return 400, {"error": "layer 须为 raw / checked / refined"}
            candidates = [layer]
        else:
            candidates = ["refined", "checked", "raw"]
        for d in candidates:
            p = api.ROOT / "data" / "chapters" / d / f"{ch:02d}.md"
            if p.exists():
                text = p.read_text(encoding="utf-8")
                import refine_paragraph
                paras = refine_paragraph.split_paragraphs(text)
                return 200, {
                    "ok": True,
                    "chapter": ch,
                    "layer": d,
                    "count": len(paras),
                    "paragraphs": [
                        {"index": i, "text": t, "chars": len(t)}
                        for i, t in enumerate(paras)
                    ]
                }
        return 404, {"error": f"第 {ch} 章不存在" + (f"（{layer} 层）" if layer else "")}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_paragraph_history(h):
    """获取段落改写历史"""
    from urllib.parse import parse_qs
    qs = parse_qs(h.path.split("?", 1)[1] if "?" in h.path else "")
    ch = int(qs.get("n", ["0"])[0])
    pi = int(qs.get("p", ["-1"])[0])
    if ch < 1 or pi < 0:
        return 400, {"error": "n(>=1)、p(>=0) 必填"}
    try:
        import refine_paragraph
        records = refine_paragraph.list_paragraph_history(ch, pi)
        return 200, {"ok": True, "chapter": ch, "paragraph_index": pi, "count": len(records), "history": records}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_paragraph_diff(h):
    """计算段落 diff：{n, p, h1?, h2?}"""
    from urllib.parse import parse_qs
    qs = parse_qs(h.path.split("?", 1)[1] if "?" in h.path else "")
    ch = int(qs.get("n", ["0"])[0])
    pi = int(qs.get("p", ["-1"])[0])
    h1 = qs.get("h1", [None])[0]
    h2 = qs.get("h2", [None])[0]
    if ch < 1 or pi < 0:
        return 400, {"error": "n(>=1)、p(>=0) 必填"}
    try:
        import refine_paragraph
        import utils.paragraph_diff as pd

        if h1 and h2:
            # 两个历史版本对比
            records = refine_paragraph.list_paragraph_history(ch, pi)
            r1 = next((r for r in records if r.get("id") == int(h1)), None)
            r2 = next((r for r in records if r.get("id") == int(h2)), None)
            if not r1 or not r2:
                return 404, {"error": f"历史版本不存在（h{h1} 或 h{h2}）"}
            diff_ops = pd.char_diff(r1.get("original", ""), r2.get("rewritten", ""))
        else:
            # 当前段落 vs 最新历史版本
            chap_path = None
            for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
                p = api.ROOT / d / f"{ch:02d}.md"   # 经 ROOT（--root 下 CWD 未必跟着换）
                if p.exists():
                    chap_path = p
                    break
            if not chap_path:
                return 404, {"error": f"第 {ch} 章不存在"}
            full_text = chap_path.read_text(encoding="utf-8")
            paras = refine_paragraph.split_paragraphs(full_text)
            if pi >= len(paras):
                return 400, {"error": f"段落 #{pi} 不存在（共 {len(paras)} 段）"}
            current = paras[pi]
            records = refine_paragraph.list_paragraph_history(ch, pi)
            if not records:
                return 404, {"error": "暂无历史记录"}
            latest = records[0]
            diff_ops = pd.char_diff(latest.get("original", ""), current)

        summary = pd.summarize_diff(diff_ops)
        return 200, {"ok": True, "diff": diff_ops, "summary": summary}
    except Exception as e:
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_style_drift(h):
    """风格偏差检测：{n, p} 或 {text_before, text_after}"""
    from urllib.parse import parse_qs
    from utils.style_drift import analyze_paragraph_drift, format_drift_report

    qs = parse_qs(h.path.split("?", 1)[1] if "?" in h.path else "")
    mode = qs.get("mode", ["chapter"])[0]

    if mode == "chapter":
        ch = int(qs.get("n", ["0"])[0])
        pi = int(qs.get("p", ["-1"])[0])
        if ch < 1 or pi < 0:
            return 400, {"error": "n(>=1)、p(>=0) 必填"}
        import refine_paragraph
        chap_path = None
        # ⚠️ 必须经 api.ROOT：`--root <书B>` 时进程 CWD 未必跟着换，
        # 裸相对路径会让这里读到**另一个项目**的章节（不报错、内容却是别人的）。
        for d in ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw"):
            p = api.ROOT / d / f"{ch:02d}.md"
            if p.exists():
                chap_path = p
                break
        if not chap_path:
            return 404, {"error": f"第 {ch} 章不存在"}
        full_text = chap_path.read_text(encoding="utf-8")
        paras = refine_paragraph.split_paragraphs(full_text)
        if pi >= len(paras):
            return 400, {"error": f"段落 #{pi} 不存在"}
        current = paras[pi]
        records = refine_paragraph.list_paragraph_history(ch, pi)
        if not records:
            return 404, {"error": "暂无历史记录，无法对比"}
        latest = records[0]
        original = latest.get("original", "")
        result = analyze_paragraph_drift(original, current)
        result["report_text"] = format_drift_report(result)
        return 200, {"ok": True, **result}
    elif mode == "direct":
        # 直接对比两段文本（POST 用）
        # 这个分支实际由 POST 端点处理
        return 400, {"error": "请用 POST /style/drift"}
    else:
        return 400, {"error": "mode 必须为 chapter 或 direct"}


def handle_style_drift_post(h, body):
    """风格偏差检测（POST 模式）：{text_before, text_after, context_before?, context_after?}"""
    from utils.style_drift import analyze_paragraph_drift, format_drift_report

    text_before = body.get("text_before", "")
    text_after = body.get("text_after", "")
    if not text_before or not text_after:
        return 400, {"error": "text_before 和 text_after 必填"}
    ctx_before = body.get("context_before")
    ctx_after = body.get("context_after")
    result = analyze_paragraph_drift(text_before, text_after, ctx_before, ctx_after)
    result["report_text"] = format_drift_report(result)
    return 200, {"ok": True, **result}


# 本模块负责的端点（供自检与文档）
# ---------------------------------------------------------------- 按钮级派单（engine: self）
# 2026-11-08：作者问“这些按钮如果不能点还有什么意义”。答案是补一条按钮级通道：
# 点一次 → 出自包含任务书 → 外部写成**草稿** → 他点“采用”才换正式稿。
# 每一条都只认盘上事实（草稿在不在、合不合判据），不认“我写完了”这句话。
def handle_self_rewrite_chapter(h, body):
    """`POST /self/action/rewrite_chapter {n, feedback?}` —— 整章重写：出任务书。"""
    body = body or {}
    try:
        n = int(body.get("n") or 0)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "n 必填且为章号（整数）"}
    if not (1 <= n <= 999):
        return 400, {"ok": False, "error": "章号必须是 1~999"}
    try:
        cfg, proj = api.load_all()
        from utils import self_actions as sa
        try:
            info = sa.write_chapter_task(cfg, proj, n, str(body.get("feedback") or ""))
        except FileNotFoundError as e:
            return 400, {"ok": False, "error": str(e)}
        return 202, dict(info, ok=True, mode="self",
                         message="已出第 %d 章的整章重写任务书（不调用模型）：%s。"
                                 "交给外部执行方写到 %s，写完回来点“采用草稿”。"
                                 % (n, info["task"], info["draft"]))
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_self_actions_list(h):
    """`GET /self/actions` —— 有哪些草稿、判据过没过、多大。"""
    try:
        cfg, _ = api.load_all()
        from utils import self_actions as sa
        items = sa.list_drafts(cfg)
        return 200, {"ok": True, "items": items, "count": len(items),
                     "note": "草稿在 data/self_drafts/；采用才换正式稿（先备份旧稿）。"}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_self_action_apply(h, body):
    """`POST /self/action/apply {n}` —— 采用第 n 章的草稿（先备份旧稿）。"""
    body = body or {}
    if body.get("confirm") is not True:
        return 400, {"ok": False, "error": "要用草稿替换正式稿，必须带 confirm: true"}
    try:
        n = int(body.get("n") or 0)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "n 必填且为章号（整数）"}
    try:
        from utils import self_actions as sa
        cfg, _ = api.load_all()
        info = sa.check_chapter_draft(n, cfg)
        if not info.get("ok"):
            return 400, {"ok": False, "error": "草稿没过判据，不能采用：" + "；".join(info["reasons"]),
                         "check": info}
        done = sa.apply_chapter_draft(n)
        return 200, dict(done, ok=True,
                         message="已采用第 %d 章的草稿（旧稿备份到 %s）"
                                 % (n, done.get("backup") or "（原本没有正文）"))
    except FileNotFoundError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_self_action_drop(h, body):
    """`POST /self/action/drop {n}` —— 放弃草稿（正式稿一个字不动）。"""
    body = body or {}
    try:
        n = int(body.get("n") or 0)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "n 必填且为章号（整数）"}
    try:
        from utils import self_actions as sa
        return 200, dict(sa.drop_chapter_draft(n), ok=True)
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 改一段：候选 → 采用（P1-5）
def _para_ids(body):
    try:
        ch = int(body.get("chapter") or 0)
        pi = int(body.get("paragraph_index"))
    except (TypeError, ValueError):
        return None, None, (400, {"ok": False, "error": "chapter(>=1) 与 paragraph_index(>=0) 必填"})
    if ch < 1 or pi < 0:
        return None, None, (400, {"ok": False, "error": "chapter(>=1) 与 paragraph_index(>=0) 必填"})
    return ch, pi, None


def handle_paragraph_preview(h, body):
    """`POST /chapters/paragraph/preview` —— 只算不落：返回 job_id，算完读 pending 看“改前/改后”。

    为什么是 job：模型改一段要几十秒，同步请求会把界面卡住；
    结果不进正文，只进 `data/state/para_pending.json`，等作者点“采用”。
    """
    body = body or {}
    ch, pi, bad = _para_ids(body)
    if bad:
        return bad
    fb = str(body.get("feedback") or "").strip()
    if not fb:
        return 400, {"ok": False, "error": "feedback 必填（要改什么）"}
    try:
        cfg, proj = api.load_all()
        from utils.engine import resolve_engine, needs_model_block_reason
        if resolve_engine(cfg) == "self":
            from utils import self_actions as sa
            try:
                info = sa.write_paragraph_task(cfg, proj, ch, pi, fb)
            except (ValueError, FileNotFoundError) as e:
                return 400, {"ok": False, "error": str(e)}
            return 202, dict(info, ok=True, mode="self",
                             message="自任务模式：已出这一段的改写任务书（不调用模型）：%s。"
                                     "外部写到 %s，写完回来点“采用”。"
                                     % (info["task"], info["draft"]))
        _why = needs_model_block_reason(cfg)
        if _why:
            return 400, {"ok": False, "error": _why}
        client = api._client_for_env(cfg, "polisher")

        def _fn():
            import refine_paragraph as rp
            ok, msg, rec = rp.preview_paragraph(cfg, proj, ch, pi, fb, client=client)
            return ok, (msg if not ok else {"message": msg, "rec": rec})

        jid, err = api.start_job("preview_para_ch%d_p%d" % (ch, pi), _fn)
        if err:
            return 409, {"ok": False, "error": err}
        return 202, {"ok": True, "job_id": jid, "chapter": ch, "index": pi,
                     "message": "正在生成候选（只算不落，正文一个字不动）"}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_paragraph_pending(h):
    """`GET /chapters/paragraph/pending?chapter=3&index=5` —— 读待采用的候选（改前/改后）。"""
    try:
        q = h._query()
        try:
            ch = int(q.get("chapter") or 0)
            pi = int(q.get("index"))
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "chapter 与 index 必填"}
        import refine_paragraph as rp
        rec = rp.get_pending(ch, pi)
        if not rec:
            # self 模式：候选是外部写的草稿文件（不是模型返回的文本）
            from utils import self_actions as sa
            info = sa.check_paragraph_draft(ch, pi)
            if info.get("exists"):
                return 200, {"ok": True, "exists": True, "mode": "self",
                             "rec": {"chapter": ch, "index": pi,
                                     "before": info["before"], "after": info["after"],
                                     "ok": info["ok"], "reasons": info["reasons"]},
                             "note": "这是外部写的草稿（data/self_drafts/），正文尚未改动。"}
            return 200, {"ok": True, "exists": False,
                         "message": "没有待采用的候选（正文就是原样）"}
        # 顺带给出同一个“被谁改过”的证据：这一段是不是还在原位置
        return 200, {"ok": True, "exists": True, "rec": rec,
                     "note": "候选只存在 data/state/para_pending.json，正文尚未改动。"}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_paragraph_apply(h, body):
    """`POST /chapters/paragraph/apply {chapter, paragraph_index, confirm:true}` —— 采用候选。"""
    body = body or {}
    if body.get("confirm") is not True:
        return 400, {"ok": False, "error": "采用会改正文，必须带 confirm: true"}
    ch, pi, bad = _para_ids(body)
    if bad:
        return bad
    try:
        import refine_paragraph as rp
        ok, msg = rp.apply_pending(ch, pi)
        if not ok and "没有待采用的候选" in str(msg):
            from utils import self_actions as sa
            if sa.para_draft_path(ch, pi).is_file():
                ok, msg = sa.apply_paragraph_draft(ch, pi)
        return (200 if ok else 400), {"ok": ok, "message": msg, "error": "" if ok else msg,
                                      "chapter": ch, "index": pi}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_paragraph_drop(h, body):
    """`POST /chapters/paragraph/drop {chapter, paragraph_index}` —— 放弃候选（正文不动）。"""
    body = body or {}
    ch, pi, bad = _para_ids(body)
    if bad:
        return bad
    try:
        import refine_paragraph as rp
        ok, msg = rp.drop_pending(ch, pi)
        from utils import self_actions as sa
        if sa.para_draft_path(ch, pi).is_file():
            _o, _m = sa.drop_paragraph_draft(ch, pi)
            ok, msg = True, "已放弃候选与草稿（正文未改动）"
        return 200, {"ok": ok, "message": msg}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("POST", "/chapters/paragraph/preview", handle_paragraph_preview),
    ("GET", "/chapters/paragraph/pending", handle_paragraph_pending),
    ("POST", "/chapters/paragraph/apply", handle_paragraph_apply),
    ("POST", "/chapters/paragraph/drop", handle_paragraph_drop),
    ("POST", "/self/action/rewrite_chapter", handle_self_rewrite_chapter),
    ("GET", "/self/actions", handle_self_actions_list),
    ("POST", "/self/action/apply", handle_self_action_apply),
    ("POST", "/self/action/drop", handle_self_action_drop),
    ("POST", "/refine/outline", handle_refine_outline),
    ("POST", "/refine/outline/node", handle_refine_outline_node),
    ("POST", "/refine/outline/undo", handle_refine_outline_undo),
    ("POST", "/refine/chapter", handle_refine_chapter),
    ("POST", "/refine/paragraph", handle_refine_paragraph),
    ("POST", "/outline/field/candidates", handle_field_candidates),
    ("POST", "/outline/field/submit", handle_field_submit),
    ("POST", "/outline/field/current", handle_field_current),
    ("POST", "/outline/sample/candidates", handle_branch_candidates),
    ("POST", "/outline/sample/submit", handle_branch_submit),
    ("POST", "/outline/sample/current", handle_branch_current),
    ("POST", "/chapters/paragraph/restore", handle_paragraph_restore),
    ("POST", "/chapters/paragraph/save", handle_paragraph_save),
    ("POST", "/style/drift", handle_style_drift_post),
    ("GET", "/chapters/paragraphs", handle_chapters_paragraphs),
    ("GET", "/chapters/paragraph/history", handle_paragraph_history),
    ("GET", "/chapters/paragraph/diff", handle_paragraph_diff),
    ("GET", "/style/drift", handle_style_drift),
)
