# -*- coding: utf-8 -*-
"""阶段 3：逐章大纲。

读整体大纲 + 设定集 → 分批（batch_outline 章/批）生成逐章大纲
→ 校验每章含 核心事件/涉及角色/功能 → progress stage3 done。
"""
import argparse
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template
from utils.config_io import load_config_yaml
from utils import volume_arc as va

# 硬字段（一直以来的契约，缺了就判废 —— 老项目的章大纲可能只有这三个）
REQUIRED_FIELDS = ["核心事件", "涉及角色", "功能"]
# 新增字段（2026-10-20，v0.7.2）：只对**新生成**的章大纲强制（strict=True），
# 存量大纲不因此判废 —— 否则升级一次就把用户已写完的大纲全判成不合格。
NEW_FIELDS = ["小目标", "悬念钩子"]
# 字段值下限（有效字符）：太短等于没写（"小目标：继续调查" 就是没写）
MIN_FIELD_CHARS = 6

_FIELD_RE_CACHE = {}


def _field_re(name):
    """匹配行首 `- 字段：值` / `字段: 值`（也接受 **加粗** 写法）。"""
    import re as _re
    key = name
    if key not in _FIELD_RE_CACHE:
        # ⚠️ 冒号后只能用 `[ \t]*`，不能写 `\s*`：`\s` 会把换行吃掉，
        # 让「- 小目标：（空值）」匹配到**下一行整行**，空值于是被判成"有内容"
        # （2026-10-20 实测：值取回来是下一行的「- 悬念钩子：…」）。
        _FIELD_RE_CACHE[key] = _re.compile(
            r"^[ \t]*(?:[-*+][ \t]+)?\**[ \t]*" + _re.escape(name)
            + r"[ \t]*\**[ \t]*[：:][ \t]*(.*)$", _re.M)
    return _FIELD_RE_CACHE[key]


def field_value(text, name):
    """取字段值（第一处）；没有该字段返回 None。多行只取第一行 —— 现有格式都是单行。"""
    m = _field_re(name).search(text or "")
    return m.group(1).strip() if m else None


def has_field(text, name):
    """字段存在且有值（空值不算有）。旧实现只判 `f in text`：
    正文里出现"钩子"两个字就算有字段 —— 那是假判据。"""
    v = field_value(text, name)
    return bool(v)


def parse_chapter_outline(path):
    """把章大纲解析成 {字段名: 值}（六字段全试一遍，缺的就是 None）。

    供 v0.9.3 伏笔台账与 GUI 复用 —— 它们不该各写一份正则。
    """
    try:
        text = read_text(path)
    except Exception:                                          # noqa: BLE001
        return {}
    return {f: field_value(text, f) for f in REQUIRED_FIELDS + NEW_FIELDS}


def check_chapter_outline(path, strict=False, new_fields=None):
    """校验单章大纲字段。返回 (ok, errors)。

    strict=False（默认，存量/GUI）：只要求三个硬字段存在且有值。
    strict=True（stage3 **新生成**后）：再要求「小目标」「悬念钩子」存在，
    且这两个字段的值至少 `MIN_FIELD_CHARS` 个有效字符。
    """
    text = read_text(path)
    errors = []
    for f in REQUIRED_FIELDS:
        if not has_field(text, f):
            errors.append(f"缺少字段或值为空: {f}")
    if strict:
        # 散文/随笔不要求「小目标/悬念钩子」（文体可裁，见 utils/writing_form.py）
        for f in (NEW_FIELDS if new_fields is None else list(new_fields)):
            v = field_value(text, f)
            if not v:
                errors.append(f"缺少字段或值为空: {f}（新标准必填）")
            elif len(v) < MIN_FIELD_CHARS:
                errors.append(
                    "字段「%s」只有 %d 字（下限 %d）：%s"
                    % (f, len(v), MIN_FIELD_CHARS, v[:20]))
    return (not errors, errors)


def outline_ok(path):
    """断点判据：大纲存在**且通过字段校验**（核心事件/涉及角色/功能）。

    ⚠️ 只判 `exists()` 不够：`reject.py --stage 3` 现在会清空本目录，但若目录里
    残留了半成品/占位大纲，会被无条件信任并喂给 stage4（写作阶段照着烂大纲写）。
    直接复用 `check_chapter_outline` —— 判据只有一份。
    """
    p = Path(path)
    if not p.exists():
        return False
    try:
        ok, _errs = check_chapter_outline(p)      # 存量宽容：不查新字段
        return bool(ok)
    except Exception:                                         # noqa: BLE001
        return False


def written_chapters(raw_dir="data/chapters/raw"):
    """已写到第几章（raw 目录里最大章号）—— 滚动规划的"当前位置"。"""
    p = Path(raw_dir)
    if not p.exists():
        return 0
    nos = []
    for f in p.glob("*.md"):
        try:
            nos.append(int(f.stem))
        except (TypeError, ValueError):
            continue
    return max(nos) if nos else 0


def build_batch_task(cfg, proj, chapters, global_outline_path, setting_path):
    listing = "\n".join(f"- 第{n}章 → data/outline/chapters/{n:02d}.md" for n in chapters)
    total = int((proj.get("book") or {}).get("chapters", 0) or max(chapters))
    # 卷弧（2026-10-20）：本批**首章所属卷**的卷纲。一批不跨卷（见 generate_chapters）。
    volume_context = va.render_context(chapters[0], total, cfg, proj)
    _, body = load_template("stage3_chapter_outline.md", {
        "first": chapters[0],
        "last": chapters[-1],
        "listing": listing,
        "path_global_outline": Path(global_outline_path).resolve(),
        "path_setting": Path(setting_path).resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "volume_context": volume_context,
    })
    return body


def generate_chapters(cfg, proj, chapters, client, task_dir="data/state/tasks",
                      cost=None, run_id=None):
    """生成指定章节的逐章大纲。返回 (ok, msg)。

    **为什么要抽成函数**：滚动规划下 stage3 只铺到"当前卷 + 下一卷"，
    stage4 写到窗口外时必须能**按需补齐单章大纲**（否则第 41 章会因缺大纲直接失败）。
    两条路径共用这一份实现 —— 判据写两遍必然漂移。
    """
    total = int((proj.get("book") or {}).get("chapters", 10))
    batch = int(cfg.get("chapter", {}).get("batch_outline", 10))
    # 文体（2026-10-20）：散文不要「小目标/悬念钩子」，strict 校验按文体取字段集
    from utils import writing_form as _wf
    _form_app = _wf.applicability(proj)
    _new_fields = _form_app.get("chapter_new_fields", NEW_FIELDS)
    out_dir = Path("data/outline/chapters")
    out_dir.mkdir(parents=True, exist_ok=True)
    # A3：与 stage2 同源 —— 只读 canon 快照（无快照则退回活稿）
    from utils.material_state import canon_or_setting

    # 批不跨卷：卷纲是按卷给的，跨卷的批会让后半批拿到错的卷弧
    size = va.volume_size(cfg, proj)
    groups, cur = [], []
    for n in chapters:
        if cur and va.volume_of(n, size) != va.volume_of(cur[0], size):
            groups.append(cur)
            cur = []
        cur.append(n)
    if cur:
        groups.append(cur)

    for bi, chs in enumerate(groups, 1):
        task = client.write_task(task_dir, f"stage3_batch{bi}.md",
                                 build_batch_task(cfg, proj, chs,
                                                  "data/outline/global.md",
                                                  canon_or_setting()))
        result = client.run_task(task)
        if cost and run_id:
            cost.charge_cost(run_id, 3, chs[0], result)
        if result["exit_code"] != 0:
            return False, f"stage3 批 {bi} 子会话失败（章节 {chs[0]}-{chs[-1]}）"
        for n in chs:
            p = out_dir / f"{n:02d}.md"
            if not p.exists():
                return False, f"stage3 第{n}章大纲未生成"
            # 新生成的章大纲按**新标准**校验（六字段 + 值不能敷衍）；
            # 存量章大纲走 outline_ok 的宽松判据，升级不会把它们判废。
            ok, missing = check_chapter_outline(p, strict=True, new_fields=_new_fields)
            if not ok:
                return False, f"stage3 第{n}章大纲不合格: {missing}"
        print(f"[stage3] 批 {bi}/{len(groups)} 完成（章节 {chs[0]}-{chs[-1]}）")
    return True, "生成完成（%d 章）" % len(chapters)


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or make_client(cfg, "outliner")
    task_dir = task_dir or "data/state/tasks"
    total = int(proj.get("book", {}).get("chapters", 10))
    out_dir = Path("data/outline/chapters")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 滚动规划（2026-10-20，台账三）：只铺到"当前卷 + 后续 rolling_volumes-1 卷"。
    # rolling_volumes=0 时 limit=total，行为与改动前逐字一致（老配置不受影响）。
    size = va.volume_size(cfg, proj)
    roll = va.rolling_volumes(cfg)
    written = written_chapters()
    limit = va.horizon(total, written, size, roll)
    if limit < total:
        print("[stage3] 滚动规划：本轮只生成到第 %d 章（全书 %d 章）；"
              "更远的章大纲等写到再定（避免越远越空泛）" % (limit, total))

    # 断点：已存在且**合格**的大纲文件跳过
    pending = [n for n in range(1, limit + 1) if not outline_ok(out_dir / f"{n:02d}.md")]
    if not pending:
        if limit < total:
            progress.set_stage(3, "done",
                               warning="滚动规划：只覆盖到第 %d 章（全书 %d 章）" % (limit, total))
            print("[stage3] 窗口内章节大纲已存在且合格，跳过")
            return True, "stage3 跳过（窗口内已存在且合格）"
        progress.mark_stage_done(3)
        print("[stage3] 全部章节大纲已存在且合格，跳过")
        return True, "stage3 跳过（已存在且合格）"

    ok, msg = generate_chapters(cfg, proj, pending, client, task_dir, cost, run_id)
    if not ok:
        progress.set_stage(3, "failed", error=msg)
        return False, msg

    if limit < total:
        progress.set_stage(3, "done",
                           warning="滚动规划：只覆盖到第 %d 章（全书 %d 章）" % (limit, total))
        print(f"[stage3] 逐章大纲完成（本轮 {len(pending)} 章，窗口上限第 {limit} 章）")
        return True, f"stage3 完成（窗口内 {len(pending)} 章，全书 {total} 章）"

    progress.mark_stage_done(3)
    print(f"[stage3] 逐章大纲完成（{total} 章）")
    return True, f"stage3 完成（{total} 章）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段3：逐章大纲")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()
    cfg = load_config_yaml("config/system.yaml")
    proj = load_config_yaml("config/project.yaml")
    from utils.progress_manager import ProgressManager
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(cfg, proj, progress, None, None, task_dir=args.task_dir)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
