# -*- coding: utf-8 -*-
"""讨论 AI 轮的 HTTP 真机验收（C1/C2/C18，2026-11-16）。

离线单测（tests/unit/test_discuss_ai_turn.py）证明的是**函数级**行为；
这个脚本证明**端点级**：真起一个 `nf_api`（--allow-fake，临时项目根），
用真 HTTP 走完：

    POST /discuss/open → POST /discuss/ai_turn → GET /stream/{job}
    → 讨论稿真的多一轮 → .ai.json 真的落盘
    第二轮 → POST /stop → job 停在 stopped → 讨论稿**一个字都没多** → 残片落盘

为什么需要它：`start_job` + `STREAMERS` 这段接线在单测里是被绕过的
（单测直接调 `run_turn`）。而"按钮点了没反应"这类事故恰好只在这条链路上出现。

前置：无（自带临时项目根，端口 18765，不碰用户在用的 8765）。
用法：python tests/e2e/e2e_discuss_ai_turn_api.py
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "Temp" / "e2e_discuss_ai"
PORT = 18765
API = "http://127.0.0.1:%d" % PORT
TOPIC = "e2e-雨夜来客"
BODY = ("第 03 章 雨夜\n\n"
        "他推门进来的时候，屋檐上的水线正连成了串，地上积了一小滩暗色的水。\n\n"
        "她把杯子放下，杯底和木桌碰出很轻的一响，茶水晃了一圈。\n")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def http(path, method="GET", body=None, timeout=30):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except ValueError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def read_sse(job_id, timeout=45):
    """读 /stream/{job_id} 直到 done。返回 (tokens, done_payload)。

    按**行**读（`for line in resp`），不按字节：SSE 是 UTF-8 文本，
    `read(1)` 会把一个汉字切成两个字节分别解码 → 满屏替换符（第一次跑就踩了）。
    """
    tokens = []
    done = {}
    req = urllib.request.Request(API + "/stream/" + job_id)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            deadline = time.time() + timeout
            for raw in resp:
                if time.time() > deadline:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if msg.get("type") == "token":
                    tokens.append(msg.get("text") or "")
                elif msg.get("type") == "done":
                    return tokens, msg
    except Exception:                                          # noqa: BLE001
        pass
    return tokens, done


def prepare_root():
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "config").mkdir(parents=True, exist_ok=True)
    (WORK / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    for name in ("system.yaml", "project.yaml"):
        src, dst = ROOT / "config" / name, WORK / "config" / name
        if src.exists():
            text = src.read_text(encoding="utf-8")
            if name == "system.yaml":
                # 强制 direct：本用例不真的发请求（--allow-fake），
                # 但 engine: self 会让端点按设计拒绝，测的就不是 AI 轮了。
                text = re.sub(r"(?m)^engine:\s*\S+", "engine: direct", text)
            dst.write_text(text, encoding="utf-8")
    (WORK / "data" / "chapters" / "raw" / "03.md").write_text(BODY, encoding="utf-8")
    return WORK


def wait_health(proc, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False, "进程已退出（code=%s）" % proc.returncode
        try:
            code, _ = http("/health", timeout=2)
            if code == 200:
                return True, "ok"
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(0.4)
    return False, "30 秒内没等到 /health"


def read_rounds(root):
    js = root / "data" / "discuss" / "03" / (TOPIC + ".json")
    try:
        return json.loads(js.read_text(encoding="utf-8")).get("rounds")
    except Exception:                                          # noqa: BLE001
        return None


def main():
    print("=" * 66)
    print("讨论 AI 轮 · HTTP 真机验收（端口 %d）" % PORT)
    root = prepare_root()
    env = dict(os.environ)
    env["NF_FAKE_STREAM_DELAY_MS"] = "45"      # 让"说一半"有真实的时间窗
    env.pop("NF_API_ALLOW_FAKE", None)

    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "nf_api.py"),
         "--port", str(PORT), "--allow-fake", "--root", str(root)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log_path = root / "server.log"
    log_file = open(str(log_path), "w", encoding="utf-8")

    def _drain(pipe, fh):
        try:
            for line in iter(pipe.readline, b""):
                fh.write(line.decode("utf-8", "replace"))
                fh.flush()
        except Exception:                                      # noqa: BLE001
            pass

    import threading
    threading.Thread(target=_drain, args=(proc.stdout, log_file), daemon=True).start()
    try:
        ok, why = wait_health(proc)
        check("E0 服务起来了", ok, why)
        if not ok:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
            return 1

        print("\n【1】开题 + AI 说一轮")
        code, data = http("/discuss/open", "POST",
                          {"chapter": 3, "topic": TOPIC,
                           "question": "这一章要不要让他当场进门？"})
        check("E1 开题 200", code == 200, (code, data))

        code, data = http("/discuss/ai_turn", "POST",
                          {"chapter": 3, "topic": TOPIC, "hint": "往冲突想"})
        check("E2 ai_turn 返回 202", code == 202, (code, data))
        job = (data or {}).get("job_id") if isinstance(data, dict) else ""
        check("E2 有 job_id", bool(job), data)
        if not job:
            return 1

        tokens, done = read_sse(job)
        text = "".join(tokens)
        check("E3 流式真推了 token（不是整块）", len(tokens) > 3,
              "blocks=%d chars=%d" % (len(tokens), len(text)))
        check("E3 done 状态 ok", done.get("status") == "ok", done)
        check("E3 done 里带结果说明", "AI 说了第 1 轮" in str(done.get("result") or ""),
              done.get("result"))

        code, data = http("/discuss/read?chapter=3&topic=" + urllib.parse.quote(TOPIC))
        body = (data or {}).get("text") if isinstance(data, dict) else ""
        check("E4 讨论稿多了一轮", code == 200 and "### 第 1 轮 · AI" in (body or ""),
              (code, (body or "")[-120:]))
        check("E4 稿里是 AI 真说的话", "这一轮先说结论" in (body or ""), (body or "")[-160:])
        ai_json = root / "data" / "discuss" / "03" / (TOPIC + ".ai.json")
        check("E5 结构化产物落盘", ai_json.exists(), ai_json)
        if ai_json.exists():
            payload = json.loads(ai_json.read_text(encoding="utf-8"))
            check("E5 产物带 stance/evidence/action",
                  payload.get("stance") and payload.get("evidence")
                  and isinstance(payload.get("action"), dict), payload)
            check("E5 引文校验结果记在产物里",
                  (payload.get("verify") or {}).get("quotes", 0) >= 1, payload.get("verify"))
        check("E6 轮次 = 1", read_rounds(root) == 1, read_rounds(root))

        print("\n【2】让 AI 闭嘴：说一半就停")
        code, data = http("/discuss/ai_turn", "POST",
                          {"chapter": 3, "topic": TOPIC, "hint": "再来一轮"})
        job2 = (data or {}).get("job_id") if isinstance(data, dict) else ""
        check("E7 第二轮 202", code == 202 and bool(job2), (code, data))
        if job2:
            time.sleep(0.25)                       # 等它真的开始说
            code, data = http("/stop", "POST", {"job_id": job2})
            check("E7 /stop 200", code == 200, (code, data))
            tokens2, done2 = read_sse(job2)
            check("E8 停在 stopped", done2.get("status") == "stopped", done2)
            check("E8 停下前确实说了一些", len("".join(tokens2)) > 0,
                  len("".join(tokens2)))
        check("E9 停下**没有**多一轮", read_rounds(root) == 1, read_rounds(root))
        trunc = list((root / "data" / "state" / "truncated").glob("discuss_*.txt")) \
            if (root / "data" / "state" / "truncated").is_dir() else []
        check("E9 残片进了 truncated", bool(trunc), [p.name for p in trunc])

        print("\n【3】失败可见：不存在的讨论")
        code, data = http("/discuss/ai_turn", "POST",
                          {"chapter": 3, "topic": "根本没有这个讨论"})
        check("E10 404 且说清原因", code == 404 and "没有这个讨论" in str(
            (data or {}).get("error") if isinstance(data, dict) else data), (code, data))
        code, data = http("/discuss/ai_turn", "POST", {"chapter": 0, "topic": "x"})
        check("E10 章节非法 400", code == 400, (code, data))

        print("\n【4】AI 说的“该改哪里”：建议 → 候选（C5/C14）")
        code, data = http("/discuss/actions?chapter=3&topic=" + urllib.parse.quote(TOPIC))
        items = (data or {}).get("items") or [] if isinstance(data, dict) else []
        check("E11 建议清单 200", code == 200, (code, data))
        check("E11 至少一条建议", len(items) >= 1, items)
        first = items[0] if items else {}
        check("E11 建议带种类与动作目标",
              first.get("kind") and isinstance(first.get("target"), dict), first)
        check("E11 锚点体检通过（原句还在正文里）",
              ((first.get("anchor") or {}).get("ok")) is True, first.get("anchor"))

        code, data = http("/discuss/actions/dismiss", "POST",
                          {"chapter": 3, "topic": TOPIC, "id": first.get("id")})
        check("E12 放弃建议 200", code == 200, (code, data))
        code, data = http("/discuss/actions?chapter=3&topic=" + urllib.parse.quote(TOPIC))
        left = (data or {}).get("items") or [] if isinstance(data, dict) else []
        check("E12 放弃后不在待处理里", all(x.get("id") != first.get("id") for x in left), left)

        code, data = http("/discuss/compress", "POST", {"chapter": 3, "topic": TOPIC})
        check("E13 压缩端点 200", code == 200, (code, data))
        check("E13 轮次不多就说没到需要压的长度",
              isinstance(data, dict) and data.get("compressed") is False, data)

        # 再来一轮拿一条新建议，然后把正文改掉 —— 原句消失，应用必须拒
        code, data = http("/discuss/ai_turn", "POST",
                          {"chapter": 3, "topic": TOPIC, "hint": "再说一条"})
        job3 = (data or {}).get("job_id") if isinstance(data, dict) else ""
        if job3:
            read_sse(job3)
        code, data = http("/discuss/actions?chapter=3&topic=" + urllib.parse.quote(TOPIC))
        items2 = (data or {}).get("items") or [] if isinstance(data, dict) else []
        fresh = items2[0] if items2 else {}
        check("E14 新一轮又落了一条建议", bool(fresh.get("id")), items2)
        # 把正文换掉：讨论里引的那句不再存在
        (root / "data" / "chapters" / "raw" / "03.md").write_text(
            "第 03 章 雨夜\n\n天亮了，街上没有人，只有水在往下流。\n", encoding="utf-8")
        code, data = http("/discuss/actions?chapter=3&topic=" + urllib.parse.quote(TOPIC))
        after = ((data or {}).get("items") or [{}])[0] if isinstance(data, dict) else {}
        check("E14 正文一改，锚点体检就报定位不到",
              ((after.get("anchor") or {}).get("ok")) is False, after.get("anchor"))
        code, data = http("/discuss/actions/apply", "POST",
                          {"chapter": 3, "topic": TOPIC, "id": fresh.get("id")})
        check("E14 应用被拒 409（不拿旧段号改错段落）", code == 409, (code, data))
        check("E14 拒文说清“不在原位”",
              "不在原位" in str((data or {}).get("error") if isinstance(data, dict) else data),
              (code, data))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:                                      # noqa: BLE001
            pass

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    print("证据目录：%s" % WORK.as_posix())
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402,F401  （下面用到 quote）
    sys.exit(main())
