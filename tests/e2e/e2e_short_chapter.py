# -*- coding: utf-8 -*-
"""严重偏短的章会不会被“整章重生成”空转掉（问题总账 五·5 的未定论，2026-11-17）。

## 为什么要有它

`docs/问题总账.md` 五·5 记着一条**未定论**：定向扩写只在字数 ≥ 下限 60% 时触发，
更低的（如 2120 汉字 vs 下限 4000）会走“整章重生成”，而同一文件的注释自承
“实测同章字数不足重跑 9 次仍复现同样的短稿” —— 于是有“白烧 token 空转”的风险。

光读代码只能看到**分支存在**，看不到“这一章最后到底怎么处置、花了几轮”。
本用例真起 nf_api + 真 stage4，用假引擎把首轮写成短稿，看它走哪条路、几轮收手。

## 怎么做到零 token

`NF_FAKE_SHORT_RATIO` 让假引擎的**整章首轮**只写下限的 R 倍（段群/扩写照常写满）。

## 判据（每条都能变红）

严重偏短（R=0.3，低于 0.6 阈值）：
- S1 首轮真写出短稿（日志里有“字数 N 不在 [low, high] 区间”，N < low）
- S2 走的是**加强模式**（日志“自动转加强模式（拆段落群逐群补足）”）
- S3 **没有**整章重生成（日志里不出现“打回：整章重生成”）
- S4 拆群数 = `plan_groups` 算出来的那个数，一个不多（调用有界）
- S5 最后正文达标、章被标完成（不是停在失败）

中度偏短（R=0.75，高于阈值）：
- S6 走的是**定向扩写**（日志“打回：定向扩写”），同样不出现整章重生成
- S7 最后正文达标、章被标完成

用法：python tests/e2e/e2e_short_chapter.py
前置：无（两段各自起临时根与假后端，端口 18795/18796，可被 NF_E2E_SHORT_PORT_A/B 覆盖；不碰 8765）
"""
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

TW = [600, 1200]
# 两段各自起一个假后端（失败注入是进程级环境变量，跑完一段就换端口重起）
PORT_A = int(os.environ.get("NF_E2E_SHORT_PORT_A") or 18795)
PORT_B = int(os.environ.get("NF_E2E_SHORT_PORT_B") or 18796)
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(api, path, body=None, method="GET", timeout=30):
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(api + path, data=data, method=method,
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
    """单章书：前三阶段已放行，第 1 章待写（raw 为空）。"""
    tmp = Path(tempfile.mkdtemp(prefix="e2e_short_"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    for d in ("data/chapters/raw", "data/outline/chapters", "data/setting",
              "data/state"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "logs").mkdir(exist_ok=True)
    (tmp / "config/project.yaml").write_text(
        'book:\n  name: "短稿策略自测"\n  genre: "测试"\n  chapters: 1\n'
        '  language: "zh-CN"\n  author: "自测"\n  style: ""\n', encoding="utf-8")
    sy = (tmp / "config/system.yaml").read_text(encoding="utf-8")
    sy = re.sub(r"(?m)^(\s*target_words:\s*)\[[^\]]*\]",
                lambda m: m.group(1) + "[%d, %d]" % (TW[0], TW[1]), sy, count=1)
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
    (tmp / "data/outline/chapters/01.md").write_text(
        "## 第1章 规程\n\n- 核心事件：沈澜按规程封库。\n- 涉及角色：沈澜。\n"
        "- 小目标：盖完章。\n- 悬念钩子：少一份。\n- 功能：铺垫。\n- 衔接：承上。\n",
        encoding="utf-8")
    (tmp / "data/state/progress.json").write_text(json.dumps({
        "project": "短稿策略自测",
        "stages": {"1": {"status": "done", "approved": True},
                   "2": {"status": "done", "approved": True},
                   "3": {"status": "done", "approved": True},
                   "4": {"status": "pending"}},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp


def wait_job(api, jid, timeout=240):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        _, d = req(api, "/jobs/" + jid)
        last = d if isinstance(d, dict) else {}
        if last.get("state") in ("ok", "failed"):
            return last
        time.sleep(0.4)
    return last


def run_case(tag, ratio, port):
    """起一个假后端跑一次 stage4，返回 (日志文本, 临时根, job)。"""
    tmp = build_root()
    api = "http://127.0.0.1:%d" % port
    env = dict(os.environ)
    env["NF_FAKE_SHORT_RATIO"] = str(ratio)
    log = open(tmp / "api.log", "wb")
    proc = subprocess.Popen(
        # -u：日志走管道是块缓冲，靠日志行做判据时必须不缓冲
        [sys.executable, "-u", str(ROOT / "scripts" / "nf_api.py"),
         "--port", str(port), "--allow-fake", "--root", str(tmp)],
        stdout=log, stderr=subprocess.STDOUT, env=env, cwd=str(tmp))
    try:
        alive = False
        for _ in range(80):
            try:
                urllib.request.urlopen(api + "/health", timeout=1)
                alive = True
                break
            except Exception:                               # noqa: BLE001
                time.sleep(0.25)
        check("%s 假后端起来" % tag, alive, "端口 %d" % port)
        if not alive:
            return "", tmp, {}
        _, d = req(api, "/stage/4/run", {"chapters": "1"}, "POST")
        jid = d.get("job_id")
        check("%s stage4 job 起得来" % tag, bool(jid), d)
        job = wait_job(api, jid) if jid else {}
        logtxt = (tmp / "api.log").read_text(encoding="utf-8", errors="replace")
        return logtxt, tmp, job
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def main():
    print("=" * 66)
    print("短稿处置策略 · 端到端（假客户端，端口 %d/%d）" % (PORT_A, PORT_B))
    print("=" * 66)
    from utils.verify_chapter import is_chapter_complete
    from utils.enhanced_chapter import plan_groups
    groups = len(plan_groups(TW[0], TW[1]))
    print("  plan_groups(%d, %d) → %d 群" % (TW[0], TW[1], groups))

    # ---- 严重偏短：R=0.3（低于 0.6 阈值）----
    print("\n=== S1–S5：严重偏短（R=0.3）===")
    log_txt, tmp, job = run_case("S1", 0.3, PORT_A)
    if not log_txt:
        return 1
    m = re.search(r"字数 (\d+) 不在 \[(\d+), (\d+)\] 区间", log_txt) \
        or re.search(r"第1章字数 (\d+) < (\d+)", log_txt)
    check("S1 首轮真写出短稿（低于下限）", bool(m) and int(m.group(1)) < TW[0],
          m.group(0) if m else "日志里没有字数不过的痕迹")
    check("S2 走加强模式（拆段落群逐群补足）",
          "自动转加强模式（拆段落群逐群补足）" in log_txt)
    check("S3 没有整章重生成", "打回：整章重生成" not in log_txt,
          [ln for ln in log_txt.splitlines() if "整章重生成" in ln][:2])
    # 拆群数从拼装行取：`[enhanced] 第1章 拼装完成：2 群 → …`
    # （逐群的 "群g/G" 行只在流式进度回调里打，非流式跑不到 —— 别拿它数）
    gm = re.search(r"拼装完成：(\d+) 群", log_txt)
    used = int(gm.group(1)) if gm else -1
    check("S4 拆群数与 plan_groups 一致（调用有界）", used == groups, (used, groups))
    chap = tmp / "data/chapters/raw/01.md"
    check("S5 章真的写出来了", chap.exists())
    if chap.exists():
        check("S5 正文达标（is_chapter_complete）",
              is_chapter_complete(chap, TW[0], TW[1]))
    prog = json.loads((tmp / "data/state/progress.json").read_text(encoding="utf-8"))
    st4 = prog.get("stages", {}).get("4", {})
    check("S5 第 1 章被标完成（不是停在失败）",
          1 in (st4.get("completed_chapters") or []) and not (st4.get("failed_chapters") or []),
          {"completed": st4.get("completed_chapters"), "failed": st4.get("failed_chapters"),
           "job": job.get("state")})

    # ---- 中度偏短：R=0.75（高于 0.6 阈值）----
    print("\n=== S6–S7：中度偏短（R=0.75）===")
    log2, tmp2, job2 = run_case("S6", 0.75, PORT_B)
    if not log2:
        return 1
    m2 = re.search(r"字数 (\d+) 不在 \[(\d+), (\d+)\] 区间", log2) \
        or re.search(r"字数 (\d+) < (\d+)", log2)
    check("S6 首轮真写出短稿（仍低于下限）", bool(m2) and int(m2.group(1)) < TW[0],
          m2.group(0) if m2 else "日志里没有字数不过的痕迹")
    check("S6 走定向扩写（不是整章重生成）",
          "打回：定向扩写" in log2 and "打回：整章重生成" not in log2,
          [ln for ln in log2.splitlines() if "打回：" in ln][:3])
    chap2 = tmp2 / "data/chapters/raw/01.md"
    check("S7 正文达标（is_chapter_complete）",
          chap2.exists() and is_chapter_complete(chap2, TW[0], TW[1]))
    prog2 = json.loads((tmp2 / "data/state/progress.json").read_text(encoding="utf-8"))
    st42 = prog2.get("stages", {}).get("4", {})
    check("S7 第 1 章被标完成", 1 in (st42.get("completed_chapters") or []),
          {"completed": st42.get("completed_chapters"), "job": job2.get("state")})

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    print("临时根（证据）: %s | %s" % (tmp, tmp2))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
