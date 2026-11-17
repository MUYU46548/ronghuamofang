# -*- coding: utf-8 -*-
"""段落级精修端到端（B 路"未覆盖：段落级精修"那一格，2026-11-17）。

## 为什么要有它

`问题总账` 第四章 B 路登记："未覆盖：段落级精修、整章重写、真实 40 章书"。
界面侧有 `e2e_ux_verify` 的多候选切换，但那是**假后端**——它验的是"按钮能点、
候选能切"，验不到"点采用之后盘上那一段真的变了、旧稿真进了历史、冲突真的不覆盖"。
这几件恰好是最贵的（改错一段要手工回退），所以必须端到端验。

## 怎么做到零 token

`preview` 是模型调用（另有用例覆盖），本用例**只验采用侧**：
直接把"模型产出的候选"写进 `para_pending.json`（就是 preview 的落点，结构同一份），
再打真 HTTP 采用。候选文本是造的，但走的是**生产代码同一条路径**。

## 判据（每条都能变红）

- P1 采用候选 → 200，且**盘上那一段真的变成候选文本**（不是只改内存）
- P2 其他段落一个字不动（只改目标段）
- P3 采用前把旧稿存进历史（`history/chNN_vM.md`）—— 采用本身可回退
- P4 候选清空（正文变了，留着旧候选会点错）
- P5 **冲突不覆盖**：候选是"基于旧正文"的，中途把这一段手改掉 → 采用 409
     且回执里有 `conflict`（现在的正文 / 候选基于的正文 / 候选 三件）
- P6 `force:true` 才覆盖（作者明确同意）
- P7 放弃候选 → 正文一个字节不动

用法：python tests/e2e/e2e_paragraph_refine.py
前置：无（自带临时项目根，端口 18792，不碰 8765）
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
sys.path.insert(0, str(ROOT / "scripts"))
PORT = int(os.environ.get("NF_E2E_PARA_PORT") or 18792)
API = "http://127.0.0.1:%d" % PORT
CH = 3

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


BODY = """## 第3章 雨夜

她把杯子放下，杯底和木桌碰出很轻的一响。

窗外的水线连成了串，屋檐下积着一小滩暗色的水。

她走到门口，回头看了一眼桌上那只杯子，又把它端起来。
"""


def build_root():
    tmp = Path(tempfile.mkdtemp(prefix="e2e_para_"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    for d in ("data/chapters/raw", "data/chapters/history", "data/outline",
              "data/setting", "data/state"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "logs").mkdir(exist_ok=True)
    (tmp / "config/project.yaml").write_text(
        'book:\n  name: "段落精修自测"\n  genre: "测试"\n  target_words: 6000\n  chapters: 3\n'
        '  language: "zh-CN"\n  author: "自测"\n  style: ""\n', encoding="utf-8")
    (tmp / "data/setting/setting.json").write_text(
        json.dumps({"characters": [], "world": {}}, ensure_ascii=False), encoding="utf-8")
    (tmp / "data/outline/global.md").write_text("# 整体大纲\n\n测试。\n", encoding="utf-8")
    (tmp / "data/chapters/raw" / ("%02d.md" % CH)).write_text(BODY, encoding="utf-8")
    (tmp / "data/state/progress.json").write_text(json.dumps({
        "project": "段落精修自测", "version": 3, "current_stage": 4,
        "stages": {str(n): {"status": "pending"} for n in range(1, 9)},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp


def paragraphs(text):
    from refine_paragraph import split_paragraphs
    return split_paragraphs(text)


def write_pending(tmp, items):
    p = tmp / "data/state/para_pending.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""


def main():
    print("=" * 66)
    print("段落级精修 · 端到端（采用 / 冲突 / 回退，端口 %d）" % PORT)
    print("=" * 66)
    tmp = build_root()
    chap = tmp / "data/chapters/raw" / ("%02d.md" % CH)
    paras = paragraphs(BODY)
    # 第 3 段（index 从 0 起；0 是标题行）
    idx = 2
    before = paras[idx].strip()
    after = "她把杯子放下，杯底磕在木桌上，响了一声，茶水晃了一圈。"
    print("  目标段 index=%d 原文：%s" % (idx, before[:40]))

    env = dict(os.environ)
    log = open(tmp / "api.log", "wb")
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "nf_api.py"), "--port", str(PORT),
         "--allow-fake", "--root", str(tmp)],
        stdout=log, stderr=subprocess.STDOUT, env=env,
        cwd=str(tmp))   # CWD 必须是临时根（history/ 与 data/** 都是 CWD 相对）
    try:
        alive = False
        for _ in range(80):
            try:
                urllib.request.urlopen(API + "/health", timeout=1)
                alive = True
                break
            except Exception:                               # noqa: BLE001
                time.sleep(0.25)
        check("P0 后端起来", alive, "端口 %d" % PORT)
        if not alive:
            return 1

        # ---- P1/P2/P3/P4：采用候选 ----
        write_pending(tmp, [{"chapter": CH, "index": idx, "cid": "c1",
                             "before": before, "after": after,
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}])
        code, d = req("/chapters/paragraph/apply",
                      {"chapter": CH, "paragraph_index": idx, "confirm": True}, "POST")
        check("P1 采用候选返回 200", code == 200 and d.get("ok") is True, (code, d.get("message")))
        txt = chap.read_text(encoding="utf-8")
        check("P1 盘上那一段真的变了", after in txt and before not in txt,
              [p[:24] for p in paragraphs(txt)])
        now = paragraphs(txt)
        # 比较口径去空白：save_paragraph_text 会做**标点/空格归一化**（正文标题
        # `## 第3章 雨夜` → `## 第3章雨夜`），那是既有的幂等归一，不是"改了别的段"。
        def _n(t):
            return "".join(ch for ch in t if not ch.isspace())
        _orig = paragraphs(BODY)
        diff = [i for i in range(len(_orig)) if i != idx and _n(now[i]) != _n(_orig[i])]
        check("P2 其他段落一个字不动（去空白比较）", not diff, diff)
        hist = list((tmp / "data/chapters/history").glob("ch%02d_v*.md" % CH))
        check("P3 采用前把旧稿存进历史", bool(hist), [p.name for p in hist][:3])
        if hist:
            check("P3 历史里是旧的那一版", before in hist[0].read_text(encoding="utf-8"))
        code_p, dp = req("/chapters/paragraph/pending?chapter=%d&index=%d" % (CH, idx))
        items = (dp.get("items") if isinstance(dp, dict) else dp) or []
        check("P4 采用后候选被清空", not items, items)

        # ---- P5：冲突不覆盖 ----
        changed = "她把杯子放下，杯底磕在木桌上，响了一声，茶水晃了一圈，又晃回来。"
        txt = chap.read_text(encoding="utf-8")
        write_pending(tmp, [{"chapter": CH, "index": idx, "cid": "c2",
                             "before": after, "after": changed,
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}])
        # 候选生成之后，这一段被"手改"过
        hand = "她把杯子放下，杯底磕在木桌上，响了一声。"
        chap.write_text(txt.replace(after, hand), encoding="utf-8")
        code, d = req("/chapters/paragraph/apply",
                      {"chapter": CH, "paragraph_index": idx, "confirm": True}, "POST")
        check("P5 冲突时 409（不静默覆盖）", code == 409, (code, d.get("error")))
        conf = d.get("conflict") or {}
        check("P5 回执摊开三件证据（现在/候选基于/候选）",
              bool(conf.get("current")) and bool(conf.get("before")) and bool(conf.get("after")),
              {k: str(v)[:20] for k, v in conf.items()})
        check("P5 正文仍是手改的那一版", hand in chap.read_text(encoding="utf-8"))

        # ---- P6：force 才覆盖 ----
        code, d = req("/chapters/paragraph/apply",
                      {"chapter": CH, "paragraph_index": idx, "confirm": True, "force": True},
                      "POST")
        check("P6 force:true 才真覆盖", code == 200 and changed in chap.read_text(encoding="utf-8"),
              (code, d.get("message")))

        # ---- P7：放弃候选不动正文 ----
        write_pending(tmp, [{"chapter": CH, "index": idx, "cid": "c3",
                             "before": changed, "after": "另一版候选，不该进正文。",
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}])
        before_sha = sha(chap)
        code, d = req("/chapters/paragraph/drop",
                      {"chapter": CH, "paragraph_index": idx}, "POST")
        check("P7 放弃候选返回 200", code == 200, (code, d.get("message")))
        check("P7 放弃后正文一个字节不动", sha(chap) == before_sha)
        check("P7 候选里的文本没进正文",
              "另一版候选" not in chap.read_text(encoding="utf-8"))
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
