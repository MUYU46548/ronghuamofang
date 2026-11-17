# -*- coding: utf-8 -*-
"""停止不亏稿（D 路缺的那一半，2026-11-17）。

## 为什么要有它

`问题总账` 第四章 D 路登记的是"回退那一半已验、**停止那一半没验**：
半截存残稿、已完成章不受影响，要真跑一次写作才看得见"。
函数级行为已有 `tests/unit/test_partial_stash.py`（S1–S5 验 `_stash_partial` 本身），
但"用户按下停止 → 残稿真的躺在盘上 → 已完成章一个字节没动"这条**端到端**
从来没跑过。没人跑它的代价很具体：停止是作者最不敢按的按钮
（原话："停一次亏一整章"），而"敢不敢按"只能靠这条链路兜底。

## 怎么做到不花钱

假客户端 + `NF_FAKE_STREAM_DELAY_MS`：真起 nf_api（`--allow-fake`）、真起 stage4 流式 job、
真在写到一半时 `POST /stop`。零模型调用、零 token。

## 边界（如实写，别把它当"半截正文已验"）

假引擎下 stage4 的正文是 `run_task` **直写文件**的，不经过 token 流；
流里只有"（fake 流式）已写出: …"这类碎念。所以本用例能验的是**接线**：
停止信号真的走流式分支、残稿机制真的被触发、已完成章没被连带毁掉。
**半截正文的完整保真只能真跑验**（真模型的 token 流才会进 `_stash_partial` 的 buf）。
把它当"已经验过停止不亏稿"是假绿 —— 这条边界是 2026-11-17 实测出来的。

## 判据（每条都能变红）

- T1 stage4 流式 job 起得来（202 + job_id）
- T2 `/stop` 走的是**流式**分支（回执是"已发送停止信号"，不是"已设置停止标志…阶段完成后停止"）
- T3 残稿落到 `data/state/truncated/chNN_停在_*.txt`，且**非空**
- T4 **已完成的章字节不变**（sha256 前后一致）—— 停止不连带毁掉前面的章
- T5 临时根内的 `history/` 有快照、**真实仓库的 history/ 没被写**（CWD 隔离）

用法：python tests/e2e/e2e_stop_keeps_draft.py
前置：无（自带临时项目根，端口 18791，不碰用户在用的 8765）
"""
import hashlib
import io
import json
import os
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
PORT = int(os.environ.get("NF_E2E_STOP_PORT") or 18791)
API = "http://127.0.0.1:%d" % PORT
STOP_AFTER = float(os.environ.get("NF_E2E_STOP_AFTER") or 0.8)
FAKE_DELAY_MS = os.environ.get("NF_E2E_STOP_DELAY_MS") or "1000"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def req(path, body=None, method="GET", timeout=25):
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


def build_root():
    """最小合法夹具：2 章，第 1 章已完成、第 2 章待写。

    只复制 config 与 prompts（CWD 相对读取的只有这两处）；scripts 直接用仓库的
    —— 本用例不改代码，不需要代码副本，少复制 500 个文件也快得多。
    """
    tmp = Path(tempfile.mkdtemp(prefix="e2e_stop_"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    for d in ("data/chapters/raw", "data/outline/chapters", "data/setting", "data/state"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "logs").mkdir(exist_ok=True)
    (tmp / "config/project.yaml").write_text(
        'book:\n  name: "停止自测"\n  genre: "测试"\n  target_words: 6000\n  chapters: 2\n'
        '  language: "zh-CN"\n  author: "自测"\n  style: ""\n', encoding="utf-8")
    (tmp / "data/setting/setting.json").write_text(json.dumps({
        "characters": [{"id": "c1", "name": "沈澜", "role": "主角",
                        "traits": ["克制", "谨慎", "按规程办事"], "relations": [],
                        "source": "自测", "locked": True}],
        "world": {"rules": ["销毁即投井"], "places": ["封库"]},
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "data/outline/global.md").write_text("# 整体大纲\n\n起承转合四段。\n",
                                                encoding="utf-8")
    for n in (1, 2):
        (tmp / "data/outline/chapters" / ("%02d.md" % n)).write_text(
            "## 第%d章 规程\n\n- 核心事件：沈澜按规程封库。\n- 涉及角色：沈澜。\n"
            "- 小目标：盖完章。\n- 悬念钩子：少一份。\n- 功能：铺垫。\n- 衔接：承上。\n" % n,
            encoding="utf-8")
    (tmp / "data/chapters/raw/01.md").write_text(
        "## 第1章 规程\n\n" + "她把卷宗翻过来，在封皮上按下一枚章。" * 40 + "\n\n"
        "<!-- quality: 8/10 -->\n<!-- summary: 封库开始。 -->\n", encoding="utf-8")
    (tmp / "data/state/progress.json").write_text(json.dumps({
        "project": "停止自测",
        "stages": {"1": {"status": "done", "approved": True},
                   "2": {"status": "done", "approved": True},
                   "3": {"status": "done", "approved": True},
                   "4": {"status": "pending"}},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def main():
    print("=" * 66)
    print("停止不亏稿 · 端到端（假客户端，端口 %d）" % PORT)
    print("=" * 66)
    tmp = build_root()
    before_01 = sha(tmp / "data/chapters/raw/01.md")
    repo_history_before = sorted(p.name for p in (ROOT / "history").glob("2026*"))
    env = dict(os.environ)
    env["NF_FAKE_STREAM_DELAY_MS"] = FAKE_DELAY_MS
    log = open(tmp / "api.log", "wb")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "nf_api.py"), "--port", str(PORT),
         "--allow-fake", "--root", str(tmp)],
        stdout=log, stderr=subprocess.STDOUT, env=env,
        # ⚠️ CWD 必须是临时根：snapshot 的 history/ 与 stage4 的 data/** 都是 CWD 相对。
        # 不设的话探路实测会把快照写进**真实仓库**的 history/（2026-11-17 踩到并清理）。
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
        check("S0 假后端起来", alive, "端口 %d" % PORT)
        if not alive:
            return 1

        code, d = req("/stage/4/run", {"stream": True, "chapters": "2"}, "POST")
        jid = d.get("job_id")
        check("T1 stage4 流式 job 起来（202 + job_id）", code == 202 and bool(jid), (code, d))

        time.sleep(STOP_AFTER)
        code, d = req("/stop", {"job_id": jid}, "POST")
        msg = str(d.get("message") or "")
        check("T2 走的是流式停止分支", code == 200 and "已发送停止信号" in msg, (code, msg))

        td = tmp / "data/state/truncated"
        deadline = time.time() + 30
        files = []
        while time.time() < deadline:
            files = sorted(td.glob("ch02_停在_*.txt")) if td.exists() else []
            if files:
                break
            time.sleep(0.3)
        check("T3 残稿落盘（ch02_停在_*.txt）", bool(files),
              [p.name for p in td.glob("*")] if td.exists() else "目录不存在")
        if files:
            txt = files[0].read_text(encoding="utf-8", errors="replace")
            # 阈值只定"非空"：fake 的 buf 里是碎念（正文由 write_task 直写），
            # 在这里断言"字数够多"等于拿碎念长度当正文证据 —— 那是假绿。
            check("T3 残稿非空", len(txt.strip()) > 0, len(txt.strip()))
        check("T4 已完成的第 1 章字节不变", sha(tmp / "data/chapters/raw/01.md") == before_01)
        repo_history_after = sorted(p.name for p in (ROOT / "history").glob("2026*"))
        check("T5 真实仓库 history/ 没被写（CWD 隔离）",
              repo_history_after == repo_history_before,
              [x for x in repo_history_after if x not in repo_history_before][:3])
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
