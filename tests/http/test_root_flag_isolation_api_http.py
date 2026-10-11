# -*- coding: utf-8 -*-
"""`--root` 隔离反证：脚本在仓库 A、`--root` 指向书档 B 时，配置必须读 B。

## 为什么需要它
`utils.project_config.PROJECT_YAML`、`utils.config_io.ROOT` 这些常量在**模块加载时**
按「脚本所在位置」固化，而 `nf_api._set_root()` 原先只刷新了 `nf_api` 自己的
GLOBAL/HISTORY_DIR 与 `switch_book` 三个常量 —— **漏刷配置族**。后果是静默的：

    脚本在仓库 A，--root 指向书档 B
    → GET /config/* 读 A 的 project.yaml，GET /state 读 B 的数据
    → 返回 200、内容却是另一个项目的（与 2026-09-20 / 10-06 修过的同类缺陷同源）

2026-10-11 实测踩到：README 截图用 `--root` 起服务，页面把**真实书名**带了出去
（UI 显示 A 的书名，而阶段状态是 B 的）。

## 为什么只测 GET
POST 会真的写 project.yaml —— **修复前**它写的是真实仓库（污染用户配置），
所以本用例只读。写入路径的跟随性由 `tests/unit/test_root_path_discipline.py`
的反证节覆盖（断言 `_set_root()` 后 `project_config.PROJECT_YAML` 指向新根），
`set_book_fields` 全程只读这一个常量。

用法：python tests/http/test_root_flag_isolation_api_http.py
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
PY = sys.executable
PORT = 8936
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []
ISOLATED_BOOK = "隔离测试书"
ISOLATED_NOTES = "只写短句"          # 与真仓库不同，一眼能分辨读的是谁
ISOLATED_TPL = "templates/隔离模板.dotx"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


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


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "MISSING"


def build_book_b():
    """书档 B：独立配置 + 独立模板，值全都与真仓库不同。"""
    tmp = Path(tempfile.mkdtemp(prefix="nf_rootflag_"))
    (tmp / "config").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    # system.yaml 必须带上：/state 的 build_state 要读它，缺了直接 500（实测）
    shutil.copy2(ROOT / "config" / "system.yaml", tmp / "config" / "system.yaml")
    # templates 只放**一个隔离模板**：清单若回 A 的模板，一眼就能看出来
    (tmp / "templates").mkdir()
    (tmp / "templates" / "隔离模板.dotx").write_bytes(b"PK\x03\x04 fixture")
    (tmp / "config" / "project.yaml").write_text(
        "# 隔离测试书档（B）—— 这行注释只存在于 B\n"
        "book:\n"
        '  name: "%s"\n' % ISOLATED_BOOK +
        '  style_notes: "%s"\n' % ISOLATED_NOTES +
        '  word_template: "%s"\n' % ISOLATED_TPL +
        "  chapters: 3\n"
        "materials:\n"
        '  dir: "materials/raw"\n'
        "  use_scraps: false\n", encoding="utf-8")
    (tmp / "data" / "state" / "progress.json").write_text(
        json.dumps({"project": ISOLATED_BOOK, "version": 3, "current_stage": 5,
                    "stages": {str(n): {"status": "pending"} for n in range(1, 8)}},
                   ensure_ascii=False), encoding="utf-8")
    return tmp


def main():
    # NF_ROOT 会盖过 --root（_resolve_root 的优先级），必须先剥掉
    os.environ.pop("NF_ROOT", None)
    real_project = ROOT / "config" / "project.yaml"
    real_before = sha(real_project)
    book_b = build_book_b()
    log = io.open(book_b / "nf_api.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(PY), str(ROOT / "scripts" / "nf_api.py"), "--port", str(PORT),
         "--allow-fake", "--root", str(book_b)],
        cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
        env={k: v for k, v in os.environ.items() if k != "NF_ROOT"})
    try:
        for _ in range(60):
            time.sleep(0.5)
            try:
                if req("GET", "/health", timeout=3)[0] == 200:
                    break
            except Exception:
                pass
        else:
            print("服务未能启动，见", book_b / "nf_api.log")
            return 1

        print("=== 1. /state 读的是 B（这条本来就是对的，作对照）===")
        code, d = req("GET", "/state")
        check("GET /state → 200", code == 200, code)
        check("阶段状态来自书档 B", d.get("book") == ISOLATED_BOOK, d.get("book"))

        print("\n=== 2. /config/word_template 必须读 B（修复前读 A → 红）===")
        code, d = req("GET", "/config/word_template")
        check("GET /config/word_template → 200", code == 200 and d.get("ok"),
              (code, str(d)[:160]))
        check("word_template 取自书档 B",
              d.get("current") == ISOLATED_TPL, d.get("current"))
        check("清单扫的是 B 的 templates/（含隔离模板）",
              any(f["name"] == "隔离模板.dotx" for f in d.get("files", [])),
              [f["name"] for f in d.get("files", [])])

        print("\n=== 3. /config/style_notes 必须读 B（同族 sibling）===")
        code, d = req("GET", "/config/style_notes")
        check("GET /config/style_notes → 200", code == 200 and d.get("ok"),
              (code, str(d)[:160]))
        check("style_notes 取自书档 B", d.get("content") == ISOLATED_NOTES,
              d.get("content"))

        print("\n=== 4. 读 B 不得碰 A（真仓库 project.yaml 逐字未改）===")
        check("真仓库 config/project.yaml 哈希未变", sha(real_project) == real_before,
              (real_before[:12], sha(real_project)[:12]))
        check("B 的配置里那行注释仍在（证明读的是 B 而非 A）",
              "这行注释只存在于 B" in (book_b / "config" / "project.yaml")
              .read_text(encoding="utf-8"))

        print("\n=== 5. 404 提示仍在（服务确实在跑 --root 实例）===")
        code, d = req("GET", "/nope")
        check("未知路径 404", code == 404, code)

        print("=" * 62)
        print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
        if FAIL:
            print("失败项: " + "、".join(FAIL))
        print("=" * 62)
        return 1 if FAIL else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log.close()
        after = sha(real_project)
        if after != real_before:
            print("!! 警告：真仓库 config/project.yaml 在测试期间被改动了（应为零副作用）")
        shutil.rmtree(book_b, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
