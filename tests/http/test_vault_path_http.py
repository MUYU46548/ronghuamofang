# -*- coding: utf-8 -*-
"""知识库路径的 GUI 入口（2026-10-31）。

## 为什么有这个测试

设置页此前只有「知识库扫描」，**没有填库路径的地方** —— 用户第一次接库只能
手改 `config/system.yaml` + `config/system.local.yaml`，还得先知道 local 覆盖优先。
排行榜第一条的「库路径没生效」正好是这个缺口的下游症状，所以这次连根补上。

这里钉四件事：

- V1 未配置时读得出"没配置"，不误报成"库是空的"
- V2 五类非法路径全部 400（相对 / 不存在 / 是文件 / 盘根 / 工作区内）
- V3 合法路径写进 `system.local.yaml`（不进 Git），且**原有注释不丢**
- V4 **改完立刻生效**：换一个库，扫描面下一次就跟着换
      （这是本仓第 N 次踩的坑：模块级快照让界面"保存成功"但实际读旧值。
        反证：把 `_get_vault_path` 改回首次固化 → V4 立刻红）

用法：python tests/http/test_vault_path_http.py
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
PORT = 8956
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=60):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    r = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Mofang-Source": "gui"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                    # noqa: BLE001
            payload = {}
        return e.code, payload


def make_vault(base, subdirs):
    """造一个最小库：base/子目录/一篇.md。第一个库额外带 .obsidian。"""
    base.mkdir(parents=True, exist_ok=True)
    for name, title in subdirs:
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        (d / (title + ".md")).write_text(
            "---\nname: %s\n---\n正文。\n" % title, encoding="utf-8")
    return base


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_vaultpath_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    for d in ("data/state", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "库路径验收"\n  chapters: 1\n', encoding="utf-8")
    # 基线把 vault_path 清空（仓库里的 system.yaml 可能带着开发机的路径）
    import re
    sy = tmp / "config" / "system.yaml"
    txt = sy.read_text(encoding="utf-8")
    txt = re.sub(r'(?m)^(\s*vault_path:\s*).*$', r'\1""', txt, count=1)
    sy.write_text(txt, encoding="utf-8")
    # local 里带一段注释 + 一个已有键：写 vault_path 时**注释必须留住**
    (tmp / "config" / "system.local.yaml").write_text(
        "# 本地配置覆盖（不进 Git）\n"
        "# 下面这条排除项是我自己加的\n"
        "obsidian:\n"
        "  skip_dirs:\n"
        "    - 参考作品\n", encoding="utf-8")
    return tmp


def main():
    tmp = build_project()
    vault_a = make_vault(tmp.parent / (tmp.name + "_vaultA"),
                         [("设定", "甲"), ("参考作品", "乙")])
    (vault_a / ".obsidian").mkdir(exist_ok=True)
    vault_b = make_vault(tmp.parent / (tmp.name + "_vaultB"),
                         [("另一个库的目录", "丙")])
    local_yaml = tmp / "config" / "system.local.yaml"
    proc = None
    try:
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

        print("\n【V1】未配置时读得出「没配置」")
        code, d = req("GET", "/config/vault")
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        check("configured=false（不把\"没配置\"说成\"库是空的\"）",
              d.get("configured") is False, d.get("configured"))
        check("source=none", d.get("source") == "none", d.get("source"))
        check("给出落点字段说明（用户知道它写在哪）",
              "system.local.yaml" in (d.get("field") or ""), d.get("field"))

        print("\n【V2】五类非法路径全拒")
        cases = [
            ("相对路径", "library/notes"),
            ("不存在", str(tmp.parent / "不存在的库_xyz")),
            ("是文件", str(tmp / "config" / "project.yaml")),
            ("盘根", str(Path(str(vault_a.anchor or "C:\\")))),
            ("工作区内", str(tmp / "materials")),
        ]
        for label, bad in cases:
            code, d = req("POST", "/config/vault", {"path": bad})
            check("拒绝%s" % label, code == 400, (code, d.get("error")))
        code, d = req("POST", "/config/vault", {})
        check("空 path 且没 clear → 400", code == 400, (code, d.get("error")))

        print("\n【V3】合法路径落 local，且注释不丢")
        before = local_yaml.read_text(encoding="utf-8")
        code, d = req("POST", "/config/vault", {"path": str(vault_a)})
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        check("回执带 md 篇数（2 篇）", d.get("md_count") == 2, d.get("md_count"))
        check("认出这是 Obsidian 库", d.get("looks_like_obsidian") is True, d)
        check("source=local", d.get("source") == "local", d.get("source"))
        after = local_yaml.read_text(encoding="utf-8")
        check("写进 local 的 vault_path 是正斜杠绝对路径",
              "vault_path:" in after and str(vault_a).replace("\\", "/") in after,
              after[-160:])
        check("原有注释没被吃掉（config_io 定向改写，不是整份 dump）",
              "# 下面这条排除项是我自己加的" in after, after[:120])
        check("原有 skip_dirs 还在（写一个键不碰别的键）",
              "参考作品" in after, after[-160:])
        check("没动 system.yaml（不进 Git 的那份才该改）",
              str(vault_a).replace("\\", "/") not in
              (tmp / "config" / "system.yaml").read_text(encoding="utf-8"))

        print("\n【V4】改完立刻生效（不等重启）")
        code, sc = req("GET", "/config/vault_scan")
        names = [x.get("name") for x in (sc.get("top_dirs") or [])]
        check("扫描面用的是新库（设定 / 参考作品）",
              "设定" in names and "参考作品" in names, names)
        code, d = req("POST", "/config/vault", {"path": str(vault_b)})
        check("换库 200", code == 200 and d.get("ok") is True, d)
        code, sc2 = req("GET", "/config/vault_scan")
        names2 = [x.get("name") for x in (sc2.get("top_dirs") or [])]
        check("**下一次扫描立刻换成库 B**（模块级快照会让这条红）",
              names2 == ["另一个库的目录"], names2)
        check("库 A 的目录已不在扫描面", "设定" not in names2, names2)

        print("\n【V5】清空")
        code, d = req("POST", "/config/vault", {"clear": True})
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        code, d2 = req("GET", "/config/vault")
        check("configured=false", d2.get("configured") is False, d2)
        check("库 B 的笔记一篇没少（清空配置 ≠ 删文件）",
              (vault_b / "另一个库的目录" / "丙.md").is_file())
        check("清空后 local 里的注释还在",
              "# 本地配置覆盖（不进 Git）" in local_yaml.read_text(encoding="utf-8"))

        print("\n【V6】路由登记")
        sys.path.insert(0, str(ROOT / "scripts"))
        from nf_api_domains import project as PR
        routes = {(m, p) for m, p, _ in PR.ROUTES}
        check("GET /config/vault 在域路由表里", ("GET", "/config/vault") in routes)
        check("POST /config/vault 在域路由表里", ("POST", "/config/vault") in routes)
        from utils import agent_guard as AG
        check("库路径**不**在 Agent 禁用名单（配置类，Agent 可代配）",
              "/config/vault" not in AG.FORBIDDEN_IN_AGENT_MODE)
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:                                # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(vault_a, ignore_errors=True)
        shutil.rmtree(vault_b, ignore_errors=True)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
