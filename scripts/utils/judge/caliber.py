# -*- coding: utf-8 -*-
"""口径登记与版本化（R04/F08）：合格线只有一处、能看见版本、能回退。

三条硬规矩（都来自 2026-10-28 产品定义）：
1. **每条判据必须有消费方标识**；消费方不存在 = 空判据 → 判定落「不可判定 A」。
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
}

# 出厂口径（v1）。kind 只有五种：阈值 / 类型 / 计数 / 结构 / 不可自动判定。
FACTORY = [
    {"id": "J01", "name": "每章最少字数", "kind": "阈值", "value": 5000,
     "consumer": "rules.body_metrics", "note": "低于即记一条章级依据（不是合格门的唯一依据）"},
    {"id": "J02", "name": "每章最多字数", "kind": "阈值", "value": 9000,
     "consumer": "rules.body_metrics", "note": "高于只提示，不据此拒收"},
    {"id": "J03", "name": "长句上限（字符）", "kind": "阈值", "value": 60,
     "consumer": "rules.body_metrics", "note": "超过记一条句级缺陷"},
    {"id": "J04", "name": "复读句最少出现次数", "kind": "计数", "value": 3,
     "consumer": "rules.body_metrics", "note": "同一句出现 ≥N 次记一条，带次数"},
    {"id": "J05", "name": "对话标签复现阈值", "kind": "计数", "value": 10,
     "consumer": "rules.body_metrics", "note": "同一标签 ≥N 次记一条"},
    {"id": "J06", "name": "伪具体·功能标准召回策略", "kind": "结构",
     "value": "unit_or_pure_action", "consumer": "rules.fake_concrete",
     "note": "只召回候选，是否成立由作者裁定（不可自动判定项）"},
    {"id": "J07", "name": "伪具体·可替换骨架最少命中", "kind": "计数", "value": 1,
     "consumer": "rules.skeleton", "note": "换数字仍成立即算命中"},
    {"id": "J08", "name": "章纲密度虚高·连续动作条数", "kind": "阈值", "value": 3,
     "consumer": "rules.outline_density",
     "note": "同场景连续动作 ≥N 条且无状态变化 → 密度虚高（建议，不作合格门）；"
             "初值 3：本次《潮汐档案》第 1 章是 3 条连续动作 + 1 条有状态变化"},
    {"id": "J09", "name": "章纲事件落地最低匹配率", "kind": "阈值", "value": 0.5,
     "consumer": "rules.outline_landing", "note": "低于则报未落地/章纲缺项"},
    {"id": "J10", "name": "缺陷段占比阈值", "kind": "阈值", "value": 0.20,
     "consumer": "verdict.decide",
     "note": "≥ 即需定点重写（2026-10-29 作者拍板 20%）"},
    {"id": "J11", "name": "三稿缩减判销毁阈值", "kind": "阈值", "value": 0.05,
     "consumer": "destroy.compare", "note": "raw→checked 缩减超 5% 判销毁"},
    {"id": "J12", "name": "每章最少事件数（作者认定）", "kind": "不可自动判定",
     "value": 2, "consumer": "author.events",
     "note": "系统不测事件数，只读作者四选一勾选"},
    {"id": "J13", "name": "token 系数", "kind": "阈值", "value": 1.6,
     "consumer": "budget.guard", "note": "未实测标定，按保守值；标定后改"},
    {"id": "J14", "name": "安全系数", "kind": "阈值", "value": 1.2,
     "consumer": "budget.guard", "note": "未实测标定，按保守值"},
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
    """给人看的口径卡（AC-05-1：每条判据一句人话 + 消费方）。"""
    out = []
    for c in (doc or {}).get("criteria") or []:
        out.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "kind": c.get("kind"),
            "value": c.get("value"),
            "consumer": c.get("consumer"),
            "consumer_name": CONSUMERS.get(c.get("consumer"), "（无消费方）"),
            "note": c.get("note", ""),
            "auto": c.get("kind") != "不可自动判定",
        })
    return out


def require_ready(doc):
    """判定前的前置：口径存在且没有空判据。返回 (ok, reason)。"""
    if not doc:
        return False, "缺口径：data/judge/caliber.json 不存在或读不出来"
    bad = empty_criteria(doc)
    if bad:
        return False, "空判据：%s（没有消费方的判据不许参与判定）" % "、".join(
            b["id"] or "?" for b in bad)
    return True, ""
