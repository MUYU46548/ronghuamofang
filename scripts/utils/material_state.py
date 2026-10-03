# -*- coding: utf-8 -*-
"""素材库状态机（A3）—— 确定性、零 token。

## 要解决的问题（执行单 §3）

素材卡**没有状态**。同角色两张卡矛盾时，提示词层只有「取更详细/更新的」一句，
没有可执行规则 → 模型自由心证、重跑漂移；被否决过的卡没有墓碑 → 下一轮复活；
大纲阶段读的是活稿 → 改素材会扰动已经定稿的大纲。

## 三条设计纪律

1. **不信任 LLM 自报的置信度**。素材卡本身是结构化的（`# 角色：X` + `## 基本信息`
   的 `- 键：值`），冲突可以**确定性**地判出来 —— 谁跟谁在哪一条上不一致、
   各自的出处是什么，全都可复现。让模型自报 confidence 只会制造不可复现的数字。
2. **分级有明确判据，不玄学**：
   - `auto`：一方是另一方的信息子集（含"待填充/未知"）→ 取更全的那份，记录取舍来源；
   - `review`：两边都非空且字面不同 → 机器裁不了，**报人**（目标个位数）。
3. **空结果 ≠ 通过**：没扫到卡、设定集不存在、无人裁决，都要显式回报，不许默认"没问题"。

## 与既有实现的关系

`plot_fragments` 的 `status` 来自 1a 碎片层（只吃 `original_scraps`）；本模块把同一套
「状态」推广到 characters/world，并补上墓碑与 canon 冻结。**判据不复制**：
名字归一复用 `utils.setting_schema.base_name`。
"""
import json
import re
from datetime import datetime
from pathlib import Path

from utils.setting_schema import base_name

# ---------------------------------------------------------------- 路径

SETTING_PATH = "data/setting/setting.json"
CANON_PATH = "data/setting/canon.json"
TOMBSTONE_PATH = "data/setting/tombstones.json"
AUDIT_PATH = "data/setting/merge_audit.json"
MATERIALS_DIR = "materials/raw"

CARD_KINDS = ("角色", "组织", "概念", "场景", "物品", "地点", "势力")

STATUS_ADOPTED = "adopted"
STATUS_REJECTED = "rejected"
STATUS_UNCERTAIN = "uncertain"

# 视作"没有信息"的值（不参与冲突判定）
EMPTY_VALUES = {"", "无", "待填充", "待定", "未知", "暂无", "n/a", "na", "none", "null",
                "-", "—", "（待补充）", "待补充"}

HEADING_RE = re.compile(r"^#\s*(" + "|".join(CARD_KINDS) + r")\s*[:：]\s*(.+?)\s*$", re.M)
SECTION_RE = re.compile(r"^##\s*(.+?)\s*$", re.M)
FIELD_RE = re.compile(r"^-\s*([^：:\n]{1,20}?)\s*[:：]\s*(.*?)\s*$", re.M)
SOURCE_RE = re.compile(r"^>\s*来源\s*[:：]\s*(.+?)\s*$", re.M)

# 这些字段各卡写法差异大、也不构成正典矛盾，永远归 auto（不打扰人）
LOW_RISK_FIELDS = ("英文名", "别名", "旧名", "曾用名")


# ---------------------------------------------------------------- 卡片解析

def _clean_name(raw):
    """`角色甲（绰号）` → `角色甲`（括号注释不是名字的一部分）。"""
    s = str(raw or "").strip()
    s = re.sub(r"[（(][^）)]*[）)]\s*$", "", s).strip()
    return s


def _is_empty_value(v):
    return str(v or "").strip().lower() in {x.lower() for x in EMPTY_VALUES}


def parse_card(path):
    """一张素材卡 → {name, kind, fields, source, file}；不是卡（如稿件/README）返回 None。"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:                                         # noqa: BLE001
        return None
    m = HEADING_RE.search(text)
    if not m:
        return None
    kind, name = m.group(1), _clean_name(m.group(2))
    if not name:
        return None

    fields = {}
    body = text[m.end():]
    # 段落切分必须用**同一字符串**上的 finditer 偏移；此前写成"在 rest 里 search、
    # 拿 rest 的偏移去切 body"—— 偏移量错位导致死循环（实测把脚本挂死）。
    marks = list(SECTION_RE.finditer(body))
    for i, sec in enumerate(marks):
        if not sec.group(1).strip().startswith("基本信息"):
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        for k, v in FIELD_RE.findall(body[sec.end():end]):
            k, v = k.strip(), v.strip()
            if k and v and k not in fields:
                fields[k] = v

    src = SOURCE_RE.search(text)
    return {"name": name, "kind": kind, "fields": fields,
            "source": src.group(1).strip() if src else "",
            "file": p.name, "path": str(p)}


def collect_cards(materials_dir=None):
    """扫素材目录 → [card]（只认能解析出 `# 类型：名字` 的卡）。

    ⚠️ 路径参数一律默认 None、在函数内解析 —— **默认值在 def 时求值**，
    写成 `materials_dir=MATERIALS_DIR` 会让测试/调用方改常量后**永远无效**
    （本仓已知坑：`--root` 对 def 时求值的默认参数无效）。下面所有带路径
    默认值的函数同理。
    """
    d = Path(materials_dir or MATERIALS_DIR)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.md")):
        c = parse_card(f)
        if c:
            out.append(c)
    return out


# ---------------------------------------------------------------- 冲突判定

def detect_conflicts(cards):
    """同名卡在不同字段上的分歧。返回 [conflict]。

    判据（确定性）：
      - 某字段在 ≥2 张卡上都有**非空**值、且去掉空格后字面不同 → 潜在冲突；
      - 若其中一个是另一个的子串（如「神明」vs「神明（月面）」）或属低风险字段 → `auto`；
      - 否则 → `review`（机器裁不了，必须报人）。
    """
    by_name = {}
    for c in cards:
        by_name.setdefault(c["name"], []).append(c)

    conflicts = []
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        fields = []
        for c in group:
            for k in c["fields"]:
                if k not in fields:
                    fields.append(k)
        for field in fields:
            vals = []
            for c in group:
                v = c["fields"].get(field)
                if v is None or _is_empty_value(v):
                    continue
                vals.append({"value": v, "source": c["source"] or c["file"],
                             "file": c["file"], "kind": c["kind"]})
            if len(vals) < 2:
                continue
            uniq = {re.sub(r"\s+", "", v["value"]) for v in vals}
            if len(uniq) < 2:
                continue
            severity = "review"
            if field in LOW_RISK_FIELDS:
                severity = "auto"
            else:
                # 一方是另一方的子串 → 信息量有包含关系，可取更全者
                norm = [re.sub(r"\s+", "", v["value"]) for v in vals]
                if any(a != b and a in b for a in norm for b in norm):
                    severity = "auto"
            conflicts.append({"name": name, "field": field,
                              "severity": severity, "values": vals})
    return conflicts


def summarize_conflicts(conflicts):
    """按名字聚合成 {name: {review: n, auto: n}}。"""
    out = {}
    for c in conflicts:
        slot = out.setdefault(c["name"], {"review": 0, "auto": 0, "fields": []})
        slot[c["severity"]] += 1
        slot["fields"].append(f"{c['field']}({c['severity']})")
    return out


# ---------------------------------------------------------------- 墓碑

def load_tombstones(path=None):
    """读墓碑文件；缺失/损坏都返回空结构（并把损坏念出来，不静默）。"""
    p = Path(path or TOMBSTONE_PATH)
    if not p.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
        # 兼容 list 形态
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return {"version": 1,
                    "entries": {e["name"]: e for e in data["entries"] if e.get("name")}}
        print("[material_state] WARN 墓碑文件结构异常，按空处理：" + str(p))
    except Exception as e:                                    # noqa: BLE001
        print("[material_state] WARN 墓碑文件解析失败，按空处理：" + str(e)[:140])
    return {"version": 1, "entries": {}}


def save_tombstones(data, path=None):
    p = Path(path or TOMBSTONE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(p)


def tombstone_names(data=None):
    return set((data or load_tombstones())["entries"].keys())


def add_tombstone(name, reason, source="", path=None):
    """登记「此条目已否决，归并时不得复活」。已存在则更新理由。"""
    name = _clean_name(name)
    if not name:
        return False, "名字为空，未登记"
    data = load_tombstones(path)
    existed = name in data["entries"]
    data["entries"][name] = {
        "name": name,
        "reason": (reason or "").strip() or "（未写理由）",
        "source": source or "",
        "rejected_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_tombstones(data, path)
    return True, (f"已{'更新' if existed else '登记'}墓碑：{name}（{data['entries'][name]['reason']}）")


def remove_tombstone(name, path=None):
    name = _clean_name(name)
    data = load_tombstones(path)
    if name not in data["entries"]:
        return False, f"墓碑里没有「{name}」"
    data["entries"].pop(name)
    save_tombstones(data, path)
    return True, f"已撤销墓碑：{name}"


# ---------------------------------------------------------------- 审计（纯计算）

def audit(setting_path=None, materials_dir=None, tombstone_path=None):
    """素材卡 ↔ 设定集 状态审计。**纯计算**，不写盘、不改设定集。

    返回 dict：
      cards / conflicts / conflict_summary /
      by_name: {name: {status, reason, conflicts:[...]}}
      counts: {adopted, rejected, uncertain}
      checked / reason（未生效时说明原因 —— 空结果 ≠ 通过）
    """
    setting_path = str(setting_path or SETTING_PATH)
    materials_dir = str(materials_dir or MATERIALS_DIR)
    tombstone_path = str(tombstone_path or TOMBSTONE_PATH)
    cards = collect_cards(materials_dir)
    conflicts = detect_conflicts(cards)
    summary = summarize_conflicts(conflicts)
    stones = load_tombstones(tombstone_path)

    result = {
        "checked": False, "reason": "",
        "audited_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "materials_dir": str(materials_dir),
        "setting_path": str(setting_path),
        "tombstone_path": str(tombstone_path),
        "card_count": len(cards),
        "conflicts": conflicts,
        "conflict_summary": summary,
        "review_conflicts": [c for c in conflicts if c["severity"] == "review"],
        "by_name": {},
        "counts": {STATUS_ADOPTED: 0, STATUS_REJECTED: 0, STATUS_UNCERTAIN: 0},
        "tombstones": sorted(stones["entries"].keys()),
    }

    names = set()
    sp = Path(setting_path)
    setting = None
    if sp.exists():
        try:
            setting = json.loads(sp.read_text(encoding="utf-8"))
        except Exception as e:                                # noqa: BLE001
            result["reason"] = "设定集解析失败：" + repr(e)
            return result
    else:
        result["reason"] = ("设定集不存在（" + str(sp) + "）→ 只能给素材侧的结论，"
                            "无法标注条目状态")
    if setting is not None:
        for key in ("characters", "world"):
            val = setting.get(key)
            items = val if isinstance(val, list) else (
                [x for xs in (val or {}).values() if isinstance(xs, list) for x in xs]
                if isinstance(val, dict) else [])
            for it in items:
                if isinstance(it, dict) and it.get("name"):
                    names.add(str(it["name"]).strip())
                    names.add(base_name(str(it["name"]).strip()))

    all_names = set(c["name"] for c in cards) | names
    for n in sorted(all_names):
        stones_hit = n in stones["entries"] or base_name(n) in stones["entries"]
        rev = summary.get(n, {}).get("review", 0)
        auto = summary.get(n, {}).get("auto", 0)
        if stones_hit:
            reason = ("已在墓碑中登记否决"
                      + ("；另有 %d 处待裁决分歧" % rev if rev else ""))
            status = STATUS_REJECTED
        elif rev:
            reason = f"{rev} 处分歧需人工裁决（自动归并 {auto} 处）"
            status = STATUS_UNCERTAIN
        else:
            reason = f"无待裁决分歧（自动归并 {auto} 处）" if auto else "无分歧"
            status = STATUS_ADOPTED
        result["by_name"][n] = {"status": status, "reason": reason,
                                "conflicts": rev + auto}
        result["counts"][status] += 1

    if result["reason"]:
        return result
    result["checked"] = True
    return result


def apply_status(setting, report):
    """把审计结论写进设定集条目（返回**新** dict，不改入参）。"""
    if not isinstance(setting, dict):
        return setting
    out = dict(setting)
    by_name = report.get("by_name") or {}

    def _mark(items):
        if not isinstance(items, list):
            return items
        marked = []
        for it in items:
            if not isinstance(it, dict):
                marked.append(it)
                continue
            nm = str(it.get("name") or "").strip()
            info = by_name.get(nm) or by_name.get(base_name(nm)) if nm else None
            if info:
                it = dict(it)
                it["status"] = info["status"]
                it["status_reason"] = info["reason"]
            marked.append(it)
        return marked

    out["characters"] = _mark(out.get("characters"))
    world = out.get("world")
    if isinstance(world, dict):
        out["world"] = {k: (_mark(v) if isinstance(v, list) else v)
                        for k, v in world.items()}
    elif isinstance(world, list):
        out["world"] = _mark(world)
    meta = dict(out.get("_meta") or {})
    meta["status_audit"] = {
        "at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "counts": report.get("counts"),
        "review_conflicts": len(report.get("review_conflicts") or []),
    }
    out["_meta"] = meta
    return out


def write_audit(report, path=None):
    p = Path(path or AUDIT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------- canon 冻结

def freeze_canon(setting_path=None, canon_path=None):
    """把已审批的设定集冻结成 canon 快照；大纲阶段只读它，不再读活稿。"""
    sp = Path(setting_path or SETTING_PATH)
    if not sp.exists():
        return False, f"设定集不存在：{sp}"
    try:
        data = json.loads(sp.read_text(encoding="utf-8"))
    except Exception as e:                                    # noqa: BLE001
        return False, f"设定集解析失败：{e!r}"
    meta = dict(data.get("_meta") or {})
    meta["canon_frozen_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    meta["canon_source"] = str(sp)
    data["_meta"] = meta
    cp = Path(canon_path or CANON_PATH)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, f"canon 快照已冻结 → {cp}"


def drop_canon(canon_path=None):
    cp = Path(canon_path or CANON_PATH)
    if cp.exists():
        cp.unlink()
        return True
    return False


def canon_or_setting(setting_path=None, canon_path=None):
    """大纲阶段取用哪一个：有 canon 用 canon（冻结快照），否则退回活稿。返回 str。"""
    cp = Path(canon_path or CANON_PATH)
    return str(cp) if cp.exists() else str(setting_path or SETTING_PATH)


def canon_state(canon_path=None):
    cp = Path(canon_path or CANON_PATH)
    if not cp.exists():
        return {"exists": False, "frozen_at": None}
    frozen = None
    try:
        frozen = (json.loads(cp.read_text(encoding="utf-8")).get("_meta") or {}).get(
            "canon_frozen_at")
    except Exception:                                         # noqa: BLE001
        pass
    return {"exists": True, "frozen_at": frozen}


# ---------------------------------------------------------------- 给提示词用

def load_audit(path=None):
    """读上一轮审计结果（`merge_audit.json`）；缺失/损坏返回 None。"""
    p = Path(path or AUDIT_PATH)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:                                    # noqa: BLE001
        print("[material_state] WARN 上一轮审计文件解析失败（本轮按「无历史」处理）："
              + str(e)[:140])
        return None


def _tombstones_subsection(path):
    data = load_tombstones(path)
    entries = data["entries"]
    if not entries:
        return []
    lines = ["### 已否决条目（**不得复活**）", "",
             "以下条目此前被明确否决。归并时**不得**把它们重新写入设定集；",
             "若素材里再次出现，跳过并在 `_meta` 里记一条 `resurrect_blocked`。", ""]
    for name in sorted(entries):
        e = entries[name]
        lines.append(f"- {name}：{e.get('reason', '')}"
                     + (f"（登记于 {e['rejected_at']}）" if e.get("rejected_at") else ""))
    lines.append("")
    return lines


def _prev_conflicts_subsection(prev_audit):
    """上一轮的**待裁决分歧** → 提示词小节（多回合继承，防"失忆版"）。

    开工单 §四.c：第二轮提示词必须含第一轮落盘的冲突记录，uncertain 卡带前轮冲突
    继续；冲突清单应随回合**单调下降**。没有这一段，每轮都是"从头自由心证"——
    数据结构落了盘却不在回合间继承，等于把状态机做成了摆设。
    """
    rev = (prev_audit or {}).get("review_conflicts") or []
    if not rev:
        return []
    lines = [f"### 上一轮仍有 {len(rev)} 处分歧**未裁决**（本轮继承，不得当成已定）", "",
             "对上轮已记过分歧的条目，本轮请：",
             "- 素材已更新到能判定 → 按新素材归并，并写明取舍依据；",
             "- 仍无法判定 → **保留双方**、在该条目 `merge_note` 里记下分歧，**不要拍板**。",
             ""]
    for it in rev[:20]:
        vals = " / ".join(f"{v.get('value', '')}（{v.get('file', '')}）"
                          for v in (it.get("values") or []))
        lines.append(f"- {it.get('name', '')} · {it.get('field', '')}：{vals}")
    if len(rev) > 20:
        lines.append(f"- …另有 {len(rev) - 20} 处（完整清单见 {AUDIT_PATH}）")
    lines.append("")
    return lines


def status_context_section(tombstone_path=None, audit_path=None):
    """归并提示词的**唯一**状态小节：墓碑（不得复活）+ 上一轮待裁决分歧（继承）。

    无墓碑也无历史 → 返回空串（不往提示词里塞空标题）。
    """
    lines = _tombstones_subsection(tombstone_path)
    prev = load_audit(audit_path)
    lines += _prev_conflicts_subsection(prev)
    if not lines:
        return ""
    head = ["## 素材状态（由系统确定性维护，必须遵守）", ""]
    if prev:
        counts = prev.get("counts") or {}
        head.append("上一轮结论：adopted {a} · uncertain {u} · rejected {r}"
                    "（登记于 {t}）".format(
                        a=counts.get("adopted", 0), u=counts.get("uncertain", 0),
                        r=counts.get("rejected", 0),
                        t=prev.get("audited_at", "未知时间")))
        head.append("")
    return "\n".join(head + lines)


def tombstones_prompt_section(path=None):
    """**旧入口**，保留为墓碑子段的单独取用（内部/兼容用）。

    新代码请用 `status_context_section()` —— 它是单一入口，同时携带墓碑与
    上一轮待裁决分歧。留两个入口容易漂移，但这里保留是因为它语义明确、可单测。
    """
    return "\n".join(_tombstones_subsection(path)).strip()
