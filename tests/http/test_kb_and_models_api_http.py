# -*- coding: utf-8 -*-
"""S3/S8 端点 HTTP 契约：/kb/*（未配置 vault）+ /models/switch 白名单。

## 本用例守护什么

**`/kb/search` 与 `/kb/build` 曾长期带着 NameError 存活**：源码直接引用
`get_vault_path()`，但该名字在 `nf_api.py` 中**从未导入** → 端点一旦被调用必崩。
之所以没人发现，是因为**全部 HTTP 契约测试里没有任何 kb 用例**（零覆盖）。
`pyflakes` 首次接入即报出这两行 —— 这也是「把 py_compile 换成 pyflakes」
这一单项改动价值的直接证据。

**`/models/switch` 曾是唯一没有校验的模型写入路径**：连格式校验都没有，
任意模型名可落盘生效，白名单纪律是纸面纪律。

覆盖：
- vault 未配置时 /kb/* 返回 **400 + 可行动提示**（而非 500 NameError）
- vault 已配置时 /kb/build 正常建立索引、/kb/search 能检索
- /models/switch：格式非法 → 400；未登记 → 400 且不写盘；已登记 → 200 且写盘
- /models/switch：strict=false 逃生门放行并回报未校验
- 校验失败时 config/system.yaml **保持不变**（不带副作用）

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起 nf_api。

用法：python tests/http/test_kb_and_models_api_http.py
"""
import io
import json
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
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8927
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def req(method, path, body=None, timeout=30):
    # URL 里的中文须 percent-encode，否则 http.client 以 ascii 编码抛错
    from urllib.parse import quote
    safe_path = quote(path, safe="/?=&")
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    r = urllib.request.Request(BASE + safe_path, data=data, method=method,
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
    except Exception as e:
        return 0, {"error": str(e)}


def build_project(vault=None):
    tmp = Path(tempfile.mkdtemp(prefix="kb_models_api_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "logs").mkdir()

    # 重写 system.yaml：保留白名单（glm-5 / kimi-k3），vault 按需设置
    import re
    syscfg = (tmp / "config" / "system.yaml").read_text(encoding="utf-8")
    # 用 lambda 作 repl，避免 Windows 路径里的 \U 被当成 re 模板转义
    va = (vault or "").replace("\\", "/")
    syscfg = re.sub(r'vault_path:\s*""', lambda m: 'vault_path: "%s"' % va, syscfg)
    (tmp / "config" / "system.yaml").write_text(syscfg, encoding="utf-8")

    if vault:
        vd = Path(vault)
        (vd / "03 设定" / "01 人物").mkdir(parents=True, exist_ok=True)
        (vd / "03 设定" / "01 人物" / "测试角色.md").write_text(
            "---\nname: 测试角色\ntags: [角色]\nlocked: false\n---\n\n"
            "一个用于索引测试的角色词条。\n", encoding="utf-8")
    return tmp


def start_api(tmp):
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            if req("GET", "/health")[0] == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.25)
    return proc


def stop_api(proc):
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def yaml_model_id(tmp, role):
    import re
    txt = (tmp / "config" / "system.yaml").read_text(encoding="utf-8")
    m = re.search(r"%s:\s*\n\s*provider:\s*\S+\s*\n\s*id:\s*(\S+)" % role, txt)
    return m.group(1) if m else None


def main():
    # ---------- A. vault 未配置 ----------
    print("=" * 66)
    print("A. vault 未配置 → /kb/* 应给出可行动 400（而非 500 NameError）")
    print("=" * 66)
    tmp = build_project(vault=None)
    print("临时项目根:", tmp)
    proc = start_api(tmp)
    try:
        code, d = req("GET", "/kb/search?q=测试")
        check("/kb/search 未配置 → 400", code == 400, f"{code} {d}")
        check("/kb/search 提示可行动（提到配置项或功能说明）",
              "vault" in json.dumps(d, ensure_ascii=False).lower(), d)
        check("/kb/search 未抛 NameError（提示不含 'name' is not defined）",
              "not defined" not in json.dumps(d), d)

        code2, d2 = req("GET", "/kb/build")
        check("/kb/build 未配置 → 400", code2 == 400, f"{code2} {d2}")
        check("/kb/build 未抛 NameError",
              "not defined" not in json.dumps(d2), d2)
    finally:
        stop_api(proc)

    # ---------- B. vault 已配置 ----------
    print("\n" + "=" * 66)
    print("B. vault 已配置 → /kb/build + /kb/search 真实可用")
    print("=" * 66)
    vault = tempfile.mkdtemp(prefix="kb_vault_")
    tmp2 = build_project(vault=vault)
    proc2 = start_api(tmp2)
    try:
        code, d = req("GET", "/kb/build")
        check("/kb/build → 200", code == 200, f"{code} {d}")
        check("索引建立并回报词条数", bool(d.get("ok")) and "total_files" in d, d)

        code, d = req("GET", "/kb/search?q=测试角色&top=5")
        check("/kb/search → 200", code == 200, f"{code} {d}")
        check("检索返回结果结构正确（results 为列表）",
              isinstance(d.get("results"), list), d)

        # 索引落盘位置
        check("索引落盘 data/state/kb_index.pkl",
              (tmp2 / "data" / "state" / "kb_index.pkl").exists())
    finally:
        stop_api(proc2)

    # ---------- C. /models/switch 白名单 ----------
    print("\n" + "=" * 66)
    print("C. /models/switch：两级校验回路已接通")
    print("=" * 66)
    tmp3 = build_project(vault=None)
    before = yaml_model_id(tmp3, "default")
    proc3 = start_api(tmp3)
    try:
        check("基线：default 角色初始模型可读", bool(before), before)

        # C1 格式非法
        for bad in ["../evil", "bad name", "x" * 200]:
            code, d = req("POST", "/models/switch", {"role": "default", "model": bad})
            check(f"格式非法被拒: {bad[:24]!r}", code == 400, f"{code} {d}")
        check("格式非法后 config 未被改写",
              yaml_model_id(tmp3, "default") == before,
              yaml_model_id(tmp3, "default"))

        # C2 未登记（第 ② 级）
        code, d = req("POST", "/models/switch",
                      {"role": "default", "model": "totally-unknown-model"})
        check("未登记模型被拒 → 400", code == 400, f"{code} {d}")
        check("拒绝理由说明白名单来源",
              "白名单" in json.dumps(d, ensure_ascii=False), d)
        check("拒绝后 config 未被改写（无副作用）",
              yaml_model_id(tmp3, "default") == before,
              yaml_model_id(tmp3, "default"))

        # C3 已登记 → 放行
        code, d = req("POST", "/models/switch", {"role": "default", "model": "glm-5"})
        check("已登记模型 → 200", code == 200, f"{code} {d}")
        check("回报 whitelist_checked=true", d.get("whitelist_checked") is True, d)
        check("config 已写入 glm-5",
              yaml_model_id(tmp3, "default") == "glm-5",
              yaml_model_id(tmp3, "default"))

        # C4 逃生门
        code, d = req("POST", "/models/switch",
                      {"role": "default", "model": "experimental-x", "strict": False})
        check("strict=false 逃生门 → 200", code == 200, f"{code} {d}")
        check("逃生门回报 whitelist_checked=false（不静默）",
              d.get("whitelist_checked") is False, d)
        check("逃生门消息里标注「未做白名单校验」",
              "未做白名单校验" in str(d.get("message", "")), d)

        # C5 未知角色仍被护栏拦住
        code, d = req("POST", "/models/switch",
                      {"role": "nonexistent-role", "model": "glm-5"})
        check("未知角色 → 400", code == 400, f"{code} {d}")

        # C6 手动添加后被准入（回路的自洽性）
        code, d = req("POST", "/models/add", {"name": "user-added-model"})
        check("/models/add 手动添加 → 200", code == 200, f"{code} {d}")
        code, d = req("POST", "/models/switch",
                      {"role": "default", "model": "user-added-model"})
        check("手动添加后即被准入（用户显式动作=确认）", code == 200, f"{code} {d}")
    finally:
        stop_api(proc3)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项:", FAIL)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
