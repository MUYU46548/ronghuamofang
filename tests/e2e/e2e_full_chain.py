# -*- coding: utf-8 -*-
"""全链零 token 验证：阶段 1→8 + 导出 Word（2026-11-19）。

## 为什么要有这个

`docs/evidence/` 里只有 2026-09-28 那一份 smoke，之后“全链出 Word 成功过 3~4 次”
全靠记忆，**没有可复跑的证据**。而且这条链一断，坏在最里面：

2026-11-19 第一次真跑就逮到三个断点，全是“任何一个新用户第一次用都会撞”的：
1. `config/project.yaml` 的 `word_template` 指向 `templates/小说格式模板V1.3.dotx`
   —— 那个文件 2026-11-07（`27363c2`）为发布卫生移出了仓库，**默认值没跟着改**
   → stage7 报“模板不存在、不出文件”，导不出 Word。
2. 自由模式（GUI 勾一道工序）跑 `stages=[1]`，跑完触发**全书**质量验收闸门，
   而这时一章都还没写 → 40 章全判缺失 → 成功的阶段被判失败。只有 `--stage N`
   那条路有豁免。
3. 假引擎在 stage6 让三卷并发写同一个 `polish_report.md`，Windows 上第二个线程
   撞同名 `.tmp` → PermissionError，整个阶段判 crashed。

## 布局必须等同打包态

代码与数据**同一个根**（打包态 workspace 就是：主进程把 scripts/prompts/templates
+ config 一起播种进去）。踩过的坑：只播 prompts 不播 scripts 时，质量闸按
`__file__` 推项目根 → 脚本在仓库、数据在工作区，两边打架，会把 40 章判成缺失。

## 跑法

    python tests/e2e/e2e_full_chain.py            # 约 2-4 分钟，零 token
    python tests/e2e/e2e_full_chain.py --keep     # 保留工作区（排障用）

依赖：.venv 里的 python + 仓库 scripts / prompts / templates / config、
`examples/sample-book/materials`（进包夹具）。
"""
import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYEXE = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYEXE.exists():
    PYEXE = Path(sys.executable)

PASS = FAIL = 0


def ok(cond, msg, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + msg)
    else:
        FAIL += 1
        print("  [FAIL] " + msg + ("  | " + str(detail)[:200] if detail else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(url, method="GET", body=None, headers=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:                                    # noqa: BLE001
            return e.code, {}


def main(argv=None):
    ap = argparse.ArgumentParser(description="全链零 token 验证")
    ap.add_argument("--keep", action="store_true", help="跑完保留工作区")
    args = ap.parse_args(argv)

    ws = Path(tempfile.mkdtemp(prefix="nf_chain_"))
    print("工作区：" + str(ws))
    shutil.copytree(ROOT / "config", ws / "config",
                    ignore=shutil.ignore_patterns("history"))
    for d in ("scripts", "prompts", "templates"):
        shutil.copytree(ROOT / d, ws / d,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "examples/sample-book/materials", ws / "materials")

    print("\n=== E0 出厂配置指向的 Word 模板必须真在 ===")
    import io
    yml = io.open(ws / "config" / "project.yaml", encoding="utf-8").read()
    line = [l for l in yml.splitlines() if l.strip().startswith("word_template:")]
    rel = line[0].split(":", 1)[1].strip().strip('"').strip("'") if line else ""
    ok(bool(rel), "配置里有 word_template", line)
    ok((ws / rel).exists() if rel else False,
       "模板文件存在：" + rel, "2026-11-07 私人模板移出仓库后默认值没改，新用户必撞")

    port = free_port()
    tok = "e2e-chain-tok"
    env = dict(__import__("os").environ, NF_GUI_TOKEN=tok, PYTHONIOENCODING="utf-8")
    env.pop("NF_ROOT", None)
    # 2026-11-20：`run_all.py` 会给所有子进程设 NF_LICENSE_ACK=1（多数套件跑在
    # 从没签过字的临时工作区上，不豁免就满屏 403）。但本脚本 E1 **正是要验真拦**
    # （未签字 → /state 403），被外层豁免污染就成了假绿 —— 实测收进 run_all 后
    # 这条当场变红（期望 403 实得 200）。这里显式摘掉，让"总闸在工作"始终是真验。
    env.pop("NF_LICENSE_ACK", None)
    logf = ws / "nf_api_child.log"
    fh = open(logf, "wb")
    proc = subprocess.Popen(
        [str(PYEXE), str(ws / "scripts/nf_api.py"), "--port", str(port),
         "--root", str(ws), "--allow-fake"],
        cwd=str(ws), env=env, stdout=fh, stderr=subprocess.STDOUT)
    base = "http://127.0.0.1:%d" % port
    GUI = {"X-Mofang-Source": "gui", "X-Mofang-Token": tok}

    try:
        for _ in range(400):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.2)
        print("\n=== E1 服务起来 + 许可签字 ===")
        c, b = http(base + "/health")
        ok(c == 200, "GET /health 200", (c, b))
        c, b = http(base + "/state")
        ok(c == 403, "未签字时全站 403（总闸在工作）", (c, b))
        c, b = http(base + "/license/ack", "POST", {"agree": True}, GUI)
        ok(c == 200, "签字 200", (c, b))
        c, b = http(base + "/state")
        ok(c == 200, "签字后 /state 200", (c, b))

        print("\n=== E2 阶段 1→8 逐段推进（审批门照本项目的门走，不绕过） ===")
        gate_hits = 0
        for n in range(1, 9):
            c, b = http(base + "/stages/run", "POST", {"stages": [n]})
            if c != 202:
                ok(False, "阶段 %d 起 job" % n, (c, b))
                break
            jid = b.get("job_id")
            t0 = time.time()
            state, st = None, {}
            while time.time() - t0 < 1800:
                _c, st = http(base + "/jobs/" + jid)
                state = (st or {}).get("state")
                if state and state != "running":
                    break
                time.sleep(2)
            detail = str((st or {}).get("result") or "")
            ok(state == "ok", "阶段 %d 跑完（%.0fs）%s" % (n, time.time() - t0, detail[:60]),
               (state, detail))
            if "exit=3" in detail or "审批" in detail:
                gate_hits += 1
                prog = json.loads((ws / "data/state/progress.json").read_text(encoding="utf-8"))
                for key, rec in sorted(prog.get("stages", {}).items()):
                    if rec.get("status") == "done" and not rec.get("approved"):
                        c2, r2 = http(base + "/approve", "POST", {"stage": int(key)}, GUI)
                        if c2 != 200:
                            # 分段审批：逐段标记已读（模拟作者逐段读完）
                            _c3, seg = http(base + "/outline/segments")
                            for it in ((seg or {}).get("segments") or []):
                                http(base + "/outline/segments/set",
                                     "POST", {"id": it.get("id"), "approved": True}, GUI)
                            c2, r2 = http(base + "/approve", "POST", {"stage": int(key)}, GUI)
                        ok(c2 == 200, "阶段 %s 确认放行" % key, (c2, str(r2)[:120]))
                # 门过了要再跑一次这个阶段（上一次停在门前，产物还没落）
                c4, b4 = http(base + "/stages/run", "POST", {"stages": [n]})
                if c4 == 202:
                    jid2 = b4.get("job_id")
                    t0 = time.time()
                    while time.time() - t0 < 1800:
                        _c, st2 = http(base + "/jobs/" + jid2)
                        if (st2 or {}).get("state") not in (None, "running"):
                            break
                        time.sleep(2)
                    ok((st2 or {}).get("state") == "ok",
                       "阶段 %d 过门后重跑完成" % n, (st2 or {}).get("result"))
        ok(gate_hits >= 2, "审批门真的拦过（不是一路空跑）", gate_hits)

        print("\n=== E3 产物 ===")
        # `data/merged/book.md` **不在这里查**（2026-11-24 修）：它由阶段 7 的
        # `merge_book()` 生成，而阶段 7 是 E4 里 `/export/word` 才触发的。
        # 放在 E3 等于“在导出之前要求导出产物”，必然红 —— 这条从 11-17 起就一直
        # 潜伏着（release-check 只跑 ux 套，full 从没进过闸门，所以没人看见）。
        for rel, low in (("data/setting/setting.json", 1),
                         ("data/outline/global.md", 1),
                         ("data/chapters/raw", 40),
                         ("data/chapters/checked", 40),
                         ("data/chapters/refined", 40)):
            p = ws / rel
            if p.is_dir():
                n = len([x for x in p.rglob("*") if x.is_file()])
                ok(n >= low, "%s ≥ %d 个文件（实 %d）" % (rel, low, n), n)
            else:
                ok(p.exists() and p.stat().st_size >= low,
                   "%s 存在" % rel, p.stat().st_size if p.exists() else "缺")

        st_json = json.loads((ws / "data/setting/setting.json").read_text(encoding="utf-8"))
        ok(all(k in st_json for k in ("characters", "world", "plot_fragments", "timeline")),
           "设定集四顶层键齐", list(st_json))

        print("\n=== E4 导出 Word 并核真身 ===")
        c, b = http(base + "/export/word", "POST", {"book_name": "全链验证"})
        ok(c == 200, "POST /export/word 200", (c, b))
        docx = Path((b or {}).get("path") or "")
        ok(docx.exists() and docx.stat().st_size > 20000,
           "docx 存在且 > 20 KB", ("%s B" % docx.stat().st_size) if docx.exists() else docx)
        if docx.exists():
            import re
            import zipfile
            z = zipfile.ZipFile(docx)
            ok("word/document.xml" in z.namelist(), "docx 结构合法（有 document.xml）")
            text = re.sub(r"<[^>]+>", "", z.read("word/document.xml").decode("utf-8"))
            cn = len(re.findall(r"[\u4e00-\u9fa5]", text))
            ok(cn > 100000, "正文汉字数 > 10 万（实 %d）" % cn, cn)
            ok("第1章" in text or "第 1 章" in text, "章节标题进正文")

        # 合并稿：导出之后才有（判据放在真正的时机上，见 E3 的说明）
        merged = ws / "data/merged/book.md"
        ok(merged.exists() and merged.stat().st_size > 1000,
           "data/merged/book.md 由导出生成",
           merged.stat().st_size if merged.exists() else "缺")

        print("\n=== E5 全链没有 500（500 = 程序故障） ===")
        logtxt = logf.read_text(encoding="utf-8", errors="replace")
        bad = [l for l in logtxt.splitlines()
               if "Traceback" in l or "PermissionError" in l]
        ok(not bad, "运行日志无 Traceback / PermissionError", bad[:3])

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        fh.close()

    print("\n" + "=" * 56)
    print("合计：%d 通过 / %d 失败" % (PASS, FAIL))
    print("工作区：" + str(ws) + ("" if args.keep else "（可删）"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
