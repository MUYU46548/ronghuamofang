# -*- coding: utf-8 -*-
"""判定基准集（正例稿）的存取（2026-11-04，作者拍板 `mofang.judge.benchmark`）。

## 为什么是"用户导入"而不是内置

判定器说"这稿不行"容易，说"我没误报"需要一份**作者认可的好稿**做对照。
这份稿子只有作者拿得出来：

- **不写死在代码本体里**（否则等于把某个人的审美固化成出厂标准）；
- **从客户端界面导入**，随时可撤；
- **导入前必须有过免责声明**（版权 / 使用范围），确认动作要留痕。

## 目录

    data/judge/benchmark/index.json          清单（含免责声明确认记录）
    data/judge/benchmark/files/<id>.md       导入的副本（原件不动）
    data/judge/benchmark/_removed/           移除区（可恢复，不删除）

## 纪律

- 只增不改：同一内容（sha256）重复导入直接拒，不覆盖旧条目。
- "移除"= 移到 `_removed/` 并从清单摘掉；**不删文件**，可恢复。
- 清单每次写前备份到 `index.json.bak-<时间戳>`。
- 本模块不调模型，纯文件操作。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

BENCH_REL = "data/judge/benchmark"
MIN_CHARS = 300
MAX_CHARS = 2_000_000

DISCLAIMER_VERSION = "2026-11-04-v1"
DISCLAIMER_TEXT = (
    "这份稿子只作判定器的对照基准（用来判断“有没有误报”）。"
    "导入后会在本项目 data/judge/benchmark/ 里存一份副本，不上传任何地方，也不进代码仓库；"
    "随时可在本页移除（移除只是移出基准目录，你的原件不受影响）。"
    "导入即表示你确认：① 这份稿子你有权使用；② 其中不含你不希望留在本机项目目录里的第三方内容。"
)


def _root(root=None) -> Path:
    return Path(root) if root else Path(".")


def _dirs(root=None) -> tuple[Path, Path, Path]:
    r = _root(root)
    base = r / BENCH_REL
    return base, base / "files", base / "_removed"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_name(name: str, fallback: str = "未命名正例") -> str:
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", str(name or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return (s[:60] or fallback)


def _load_index(root=None) -> dict:
    base, _, _ = _dirs(root)
    p = base / "index.json"
    if not p.exists():
        return {"version": 1, "disclaimer_version": DISCLAIMER_VERSION, "entries": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {"version": 1, "disclaimer_version": DISCLAIMER_VERSION, "entries": []}
    if not isinstance(d, dict):
        return {"version": 1, "disclaimer_version": DISCLAIMER_VERSION, "entries": []}
    d.setdefault("entries", [])
    d.setdefault("version", 1)
    d.setdefault("disclaimer_version", DISCLAIMER_VERSION)
    return d


def _save_index(doc: dict, root=None) -> None:
    base, _, _ = _dirs(root)
    base.mkdir(parents=True, exist_ok=True)
    p = base / "index.json"
    if p.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            shutil.copy2(p, base / ("index.json.bak-" + stamp))
        except Exception:                                      # noqa: BLE001
            pass
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def _summary(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "name": e.get("name"),
        "chars": e.get("chars"),
        "imported_at": e.get("imported_at"),
        "source": e.get("source"),
        "license_version": (e.get("license") or {}).get("version"),
    }


def list_entries(root=None, include_removed: bool = False) -> dict:
    doc = _load_index(root)
    active = [_summary(e) for e in doc.get("entries") or []]
    out = {
        "ok": True,
        "disclaimer": {"version": DISCLAIMER_VERSION, "text": DISCLAIMER_TEXT},
        "entries": active,
        "count": len(active),
        "verified": len(active) > 0,
        "min_chars": MIN_CHARS,
    }
    if include_removed:
        base, _, rem = _dirs(root)
        rows = []
        if rem.exists():
            for p in sorted(rem.glob("*.json")):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:                              # noqa: BLE001
                    continue
                rows.append(_summary(d))
        out["removed"] = rows
    return out


def count(root=None) -> int:
    return len(_load_index(root).get("entries") or [])


def import_text(name, text, accept_license, source="粘贴", root=None,
                license_version=DISCLAIMER_VERSION):
    """导入一份正例。返回 (ok, msg, entry)。

    三条硬拦：没勾免责声明、正文太短、同一内容已存在。
    """
    if accept_license is not True:
        return False, "必须先确认免责声明（版权与使用范围）", None
    body = (text or "").replace("\r\n", "\n").strip()
    if len(body) < MIN_CHARS:
        return False, "正文太短（至少 %d 字，当前 %d）—— 正例要够一段完整文字" % (
            MIN_CHARS, len(body)), None
    if len(body) > MAX_CHARS:
        return False, "正文太大（上限 %d 字）" % MAX_CHARS, None

    base, files, _ = _dirs(root)
    doc = _load_index(root)
    sha = _sha(body)
    for e in doc.get("entries") or []:
        if e.get("sha256") == sha:
            return False, "同一份内容已经导入过（%s）" % e.get("name"), None

    eid = sha[:12]
    files.mkdir(parents=True, exist_ok=True)
    (files / (eid + ".md")).write_text(body, encoding="utf-8")
    entry = {
        "id": eid,
        "name": _safe_name(name),
        "file": "files/%s.md" % eid,
        "sha256": sha,
        "chars": len(body),
        "source": str(source or "")[:200],
        "imported_at": _now(),
        "license": {"version": license_version, "accepted_at": _now(),
                    "accepted_by": "author"},
    }
    doc["entries"].append(entry)
    doc["disclaimer_version"] = license_version
    _save_index(doc, root)
    return True, "已导入：%s（%d 字）" % (entry["name"], entry["chars"]), entry


def import_path(path, name, accept_license, root=None):
    p = Path(str(path or "").strip())
    if not path:
        return False, "path 必填（要导入的 .md / .txt / .docx / .pdf 路径）", None
    if not p.exists() or not p.is_file():
        return False, "文件不存在：%s" % p, None
    if p.stat().st_size > MAX_CHARS * 4:
        return False, "文件太大（上限约 %d 字）" % MAX_CHARS, None
    # 按后缀抽纯文本（2026-11-04）：此前一律 read_text，作者交 .docx/.pdf 时
    # 读进来是二进制 → 正例导进去是乱码 / 直接失败。口径与“参考作品拆解”共用
    # 一份（utils.style_profile.read_source_text），不再各自写一套。
    from utils.style_profile import read_source_text
    ok_src, text, err = read_source_text(p)
    if not ok_src:
        return False, err, None
    return import_text(name or p.stem, text, accept_license, source=str(p), root=root)


def remove(eid, root=None):
    """移出基准集（移到 _removed/，不删文件）。"""
    base, files, rem = _dirs(root)
    doc = _load_index(root)
    ent = next((e for e in doc.get("entries") or [] if e.get("id") == eid), None)
    if not ent:
        return False, "没有这条正例：%s" % eid, None
    rem.mkdir(parents=True, exist_ok=True)
    src = files / (eid + ".md")
    if src.exists():
        shutil.move(str(src), str(rem / (eid + ".md")))
    ent = dict(ent)
    ent["removed_at"] = _now()
    (rem / (eid + ".json")).write_text(
        json.dumps(ent, ensure_ascii=False, indent=1), encoding="utf-8")
    doc["entries"] = [e for e in doc.get("entries") or [] if e.get("id") != eid]
    _save_index(doc, root)
    return True, "已移出基准集：%s（可在“已移除”里恢复）" % ent.get("name"), ent


def restore(eid, root=None):
    base, files, rem = _dirs(root)
    meta_p = rem / (eid + ".json")
    if not meta_p.exists():
        return False, "没有可恢复的记录：%s" % eid, None
    ent = json.loads(meta_p.read_text(encoding="utf-8"))
    doc = _load_index(root)
    if any(e.get("id") == eid for e in doc.get("entries") or []):
        return False, "这条已经在基准集里了", None
    files.mkdir(parents=True, exist_ok=True)
    src = rem / (eid + ".md")
    if src.exists():
        shutil.move(str(src), str(files / (eid + ".md")))
    ent.pop("removed_at", None)
    doc["entries"].append(ent)
    _save_index(doc, root)
    try:
        meta_p.unlink()
    except Exception:                                          # noqa: BLE001
        pass
    return True, "已恢复：%s" % ent.get("name"), ent
