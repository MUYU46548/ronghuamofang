# -*- coding: utf-8 -*-
"""提示词\u300c出厂保底\u300d与**读写同源**（2026-10-31）。

## 用户提的问题

\u300c提示词模板是否像测试用书一样有一键还原默认值的功能？如果被用户改坏了却不能
还原默认提示词，就失去了最后的保底。\u300d

查下来是两件事，都不轻：

| # | 现象 | 根因 |
|---|---|---|
| ① | 只有 history 回滚，没有出厂保底 | history 是**用户改动记录**，救不了三种事故：滚动淘汰（只留 50 份）/ 外部编辑器手改（不留痕）/ 升级覆盖 |
| ② | 界面保存了却不生效 | GUI 端点写 `ROOT/prompts`（打包态 = resources/payload），流水线 `load_template` 读 **cwd 相对** `prompts/`（= workspace）—— 一个功能读一套、写另一套 |

② 比 ① 更严重：它让\u300c还原出厂\u300d本身也没意义（还原到 payload，运行还读 workspace）。

## 钉什么

- P1 `/prompts/factory` 盘点：有出厂副本 / 与当前是否一致
- P2 还原后内容 == 出厂副本
- P3 还原前那份（改坏的）**进 history** —— 否则连"改坏了什么"都无从对照
- P4 没有出厂副本时**明说**，不假装成功
- P5 `--root` 下 `/prompts/list` 列的是**该项目**的模板（反证：改回 `ROOT/prompts`
     写死，这条会在临时 root 里列出仓库的模板 → 红）
- P6 白名单外（`../`、非 stage 前缀）一律拒绝

用法：python tests/http/test_prompt_factory_http.py
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
PASS, FAIL = [], []

CUR = "# 我改过的模板\n这是用户版本。\n第二行。\n"
FAC = "# 出厂版\n出厂正文。\n"

MIN_YAML = """book:
  name: "提示词保底验收"
  chapters: 3
"""


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


def build_project():
    """复刻**打包态结构**：代码区（payload）与工作区（workspace）分开。

    ```
    tmp/code/scripts/…        ← 代码（--root 指向别处时，旧实现会把 prompts 算在这里）
    tmp/code/prompts/…        ← 代码区模板（8 个"出厂"模板，只为制造差异）
    tmp/ws/config|data|prompts ← 工作区（--root 指向这里，cwd 也是这里）
    tmp/ws/prompts（2 个）     ← 本项目模板
    ```
    这个分离是必须的：若代码与工作区同在 tmp，新旧实现对 `ROOT/prompts` 与
    `cwd/prompts` 会得到同一个目录，P5 就成了**假绿**（我曾这样验过一次，没红）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="nf_pf_"))
    code = tmp / "code"
    ws = tmp / "ws"
    (code).mkdir()
    shutil.copytree(ROOT / "scripts", code / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # 代码区模板（模拟 payload 里的出厂 prompts）：故意与工作区不同
    (code / "prompts" / "factory").mkdir(parents=True)
    for n in ("stage1_materials.md", "stage2_global_outline.md", "stage3_chapter_outline.md",
              "stage4_writing.md", "stage4_refine.md", "stage5_check.md",
              "stage6_polish.md", "stage7_convert.md"):
        (code / "prompts" / n).write_text("# 代码区出厂模板 %s\n" % n, encoding="utf-8")

    (ws / "config").mkdir(parents=True)
    for n in ("system.yaml",):
        shutil.copy2(ROOT / "config" / n, ws / "config" / n)
    (ws / "config" / "project.yaml").write_text(MIN_YAML, encoding="utf-8")
    for d in ("data/state", "materials/raw"):
        (ws / d).mkdir(parents=True, exist_ok=True)
    # 工作区模板（本项目自己的 prompts/，只有两个）
    p = ws / "prompts"
    (p / "factory").mkdir(parents=True)
    (p / "stage4_writing.md").write_text(CUR, encoding="utf-8")
    (p / "factory" / "stage4_writing.md").write_text(FAC, encoding="utf-8")
    (p / "stage6_polish.md").write_text("# 无出厂副本的模板\n", encoding="utf-8")
    return tmp, code / "scripts" / "nf_api.py", ws


def main():
    tmp, api_script, ws = build_project()
    proc = None
    try:
        env = dict(os.environ)
        env["NF_ROOT"] = str(ws)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [PY, str(api_script), "--port", str(PORT), "--root", str(ws)],
            cwd=str(ws), env=env,
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

        print("\n【P5】读写同源：--root 下 /prompts/list 列的是**这个项目**的模板")
        code, d = req("GET", "/prompts/list")
        names = [i["name"] for i in d.get("items", [])]
        check("200 且模板来自该项目 prompts/（2 个）", code == 200 and len(names) == 2,
              names)
        check("列出的正是本项目那两个（不是代码区那 16 个）",
              set(names) == {"stage4_writing.md", "stage6_polish.md"}, names)

        print("\n【P1】/prompts/factory 盘点：保底在不在、与当前是否一致")
        code, d = req("GET", "/prompts/factory")
        items = {i["name"]: i for i in (d.get("items") or [])}
        check("200 且两个模板都在盘点里", code == 200 and len(items) == 2, items)
        check("stage4_writing 有出厂副本且标记为不一致",
              items.get("stage4_writing.md", {}).get("has_factory") is True
              and items["stage4_writing.md"]["diff"]["identical"] is False, items)
        check("stage6_polish 被明确标成\u300c没有出厂副本\u300d（不假装有）",
              items.get("stage6_polish.md", {}).get("has_factory") is False
              and "stage6_polish.md" in (d.get("missing") or []), d.get("missing"))

        print("\n【P2/P3】还原出厂：内容回到出厂版，改坏的版本进 history")
        code, d = req("POST", "/prompts/factory_restore",
                      {"name": "stage4_writing.md"})
        check("200 且 ok=true", code == 200 and d.get("ok") is True, d)
        after = (ws / "prompts" / "stage4_writing.md").read_text(encoding="utf-8")
        check("磁盘内容 == 出厂副本（不是\u300c看起来成功\u300d）", after == FAC, repr(after))
        hist = list((ws / "prompts" / "history").glob("stage4_writing_*.md")) \
            if (ws / "prompts" / "history").is_dir() else []
        check("还原前那份进了 history（否则无从对照改坏了什么）", len(hist) == 1, hist)
        if hist:
            check("history 里存的正是被覆盖的用户版本",
                  hist[0].read_text(encoding="utf-8") == CUR,
                  hist[0].read_text(encoding="utf-8")[:80])
        code, d2 = req("GET", "/prompts/factory")
        it = {i["name"]: i for i in (d2.get("items") or [])}.get("stage4_writing.md")
        check("还原后盘点显示与出厂一致", (it or {}).get("diff", {}).get("identical") is True,
              it)

        print("\n【P4】没有出厂副本 → 明说，不假装成功")
        code, d = req("POST", "/prompts/factory_restore", {"name": "stage6_polish.md"})
        check("非 200 且带可读原因", code != 200 and "出厂副本" in str(d.get("error")), d)
        check("提示里说清\u300c这只说明随包版本缺这一份\u300d", "随包" in str(d.get("hint")), d)

        print("\n【P6】白名单外一律拒绝")
        for bad in ("../system.yaml", "stage1_../../x.md", "not_a_stage.md", ""):
            code, d = req("POST", "/prompts/factory_restore", {"name": bad})
            check("拒绝 %r" % bad, code >= 400, (code, d))
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
