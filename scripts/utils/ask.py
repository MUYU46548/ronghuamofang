# -*- coding: utf-8 -*-
"""要作者定的事（question）：判据只给结论，这里给选项。

## 为什么要有这一层

用户 2026-11-11 的判词："判据输出只有结论，无'要问作者什么/选项/默认'。
界面因此只能显示通过或不通过。"

决定 ②（界面决定、对话干活）要落地，界面就得是**作答位**：一句话问清、几个选项能点、
默认值写明白。红绿灯不是作答位。

## 唯一落点

`data/state/questions.json` —— **与 2026-10-31 的 Agent 提问通道同一个文件、同一个形状**
（`{"items": [...]}`）。刻意不另开一份：两个写点写同一个文件会互相覆盖，而"两条通道"
最后一定是老的没人答、新的没人看。

一条问题就是一个可读的字典：

```
{id, at, question, context, options[字符串 或 {value,label,note}], default,
 effect, source, stage, chapter, status(pending/answered), answer, answered_at/by}
```

两条纪律（与产物闸同源）：

- **id 稳定**：判据反复生成同一条问题是 upsert，不刷屏；没有 id 的（agent 顺手一问）
  按时间戳生成，与旧通道一致。
- **答案绑内容**：题干 / 选项 / 默认值 / 效果变了 → 旧答案作废并重开。拿旧答案套新问题，
  和拿旧批准套新稿是同一类错。

## 答了会怎样（`effect`）

- `rewrite_chapter` + 选到 `rewrite` → 该章进 `data/state/rewrite_queue.json`，
  下次阶段 4 把它当"待写"重出任务书；新版本被批准合成后自动出队
  （见 `stage4_writing._promote_chapter_candidates`）。选到 `keep` 则出队。
- `refine_paragraph`（C8，2026-11-16）+ 选到要改 → 在**那一段**挂一条批注，
  内容 = 问题 + 你的选择。批注是"该改这一段"的句柄：到精修页签点它就能生成候选
  （正文不动）。不自动调模型 —— 答题不该顺手花钱。
- `open_discuss`（C8）+ 任意作答 → 按 `target.chapter` + `topic` 开一篇讨论，
  把问题作为"待解决"，把你的选择作为第一轮作者发言。讨论面板里接着聊。
- 其余情况只记录（v1 边界，见 `docs/问题总账.md` 第 6 条）。
"""
import hashlib
import json
import time
import uuid
from pathlib import Path

STATE_REL = "data/state/questions.json"
QUEUE_REL = "data/state/rewrite_queue.json"

#: 上限：这是"待办"不是日志，塞满 200 条说明没人看。
MAX_ITEMS = 200

#: 一条问题的默认选项（给"只问一句"的调用方用，免得每个调用方自己编）。
DEFAULT_OPTIONS = [
    {"value": "yes", "label": "行", "note": ""},
    {"value": "no", "label": "不行", "note": ""},
]


def state_path(root):
    return Path(root) / STATE_REL


def queue_path(root):
    return Path(root) / QUEUE_REL


def _read_json(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def load(root):
    """读整份清单。坏文件当空清单 —— 问题丢了只是不方便，不该让界面 500。"""
    doc = _read_json(state_path(root), {"items": []})
    items = [x for x in (doc.get("items") or []) if isinstance(x, dict)]
    return {"items": items}


def _save(root, doc):
    items = [x for x in (doc.get("items") or []) if isinstance(x, dict)]
    _write_json(state_path(root), {"items": items[-MAX_ITEMS:]})


def _sig(item):
    payload = json.dumps([item.get("question") or "",
                          item.get("context") or "",
                          item.get("options") or [],
                          item.get("default") or "",
                          item.get("effect") or "",
                          item.get("target") or {},
                          item.get("topic") or ""],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _new_id():
    return "q_%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:4])


def _option_values(item):
    """该题可选的 value（选项既可能是字符串，也可能是 {value,label,note}）。"""
    out = []
    for o in (item.get("options") or []):
        v = str((o.get("value") if isinstance(o, dict) else o) or "").strip()
        if v:
            out.append(v)
    return out


def upsert(root, item, by="system"):
    """登记一条问题（幂等）。返回 `(id, changed)`。

    已答过且内容没变 → 原样保留；内容变了 → 重开（答案清空）。
    """
    item = dict(item or {})
    qid = str(item.get("id") or "").strip() or _new_id()
    question = str(item.get("question") or "").strip()
    if not question:
        return qid, False
    options = item.get("options") if item.get("options") else list(DEFAULT_OPTIONS)
    if not isinstance(options, list):
        return qid, False
    payload = {
        "id": qid,
        "question": question,
        "context": str(item.get("context") or ""),
        "options": options,
        "default": str(item.get("default") or "").strip(),
        "effect": str(item.get("effect") or ""),
        "source": str(item.get("source") or "manual"),
        "stage": item.get("stage"),
        "chapter": item.get("chapter"),
        # C8（2026-11-16）：effect 需要落到具体位置时用它 ——
        # refine_paragraph 要段落号，open_discuss 要主题名。
        "target": item.get("target") if isinstance(item.get("target"), dict) else {},
        "topic": str(item.get("topic") or ""),
    }
    if not payload["default"]:
        vals = _option_values(payload)
        payload["default"] = vals[0] if vals else ""
    sig = _sig(payload)
    doc = load(root)
    prev = next((x for x in doc["items"] if x.get("id") == qid), None)
    if prev and prev.get("sig") == sig and prev.get("status") in ("pending", "answered"):
        return qid, False          # 同一件事、没变过：不重开、不刷屏
    base = dict(prev or {})
    if base.get("status") == "answered":
        # 内容变了：旧答案作废（不然就是"拿旧答案套新问题"）
        base = {"id": qid}
    base.update(payload)
    base.update({
        "sig": sig,
        "status": "pending",
        "answer": None,
        "answered_at": None,
        "answered_by": None,
        "at": base.get("at") or _now(),
        "asked_by": base.get("asked_by") or str(by),
    })
    if prev:
        doc["items"] = [base if x.get("id") == qid else x for x in doc["items"]]
    else:
        doc["items"].append(base)
    _save(root, doc)
    return qid, True


def open_items(root):
    return [x for x in load(root)["items"] if x.get("status") == "pending"]


def answered_items(root, since=""):
    items = [x for x in load(root)["items"] if x.get("status") == "answered"]
    if since:
        items = [x for x in items if str(x.get("answered_at") or "") >= since]
    return items


def find(root, qid):
    return next((x for x in load(root)["items"] if x.get("id") == qid), None)


def answer(root, qid, answer_text=None, value=None, by="gui", note=""):
    """作者作答。返回 `(ok, msg, item)`。

    - `value`：选了哪个选项（给了选项的题用这个，避免"答了一句话但不知道算哪个"）。
    - `answer_text`：自由回答（老通道的用法，保留）。

    **可以改主意**，但只有带 `effect` 的题（决定型）允许改；纯提问（agent 问一句）
    仍是一次性的 —— 否则 agent 读到的"回答"可能已经被改过三遍。
    """
    qid = str(qid or "").strip()
    value = str(value or "").strip()
    text = str(answer_text or "").strip()
    if not qid:
        return False, "id 必填（要答哪一条）", None
    item = find(root, qid)
    if not item:
        return False, "没有这条问题：%s" % qid, None
    if item.get("status") == "answered" and not item.get("effect"):
        return False, "这条已经回答过了", item
    opts = _option_values(item)
    if value:
        if opts and value not in opts:
            return False, "这个选项不在本题里：%s（可选：%s）" % (value, "、".join(opts)), item
        text = value
    if not text:
        return False, "answer 不能为空", item
    doc = load(root)
    for it in doc["items"]:
        if it.get("id") != qid:
            continue
        it["status"] = "answered"
        it["answer"] = text
        it["answered_at"] = _now()
        it["answered_by"] = str(by)
        if note:
            it["note"] = str(note)
        break
    _save(root, doc)
    item = find(root, qid) or item
    _apply_effect(root, item, text)
    return True, "已回答（Agent 下次读 /ask/answers 就能看到）", item


def _chapter_paragraphs(root, chapter):
    """取这一章最接近成品那一层的段落（与段落精修同一套切分）。"""
    from utils.file_io import read_text
    from refine_paragraph import split_paragraphs
    for layer in ("refined", "checked", "raw"):
        p = Path(root) / "data" / "chapters" / layer / ("%02d.md" % int(chapter))
        if p.exists():
            try:
                return split_paragraphs(read_text(p)), layer
            except OSError:
                continue
    return [], ""


def _apply_effect(root, item, chosen):
    """把答案变成动作（C8，2026-11-16 起三种）。

    - `rewrite_chapter`：进重写队列（原有）；
    - `refine_paragraph`：在那一段挂批注（候选由作者点，答题不花钱）；
    - `open_discuss`：开一篇讨论，把你的选择作为第一轮作者发言。
    """
    effect = str(item.get("effect") or "")
    if effect == "open_discuss":
        _effect_open_discuss(root, item, chosen)
        return
    if effect == "refine_paragraph":
        _effect_refine_paragraph(root, item, chosen)
        return
    if effect == "annotate_unreadable":
        _effect_annotate_unreadable(root, item, chosen)
        return
    if effect != "rewrite_chapter":
        return
    try:
        chapter = int(item.get("chapter") or 0)
    except (TypeError, ValueError):
        return
    if chapter <= 0:
        return
    chosen = str(chosen or "").strip()
    if chosen == "rewrite" or chosen.startswith("重写"):
        add_rewrite(root, chapter, reason=item.get("question") or "")
    elif chosen in ("keep", "later") or chosen.startswith("保留") or chosen.startswith("先放"):
        clear_rewrites(root, [chapter])


def _effect_refine_paragraph(root, item, chosen):
    """答案 → 那一段上的一条批注（带段号与原句，改不改由作者点）。"""
    target = item.get("target") or {}
    try:
        chapter = int(target.get("chapter") or item.get("chapter") or 0)
        idx = int(target.get("paragraph_index"))
    except (TypeError, ValueError):
        return
    if chapter <= 0 or idx < 0:
        return
    paras, layer = _chapter_paragraphs(root, chapter)
    if not paras or idx >= len(paras):
        return
    note = "作者已定：%s → %s" % (str(item.get("question") or "")[:80], str(chosen)[:60])
    try:
        from utils import annotations as ann
        ann.add(root, chapter, note, idx, paras, layer=layer)
    except (ValueError, OSError):
        return


def _effect_open_discuss(root, item, chosen):
    """答案 → 开一篇讨论（问题成"待解决"，选择成第一轮作者发言）。"""
    target = item.get("target") or {}
    try:
        chapter = int(target.get("chapter") or item.get("chapter") or 0)
    except (TypeError, ValueError):
        return
    if chapter <= 0:
        return
    question = str(item.get("question") or "").strip()
    topic = str(item.get("topic") or "").strip() or (question[:24] or "待定")
    try:
        from utils import discussion
        discussion.open_thread(chapter, topic, question, root=root)
        discussion.append_round(chapter, topic, "author", str(chosen or ""), root=root)
    except OSError:
        return


def summary(root):
    items = load(root)["items"]
    open_n = len([x for x in items if x.get("status") == "pending"])
    return {"open": open_n, "answered": len(items) - open_n, "rewrites": queue_rewrites(root)}


# ---------------------------------------------------------------- 重写队列

def queue_rewrites(root):
    data = _read_json(queue_path(root), {"chapters": []})
    out = []
    for n in data.get("chapters") or []:
        try:
            v = int(n)
        except (TypeError, ValueError):
            continue
        if v > 0 and v not in out:
            out.append(v)
    return sorted(out)


def add_rewrite(root, chapter, reason=""):
    chapter = int(chapter)
    data = _read_json(queue_path(root), {"chapters": []})
    chs = [int(n) for n in (data.get("chapters") or []) if str(n).strip().isdigit()]
    if chapter not in chs:
        chs.append(chapter)
    data["chapters"] = sorted(set(chs))
    data["updated_at"] = _now()
    if reason:
        reasons = data.get("reasons") or {}
        reasons[str(chapter)] = str(reason)[:200]
        data["reasons"] = reasons
    _write_json(queue_path(root), data)


def clear_rewrites(root, chapters):
    """出队（合成新版本后调用）。队列空了就删文件，别留一个空壳让读方猜。"""
    p = queue_path(root)
    if not p.exists():
        return
    data = _read_json(p, {"chapters": []})
    drop = {int(n) for n in chapters}
    keep = [int(n) for n in (data.get("chapters") or [])
            if str(n).strip().isdigit() and int(n) not in drop]
    if not keep:
        try:
            p.unlink()
        except OSError:
            pass
        return
    data["chapters"] = sorted(set(keep))
    reasons = data.get("reasons") or {}
    for n in drop:
        reasons.pop(str(n), None)
    data["reasons"] = reasons
    data["updated_at"] = _now()
    _write_json(p, data)


# ---------------------------------------------------------------- 判据 → 问题

def _fmt(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return ("%.3f" % f).rstrip("0").rstrip(".")


def collect_structure_questions(root, result=None):
    """把"结构指标越线"变成具体问题（零 token）。

    `result` 可传 `utils.judge.structure.collect()` 的返回值；不传就读
    `data/review/structure.json`（阶段 5 落盘的那份）。都没有 → 什么都不生成。

    为什么问"这一章怎么办"而不是给个分数：结构指标只提示要不要人工抽样，
    它**判不了**这一章写得好不好 —— 那是作者的事。给选项，不给结论。
    """
    if result is None:
        p = Path(root) / "data" / "review" / "structure.json"
        if not p.is_file():
            return 0, 0
        try:
            result = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return 0, 0
        if not isinstance(result, dict) or not result.get("ok"):
            return 0, 0
    added = skipped = 0
    for ch in (result.get("chapters") or []):
        if not isinstance(ch, dict):
            continue
        try:
            no = int(ch.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        trig = ch.get("triggered") or []
        over = bool(ch.get("over_dialogue_max"))
        if not trig and not over:
            continue
        bits = []
        for t in trig:
            if not isinstance(t, dict):
                continue
            bits.append("%s %s（阈值 %s）" % (t.get("label") or t.get("code"),
                                              _fmt(t.get("value")), _fmt(t.get("limit"))))
        if over:
            bits.append("对话段占比 %s 超过上限 %s"
                        % (_fmt((ch.get("metrics") or {}).get("dialogue_cover")),
                           _fmt(ch.get("dialogue_max"))))
        qid, changed = upsert(root, {
            "id": "structure:ch%02d" % no,
            "stage": 4,
            "chapter": no,
            "question": "第 %d 章有 %d 项结构指标越线 —— 这一章怎么办？" % (no, len(bits)),
            "context": "；".join(bits) + "。阈值来自口径表 J16–J21：这些只提示人工抽样，"
                                        "不判文学质量。",
            "options": [
                {"value": "rewrite", "label": "重写这一章",
                 "note": "加入待重写清单，下次写作阶段重出这一章的任务书"},
                {"value": "keep", "label": "保留不改",
                 "note": "记下，不动稿子"},
                {"value": "later", "label": "先放着",
                 "note": "留在面板里，之后再说"},
            ],
            "default": "keep",
            "effect": "rewrite_chapter",
            "source": "structure",
        }, by="system")
        if changed:
            added += 1
        else:
            skipped += 1
    return added, skipped


#: 句法病问题里最多挂几处批注（再多作者看不出重点）
UNREADABLE_MAX_NOTES = 3


def _locate_paragraph(paras, sentence):
    """这句在正文的哪一段（找不到返回 -1）。"""
    s = str(sentence or "").strip()
    if not s:
        return -1
    for i, p in enumerate(paras):
        if s in p:
            return i
    return -1


def collect_unreadable_questions(root, result=None):
    """句法层“读不通的句子”→ 可作答的问题（零 token，只读）。

    `result` 传 `utils.ai_voice.scan_book()` 的返回值；不传就地扫一遍。
    **不给分数、不给结论**：它只说明“这几句结构缺件”，改不改是作者的事。
    （问题总账第 6 条剩下的那一半：句法病原本只有结论，没有出口。）
    """
    if result is None:
        try:
            from utils import ai_voice
            result = ai_voice.scan_book(Path(root))
        except Exception as e:                                # noqa: BLE001
            print("[ask] 句法层扫描失败：%s: %s" % (type(e).__name__, e))
            return 0, 0
    if not isinstance(result, dict) or not result.get("ok"):
        return 0, 0
    added = skipped = 0
    for ch in (result.get("chapters") or []):
        if not isinstance(ch, dict):
            continue
        try:
            no = int(ch.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        hits = [h for h in (ch.get("hits") or []) if isinstance(h, dict)]
        total = sum(int(h.get("count") or 0) for h in hits)
        if no <= 0 or total <= 0:
            continue
        samples = []
        for h in hits:
            for s in (h.get("samples") or []):
                if s and s not in samples:
                    samples.append(s)
        bits = "；".join("%s ×%d" % (h.get("label"), h.get("count")) for h in hits)
        qid, changed = upsert(root, {
            "id": "unreadable:ch%02d" % no,
            "stage": 4,
            "chapter": no,
            "question": "第 %d 章机器扫到 %d 句读不通（%s）—— 要不要逐段过一遍？" % (no, total, bits),
            "context": "例：" + "／".join("“%s”" % s for s in samples[:3])
                       + "。句法层只看结构缺件（单字碎句、引介语后没有主句），"
                         "不进“像不像 AI”的判定线；改不改由你定。",
            "options": [
                {"value": "mark", "label": "逐段挂批注",
                 "note": "在命中句所在段落（最多 %d 处）各挂一条批注，"
                         "精修页签里点它生成候选" % UNREADABLE_MAX_NOTES},
                {"value": "keep", "label": "保留不改", "note": "记下，不动稿子"},
                {"value": "later", "label": "先放着", "note": "留在面板里，之后再说"},
            ],
            "default": "keep",
            "effect": "annotate_unreadable",
            # 句子带在 target 里：内容变了 sig 就变，旧答案自动作废（不许拿旧答案套新句子）
            "target": {"chapter": no, "sentences": samples[:UNREADABLE_MAX_NOTES]},
            "source": "unreadable",
        }, by="system")
        if changed:
            added += 1
        else:
            skipped += 1
    return added, skipped


def _effect_annotate_unreadable(root, item, chosen):
    """答案 → 命中句所在段落挂批注（不调模型，候选仍由作者点）。"""
    c = str(chosen or "").strip()
    if not (c == "mark" or c.startswith("逐段")):
        return
    target = item.get("target") or {}
    try:
        chapter = int(target.get("chapter") or item.get("chapter") or 0)
    except (TypeError, ValueError):
        return
    if chapter <= 0:
        return
    paras, layer = _chapter_paragraphs(root, chapter)
    if not paras:
        return
    try:
        from utils import annotations as ann
    except ImportError:
        return
    done = 0
    for s in (target.get("sentences") or []):
        idx = _locate_paragraph(paras, s)
        if idx < 0:
            continue
        note = ("作者已定：这句机器判为读不通 —— “%s”。点它生成改写候选（正文未动）。"
                % str(s)[:60])
        try:
            ann.add(root, chapter, note, idx, paras, layer=layer)
            done += 1
        except (ValueError, OSError):
            continue
        if done >= UNREADABLE_MAX_NOTES:
            break


#: 质量清单（`quality_checklist.py`）落盘位置 —— 与 structure.json 同一目录、同一形状。
QUALITY_REL = "data/review/quality.json"


def collect_quality_questions(root, report=None):
    """章节质量清单未过的项 → 每题一章（零 token）。

    `report` 形如 `{"chapters": [{"chapter": 3, "issues": [...]}]}`，
    由 `quality_checklist.py` 落盘；不传就读 `data/review/quality.json`。
    为什么值得做：清单未过原本只在控制台打一行 `[FAIL]` + 退 1，
    作者在界面上只看到“跑完了/没跑完”，看不到“要不要为这一章做什么”。
    """
    if report is None:
        p = Path(root) / QUALITY_REL
        if not p.is_file():
            return 0, 0
        try:
            report = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return 0, 0
    if not isinstance(report, dict):
        return 0, 0
    added = skipped = 0
    for ch in (report.get("chapters") or []):
        if not isinstance(ch, dict):
            continue
        try:
            no = int(ch.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        issues = [str(x) for x in (ch.get("issues") or []) if str(x).strip()]
        if no <= 0 or not issues:
            continue
        qid, changed = upsert(root, {
            "id": "quality:ch%02d" % no,
            "stage": 5,
            "chapter": no,
            "question": "第 %d 章没过质量清单（%d 项）—— 重写这一章，还是按现状收？" % (no, len(issues)),
            "context": "；".join(issues[:4]) + ("…" if len(issues) > 4 else "")
                       + "。清单是确定性检查（缺件/退化/字数/大纲对齐），它说“不合格”，"
                         "但发不发由你定。",
            "options": [
                {"value": "rewrite", "label": "重写这一章",
                 "note": "进待重写清单，下次写作阶段重出这一章的任务书"},
                {"value": "keep", "label": "按现状收", "note": "清单意见记下，稿子不动"},
                {"value": "later", "label": "先放着", "note": "留在面板里，之后再说"},
            ],
            "default": "later",
            "effect": "rewrite_chapter",
            "source": "quality",
        }, by="system")
        if changed:
            added += 1
        else:
            skipped += 1
    return added, skipped


#: 一致性账本（`story_state.save_state` 落盘的位置）
STORY_STATE_REL = "data/state/story_state.json"


def collect_story_conflict_questions(root, state=None):
    """跨章硬伤（死而复生 / 写死两次 / 物品复现 / 时间倒退）→ 可作答的问题（零 token）。

    `state` 传 `story_state.rebuild()` 的返回值；不传就读 `data/state/story_state.json`。
    只取 `level == "conflict"`（warning 级的疑似错名不刷待办 —— 那是提示，不是硬伤）。

    为什么值得做（P1-2，2026-11-19）：阶段 5 分批检查，每批 ≤5 章、批间看不到彼此，
    跨章的硬伤在批内天然看不见。账本是跨章累计的，能在阶段末把"第 2 章死了、
    第 12 章又出场"这类问题捞出来 —— 但原先它只在控制台打一行，作者在界面上看不到，
    更没有"这一章怎么办"的作答位。
    """
    if state is None:
        p = Path(root) / STORY_STATE_REL
        if not p.is_file():
            return 0, 0
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return 0, 0
    if not isinstance(state, dict):
        return 0, 0
    by_ch = {}
    for c in (state.get("conflicts") or []):
        if not isinstance(c, dict) or c.get("level") != "conflict":
            continue
        try:
            no = int(c.get("chapter") or 0)
        except (TypeError, ValueError):
            continue
        if no <= 0:
            continue
        by_ch.setdefault(no, []).append(str(c.get("detail") or c.get("code") or "").strip())
    added = skipped = 0
    for no in sorted(by_ch):
        bits = [b for b in by_ch[no] if b]
        if not bits:
            continue
        qid, changed = upsert(root, {
            "id": "story:ch%02d" % no,
            "stage": 5,
            "chapter": no,
            "question": "第 %d 章和前面的账对不上（%d 处）—— 这一章怎么办？" % (no, len(bits)),
            "context": "；".join(bits[:4]) + ("…" if len(bits) > 4 else "")
                       + "。这些是确定性判据（不是模型印象）：死亡/物品/年份与前面章节冲突。"
                         "改不改由你定。",
            "options": [
                {"value": "rewrite", "label": "重写这一章",
                 "note": "进待重写清单，下次写作阶段重出这一章的任务书"},
                {"value": "keep", "label": "按现状收", "note": "可能是前文的账记错了，先留着"},
                {"value": "later", "label": "先放着", "note": "留在面板里，之后再说"},
            ],
            "default": "later",
            "effect": "rewrite_chapter",
            "source": "story_state",
        }, by="system")
        if changed:
            added += 1
        else:
            skipped += 1
    return added, skipped
