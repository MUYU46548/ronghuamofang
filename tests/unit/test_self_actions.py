# -*- coding: utf-8 -*-
"""按钮级派单：整章重写 → 草稿 → 你采用才落盘（2026-11-08）。

## 要治的病（暮雨原话）

“精修大纲、提精修、段落重写、整章重写、运行审查……这些全是有用的东西，
如果不能点还有什么意义？难道绒花墨坊根本没搞定和 agent 的交互吗”

对。阶段级任务书早就通了（1~6），**按钮级没有派单通道** —— 一点就是“用不了”。
这个用例锁的是补上的那三段：出任务书 → 判草稿 → 采用（带备份）。

## 判据（每条都能变红）

- S1 出任务书：含现有正文全文、写哪个草稿路径、判据（字数区间）、不写正式稿
- S2 判完成只看盘上草稿：没有草稿 → 不合格；太短 / 缺标题 / 含占位符 → 逐条说原因
- S3 合格草稿 → ok；采用 = 备份旧稿 + 换正式稿（旧稿在 rewrite_backup 里）
- S4 放弃草稿：草稿没了，正式稿**一个字节不动**
- S5 没写意见也能出任务书（意见可选）；不存在的章 → 400 而不是写个空任务
- S6 接线：三条路由 + nf_api 转发

用法：python tests/unit/test_self_actions.py
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


from utils import self_actions as sa                                 # noqa: E402
from nf_api_domains import refine as dom_refine                      # noqa: E402

CFG = {"chapter": {"target_words": [4000, 7000]}}
PROJ = {"book": {"chapters": 10}}

CH1 = """## 第1章 入境

他在码头下船，风把盐味推进喉咙。

<!-- summary: 柯砚入境。 -->
<!-- quality: 7/10 -->
"""

LONG = "## 第1章 入境\n\n" + ("他推门进来，屋檐的水线连成串，地上积了一小滩暗色的水。" * 260) + \
       "\n\n<!-- summary: 新写的一版。 -->\n<!-- quality: 8/10 -->\n"


class _FakeH:
    headers = {}


def _setup():
    tmp = Path(tempfile.mkdtemp(prefix="nf_sa_"))
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True)
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text(CH1, encoding="utf-8")
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text(
        "## 第1章 大纲\n- 核心事件：他下船，被扣下问话。\n", encoding="utf-8")
    return tmp


def _in_tmp(fn):
    tmp = _setup()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        return fn(tmp)
    finally:
        os.chdir(cwd)


def case_task():
    print("\n【S1】出任务书：自包含，且不碰正式稿")

    def run(tmp):
        before = (tmp / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
        info = sa.write_chapter_task(CFG, PROJ, 1, "把开头写得更冷一点")
        after = (tmp / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
        task = Path(info["task"]).read_text(encoding="utf-8")
        return info, task, before == after, Path(info["draft"])

    info, task, untouched, draft = _in_tmp(run)
    check("S1 任务书落盘", Path(info["task"]).name.startswith("rewrite_ch01_"), info["task"])
    check("S1 草稿路径指向 data/self_drafts", info["draft"].endswith("chapter_01.md"), info["draft"])
    check("S1 任务书含现有正文全文", "他在码头下船" in task)
    check("S1 任务书含你的意见", "把开头写得更冷一点" in task)
    check("S1 任务书写明判据的字数区间（4000~7000）", "4000~7000" in task, task[:200])
    check("S1 任务书**不**让人直接改正式稿",
          "不要**直接改 `data/chapters/raw/01.md`" in task or "不要" in task)
    check("S1 正式稿一个字节没动", untouched)
    check("S1 草稿此刻还不存在（外部还没写）", not draft.exists())


def case_check():
    print("\n【S2】判完成：只看盘上草稿，逐条说原因")
    info = _in_tmp(lambda tmp: sa.check_chapter_draft(1, CFG))
    check("S2 没草稿 → 不合格 + 说清路径", not info["ok"] and "草稿还没生成" in info["reasons"][0],
          info["reasons"])

    def short(tmp):
        (tmp / "data" / "self_drafts").mkdir(parents=True, exist_ok=True)
        (tmp / "data" / "self_drafts" / "chapter_01.md").write_text(
            "## 第1章 入境\n\n太短了。\n", encoding="utf-8")
        return sa.check_chapter_draft(1, CFG)

    short_info = _in_tmp(short)
    reasons = "；".join(short_info["reasons"])
    check("S2 太短 → 不合格", not short_info["ok"], short_info)
    check("S2 说明字数不够", "不在" in reasons and "汉字数" in reasons, reasons)

    def bad_title(tmp):
        (tmp / "data" / "self_drafts").mkdir(parents=True, exist_ok=True)
        (tmp / "data" / "self_drafts" / "chapter_01.md").write_text(
            LONG.replace("## 第1章 入境", "## 第9章 跑错了").replace("待补", "", 1) + "\n待补\n",
            encoding="utf-8")
        return sa.check_chapter_draft(1, CFG)

    bad = _in_tmp(bad_title)
    rs = "；".join(bad["reasons"])
    check("S2 标题不是这一章 → 报出来", "缺标题行" in rs, rs)
    check("S2 含占位符 → 报出来", "占位符" in rs, rs)


def case_good_and_apply():
    print("\n【S3】合格 → 采用 = 先备份再换")

    def run(tmp):
        (tmp / "data" / "self_drafts").mkdir(parents=True, exist_ok=True)
        (tmp / "data" / "self_drafts" / "chapter_01.md").write_text(LONG, encoding="utf-8")
        info = sa.check_chapter_draft(1, CFG)
        applied = sa.apply_chapter_draft(1)
        raw = (tmp / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
        backup = Path(applied["backup"]).read_text(encoding="utf-8") if applied["backup"] else ""
        return info, applied, raw, backup

    info, applied, raw, backup = _in_tmp(run)
    check("S3 合格草稿判 ok", info["ok"], info["reasons"])
    check("S3 采用后正式稿换成草稿", "新写的一版" in raw, raw[:60])
    check("S3 旧稿进了 rewrite_backup", "他在码头下船" in backup, applied["backup"])
    check("S3 备份文件真有", bool(applied["backup"]) and "rewrite_backup" in applied["backup"],
          applied["backup"])


def case_drop():
    print("\n【S4】放弃草稿：正式稿不动")
    def run(tmp):
        (tmp / "data" / "self_drafts").mkdir(parents=True, exist_ok=True)
        (tmp / "data" / "self_drafts" / "chapter_01.md").write_text(LONG, encoding="utf-8")
        res = sa.drop_chapter_draft(1)
        raw = (tmp / "data" / "chapters" / "raw" / "01.md").read_text(encoding="utf-8")
        return res, raw, (tmp / "data" / "self_drafts" / "chapter_01.md").exists()

    res, raw, exists = _in_tmp(run)
    check("S4 草稿删掉了", res.get("removed") is True and not exists, res)
    check("S4 正式稿没动", "他在码头下船" in raw)


def case_edge():
    print("\n【S5】边界：不存在的章 / 意见可空")
    def run(tmp):
        try:
            sa.write_chapter_task(CFG, PROJ, 9, "")
            return "no-raise"
        except FileNotFoundError as e:
            return str(e)

    msg = _in_tmp(run)
    check("S5 不存在的章 → 明确报错（不写空任务）", "还没有正文" in msg, msg)

    def run2(tmp):
        info = sa.write_chapter_task(CFG, PROJ, 1, "")
        return Path(info["task"]).read_text(encoding="utf-8")

    task = _in_tmp(run2)
    check("S5 没写意见也能出任务书", "没写具体意见" in task)


def case_refine_kind():
    print("\n【S7】按意见精修（kind=refine）：必须有意见，任务书措辞不同")

    def run(tmp):
        try:
            sa.write_chapter_task(CFG, PROJ, 1, "   ", kind="refine")
            return None
        except ValueError as e:
            return str(e)

    msg = _in_tmp(run)
    check("S7 没写意见 → 拒绝出任务书", bool(msg) and "必须写意见" in str(msg), msg)

    def run2(tmp):
        info = sa.write_chapter_task(CFG, PROJ, 1, "第一段的天气描写删掉，换成屋里的声音。", kind="refine")
        return info, Path(info["task"]).read_text(encoding="utf-8")

    info, task = _in_tmp(run2)
    check("S7 返回 kind=refine", info.get("kind") == "refine", info)
    check("S7 任务书说“按意见精修、别整章推翻”",
          "按意见精修" in task and "别整章推翻" in task, task[:200])
    check("S7 任务书逐条列出意见", "第一段的天气描写删掉" in task)
    check("S7 草稿仍走同一个路径（共用采纳机制）",
          info["draft"].endswith("chapter_01.md"), info["draft"])


def case_refine_handler():
    print("\n【S8】/refine/chapter 在 self 下出任务书（不再 400）")
    import nf_api

    def run(tmp):
        (tmp / "config").mkdir(parents=True, exist_ok=True)
        (tmp / "config" / "system.yaml").write_text("engine: self\n", encoding="utf-8")
        (tmp / "config" / "project.yaml").write_text(
            "book:\n  name: 测试书\n  chapters: 10\n", encoding="utf-8")
        old_root = nf_api.ROOT
        nf_api.ROOT = tmp
        try:
            return dom_refine.handle_refine_chapter(
                _FakeH(), {"chapter": 1, "feedback": "把结尾的总结句删掉"})
        finally:
            nf_api.ROOT = old_root

    code, data = _in_tmp(run)
    check("S8 返回 202 + mode=self", code == 202 and data.get("mode") == "self", (code, data))
    check("S8 给出任务书路径", bool(data.get("task")) and "{" not in str(data.get("task")),
          data.get("task"))
    check("S8 没起 job", "job_id" not in data, data)


def case_wiring():
    print("\n【S6】接线：三条路由 + 转发")
    paths = {(r[0], r[1]) for r in dom_refine.ROUTES}
    check("S6 出任务书路由", ("POST", "/self/action/rewrite_chapter") in paths)
    check("S6 草稿列表路由", ("GET", "/self/actions") in paths)
    check("S6 采用 / 放弃路由",
          ("POST", "/self/action/apply") in paths and ("POST", "/self/action/drop") in paths)
    api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("S6 nf_api 有转发（三条 POST + 一条 GET）",
          api_src.count("handle_self_action") >= 3 and "handle_self_actions_list" in api_src)


def main():
    print("=" * 66)
    print("按钮级派单：整章重写 → 草稿 → 采用")
    case_task()
    case_check()
    case_good_and_apply()
    case_drop()
    case_edge()
    case_refine_kind()
    case_refine_handler()
    case_wiring()
    print("\n" + "-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
