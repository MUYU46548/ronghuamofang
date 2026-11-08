# -*- coding: utf-8 -*-
"""酒馆（SillyTavern）资料导入 —— 竞品三路之二（2026-11-07）。

## 为什么抄这一条

催更姬的“酒馆资料导入”：把已有的角色卡、世界书导进来继续整理。
作者点名“直接看代码抄都行”——所以这里按**公开格式**自己实现解析，不抄任何代码：

| 格式 | 怎么认 |
|---|---|
| 角色卡 `.json` | `{"spec":"chara_card_v2","data":{...}}` 或 v1 平铺字段 |
| 角色卡 `.png` | PNG 的 `tEXt` 块里 `chara`（base64 → JSON）；`ccv3` 同样认 |
| 世界书 `.json` | `{"entries": {...}}`（每条 `comment/keys/content/disable`） |

## 输出

每个角色/世界书条目落**一张 markdown 素材卡**（默认 `materials/raw/`），带 frontmatter：

```
---
name: 角色名
source: tavern-card
imported_at: 2026-11-07T...
---
```

## 三条纪律

1. **只写素材目录，不碰设定集/正典**：导入是“把料放进来”，归并仍走阶段 1 与作者审批。
2. **默认不覆盖**：同名文件已存在 → 列出冲突、跳过；要覆盖得显式 `overwrite=True`
   （素材是作者的数据，静默覆盖等于删稿）。
3. **`disable: true` 的世界书条目不导入**，但在报告里点数 —— 静默少读一条比报错更糟。
"""
from __future__ import annotations

import base64
import json
import re
import struct
from datetime import datetime
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"
CARD_TEXTS = ("chara", "ccv3", "card")
_BAD = re.compile(r'[\\/:*?"<>|\r\n\t]')

# 角色卡有、世界书没有的字段（用于“先判形状”）。
CARD_MARKERS = ("personality", "scenario", "first_mes", "mes_example",
                "character_book", "creator_notes")


def _looks_like_worldbook(obj):
    """这份 JSON 像不像 SillyTavern 世界书。

    **为什么需要这道形状判据（2026-11-08 修）**：真实 ST 世界书导出**顶层就带**
    `name` / `description` / `originalData`（见 ST 的 world-info 导出格式），
    旧实现“有 name 就按角色卡收”会把世界书整份当成一张空壳角色卡：
    只生成 `《世界书名》（酒馆角色卡）.md`，六条设定一条都不进去，
    而且 **不报错** —— 测试清单 C 节正好会踩到。

    判据回到形状本身：
      · v2 角色卡一定有 `data` 对象（世界书没有）→ 直接排除；
      · 世界书的 `entries` 是**对象且键全为数字串**（ST 的原生形状），或者是数组
        （Agnai / RoleCall 那类导出）。
    """
    if not isinstance(obj, dict):
        return False
    if isinstance(obj.get("data"), dict):
        return False
    # v1 平铺角色卡若恰好夹带 entries，也不能算世界书
    if any(k in obj for k in CARD_MARKERS):
        return False
    entries = obj.get("entries")
    if isinstance(entries, dict):
        keys = [str(k) for k in list(entries)[:20]]
        return all(k.isdigit() for k in keys) if keys else True
    return isinstance(entries, list)


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _safe_name(name, fallback="未命名"):
    s = _BAD.sub("_", str(name or "").strip()).strip(" .")
    return s[:80] or fallback


def png_text_chunks(data: bytes):
    """读 PNG 的 tEXt 块 → {keyword: text}。坏块跳过，不抛。"""
    if not data.startswith(PNG_SIG):
        raise ValueError("不是 PNG 文件（签名不符）")
    out, i = {}, len(PNG_SIG)
    while i + 8 <= len(data):
        (length,) = struct.unpack(">I", data[i:i + 4])
        ctype = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + length]
        i += 12 + length                       # length + type + data + crc
        if ctype == b"tEXt" and b"\x00" in body:
            k, _, v = body.partition(b"\x00")
            try:
                out[k.decode("latin-1")] = v.decode("latin-1")
            except Exception:                  # noqa: BLE001
                continue
        elif ctype == b"IEND":
            break
    return out


def parse_card(path):
    """角色卡（.json / .png）→ dict（统一成 v2 的 data 形状 + 内嵌世界书）。"""
    p = Path(path)
    raw = p.read_bytes()
    if p.suffix.lower() == ".png":
        texts = png_text_chunks(raw)
        payload = next((texts[k] for k in CARD_TEXTS if texts.get(k)), "")
        if not payload:
            raise ValueError("这张 PNG 里没有角色卡数据（tEXt 块缺 chara）——可能是普通图片")
        try:
            obj = json.loads(base64.b64decode(payload).decode("utf-8"))
        except Exception as e:                 # noqa: BLE001
            raise ValueError("PNG 里的角色卡数据解不开：%s" % e)
    else:
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:                 # noqa: BLE001
            raise ValueError("不是合法的 JSON：%s" % e)
    if not isinstance(obj, dict):
        raise ValueError("角色卡顶层必须是对象")
    if _looks_like_worldbook(obj):
        raise ValueError("这是世界书（顶层 entries），不是角色卡 —— 请按世界书导入")
    data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
    if not str(data.get("name") or "").strip():
        raise ValueError("角色卡没有 name 字段")
    return {
        "name": str(data.get("name")).strip(),
        "description": data.get("description") or "",
        "personality": data.get("personality") or "",
        "scenario": data.get("scenario") or "",
        "first_mes": data.get("first_mes") or "",
        "mes_example": data.get("mes_example") or "",
        "creator_notes": data.get("creator_notes") or "",
        "tags": data.get("tags") or [],
        "lorebook": _lorebook_from_card(data.get("character_book")),
        "source_file": str(p),
    }


def _lorebook_from_card(book):
    """角色卡内嵌世界书（character_book.entries 是列表）。"""
    if not isinstance(book, dict):
        return []
    out = []
    for e in book.get("entries") or []:
        if not isinstance(e, dict):
            continue
        if e.get("enabled") is False or e.get("disable") is True:
            continue
        out.append({"name": str(e.get("name") or e.get("comment") or "").strip(),
                    "keys": e.get("keys") or e.get("key") or [],
                    "content": str(e.get("content") or "")})
    return [x for x in out if x["content"].strip()]


def parse_worldbook(path):
    """SillyTavern 世界书 JSON → {"name","entries":[{name,keys,content}] ,"disabled":n}。"""
    p = Path(path)
    try:
        obj = json.loads(p.read_bytes().decode("utf-8-sig"))
    except Exception as e:                     # noqa: BLE001
        raise ValueError("不是合法的 JSON：%s" % e)
    if not isinstance(obj, dict) or not isinstance(obj.get("entries"), (dict, list)):
        raise ValueError("不像世界书：顶层缺 entries（对象或列表）")
    entries = obj.get("entries")
    items = list(entries.values()) if isinstance(entries, dict) else entries
    out, disabled = [], 0
    for e in items:
        if not isinstance(e, dict):
            continue
        if e.get("disable") is True or e.get("enabled") is False:
            disabled += 1
            continue
        content = str(e.get("content") or "").strip()
        if not content:
            continue
        keys = e.get("key") or e.get("keys") or []
        if isinstance(keys, str):
            keys = [k.strip() for k in re.split(r"[,，]", keys) if k.strip()]
        items_name = str(e.get("comment") or e.get("name") or "").strip()
        if not items_name and keys:
            items_name = str(keys[0])
        out.append({"name": items_name or "未命名条目", "keys": keys, "content": content})
    return {"name": p.stem, "entries": out, "disabled": disabled, "source_file": str(p)}


def card_markdown(card):
    """角色卡 → 一张素材卡（正文按小节组织，便于阶段 1 归并时读懂）。"""
    fm = ["---", "name: %s" % card["name"], "source: tavern-card",
          "imported_at: %s" % _now()]
    if card.get("tags"):
        fm.append("tags: [%s]" % ", ".join(str(t) for t in card["tags"][:12]))
    fm += ["---", "", "# %s" % card["name"], ""]
    blocks = [
        ("设定", card.get("description")),
        ("性格", card.get("personality")),
        ("场景", card.get("scenario")),
        ("开场", card.get("first_mes")),
        ("示例对话", card.get("mes_example")),
    ]
    for title, body in blocks:
        if str(body or "").strip():
            fm += ["## %s" % title, "", str(body).strip(), ""]
    if card.get("lorebook"):
        fm += ["## 内嵌世界书", ""]
        for e in card["lorebook"]:
            fm += ["### %s" % (e.get("name") or "未命名条目"), ""]
            if e.get("keys"):
                fm += ["触发词：%s" % "、".join(str(k) for k in e["keys"]), ""]
            fm += [e["content"].strip(), ""]
    if str(card.get("creator_notes") or "").strip():
        fm += ["## 作者备注", "", str(card["creator_notes"]).strip(), ""]
    return "\n".join(fm).rstrip() + "\n"


def worldbook_markdown(wb):
    lines = ["---", "name: %s · 世界书" % wb["name"], "source: tavern-worldbook",
             "imported_at: %s" % _now(), "---", "",
             "# %s · 世界书" % wb["name"], ""]
    for e in wb["entries"]:
        lines += ["## %s" % e["name"], ""]
        if e.get("keys"):
            lines += ["触发词：%s" % "、".join(str(k) for k in e["keys"]), ""]
        lines += [e["content"].strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def _write_new(path: Path, text: str):
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def import_file(path, out_dir="materials/raw", dry_run=False, overwrite=False):
    """导入一个 .png / .json 资料文件。返回报告（写了哪些、跳过哪些、为什么）。"""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": "文件不存在：%s" % p}
    out = Path(out_dir)
    result = {"ok": True, "source": str(p), "dry_run": bool(dry_run),
              "written": [], "skipped": [], "kind": ""}
    try:
        if p.suffix.lower() == ".png":
            card = parse_card(p)
            docs = [(_safe_name(card["name"]) + "（酒馆角色卡）.md", card_markdown(card))]
            result["kind"] = "card-png"
        else:
            # 先判**形状**，再按角色卡/世界书解析。
            # 2026-11-08：旧实现“先按角色卡试”，而真 ST 世界书顶层就有 name，
            # 于是一份世界书会被静默导成一张空壳角色卡（条目全丢、不报错）。
            try:
                _obj = json.loads(p.read_bytes().decode("utf-8-sig"))
            except Exception:                  # noqa: BLE001
                _obj = None                    # 坏文件交给 parse_card 报原文错误
            if _looks_like_worldbook(_obj):
                wb = parse_worldbook(p)
                docs = [(_safe_name(wb["name"]) + "（世界书）.md", worldbook_markdown(wb))]
                result["kind"] = "worldbook"
                result["entries"] = len(wb["entries"])
                result["disabled_entries"] = wb["disabled"]
            else:
                try:
                    card = parse_card(p)
                    docs = [(_safe_name(card["name"]) + "（酒馆角色卡）.md", card_markdown(card))]
                    result["kind"] = "card-json"
                except ValueError:
                    wb = parse_worldbook(p)
                    docs = [(_safe_name(wb["name"]) + "（世界书）.md", worldbook_markdown(wb))]
                    result["kind"] = "worldbook"
                    result["entries"] = len(wb["entries"])
                    result["disabled_entries"] = wb["disabled"]
    except ValueError as e:
        return {"ok": False, "error": str(e), "source": str(p)}

    for filename, text in docs:
        target = out / filename
        if target.exists() and not overwrite:
            result["skipped"].append({"file": filename, "why": "同名素材卡已存在（不覆盖）"})
            continue
        result["written"].append({"file": str(target), "chars": len(text)})
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    if not result["written"] and result["skipped"]:
        result["ok"] = False
        result["error"] = "目标素材卡都已存在；确要覆盖请显式加 --overwrite"
    return result
