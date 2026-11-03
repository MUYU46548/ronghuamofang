# -*- coding: utf-8 -*-
"""两人同场的互动约束（C 组“关系网驱动互动”，2026-11-03）。

## 要治的病

台账 C 组那条 ⚠️ 的原话：`RoleGraph` 只展示关系，**没有“这两人同场会怎么说话”
的约束**。关系图告诉你有条边，但不告诉写作时该怎么落笔 —— 于是同一对师兄弟，
这一章互称全名，下一章又喊“师兄”，人称与分寸全靠模型即兴。

## 口径（确定性、零 token，**不冒充正典**）

- 证据只取设定集里**已有的原文**（`relations` 字段里提到对方的那几句），
  原文照录，不改写；
- 关系基调（亲近 / 主从 / 对立 / 疏离 / 未知）由**关系词表**判定；
  词表命中不了就明说“未知”，不猜；
- 称呼不做二次推断：设定里没写“怎么称呼”，就明说没写，并给一条按基调的默认建议
  （标成建议，不是正典）；
- 输出永远带 `basis`（依据来自哪几条原文），作者能自己复核 —— 关系判断错了，
  能一眼看出是哪句原文导致的。
"""
import json
import re
from pathlib import Path

LIVE_REL = Path("data") / "setting" / "setting.json"

#: 关系词 → 基调。顺序有意义：先匹配到的（更具体的）优先。
REL_TONES = (
    ("仇敌", ("仇敌", "死敌", "不共戴天", "血仇", "杀父", "宿敌")),
    ("对立", ("对手", "竞争", "敌对", "政敌", "情敌", "互相提防", "猜忌")),
    ("主从", ("主仆", "上下级", "上司", "下属", "君臣", "师徒", "师父", "弟子",
              "宗主", "门下", "雇佣")),
    ("亲近", ("夫妻", "道侣", "父子", "母子", "父女", "母女", "兄弟", "姐妹",
              "师兄弟", "师姐", "师妹", "师兄", "师弟", "同门", "青梅竹马",
              "挚友", "好友", "同伴", "搭档", "恋人")),
    ("疏离", ("陌路", "旧识", "点头之交", "素不相识", "多年未见", "隔阂")),
)

#: 基调 → 写法约束（明确标为“建议”，不是设定）
TONE_GUIDE = {
    "仇敌": {"address": "不称名、不称职称，用敌称或直呼其罪；避免任何亲昵字眼",
             "avoid": "寒暄、共同回忆、并肩作战式的默契",
             "writing": ["台词短、少解释，动作先于语言；",
                         "冲突点落在具体旧账上，不要空喊仇恨；",
                         "允许一次克制的停顿代替争吵。"]},
    "对立": {"address": "称职称或姓氏（“沈大人”），不叫小名",
             "avoid": "把对方写成完全无理的恶人；也避免突然交心",
             "writing": ["每句都留一层潜台词；",
                         "用事务性对话推进，不用情绪宣言；",
                         "立场差异要落到具体做法，而不是“你不懂我”。"]},
    "主从": {"address": "下级用敬称（“先生”“大人”“师兄”），上级可直呼名",
             "avoid": "下级抢话、平辈口吻的玩笑；上级无来由的示弱",
             "writing": ["句长与语气分出尊卑：下级句子更短、更多确认；",
                         "下级可以有内心异议，但表面服从；",
                         "上级的松动只用细节（递茶、改口）表达。"]},
    "亲近": {"address": "用亲称或省姓（“阿砚”“师兄”），正式场合才连名带姓",
             "avoid": "全程敬语、生硬的“您”；也避免腻到失真",
             "writing": ["允许打断、抢话、把话说半句；",
                         "关心落在具体动作（塞干粮、披衣），不宣言；",
                         "吵架也不撤掉称呼的亲近。"]},
    "疏离": {"address": "连名带姓，或用身份称呼",
             "avoid": "突然的亲密称呼、共同的私密回忆",
             "writing": ["对话偏事务性，留白多；",
                         "用“没接话”“看向别处”表达距离；",
                         "不解释为什么疏远。"]},
    "未知": {"address": "设定里没写，先按身份取最保守的称呼（姓 + 职称）",
             "avoid": "自行发明亲密关系或旧怨",
             "writing": ["先把关系写清再写对话；",
                         "若本章必须定调，先在设定集里补一条关系。"]},
}


def _load(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:                                        # noqa: BLE001
        return None


def _index(doc):
    out = {}
    for e in (doc or {}).get("characters") or []:
        if not isinstance(e, dict):
            continue
        n = str(e.get("name") or e.get("名称") or "").strip()
        if n:
            out[n] = e
    return out


def _relations(entry):
    v = (entry or {}).get("relations") or (entry or {}).get("关系") or []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return ["%s：%s" % (k, x) for k, x in v.items()]
    return [str(x) for x in v if str(x).strip()]


def _mentions(text, name):
    """这句关系原文有没有提到对方（支持“沈砚”“阿砚”这类包含关系）。"""
    if not text or not name:
        return False
    if name in text:
        return True
    base = re.sub(r"（.*?）|\(.*?\)", "", name).strip()
    return bool(base and base in text)


def _tone_of(texts):
    joined = " ".join(texts)
    for tone, words in REL_TONES:
        if any(w in joined for w in words):
            return tone
    return "未知"


def analyze(doc, a, b):
    """纯函数：设定集文档 + 两个名字 → 互动约束。可单测。"""
    a = str(a or "").strip()
    b = str(b or "").strip()
    out = {"ok": True, "a": a, "b": b, "found": False, "tone": "未知",
           "evidence": [], "basis": [], "guide": {}, "missing": [],
           "note": ""}
    if not a or not b:
        return {"ok": False, "error": "a / b 必填"}
    if a == b:
        return {"ok": False, "error": "a 与 b 是同一个角色"}

    idx = _index(doc)
    miss = [n for n in (a, b) if n not in idx]
    if miss:
        out["missing"] = miss
        out["note"] = ("设定集里没有：" + "、".join(miss)
                       + "（先在素材/设定里补条目，才能谈同场约束）")
        return out
    out["found"] = True

    ra = [t for t in _relations(idx[a]) if _mentions(t, b)]
    rb = [t for t in _relations(idx[b]) if _mentions(t, a)]
    basis = ([{"who": a, "text": t} for t in ra]
             + [{"who": b, "text": t} for t in rb])

    out["basis"] = basis
    out["evidence"] = [x["text"] for x in basis]
    out["tone"] = _tone_of(out["evidence"])

    guide = dict(TONE_GUIDE.get(out["tone"], TONE_GUIDE["未知"]))
    out["guide"] = guide
    if not evidence_has_address(out["evidence"]):
        out.setdefault("missing", []).append("称呼")
        out["note"] = ("设定集里没有写明他们怎么称呼彼此 —— 下面给的是按关系基调的"
                       "默认建议，不是正典；写进正文前建议先补一条关系说明。")
    return out


def evidence_has_address(texts):
    """关系原文里有没有“称… / 叫… / 尊称…”这类称呼线索。"""
    joined = " ".join(texts or [])
    return any(k in joined for k in ("称", "叫", "喊", "尊称", "直呼"))


def build(root, a, b, kind="characters"):
    doc = _load(Path(root) / LIVE_REL)
    if doc is None:
        return {"ok": False,
                "error": "还没有可读的 data/setting/setting.json（阶段 1 尚未完成）"}
    return analyze(doc, a, b)
