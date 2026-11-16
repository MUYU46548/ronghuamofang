# -*- coding: utf-8 -*-
"""第 4 批能力：整本视角 / 外派耐久性 / 答案变动作 / 手册讨论篇（C4/C7/C8/C22）。

## 四条各自的判据

- **C4 整本视角**：`book_pack()` 给出设定/大纲/判定/伏笔/摘要/讨论/口径/轮次/成本
  九块；缺什么报缺（`hints`），不编数字；**不进写作提示词**（自带说明）。
- **C7 外派耐久性**：并发有上限；能取消（真杀进程 / 杀不了要明说）；
  服务重启后上一进程留下的 `running` 会按 **pid 事实** 标成 `orphaned`。
- **C8 答案变动作**：`refine_paragraph` → 那一段挂批注；`open_discuss` → 开一篇讨论
  并把你的选择作为第一轮；两者都**不自动调模型**。
- **C22 手册**：讨论有一篇作者向的手册，讲清"一轮是什么/钱花在哪/停不下来怎么办"。

用法：python tests/unit/test_batch4_capabilities.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api                                                # noqa: E402
import nf_agent_dispatch as disp                             # noqa: E402
from utils import ask, context_report as cr, discussion       # noqa: E402
from nf_api_domains import misc as dom_misc                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def _old_stamp(minutes=120):
    import datetime
    return (datetime.datetime.now() - datetime.timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S")


def prep_root():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "setting").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "summaries").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "logs").mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "project.yaml").write_text(
        "book:\n  name: 自造测试书\n  chapters: 3\n", encoding="utf-8")
    (tmp / "data" / "setting" / "setting.json").write_text(json.dumps({
        "characters": [{"name": "何望", "locked": True}, {"name": "白榆"}],
        "world": {"地点": ["机房：城外的供水点"]},
        "timeline": [{"time": "第 1 天", "event": "开场"}],
        "plot_fragments": [],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text("## 卷\n- 三章\n", encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text("## 第1章\n", encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "02.md").write_text("## 第2章\n", encoding="utf-8")
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text(
        "## 第 1 章 试\n\n他把伞靠在门边，雨水顺着伞骨淌下来。\n", encoding="utf-8")
    from utils.summary_chain import write_rolling
    write_rolling(tmp / "data" / "summaries" / "rolling.md",
                  {"global_summary": "进展一般。", "chapters": {"1": "他回来了。"}})
    return tmp


def main():
    print("=" * 66)
    print("第 4 批：整本视角 / 外派耐久性 / 答案变动作 / 手册（C4/C7/C8/C22）")
    tmp = prep_root()
    orig_root = nf_api.ROOT
    nf_api.ROOT = tmp
    try:
        print("\n【1】整本视角（C4）")
        pack = cr.book_pack(tmp, {"chapter": {"target_words": [4000, 7200]}})
        check("C4 九块都在",
              all(k in pack for k in ("book", "setting", "outline", "judge", "foreshadow",
                                      "rolling", "discuss", "glossary", "rounds", "cost")),
              list(pack))
        check("C4 设定集数出来了",
              pack["setting"]["characters"] == 2 and pack["setting"]["locked"] == 1, pack["setting"])
        check("C4 大纲缺章算出来了",
              pack["outline"]["chapter_files"] == 2 and pack["outline"]["missing"] == [3],
              pack["outline"])
        check("C4 滚动摘要认到第 1 章", pack["rolling"].get("covered") == 1, pack["rolling"])
        check("C4 明确写了不进写作提示词", "不进写作提示词" in pack["hint"], pack["hint"])
        check("C4 没有 runs.db 时不编成本",
              pack["cost"].get("calls") == 0 and "note" in pack["cost"], pack["cost"])

        api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        check("C4 路由登记", any(p == "/context/book" for _m, p, _f in dom_misc.ROUTES))
        check("C4 分发器接上", "dom_misc.handle_context_book" in api_src)

        print("\n【2】外派耐久性（C7）")
        check("C7 自进程判活为 True", disp._pid_alive(os.getpid()) is True)
        dead = 999999 if os.name != "nt" else 999998
        check("C7 死 pid 判 False（判不了也算不通过）",
              disp._pid_alive(dead) in (False, None), disp._pid_alive(dead))

        runs = tmp / "data" / "state" / "agent_runs.jsonl"
        runs.write_text("\n".join([
            # created_at 用"两小时前"：刚派的活由 watcher 收尾，重扫不碰
            # （FRESH_SECONDS=120，防"进程刚退出、watcher 还没写终态"被误标）。
            json.dumps({"run_id": "r_dead", "status": "running", "pid": dead,
                        "title": "死进程", "created_at": _old_stamp()}),
            json.dumps({"run_id": "r_alive", "status": "running", "pid": os.getpid(),
                        "title": "活进程", "created_at": _old_stamp()}),
        ]) + "\n", encoding="utf-8")
        os.environ["NF_ROOT"] = str(tmp)
        try:
            done = disp.reconcile_orphans(force=True)
            check("C7 死进程被标 orphaned", "r_dead" in done, done)
            latest = {r.get("run_id"): r for r in disp.list_agent_runs(10)}
            check("C7 orphaned 落盘", latest.get("r_dead", {}).get("status") == "orphaned",
                  latest.get("r_dead"))
            check("C7 活进程不动", latest.get("r_alive", {}).get("status") == "running",
                  latest.get("r_alive"))

            os.environ["NF_DISPATCH_MAX_CONCURRENT"] = "1"
            check("C7 并发上限读得到", disp._max_concurrent() == 1)
            class FakeProc:
                pid = os.getpid()
            disp._ACTIVE["r_fake"] = FakeProc()
            try:
                res = disp.dispatch_to_hermes("测试并发", "上下文", source="test")
                check("C7 到上限就明确拒派", res.get("ok") is False
                      and "最多" in str(res.get("error")), res)
            finally:
                disp._ACTIVE.pop("r_fake", None)
                os.environ.pop("NF_DISPATCH_MAX_CONCURRENT", None)

            # 取消：起一个真会睡的子进程，塞进 _ACTIVE，取消它
            p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
            disp._ACTIVE["r_kill"] = p
            disp._append_run({"run_id": "r_kill", "status": "running", "pid": p.pid,
                              "title": "要取消的", "created_at": _old_stamp(0)})
            out = disp.cancel_run("r_kill")
            time.sleep(1.0)
            check("C7 取消成功", out.get("ok") is True and out.get("status") == "cancelled", out)
            check("C7 真把进程杀了（或明确说没杀到）",
                  out.get("killed") in (True, False), out)
            check("C7 记录终态是 cancelled",
                  disp._latest_run("r_kill").get("status") == "cancelled",
                  disp._latest_run("r_kill"))
            try:
                p.kill()
            except Exception:                                  # noqa: BLE001
                pass
            disp._ACTIVE.pop("r_kill", None)

            out2 = disp.cancel_run("根本没这趟")
            check("C7 取消不存在的 run 不炸", out2.get("ok") is False and "不存在" in str(out2), out2)
            check("C7 CLI 有 --cancel / --reconcile",
                  "--cancel" in (ROOT / "scripts" / "nf_agent_dispatch.py").read_text(encoding="utf-8"))
            check("C7 API 路由登记", any(p == "/agent/cancel" for _m, p, _f in dom_misc.ROUTES))
            check("C7 API 分发器接上", "handle_agent_cancel" in api_src)
        finally:
            os.environ.pop("NF_ROOT", None)

        print("\n【3】答案变动作（C8）")
        body = tmp / "data" / "chapters" / "raw" / "01.md"
        paras = body.read_text(encoding="utf-8").split("\n\n")
        qid, _ = ask.upsert(str(tmp), {
            "question": "这一段要不要收住？",
            "options": [{"value": "rewrite", "label": "改"}, {"value": "keep", "label": "保留"}],
            "effect": "refine_paragraph",
            "target": {"chapter": 1, "paragraph_index": 1},
        })
        ok, msg, item = ask.answer(str(tmp), qid, value="rewrite", by="author")
        ann_file = tmp / "data" / "state" / "annotations" / "ch01.json"
        check("C8 refine_paragraph 作答成功", ok is True, msg)
        check("C8 批注落在目标段", ann_file.exists(), ann_file)
        if ann_file.exists():
            items = json.loads(ann_file.read_text(encoding="utf-8"))["annotations"]
            check("C8 批注内容带问题与选择",
                  bool(items) and "作者已定" in items[0]["note"] and "这一段要不要收住" in items[0]["note"],
                  items[0]["note"] if items else None)
            check("C8 批注指到第 1 段（0 起）", items and items[0]["para_index"] == 1,
                  items[0].get("para_index") if items else None)

        qid2, _ = ask.upsert(str(tmp), {
            "question": "结局要不要开信？",
            "options": [{"value": "yes", "label": "开"}, {"value": "no", "label": "不开"}],
            "effect": "open_discuss",
            "topic": "结局要不要开信",
            "target": {"chapter": 1},
        })
        ok2, msg2, _ = ask.answer(str(tmp), qid2, value="yes", by="author")
        threads = discussion.list_threads(1, str(tmp))
        check("C8 open_discuss 开出了讨论", ok2 is True and any(
            t.get("topic") == "结局要不要开信" for t in threads), threads)
        md, _js = discussion.thread_paths(1, "结局要不要开信", str(tmp))
        text = md.read_text(encoding="utf-8") if md.exists() else ""
        check("C8 讨论里第一轮是作者的选择", "第 1 轮 · 作者" in text, text[-160:])

        print("\n【4】手册讨论篇（C22）")
        help_dir = ROOT / "console" / "src" / "help"
        hp = help_dir / "10-discuss.md"
        check("C22 手册文件存在", hp.exists(), hp)
        if hp.exists():
            text = hp.read_text(encoding="utf-8")
            check("C22 标题是作者向的一句话", text.startswith("# 讨论："), text[:40])
            for kw in ("让 AI 说这一轮", "让 AI 闭嘴", "候选", "定稿", "压缩老轮次", "没记进讨论"):
                check("C22 讲了“%s”" % kw, kw in text)
        check("C22 手册目录按文件名排序加载（10- 前缀在 09 之后）",
              sorted(p.name for p in help_dir.glob("*.md"))[-1].startswith("10-"),
              sorted(p.name for p in help_dir.glob("*.md"))[-1:])
    finally:
        nf_api.ROOT = orig_root

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
