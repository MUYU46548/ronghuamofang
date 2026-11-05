# -*- coding: utf-8 -*-
"""口径登记与版本化（R04/F08）：合格线只有一处、能看见版本、能回退。

三条硬规矩（都来自 2026-10-28 产品定义）：
1. **每条判据必须有消费方标识**；消费方不存在 = 空判据 → 判定落“不可判定 A”。
   现状 `verify.min_chapter_words` 就是"配了没人读"的反面教材。
2. **版本只增不改**：改阈值 → 新版本 + 旧版本进 history；历史只读。
3. 判定时**只读登记表**，代码里不留"没有就回落默认值"的分支 —— 缺值就是缺口径。

文件：`data/judge/caliber.json`（唯一口径文件；判定记录另存 `records.json`）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

CALIBER_REL = "data/judge/caliber.json"

# 消费方白名单：判据的 consumer 必须落在这里，否则算空判据。
# 新增规则时**先在这里登记**，再写规则体 —— 顺序反过来就会先有判据后没人读。
CONSUMERS = {
    "rules.body_metrics": "F01 正文体检（字数/句长/复读/对话标签）",
    "rules.fake_concrete": "F02 伪具体·功能标准（候选召回）",
    "rules.skeleton": "F03 伪具体·可替换标准（骨架）",
    "rules.outline_density": "F04 章纲粒度过粗",
    "rules.outline_landing": "F05 正文未落地章纲",
    "verdict.decide": "R15 三档结论",
    "destroy.compare": "R02 三稿对照（销毁检测）",
    "author.events": "F03b 作者事件勾选（唯一硬门）",
    "budget.guard": "R01 预算—篇幅联动校验",
    "model.feelings": "M4 第三条证据（模型提议 → 作者终裁）",
    "rules.structure_metrics": "F06 结构指标（文学质量抽样提示：只提示抽样，不判合格）",
}

# 产出方（M4=B，2026-11-04 作者拍板）：每条判据必须写清**这条证据谁产的**。
#
# 为什么不能省：规则与模型混在同一张表里而不标产出方，出问题时无从审计 ——
# “系统量出来的”和“模型猜出来的”读起来一模一样。M4 的验收正是这一条：
# 第三条证据的产出方必须与作者所选一致，模型越层代裁必须打得回（见 feelings.py）。
PRODUCERS = {
    "rules": "规则（确定性，零 token）",
    "model": "模型（先出候选）",
    "author": "作者（终裁）",
}

# 谁定结论（settled_by）：规则判据由 verdict.decide 映射成档位；作者类由作者本人定。
SETTLERS = {
    "verdict.decide": "规则映射（三档结论）",
    "author": "作者本人",
}

# 出厂口径（v1）。kind 只有五种：阈值 / 类型 / 计数 / 结构 / 不可自动判定。
#
# `producer` / `settled_by` 是**登记字段**（不是阈值）：改它算改口径描述，
# 但不参与 `fingerprint`（指纹口径仍是 id+value）。见 needs_upgrade / upgrade。
FACTORY = [
    {"id": "J01", "name": "每章最少字数", "kind": "阈值", "value": 5000,
     "consumer": "rules.body_metrics", "producer": "rules",
     "settled_by": "verdict.decide",
     "note": "低于即记一条章级依据（不是合格门的唯一依据）"},
    {"id": "J02", "name": "每章最多字数", "kind": "阈值", "value": 9000,
     "consumer": "rules.body_metrics", "producer": "rules",
     "settled_by": "verdict.decide", "note": "高于只提示，不据此拒收"},
    {"id": "J03", "name": "长句上限（字符）", "kind": "阈值", "value": 60,
     "consumer": "rules.body_metrics", "producer": "rules",
     "settled_by": "verdict.decide", "note": "超过记一条句级缺陷"},
    {"id": "J04", "name": "复读句最少出现次数", "kind": "计数", "value": 3,
     "consumer": "rules.body_metrics", "producer": "rules",
     "settled_by": "verdict.decide", "note": "同一句出现 ≥N 次记一条，带次数"},
    {"id": "J05", "name": "对话标签复现阈值", "kind": "计数", "value": 10,
     "consumer": "rules.body_metrics", "producer": "rules",
     "settled_by": "verdict.decide", "note": "同一标签 ≥N 次记一条"},
    {"id": "J06", "name": "伪具体·功能标准召回策略", "kind": "结构",
     "value": "unit_or_pure_action", "consumer": "rules.fake_concrete",
     # 规则只召回候选；"这条算不算伪具体"由作者裁定 —— 两个字段各说一件事。
     "producer": "rules", "settled_by": "author",
     "note": "只召回候选，是否成立由作者裁定（不可自动判定项）"},
    {"id": "J07", "name": "伪具体·可替换骨架最少命中", "kind": "计数", "value": 1,
     "consumer": "rules.skeleton", "producer": "rules",
     "settled_by": "verdict.decide", "note": "换数字仍成立即算命中"},
    {"id": "J08", "name": "章纲密度虚高·连续动作条数", "kind": "阈值", "value": 3,
     "consumer": "rules.outline_density", "producer": "rules",
     "settled_by": "verdict.decide",
     "note": "同场景连续动作 ≥N 条且无状态变化 → 密度虚高（建议，不作合格门）；"
             "初值 3：本次《潮汐档案》第 1 章是 3 条连续动作 + 1 条有状态变化"},
    {"id": "J09", "name": "章纲事件落地最低匹配率", "kind": "阈值", "value": 0.5,
     "consumer": "rules.outline_landing", "producer": "rules",
     "settled_by": "verdict.decide", "note": "低于则报未落地/章纲缺项"},
    {"id": "J10", "name": "缺陷段占比阈值", "kind": "阈值", "value": 0.20,
     "consumer": "verdict.decide", "producer": "rules",
     "settled_by": "verdict.decide",
     "note": "≥ 即需定点重写（2026-10-29 作者拍板 20%）"},
    {"id": "J11", "name": "三稿缩减判销毁阈值", "kind": "阈值", "value": 0.05,
     "consumer": "destroy.compare", "producer": "rules",
     "settled_by": "verdict.decide", "note": "raw→checked 缩减超 5% 判销毁"},
    {"id": "J12", "name": "每章最少事件数（作者认定）", "kind": "不可自动判定",
     "value": 2, "consumer": "author.events", "producer": "author",
     "settled_by": "author",
     "note": "系统不测事件数，只读作者四选一勾选"},
    {"id": "J13", "name": "token 系数", "kind": "阈值", "value": 1.6,
     "consumer": "budget.guard", "producer": "rules",
     "settled_by": "verdict.decide", "note": "未实测标定，按保守值；标定后改"},
    {"id": "J14", "name": "安全系数", "kind": "阈值", "value": 1.2,
     "consumer": "budget.guard", "producer": "rules",
     "settled_by": "verdict.decide", "note": "未实测标定，按保守值"},
    # M4=B（2026-11-04）：第三条证据（"这句打不打动人"）改由**模型先出候选**、
    # 作者逐句终裁。规则不碰这一条 —— 它本来就是机器判不了的那一类。
    # 模型写进来的只能是 `pending` 提案；终裁只认作者的动作（/judge/feelings/settle），
    # 模型自己写档位/终裁一律 400（见 feelings.assert_proposal）。
    {"id": "J15", "name": "第三条证据（感受标准）产出方", "kind": "不可自动判定",
     "value": "model_then_author", "consumer": "model.feelings",
     "producer": "model", "settled_by": "author",
     "note": "M4=B：模型先出候选，作者逐句终裁；模型越层自裁一律拒收"},
    # 结构指标（F06，2026-11-05）：治“七维 8.14 分、对白却全是单回合问答”。
    # 这六条**只提示人工抽样**，不判合格 —— 所以 settled_by 是 author：
    # 机器给证据，判断是作者的。阈值是提示线，不是合格线。
    {"id": "J16", "name": "问号占对白下限", "kind": "阈值", "value": 0.15,
     "consumer": "rules.structure_metrics", "producer": "rules",
     "settled_by": "author",
     "note": "低于此线 → 对白全是陈述句，提示抽样（初值 0.15：实测 1269 句对白仅 33 句问号 = 2.6%）"},
    {"id": "J17", "name": "连续单回合问答上限", "kind": "计数", "value": 10,
     "consumer": "rules.structure_metrics", "producer": "rules",
     "settled_by": "author",
     "note": "连续“问→答”超过此组数 → 对话死板，提示抽样"},
    {"id": "J18", "name": "角色间平均句长差下限（字）", "kind": "阈值", "value": 8,
     "consumer": "rules.structure_metrics", "producer": "rules",
     "settled_by": "author",
     "note": "不同角色说话长度差小于此值 → 声音同质，提示抽样（每人 ≥3 句才参与）"},
    {"id": "J19", "name": "格言式单句密度上限", "kind": "阈值", "value": 0.15,
     "consumer": "rules.structure_metrics", "producer": "rules",
     "settled_by": "author",
     "note": "独立成段的短警句占非对白段比例超此值 → 说教味，提示抽样"},
    {"id": "J20", "name": "对话段占比下限", "kind": "阈值", "value": 0.10,
     "consumer": "rules.structure_metrics", "producer": "rules",
     "settled_by": "author",
     "note": "含对白的段落占比低于此值 → 通篇叙述，提示抽样（无对白文体判 N/A，不据此提示）"},
    {"id": "J21", "name": "平均段长上限（字）", "kind": "阈值", "value": 220,
     "consumer": "rules.structure_metrics", "producer": "rules",
     "settled_by": "author",
     "note": "段落平均字数超此值 → 手机阅读吃力，提示抽样"},
]


def _now():
    return datetime.now().isoformat(timespec="seconds")


def fingerprint(criteria):
    """口径指纹：只看 id+value（名称/备注改了不算改口径）。顺序归一。"""
    canon = sorted((str(c.get("id")), str(c.get("value"))) for c in criteria or [])
    raw = json.dumps(canon, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def load(root="."):
    p = Path(root) / CALIBER_REL
    if not p.exists():
        return bootstrap(root)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None                                         # 坏文件 ≠ 没口径
    return d


def bootstrap(root="."):
    """首次使用：把出厂表写成 v1（**这是一次显式初始化，不是判定时的默认兜底**）。"""
    doc = {
        "version": 1,
        "fingerprint": fingerprint(FACTORY),
        "updated_at": _now(),
        "updated_by": "出厂",
        "calibrated": False,
        "criteria": [dict(c) for c in FACTORY],
        "history": [],
    }
    save_raw(root, doc)
    return doc


def save_raw(root, doc):
    p = Path(root) / CALIBER_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def values(doc):
    """判据 id → value（判定引擎唯一读数入口）。"""
    return {c.get("id"): c.get("value") for c in (doc or {}).get("criteria") or []}


def empty_criteria(doc):
    """空判据：consumer 缺失或不在白名单里（R04 AC-04-1）。"""
    bad = []
    for c in (doc or {}).get("criteria") or []:
        if not c.get("consumer") or c["consumer"] not in CONSUMERS:
            bad.append({"id": c.get("id"), "name": c.get("name"),
                        "consumer": c.get("consumer")})
    return bad


def coerce_value(current, new):
    """把界面传来的字符串转回判据本来的类型（否则 `int("0.30")` 会直接炸）。

    规则：
    - 原值是整数 → 收数字/数字串（"0.30" → 0；"5000" → 5000）
    - 原值是浮点 → 收 float
    - 原值是字符串（结构类）→ 原样
    转不了就抛 ValueError，由调用方报 400。
    """
    if isinstance(current, bool):
        return bool(new)
    if isinstance(current, int):
        return int(float(new))
    if isinstance(current, float):
        return float(new)
    return new


def update(root, changes, by="作者"):
    """改口径：只接受已登记的 id；改值即产新版本，旧版本进 history（只读）。

    `changes` = {id: value}；未列出的判据保持不变。
    返回 (ok, payload)。payload 里带新版本号与影响面前提所需的字段。
    """
    doc = load(root)
    if not doc:
        return False, {"error": "口径文件读不出来（%s），先修复它再改口径" % CALIBER_REL}
    by_id = {c.get("id"): c for c in doc.get("criteria") or []}
    unknown = [k for k in (changes or {}) if k not in by_id]
    if unknown:
        return False, {"error": "未登记的判据 id：%s（先登记再改值）" % "、".join(unknown)}
    clean = {}
    for k, v in (changes or {}).items():
        try:
            clean[k] = coerce_value(by_id[k].get("value"), v)
        except (TypeError, ValueError):
            return False, {"error": "判据 %s 的值必须是数字（收到 %r）" % (k, v)}
    changes = clean
    new = []
    for c in doc.get("criteria") or []:
        c2 = dict(c)
        if c["id"] in (changes or {}):
            c2["value"] = changes[c["id"]]
        new.append(c2)
    fp = fingerprint(new)
    if fp == doc.get("fingerprint"):
        return True, {"ok": True, "changed": False, "version": doc.get("version"),
                      "fingerprint": fp, "message": "口径没有变化（版本不变）"}
    old = {k: v for k, v in doc.items() if k != "history"}
    old["superseded_at"] = _now()
    hist = list(doc.get("history") or [])
    hist.append(old)
    doc2 = {
        "version": int(doc.get("version") or 1) + 1,
        "fingerprint": fp,
        "updated_at": _now(),
        "updated_by": by,
        "calibrated": doc.get("calibrated", False),
        "criteria": new,
        "history": hist[-50:],
    }
    save_raw(root, doc2)
    return True, {"ok": True, "changed": True, "version": doc2["version"],
                  "fingerprint": fp, "criteria": new}


def rollback(root, version, by="作者"):
    """回退到历史版本：把 history 里那一版提为新版本（不删历史）。"""
    doc = load(root)
    if not doc:
        return False, {"error": "口径文件读不出来"}
    target = None
    for h in doc.get("history") or []:
        if int(h.get("version") or 0) == int(version):
            target = h
            break
    if not target:
        return False, {"error": "历史里没有第 %s 版" % version}
    return update(root, {c["id"]: c.get("value") for c in target.get("criteria") or []},
                  by="%s（回退到 v%s）" % (by, version))


def describe(doc):
    """给人看的口径卡（AC-05-1：每条判据一句人话 + 消费方 + **产出方**）。

    产出方字段（M4，2026-11-04）必须随表出来：作者要在界面上看见
    “这条是系统量的 / 这条是模型提的 / 这条是我拍的”，否则三条证据
    在界面上长得一样，“谁替谁做了判断”就无从审计。
    """
    out = []
    for c in (doc or {}).get("criteria") or []:
        producer = c.get("producer")
        settled = c.get("settled_by")
        out.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "kind": c.get("kind"),
            "value": c.get("value"),
            "consumer": c.get("consumer"),
            "consumer_name": CONSUMERS.get(c.get("consumer"), "（无消费方）"),
            "producer": producer or "",
            # 未登记就明说未登记 —— 不拿出厂表悄悄顶上（那就成了“配了没人读”的反面：读了没配）
            "producer_name": PRODUCERS.get(producer, "（未登记产出方）"),
            "settled_by": settled or "",
            "settled_by_name": SETTLERS.get(settled, "（未登记）"),
            "note": c.get("note", ""),
            "auto": c.get("kind") != "不可自动判定",
            # 能被界面改值的只有“规则量出来的阈值/计数”—— 作者类与模型类都不是数字
            "editable": bool(c.get("kind") != "不可自动判定" and producer != "author"
                             and producer != "model"),
        })
    return out


def needs_upgrade(doc):
    """登记表还缺什么（M4 升级用）：只报告，不改口径。

    老工作区的 `caliber.json` 是 v1 的 14 条，没有 `producer` / `settled_by`；
    判定照跑（`values()` 只读 id→value），但界面上的“是谁产的”那一列会是空的。
    补登记走 `upgrade()`（产新版本、旧版进 history），不在这里偷偷写盘。
    """
    crit = (doc or {}).get("criteria") or []
    by_id = {c.get("id"): c for c in crit}
    missing_meta = [c.get("id") for c in crit
                    if not c.get("producer") or c.get("producer") not in PRODUCERS]
    missing_ids = [c["id"] for c in FACTORY if c["id"] not in by_id]
    return {"criteria_without_producer": missing_meta,
            "missing_factory_ids": missing_ids,
            "needed": bool(missing_meta or missing_ids)}


def third_evidence(doc):
    """第三条证据（producer=model）那几条判据 —— M4 的唯一读数入口。"""
    return [{"id": c.get("id"), "name": c.get("name"), "value": c.get("value"),
             "settled_by": c.get("settled_by"),
             "settled_by_name": SETTLERS.get(c.get("settled_by"), "（未登记）"),
             "consumer": c.get("consumer")}
            for c in (doc or {}).get("criteria") or []
            if c.get("producer") == "model"]


def upgrade(root, by="升级补登记（M4：判据加产出方）"):
    """把老口径补齐到出厂表：补 `producer`/`settled_by`，补**新登记的判据**（J15）。

    ## 为什么是一次显式迁移，而不是读盘时自动顶上

    口径模块的铁律是“判定时只读登记表，代码里不留回落默认值”。自动顶上会让
    “表里没写”看起来像“表里写了”，而这恰恰是 M4 要防的：产出方必须是**登记过的**
    事实，不是代码里的默认值。所以升级留一个显式入口（GUI 一个按钮 / POST 一次），
    产新版本、旧版进 history —— 和改口径同一条版本链。

    返回值里的 `filled` / `added` 让调用方能说清“到底改了什么”。
    """
    doc = load(root)
    if not doc:
        return False, {"error": "口径文件读不出来（%s）" % CALIBER_REL}
    factory = {c["id"]: c for c in FACTORY}
    crit = [dict(c) for c in doc.get("criteria") or []]
    by_id = {c.get("id"): c for c in crit}
    added, filled = [], []
    for fid, fc in factory.items():
        if fid not in by_id:
            crit.append(dict(fc))
            added.append(fid)
            continue
        for key in ("producer", "settled_by"):
            if fc.get(key) and not by_id[fid].get(key):
                by_id[fid][key] = fc[key]
                filled.append("%s.%s" % (fid, key))
    if not added and not filled:
        return True, {"ok": True, "changed": False, "version": doc.get("version"),
                      "message": "登记表已是最新（每条都有产出方）"}
    fp = fingerprint(crit)
    old = {k: v for k, v in doc.items() if k != "history"}
    old["superseded_at"] = _now()
    hist = list(doc.get("history") or []) + [old]
    doc2 = {
        "version": int(doc.get("version") or 1) + 1,
        "fingerprint": fp,
        "updated_at": _now(),
        "updated_by": by,
        "calibrated": doc.get("calibrated", False),
        "criteria": crit,
        "history": hist[-50:],
    }
    save_raw(root, doc2)
    return True, {"ok": True, "changed": True, "version": doc2["version"],
                  "fingerprint": fp, "added": added, "filled": filled,
                  "criteria": crit}



def require_ready(doc):
    """判定前的前置：口径存在且没有空判据。返回 (ok, reason)。"""
    if not doc:
        return False, "缺口径：data/judge/caliber.json 不存在或读不出来"
    bad = empty_criteria(doc)
    if bad:
        return False, "空判据：%s（没有消费方的判据不许参与判定）" % "、".join(
            b["id"] or "?" for b in bad)
    return True, ""
