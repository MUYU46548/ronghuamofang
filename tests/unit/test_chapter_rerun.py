# -*- coding: utf-8 -*-
"""重跑单章＝合并不清空（2026-11-04，task-20261104-002）。

## 锁的是什么

用户口径（问卷 `mofang.rerun.merge`）：**单章重跑不触发其他章重算**。

此前"重跑第 N 章"只有两条路，都不满足：
- `/chapters/rewrite`：只重写 raw，`checked/refined/评审` 还是旧的；
- 「从阶段 N 重跑」：`reject.py` 清**整本**该阶段及下游 —— 40 章的书为 1 章重烧 39 章。

本用例跑**真 stage4/5/6 + FakeClient**，判据落在**哈希与 mtime**上：

A. prepare：目标章三层旧产物移到备份、其他章文件一个字节不动；
B. 进度：只清第 3 章的标记，`approved` 不动，别的阶段状态不动；
C. 重跑后：第 1/2/4 章三层稿 sha256 与 mtime 全不变；第 3 章三层都焕新；
D. 汇总：rolling.md 的第 3 章段用新稿、其他章段不变；
E. 影响面预览（inventory）与实际动作同源。

全程离线、零 LLM。用法：python tests/unit/test_chapter_rerun.py
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.pop("NF_ROOT", None)

from utils.fake_client import FakeClient                # noqa: E402
from utils.progress_manager import ProgressManager      # noqa: E402
from utils import chapter_rerun as CR                   # noqa: E402
import stage4_writing as s4                             # noqa: E402
import stage5_check as s5                               # noqa: E402
import stage6_polish as s6                              # noqa: E402
import orchestrator                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:300]) if detail else ""))


SETTING = {
    "characters": [{"id": "luxi", "name": "苏芷", "role": "主角",
                    "traits": ["冷静"], "source": "fake"}],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [], "timeline": [],
    "_meta": {"version": 1, "generated_from": ["fake"]},
}


class ScopedFakeClient(FakeClient):
    """stage6 只写"任务名里那一章"。

    真实 stage6 是**一章一请求**（`stage6_polish_vol0_ch03.md`）；FakeClient 的
    实现却把 `checked/` 全量复制到 `refined/` —— 那是假的实现细节，会掩盖
    "重跑单章是否碰了别的章"这个判据。这里与真实语义对齐。
    """

    def run_task(self, task_file, workdir=None, model=None, session_id=None):
        name = Path(task_file).name
        m = re.search(r"stage6.*_ch(\d+)\.md$", name)
        if m:
            n = m.group(1)
            from utils.file_io import read_text as _rt, write_text as _wt
            src = Path("data/chapters/checked/%s.md" % n)
            dst = Path("data/chapters/refined/%s.md" % n)
            dst.parent.mkdir(parents=True, exist_ok=True)
            _wt(dst, _rt(src))
            return {"exit_code": 0, "stdout_tail": "（fake）scoped stage6",
                    "tokens": 100, "tokens_out": 50, "cost_yuan": 0.0,
                    "estimated": True, "provider": "fake", "model": "fake",
                    "requests": 1}
        return super().run_task(task_file, workdir=workdir, model=model)


def build_workspace(total=4):
    tmp = Path(tempfile.mkdtemp(prefix="nf_rerun_"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "重跑测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    sys_yaml = (ROOT / "config" / "system.yaml").read_text(encoding="utf-8")
    # 裁定闸默认 block：本用例要验证"4→5→6 一条链"，先关掉人工门
    # （人工门本身由 test_stage5_gate_integration 锁）。
    sys_yaml = sys_yaml.replace("stage5_diff: block", "stage5_diff: off")
    # FakeClient 的 stage6 会自己复制整批 checked→refined；并发 3 卷会在同一
    # 目标文件上抢写（WinError 32）。本用例验的是"范围"，不是并发。
    sys_yaml = sys_yaml.replace("  polish: 3", "  polish: 1")
    (tmp / "config" / "system.yaml").write_text(sys_yaml, encoding="utf-8")
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 重跑测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n"
            "- 功能：承接\n" % n, encoding="utf-8")
    return tmp


def _sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _stamp(root: Path, n: int):
    """该章三层稿的 (sha256, mtime_ns)。缺失层记 None。"""
    out = {}
    for layer in ("raw", "checked", "refined"):
        p = root / "data" / "chapters" / layer / ("%02d.md" % n)
        out[layer] = (_sha(p), p.stat().st_mtime_ns) if p.exists() else None
    return out


def main():
    tmp = build_workspace(4)
    origin = os.getcwd()
    try:
        os.chdir(tmp)
        from utils.config_io import load_config_yaml
        cfg = load_config_yaml("config/system.yaml")
        proj = load_config_yaml("config/project.yaml")
        cfg_line = (tmp / "config" / "system.yaml").read_text(encoding="utf-8")
        assert "stage5_diff: off" in cfg_line, "夹具没把裁定闸关掉"

        # ---- 造出"已经跑完 4 章"的测试项目 ----
        prog = ProgressManager(tmp / "data" / "state" / "progress.json")
        ok, msg = s4.run_stage(cfg, proj, prog, None, None, client=ScopedFakeClient(),
                               task_dir=str(tmp / "data" / "state" / "tasks"))
        check("前置：阶段 4 跑完 4 章", ok, msg)
        ok, msg = s5.run_stage(cfg, proj, prog, None, None, client=ScopedFakeClient(),
                               task_dir=str(tmp / "data" / "state" / "tasks"))
        check("前置：阶段 5 跑完", ok, msg)
        ok, msg = s6.run_stage(cfg, proj, prog, None, None, client=ScopedFakeClient(),
                               task_dir=str(tmp / "data" / "state" / "tasks"))
        check("前置：阶段 6 跑完", ok, msg)
        for layer in ("raw", "checked", "refined"):
            check("前置：%s 层 4 章齐" % layer,
                  len(list((tmp / "data" / "chapters" / layer).glob("*.md"))) == 4)

        before = {n: _stamp(tmp, n) for n in (1, 2, 3, 4)}
        rolling_before = (tmp / "data" / "summaries" / "rolling.md").read_text(encoding="utf-8")
        # 标记"阶段 5 已审批"，重跑单章不该把它抹掉
        prog.set_approved(5, True)
        prog.set_approved(4, True)
        stage13_before = {k: json.dumps(prog.data["stages"][str(k)], sort_keys=True)
                          for k in (1, 2, 3)}

        print("\n【A】影响面预览与实际动作同源")
        inv = CR.inventory(tmp, 3, 6)
        paths = {i["path"] for i in inv}
        check("预览含 raw/checked/refined/评审",
              any("raw/03.md" in p for p in paths)
              and any("checked/03.md" in p for p in paths)
              and any("refined/03.md" in p for p in paths)
              and any("review/03.json" in p for p in paths), sorted(paths))

        print("\n【B】prepare：只作废第 3 章，其他章原封不动")
        info = CR.prepare(tmp, 3, through=6)
        check("生成了整轮快照（备份不了就不许动）", bool(info.get("snapshot")), info)
        raw3 = tmp / "data" / "chapters" / "raw" / "03.md"
        check("第 3 章 raw 已移走（否则 stage4 判已完成）", not raw3.exists())
        for layer in ("checked", "refined"):
            p = tmp / "data" / "chapters" / layer / "03.md"
            check("第 3 章 %s 已移走" % layer, not p.exists())
        check("旧产物有备份目录", bool(info.get("backup_dir"))
              and (tmp / info["backup_dir"]).is_dir(), info.get("backup_dir"))
        check("备份里有旧 checked/03",
              (tmp / info["backup_dir"] / "checked" / "03.md").exists())
        for n in (1, 2, 4):
            check("第 %d 章三层没被 prepare 碰到" % n, _stamp(tmp, n) == before[n])
        p2 = ProgressManager(tmp / "data" / "state" / "progress.json")
        check("完成标记只清第 3 章",
              3 not in p2.completed_chapters(4) and {1, 2, 4} <= set(p2.completed_chapters(4)),
              p2.completed_chapters(4))
        check("approved 不动（阶段 4/5 仍为已批）",
              bool(p2.data["stages"]["4"].get("approved"))
              and bool(p2.data["stages"]["5"].get("approved")))
        check("阶段 4/5/6 置 pending 以便重跑",
              all(p2.data["stages"][str(k)]["status"] == "pending" for k in (4, 5, 6)),
              [p2.data["stages"][str(k)]["status"] for k in (4, 5, 6)])

        print("\n【C】真跑 4→5→6（只写第 3 章）")
        os.environ["NF_STAGE4_ONLY"] = "3"
        os.environ["NF_SKIP_POST_STAGE"] = "1"
        os.environ["NF_RUN_PLAN"] = "rerun_ch03 through=6"
        try:
            rc = orchestrator.run(from_stage=4, stages=[4, 5, 6],
                                  client=ScopedFakeClient(), chapters={3})
        finally:
            for k in ("NF_STAGE4_ONLY", "NF_SKIP_POST_STAGE", "NF_RUN_PLAN"):
                os.environ.pop(k, None)
        check("重跑 run 正常结束（exit=0）", rc == 0, rc)
        _dbg = ProgressManager(tmp / "data" / "state" / "progress.json")
        print("  [DEBUG] 阶段状态：", {k: (v or {}).get("status")
                                       for k, v in (_dbg.data.get("stages") or {}).items()})

        after = {n: _stamp(tmp, n) for n in (1, 2, 3, 4)}
        for n in (1, 2, 4):
            check("第 %d 章三层 sha256 + mtime **都不变**" % n, after[n] == before[n],
                  {"before": before[n], "after": after[n]})
        for layer in ("raw", "checked", "refined"):
            check("第 3 章 %s 已重算（mtime 变了）" % layer,
                  after[3][layer] and before[3][layer]
                  and after[3][layer][1] != before[3][layer][1],
                  {"before": before[3][layer], "after": after[3][layer]})
        check("阶段 5 的旧改稿提案没有残留",
              not (tmp / "data" / "chapters" / "checked_pending" / "03.md").exists())

        print("\n【D】汇总只动第 3 章")
        rolling_after = (tmp / "data" / "summaries" / "rolling.md").read_text(encoding="utf-8")
        for n in (1, 2, 4):
            seg_b = rolling_before.split("### 第%d章" % n)[-1].split("###")[0]
            seg_a = rolling_after.split("### 第%d章" % n)[-1].split("###")[0]
            check("rolling.md 第 %d 章段不变" % n, seg_b == seg_a)
        app = json.loads((tmp / "data" / "state" / "appearances.json")
                         .read_text(encoding="utf-8"))
        check("出场记录仍是全书口径（4 章）", app.get("total_chapters") == 4,
              app.get("total_chapters"))

        print("\n【E】进度语义：别的阶段一个字段都不动")
        p3 = ProgressManager(tmp / "data" / "state" / "progress.json")
        for k in (1, 2, 3):
            check("阶段 %d 进度未变" % k,
                  json.dumps(p3.data["stages"][str(k)], sort_keys=True) == stage13_before[k])
        check("阶段 4 回到 done", p3.data["stages"]["4"]["status"] == "done",
              p3.data["stages"]["4"].get("status"))
        check("阶段 4 的 rerun 留痕指向第 3 章",
              p3.data["stages"]["4"].get("rerun_chapter") == 3)
        check("全书 4 章仍都在 completed_chapters",
              set(p3.completed_chapters(4)) >= {1, 2, 3, 4}, p3.completed_chapters(4))

        print("\n【F】端点接线（静态判据）")
        domain = (ROOT / "scripts" / "nf_api_domains" / "chapters.py").read_text(encoding="utf-8")
        api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        orc_src = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
        check("域模块有 /chapters/rerun 与影响面预览",
              "handle_chapters_rerun" in domain and "/chapters/rerun/plan" in domain)
        check("分发器有两条转发", "\"/chapters/rerun\"" in api_src
              and "\"/chapters/rerun/plan\"" in api_src)
        check("章范围扩到 5/6", "n in (4, 5, 6)" in orc_src)
        check("上游未放行不许进下游的闸在", "尚未放行" in orc_src)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  · " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
