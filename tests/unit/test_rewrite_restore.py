# -*- coding: utf-8 -*-
"""失败重写自动恢复旧稿（2026-10-27 数据安全网）。

## 为什么需要它

`_prepare_chapter_rewrite` 把 `raw/NN.md` **移走**（必须移：留着的话 stage4 判该章
已完成、根本不重写）。可重写一旦失败，旧稿就**没有人负责放回** —— 实测《潮汐档案》
第 3 章模型连续三次「未生成章节文件」→ raw/03.md 缺失、stage4 failed、
completed_chapters 少一章，界面看起来像章节被删了，旧稿只剩在 rewrite_backup/。

## 判据

A. 失败且新稿缺失 → 备份被**复制**回 raw/（备份仍在）、完成标记补回、失败留痕清掉
B. 新稿已存在（哪怕半成品）→ 绝不覆盖
C. 没有备份 → 不动任何东西，文案说清「需人工捞回」
D. 阶段状态按「全书章是否都齐」定 done / pending；失败那轮的 started_at/finished_at 不残留
E. 两条 job 路径（普通 / 流式）与异常穿透都接上收尾

全程离线、零 LLM。
用法：python tests/unit/test_rewrite_restore.py
"""
import io
import os
import queue
import shutil
import sys
import tempfile
import threading
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.pop("NF_ROOT", None)

from utils.progress_manager import ProgressManager      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:260]) if detail else ""))


def build_ws(total=3, written=None, tag="rr"):
    """最小工作区：total 章的书，其中前 written 章有合格正文且标完成。"""
    written = total if written is None else written
    tmp = Path(tempfile.mkdtemp(prefix="nf_rr_%s_" % tag))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "恢复测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    (tmp / "config" / "system.yaml").write_text(
        (ROOT / "config" / "system.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    raw = tmp / "data" / "chapters" / "raw"
    raw.mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    for i in range(1, written + 1):
        (raw / ("%02d.md" % i)).write_text(
            "## 第%d章\n\n" % i + ("旧稿正文%d。" % i) * 50, encoding="utf-8")
    prog = ProgressManager(tmp / "data" / "state" / "progress.json")
    prog.data["stages"]["4"] = {"status": "done",
                                "completed_chapters": list(range(1, written + 1))}
    prog.save()
    return tmp


def main():
    import nf_api

    origin = os.getcwd()
    tmp = build_ws(total=3, tag="a")
    try:
        os.chdir(tmp)
        nf_api._set_root(tmp)
        raw = tmp / "data" / "chapters" / "raw"
        prog_path = tmp / "data" / "state" / "progress.json"
        before = (raw / "02.md").read_text(encoding="utf-8")

        print("\n【A】失败且新稿缺失 → 复制回 raw + 补完成标记 + 清失败留痕")
        bak = nf_api._prepare_chapter_rewrite(2)
        check("前置：raw/02.md 已被移走", not (raw / "02.md").exists(),
              sorted(p.name for p in raw.glob("*.md")))
        # 模拟真实的失败现场：stage4 标 failed，留了失败章与运行时刻
        prog = ProgressManager(prog_path)
        prog.set_stage(4, "failed", failed_count=1)
        prog.mark_chapter_failed(2, "章节文件未生成")
        st = prog.data["stages"]["4"]
        st["started_at"] = "2020-01-01T00:00:00"
        st["finished_at"] = "2020-01-01T00:00:01"
        prog.save()

        ok, detail = nf_api._rewrite_job_result(2, False, "exit=1", True)
        check("job 结果仍是失败（不伪装成功）", ok is False, ok)
        check("文案写明「旧稿已自动恢复」", "旧稿已自动恢复" in detail, detail)
        check("raw/02.md 回来了", (raw / "02.md").exists())
        check("恢复内容 = 重写前原文",
              (raw / "02.md").read_text(encoding="utf-8") == before)
        check("备份仍在（复制不是移动，可二次反悔）",
              bool(bak) and (tmp / bak).exists(), bak)
        prog2 = ProgressManager(prog_path)
        check("完成标记补回", 2 in prog2.completed_chapters(4),
              prog2.completed_chapters(4))
        check("该章的失败留痕已清",
              all(f["n"] != 2 for f in prog2.failed_chapters(4)),
              prog2.failed_chapters(4))
        check("全书齐 → 阶段回到 done", prog2.stage_status(4) == "done",
              prog2.stage_status(4))
        st2 = prog2.data["stages"]["4"]
        check("失败那轮的 started_at 不残留", "started_at" not in st2, st2)
        check("失败那轮的 finished_at 被换成新的",
              st2.get("finished_at") != "2020-01-01T00:00:01", st2.get("finished_at"))

        print("\n【B】新稿已在盘上（哪怕半成品）→ 绝不覆盖")
        (raw / "02.md").write_text("## 第2章\n\n新稿半成品。", encoding="utf-8")
        ret = nf_api._restore_chapter_after_failed_rewrite(2)
        check("不动手（返回 None）", ret is None, ret)
        check("新稿原样保留",
              "新稿半成品" in (raw / "02.md").read_text(encoding="utf-8"))

        print("\n【C】没有备份 → 不编造，文案说清要人工捞回")
        (raw / "01.md").unlink()
        ret = nf_api._restore_chapter_after_failed_rewrite(1)
        check("没有备份 → 不动手", ret is None, ret)
        check("raw/01.md 仍然不存在（没有凭空造文件）", not (raw / "01.md").exists())
        ok, detail = nf_api._rewrite_job_result(1, False, "exit=1", True)
        check("有旧稿但恢复失败 → 明确指出手工捞回路径",
              "rewrite_backup" in detail, detail)
        ok, detail = nf_api._rewrite_job_result(1, False, "exit=1", False)
        check("本来就没有旧稿（新书）→ 不改写失败文案",
              detail == "exit=1", detail)

        print("\n【D】全书没齐 → 阶段回 pending（不虚报 done）")
        tmp2 = build_ws(total=5, written=3, tag="b")
        try:
            os.chdir(tmp2)
            nf_api._set_root(tmp2)
            raw2 = tmp2 / "data" / "chapters" / "raw"
            nf_api._prepare_chapter_rewrite(3)
            nf_api._restore_chapter_after_failed_rewrite(3)
            prog3 = ProgressManager(tmp2 / "data" / "state" / "progress.json")
            check("5 章的书只写到 3 章 → status=pending",
                  prog3.stage_status(4) == "pending", prog3.stage_status(4))
            check("第 3 章仍被标完成（有稿就是有稿）",
                  3 in prog3.completed_chapters(4), prog3.completed_chapters(4))
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
            os.chdir(tmp)
            nf_api._set_root(tmp)

        print("\n【E】两条 job 路径都接上收尾（不接 = 安全网只在单测里成立）")
        # 普通路径：把工厂换成"必定失败"，不碰 LLM
        orig_f = nf_api.act_run_stage
        nf_api.act_run_stage = lambda cfg, only_stage, from_stage: (
            lambda: (False, "exit=1"))
        try:
            ok, detail = nf_api.act_rewrite_chapter({}, 3)()
        finally:
            nf_api.act_run_stage = orig_f
        check("普通路径：结果失败", ok is False, ok)
        check("普通路径：旧稿已恢复", "旧稿已自动恢复" in detail, detail)
        check("普通路径：raw/03.md 存在", (raw / "03.md").exists())
        check("普通路径：环境变量已清干净",
              "NF_STAGE4_ONLY" not in os.environ
              and "NF_SKIP_POST_STAGE" not in os.environ)

        # 异常穿透：工厂抛错也要先把旧稿放回，再把异常抛出去
        def _boom(cfg, only_stage, from_stage):
            def _fn():
                raise RuntimeError("模拟崩溃")
            return _fn
        orig_f = nf_api.act_run_stage
        nf_api.act_run_stage = _boom
        (raw / "03.md").unlink()
        raised = False
        try:
            nf_api.act_rewrite_chapter({}, 3)()
        except RuntimeError:
            raised = True
        finally:
            nf_api.act_run_stage = orig_f
        check("异常穿透：runtime error 照常抛出", raised)
        check("异常穿透：旧稿照样恢复（崩溃不留缺章的书）",
              (raw / "03.md").exists())
        check("异常穿透：环境变量已清干净",
              "NF_STAGE4_ONLY" not in os.environ
              and "NF_SKIP_POST_STAGE" not in os.environ)

        # 流式路径
        orig_s = nf_api.act_run_stage_streamed
        nf_api.act_run_stage_streamed = lambda *a, **k: (lambda: (False, "exit=1"))
        (raw / "03.md").unlink()
        try:
            ok, detail = nf_api.act_rewrite_chapter_streamed(
                {}, 3, "preview", queue.Queue(), threading.Event())()
        finally:
            nf_api.act_run_stage_streamed = orig_s
        check("流式路径：结果失败", ok is False, ok)
        check("流式路径：旧稿已恢复", "旧稿已自动恢复" in detail, detail)
        check("流式路径：raw/03.md 存在", (raw / "03.md").exists())

        print("\n【F】源码判据：安全网接在收尾处，且有「不覆盖新稿」的守卫")
        src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        check("收尾函数存在且被两条 job 路径调用",
              src.count("_rewrite_job_result(") >= 3, src.count("_rewrite_job_result("))
        check("恢复函数有「新稿存在就不动手」的守卫",
              "if raw.exists():" in src)
        check("恢复正常用 copy2（复制，不是 move）",
              "shutil.copy2(str(src), str(raw))" in src)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)
        nf_api._set_root(ROOT)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
