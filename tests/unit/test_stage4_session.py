# -*- coding: utf-8 -*-
"""P1-3 整卷会话 + 跨 run 接力（2026-11-25）。

## 判据（施工任务书 §4）

    换会话按 60% 触发（偏差 ≤ 10 个百分点）
    换会话后首章字数 ≥ 前一章 80%
    暂停 24 小时后再续，能接上（不重读全书：输入 ≤ 3 万 token）
    半章残稿存在且能被续写

本用例判能离线判的三条：

A. **跨 run 接得上**：跑完第 1 章退出进程，重开一个 ProgressManager 再跑第 2 章，
   第一次调用必须带上一轮存下的 session_id（否则“换个会话接着写”永远接不上）。
B. **水位 60% 换会话**：累计输入过线 → 写接力包 `data/state/relay/chNN.md`，
   并把 session 清空（下一章新开会话）。
C. **接力包四件齐**：决策文件 / 最近三章 / 进度 / 未回收伏笔，且要求“先复述再写”。

## 为什么用假客户端

`FakeClient` 没有会话概念，这里继承它并注入 `session_id` 与 `tokens` ——
判的是**墨坊这一侧的记账与交接**，不是 DSH 会不会真的续 —— 那一条要真机。

用法：python tests/unit/test_stage4_session.py
"""
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

from utils.fake_client import FakeClient                  # noqa: E402
from utils.progress_manager import ProgressManager        # noqa: E402
import stage4_writing as s4                               # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


SETTING = {
    "characters": [{"id": "suzhi", "name": "苏芷", "role": "主角",
                    "traits": ["冷静"], "source": "fake"}],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [], "timeline": [], "_meta": {"version": 1},
}


class SessionFake(FakeClient):
    """会报 session_id 与输入 token 的假客户端（DSH 的会话形状）。"""

    session_continuation = True

    def __init__(self, sid="sess-alpha", tokens=40000):
        super().__init__()
        self.sid = sid
        #: 每章报的输入 token（单值或按调用顺序取值，最后一项复用）
        self.tokens = tokens if isinstance(tokens, list) else [tokens]
        self.calls = []          # [(task_name, session_id)]

    def _wrap(self, task_file, session_id, res):
        self.calls.append((Path(task_file).name, session_id))
        tok = self.tokens[min(len(self.calls) - 1, len(self.tokens) - 1)]
        out = dict(res)
        out["session_id"] = self.sid
        out["tokens"] = tok
        return out

    def run_task(self, task_file, workdir=None, model=None, session_id=None):
        r = super().run_task(task_file, workdir=workdir, model=model,
                             session_id=session_id)
        return self._wrap(task_file, session_id, r)

    def run_task_stream(self, task_file, on_piece, stop_flag, workdir=None,
                        model=None, session_id=None):
        r = super().run_task_stream(task_file, on_piece, stop_flag,
                                    workdir=workdir, model=model, session_id=session_id)
        return self._wrap(task_file, session_id, r)


def build_workspace(tag="sess", budget=250000):
    tmp = Path(tempfile.mkdtemp(prefix="stage4_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "会话测试书"\n  chapters: 2\n', encoding="utf-8")
    sys_yaml = (ROOT / "config" / "system.yaml").read_text(encoding="utf-8")
    sys_yaml = re.sub(r"^\s*target_words:.*$", "  target_words: [2000, 5500]",
                      sys_yaml, flags=re.M)
    sys_yaml = re.sub(r"^\s*enhanced_mode:.*$", "  enhanced_mode: false",
                      sys_yaml, flags=re.M)
    if re.search(r"^\s*session_token_budget:", sys_yaml, re.M):
        sys_yaml = re.sub(r"^(\s*)session_token_budget:.*$",
                          r"\1session_token_budget: %d" % budget, sys_yaml, flags=re.M)
    else:
        sys_yaml = sys_yaml.replace("dsh:", "dsh:\n  session_token_budget: %d" % budget, 1)
    (tmp / "config" / "system.yaml").write_text(sys_yaml, encoding="utf-8")
    for d in ("materials/raw", "data/setting", "data/outline/chapters",
              "data/state", "data/summaries"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 会话测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in (1, 2):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件%d\n- 涉及角色：苏芷（主角）\n"
            % (n, n), encoding="utf-8")
    return tmp


def run_once(tmp, client, chapters=None):
    import yaml
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s4.run_stage(cfg, proj, progress, None, None, client=client,
                           task_dir=str(tmp / "data" / "state" / "tasks"),
                           chapters=chapters)
    return ok, msg, progress


def main():
    old = os.getcwd()

    # ---------------- A. 跨 run 接得上 ----------------
    print("\n【A】跑完第 1 章退出进程，重开再跑第 2 章 —— 会话要接上")
    tmp = build_workspace("a", budget=250000)
    try:
        os.chdir(tmp)
        c1 = SessionFake(sid="sess-alpha")
        ok1, _, p1 = run_once(tmp, c1, chapters=[1])
        check("第 1 章成功", bool(ok1), ok1)
        check("第 1 章新开会话（第一次调用没有 session_id）",
              c1.calls and c1.calls[0][1] is None, c1.calls[:2])
        sess = p1.data.get("stages", {}).get("4", {}).get("session") or {}
        check("会话 id 落进 progress", sess.get("id") == "sess-alpha", sess)
        check("记了写到第几章", sess.get("last_chapter") == 1, sess)
        check("记了累计输入 token", int(sess.get("tokens_in") or 0) == 40000, sess)

        # 模拟“退出进程、隔天再跑”：新 ProgressManager 从盘上读
        c2 = SessionFake(sid="sess-alpha")
        ok2, _, p2 = run_once(tmp, c2, chapters=[2])
        check("第 2 章成功", bool(ok2), ok2)
        check("第 2 章第一次调用带上了上一轮的 session_id",
              c2.calls and c2.calls[0][1] == "sess-alpha", c2.calls[:2])
        sess2 = p2.data.get("stages", {}).get("4", {}).get("session") or {}
        check("累计输入在跨 run 后继续累加（不是归零重来）",
              int(sess2.get("tokens_in") or 0) == 80000, sess2)
        check("没有写过接力包（水位没到）",
              not (tmp / "data" / "state" / "relay" / "ch01.md").exists())
    finally:
        os.chdir(old)
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------------- B. 水位 60% 换会话 ----------------
    print("\n【B】累计输入过 60% 水位 → 换会话 + 写接力包")
    tmp = build_workspace("b", budget=66000)     # 60% = 39600：第 1 章 4 万过线，第 2 章 2 万不过
    try:
        os.chdir(tmp)
        c = SessionFake(sid="sess-beta", tokens=[40000, 20000])
        ok, _, p = run_once(tmp, c, chapters=[1, 2])
        check("两章都成功", bool(ok), ok)
        relay = tmp / "data" / "state" / "relay" / "ch01.md"
        check("换会话时写了接力包 ch01.md", relay.exists(), str(relay))
        if relay.exists():
            body = relay.read_text(encoding="utf-8")
            check("接力包有“现在到哪”", "现在到哪" in body and "下一章" in body, body[:120])
            check("接力包给了最近三章原文路径", "最近三章" in body and "01.md" in body)
            check("接力包指向决策文件", "decisions.md" in body, body[:200])
            check("接力包列了未回收伏笔", "未回收伏笔" in body)
            check("接力包要求先复述再写正文", "复述" in body and "判失败" in body)
        sess = p.data.get("stages", {}).get("4", {}).get("session") or {}
        check("换会话后新开的那一条也已落盘（下一轮接着用）",
              sess.get("id") == "sess-beta", sess)
        check("累计输入从换会话之后重新计数（不是接着上一卷累加）",
              int(sess.get("tokens_in") or 0) == 20000, sess)
        check("记了换过几次会话", int(sess.get("rotations") or 0) == 1, sess)
        check("第 2 章第一次调用没有 session_id（换掉了）",
              len(c.calls) >= 2 and c.calls[1][1] is None, c.calls[:3])
        task2 = (tmp / "data" / "state" / "tasks" / "stage4_ch02.md")
        check("第 2 章任务书里带了接力包",
              task2.exists() and "接力包" in task2.read_text(encoding="utf-8"))
    finally:
        os.chdir(old)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
