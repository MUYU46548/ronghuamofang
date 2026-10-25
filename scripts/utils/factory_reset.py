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
- **隐式身份（标记丢失也能认出，describe 的 B/C 两条路）**：标记经不起
  「归档 → 同名重建」这类正常操作（标记随书走）。B：书名 == 随包样例书名；
  C：书名 == 某预设名 **且** `materials/raw` 与该预设源逐字节一致（文件名集合
  + 每个 sha256，多一张/少一张/改一个字都不认 —— 误杀方向是「按钮不出现」，
  绝不是给真书摆还原入口）。用户 2026-10-25 实况：10:08 铺 rail-end、
  10:09 同名新建项目把标记归档走，此后还原按钮消失 —— C 路就是为它补的。
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
import hashlib
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


def _tree_count(d):
    """目录里有几个文件（不存在/空目录 = 0）。脏判据用：白名单目录非空才算「有东西可清」。"""
    d = Path(d)
    if not d.is_dir():
        return 0
    return sum(1 for p in d.rglob("*") if p.is_file())


def _cards_dir(src):
    """出厂源的素材卡目录（两种布局都认）：

    - 预设：`<src>/materials/raw/*.md`（铺设时也是从这里取）
    - 随包样例书：`<src>/materials/*.md` 直下（没有 raw/ 层 —— 实勘
      payload/examples/sample-book 19 张卡就在直下层，写死 raw/ 会数出 0 张
      把合法样例判成"源不完整"）
    """
    src = Path(src)
    for cand in (src / "materials" / "raw", src / "materials"):
        if _count_cards(cand) > 0:
            return cand
    return src / "materials" / "raw"


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


def _sample_dir(root):
    """随包样例书目录（与 factory 域同一条候选链）：env → 打包态 → 源码态 → 代码根。

    返回样例书目录（含 project.yaml 的那层）或 None。
    """
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
    except Exception:                                          # noqa: BLE001
        pass
    for c in cands:
        for sub in (Path(c) / "sample-book", Path(c)):
            if (sub / "project.yaml").is_file():
                return sub
    return None


def _book_title_of(path):
    """严格读 project.yaml 的 book.name；缺失/坏文件/重复键返回 None。"""
    from utils.project_config import load_project_yaml
    try:
        data = load_project_yaml(Path(path)) or {}
        return (((data.get("book") or {}).get("name")) or "").strip() or None
    except Exception:                                          # noqa: BLE001
        return None


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _preset_source_by_name(root, cur_name):
    """按书名找同名随包预设。返回 `(id|None, src|None, min_cards, 说明)`。

    describe 路径 C 用。预设目录解析与 `resolve_source` 同一条链
    （`preset_apply.find_presets_dir`：NF_SAMPLE_ROOT → root → 代码根），
    读 preset.yaml 走严格 loader（重复键抛错 → 跳过该预设，不静默取后值）。
    """
    import preset_apply as pa
    pdir = pa.find_presets_dir(root)
    if not pdir:
        return None, None, 0, None
    for d in sorted(Path(pdir).iterdir()):
        meta_path = d / "preset.yaml"
        if not meta_path.is_file():
            continue
        try:
            from utils.config_io import load_config_yaml
            meta = load_config_yaml(str(meta_path)) or {}
        except Exception:                                     # noqa: BLE001
            continue
        if ((meta.get("name") or "").strip()) != cur_name:
            continue
        min_cards = int((meta.get("verify") or {}).get("min_cards") or 1)
        return d.name, d, min_cards, "随包预设 %s（书名一致）" % d.name
    return None, None, 0, None


def _cards_match_source(root, src_raw):
    """当前 `materials/raw` 与出厂源是否逐字节一致（文件名集合 + 每个 sha256）。

    刻意从严：多一张、少一张、改一个字都判不一致。误杀方向是「按钮不出现」，
    不是「真书被摆上还原入口」—— 与 `factory_manifest.detect_sample_matches`
    对样例书同一口径（逐字节一致才算出厂）。
    """
    cur = Path(root) / "materials" / "raw"
    cur_files = {p.name: p for p in cur.glob("*.md")} if cur.is_dir() else {}
    src_files = {p.name: p for p in Path(src_raw).glob("*.md")}
    if not src_files or set(cur_files) != set(src_files):
        return False
    return all(_sha256_file(cur_files[n]) == _sha256_file(src_files[n])
               for n in cur_files)


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
        sub = _sample_dir(root)
        if sub is None:
            return None, 0, "样例出厂源不在（examples/sample-book 缺失）"
        return sub, 1, "出厂样例书"
    return None, 0, "未知标记类型：%s" % kind


def describe(root):
    """只读预览：当前工作区是不是出厂测试项目 + 还原会动什么。

    身份三条路（任一命中 + 完整性自检通过 = 出厂测试项目）：
      A. **铺设标记**（preset_apply 写的）—— 向导铺的预设项目；
      B. **隐式样例**—— 书名与随包样例书一致。随包样例（就叫《雾港核心》）
         是装机就有的测试项目，用户直接拿它跑完过全流程，没有铺设标记 ——
         这类**同样是一键还原的对象**（用户 2026-10-23 纠正：他工作区里
         跑完的《雾港核心》不是"真书"，是测试项目，还原功能就是为它做的）。
         名字是唯一能认出它的身份 —— 内容早被跑出来的进度覆盖，指纹认不出。
      C. **隐式预设**—— 书名与某预设名一致 **且** 素材卡与预设源逐字节一致。
         标记经不起「归档 → 同名重建」（标记随书走，2026-10-25 实测按钮消失）；
         而素材逐字节一致证明这套卡就是预设铺的、一个字没动过（详见 `_cards_
         match_source` 的从严判据）。
    """
    root = Path(root)
    marker = read_marker(root)
    cur_name = _current_book_name(root)
    out = {"is_factory": False, "kind": None, "id": None,
           "book_name": cur_name, "marker_book_name": None,
           "source_ok": False, "source": None, "reason": None,
           "wipe_plan": None}
    if cur_name is None:
        out["reason"] = "当前配置无法解析（拿不到书名），自检不过"
        return out
    out["marker_book_name"] = (marker.get("book_name") or "").strip() if marker else None

    reasons = []
    eff, src, min_cards, info = None, None, 0, None
    # A. 铺设标记（与当前书名绑定；换过书 = 标记失效，不误伤）
    if marker and out["marker_book_name"] == cur_name:
        src, min_cards, info = resolve_source(root, marker)
        if src is not None:
            eff = marker
        else:
            reasons.append(info)
    elif marker:
        reasons.append("当前书《%s》与出厂标记《%s》不符"
                       % (cur_name, out["marker_book_name"] or "?"))
    else:
        reasons.append("无出厂标记")
    # B. 隐式样例：书名 == 随包样例书名
    if eff is None:
        sdir = _sample_dir(root)
        stitle = _book_title_of(Path(sdir) / "project.yaml") if sdir else None
        if sdir and stitle and stitle == cur_name:
            if _count_cards(_cards_dir(sdir)) >= 1:
                eff = {"kind": "sample", "id": "sample-book"}
                src, min_cards, info = sdir, 1, "随包样例书（书名一致）"
            else:
                reasons.append("随包样例源不完整（0 张素材卡）")
        elif not sdir:
            reasons.append("随包样例源不在（examples/sample-book 缺失）")
    # C. 隐式预设：书名 == 随包某预设名 **且** 素材卡与该预设源逐字节一致。
    # 标记经不起「归档 → 同名重建」这类正常操作（标记随书走；用户 2026-10-25
    # 实测 10:08 铺 rail-end、10:09 同名新建项目把标记归档走，按钮从此消失）。
    # 两条一起才认：书名可以撞，逐字节一致的素材证明「这套卡一个字没动过」——
    # 改过任何一张卡都不认（误杀方向 = 按钮不出现，绝不是给真书摆还原入口）。
    if eff is None:
        pid, psrc, pmin, pnote = _preset_source_by_name(root, cur_name)
        if pid:
            n_psrc = _count_cards(_cards_dir(psrc))
            if n_psrc < pmin:
                reasons.append("%s 不完整（%d 张卡 < 要求 %d）" % (pnote, n_psrc, pmin))
            elif _cards_match_source(root, _cards_dir(psrc)):
                eff = {"kind": "preset", "id": pid}
                src, min_cards, info = psrc, pmin, "%s（素材逐字节一致）" % pnote
            else:
                reasons.append("书名与预设 %s 相同，但素材卡与出厂源不一致"
                               "（改动过 = 用户数据）" % pid)
    if eff is None:
        out["reason"] = "；".join(r for r in reasons if r) or "不是出厂测试项目"
        return out
    n_src = _count_cards(_cards_dir(src))
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
    # 脏判据（0.7.0，用户 2026-10-24 点名「已经清理完了按钮还在，很蠢」）：
    # 还原白名单里**还有东西**才算「有可还原内容」。已是出厂状态时 GUI 按钮置灰 ——
    # 一个没有可清内容的「一键还原」是摆在那等人误点的坑，不是功能。
    # 判据与 reset() 的擦除白名单同源（WIPE_DIRS/WIPE_SUBDIRS/WIPE_FILES + 素材卡数），
    # 白名单加项这里自动跟上，不会两边漂移。
    dirty_items = []
    if cur_cards != n_src:
        dirty_items.append("素材卡 %d 张（出厂 %d 张）" % (cur_cards, n_src))
    for _rel in WIPE_DIRS + WIPE_SUBDIRS:
        n = _tree_count(root / _rel)
        if n:
            dirty_items.append("%s（%d 个文件）" % (_rel, n))
    for _rel in WIPE_FILES:
        if (root / _rel).exists():
            dirty_items.append(_rel)
    out.update(is_factory=True, source_ok=True, source=str(src),
               kind=eff.get("kind"), id=eff.get("id"),
               wipe_plan={"chapters_done": n_ch, "cards_now": cur_cards,
                          "cards_factory": n_src, "stages": stages,
                          "dirty": bool(dirty_items),
                          "dirty_items": dirty_items})
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

    # 铺回出厂源 —— 身份以 describe 命中的为准（隐式样例没有标记，
    # 再读一次 marker 会解析失败）。
    src_eff = {"kind": info.get("kind"), "id": info.get("id")}
    src, _, _info = resolve_source(root, src_eff)
    if src is None:
        report["error"] = "还原中途发现出厂源消失，已中止（素材已备份、书状态已清）：%s" % _info
        return False, report
    raw_src = _cards_dir(src)
    raw.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(raw_src.glob("*.md")):
        shutil.copy2(p, raw / p.name)
        n += 1
    report["actions"].append("素材 %d 张已铺回" % n)

    # 配置写回：走 set_book_fields（定向改写 + 备份 + 回读校验）；
    # sample 源直接覆盖 project.yaml（它本来就是出厂整份，无用户改动可保）。
    from utils.project_config import set_book_fields
    if src_eff.get("kind") == "preset":
        from utils.config_io import load_config_yaml
        meta = load_config_yaml(str(Path(src) / "preset.yaml")) or {}
        # 书名以 describe 时确认的当前名为准（那一刻它 == 标记书名）
        fields = {"name": info.get("book_name") or meta.get("name")}
        for k in ("genre", "chapters"):
            if meta.get(k) is not None:
                fields[k] = meta[k]
        uo = (meta.get("user_outline") or "").strip()
        if uo:
            fields["user_outline"] = uo
        ok, msg = set_book_fields(fields)
    else:
        try:
            bak_dir = root / "data" / "state" / "preset_backup" / stamp
            bak_dir.mkdir(parents=True, exist_ok=True)
            cur_proj = root / "config" / "project.yaml"
            if cur_proj.exists():
                shutil.copy2(cur_proj, bak_dir / "project.yaml")
            shutil.copy2(Path(src) / "project.yaml", cur_proj)
            ok, msg = True, "project.yaml 已按出厂源覆盖（旧份已备份）"
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
