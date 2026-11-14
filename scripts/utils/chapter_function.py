# -*- coding: utf-8 -*-
"""章职能判断：这一章有没有干成它该干的事（零 token，只给证据）。

## 要填的那层空白

七维评审管**正确性**（逻辑无懈可击的干稿也能满分），审美五问管**好不好看**。
中间一层一直空着：**这一章有没有兑现章大纲里写的事**（核心事件 / 出场人物 / 场景 / 钩子）。

## 只做能确定的部分（别把猜测做成结论）

| 字段 | 怎么查 | 强度 |
|---|---|---|
| 涉及角色 | 大纲点名的人，正文里出现没有 | 强：大纲要求出场而正文没有，基本是真问题 |
| 场景 | 大纲点名的地点，正文里出现没有（支持片段命中） | 强 |
| 核心事件 / 小目标 / 悬念钩子 | 只比**专名与编号**（人名、地名、`940-1103-2` 这类），没有专名就判“不可判定” | 弱：**没出现不等于没写**，可能只是换了说法 |

硬纪律：

- **不给分**。输出是“核查表 + 需你过目的条目”，不是文学评分。
- **不可判定 ≠ 不通过**：抽不出专名的字段一律 `na`，不记成缺。
- 大纲括号里自己写着“未具名 / 只在记录里出现 / 不出场”的条目**不计入覆盖率**
  —— 作者早就说明它不在正文里，机器再报一次就是废话。

## 数据

- 大纲：`data/outline/chapters/NN.md`（八字段：核心事件 / 涉及角色 / 场景 / 小目标 / 悬念钩子 / 功能 / 衔接）
- 正文：`refined > checked > raw`（复用 `cliche_scan.chapter_path`，同一套优先级）
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

FIELDS = ("核心事件", "涉及角色", "场景", "小目标", "悬念钩子", "功能", "衔接")
NAME_LIST_FIELDS = ("涉及角色",)
PLACE_LIST_FIELDS = ("场景",)
NARRATIVE_FIELDS = ("核心事件", "小目标", "悬念钩子")
NOTE_FIELDS = ("功能", "衔接")
#: 大纲括号里写着这些的条目：作者已声明它不在正文里，不算缺
EXCLUDE_MARKS = ("未具名", "只在记录里出现", "不出场", "不出现", "仅提及")
BULLET_RE = re.compile(r"^-\s*([^：:]{2,10})\s*[：:]\s*(.+)$", re.M)
TITLE_RE = re.compile(r"^#{1,3}\s*(第\s*\d+\s*章[^\n]*)$", re.M)
#: 编号/数字串（如 940-1103-2）—— 这类东西在正文里几乎只有一种写法，查得住。
#: 排除 `c001` 这种角色 ID（前面紧贴字母的不算编号），末段允许 1 位（940-1103-2）。
NUM_RE = re.compile(r"(?<![A-Za-z0-9])[0-9]{2,}(?:-[0-9]+)*(?![0-9])")


def _outline_dir(root) -> Path:
    """大纲目录：项目根是 `data/outline/chapters`；归档书档直接 `outline/chapters`。"""
    p = Path(root)
    d = p / "data" / "outline" / "chapters"
    return d if d.is_dir() else (p / "outline" / "chapters")


def _chapters_base(root) -> Path:
    """正文根：项目根 `data/chapters`，书档 `chapters`（与 three_state_diff 同一套兼容）。"""
    p = Path(root)
    d = p / "data" / "chapters"
    return d if d.is_dir() else (p / "chapters")


def outline_path(root, n: int) -> Path:
    return _outline_dir(root) / ("%02d.md" % int(n))


def _body_path(root, n: int):
    """正文：refined > checked > raw（与 cliche_scan.chapter_path 同一优先级）。"""
    base = _chapters_base(root)
    for layer in ("refined", "checked", "raw"):
        p = base / layer / ("%02d.md" % int(n))
        if p.exists():
            return p
    return None


def parse_outline(text: str) -> dict:
    """`## 第N章 标题` + `- 字段：内容` → {"title", "fields"}。不认识的字段丢掉（不猜）。"""
    title = ""
    m = TITLE_RE.search(text or "")
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        # 大纲标题本身带“第N章”，界面上会再拼一次章号 —— 这里去掉前缀，避免“第1章 第1章 标题”
        title = re.sub(r"^第\s*\d+\s*章\s*", "", title).strip()
    fields = {}
    for k, v in BULLET_RE.findall(text or ""):
        k = k.strip()
        if k in FIELDS and k not in fields:
            fields[k] = v.strip()
    return {"title": title, "fields": fields}


def _split_items(section: str) -> list:
    """按顿号/分号/逗号切条目，保留原文（后面还要判“括号里是否声明不在正文”）。"""
    parts = re.split(r"[、；;]", section or "")
    return [p.strip() for p in parts if p.strip()]


def _strip_note(item: str) -> tuple:
    """`柯砚（c001，主角）` → ("柯砚", "c001，主角")；没有括号时 note 为空。"""
    m = re.match(r"^(.+?)[（(](.+?)[）)]\s*$", item)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return item.strip(), ""


def _setting_dict(root) -> dict:
    """专名表：人物（名字 + 别名）、地点/势力名。没有设定集就返回空 —— 不给假专名。"""
    base = Path(root)
    data = {}
    for p in (base / "data" / "setting" / "setting.json",
              base / "setting" / "setting.json"):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:                                  # noqa: BLE001
                data = {}
            break
    people, places = [], []
    for it in (data.get("characters") or []):
        if isinstance(it, dict):
            v = str(it.get("name") or "").strip()
            if len(v) >= 2:
                people.append(v)
            for a in (it.get("aliases") or []):
                a = str(a or "").strip()
                if len(a) >= 2:
                    people.append(a)
    world = data.get("world") or {}
    if isinstance(world, dict):
        for key in ("locations", "factions"):
            for it in (world.get(key) or []):
                if isinstance(it, dict):
                    v = str(it.get("name") or "").strip()
                    if len(v) >= 2:
                        places.append(v)
    return {"people": sorted(set(people)), "places": sorted(set(places))}


def list_items(section: str, allow_de: bool = False) -> tuple:
    """名单型字段 → (参与核查的词, 被排除的条目)。

    排除两条：括号里声明“未具名 / 不出场”的；以及**人物名单里**含“的”的短语
    （“老太太的孙子”不是名字，正文里不会照抄这个词）。
    场景允许含“的”（“柯砚的独院”），命中判定会拆开后片段比。
    """
    keep, skipped = [], []
    for raw in _split_items(section):
        name, note = _strip_note(raw)
        if any(mark in note for mark in EXCLUDE_MARKS):
            skipped.append({"item": name, "why": "括号里声明它不在正文（" + note + "）"})
            continue
        if len(name) < 2 or (not allow_de and "的" in name):
            skipped.append({"item": name, "why": "不是专名（含“的”或过短）"})
            continue
        keep.append(name)
    return keep, skipped


def _hit_place(text: str, item: str) -> str:
    """地点命中：整词 → 按“的”拆开的片段 → 尾部 4/3/2 字（“内海夜航班船”常写成“内海”）。"""
    if item in text:
        return "hit"
    parts = [p for p in re.split(r"的", item) if len(p) >= 2]
    if any(p in text for p in parts):
        return "partial"
    for n in (4, 3, 2):
        if len(item) > n and item[-n:] in text:
            return "partial"
    return "miss"


def _names_in(root, section: str) -> list:
    """叙述型字段里认出的已知专名（设定集里的人名 / 地名）。"""
    d = _setting_dict(root)
    return [n for n in (d["people"] + d["places"]) if n in (section or "")]


def check_chapter(n: int, root) -> dict:
    """判一章。返回核查表；**大纲缺失或正文缺失都如实说**，不当成通过。"""
    n = int(n)
    op = outline_path(root, n)
    bp = _body_path(root, n)
    out = {"chapter": n, "outline": str(op).replace("\\", "/"),
           "body": str(bp).replace("\\", "/") if bp else "",
           "layer": bp.parent.name if bp else "", "chars": 0,
           "title": "", "checks": [], "needs_attention": [], "notes": []}
    if not op.exists():
        out["notes"].append("这一章没有大纲（data/outline/chapters/%02d.md），无法判断职能" % n)
        return out
    if bp is None:
        out["notes"].append("这一章还没有正文（refined / checked / raw 都没有）")
        return out
    text = bp.read_text(encoding="utf-8", errors="replace")
    out["chars"] = len(text)
    parsed = parse_outline(op.read_text(encoding="utf-8", errors="replace"))
    out["title"] = parsed["title"]
    fields = parsed["fields"]
    if not fields:
        out["notes"].append("大纲里没解析出字段（核心事件 / 涉及角色 / 场景 …），无法判断")

    for field in FIELDS:
        if field not in fields:
            continue
        section = fields[field]
        if field in NOTE_FIELDS:
            # 这两个是给作者的说明，正文里查不到对应物 —— 只回显，不判
            out["checks"].append({"field": field, "level": "na", "required": [],
                                  "hit": [], "partial": [], "miss": [],
                                  "skipped": [], "verdict": "不可判定（说明性字段）",
                                  "section": section})
            continue

        skipped = []
        if field in NAME_LIST_FIELDS or field in PLACE_LIST_FIELDS:
            is_place = field in PLACE_LIST_FIELDS
            required, skipped = list_items(section, allow_de=is_place)
            names = set(_setting_dict(root)["people"])
            level = "strong"
            hit, partial, miss, soft_miss = [], [], [], []
            for it in required:
                if is_place:
                    mark = _hit_place(text, it)
                elif it in names:
                    # 设定集里真有这个人 → 强判据：名字必须出现
                    mark = "hit" if it in text else "miss"
                else:
                    # 身份描述（“盐船船家”“封声堤值守”）不是名字，按片段查；
                    # 没命中只算“可能换了说法”，不进强判据的 miss
                    mark = _hit_place(text, it)
                    if mark == "miss":
                        soft_miss.append(it)
                        continue
                {"hit": hit, "partial": partial, "miss": miss}[mark].append(it)
            if not required:
                level, verdict = "na", "不可判定（名单里没有可查的专名）"
            elif not miss:
                verdict = "全部出现" + ("（含部分写法）" if partial else "")
                if soft_miss:
                    verdict += "；身份描述按片段也没对上：%s（可能是别的说法）" % "、".join(soft_miss)
            else:
                verdict = "缺 %d 个：%s" % (len(miss), "、".join(miss))
                if soft_miss:
                    verdict += "；另有身份描述没按字面对上：%s" % "、".join(soft_miss)
                out["needs_attention"].append(
                    "%s：%s 大纲点名了，正文里没出现（也可能是换了写法，你自己看一眼）"
                    % (field, "、".join(miss)))
        else:
            # 叙述型：只认已知专名 + 编号，**绝不整段当专名**
            required = _names_in(root, section)
            level = "weak" if required else "na"
            hit = [i for i in required if i in text]
            partial, miss = [], [i for i in required if i not in text]
            if not required:
                verdict = "不可判定（这一段里没有已知的人名 / 地名）"
            elif not miss:
                verdict = "点到的 %s 都在正文里" % "、".join(required)
            else:
                verdict = "没出现：%s（不代表没写，可能换了说法）" % "、".join(miss)
                if len(miss) >= 2 and len(miss) == len(required):
                    out["needs_attention"].append(
                        "%s：这一段点到的 %s 在正文里一个都没有 —— 先确认这章有没有写这件事"
                        % (field, "、".join(required[:4])))

        out["checks"].append({"field": field, "level": level, "required": required,
                              "hit": hit, "partial": partial, "miss": miss,
                              "skipped": skipped, "verdict": verdict, "section": section})

    # 编号类证据：大纲里出现的编号，正文里有没有（比如 940-1103-2）
    nums = sorted(set(NUM_RE.findall(" ".join(fields.values()))))
    if nums:
        out["checks"].append({
            "field": "编号", "level": "weak", "required": nums,
            "hit": [x for x in nums if x in text],
            "partial": [], "miss": [x for x in nums if x not in text],
            "skipped": [], "section": "",
            "verdict": ("编号都对得上" if all(x in text for x in nums)
                        else "编号没出现：%s（可能是别的章用了）"
                             % "、".join(x for x in nums if x not in text)),
        })
    out["ok"] = True
    return out


def check_book(root, chapters=None) -> dict:
    """整本：有章大纲的章逐章判。章节集合取大纲目录（没有大纲的章无从判）。"""
    d = _outline_dir(root)
    nums = []
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            if f.stem.isdigit():
                nums.append(int(f.stem))
    if chapters:
        nums = [n for n in nums if n in set(int(c) for c in chapters)]
    rows = [check_chapter(n, root) for n in nums]
    return {"ok": True,
            "checked_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "chapters": rows,
            "stats": {"chapters": len(rows),
                      "with_body": sum(1 for r in rows if r.get("body")),
                      "need_attention": sum(1 for r in rows if r.get("needs_attention"))}}


def render_md(rep: dict) -> str:
    s = rep.get("stats") or {}
    lines = ["# 章职能核查（零 token）", "",
             "> 查的是“这一章有没有兑现章大纲写的事”，**不给分**。",
             "> 强判据（涉及角色 / 场景）缺了基本是真问题；弱判据（事件 / 小目标 / 钩子）只比专名与编号，",
             "> 没出现不等于没写。抽不出专名的字段记“不可判定”，不当成不通过。", "",
             "共 %d 章（有正文 %d 章，需你过目 %d 章）" % (
                 s.get("chapters", 0), s.get("with_body", 0), s.get("need_attention", 0))]
    for r in rep.get("chapters") or []:
        lines += ["", "## 第 %d 章 %s" % (r["chapter"], r.get("title") or "")]
        if r.get("notes"):
            lines += ["", "注意：" + "；".join(r["notes"])]
        if not r.get("body"):
            continue
        lines += ["", "正文：`%s`（%s，%d 字）" % (r["body"], r.get("layer") or "-", r.get("chars") or 0), "",
                  "| 字段 | 强度 | 核查结果 |", "|---|---|---|"]
        for c in r.get("checks") or []:
            lines.append("| %s | %s | %s |" % (c["field"], c["level"], c["verdict"]))
        if r.get("needs_attention"):
            lines += ["", "**需你过目**："]
            lines += ["- " + x for x in r["needs_attention"]]
    return "\n".join(lines) + "\n"


def write_report(rep: dict, root) -> Path:
    out = Path(root) / "data" / "review"
    out.mkdir(parents=True, exist_ok=True)
    (out / "chapter_function.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    p = out / "chapter_function.md"
    p.write_text(render_md(rep), encoding="utf-8")
    return p
