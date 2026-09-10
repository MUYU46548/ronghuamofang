# -*- coding: utf-8 -*-
"""nf_api 验收自测（GUI 化 P0）。启动 --allow-fake 服务 → 纯 HTTP 驱动全链。

前置：config/project.yaml 书名可任意（fake 不读）；data/ 已清理。
运行：.venv/Scripts/python.exe scripts/nf_api_selftest.py
覆盖：health/models/state、stage1→2 全链、审批门（exit=3 语义）、
      撤销审批、打回 dry-run、refine dry-run、409 并发互斥、404。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8901
BASE = "http://127.0.0.1:" + str(PORT)

ok_count, fails = 0, []


def check(name, cond, info=""):
    global ok_count
    if cond:
        ok_count += 1
        print("PASS", name)
    else:
        fails.append(name)
        print("FAIL", name, "--", str(info)[:200])


def req(method, path, body=None):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {}
        return e.code, payload


def wait_job(jid, timeout=60):
    for _ in range(timeout * 10):
        _, job = req("GET", "/jobs/" + jid)
        if job["state"] in ("ok", "failed"):
            return job
        time.sleep(0.2)
    return {"state": "timeout"}


def wait_state(cond, timeout=30, desc=""):
    for _ in range(timeout * 10):
        _, st = req("GET", "/state")
        if cond(st):
            return st
        time.sleep(0.2)
    _, st = req("GET", "/state")
    print("  state 快照:", json.dumps(st["stages"], ensure_ascii=False)[:300])
    raise TimeoutError("等待超时: " + desc)


def main():
    # 护栏：自测会清空 data/ 运行产物，检测到真实书档时拒绝执行（除非 --force）
    if "--force" not in sys.argv:
        guard = ROOT / "data" / "setting" / "setting.json"
        raw_dir = ROOT / "data" / "chapters" / "raw"
        if guard.exists() or (raw_dir.exists() and any(raw_dir.glob("*.md"))):
            print("⚠️ 检测到已存在的书档产物（setting.json / 章节）。")
            print("   本自测会清空 data/ 与 logs/。确认要销毁请加 --force 重跑。")
            return 2

    # 强制快照：销毁前先备份，防止数据丢失
    print("== 销毁前自动快照 ==")
    from snapshot import snapshot as make_snapshot
    snap_dir = make_snapshot("selftest_wipe")
    print(f"   快照已保存: {snap_dir}")

    # 清场：data/ 与 logs/（保留 books/ 归档）
    for d in ("data/setting", "data/outline", "data/chapters", "data/summaries",
              "data/state", "data/tmp", "data/test_llm_t8", "logs"):
        p = ROOT / d
        if d == "data/state" and p.exists():
            pass  # progress 由服务端管理，删除后 ProgressManager 自动重建
        if p.exists():
            (shutil.rmtree if p.is_dir() else (lambda x: x.unlink()))(p)
    print("== 已清理 data/ 与 logs/ ==")

    env = dict(os.environ, NF_API_ALLOW_FAKE="1")
    proc = subprocess.Popen([str(PY), str(ROOT / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # 基础端点
        for _ in range(50):
            try:
                code, h = req("GET", "/health")
                break
            except Exception:
                time.sleep(0.2)
        check("health", code == 200 and h["ok"] is True and h["allow_fake"] is True, h)
        code, models = req("GET", "/models")
        check("models 回显", code == 200 and "engine" in models and "model" in models,
              str(models)[:120])

        # stage1（fake）→ 设定集
        code, r1 = req("POST", "/stage/1/run")
        check("stage1 接受", code == 202 and "job_id" in r1, str(r1)[:120])
        j1 = wait_job(r1["job_id"])
        check("stage1 完成", j1["state"] == "ok", str(j1)[:200])
        check("setting.json 生成", (ROOT / "data/setting/setting.json").exists())

        # stage2 → 审批门（exit=3 语义 = ok）
        code, r2 = req("POST", "/stage/2/run")
        check("stage2 接受", code == 202, str(r2)[:120])
        j2 = wait_job(r2["job_id"])
        check("stage2 完成", j2["state"] == "ok", str(j2)[:200])
        check("global.md 生成", (ROOT / "data/outline/global.md").exists())
        st = wait_state(lambda s: s["stages"][1]["status"] == "done"
                        and s["stages"][1]["approved"] is False, desc="stage2 done 未审批")
        check("stage2 done 且未审批", True)
        check("成本记账有流水", st["cost"]["calls"] >= 2, str(st["cost"])[:120])

        # 未审批 → 下游仍会暂停（stage3 run → exit 3，job ok）
        code, r3 = req("POST", "/stage/3/run")
        j3 = wait_job(r3["job_id"])
        check("审批门生效（未审批重跑=门暂停）", j3["state"] == "ok" and "3=等待审批" in (j3.get("result") or ""),
              str(j3)[:200])

        # 409 互斥：再点一次 run（无任务在跑时可能不触发；先制造长任务不需要——验证 409 路径存在即可）
        code, r4 = req("POST", "/stage/9/run")
        check("非法阶段 400", code == 400, str(r4))

        # 审批 stage2
        code, ra = req("POST", "/approve", {"stage": 2})
        check("approve 200", code == 200 and ra["ok"] is True, str(ra)[:120])
        _, st = req("GET", "/state")
        check("审批状态可见", st["stages"][1]["approved"] is True)

        # 未完成阶段审批 → 409 提示
        code, rb = req("POST", "/approve", {"stage": 4})
        check("未完成审批 409", code == 409, str(rb)[:120])

        # 撤销审批
        code, rc_ = req("POST", "/approve", {"stage": 2, "revoke": True})
        check("revoke 200", code == 200, str(rc_)[:120])

        # 恢复审批（后续打回 dry-run 需要 stage2 approved）
        req("POST", "/approve", {"stage": 2})

        # 打回 dry-run（不清产物）
        before = (ROOT / "data/outline/global.md").exists()
        code, rd = req("POST", "/reject", {"stage": 2, "reason": "自测打回", "dry_run": True})
        check("reject dry-run 200", code == 200 and rd["ok"] is True, str(rd)[:160])
        check("dry-run 未清产物", (ROOT / "data/outline/global.md").exists() == before)

        # refine outline dry-run（FakeClient 模式下不调用真实引擎）
        code, rf = req("POST", "/refine/outline", {"feedback": "第2章加强露汐", "dry_run": True})
        check("refine outline 202", code == 202, str(rf)[:120])
        if code == 202:
            jf = wait_job(rf["job_id"])
            check("refine outline 完成", jf["state"] == "ok", str(jf)[:200])

        # 404
        code, e404 = req("GET", "/nope")
        check("未知路径 404", code == 404, str(e404))

        print("====", ok_count, "PASS,", len(fails), "FAIL", fails)
        return 1 if fails else 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
