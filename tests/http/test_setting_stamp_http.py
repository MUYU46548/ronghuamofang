# -*- coding: utf-8 -*-
"""设定集保存的“盘上指纹”闸（问题总账第 7 条同族）—— 真起 nf_api，临时工作区。

## 这条判据守的是什么

界面整份提交的是它**打开那一刻**的设定集副本。中间只要有人动过盘
（外部 Agent 调 `/setting/save`、CLI、另一个窗口、阶段重跑），
旧实现的“无条件覆盖”就把别人的改动静默抹掉 —— 用户看到的是“我什么都没删，条目没了”。

现在 `GET /setting/current` 附带盘上 `mtime`，界面保存时带回来：
对不上 → **409 拒收，一个字都不写**；不传 → 老行为（脚本/用例照旧）。

覆盖：
  A GET 带 mtime，且等于盘上真实 st_mtime
  B 不传 expected_mtime → 200（老行为不回归）
  C 传对的 mtime → 200，盘上真变了，回传新 mtime
  D 外部改盘后拿旧 mtime 保存 → 409，**盘上仍是别人写的那份**（没被覆盖）
  E 点“刷新”拿到新 mtime 后再存 → 200（不是把人锁死）
  F expected_mtime 不是数字 → 400（不是 500）
  G setting 不是对象 → 400（老护栏不回归）
  H 盘上文件不存在 + 带 mtime → 409（不是 500 / 不是静默新建）
  I 每次成功保存都留了备份（history/ 里有文件）

用法：python tests/http/test_setting_stamp_http.py
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
PORT = 8947
BASE = "http://127.0.0.1:%d" % PORT
GUI_TOKEN = "stampcase-token"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def req(method, path, body=None, timeout=60):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {}
        return e.code, payload


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_setting_stamp_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "materials" / "raw").mkdir(parents=True)
    setting_dir = tmp / "data" / "setting"
    setting_dir.mkdir(parents=True)
    seed = {"characters": [{"name": "青芜", "role": "主角"}],
            "world": [], "plot_fragments": [], "timeline": []}
    (setting_dir / "setting.json").write_text(
        json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp


def read_disk(tmp):
    return json.loads((tmp / "data" / "setting" / "setting.json").read_text(encoding="utf-8"))


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=dict(os.environ, NF_GUI_TOKEN=GUI_TOKEN))
    try:
        for _ in range(60):
            try:
                code, _h = req("GET", "/health")
                if code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            print("  [FAIL] 服务未起来")
            return 1

        setting_path = tmp / "data" / "setting" / "setting.json"

        print("\n=== A. GET /setting/current 带盘上指纹 ===")
        code, d = req("GET", "/setting/current")
        disk_mtime = setting_path.stat().st_mtime
        check("A1 200 且返回 setting", code == 200 and d.get("ok") and d.get("setting"))
        check("A2 返回 mtime 且等于盘上 st_mtime",
              isinstance(d.get("mtime"), (int, float))
              and abs(float(d["mtime"]) - disk_mtime) <= 0.001,
              (d.get("mtime"), disk_mtime))

        print("\n=== B. 不传 expected_mtime = 老行为（脚本 / 旧客户端照旧） ===")
        payload = {"characters": [{"name": "青芜", "role": "主角（改）"}],
                   "world": [], "plot_fragments": [], "timeline": []}
        code, d = req("POST", "/setting/save", {"setting": payload})
        check("B1 200 且 ok", code == 200 and d.get("ok"), (code, d))
        check("B2 盘上真变了", read_disk(tmp)["characters"][0]["role"] == "主角（改）", read_disk(tmp))

        print("\n=== C. 带对的 mtime → 保存成功、回传新指纹 ===")
        code, d = req("GET", "/setting/current")
        stamp = d.get("mtime")
        time.sleep(0.02)
        payload2 = {"characters": [{"name": "青芜", "role": "主角（再改）"}],
                    "world": [], "plot_fragments": [], "timeline": []}
        code, d2 = req("POST", "/setting/save",
                       {"setting": payload2, "expected_mtime": stamp})
        new_mtime = setting_path.stat().st_mtime
        check("C1 200 且 ok", code == 200 and d2.get("ok"), (code, d2))
        check("C2 盘上是这一次的内容", read_disk(tmp)["characters"][0]["role"] == "主角（再改）")
        check("C3 回传的 mtime 就是盘上现在的 mtime",
              isinstance(d2.get("mtime"), (int, float))
              and abs(float(d2["mtime"]) - new_mtime) <= 0.001,
              (d2.get("mtime"), new_mtime))

        print("\n=== D. 外部改盘后拿旧指纹保存 → 拒收，不覆盖别人的改动 ===")
        old_stamp = stamp                       # C 之前那一枚（已经过期）
        external = {"characters": [{"name": "青芜", "role": "外部 Agent 写的"}],
                    "world": [], "plot_fragments": [], "timeline": []}
        time.sleep(0.02)
        setting_path.write_text(json.dumps(external, ensure_ascii=False, indent=2),
                                encoding="utf-8")            # 模拟 CLI / 另一个窗口改盘
        code, d3 = req("POST", "/setting/save",
                       {"setting": payload2, "expected_mtime": old_stamp})
        check("D1 409（不是 200 悄悄覆盖）", code == 409, (code, d3))
        check("D2 说清了是谁动过盘 + 怎么办",
              "已被改过" in str(d3.get("error") or "") and d3.get("hint"), d3)
        check("D3 盘上**仍是外部那份**（一个字都没被写回）",
              read_disk(tmp)["characters"][0]["role"] == "外部 Agent 写的", read_disk(tmp))
        check("D4 409 时回传当前盘上 mtime（前端据此提示刷新）",
              isinstance(d3.get("mtime"), (int, float)), d3.get("mtime"))

        print("\n=== E. 点“刷新”拿到新指纹后再存 → 能存（不把人锁死） ===")
        code, d = req("GET", "/setting/current")
        fresh = d.get("mtime")
        time.sleep(0.02)
        code, d4 = req("POST", "/setting/save",
                       {"setting": payload2, "expected_mtime": fresh})
        check("E1 200 且 ok", code == 200 and d4.get("ok"), (code, d4))
        check("E2 盘上换成本次内容",
              read_disk(tmp)["characters"][0]["role"] == "主角（再改）", read_disk(tmp))

        print("\n=== F/G. 坏输入不被当成 500 ===")
        code, d5 = req("POST", "/setting/save",
                       {"setting": payload2, "expected_mtime": "abc"})
        check("F1 expected_mtime 非数字 → 400", code == 400, (code, d5))
        code, d6 = req("POST", "/setting/save", {"setting": [], "expected_mtime": fresh})
        check("G1 setting 不是对象 → 400（老护栏不回归）", code == 400, (code, d6))

        print("\n=== H. 盘上文件不存在 + 带指纹 → 409（不是静默新建） ===")
        setting_path.unlink()
        code, d7 = req("POST", "/setting/save",
                       {"setting": payload2, "expected_mtime": fresh})
        check("H1 409", code == 409, (code, d7))
        check("H2 没有静默把文件建出来", not setting_path.exists())
        code, d8 = req("GET", "/setting/current")
        check("H3 文件没了时 GET 仍是 404（不是 500）", code == 404, (code, d8))

        print("\n=== I. 成功保存留了备份 ===")
        hist = tmp / "data" / "setting" / "history"
        check("I1 history/ 里至少一份备份",
              hist.exists() and len(list(hist.glob("setting_*.json"))) >= 1,
              list(hist.glob("*.json")) if hist.exists() else "无 history 目录")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
