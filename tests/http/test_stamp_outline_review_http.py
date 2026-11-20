# -*- coding: utf-8 -*-
"""大纲与审稿决策的盘上指纹闸 —— 真起 nf_api，临时工作区。

## 为什么补这一份

2026-11-13 只给**设定集**装了指纹闸（`/setting/save` 的 `expected_mtime`），
`docs/问题总账.md` 当时就把剩下的写在那里：大纲与审稿还没有指纹闸，
它们保存的是整份文本 / 整批决策，并发覆盖同样会发生，这一轮没做。

后果与设定集同一族，但更隐蔽：大纲是整份覆盖写，审稿决策是一份文件装全书
所有 accept/ignore。中间被 CLI / 外部 Agent / 另一个窗口写过之后，界面再点保存
就整份盖掉，作者只会看到"我什么都没删，怎么少了一段"。

## 覆盖（三组，同一套口径）

A 整份大纲 `/outline/save`
  A1 `GET /outline/structure` 带 mtime 且等于盘上 st_mtime
  A2 不传 expected_mtime → 200（脚本 / 旧客户端不回归）
  A3 传对的 mtime → 200，盘上真变了
  A4 外部改盘后拿旧指纹 → 409，**盘上仍是别人那份**
  A5 A4 那一次**没产生新备份**（校验必须在备份之前，否则 history 里堆假保存）
  A6 刷新拿到新指纹后再存 → 200（不把人锁死）
  A7 expected_mtime 非数字 → 400（不是 500）

B 分章大纲 `/outline/chapters/save`
  B1 `GET /outline/chapters/get` 带 mtime
  B2 旧指纹 → 409，盘上不动
  B3 B2 那一次**没多出 .bak**（同一红线）
  B4 对指纹 → 200，盘上真变

C 审稿决策 `/review/decisions`
  C1 盘上还没有文件时 GET 给 `mtime: None`（不是 404，也不是编一个数）
  C2 首存不传指纹 → 200
  C3 拿到 mtime 后传对的 → 200
  C4 外部改盘后旧指纹 → 409，盘上仍是外部那份
  C5 非数字 → 400

D 老护栏不回归：同一端点**不带 GUI 双因子**时仍只写候选区，正式稿一个字节不动。

用法：python tests/http/test_stamp_outline_review_http.py
"""
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
PY = sys.executable
PORT = 8963
BASE = "http://127.0.0.1:%d" % PORT
GUI_TOKEN = "stampcase-outline-token"
GUI_HEADERS = {"X-Mofang-Source": "gui", "X-Mofang-Token": GUI_TOKEN}

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def req(method, path, body=None, gui=True, timeout=60):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    headers = {"Content-Type": "application/json"}
    if gui:
        headers.update(GUI_HEADERS)
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                   # noqa: BLE001
            payload = {}
        return e.code, payload


OUTLINE_OK = """# 测试书

## 起
第一幕：主角在码头接到一封没有落款的信，决定回家。

## 承
第二幕：旧宅里发现父亲留下的账本，账本上多出四个人名。

## 转
第三幕：四个人名逐一出现，其中一个是自己。

## 合
第四幕：把账本烧掉，回到码头。

## 关键节点
- 收信：码头，主角接下无名信
- 查账：旧宅书房，翻出账本
- 对质：祠堂，四个名字当面对上

## 预计章节数
12
"""

CH_OUTLINE_OK = """# 第1章 无名信

## 核心事件
- 主角在码头收到一封没有落款的信

## 涉及角色
- 沈砚

## 章职能
建立日常，抛出第一根刺。
"""


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_stamp_ole_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(
        json.dumps({"characters": [{"name": "沈砚", "role": "主角"}],
                    "world": [], "plot_fragments": [], "timeline": []},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp / "data" / "outline" / "chapters").mkdir(parents=True)
    (tmp / "data" / "outline" / "global.md").write_text(OUTLINE_OK, encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text(CH_OUTLINE_OK, encoding="utf-8")
    return tmp


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            # 许可总闸（2026-11-18）：临时工作区从没被人签过字，
                            # 不豁免会满屏 403（看起来像"整套端点全坏了"）。
                            # 真拦的判据在 tests/http/test_license_http.py，不在这里。
                            env=dict(os.environ, NF_GUI_TOKEN=GUI_TOKEN,
                                     NF_LICENSE_ACK="1"))
    try:
        for _ in range(80):
            try:
                code, _h = req("GET", "/health")
                if code == 200:
                    break
            except Exception:                               # noqa: BLE001
                time.sleep(0.25)
        else:
            print("  [FAIL] 服务未起来")
            return 1

        global_p = tmp / "data" / "outline" / "global.md"
        hist_dir = tmp / "data" / "outline" / "history"
        chap_p = tmp / "data" / "outline" / "chapters" / "01.md"
        dec_p = tmp / "data" / "outline" / "review_report.decisions.json"

        print("\n=== A. 整份大纲：指纹闸 ===")
        code, d = req("GET", "/outline/structure")
        disk_mtime = global_p.stat().st_mtime
        check("A1 GET /outline/structure 带盘上 mtime",
              code == 200 and isinstance(d.get("mtime"), (int, float))
              and abs(float(d["mtime"]) - disk_mtime) <= 0.001,
              (code, d.get("mtime"), disk_mtime))

        v1 = OUTLINE_OK.replace("12", "13")
        code, d = req("POST", "/outline/save", {"content": v1})
        check("A2 不传 expected_mtime → 200（老行为不回归）",
              code == 200 and d.get("ok"), (code, d))
        check("A2b 盘上真变了", "13" in global_p.read_text(encoding="utf-8"))

        code, d = req("GET", "/outline/structure")
        stamp = d.get("mtime")
        n_hist_before = len(list(hist_dir.glob("*"))) if hist_dir.exists() else 0
        v2 = OUTLINE_OK.replace("12", "14")
        time.sleep(0.02)
        code, d2 = req("POST", "/outline/save",
                       {"content": v2, "expected_mtime": stamp})
        check("A3 传对的 mtime → 200 且盘上是这一次",
              code == 200 and d2.get("ok")
              and "14" in global_p.read_text(encoding="utf-8"), (code, d2))
        n_hist_ok = len(list(hist_dir.glob("*"))) if hist_dir.exists() else 0
        check("A3b 成功保存留了备份（比上一次多）", n_hist_ok > n_hist_before,
              (n_hist_before, n_hist_ok))

        # —— 外部改盘：模拟 CLI / 另一个窗口 ——
        n_hist_before = len(list(hist_dir.glob("*"))) if hist_dir.exists() else 0
        time.sleep(0.02)
        external = OUTLINE_OK.replace("12", "99")
        global_p.write_text(external, encoding="utf-8")
        code, d3 = req("POST", "/outline/save",
                       {"content": v2, "expected_mtime": stamp})   # stamp 已过期
        check("A4 旧指纹 → 409（不悄悄覆盖）", code == 409, (code, d3))
        check("A4b 说清谁动过盘 + 怎么办",
              "已被改过" in str(d3.get("error") or "") and d3.get("hint"), d3)
        check("A4c 盘上**仍是外部那份**（一个字没被写回）",
              "99" in global_p.read_text(encoding="utf-8")
              and "14" not in global_p.read_text(encoding="utf-8"))
        n_hist_bad = len(list(hist_dir.glob("*"))) if hist_dir.exists() else 0
        check("A5 409 那次**没产生备份**（校验在备份之前）",
              n_hist_bad == n_hist_before, (n_hist_before, n_hist_bad))
        check("A5b 409 回传当前盘上 mtime（前端据此提示刷新）",
              isinstance(d3.get("mtime"), (int, float)), d3.get("mtime"))

        code, d = req("GET", "/outline/structure")
        fresh = d.get("mtime")
        time.sleep(0.02)
        code, d4 = req("POST", "/outline/save",
                       {"content": v2, "expected_mtime": fresh})
        check("A6 刷新后新指纹 → 200（不把人锁死）",
              code == 200 and d4.get("ok")
              and "14" in global_p.read_text(encoding="utf-8"), (code, d4))

        code, d5 = req("POST", "/outline/save",
                       {"content": v2, "expected_mtime": "abc"})
        check("A7 expected_mtime 非数字 → 400", code == 400, (code, d5))

        print("\n=== B. 分章大纲：指纹闸 ===")
        code, d = req("GET", "/outline/chapters/get?n=1")
        ch_mtime = chap_p.stat().st_mtime
        check("B1 GET /outline/chapters/get 带 mtime",
              code == 200 and isinstance(d.get("mtime"), (int, float))
              and abs(float(d["mtime"]) - ch_mtime) <= 0.001,
              (code, d.get("mtime"), ch_mtime))

        n_bak_before = len(list((tmp / "data" / "outline" / "chapters").glob("*.bak")))
        time.sleep(0.02)
        external_ch = CH_OUTLINE_OK.replace("沈砚", "外部写的名字")
        chap_p.write_text(external_ch, encoding="utf-8")
        code, d6 = req("POST", "/outline/chapters/save",
                       {"n": 1, "content": CH_OUTLINE_OK.replace("无名信", "新标题"),
                        "expected_mtime": ch_mtime})
        check("B2 旧指纹 → 409", code == 409, (code, d6))
        check("B2b 盘上仍是外部那份",
              "外部写的名字" in chap_p.read_text(encoding="utf-8"))
        n_bak_after = len(list((tmp / "data" / "outline" / "chapters").glob("*.bak")))
        check("B3 409 那次没多出 .bak（校验在备份之前）",
              n_bak_after == n_bak_before, (n_bak_before, n_bak_after))

        code, d = req("GET", "/outline/chapters/get?n=1")
        fresh_ch = d.get("mtime")
        time.sleep(0.02)
        code, d7 = req("POST", "/outline/chapters/save",
                       {"n": 1, "content": CH_OUTLINE_OK.replace("无名信", "新标题"),
                        "expected_mtime": fresh_ch})
        check("B4 对指纹 → 200 且盘上真变",
              code == 200 and d7.get("ok")
              and "新标题" in chap_p.read_text(encoding="utf-8"), (code, d7))

        print("\n=== C. 审稿决策：指纹闸 ===")
        check("C1 盘上没有决策文件时给 mtime: None（不是 404 / 不编数）",
              not dec_p.exists(), dec_p)
        code, d = req("GET", "/review/decisions")
        check("C1b GET 返回空决策 + mtime: None",
              code == 200 and d.get("decisions") == [] and d.get("mtime") is None,
              (code, d))

        dec1 = {"decisions": [{"finding_id": "F1", "chapter": 1,
                               "action": "accept", "feedback": ""}]}
        code, d = req("POST", "/review/decisions", dec1)
        check("C2 首存不传指纹 → 200", code == 200 and d.get("ok"), (code, d))

        code, d = req("GET", "/review/decisions")
        dec_stamp = d.get("mtime")
        check("C3a GET 现在带 mtime",
              isinstance(dec_stamp, (int, float)), dec_stamp)
        dec2 = {"decisions": [{"finding_id": "F2", "chapter": 2,
                               "action": "ignore", "feedback": ""}]}
        time.sleep(0.02)
        code, d8 = req("POST", "/review/decisions",
                       dict(dec2, expected_mtime=dec_stamp))
        check("C3 传对的指纹 → 200 且盘上换成新一批",
              code == 200 and d8.get("ok")
              and "F2" in dec_p.read_text(encoding="utf-8"), (code, d8))

        time.sleep(0.02)
        external_dec = {"decisions": [{"finding_id": "EXT", "chapter": 9,
                                       "action": "accept", "feedback": ""}]}
        dec_p.write_text(json.dumps(external_dec, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        code, d9 = req("POST", "/review/decisions",
                       dict(dec2, expected_mtime=dec_stamp))     # 已过期
        check("C4 旧指纹 → 409", code == 409, (code, d9))
        check("C4b 盘上仍是外部那份（没被整批抹掉）",
              "EXT" in dec_p.read_text(encoding="utf-8")
              and "F2" not in dec_p.read_text(encoding="utf-8"))
        check("C4c 拒文说清了原因", "已被改过" in str(d9.get("error") or ""), d9)

        code, d10 = req("POST", "/review/decisions",
                        dict(dec2, expected_mtime="xyz"))
        check("C5 expected_mtime 非数字 → 400", code == 400, (code, d10))

        print("\n=== D. 老护栏不回归：非 GUI 来源仍只写候选区 ===")
        before = global_p.read_text(encoding="utf-8")
        code, d11 = req("POST", "/outline/save",
                        {"content": OUTLINE_OK.replace("12", "77")}, gui=False)
        after = global_p.read_text(encoding="utf-8")
        check("D1 非 GUI 保存 → 200 且落候选区",
              code == 200 and d11.get("candidate"), (code, d11))
        check("D2 正式大纲一个字节没动", before == after)

    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:                                   # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
