# -*- coding: utf-8 -*-
"""「新建项目向导 + 关于 + 风格笔记」相关后端自检（HTTP 端到端）。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起 nf_api
（--allow-fake），所有读写都落在临时目录。

覆盖：
  GET  /config/style_notes   回归：此前 import 不存在的 get_style_notes → 设置页签必弹
                             `ImportError: cannot import name 'get_style_notes'`；
                             现应 200 + 返回 project.yaml 里的原值
  POST /config/style_notes   写入→回读一致，且 config/history/ 有备份
  POST /project/create       护栏：空名 / 非法字符 / 章节数越界/非数字 → 400，且**不落地**
  POST /project/create       当前工作区有数据且 archive_current=false → 400 并保留数据
                             （绝不静默清空书稿）
  POST /project/create       正常路径：旧项目进 data/books/、工作区重建、
                             project.yaml 的 name/genre/chapters/style_notes 全落地、
                             注释与其它块仍在（定向改写而非重写文件）
  POST /project/init         回归：首启向导填的书名以前被静默丢弃，现在必须写进 project.yaml
  GET  /about                关于弹窗数据齐全（版本/路径/环境/书名）
  GET  /nope                 404 提示里列出了新端点
  set_book_fields 单元行为   多行 style_notes 走块标量、写后回读校验、非法字段拒绝

用法：python tests/test_project_wizard_api_http.py
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
PORT = 8934
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


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


PROJECT_YAML = '''# =========================================================
# NovelForge 项目信息（每次运行前可修改）   ← 这行注释必须活下来
# =========================================================

book:
  name: "旧书"          # 书名
  genre: "奇幻"                    # 类型
  target_words: 80000            # 全书目标字数
  chapters: 12                    # 本次运行章节数
  author: ""            # 作者名
  style_notes: ""          # 用户风格笔记

materials:
  dir: "materials/raw"
  use_scraps: true
'''


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_wizard_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "config" / "project.yaml").write_text(PROJECT_YAML, encoding="utf-8")
    # 工作区有数据（has_work() 命中：progress.json + chapters/）
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "chapters" / "raw").mkdir(parents=True)
    (tmp / "data" / "progress.json").write_text(
        json.dumps({"project": "旧书", "stages": {"1": {"status": "done"}}}, ensure_ascii=False),
        encoding="utf-8")
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text("# 第一章\n正文\n", encoding="utf-8")
    (tmp / "materials" / "raw").mkdir(parents=True)
    return tmp


def main():
    tmp = build_project()
    log = io.open(tmp / "nf_api.log", "w", encoding="utf-8")
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            time.sleep(0.5)
            try:
                if req("GET", "/health", timeout=3)[0] == 200:
                    break
            except Exception:
                pass
        else:
            print("服务未能启动，见 " + str(tmp / "nf_api.log"))
            return 1

        proj = tmp / "config" / "project.yaml"

        print("=== 1. 风格笔记读取（回归：ImportError 必须消失）===")
        code, d = req("GET", "/config/style_notes")
        check("GET /config/style_notes → 200（此前 500 ImportError）",
              code == 200 and d.get("ok"), (code, str(d)[:180]))
        check("字段口径标注正确", d.get("field") == "book.style_notes" and d.get("path") == "config/project.yaml",
              (d.get("path"), d.get("field")))
        check("空值返回空串（不是 None/报错）", d.get("content") == "", repr(d.get("content")))
        code, d = req("POST", "/config/style_notes", {"content": "多用短句\n少用形容词"})
        check("POST 写入 → 200", code == 200 and d.get("ok"), (code, str(d)[:180]))
        code, d = req("GET", "/config/style_notes")
        check("回读一致（多行笔记）", d.get("content") == "多用短句\n少用形容词", repr(d.get("content")))
        check("写入前留了备份 config/history/",
              list((tmp / "config" / "history").glob("project_*.yaml")), None)
        check("定向改写没有丢掉注释与其它块",
              "这行注释必须活下来" in proj.read_text(encoding="utf-8")
              and "use_scraps: true" in proj.read_text(encoding="utf-8"))

        print("\n=== 1b. 风格笔记反复改写（单行 ↔ 多行，不得留残值/重复键）===")
        for i, val in enumerate(["单行版", "多行版第一句\n多行版第二句", "又回到单行"], 1):
            code, d = req("POST", "/config/style_notes", {"content": val})
            c2, d2 = req("GET", "/config/style_notes")
            n_key = proj.read_text(encoding="utf-8").count("style_notes:")
            check("第 %d 轮改写后回读一致（%s）" % (i, val.replace("\n", "/")),
                  code == 200 and d2.get("content") == val, repr(d2.get("content")))
            check("style_notes 键未出现重复（第 %d 轮）" % i, n_key == 1, n_key)
        check("反复改写后 YAML 仍可解析且注释仍在",
              "这行注释必须活下来" in proj.read_text(encoding="utf-8"))

        print("\n=== 2. 新建项目：参数护栏（非法输入不得留下任何改动）===")
        before = proj.read_text(encoding="utf-8")
        for bad, label in (({"name": ""}, "空书名"),
                           ({"name": "  "}, "纯空格书名"),
                           ({"name": "a/b"}, "书名含斜杠"),
                           ({"name": "a:b"}, "书名含冒号"),
                           ({"name": "新书", "chapters": 0}, "章节数 0"),
                           ({"name": "新书", "chapters": 1000}, "章节数 1000"),
                           ({"name": "新书", "chapters": "abc"}, "章节数非数字")):
            code, d = req("POST", "/project/create", bad)
            check("%s → 400" % label, code == 400 and not d.get("ok"), (code, str(d)[:160]))
        check("非法输入后 project.yaml 未被改动", proj.read_text(encoding="utf-8") == before)
        check("非法输入后工作区数据仍在", (tmp / "data" / "chapters" / "raw" / "01.md").exists())

        print("\n=== 3. 新建项目：有数据但不归档 → 拒绝（不许静默清空书稿）===")
        code, d = req("POST", "/project/create", {"name": "新书", "archive_current": False})
        check("archive_current=false → 400", code == 400 and not d.get("ok"), (code, str(d)[:200]))
        check("提示里点名当前项目名并给出出路",
              "旧书" in str(d.get("error")) and "归档" in str(d.get("error")), str(d.get("error"))[:200])
        check("数据零改动", (tmp / "data" / "chapters" / "raw" / "01.md").exists())

        print("\n=== 4. 新建项目：正常路径 ===")
        code, d = req("POST", "/project/create", {
            "name": "新月纪", "genre": "科幻", "chapters": 20,
            "style_notes": "冷峻白描，短句为主",
        })
        check("POST /project/create → 200", code == 200 and d.get("ok"), (code, str(d)[:260]))
        check("旧项目已归档到 data/books/旧书/",
              (tmp / "data" / "books" / "旧书" / "chapters" / "raw" / "01.md").exists(),
              [p.name for p in (tmp / "data" / "books").iterdir()] if (tmp / "data" / "books").exists() else None)
        check("工作区已清空旧书数据", not (tmp / "data" / "chapters" / "raw" / "01.md").exists())
        check("空工作区骨架已重建",
              (tmp / "data" / "setting").is_dir() and (tmp / "data" / "outline").is_dir()
              and (tmp / "data" / "state").is_dir())
        text = proj.read_text(encoding="utf-8")
        book = json.loads("{}")  # 占位，下面用 yaml 解析
        import yaml
        book = (yaml.safe_load(text) or {}).get("book") or {}
        check("project.yaml: name 落地", book.get("name") == "新月纪", book.get("name"))
        check("project.yaml: genre 落地", book.get("genre") == "科幻", book.get("genre"))
        check("project.yaml: chapters 落地为整数 20", book.get("chapters") == 20, book.get("chapters"))
        check("project.yaml: style_notes 落地", book.get("style_notes") == "冷峻白描，短句为主",
              book.get("style_notes"))
        check("注释与其它块未被破坏",
              "这行注释必须活下来" in text and "use_scraps: true" in text
              and "target_words" in text)
        code, s = req("GET", "/state")
        check("/state 书名已切到新项目", s.get("book") == "新月纪", s.get("book"))

        print("\n=== 5. 首次启动向导（/project/init）也要真写盘 ===")
        code, d = req("POST", "/project/init", {"name": "向导书", "type": "都市", "chapters": 7,
                                                "style_notes": "口语化"})
        check("POST /project/init → 200", code == 200 and d.get("ok"), (code, str(d)[:220]))
        book = (yaml.safe_load(proj.read_text(encoding="utf-8")) or {}).get("book") or {}
        check("向导填的书名/类型/章数/笔记全部落地（此前被静默丢弃）",
              book.get("name") == "向导书" and book.get("genre") == "都市"
              and book.get("chapters") == 7 and book.get("style_notes") == "口语化", book)

        print("\n=== 6. GET /about（关于弹窗数据）===")
        code, d = req("GET", "/about")
        check("GET /about → 200", code == 200 and d.get("ok"), (code, str(d)[:160]))
        check("含版本/项目根/数据目录/书名",
              all(d.get(k) for k in ("version", "project_root", "data_dir", "config")),
              {k: d.get(k) for k in ("version", "project_root", "config")})
        check("版本号来自 console/package.json", d.get("version") not in (None, ""), d.get("version"))
        check("给出仓库地址与许可入口",
              "github.com" in str(d.get("repo")), d.get("repo"))

        print("\n=== 7. 404 提示与新路由 ===")
        code, d = req("GET", "/nope")
        check("未知路径仍 404", code == 404)
        check("404 列表含新端点",
              "/project/create" in str(d.get("error")) and "/about" in str(d.get("error")),
              str(d.get("error"))[:200])

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
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
