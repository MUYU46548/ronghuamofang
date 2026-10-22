# -*- coding: utf-8 -*-
"""校验留痕台账（第一波防御工程 · 任务一）。

施工单要求「每次校验留痕：字数、问题清单、处置（通过/重生成/待人工）」。
落盘 `data/state/validation/NN.json`（单章最后一次校验的完整现场）
+ `summary.json`（全部章的处置一览，GUI / CLI 只读）。

判据口径：
- disposition ∈ {passed, regenerated, manual}
  - passed    = 校验通过（含"打回重生成后通过"，attempts 里能看到打了几轮）
  - regenerated = 打回重生成的轮次已用尽仍未过关 → 停在**待人工**，不混入合格品
  - pending   = 本轮还没轮到这章（仅出现在 summary 缺席态，不落盘）
- 只增不删：每章 JSON 覆盖写（保留最后一次完整现场），summary 同章覆盖。
- 任何 IO 失败只 WARN 不阻断写作 —— 留痕坏了不该拖垮产线（同 text_norm 纪律）。
"""
import json
from datetime import datetime
from pathlib import Path

from utils.file_io import read_text, write_text

VALIDATION_DIR = Path("data/state/validation")

DISPOSITIONS = ("passed", "regenerated", "manual")


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def log_chapter_validation(chapter_no, check, disposition,
                           regen_rounds=0, actions=(), extra=None):
    """记录一次章节校验的完整处置。

    check        : verify_chapter.ChapterCheck（含标点/退化明细）
    disposition  : "passed" | "regenerated"（= 待人工）
    regen_rounds : 本轮实际打回重生成的次数
    actions      : 每轮采取的动作序列（"扩写第1轮" / "重生成第2轮" …）
    extra        : 附加信息 dict（如熔断/停止）
    """
    if disposition not in DISPOSITIONS:
        raise ValueError(f"未知处置 {disposition!r}，可选 {DISPOSITIONS}")
    rec = {
        "chapter": int(chapter_no),
        "checked_at": _now(),
        "word_count": int(getattr(check, "word_count", 0) or 0),
        "ok": bool(getattr(check, "ok", False)),
        "disposition": disposition,
        "manual": disposition == "regenerated",   # regenerated = 停在待人工
        "regen_rounds": int(regen_rounds),
        "actions": list(actions),
        "errors": list(getattr(check, "errors", []) or []),
        "warnings": list(getattr(check, "warnings", []) or []),
        "punct_errors": list(getattr(check, "punct_errors", []) or []),
        "punct_warnings": list(getattr(check, "punct_warnings", []) or []),
        "degenerate": list(getattr(check, "degenerate", []) or []),
        "degen_metrics": dict(getattr(check, "degen_metrics", {}) or {}),
        "quality": getattr(check, "quality", None),
        "path": str(getattr(check, "path", "")),
    }
    if extra:
        rec["extra"] = extra
    try:
        VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
        write_text(VALIDATION_DIR / f"{int(chapter_no):02d}.json",
                   json.dumps(rec, ensure_ascii=False, indent=2))
        _update_summary(int(chapter_no), rec)
    except Exception as e:                                # noqa: BLE001
        print(f"[validation] WARN 留痕写入失败（不影响写作）: {type(e).__name__}: {e}")
        return rec
    return rec


def _update_summary(chapter_no, rec):
    """summary.json：{章号: {disposition, word_count, checked_at, regen_rounds}}。"""
    p = VALIDATION_DIR / "summary.json"
    data = {}
    if p.exists():
        try:
            data = json.loads(read_text(p)) or {}
        except Exception:                                 # noqa: BLE001
            data = {}
    data[f"{chapter_no:02d}"] = {
        "disposition": rec["disposition"],
        "manual": rec["manual"],
        "word_count": rec["word_count"],
        "regen_rounds": rec["regen_rounds"],
        "checked_at": rec["checked_at"],
        "errors": len(rec["errors"]),
        "punct_errors": len(rec["punct_errors"]),
    }
    write_text(p, json.dumps(data, ensure_ascii=False, indent=2))


def load_summary():
    """读 summary（读不到返回 {}，不抛）。"""
    p = VALIDATION_DIR / "summary.json"
    if not p.exists():
        return {}
    try:
        return json.loads(read_text(p)) or {}
    except Exception:                                     # noqa: BLE001
        return {}


def load_chapter_log(chapter_no):
    """读单章留痕（读不到返回 None）。"""
    p = VALIDATION_DIR / f"{int(chapter_no):02d}.json"
    if not p.exists():
        return None
    try:
        return json.loads(read_text(p))
    except Exception:                                     # noqa: BLE001
        return None


def manual_chapters():
    """停在「待人工」的章号列表（升序）。"""
    return sorted(int(k) for k, v in load_summary().items() if v.get("manual"))
