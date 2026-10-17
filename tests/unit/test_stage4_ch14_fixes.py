# -*- coding: utf-8 -*-
"""第14次试跑修复自检（2026-10-17，纯 fake / 离线，零 LLM、零网络）。

## 修的四个根因与本用例的对应

1. **hermes 子会话任务路径相对 → cwd 不匹配时静默失败**（ch01 十四连败主因之一）：
   `_build_cmd` 现在把任务路径 resolve 成绝对路径。本用例断言 `-q` 提示里出现
   绝对路径 —— 相对路径（`data/state/tasks/xx.md`）不再可能。
2. **`data/summaries/` 缺失**（ch01 十四连败主因之二）：首章开跑前 rolling.md
   尚不存在，而任务把它列为必读输入，子会话读不到直接放弃。stage4 现在开工前
   预创建骨架。本用例断言：即使一章都没写成，跑完后 rolling.md 也存在。
3. **字数不足整章重写复现短稿**（ch03 九连败主因）：现在改为"读原稿定向扩写"，
   每章每轮至多一次。本用例三段断言：差一点 → 扩写后过；差太多（< 下限 60%）
   → 不扩写直接判失败；扩写后仍短 → 判失败且**只扩写一次**（不空转）。
4. **跨会话双跑 + runs 脏行**：RunLock 把"同时只跑一条"变成机制，
   `sweep_stale_running` 把强杀留下的 running 脏行收敛为 interrupted。

另附 App.vue 三处前端修复的**源码级**断言（复制任务按钮不再被运行禁用、
补救弹窗有复制按钮、收件箱空态指明阶段3产物去向）。

用法：python tests/unit/test_stage4_ch14_fixes.py
"""
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.progress_manager import ProgressManager          # noqa: E402
from utils.run_lock import RunLock, _pid_alive              # noqa: E402
from utils.db import RunDB                                  # noqa: E402
from utils.verify_chapter import check_chapter              # noqa: E402
import stage4_writing as s4                                 # noqa: E402
from utils.llm_client import HermesClient                   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


SETTING = {
    "characters": [{"id": "luxi", "name": "苏芷", "role": "主角",
                    "traits": ["冷静"], "source": "fake"}],
    "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
    "plot_fragments": [], "timeline": [], "_meta": {"version": 1},
}

# 随机中文句段生成器：字符级抽样，12-gram 复读率天然趋零，够绕过退化检测。
_POOL = ("他她它的是了在有人不来去上下天地山水风雨云光暗门窗桌椅灯火茶酒"
         "剑刀机关齿轮锁链绳索封印阵法符文石砖瓦木铜铁银金布衣裘袍靴帽")
_PH = ("<!-- summary: 第%d章测试摘要 -->\n<!-- quality: 8/10 -->")


def _gen_body(target_chars, seed):
    rng = random.Random(seed)
    paras = []
    total = 0
    i = 0
    while total < target_chars:
        i += 1
        n = rng.randint(180, 260)
        # 段首嵌序号：保证段与段之间必然不同（防模板拼装式复读）
        s = "第%d段" % i + "".join(rng.choice(_POOL) for _ in range(n))
        paras.append(s)
        total += len(s)
    return "\n\n".join(paras)


def _chapter(n, target_chars, seed_base=7):
    return ("## 第%d章 测试章\n\n" % n) + _gen_body(target_chars, seed_base + n) \
        + "\n\n" + (_PH % n) + "\n"


def build_workspace(total=1, tag="ch14"):
    tmp = Path(tempfile.mkdtemp(prefix="stage4_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "修复测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 修复测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n" % n,
            encoding="utf-8")
    return tmp


class AlwaysFailClient:
    def write_task(self, task_dir, name, content):
        Path(task_dir).mkdir(parents=True, exist_ok=True)
        p = Path(task_dir) / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def run_task(self, task_path, **kwargs):
        return {"exit_code": 1, "tokens": 10, "tokens_out": 5, "text": "",
                "model": "fake"}


class ShortClient:
    """首写短稿；`always_short=True` 时扩写任务也只给短稿（模拟扩写失败）。"""

    SHORT_CHARS = 1300     # < 2000 下限，但 >= 1200（60%）→ 够得着扩写门槛
    LONG_CHARS = 2400      # 达标稿

    def __init__(self, always_short=False):
        self.calls = []
        self.always_short = always_short

    def write_task(self, task_dir, name, content):
        Path(task_dir).mkdir(parents=True, exist_ok=True)
        p = Path(task_dir) / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def run_task(self, task_path, **kwargs):
        name = Path(task_path).name
        self.calls.append(name)
        m = re.match(r"stage4_ch(\d+)(_expand)?\.md$", name)
        n = int(m.group(1))
        is_expand = bool(m.group(2))
        chars = self.SHORT_CHARS if (self.always_short or not is_expand) \
            else self.LONG_CHARS
        Path("data/chapters/raw").mkdir(parents=True, exist_ok=True)
        Path("data/chapters/raw/%02d.md" % n).write_text(
            _chapter(n, chars), encoding="utf-8")
        return {"exit_code": 0, "tokens": 10, "tokens_out": 5, "text": "",
                "model": "fake"}


def run_stage4(tmp, client):
    import yaml
    os.chdir(tmp)
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s4.run_stage(cfg, proj, progress, None, None,
                           client=client, task_dir=str(tmp / "data" / "state" / "tasks"))
    return ok, msg, progress


# ---------------------------------------------------------------- 1. 任务路径绝对化
def case_build_cmd_absolute():
    print("\n【1】hermes 任务路径绝对化（cwd 不再是子会话的生死线）")
    c = HermesClient()
    cmd = c._build_cmd(Path("data") / "state" / "tasks" / "stage4_ch01.md")
    q = cmd[3]
    check("-q 提示含绝对路径", str(Path("data/state/tasks/stage4_ch01.md").resolve()) in q, q[:160])
    check("-q 提示不含相对路径原形", "严格按 data\\state" not in q and "严格按 data/state" not in q, q[:160])
    m_abs = re.search(r"严格按 (.+?) 中的指示", q)
    check("解析出的路径是绝对路径（盘符/根开头）",
          bool(m_abs) and os.path.isabs(m_abs.group(1)), m_abs.group(1) if m_abs else q[:120])


# ---------------------------------------------------------------- 2. rolling 骨架
def case_rolling_skeleton():
    print("\n【2】滚动摘要骨架预创建（首章不再因缺输入而弃写）")
    tmp = build_workspace(total=1, tag="roll")
    origin = os.getcwd()
    try:
        ok, msg, _ = run_stage4(tmp, AlwaysFailClient())
        rolling = tmp / "data" / "summaries" / "rolling.md"
        check("即使一章都没写成，rolling.md 也已存在", rolling.exists())
        if rolling.exists():
            from utils.summary_chain import load_rolling
            data = load_rolling(rolling)
            # write_rolling 对空摘要写占位句「（待首次压缩生成）」，解析应零章节
            check("骨架可被 load_rolling 解析（零章节）",
                  data["chapters"] == {} and "待首次压缩" in data["global_summary"], data)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 3. 定向扩写
def case_expand_success():
    print("\n【3a】字数差一点 → 定向扩写后过")
    tmp = build_workspace(total=1, tag="exp")
    origin = os.getcwd()
    try:
        c = ShortClient()
        ok, msg, progress = run_stage4(tmp, c)
        expand_calls = [x for x in c.calls if "_expand" in x]
        check("阶段判成功", ok is True, msg)
        check("发出过扩写任务（且只有一次）", len(expand_calls) == 1, c.calls)
        check("扩写后章节达标", check_chapter(tmp / "data" / "chapters" / "raw" / "01.md",
                                              2000, 5500).ok)
        check("阶段状态 done", progress.stage_status(4) == "done", progress.stage_status(4))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_expand_refused_when_too_short():
    print("\n【3b】字数差太多（< 下限 60%）→ 不扩写，直接判失败")
    tmp = build_workspace(total=1, tag="short")
    origin = os.getcwd()
    try:
        c = ShortClient()
        c.SHORT_CHARS = 900     # < 1200 = 0.6*2000，扩写救不回来
        ok, msg, progress = run_stage4(tmp, c)
        expand_calls = [x for x in c.calls if "_expand" in x]
        check("阶段判失败", ok is False, msg)
        check("未发出扩写任务（差距太大不值得扩）", expand_calls == [], c.calls)
        check("章节被记为失败章", len(progress.failed_chapters(4)) == 1,
              progress.failed_chapters(4))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_expand_once_no_loop():
    print("\n【3c】扩写后仍短 → 判失败，且只扩写一次（不空转）")
    tmp = build_workspace(total=1, tag="loop")
    origin = os.getcwd()
    try:
        c = ShortClient(always_short=True)
        ok, msg, progress = run_stage4(tmp, c)
        expand_calls = [x for x in c.calls if "_expand" in x]
        check("阶段判失败", ok is False, msg)
        check("扩写任务恰好一次（不会反复扩写空转）", len(expand_calls) == 1, c.calls)
        check("章节被记为失败章", len(progress.failed_chapters(4)) == 1,
              progress.failed_chapters(4))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 4. 运行锁
def _spawn_alive_pid():
    """起一个真实存活的子进程，返回 (proc, pid)。"""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    return proc, proc.pid


def case_run_lock():
    print("\n【4】跨进程运行锁")
    tmp = Path(tempfile.mkdtemp(prefix="runlock_"))
    lock_path = tmp / "data" / "state" / "run.lock"
    try:
        l1 = RunLock(lock_path)
        ok, msg = l1.acquire()
        check("首次拿锁成功", ok is True, msg)
        check("锁文件落盘且 pid 为本进程",
              json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid())

        l2 = RunLock(lock_path)
        ok2, msg2 = l2.acquire()
        check("同进程重入（nf_api 串行 job 场景）不自我拒绝", ok2 is True, msg2)

        # 真并发：锁被另一个**活进程**持有 → 拒绝
        proc, pid = _spawn_alive_pid()
        l1.release()
        lock_path.write_text(json.dumps({"pid": pid, "started_at": "x", "cmd": "other"}),
                             encoding="utf-8")
        l3 = RunLock(lock_path)
        ok3, msg3 = l3.acquire()
        check("他人活进程持锁 → 拒绝启动并给出指引",
              ok3 is False and "流水线" in msg3, msg3)
        check("拒绝时未删别人的锁", lock_path.exists())

        # 持有进程死掉 → 陈旧锁接管
        proc.kill()
        proc.wait(timeout=10)
        l4 = RunLock(lock_path)
        ok4, msg4 = l4.acquire()
        check("持有进程已死 → 陈旧锁接管", ok4 is True, msg4)
        check("接管后锁归自己", json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid())

        # 损坏的锁文件 → 接管而不是崩溃
        l4.release()
        lock_path.write_text("not json{{{", encoding="utf-8")
        l5 = RunLock(lock_path)
        ok5, msg5 = l5.acquire()
        check("损坏锁文件 → 告警接管（不崩）", ok5 is True, msg5)

        # release 只删自己的锁
        lock_path.write_text(json.dumps({"pid": pid + 12345}), encoding="utf-8")
        l5.release()
        check("release 不误删他人锁（pid 比对）", lock_path.exists())

        check("pid 查活：本进程为活", _pid_alive(os.getpid()) is True)
        check("pid 查活：不存在的 pid 为死", _pid_alive(999999999) is False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 5. runs 脏行清扫
def case_sweep_stale_running():
    print("\n【5】runs 脏行启动清扫（强杀留下的 running 收敛为 interrupted）")
    tmp = Path(tempfile.mkdtemp(prefix="sweep_"))
    try:
        db = RunDB(tmp / "runs.db")
        r1 = db.start_run("from=1")
        r2 = db.start_run("from=4")
        check("两条 running 脏行在库", db.sweep_stale_running() == 2)
        rows = db.conn.execute("SELECT status FROM runs").fetchall()
        check("全部收敛为 interrupted",
              all(r[0] == "interrupted" for r in rows), rows)
        check("二次清扫无事可做（幂等）", db.sweep_stale_running() == 0)
        db.finish_run(r1, "done")
        check("清扫不碰已收尾的行",
              db.conn.execute("SELECT status FROM runs WHERE id=?", (r1,)).fetchone()[0] == "done")
        db.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 6. 前端修复（源码级）
def case_app_vue_fixes():
    print("\n【6】App.vue 三处前端修复（源码级断言）")
    src = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    copy_line = next((ln for ln in src.splitlines()
                      if "copyStagePrompt(s.stage)" in ln and "<button" in ln), "")
    check("复制任务按钮不再被 isRunning 禁用",
          copy_line != "" and ":disabled=\"isRunning\"" not in copy_line, copy_line[:150])
    check("补救弹窗有「复制全部建议」按钮",
          "copyRemedy" in src and "复制全部建议" in src)
    check("remedyAsText 拼装函数存在", "function remedyAsText()" in src)
    check("收件箱空态指明阶段3产物去向",
          "阶段 3（逐章大纲）不设审批门" in src)


# ---------------------------------------------------------------- 7. orchestrator 接线（源码级）
def case_orchestrator_wiring():
    print("\n【7】orchestrator 接线（锁 + 清扫）")
    src = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    check("run() 内获取运行锁", "run_lock.acquire()" in src)
    check("拿锁失败即退出（不硬跑）", "无法获取运行锁" in src)
    check("拿锁后清扫脏行", "sweep_stale_running()" in src)
    check("finally 释放锁", "run_lock.release()" in src)


def main():
    t0 = time.time()
    for fn in (case_build_cmd_absolute, case_rolling_skeleton,
               case_expand_success, case_expand_refused_when_too_short,
               case_expand_once_no_loop, case_run_lock, case_sweep_stale_running,
               case_app_vue_fixes, case_orchestrator_wiring):
        fn()
    print("\n==== 结果: PASS %d / FAIL %d （%.1fs）===="
          % (len(PASS), len(FAIL), time.time() - t0))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
