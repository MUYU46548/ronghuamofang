# -*- coding: utf-8 -*-
"""改一段：候选 → 采用（P1-5，2026-11-08）。

## 判据（用户原话）

“重写返回后给‘改前/改后’并排，点‘采用’才写盘；不采用保留原文。”
—— 所以最硬的一条是：**不点采用，正文一个字节不变**。

## 锁什么

- C1 预览不落盘：模型写完后被回滚，正文与预览前逐字节相同
- C2 候选只进 `data/state/para_pending.json`，含改前/改后
- C3 采用 = 换进正文（只动目标段）+ 旧版进段落历史 + 候选清掉
- C4 放弃 = 候选清掉，正文不动
- C5 self 模式：出任务书 → 外部写草稿 → 判据 → 采用（同一套写盘路径）

用法：python tests/unit/test_para_candidate.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


import refine_paragraph as RP                                       # noqa: E402

# 任务书模板在 prompts/ 下（相对 cwd）；用例在临时目录里跑，拿不到模板。
# 这里只替模板，不替任何判据 —— 段落切分、写盘、回滚、判据全走真实实现。
RP.load_template = lambda name, mapping: ({}, "（用例替身模板）{{\n  \"{{paragraph_text}}\"\n}}")
from utils import self_actions as SA                                # noqa: E402

CH = """## 第1章 入境

他在码头下船，风把盐味推进喉咙。

她抬眼看了他一下，没有说话。

第三段：他记得那天的天气。
"""
NEW_P2 = "她抬眼看了他，什么也没说，把伞往他那边偏了偏。"


class _FakeClient:
    """只做两件事：写任务文件、把目标段替换成 NEW_P2（模拟模型改稿）。"""

    def __init__(self, chap_path):
        self.chap = chap_path

    def write_task(self, task_dir, name, body):
        p = Path(task_dir) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def run_task(self, task, session_id=None):   # K8：实现必须收 session_id
        text = self.chap.read_text(encoding="utf-8")
        paras = RP.split_paragraphs(text)
        paras[2] = NEW_P2
        self.chap.write_text("\n\n".join(paras) + "\n", encoding="utf-8")
        return {"exit_code": 0}


def _setup():
    tmp = Path(tempfile.mkdtemp(prefix="nf_para_"))
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True)
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text(CH, encoding="utf-8")
    (tmp / "data" / "self_drafts").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    return tmp


def _in_tmp(fn):
    tmp = _setup()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        return fn(tmp)
    finally:
        os.chdir(cwd)


CFG = {"chapter": {"target_words": [4000, 7200]}}
PROJ = {"book": {"chapters": 3}}


def case_preview_rollback():
    print("\n【C1/C2】预览不落盘：模型写完被回滚，正文逐字节还原")

    def run(tmp):
        chap = tmp / "data" / "chapters" / "raw" / "01.md"
        before = chap.read_text(encoding="utf-8")
        ok, msg, rec = RP.preview_paragraph(CFG, PROJ, 1, 2, "把这一段改得克制一点",
                                            client=_FakeClient(chap))
        after = chap.read_text(encoding="utf-8")
        pending = RP.get_pending(1, 2)
        return ok, msg, rec, before, after, pending

    ok, msg, rec, before, after, pending = _in_tmp(run)
    check("C1 预览成功", ok, msg)
    check("C1 正文与预览前**完全一致**（一个字节都没变）", before == after,
          (before[:40], after[:40]))
    check("C1 返回值里带改后文本", NEW_P2 in (rec or {}).get("after", ""), rec and rec.get("after"))
    check("C2 候选进了 para_pending.json", bool(pending), pending)
    check("C2 候选里同时有改前与改后",
          pending and NEW_P2 in pending.get("after", "")
          and "她抬眼看了他一下" in pending.get("before", ""), pending)
    check("C2 备份路径记在候选里", bool(pending and pending.get("backup")), pending and pending.get("backup"))


def case_apply():
    print("\n【C3】采用：只动目标段，旧版进段落历史")

    def run(tmp):
        chap = tmp / "data" / "chapters" / "raw" / "01.md"
        RP.preview_paragraph(CFG, PROJ, 1, 2, "改得克制一点", client=_FakeClient(chap))
        before_text = chap.read_text(encoding="utf-8")
        ok, msg = RP.apply_pending(1, 2)
        after_text = chap.read_text(encoding="utf-8")
        hist = RP.list_paragraph_history(1, 2)
        return ok, msg, before_text, after_text, hist, RP.get_pending(1, 2)

    ok, msg, before_text, after_text, hist, pending = _in_tmp(run)
    check("C3 采用成功", ok, msg)
    check("C3 目标段换成了候选", "把伞往他那边偏了偏" in after_text, after_text[:80])
    check("C3 其它段原样（第一段还在）", "他在码头下船" in after_text)
    check("C3 旧版进了段落历史", bool(hist), hist)
    check("C3 候选已清掉", pending is None, pending)
    check("C3 采用前正文确实是旧的（说明不是它自己改的）", "把伞往他那边偏了偏" not in before_text)


def case_drop():
    print("\n【C4】放弃：候选清掉，正文不动")

    def run(tmp):
        chap = tmp / "data" / "chapters" / "raw" / "01.md"
        RP.preview_paragraph(CFG, PROJ, 1, 2, "改得克制一点", client=_FakeClient(chap))
        before_text = chap.read_text(encoding="utf-8")
        ok, msg = RP.drop_pending(1, 2)
        after_text = chap.read_text(encoding="utf-8")
        return ok, msg, before_text, after_text, RP.get_pending(1, 2)

    ok, msg, before_text, after_text, pending = _in_tmp(run)
    check("C4 放弃成功", ok, msg)
    check("C4 正文一个字没动", before_text == after_text)
    check("C4 候选清掉了", pending is None)


def case_self_draft():
    print("\n【C5】self 模式：任务书 → 草稿 → 判据 → 采用")

    def run(tmp):
        info = SA.write_paragraph_task(CFG, PROJ, 1, 2, "改得克制一点")
        task = Path(info["task"]).read_text(encoding="utf-8")
        not_yet = SA.check_paragraph_draft(1, 2)
        Path(info["draft"]).write_text(NEW_P2, encoding="utf-8")
        okcheck = SA.check_paragraph_draft(1, 2)
        ok, msg = SA.apply_paragraph_draft(1, 2)
        text = (tmp / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
        return info, task, not_yet, okcheck, ok, msg, text

    info, task, not_yet, okcheck, ok, msg, text = _in_tmp(run)
    check("C5 任务书写明“只写这一段正文”", "只放**这一段改后的正文**" in task or "只这一段" in task,
          task[:160])
    check("C5 任务书带原文与上下文", "她在码头" in task or "她抬眼看了他一下" in task)
    check("C5 没草稿时判不合格", not_yet["ok"] is False and not_yet["reasons"], not_yet)
    check("C5 草稿合格", okcheck["ok"], okcheck["reasons"])
    check("C5 采用成功并可回退（写盘走 save_paragraph_text）", ok, msg)
    check("C5 正文换成了草稿", "把伞往他那边偏了偏" in text)
    check("C5 草稿用完删掉", not SA.para_draft_path(1, 2).is_file())


def case_reject_bad_draft():
    print("\n【C5b】草稿不合格 → 不许采用")
    def run(tmp):
        info = SA.write_paragraph_task(CFG, PROJ, 1, 2, "改得克制一点")
        Path(info["draft"]).write_text("她抬眼看了他一下，没有说话。", encoding="utf-8")  # 原样复制
        return SA.check_paragraph_draft(1, 2), SA.apply_paragraph_draft(1, 2)

    info, applied = _in_tmp(run)
    ok, msg = applied
    check("C5b 原样复制判不合格", info["ok"] is False and "一模一样" in "；".join(info["reasons"]),
          info["reasons"])
    check("C5b 采用被拒", ok is False, msg)


def main():
    print("=" * 66)
    print("改一段：候选 → 采用（P1-5）")
    case_preview_rollback()
    case_apply()
    case_drop()
    case_self_draft()
    case_reject_bad_draft()
    print("\n" + "-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
