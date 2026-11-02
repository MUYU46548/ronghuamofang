# -*- coding: utf-8 -*-
"""配置写入的三条路径：改值可以，**不许把注释带走**（2026-10-31）。

## 起因

用户让重做\u300c引擎与模型\u300d界面时，先把写入侧查了一遍，发现：

| 端点 | 旧写法 | 后果 |
|---|---|---|
| `POST /config/provider` | `yaml.dump(整份 cfg)` 覆盖写 | system.yaml 里 172 行注释**全部消失** |
| `POST /models/switch` | `yaml.safe_dump(整份 cfg)` | 同上 |
| `POST /config/engine` | **不存在** | 界面上那行\u300c引擎与模型\u300d只是只读文字，用户只能手改 YAML |

那些注释不是装饰：止烧阈值的口径、`model.*` 只在 direct 下生效、为什么禁用某些
思考模型、provider 的注意事项，全写在里面。用户点一次下拉 = 悄悄删掉说明书，
而且**没有任何提示**（文件照样能解析、程序照样能跑）。

## 钉什么

- C1 切引擎：值真落地、注释一行不少、文件仍可解析
- C2 非法引擎：400 且**文件一个字节都不变**（拒绝要彻底，不能先写后校验）
- C3 切 provider：注释一行不少
- C4 切模型：注释一行不少
- C5 顶层键写入不得带前导空格（写坏 YAML 的那次真实事故）

反证（会变红）：把任一处写回 `yaml.dump(cfg)` → 注释计数从 172 掉到 0。

用法：python tests/http/test_config_writes_http.py
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
PORT = 8951
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def req(method, path, body=None, timeout=60):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        "X-Mofang-Source": "gui"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                    # noqa: BLE001
            payload = {}
        return e.code, payload


def comments(text):
    """注释行数（含行内注释）。用\u300c行数\u300d而不是\u300c字符数\u300d：一目了然。"""
    return sum(1 for ln in text.splitlines() if "#" in ln)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="nf_cw_"))
    (tmp / "config").mkdir()
    (tmp / "data" / "state").mkdir(parents=True)
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    shutil.copy2(ROOT / "config" / "project.yaml", tmp / "config" / "project.yaml")
    cfg = tmp / "config" / "system.yaml"
    base_text = cfg.read_text(encoding="utf-8")
    base_comments = comments(base_text)
    proc = None
    try:
        check("夹具自证：system.yaml 本身就带注释", base_comments > 50, base_comments)
        env = dict(os.environ)
        env["NF_ROOT"] = str(tmp)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [PY, str(ROOT / "scripts" / "nf_api.py"), "--port", str(PORT),
             "--root", str(tmp)],
            cwd=str(tmp), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        ready = False
        for _ in range(80):
            try:
                if req("GET", "/health")[0] == 200:
                    ready = True
                    break
            except Exception:                                # noqa: BLE001
                time.sleep(0.5)
        check("nf_api 起来了", ready)
        if not ready:
            return 1

        print("\n【C1】切引擎：值落地 + 注释一行不少")
        cur_engine = req("GET", "/models")[1].get("engine")
        target = "direct" if cur_engine != "direct" else "hermes"
        code, d = req("POST", "/config/engine", {"engine": target})
        check("200 且 ok=true", code == 200 and d.get("ok") is True, d)
        txt = cfg.read_text(encoding="utf-8")
        check("engine 真的写成了 %s" % target,
              txt.splitlines()[0].strip() == "engine: " + target, txt.splitlines()[:2])
        check("注释行数没变（%d → %d）" % (base_comments, comments(txt)),
              comments(txt) == base_comments, comments(txt))
        check("GET /models 回报的 engine 跟着变",
              req("GET", "/models")[1].get("engine") == target)

        print("\n【C5】顶层键不得带前导空格（写坏 YAML 的那次事故）")
        first = txt.splitlines()[0]
        check("第一行没有前导空格", not first.startswith(" "), repr(first))
        code, d = req("GET", "/state")
        check("整份配置仍可被读（/state 200）", code == 200, code)

        print("\n【C2】非法引擎：400，且文件一个字节都不变")
        before = cfg.read_bytes()
        for bad in ("openai", "", "HERMES2", None):
            code, d = req("POST", "/config/engine", {"engine": bad})
            check("拒绝 %r" % (bad,), code == 400, (code, d.get("error")))
        check("被拒绝的请求没有改动文件", cfg.read_bytes() == before)

        print("\n【C3】切 provider：注释一行不少")
        # 必须拿**真实存在的** provider 名：用错名字会走 400 分支，那条路径根本不写文件
        # —— 断言照样绿，但什么都没验到（本仓叫它“假绿”）。
        provs = list((req("GET", "/models/available")[1].get("providers") or {}).keys())
        check("夹具自证：拿到了至少一个 provider（否则 C3 是空转）", bool(provs), provs)
        if provs:
            code, d = req("POST", "/config/provider",
                          {"role": "writer", "provider": provs[0]})
            check("200 且写入了真实 provider", code == 200 and d.get("ok") is True,
                  (code, d))
            txt = cfg.read_text(encoding="utf-8")
            check("provider 路径也没吃掉注释", comments(txt) == base_comments,
                  "%d → %d" % (base_comments, comments(txt)))
            check("provider 真的落到 writer 名下",
                  "provider: " + provs[0] in txt, provs[0])
        else:
            print("  [SKIP] 没有可用 provider，跳过 provider 写入")

        print("\n【C4】切模型：注释一行不少")
        models = req("GET", "/models")[1].get("model") or {}
        mid = ((models.get("writer") or {}).get("id") or "").strip()
        if mid:
            code, d = req("POST", "/models/switch", {"role": "writer", "model": mid})
            check("切到当前同名模型也返回 200", code == 200, (code, d))
            txt = cfg.read_text(encoding="utf-8")
            check("模型路径同样保住注释", comments(txt) == base_comments,
                  "%d → %d" % (base_comments, comments(txt)))
            check("文件仍可解析（回读 engine 不报错）", req("GET", "/models")[0] == 200)
        else:
            print("  [SKIP] config 里没有 writer 模型名，跳过模型写入")
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
