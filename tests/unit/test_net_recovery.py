# -*- coding: utf-8 -*-
"""第一波防御 · 任务二验收（报错自愈与断点续跑，纯 fake / 子进程，零 LLM）。

## 施工单验收线（2026-10-22）

> 试跑中途强杀进程 1 次 + 人为断网 1 次：恢复后续跑从断点继续，
> 已完成段落零重复零丢失。

## 本用例的对应

1. **断网**：client 抛 URLError（模拟 llm_client 内部重试耗尽后 raise）→
   run_stage 不崩、按失败记账、已完成章保留；网络恢复后续跑只补失败章。
2. **强杀**：真实子进程在「第 2 章写盘之后、标完成之前」`os._exit(1)`
   （等价 SIGKILL：无 finally / 无 atexit / progress 未保存）→ 重启后：
   第 1 章（已记账）不重写、第 2 章（崩溃窗口孤儿）零 LLM 补账、
   第 3 章正常写。全程 LLM 调用只发生在第 3 章。
3. **传输异常的排障口径**：失败原因必须点明「网络/传输异常」，
   不能误导成「子会话退出码非零」（那会让人去查提示词）。

用法：python tests/unit/test_net_recovery.py
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
import urllib.error
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils.progress_manager import ProgressManager        # noqa: E402
import stage4_writing as s4                               # noqa: E402

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

_POOL = ("他她它的是了在有人不来去上下天地山水风雨云光暗门窗桌椅灯火茶酒"
         "剑刀机关齿轮锁链绳索封印阵法符文石砖瓦木铜铁银金布衣裘袍靴帽")
_PH = ("<!-- summary: 第%d章测试摘要 -->\n<!-- quality: 8/10 -->")


def _gen_body(target_chars, seed):
    rng = random.Random(seed)
    paras, total, i = [], 0, 0
    while total < target_chars:
        i += 1
        n = rng.randint(180, 260)
        s = "第%d段" % i + "".join(rng.choice(_POOL) for _ in range(n))
        paras.append(s)
        total += len(s)
    return "\n\n".join(paras)


def _chapter(n, target_chars=2400, seed_base=7):
    return ("## 第%d章 测试章\n\n" % n) + _gen_body(target_chars, seed_base + n) \
        + "\n\n" + (_PH % n) + "\n"


def _patch_cfg(sys_yaml, low=2000, high=3000):
    lines = Path(sys_yaml).read_text(encoding="utf-8").split("\n")
    out = []
    for ln in lines:
        if ln.strip().startswith("target_words:"):
            out.append("  target_words: [%d, %d]" % (low, high))
            continue
        out.append(ln)
    Path(sys_yaml).write_text("\n".join(out), encoding="utf-8")


def build_workspace(total=3, tag="net"):
    tmp = Path(tempfile.mkdtemp(prefix="netrec_%s_" % tag))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config").mkdir()
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "断点续跑测试书"\n  chapters: %d\n' % total, encoding="utf-8")
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    _patch_cfg(tmp / "config" / "system.yaml")
    (tmp / "materials" / "raw").mkdir(parents=True)
    for sub in ("setting", "outline/chapters", "state"):
        (tmp / "data" / sub).mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False), encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(
        "# 断点续跑测试书 大纲\n\n## 起\n开篇\n", encoding="utf-8")
    for n in range(1, total + 1):
        (tmp / "data" / "outline" / "chapters" / ("%02d.md" % n)).write_text(
            "# 第%d章 大纲\n\n- 核心事件：测试事件\n- 涉及角色：苏芷（主角）\n" % n,
            encoding="utf-8")
    return tmp


def run_stage4(tmp, client):
    import yaml
    os.chdir(tmp)
    cfg = yaml.safe_load((tmp / "config" / "system.yaml").read_text(encoding="utf-8"))
    proj = yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
    progress = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    ok, msg = s4.run_stage(cfg, proj, progress, None, None,
                           client=client, task_dir=str(tmp / "data" / "state" / "tasks"))
    return ok, msg, progress


class _BaseClient:
    def write_task(self, task_dir, name, content):
        Path(task_dir).mkdir(parents=True, exist_ok=True)
        p = Path(task_dir) / name
        p.write_text(content, encoding="utf-8")
        return str(p)

    def _write_chapter(self, name):
        n = int(re.search(r"stage4_ch(\d+)", name).group(1))
        raw = Path("data/chapters/raw")
        raw.mkdir(parents=True, exist_ok=True)
        (raw / ("%02d.md" % n)).write_text(_chapter(n), encoding="utf-8")
        return {"exit_code": 0, "tokens": 10, "tokens_out": 5, "text": "",
                "model": "fake"}


class OfflineThenOnlineClient(_BaseClient):
    """第 2 章起断网（raise URLError，等价 llm_client 重试耗尽后的穿透异常）；
    `online=True` 时网络恢复（重跑轮）。"""

    def __init__(self, online=False):
        self.online = online
        self.calls = []

    def run_task(self, task_path, **kw):
        name = Path(task_path).name
        self.calls.append(name)
        n = int(re.search(r"stage4_ch(\d+)", name).group(1))
        if not self.online and n >= 2:
            raise urllib.error.URLError("[Errno 11001] getaddrinfo failed")
        return self._write_chapter(name)


def case_offline_no_crash():
    print("\n【1】断网：run_stage 不崩、按失败记账、已完成章保留")
    tmp = build_workspace(total=3, tag="off")
    origin = os.getcwd()
    try:
        c = OfflineThenOnlineClient(online=False)
        ok, msg, progress = run_stage4(tmp, c)
        check("断网时不抛异常（异常已被兜住）", ok is False, msg)
        check("失败消息不误导（阶段失败而非 crashed）",
              "失败" in msg and "crashed" not in msg, msg)
        check("第 1 章（断网前完成）保留", 1 in progress.completed_chapters(4),
              progress.completed_chapters(4))
        check("第 2、3 章记为失败章",
              sorted(f["n"] for f in progress.failed_chapters(4)) == [2, 3],
              progress.failed_chapters(4))
        f2 = next(f for f in progress.failed_chapters(4) if f["n"] == 2)
        check("失败原因点明「网络/传输异常」（排障不跑偏）",
              "网络/传输异常" in f2["error"], f2["error"])
        check("第 1 章文件在盘", (tmp / "data/chapters/raw/01.md").exists())

        # ---- 网络恢复 → 续跑只补失败章 ----
        c2 = OfflineThenOnlineClient(online=True)
        ok2, msg2, progress2 = run_stage4(tmp, c2)
        check("恢复后阶段成功", ok2 is True, msg2)
        rewritten = [x for x in c2.calls if re.search(r"stage4_ch01", x)]
        check("第 1 章零重复（恢复轮没碰它）", rewritten == [], c2.calls)
        check("两轮后 3 章全部完成",
              progress2.completed_chapters(4) == [1, 2, 3],
              progress2.completed_chapters(4))
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 强杀（子进程）
_KILL_DRIVER = r'''
import io, json, os, re, sys, random, traceback
# 证据双写：CI 上子进程 rc=1 却 stdout/stderr 一个字节都没有（2026-10-24 实测），
# 管子这条路带不回根因 —— 日志文件不经过管道，谁也丢不掉。
_LOG = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_kill_out.log"),
               "w", encoding="utf-8")
_real_print = print
def print(*a, **k):
    _LOG.write(" ".join(str(x) for x in a) + "\n"); _LOG.flush()   # 先落文件再打管子
    k.pop("file", None)
    _real_print(*a, **k)
def _hook(t, v, tb):
    _LOG.write("EXC " + "".join(traceback.format_exception(t, v, tb))); _LOG.flush()
sys.excepthook = _hook
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, {scripts!r})
os.chdir({cwd!r})
import yaml
from utils.progress_manager import ProgressManager
import stage4_writing as s4

_POOL = ("他她它的是了在有人不来去上下天地山水风雨云光暗门窗桌椅灯火茶酒"
         "剑刀机关齿轮锁链绳索封印阵法符文石砖瓦木铜铁银金布衣裘袍靴帽")

def _gen_body(target_chars, seed):
    rng = random.Random(seed)
    paras, total, i = [], 0, 0
    while total < target_chars:
        i += 1
        n = rng.randint(180, 260)
        s = "第%d段" % i + "".join(rng.choice(_POOL) for _ in range(n))
        paras.append(s); total += len(s)
    return "\n\n".join(paras)

class KillAfterCh2Client:
    """第 2 章写盘后立即硬杀进程（os._exit = 无 finally/atexit）。"""
    def write_task(self, task_dir, name, content):
        os.makedirs(task_dir, exist_ok=True)
        p = os.path.join(task_dir, name)
        open(p, "w", encoding="utf-8").write(content)
        return p
    def run_task(self, task_path, **kw):
        name = os.path.basename(task_path)
        n = int(re.search(r"stage4_ch(\d+)", name).group(1))
        raw = "data/chapters/raw"
        os.makedirs(raw, exist_ok=True)
        body = ("## 第%d章 测试章\n\n" % n) + _gen_body(2400, 7 + n) + \
            "\n\n<!-- summary: 摘要 -->\n<!-- quality: 8/10 -->\n"
        open(os.path.join(raw, "%02d.md" % n), "w", encoding="utf-8").write(body)
        if n == 2:
            print("KILLPOINT: ch02 written, exiting hard", flush=True)
            os._exit(1)          # 等价强杀：progress 尚未标 2 完成
        return {{"exit_code": 0, "tokens": 10, "tokens_out": 5, "text": "",
                 "model": "fake"}}

cfg = yaml.safe_load(open("config/system.yaml", encoding="utf-8").read())
proj = yaml.safe_load(open("config/project.yaml", encoding="utf-8").read())
progress = ProgressManager("data/state/progress.json")
s4.run_stage(cfg, proj, progress, None, None, client=KillAfterCh2Client(),
             task_dir="data/state/tasks")
print("UNREACHABLE", flush=True)
'''


class CountingOnlineClient(_BaseClient):
    def __init__(self):
        self.calls = []

    def run_task(self, task_path, **kw):
        name = Path(task_path).name
        self.calls.append(name)
        return self._write_chapter(name)


def case_kill_resume():
    print("\n【2】强杀：子进程硬杀在「写盘后、标完成前」→ 重启续跑零重复零丢失")
    tmp = build_workspace(total=3, tag="kill")
    origin = os.getcwd()
    try:
        driver = tmp / "_kill_driver.py"
        driver.write_text(_KILL_DRIVER.format(scripts=str(ROOT / "scripts"),
                                              cwd=str(tmp)), encoding="utf-8")
        r = subprocess.run([sys.executable, str(driver)], capture_output=True,
                           text=True, timeout=120, cwd=str(tmp))
        out = (r.stdout or "") + (r.stderr or "")
        # 详情必须压成**单行**：run_tests 的失败摘录只取 [FAIL] 那一行，多行 detail
        # （子进程 traceback 恰好以换行结尾时）会被整段丢掉 —— CI 上曾因此只看到
        # 「KILLPOINT 不在输出里」，却看不到子进程到底炸在哪（红了一天无从下手）。
        flat = " | ".join(out.splitlines()[-6:])[-400:]
        # 管子输出可能整段丢失（CI 实测 rc=1 却一个字节都没有）→ 回读驱动写的日志文件
        logf = tmp / "_kill_out.log"
        flog = " | ".join(logf.read_text(encoding="utf-8").splitlines()[-8:])[-500:] \
            if logf.exists() else "(驱动没写出日志文件)"
        check("子进程确实走到强杀点", "KILLPOINT" in out,
              "rc=%s · pipe=[%s] · file=[%s]" % (r.returncode, flat, flog))
        check("子进程未走完（无 UNREACHABLE）", "UNREACHABLE" not in out)

        raw = tmp / "data" / "chapters" / "raw"
        check("强杀后 1、2 章文件在盘（写盘先于记账）",
              (raw / "01.md").exists() and (raw / "02.md").exists())
        pj = tmp / "data" / "state" / "progress.json"
        prog = json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else {}
        done_before = prog.get("stages", {}).get("4", {}).get("completed_chapters", [])
        check("强杀时只记账到第 1 章（第 2 章落在崩溃窗口）",
              done_before == [1], done_before)

        # ---- 重启续跑 ----
        c = CountingOnlineClient()
        ok, msg, progress = run_stage4(tmp, c)
        check("续跑后阶段成功", ok is True, msg)
        check("3 章全部完成", progress.completed_chapters(4) == [1, 2, 3],
              progress.completed_chapters(4))
        # 第 1 章已记账 → 跳过；第 2 章崩溃窗口孤儿 → 零 LLM 补账；只有第 3 章真调用
        only_ch3 = [x for x in c.calls if re.search(r"stage4_ch", x)]
        check("续跑 LLM 调用只发生在第 3 章（1、2 章零重复）",
              len(only_ch3) == 1 and "ch03" in only_ch3[0], c.calls)
        roll = tmp / "data" / "summaries" / "rolling.md"
        check("第 2 章摘要已补进滚动摘要（补账而非重写）",
              roll.exists() and "第2章" in roll.read_text(encoding="utf-8").replace("第 2 章", "第2章"),
              roll.read_text(encoding="utf-8")[:120] if roll.exists() else "missing")
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def case_backoff_config_present():
    print("\n【3】指数退避与重试上限可配（盘点确认，判据在 llm_client）")
    src = (ROOT / "scripts" / "utils" / "llm_client.py").read_text(encoding="utf-8")
    check("非流式请求有指数退避 sleep(2**attempt)", "time.sleep(2 ** attempt)" in src)
    cfg = (ROOT / "config" / "system.yaml").read_text(encoding="utf-8")
    m = re.search(r"retries:\s*(\d+)", cfg)
    check("providers.retries 已配（默认 5 次）", bool(m) and int(m.group(1)) == 5,
          m.group(1) if m else "missing")
    check("5xx/断连走重试、400/401/403/404 直接 raise（死模型不吃链）",
          "if e.code in (400, 401, 403, 404):" in src)


def main():
    t0 = time.time()
    for fn in (case_offline_no_crash, case_kill_resume, case_backoff_config_present):
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
