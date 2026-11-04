# -*- coding: utf-8 -*-
"""M4=B 第三条证据（2026-11-04 作者拍板）：**模型先出候选，作者逐条终裁**。

## 为什么单独一层

判定引擎的前两条证据都是确定性的（规则量字数/句长/复读；三稿对照量压缩率），
第三条是“这句打不打动人”—— 机器判不了，P0 的形态是留一个作者裁定入口（J12）。
M4 把它改成：模型先出候选、作者终裁。于是多出一样东西要守：**谁在哪一层签字**。

## 越层是这里唯一的硬边界

模型能落到盘上的只有 `pending` 提案（段落号 + 原句 + 它认为的感受 + 一句理由）。
以下写法一律拒收、**一条都不落盘**（不是“部分接受”，静默丢弃比报错更糟）：

1. payload / 条目里带结论类字段（`verdict` / `status` / `checked` / `book_reject` /
   `source` / `settled_by` …）—— 那是 `verdict.decide` 与作者的位置；
2. 原句在正文里定位不到 —— 编造的证据不是证据（复用 `text.locate`，与缺陷单同一把尺）；
3. 段落号与正文对不上 / 感受不在三选一 / 理由为空或太短 ——
   没有位置和理由的“感受”等于随手点击。

终裁只认 `/judge/feelings/settle`（GUI-only，列进 `FORBIDDEN_IN_AGENT_MODE`）：
- 采纳 → 落成**作者**的逐句裁定（`source=author` + `from_proposal`）；
- 推翻 → 作者自己给感受与理由，同样落成作者裁定；
- 驳回 → 只记理由，不进裁定库（模型提错了也是过程的一部分，留着看得见）。

## 为什么提案也写 records.json

F13 单一出口：判定相关的一切只写这一份文件。另开一个 `feelings.json` 会立刻复发
“报告说 A、文件说 B”的旧病 —— 而“作者裁了几条”正好是报告要读的数。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from utils.judge import caliber as C
from utils.judge import records as R
from utils.judge import text as T

# 一条提案允许的字段（多一个都不收：未知字段静默忽略 = 用户以为存了其实没存）
ITEM_FIELDS = ("paragraph_index", "sentence", "feeling", "reason")
# settle 的字段
DECISION_FIELDS = ("id", "action", "feeling", "reason")
FEELINGS = ("打中了", "没打中", "拿不准")
ACTIONS = ("accept", "override", "reject")
MAX_ITEMS = 30
MIN_REASON = 4

# 越层字段：模型带这些键，就是在替作者/替规则下结论。
OVERREACH = {
    "verdict": "档位只由 verdict.decide 映射",
    "status": "提案状态由系统维护",
    "settled_by": "终裁方只能是作者",
    "final": "终裁不是模型的动作",
    "final_feeling": "终裁不是模型的动作",
    "quality": "判定引擎不出分数",
    "score": "判定引擎不出分数",
    "checked": "事件勾选是作者的动作",
    "rejected": "事件勾选是作者的动作",
    "classes": "事件分类是作者的动作",
    "events": "事件数是作者认定的",
    "chosen": "事件勾选是作者的动作",
    "book_reject": "整本否掉是作者的决定",
    "accepted": "采纳是作者的动作",
    "source": "来源由系统写",
    "author": "作者字段不可由模型写",
}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def chapter_text(root, chapter):
    """第 N 章 raw 正文（证据核对的对象）。没有就返回 None。"""
    from utils.judge import backtest as B
    p = Path(root) / B.RAW_DIR / ("%02d.md" % int(chapter))
    try:
        return p.read_text(encoding="utf-8") if p.exists() else None
    except Exception:                                          # noqa: BLE001
        return None


def assert_no_overreach(payload):
    """越层检查：返回 (ok, 违规说明列表)。只认白名单键。"""
    bad = []
    for k in payload or {}:
        if k in OVERREACH:
            bad.append("%s（%s）" % (k, OVERREACH[k]))
    return (not bad), bad


def _pid(chapter, item, seq):
    raw = "%s|%s|%s|%s|%s" % (chapter, item.get("paragraph_index"),
                              item.get("sentence"), item.get("feeling"), seq)
    return "c%s-%s" % (int(chapter), hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8])


def locate_evidence(body, sentence):
    """在正文里定位一条证据：返回段号或 None。

    先按 `text.locate` 的句子口径找（与缺陷单同一把尺），找不到再退化为
    “整段包含” —— 模型常把两句引在一起（`“你回来晚了。”她说。`），
    按句子切分后跨了两个切句单元。**退化不放宽反编造**：仍然要求逐字出现在正文里，
    只是允许一句跨过切句边界。
    """
    loc = T.locate(body, sentence)
    if loc:
        return loc[0]
    target = " ".join(str(sentence or "").split())
    if not target:
        return None
    for i, para in enumerate(T.paragraphs(body), 1):
        if target in " ".join(para.split()):
            return i
    return None


def propose(root, chapter, items, model="", note=""):
    """模型提案入口（Agent 可调用）。返回 (ok, payload)；ok=False 时盘上一个字节没动。"""
    try:
        chapter = int(chapter)
    except (TypeError, ValueError):
        return False, {"ok": False, "error": "chapter 必须是正整数"}
    if chapter <= 0:
        return False, {"ok": False, "error": "chapter 必须是正整数"}
    if not isinstance(items, list) or not items:
        return False, {"ok": False, "error": "items 必须是至少 1 条提案的列表"}
    if len(items) > MAX_ITEMS:
        return False, {"ok": False,
                       "error": "一次最多 %d 条（收到 %d 条）—— 一次全章铺满等于没挑" %
                                (MAX_ITEMS, len(items))}

    body = chapter_text(root, chapter)
    if body is None:
        return False, {"ok": False,
                       "error": "没有第 %d 章的 raw 正文，无法核对证据（先跑写作阶段）" % chapter}

    problems, clean = [], []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            problems.append("第 %d 条不是对象" % i)
            continue
        extra = [k for k in it if k not in ITEM_FIELDS]
        if extra:
            over = [k for k in extra if k in OVERREACH]
            if over:
                problems.append("第 %d 条越层：模型不得写 %s（%s）"
                                % (i, "、".join(over), OVERREACH[over[0]]))
            unknown = [k for k in extra if k not in OVERREACH]
            if unknown:
                problems.append("第 %d 条有未知字段：%s" % (i, "、".join(unknown)))
            continue
        sentence = str(it.get("sentence") or "").strip()
        feeling = str(it.get("feeling") or "").strip()
        reason = str(it.get("reason") or "").strip()
        if not sentence:
            problems.append("第 %d 条缺原句" % i)
            continue
        if feeling not in FEELINGS:
            problems.append("第 %d 条感受只能是 打中了/没打中/拿不准（收到 %r）" % (i, feeling))
            continue
        if len(reason) < MIN_REASON:
            problems.append("第 %d 条理由太短（至少 %d 个字）—— 没理由的感受不算证据"
                            % (i, MIN_REASON))
            continue
        # 定位口径只有一份（locate_evidence 内部先按句子、再按整段包含）
        pi_at = locate_evidence(body, sentence)
        if not pi_at:
            problems.append("第 %d 条原句在正文里定位不到 —— 不许编造引文" % i)
            continue
        pi = it.get("paragraph_index")
        if pi is not None:
            try:
                pi = int(pi)
            except (TypeError, ValueError):
                problems.append("第 %d 条 paragraph_index 不是整数" % i)
                continue
            if pi != pi_at:
                problems.append("第 %d 条段落号对不上：正文里在第 %d 段（收到第 %d 段）"
                                % (i, pi_at, pi))
                continue
        clean.append({"paragraph_index": pi_at, "sentence": sentence,
                      "feeling": feeling, "reason": reason})

    if problems:
        return False, {"ok": False,
                       "error": "提案被拒（一条都没落盘）：" + "；".join(problems[:6]),
                       "problems": problems}

    doc = R.load(root)
    store = doc.setdefault("proposals", {}).setdefault("feelings", {})
    lst = store.setdefault(str(chapter), [])
    added, duplicated = [], 0
    now = _now()
    for it in clean:
        dup = any(p.get("sentence") == it["sentence"] and p.get("feeling") == it["feeling"]
                  and (p.get("status") or "pending") == "pending" for p in lst)
        if dup:
            duplicated += 1
            continue
        pid = _pid(chapter, it, len(lst) + 1)
        lst.append(dict(it, id=pid, chapter=chapter, model=str(model or ""),
                        note=str(note or ""), at=now, status="pending",
                        settled_at=None, final_feeling=None, settled_by=None,
                        settle_reason=None))
        added.append(pid)
    if added:
        R.save(root, doc)
    pending = sum(1 for p in lst if (p.get("status") or "pending") == "pending")
    return True, {"ok": True, "chapter": chapter, "added": added,
                  "duplicated": duplicated, "pending": pending,
                  "producer": "model",
                  "message": ("已记下 %d 条候选（待作者终裁）" % len(added)) if added
                             else "全部是已存在的待裁候选（没有重复落盘）"}


def settle(root, chapter, decisions, by="作者"):
    """作者终裁（GUI-only）。返回 (ok, payload)。

    只处理 `pending` 提案：已终裁过的再点一次会明说“已终裁过”，
    不许把“作者后一次点的”悄悄覆盖掉前一次（那等于没有留痕）。
    """
    try:
        chapter = int(chapter)
    except (TypeError, ValueError):
        return False, {"ok": False, "error": "chapter 必须是正整数"}
    if not isinstance(decisions, list) or not decisions:
        return False, {"ok": False, "error": "decisions 必须是非空列表"}

    body = chapter_text(root, chapter)
    summary = {"accept": 0, "override": 0, "reject": 0}
    for i, d in enumerate(decisions, 1):
        if not isinstance(d, dict):
            return False, {"ok": False, "error": "第 %d 条裁定不是对象" % i}
        extra = [k for k in d if k not in DECISION_FIELDS]
        if extra:
            return False, {"ok": False,
                           "error": "第 %d 条裁定有未知字段：%s" % (i, "、".join(extra))}
        pid = str(d.get("id") or "").strip()
        action = str(d.get("action") or "").strip()
        if not pid:
            return False, {"ok": False, "error": "第 %d 条裁定缺 id" % i}
        if action not in ACTIONS:
            return False, {"ok": False,
                           "error": "第 %d 条 action 只能是 accept/override/reject（收到 %r）"
                                    % (i, action)}
        doc = R.load(root)
        lst = ((doc.get("proposals") or {}).get("feelings") or {}).get(str(chapter)) or []
        prop = next((p for p in lst if p.get("id") == pid), None)
        if not prop:
            return False, {"ok": False, "error": "第 %d 条找不到提案 %s（第 %d 章）"
                                                  % (i, pid, chapter)}
        status = prop.get("status") or "pending"
        if status != "pending":
            return False, {"ok": False,
                           "error": "第 %d 条提案已终裁过（%s），不覆盖 —— 要改请重新裁定"
                                    % (i, status)}
        if body is not None and not locate_evidence(body, prop.get("sentence")):
            return False, {"ok": False,
                           "error": "第 %d 条的正文已经变了，原句定位不到 —— "
                                    "旧提案不能用作终裁依据，请重新提案" % i}

        if action == "accept":
            feeling = prop.get("feeling")
            reason = str(d.get("reason") or prop.get("reason") or "").strip()
        elif action == "override":
            feeling = str(d.get("feeling") or "").strip()
            if feeling not in FEELINGS:
                return False, {"ok": False,
                               "error": "第 %d 条推翻时必须给感受（打中了/没打中/拿不准）" % i}
            reason = str(d.get("reason") or "").strip()
            if len(reason) < MIN_REASON:
                return False, {"ok": False,
                               "error": "第 %d 条推翻时要写理由（至少 %d 个字）"
                                        % (i, MIN_REASON)}
        else:                                                   # reject
            feeling = None
            reason = str(d.get("reason") or "").strip()
            if len(reason) < MIN_REASON:
                return False, {"ok": False,
                               "error": "第 %d 条驳回也要说明理由（至少 %d 个字）—— "
                                        "“模型提错了”本身就是过程记录" % (i, MIN_REASON)}

        prop.update({"status": {"accept": "accepted", "override": "overridden",
                                "reject": "rejected"}[action],
                     "settled_at": _now(), "final_feeling": feeling,
                     "settled_by": str(by or "作者"), "settle_reason": reason})
        R.save(root, doc)
        if feeling:
            ok, item = R.set_sentence(root, chapter, prop.get("paragraph_index"),
                                      prop.get("sentence"), feeling, reason,
                                      source="author", from_proposal=pid)
            if not ok:
                return False, item
        summary[action] += 1

    return True, {"ok": True, "chapter": chapter, "summary": summary,
                  "pending": snapshot(root, chapter)["counts"].get("pending", 0),
                  "message": "已终裁：采纳 %d / 推翻 %d / 驳回 %d"
                             % (summary["accept"], summary["override"], summary["reject"])}


def snapshot(root, chapter=None):
    """给界面用的全量视图（某章或全书）。"""
    doc = R.load(root)
    store = (doc.get("proposals") or {}).get("feelings") or {}
    chapters = [int(chapter)] if chapter else sorted(int(k) for k in store if str(k).isdigit())
    items = []
    counts = {"pending": 0, "accepted": 0, "overridden": 0, "rejected": 0}
    for ch in chapters:
        for p in store.get(str(ch)) or []:
            st = p.get("status") or "pending"
            counts[st] = counts.get(st, 0) + 1
            items.append(dict(p, chapter=ch))
    sentences = R.sentence_verdicts(root, chapter) if chapter else R.sentence_verdicts(root)
    cal = C.load(root)
    return {
        "ok": True,
        "chapter": chapter,
        "counts": counts,
        "total": len(items),
        "pending": [x for x in items if (x.get("status") or "pending") == "pending"],
        "settled": [x for x in items if (x.get("status") or "pending") != "pending"],
        "author_verdicts": len(sentences),
        "author_from_model": sum(1 for s in sentences if s.get("from_proposal")),
        "criteria": C.third_evidence(cal) if cal else [],
    }


def summary(root):
    """报告页的一行摘要（不搬正文）。"""
    snap = snapshot(root, None)
    per_chapter = {}
    for x in snap["pending"]:
        per_chapter[str(x.get("chapter"))] = per_chapter.get(str(x.get("chapter")), 0) + 1
    return {"counts": snap["counts"], "total": snap["total"],
            "pending_by_chapter": per_chapter,
            "author_from_model": snap["author_from_model"],
            "criteria": snap["criteria"]}
