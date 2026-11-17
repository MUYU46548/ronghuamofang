# -*- coding: utf-8 -*-
"""整章重写的**产物落盘**端到端（B 路"未覆盖：整章重写"那一格，2026-11-17）。

## 为什么要有它

`e2e_rewrite_panel` 验的是"面板能开、文件尾能刷、中断能停"——它前面挡着一个测试代理，
`/chapters/rewrite` 被脚本化的假 SSE 接管，**根本不打生产代码**。
`test_chapter_rewrite` / `test_rewrite_restore` 验的是 `_prepare` / `_restore` 这些函数本身
（`orch_run` 被 monkeypatch 掉了）。
于是"点了重写，盘上到底变成什么"从来没被端到端跑过：真 orchestrator、真 stage4、
真 HTTP、真落盘。而这一格恰好是最贵的——重写错了整章正文就没了。

## 怎么做到零 token

假客户端（`--allow-fake`）：stage4 的 `run_task` 按任务书里的字数区间直写 `raw/NN.md`。
模型调用是假的，但**走的是生产同一条链路**（orchestrator → stage4 → 落盘 → 完成判定）。

## 判据（每条都能变红）

- R1 重写 job 起得来（202 + job_id），且**旧稿在 job 一开始就被移到
     `rewrite_backup/02_*.md`**、`raw/02.md` 消失（不是跑完才动）
- R2 跑完 `raw/02.md` 是**新稿**：fake 写的正文在、旧稿标记不在、汉字数达标
- R3 只动目标章：`raw/01.md`、`raw/03.md` 字节不变（sha256 前后一致）
- R4 备份里的旧稿**完整**（sha 与重写前一致）—— 这是唯一后悔药
- R5 备份**能直接用**：把备份放回 raw/ 后仍判合格（`is_chapter_complete`）
- R6 **失败自动回退**：注入 stage4 失败 → job 结果说"旧稿已自动恢复"，
      `raw/02.md` 回到重写前那一版（不是被删、不是半成品）
- R7 CWD 隔离：真实仓库的 `data/chapters/raw` 一个文件都没多/少

用法：python tests/e2e/e2e_rewrite_lands.py
前置：无（自带临时项目根，端口 18793，不碰 8765）
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PORT = int(os.environ.get("NF_E2E_REWRITE_PORT") or 18793)
API = "http://127.0.0.1:%d" % PORT
CH = 2
TW_MIN, TW_MAX = 600, 1200
OLD_MARK = "【旧稿标记】这一段只存在于旧稿里。"
JOB_TIMEOUT = float(os.environ.get("NF_E2E_REWRITE_TIMEOUT") or 180)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(path, body=None, method="GET", timeout=30):
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(API + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, json.loads(x.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:                                   # noqa: BLE001
            return e.code, {}


def sha(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def old_body(n):
    """旧稿：结构健康的正文 + 一条只属于旧稿的标记段。"""
    from utils.fake_client import _words_para
    return ("## 第%d章 旧稿标题\n\n%s\n\n%s\n\n<!-- quality: 8/10 -->\n"
            "<!-- summary: 旧稿摘要：这一版要被重写掉。 -->\n"
            % (n, OLD_MARK, _words_para(TW_MIN + 50)))


def build_root():
    tmp = Path(tempfile.mkdtemp(prefix="e2e_rewrite_"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    for d in ("data/chapters/raw", "data/outline/chapters", "data/setting",
              "data/state"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "logs").mkdir(exist_ok=True)
    (tmp / "config/project.yaml").write_text(
        'book:\n  name: "重写落盘自测"\n  genre: "测试"\n  chapters: 3\n'
        '  language: "zh-CN"\n  author: "自测"\n  style: ""\n', encoding="utf-8")
    # 单章区间压到 [600,1200]：夹具与 fake 产物都按这个下限生成，跑得快
    sy = (tmp / "config/system.yaml").read_text(encoding="utf-8")
    sy = re.sub(r"(?m)^(\s*target_words:\s*)\[[^\]]*\]",
                lambda m: m.group(1) + "[%d, %d]" % (TW_MIN, TW_MAX), sy, count=1)
    sy = re.sub(r"(?m)^(\s*agent_mode:\s*)true", r"\1false", sy)
    (tmp / "config/system.yaml").write_text(sy, encoding="utf-8")
    (tmp / "data/setting/setting.json").write_text(json.dumps({
        "characters": [{"id": "c1", "name": "沈澜", "role": "主角",
                        "traits": ["克制"], "relations": [], "source": "自测",
                        "locked": True}],
        "world": {"rules": ["销毁即投井"], "places": ["封库"]},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "data/outline/global.md").write_text("# 整体大纲\n\n起承转合四段。\n",
                                                encoding="utf-8")
    for n in (1, 2, 3):
        (tmp / "data/outline/chapters" / ("%02d.md" % n)).write_text(
            "## 第%d章 规程\n\n- 核心事件：沈澜按规程封库。\n- 涉及角色：沈澜。\n"
            "- 小目标：盖完章。\n- 悬念钩子：少一份。\n- 功能：铺垫。\n- 衔接：承上。\n" % n,
            encoding="utf-8")
        (tmp / "data/chapters/raw" / ("%02d.md" % n)).write_text(old_body(n),
                                                                encoding="utf-8")
    (tmp / "data/state/progress.json").write_text(json.dumps({
        "project": "重写落盘自测",
        "stages": {"1": {"status": "done", "approved": True},
                   "2": {"status": "done", "approved": True},
                   "3": {"status": "done", "approved": True},
                   "4": {"status": "done", "approved": True,
                         "completed_chapters": [1, 2, 3]}},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp


def wait_job(jid, timeout=JOB_TIMEOUT):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        code, d = req("/jobs/" + jid)
        last = d if isinstance(d, dict) else {}
        if last.get("state") in ("ok", "failed"):
            return last
        time.sleep(0.4)
    return last


def wait_until(fn, timeout=15, step=0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(step)
    return fn()


def main():
    print("=" * 66)
    print("整章重写 · 产物落盘端到端（假客户端，端口 %d）" % PORT)
    print("=" * 66)
    tmp = build_root()
    raw = tmp / "data/chapters/raw"
    bak_dir = tmp / "data/chapters/rewrite_backup"
    fail_flag = tmp / "fake_fail.flag"
    repo_raw_before = sorted(p.name for p in (ROOT / "data/chapters/raw").glob("*.md"))
    old_sha = sha(raw / ("%02d.md" % CH))
    sha_01, sha_03 = sha(raw / "01.md"), sha(raw / "03.md")

    env = dict(os.environ)
    env["NF_FAKE_FAIL_FILE"] = str(fail_flag)
    log = open(tmp / "api.log", "wb")
    proc = subprocess.Popen(
        # -u：日志走管道是块缓冲，不关缓冲的话 R1 的顺序判据要等进程退出才看得到
        # 阶段横幅（实测 15s 也等不到），只能判成假红。
        [sys.executable, "-u", str(ROOT / "scripts" / "nf_api.py"), "--port", str(PORT),
         "--allow-fake", "--root", str(tmp)],
        stdout=log, stderr=subprocess.STDOUT, env=env,
        # ⚠️ CWD 必须是临时根：stage4 的 data/** 与 snapshot 的 history/ 都是 CWD 相对
        cwd=str(tmp))
    try:
        alive = False
        for _ in range(80):
            try:
                urllib.request.urlopen(API + "/health", timeout=1)
                alive = True
                break
            except Exception:                               # noqa: BLE001
                time.sleep(0.25)
        check("R0 假后端起来", alive, "端口 %d" % PORT)
        if not alive:
            return 1

        # ---- 第一轮：成功重写 ----
        print("\n=== R1/R2：点重写 → 旧稿挪走 → 新稿落盘 ===")
        code, d = req("/chapters/rewrite", {"n": CH}, "POST")
        jid = d.get("job_id")
        check("R1 重写 job 起得来（202 + job_id）", code == 202 and bool(jid), (code, d))
        if not jid:
            return 1
        job = wait_job(jid)
        check("R2 job 跑完且成功", job.get("state") == "ok",
              (job.get("state"), str(job.get("result"))[:160]))
        # “旧稿先挪走、再跑 stage4”的**顺序**：假引擎一秒就跑完，事后看盘上文件
        # 分不出先后（新稿会立刻出现）。顺序证据只能取自日志：准备行必须排在阶段横幅之前。
        # 日志走管道是块缓冲，两行都可能还没落盘 → 先等两行都出现再比位置。
        def _log_ready():
            t = (tmp / "api.log").read_text(encoding="utf-8", errors="replace")
            return "旧稿已移到" in t and "==== 开始阶段 4 ====" in t
        wait_until(_log_ready, timeout=15)
        logtxt = (tmp / "api.log").read_text(encoding="utf-8", errors="replace")
        i_move = logtxt.rfind("旧稿已移到")
        i_stage = logtxt.rfind("==== 开始阶段 4 ====")
        check("R1 旧稿先被移走，然后才起 stage4（日志顺序）",
              i_move >= 0 and i_stage >= 0 and i_move < i_stage, (i_move, i_stage))
        baks = sorted(bak_dir.glob("%02d_*.md" % CH))
        check("R1 备份只有一个（不多不少）", len(baks) == 1, [p.name for p in baks])
        new_path = raw / ("%02d.md" % CH)
        check("R2 新稿落到 raw/02.md", new_path.exists())
        if new_path.exists():
            txt = new_path.read_text(encoding="utf-8")
            from utils.verify_chapter import count_cn_words, is_chapter_complete
            words = count_cn_words(txt)
            # 标题里那个空格会被 stage4 的标点/空格归一化吃掉（`## 第2章 测试章节`
            # → `## 第2章测试章节`，既有幂等归一，不是缺陷）→ 比较时去空白。
            flat = re.sub(r"\s+", "", txt)
            check("R2 新稿是模型新写的（fake 标题在）", "##第2章测试章节" in flat,
                  txt.splitlines()[0] if txt else "")
            check("R2 旧稿标记没被残留带进新稿", OLD_MARK not in txt)
            check("R2 新稿汉字数达标（≥ %d）" % TW_MIN, words >= TW_MIN, words)
            check("R2 新稿判合格（is_chapter_complete）",
                  is_chapter_complete(new_path, TW_MIN, TW_MAX))

        print("\n=== R3/R4/R5：只动目标章 / 旧稿完整 / 备份能直接用 ===")
        check("R3 第 1 章字节不变", sha(raw / "01.md") == sha_01)
        check("R3 第 3 章字节不变", sha(raw / "03.md") == sha_03)
        if baks:
            check("R4 备份 = 重写前那一版（sha 一致）", sha(baks[0]) == old_sha,
                  (sha(baks[0])[:12], old_sha[:12]))
            from utils.verify_chapter import is_chapter_complete
            check("R5 备份放回 raw 也判合格（捞回即用）",
                  is_chapter_complete(baks[0], TW_MIN, TW_MAX))
        else:
            check("R4 备份存在", False)
            check("R5 备份可用", False)

        # ---- 第二轮：失败 → 自动回退 ----
        print("\n=== R6：注入 stage4 失败 → 旧稿自动放回 ===")
        # 先给这一版加一条只属于它的标记：假引擎每次写出的正文是一样的，
        # 不标记的话“恢复了”与“又重写成功”在盘上长得一模一样 —— 判据就假绿。
        HAND = "【第二轮之前的手改标记】"
        cur = (raw / ("%02d.md" % CH)).read_text(encoding="utf-8")
        (raw / ("%02d.md" % CH)).write_text(cur + "\n" + HAND + "\n", encoding="utf-8")
        cur_sha = sha(raw / ("%02d.md" % CH))
        fail_flag.write_text("1", encoding="utf-8")
        code, d = req("/chapters/rewrite", {"n": CH}, "POST")
        jid2 = d.get("job_id")
        check("R6 第二次重写 job 起得来", code == 202 and bool(jid2), (code, d))
        if jid2:
            job2 = wait_job(jid2)
            res = str(job2.get("result") or "")
            check("R6 job 如实报失败（不伪装成功）", job2.get("state") == "failed",
                  (job2.get("state"), res[:160]))
            check("R6 回执说清“旧稿已自动恢复”", "旧稿已自动恢复" in res, res[:220])
            restored = raw / ("%02d.md" % CH)
            check("R6 raw/02.md 回来了且内容 = 重写前那一版",
                  sha(restored) == cur_sha,
                  (sha(restored)[:12], cur_sha[:12]))
            check("R6 恢复的是带手改标记的那一版（不是又一次重写）",
                  restored.exists() and HAND in restored.read_text(encoding="utf-8"))
        try:
            fail_flag.unlink()
        except OSError:
            pass

        print("\n=== R7：CWD 隔离（真实仓库一个文件都没动）===")
        repo_raw_after = sorted(p.name for p in (ROOT / "data/chapters/raw").glob("*.md"))
        check("R7 真实仓库 data/chapters/raw 没变", repo_raw_after == repo_raw_before,
              [x for x in repo_raw_after if x not in repo_raw_before][:3])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    print("临时根（证据）: %s" % tmp)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
