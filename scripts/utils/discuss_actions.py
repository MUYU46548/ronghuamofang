# -*- coding: utf-8 -*-
"""讨论产出的“动作建议”：AI 说改哪里 → 作者点一次才进候选区（C5/C14，2026-11-16）。

## 为什么要有这一层

C1/C2 之后 AI 能说一轮，`.ai.json` 里也带了 `action{kind,target,why}`。但**没有任何消费者**：
`grep -rn "ai.json" scripts/ | grep -v discuss_ai` 零命中。于是 AI 说"第 12 章第 3 段该收住"，
作者还得自己翻到那一段、自己组织一遍意见、自己点重写 —— 讨论与动作之间还是断的。

本模块补两件事：

1. **建议落盘**：每轮 `action.kind != none` 时存成一条**建议**（`<主题>.actions.json`），
   带状态 `pending / started / dismissed`。建议**不是队列**：它不会自己触发任何模型调用。
2. **锚点校验（C14）**：段落序号会漂 —— 正文一改，`para_index=3` 可能指到别的段。
   所以每条建议同时记 `quote` 与 `para_hash`，应用前按原文/指纹重新定位：
   找不回来就直说"这一段已经不在原位"，绝不拿段号硬指（`utils/annotations.py`
   的 `relocate` 是同一族判据，这里复用它的口径：精确命中 → 指纹命中 → missing）。

## 与"候选区"的关系

作者点"按这条生成候选"时，本模块**不写正文**，只调用
`/chapters/paragraph/preview` 同一条实现（`refine_paragraph.preview_paragraph`）：
候选进 `data/state/para_pending.json`，正文一个字不动，作者再点"采用"才换稿。
整章重写（`rewrite_chapter`）代价大得多，必须显式 `confirm`，且旧稿会先备份。

## 数据形状（写死）

    data/discuss/03/结局要不要开信.actions.json
    {
      "chapter": 3,
      "topic": "结局要不要开信",
      "items": [
        {"id": "act_...", "round": 7, "kind": "refine_paragraph",
         "target": {"chapter": 3, "para_index": 3, "para_hash": "…", "quote": "…"},
         "why": "…", "at": "2026-11-16T…", "status": "pending"}
      ]
    }

文件名带 `.actions.json` 后缀，`discussion._is_derived_json` 会把它排除在讨论列表之外
（否则讨论清单里会凭空多出没有轮次的条目）。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from utils import discussion

KINDS_WITH_ACTION = ("suggest_edit", "refine_paragraph", "rewrite_chapter")
HASH_LEN = 12


# ---------------------------------------------------------------- 基础
def _norm(text: str) -> str:
    """比较用归一：只留汉字/字母/数字（与 `annotations._norm` 同一口径）。"""
    try:
        from utils.homogeneity import cjk_only
        return cjk_only(text or "")
    except Exception:                                          # noqa: BLE001
        return re.sub(r"\s+", "", str(text or ""))


def para_hash(text: str) -> str:
    """段落指纹。为什么不用段号：正文一改，段号就会指到别的段落（C14 的全部理由）。"""
    return hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()[:HASH_LEN]


def _layer_paragraphs(chapter, root="."):
    """这一章最接近成品那一层的 (段落列表, 层名)。层名给批注用 —— 批注绑层，串层会改错段。"""
    try:
        from utils import discuss_ai
        body, layer = discuss_ai.chapter_body(chapter, root)
    except Exception:                                          # noqa: BLE001
        body, layer = "", ""
    if not body:
        return [], layer or ""
    try:
        import refine_paragraph
        return (refine_paragraph.split_paragraphs(body) or []), layer
    except Exception:                                          # noqa: BLE001
        return [p.strip() for p in body.split("\n\n") if p.strip()], layer


def chapter_paragraphs(chapter, root="."):
    """这一章最接近成品那一层的段落列表（与 `discuss_ai.chapter_body` 同源）。"""
    return _layer_paragraphs(chapter, root)[0]


def actions_path(chapter, topic, root="."):
    md, _js = discussion.thread_paths(chapter, topic, root)
    return md.with_suffix(".actions.json")


def _read(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, data: dict) -> None:
    from utils.file_io import write_text
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text(p, json.dumps(data, ensure_ascii=False, indent=2))


def _new_id(chapter, round_no, kind) -> str:
    raw = "%s|%s|%s|%s" % (chapter, round_no, kind, time.time())
    return "act_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------- 锚点（C14）
def _locate(paras, nq, old_idx=None):
    """在段落里找引文 → 返回段号；找不到返回 None。

    三档，越往下越宽松（**只在同一章正文内比**，不跨章、不猜）：
    1. 整段完全相同（最常见，也最可靠）；
    2. 互相包含：引文是某段的一部分（模型只引了半句），或者某段整个在引文里
       —— 后者是**引文跨了标题行**的实况（`"第 03 章 雨夜\\n\\n他推门进来…"`
       会被模型当成一整句引下来），此时取最长的那个段；
    3. 前 12 字在段里出现（尾部被模型截断）。
    """
    if not nq:
        return None
    norms = [_norm(p) for p in paras]
    exact = [i for i, n in enumerate(norms) if n and n == nq]
    if exact:
        return old_idx if old_idx in exact else exact[0]
    sub = [i for i, n in enumerate(norms)
           if n and ((len(nq) >= 8 and nq in n) or (len(n) >= 12 and n in nq))]
    if sub:
        # 段号仍在候选里就用它（模型给的段号大多是对的，别主动改）
        if old_idx in sub:
            return old_idx
        return max(sub, key=lambda i: len(norms[i]))
    if len(nq) >= 12:
        head = nq[:12]
        for i, n in enumerate(norms):
            if head in n:
                return i
    return None


def capture_anchor(chapter, target, root="."):
    """把模型给的 target 变成**可核对的锚点**：补上 quote 对应的 para_hash。

    模型常常只给 `para_index`（甚至给错）。这里以**正文为准**：
    - 有 quote 且能在正文里逐段对上 → 以原文那一段为准，段号纠正为真实位置；
    - quote 对不上 → 保留模型给的段号与 quote，但 `para_hash` 留空、
      `unverified=True`（应用时会被拒，而不是"猜一段"改下去）。
    """
    target = dict(target or {})
    try:
        idx = int(target.get("para_index"))
    except (TypeError, ValueError):
        idx = None
    quote = str(target.get("quote") or "").strip()
    paras = chapter_paragraphs(chapter, root) if (quote or idx is not None) else []
    out = {"chapter": int(chapter), "para_index": idx, "quote": quote,
           "para_hash": "", "unverified": False}

    nq = _norm(quote)
    if nq:
        pick = _locate(paras, nq, idx)
        if pick is not None:
            out["para_index"] = pick
            out["para_hash"] = para_hash(paras[pick])
            return out
        out["unverified"] = True
        return out
    if idx is not None and 0 <= idx < len(paras):
        out["para_hash"] = para_hash(paras[idx])
        out["quote"] = (paras[idx] or "").strip()[:120]
        return out
    out["unverified"] = True
    return out


def resolve_anchor(chapter, target, root="."):
    """应用前重新定位：返回 {ok, index, drift, why}。

    三档（与 `annotations.relocate` 同族）：
    - quote 命中 → ok，段号以真实位置为准；与原段号不同则 drift=True；
    - quote 对不上但 para_hash 命中 → ok + drift=True（这一段被挪过位置）；
    - 都找不到 → ok=False，why = "这一段已经不在原位"。
    """
    target = dict(target or {})
    paras, layer = _layer_paragraphs(chapter, root)
    if not paras:
        return {"ok": False, "index": None, "drift": False, "layer": layer,
                "why": "这一章还没有正文，没有段落可定位"}
    try:
        old_idx = int(target.get("para_index"))
    except (TypeError, ValueError):
        old_idx = None
    quote = str(target.get("quote") or "").strip()
    nq = _norm(quote)
    if nq:
        pick = _locate(paras, nq, old_idx)
        if pick is not None:
            return {"ok": True, "index": pick, "drift": pick != old_idx, "layer": layer,
                    "why": "" if pick == old_idx else
                           "这一段被挪过位置（现在在第 %d 段）" % (pick + 1)}
    want = str(target.get("para_hash") or "").strip()
    if want:
        for i, p in enumerate(paras):
            if para_hash(p) == want:
                return {"ok": True, "index": i, "drift": True, "layer": layer,
                        "why": "引用的原句已改，按段落指纹找回到第 %d 段" % (i + 1)}
    return {"ok": False, "index": None, "drift": False, "layer": layer,
            "why": "这一段已经不在原位（引用的原句在这一层里找不到，可能已被改写或删除）"}


# ---------------------------------------------------------------- 建议存取
def record_suggestion(chapter, topic, payload, root="."):
    """一轮 AI 发言之后调用：`action.kind != none` 就存一条**建议**（不触发任何调用）。

    返回建议 dict；没有动作或存不下返回 None。
    """
    action = (payload or {}).get("action") or {}
    kind = str(action.get("kind") or "none").strip().lower()
    if kind not in KINDS_WITH_ACTION:
        return None
    chapter = int(chapter)
    target = capture_anchor(chapter, action.get("target"), root)
    p = actions_path(chapter, topic, root)
    data = _read(p) or {"chapter": chapter, "topic": topic, "items": []}
    items = list(data.get("items") or [])

    round_no = int((payload or {}).get("round") or 0)
    # 幂等：同一轮同一种动作只存一条（自任务两次收编 / 重跑同一轮都不该翻倍）
    if any(int(x.get("round") or 0) == round_no and x.get("kind") == kind for x in items):
        return None
    item = {
        "id": _new_id(chapter, round_no, kind),
        "round": round_no,
        "kind": kind,
        "target": target,
        "why": str(action.get("why") or "").strip(),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "pending",
    }
    items.append(item)
    data.update({"chapter": chapter, "topic": topic, "items": items})
    _write(p, data)
    return item


def list_suggestions(chapter=None, root=".", status=None, topic=None):
    """列出建议（零 token）。chapter 为空 = 全书。"""
    base = Path(root) / discussion.DISCUSS_DIR
    out = []
    if not base.is_dir():
        return out
    dirs = [base / ("%02d" % int(chapter))] if chapter else sorted(
        [d for d in base.iterdir() if d.is_dir() and d.name.isdigit()])
    for d in dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.actions.json")):
            data = _read(f)
            for it in data.get("items") or []:
                if not isinstance(it, dict):
                    continue
                if topic and str(it.get("topic") or data.get("topic")) != topic:
                    continue
                if status and str(it.get("status")) != status:
                    continue
                row = dict(it)
                row.setdefault("chapter", data.get("chapter") or int(d.name))
                row.setdefault("topic", data.get("topic") or "")
                out.append(row)
    return sorted(out, key=lambda x: (int(x.get("chapter") or 0), x.get("at") or ""))


def get_suggestion(chapter, topic, sid, root="."):
    for it in (_read(actions_path(chapter, topic, root)).get("items") or []):
        if str(it.get("id")) == str(sid):
            return dict(it, chapter=int(chapter), topic=topic)
    return None


def set_status(chapter, topic, sid, status, root=".", **extra):
    """改建议状态（pending / started / annotated / dismissed）。返回更新后的 dict 或 None。"""
    p = actions_path(chapter, topic, root)
    data = _read(p)
    hit = None
    for it in data.get("items") or []:
        if str(it.get("id")) == str(sid):
            it["status"] = str(status)
            it.update(extra)
            hit = dict(it, chapter=int(chapter), topic=topic)
    if hit:
        _write(p, data)
    return hit


def annotate_suggestion(chapter, topic, sid, root="."):
    """把一条建议落成**段落批注**（2026-11-17，讨论 P0）：正文一个字不动。

    与“按这条生成候选”的分工：候选会花一次模型调用、结果要作者采用；
    批注只是把“讨论里说要改这里”落到段落上，之后可在批注列表点“改这一处”，
    走的是同一条读改链（预览 → 采用 → 按批注重算）。

    返回 `(ok, msg, annotation, anchor)`。定位失败**绝不猜段号**。
    """
    from utils import annotations as ann

    sug = get_suggestion(chapter, topic, sid, root)
    if not sug:
        return False, "找不到这条建议（可能已被处理）", None, None
    if str(sug.get("status")) == "dismissed":
        return False, "这条建议已经被你放弃了", None, None
    if sug.get("annotation_id"):
        return (False, "这条建议已经落成批注（%s）" % sug["annotation_id"],
                None, None)
    anchor = resolve_anchor(chapter, sug.get("target"), root)
    if not anchor.get("ok"):
        return False, anchor.get("why") or "这一段已经不在原位", None, anchor
    paras, layer = _layer_paragraphs(chapter, root)
    idx = int(anchor.get("index") or 0)
    if idx < 0 or idx >= len(paras):
        return False, "定位到的段号超出正文范围（本章 %d 段）" % len(paras), None, anchor
    note = str(sug.get("why") or "").strip() or "讨论里点出的这一处"
    note = "%s\n来源：讨论《%s》第 %s 轮" % (note, topic, sug.get("round") or "?")
    item = ann.add(root, chapter, note, idx, paras, layer=layer or "refined")
    set_status(chapter, topic, sid, "annotated", root=root,
               annotation_id=item["id"], anchor_index=idx,
               annotated_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    return (True, "已落成批注（第 %d 段，%s 层）—— 正文没动；要改就去批注列表点“改这一处”"
            % (idx + 1, layer or "refined"), item, anchor)
