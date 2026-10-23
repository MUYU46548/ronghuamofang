# -*- coding: utf-8 -*-
"""出厂测试项目「一键还原」（0.6.22）。

## 为什么需要

预设/出厂样例铺设后跑过一轮，工作区堆满测试进度（章节、设定集、
progress、评审账本……），但既没有「回到出厂原样」的入口。铺新预设
是整本替换（会归档当前书），不等于「把这本测试书重置干净」。

**边界（用户 2026-10-23 定调）：仅出厂测试项目允许一键还原。**
真书（用户自己的作品）绝不能有一键抹除入口 —— 那是事故不是功能。

## 机制

- **标记** `data/state/factory_project.json`：
  `preset_apply.apply()` 铺设成功时写入 `{kind, id, book_name, applied_at}`；
  `switch_book.archive()` / `init_empty()`（换书/归档）时清除。
- **三重自检**（任一不过 = 不动任何文件）：
  ① 标记存在；② 当前工作区书名 == 标记书名（换过书 = 标记失效，惰性不误伤）；
  ③ 出厂源还在且完整（预设目录 / 样例目录 + 卡数下限）。
- **先快照后动手**：快照失败即中止（还原是破坏性操作，无回退点不动手）。
- **旧素材先备份**到 `data/state/preset_backup/<时间戳>/`（与铺设同口径）。
- **还原后验证**：卡数与出厂源一致 + progress.json 已清，不过即报失败。

刻意不清的（还原 ≠ 抹掉用户/系统资产）：
`data/books/`（他书归档）、`data/presets/`（用户写作预设）、`history/`
（快照）、`logs/`（成本审计）、`output/`（已导出的成品文件）、
`data/state/{factory_manifest,agent_audit,workspace_schema}`（系统台账）。

入口纪律：GUI 二次确认（端点双因子 + `confirm:true`）；API 端点
`POST /factory/project/reset` 列入 FORBIDDEN_IN_AGENT_MODE ——
Agent 可盘点（`GET /factory/project` 只读），不能动手。
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

MARKER_REL = Path("data/state/factory_project.json")

# 还原时清掉的书状态（相对工作区根）。清单是白名单：不在表里的东西一律不动。
WIPE_DIRS = ("data/chapters", "data/outline", "data/setting",
             "data/summaries", "data/merged", "data/review", "data/handoff")
WIPE_SUBDIRS = ("data/state/tasks", "data/state/validation", "data/state/truncated")
WIPE_FILES = ("data/state/progress.json", "data/state/book_pacing.json")


def marker_path(root):
    return Path(root) / MARKER_REL


def write_marker(root, kind, source_id, book_name):
    """铺设成功后落标记。preset_apply 调用；kind: preset|sample。"""
    p = marker_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"kind": kind, "id": source_id, "book_name": (book_name or "").strip(),
         "applied_at": datetime.now().isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2), encoding="utf-8")


def read_marker(root):
    p = marker_path(root)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:                                          # noqa: BLE001
        return None


def clear_marker(root):
    """换书/归档/初始化时清除标记（工作区不再是出厂测试项目）。"""
    p = marker_path(root)
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            return False
    return False


def _count_cards(raw_dir):
    d = Path(raw_dir)
    if not d.is_dir():
        return 0
    return len([p for p in d.iterdir() if p.is_file() and p.suffix == ".md"])


def _current_book_name(root):
    """读当前书名（严格 loader：重复键抛 ConfigError → 返回 None = 自检不过）。

    ⚠️ 必须按 **root 参数** 读，不能走 switch_book 的模块级 PROJECT_ROOT ——
    它按代码位置定位，`--root` 与测试夹具场景会静默读到另一个项目的书名
    （同 set_book_fields 根重定向的坑）。
    """
    from utils.project_config import load_project_yaml
    try:
        data = load_project_yaml(Path(root) / "config" / "project.yaml") or {}
        return (((data.get("book") or {}).get("name")) or "").strip() or None
    except Exception:                                          # noqa: BLE001
        # 文件不存在/坏配置/重复键 —— 还原必须知道当前是谁，
        # 拿不到名字 = 自检不过，不静默放行。
        return None


def resolve_source(root, marker):
    """按标记找出厂源。返回 `(src|None, min_cards, 说明)`。"""
    kind = (marker or {}).get("kind")
    source_id = (marker or {}).get("id")
    if kind == "preset":
        import preset_apply as pa
        pdir = pa.find_presets_dir(root)
        if not pdir:
            return None, 0, "预设目录 examples/presets/ 不在（安装包残缺）"
        src = pdir / source_id
        meta_path = src / "preset.yaml"
        if not meta_path.exists():
            return None, 0, "预设出厂源已不在：%s" % source_id
        try:
            from utils.config_io import load_config_yaml
            meta = load_config_yaml(meta_path) or {}
        except Exception as e:                                  # noqa: BLE001
            return None, 0, "preset.yaml 读取失败：%s" % str(e)[:60]
        min_cards = int((meta.get("verify") or {}).get("min_cards") or 1)
        return src, min_cards, "预设 %s" % source_id
    if kind == "sample":
        # 样例书源：与 factory 域同一套候选链（环境变量 → 打包态 → 源码态 → 代码根）
        import os
        cands = []
        env = os.environ.get("NF_SAMPLE_ROOT")
        if env:
            cands.append(Path(env))
        cands.append(Path(root) / "payload" / "examples")
        cands.append(Path(root) / "examples")
        try:
            import nf_api  # noqa: F401  仅用于定位代码根；源码态下 root 即仓库根
            cands.append(Path(nf_api.__file__).resolve().parents[1] / "examples")
        except Exception:                                       # noqa: BLE001
            pass
        for c in cands:
            for sub in (Path(c) / "sample-book", Path(c)):
                if (sub / "project.yaml").is_file():
                    return sub, 1, "出厂样例书"
        return None, 0, "样例出厂源不在（examples/sample-book 缺失）"
    return None, 0, "未知标记类型：%s" % kind


def describe(root):
    """只读预览：当前工作区是不是出厂测试项目 + 还原会动什么。"""
    root = Path(root)
    marker = read_marker(root)
    cur_name = _current_book_name(root)
    out = {"is_factory": False, "kind": None, "id": None,
           "book_name": cur_name, "marker_book_name": None,
           "source_ok": False, "source": None, "reason": None,
           "wipe_plan": None}
    if not marker:
        out["reason"] = "当前工作区没有出厂标记（不是出厂测试项目，不可一键还原）"
        return out
    out["marker_book_name"] = (marker.get("book_name") or "").strip()
    out["kind"], out["id"] = marker.get("kind"), marker.get("id")
    if cur_name is None:
        out["reason"] = "当前配置无法解析（拿不到书名），自检不过"
        return out
    if out["marker_book_name"] != cur_name:
        out["reason"] = ("工作区书名（%s）与出厂标记（%s）不一致 —— "
                         "换过书后标记失效，不误伤" % (cur_name, out["marker_book_name"]))
        return out
    src, min_cards, info = resolve_source(root, marker)
    if src is None:
        out["reason"] = info
        return out
    n_src = _count_cards(Path(src) / "materials" / "raw")
    if n_src < min_cards:
        out["reason"] = "出厂源不完整：%s 只有 %d 张卡 < 要求 %d" % (info, n_src, min_cards)
        return out
    # 清单：还原会动什么
    raw = root / "materials" / "raw"
    cur_cards = _count_cards(raw)
    ch = root / "data" / "chapters" / "raw"
    n_ch = len(list(ch.glob("*.md"))) if ch.is_dir() else 0
    stages = None
    prog = root / "data" / "state" / "progress.json"
    if prog.exists():
        try:
            stages = sorted(((json.loads(prog.read_text(encoding="utf-8"))
                              or {}).get("stages") or {}).keys())
        except Exception:                                       # noqa: BLE001
            stages = ["(progress.json 无法解析)"]
    out.update(is_factory=True, source_ok=True, source=str(src),
               wipe_plan={"chapters_done": n_ch, "cards_now": cur_cards,
                          "cards_factory": n_src, "stages": stages})
    return out


def reset(root, dry_run=False):
    """执行还原。返回 `(ok, report)`；自检不过 = 一个字节都不动。"""
    root = Path(root)
    info = describe(root)
    report = {"describe": info, "actions": [], "ok": False}
    if not info["is_factory"]:
        report["error"] = info["reason"] or "不是出厂测试项目"
        return False, report
    if dry_run:
        report["ok"] = True
        report["dry_run"] = True
        return True, report

    # 纪律：破坏性操作前必留回退点。快照失败即中止。
    try:
        import snapshot as snap_mod
        snap_mod.snapshot("factory_reset")
        report["actions"].append("已快照（history/ 可回退）")
    except Exception as e:                                      # noqa: BLE001
        report["error"] = "快照失败，已中止（无回退点不动手）：%s" % str(e)[:80]
        return False, report

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 旧素材先备份再清（与 preset_apply 铺设同口径）
    raw = root / "materials" / "raw"
    if raw.is_dir():
        existing = [p for p in raw.iterdir() if p.is_file()]
        if existing:
            bak = root / "data" / "state" / "preset_backup" / stamp
            bak.mkdir(parents=True, exist_ok=True)
            for p in existing:
                shutil.copy2(p, bak / p.name)
            for p in existing:
                p.unlink()
            report["actions"].append("旧素材 %d 份已备份到 %s" % (len(existing), bak))

    # 清书状态（白名单，见模块头）
    for rel in WIPE_DIRS + WIPE_SUBDIRS:
        d = root / rel
        if d.exists():
            shutil.rmtree(str(d), ignore_errors=True)
            report["actions"].append("已清 %s/" % rel)
    for rel in WIPE_FILES:
        f = root / rel
        if f.exists():
            f.unlink()
            report["actions"].append("已清 %s" % rel)

    # 铺回出厂源
    marker = read_marker(root) or {}
    src, _, _info = resolve_source(root, marker)
    raw_src = Path(src) / "materials" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(raw_src.glob("*.md")):
        shutil.copy2(p, raw / p.name)
        n += 1
    report["actions"].append("素材 %d 张已铺回" % n)

    # 配置写回：走 set_book_fields（定向改写 + 备份 + 回读校验）；
    # sample 源直接覆盖 project.yaml（它本来就是出厂整份，无用户改动可保）。
    from utils.project_config import set_book_fields
    if marker.get("kind") == "preset":
        from utils.config_io import load_config_yaml
        meta = load_config_yaml(str(Path(src) / "preset.yaml")) or {}
        fields = {"name": marker.get("book_name") or meta.get("name")}
        for k in ("genre", "chapters"):
            if meta.get(k) is not None:
                fields[k] = meta[k]
        uo = (meta.get("user_outline") or "").strip()
        if uo:
            fields["user_outline"] = uo
        ok, msg = set_book_fields(fields)
    else:
        try:
            shutil.copy2(Path(src) / "project.yaml", root / "config" / "project.yaml")
            ok, msg = True, "project.yaml 已按出厂源覆盖"
        except OSError as e:
            ok, msg = False, str(e)[:80]
    if not ok:
        report["error"] = "素材已铺回，但配置写回失败：%s" % msg
        return False, report
    report["actions"].append(msg)

    # 验证：卡数与出厂源一致 + progress 已清
    n_after = _count_cards(raw)
    n_factory = _count_cards(raw_src)
    prog_gone = not (root / "data" / "state" / "progress.json").exists()
    report["verified"] = {"cards_after": n_after, "cards_factory": n_factory,
                          "progress_cleared": prog_gone}
    if n_after != n_factory or not prog_gone:
        report["error"] = ("还原后验证不过：卡 %d/%d 张，progress %s"
                           % (n_after, n_factory,
                              "已清" if prog_gone else "仍在"))
        return False, report
    report["ok"] = True
    return True, report
