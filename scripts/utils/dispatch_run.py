# -*- coding: utf-8 -*-
"""派单执行：**一次调用改多段**（2026-11-28，总账 F7 的最后一环）。

## 这一层补的是什么

F7 的前两环已经齐了：`dispatch.save` 记候选、`apply` 走 `save_paragraph_text`
采用（与手改同一条回写路径），界面逐条显示。缺的是**中间那次模型调用由谁跑**。

历史上“谁跑这次调用”被当成一个待拍板的问题（后端直连 vs 外部 DSH 回写），
卡了两周。本文的选择是：**墨坊自己跑**，理由是判据本来就该只有一份 ——
派单与段落精修用的是同一个 `client`（同一个 `make_client(cfg, "polisher")`），
外部 DSH 回写那条路仍然可用（`POST /chapters/dispatch action=save` 没动），
只是不再是唯一入口。

作者 2026-11-25 定的口径：**分次调用会让成本增加三五倍**。所以这里一次调用
把 N 段一起发出去，不逐段调用。

## 为什么产出不直接写正文

一次改多段，中间某段跑偏就得整批回滚。所以产出先落候选（`dispatch.save`），
作者逐条看过再采用 —— 采用才碰正文，且走的是与手改同一条路径（有历史版本）。

## 段号解析为什么这么严

模型最容易犯的错是把段号写变形（`#7`、`7.`、`第7段`）。解析用宽松正则吸进来，
但**只认任务书里列出的那些段号**：多出来的段一概丢弃并记进 `rejected`，
少掉的段标成 `missing`。宁可这一批少采用几段，也不许把 A 段的改写写到 B 段上 ——
那是会毁正文的错，且作者很难看出来。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: 一次派单最多几段。上限的依据是输出预算：每段改写 ~1.5 倍原长，40k max_tokens
#: 装得下约 20 条约 500 字的段；再多就会撞截断（截断 = 整批作废，见 _finish）。
MAX_SEGMENTS_PER_CALL = 20

#: 模型输出里“这一段开始了”的标记。宽松匹配：`===SEGMENT: 7===` 的各种变形。
_SEG_RE = re.compile(r"^[ \t]*===+\s*SEGMENT\s*[:：]\s*#?\s*(\d+)\s*===+[ \t]*$",
                     re.IGNORECASE | re.MULTILINE)


def parse_segments(raw: str, allowed: List[int]) -> Tuple[Dict[int, str], List[int], List[int]]:
    """把模型输出切成 {段号: 正文}。

    返回 (taken, missing, extra)：
      - taken: 段号 → 改写后的正文（只含 allowed 里的段号）
      - missing: allowed 里模型没给的段号
      - extra: 模型给了但不在 allowed 里的段号（会被丢弃）

    **不合并、不猜**：段号对不上就是 discarded，绝不把内容塞给邻近的段。
    """
    allowed_set = set(int(x) for x in allowed)
    marked = list(_SEG_RE.finditer(raw or ""))
    taken: Dict[int, str] = {}
    extra: List[int] = []

    for i, m in enumerate(marked):
        try:
            seg = int(m.group(1))
        except (TypeError, ValueError):
            continue
        start = m.end()
        end = marked[i + 1].start() if i + 1 < len(marked) else len(raw)
        body = (raw or "")[start:end].strip()
        # 去掉模型可能自带的包裹：代码围栏 / 引号
        body = _strip_wrapping(body)
        if not body:
            continue
        if seg in allowed_set:
            taken[seg] = body
        else:
            extra.append(seg)

    missing = sorted(allowed_set - set(taken))
    return taken, missing, sorted(set(extra))


def _strip_wrapping(text: str) -> str:
    """剥掉代码围栏与整体包裹的引号；不动正文内部。"""
    t = (text or "").strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines:
            lines = lines[1:]
        # 结尾围栏
        while lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    # 整体被中文/英文双引号包住（模型爱干这个）
    # 直角引号用码位写：这是**要匹配的字符**，不是文档用词 ——
    # 仓库硬纪律禁止源码出现字面直角引号（质量门禁会拦），用转义既守纪律又能剥。
    for lq, rq in (("\u201c", "\u201d"), ('"', '"'), ("\u300c", "\u300d")):
        if len(t) > 2 and t.startswith(lq) and t.endswith(rq):
            inner = t[len(lq):-len(rq)]
            # 只有在内部没有同名引号时才算"整体包裹"，否则可能只是正文带引号
            if lq not in inner and rq not in inner:
                t = inner.strip()
            break
    return t


def build_segments_block(paragraphs: List[str], indexes: List[int]) -> str:
    """把待改段落拼成任务书里的 `{{segments}}` 段。"""
    chunks = []
    for idx in indexes:
        if idx < 0 or idx >= len(paragraphs):
            continue
        text = paragraphs[idx]
        chunks.append("===SEGMENT: %d===\n%s" % (idx, text))
    return "\n\n".join(chunks)


def build_dispatch_task(cfg, proj, chapter: int, paragraphs: List[str],
                        indexes: List[int], notes: Optional[Dict[int, str]] = None,
                        chap_path: Optional[str] = None,
                        outline_path: Optional[str] = None,
                        root: Optional[str] = None) -> str:
    """生成批量派单任务书正文（模型一次读、一次答）。

    `notes` 是 {段号: 作者这条修改意见}，可不给 —— 不给时模型按通用原则改。
    """
    from utils.template_loader import load_template
    from utils.file_io import read_text

    base = Path(root) if root else Path(".")
    current_full = ""
    if chap_path:
        try:
            current_full = read_text(Path(chap_path))
        except Exception:                             # noqa: BLE001
            current_full = ""

    seg_lines = []
    for idx in indexes:
        if idx < 0 or idx >= len(paragraphs):
            continue
        text = paragraphs[idx]
        note = ""
        if notes:
            note = str(notes.get(idx) or notes.get(str(idx)) or "").strip()
        if note:
            seg_lines.append("===SEGMENT: %d===\n%s\n\n修改意见：%s" % (idx, text, note))
        else:
            seg_lines.append("===SEGMENT: %d===\n%s" % (idx, text))

    _meta, body = load_template("stage4_dispatch_batch.md", {
        "chapter": chapter,
        "count": len(indexes),
        "path_outline": str(Path(outline_path).resolve()) if outline_path else "(未提供)",
        "path_setting": str((base / "data" / "setting" / "setting.json").resolve()),
        "segments": "\n\n".join(seg_lines),
        # 整章会很长，不放进 variables 让 load_template 做字符串替换（那会拼两次），
        # 先给空占位消掉"残留占位符"告警，再单独替换成正文。
        "current_chapter": "",
    })
    body = body.replace("{{current_chapter}}", current_full)
    return body


def run_batch(cfg, proj, chapter: int, indexes: List[int],
              notes: Optional[Dict[int, str]] = None,
              client=None, root: Optional[str] = None,
              task_dir: str = "data/state/tasks",
              layer: str = "refined", dry_run: bool = False) -> Dict[str, Any]:
    """一次调用改多段；产出落成候选，不碰正文。

    返回 {ok, message, items, missing, extra, tokens_out, requests}。
    `ok=False` 时正文与候选都不动（宁可这一批白跑，也不许半截候选进库）。
    """
    from utils.file_io import read_text
    from utils.llm_client import make_client

    base = Path(root) if root else Path(".")
    indexes = [int(i) for i in (indexes or [])]
    if not indexes:
        return {"ok": False, "message": "没有要改的段（indexes 为空）", "items": []}
    if len(indexes) > MAX_SEGMENTS_PER_CALL:
        return {"ok": False,
                "message": "一次最多 %d 段（给了 %d 段）—— 分两批，别撞截断"
                           % (MAX_SEGMENTS_PER_CALL, len(indexes)),
                "items": []}

    # 章节正文
    chap_path = _pick_chapter_path(chapter, root)
    if chap_path is None:
        return {"ok": False, "message": "第 %d 章不存在（raw/checked/refined 均无）" % chapter,
                "items": []}
    full_text = read_text(chap_path)
    paragraphs = _split_paragraphs(full_text)

    bad = [i for i in indexes if i < 0 or i >= len(paragraphs)]
    if bad:
        return {"ok": False,
                "message": "段号越界：第 %d 章共 %d 段，越界 %s"
                           % (chapter, len(paragraphs), bad),
                "items": []}

    outline_dir = base / "data" / "outline" / "chapters"
    outset = outline_dir / ("%02d.md" % chapter)
    if not outset.exists():
        outset = base / "data" / "outline" / "global.md"

    body = build_dispatch_task(cfg, proj, chapter, paragraphs, indexes, notes=notes,
                               chap_path=str(chap_path),
                               outline_path=str(outset) if outset.exists() else None,
                               root=str(base))

    client = client or make_client(cfg, "polisher")
    task_file = client.write_task(task_dir,
                                  "stage4_dispatch_ch%02d.md" % chapter, body)
    if dry_run:
        return {"ok": True, "message": "dry-run：任务书已生成，未调用模型",
                "items": [], "task": str(task_file)}

    result = client.run_task(task_file)
    if result.get("exit_code") != 0:
        # 截断尤其要拦住：finish_reason=length 意味着最后几段可能缺，整批不可信
        why = result.get("error") or result.get("stdout_tail") or ""
        if result.get("finish_reason") == "length":
            why = "输出被截断（finish_reason=length）—— 整批作废，可减少段数后重试"
        return {"ok": False, "message": "模型调用失败：" + str(why)[:300],
                "items": [], "tokens_out": int(result.get("tokens_out") or 0)}

    raw = result.get("text") or result.get("content") or ""
    if not raw:
        # 有的引擎把回复写在 stdout_tail
        raw = result.get("stdout_tail") or ""

    taken, missing, extra = parse_segments(raw, indexes)
    if not taken:
        return {"ok": False,
                "message": "模型回复里没有认出任何段（期望段号 %s）—— 未写入候选"
                           % indexes,
                "items": [], "raw_tail": raw[-400:]}

    items = []
    for seg in sorted(taken):
        items.append({
            "id": "d%d_%d" % (seg, chapter),
            "para": seg,
            "note": (notes or {}).get(seg) or (notes or {}).get(str(seg)) or "",
            "before": paragraphs[seg],
            "after": taken[seg],
            "status": "open",
        })

    from utils import dispatch as dsp
    dsp.save(base, chapter, items, layer=layer, source="dispatch")

    msg = "第 %d 章已生成 %d 条候选（未碰正文，等逐条采用）" % (chapter, len(items))
    if missing:
        msg += "；缺 %d 段 %s" % (len(missing), missing)
    if extra:
        msg += "；丢弃越界段号 %s" % extra
    return {"ok": True, "message": msg, "items": items,
            "missing": missing, "extra": extra,
            "tokens_out": int(result.get("tokens_out") or 0),
            "requests": int(result.get("requests") or 1)}


# ------------------------------------------------------------------ 小工具
# 这两个刻意不 import refine_paragraph：那个模块 import 时会拉起 make_client 等
# 一串重依赖，派单只需要"按层挑文件"和"按空行切段"两件小事，判据也简单到
# 不值得共享 —— 共享反而让两边互相拖死（改一边要验另一边）。

def _pick_chapter_path(chapter: int, root=None) -> Optional[Path]:
    """按 refined > checked > raw 的优先级挑本章文件。"""
    base = Path(root) if root else Path(".")
    for layer in ("refined", "checked", "raw"):
        d = base / "data" / "chapters" / layer
        if not d.is_dir():
            continue
        hit = sorted(d.glob("ch%02d*.md" % chapter)) or sorted(d.glob("第%02d章*.md" % chapter))
        if hit:
            return hit[0]
    return None


def _split_paragraphs(text: str) -> List[str]:
    """按空行切段 —— 与 refine_paragraph.split_paragraphs 同一口径（空行分隔）。"""
    import re as _re
    parts = _re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n"))
    return [p.strip() for p in parts if p.strip()]
