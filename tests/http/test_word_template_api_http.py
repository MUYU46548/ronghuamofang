# -*- coding: utf-8 -*-
"""Word 成品模板端点（件五 GUI 入口）HTTP 自检。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ templates/ 复制到临时目录当 ROOT
起 nf_api（--allow-fake），所有读写都落在临时目录。

覆盖：
  GET  /config/word_template   清单（templates/ 下 .dotx/.docx）+ 当前值 + 出厂默认值
                               当前值指向的文件不在 templates/ → current_exists=False（不静默）
  POST /config/word_template   选择 → 落地 book.word_template + config/history/ 备注 + 注释存活
                               （首次写入走「键不存在 → 插入 book 块」那条历史上写坏过 YAML 的路）
                               带 templates/ 前缀的写法同样接受
                               恢复默认（reset: true）→ 回到出厂模板
                               护栏：目录穿越 / 文件不存在 / 非 .dotx|.docx / 空值 → 400 且**零改动**
                               反复改写不产生重复键（重复键 = 后续读取静默取后值）

用法：python tests/http/test_word_template_api_http.py
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
PY = sys.executable
PORT = 8935
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []

PROJECT_YAML = '''# =========================================================
# NovelForge 项目信息（每次运行前可修改）   ← 这行注释必须活下来
# =========================================================

book:
  name: "模板测试书"          # 书名
  genre: "奇幻"                    # 类型
  chapters: 3                      # 本次运行章节数
  author: "测试者"

materials:
  dir: "materials/raw"
  use_scraps: true
'''

DEFAULT = "templates/小说格式模板V1.3.dotx"


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


def _force_agent_mode_off(system_yaml: Path):
    """测试环境强制标准模式（裸请求无 GUI 来源头，agent_mode 下会被守卫拦）。"""
    if not system_yaml.exists():
        return
    lines = system_yaml.read_text(encoding="utf-8").splitlines()
    out, replaced = [], False
    for ln in lines:
        if ln.strip().startswith("agent_mode:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append(indent + "agent_mode: false   # 测试环境强制标准模式")
            replaced = True
        else:
            out.append(ln)
    if replaced:
        system_yaml.write_text("\n".join(out) + "\n", encoding="utf-8")


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_wordtpl_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    shutil.copytree(ROOT / "templates", tmp / "templates")
    (tmp / "config" / "project.yaml").write_text(PROJECT_YAML, encoding="utf-8")
    _force_agent_mode_off(tmp / "config" / "system.yaml")
    (tmp / "templates" / "备用模板.docx").write_bytes(b"PK\x03\x04 fake docx for listing")
    (tmp / "templates" / "说明.txt").write_text("不该进清单", encoding="utf-8")  # 非模板扩展名
    (tmp / "data" / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "materials" / "raw").mkdir(parents=True, exist_ok=True)
    return tmp


def main():
    tmp = build_project()
    proj = tmp / "config" / "project.yaml"
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

        print("=== 1. 清单与当前值 ===")
        code, d = req("GET", "/config/word_template")
        check("GET /config/word_template → 200", code == 200 and d.get("ok"),
              (code, str(d)[:180]))
        names = [f["name"] for f in d.get("files", [])]
        check("列出 templates/ 下的 .dotx/.docx（含出厂模板与备用）",
              "小说格式模板V1.3.dotx" in names and "备用模板.docx" in names, names)
        check("非模板扩展名不进清单（说明.txt 被滤掉）", "说明.txt" not in names, names)
        check("条目带 size/mtime/path 三件套",
              all({"name", "size", "mtime", "path"} <= set(f) for f in d.get("files", [])),
              d.get("files", [])[:1])
        check("初始 project.yaml 无该键 → current 为空串且不报错",
              d.get("current") == "", repr(d.get("current")))
        check("current_exists=False（当前没配模板时明说，不静默）",
              d.get("current_exists") is False, d.get("current_exists"))
        check("出厂默认值口径正确", d.get("default") == DEFAULT, d.get("default"))
        check("字段/路径口径标注正确",
              d.get("field") == "book.word_template"
              and d.get("path") == "config/project.yaml"
              and d.get("dir") == "templates",
              (d.get("path"), d.get("field"), d.get("dir")))

        print("\n=== 2. 选择模板：键不存在 → 插入 book 块（历史写坏 YAML 的那条路）===")
        code, d = req("POST", "/config/word_template", {"template": "备用模板.docx"})
        check("POST 选择 → 200", code == 200 and d.get("ok"), (code, str(d)[:200]))
        check("响应带回新值与落点", d.get("current") == "templates/备用模板.docx"
              and d.get("field") == "book.word_template", str(d)[:200])
        code, d = req("GET", "/config/word_template")
        check("回读 = 新选的模板", d.get("current") == "templates/备用模板.docx",
              repr(d.get("current")))
        check("回读 current_exists=True", d.get("current_exists") is True,
              d.get("current_exists"))
        text = proj.read_text(encoding="utf-8")
        check("project.yaml 注释与其它块未被破坏",
              "这行注释必须活下来" in text and "use_scraps: true" in text)
        check("word_template 键只出现一次（插入而非重复）",
              text.count("word_template:") == 1, text.count("word_template:"))
        import yaml
        book = (yaml.safe_load(text) or {}).get("book") or {}
        check("严格 loader 解析后值正确", book.get("word_template") == "templates/备用模板.docx",
              book.get("word_template"))
        check("写入前留了备份 config/history/",
              bool(list((tmp / "config" / "history").glob("project_*.yaml"))),
              [p.name for p in (tmp / "config" / "history").glob("project_*.yaml")][:3])

        print("\n=== 3. 护栏：非法输入必须 400 且零改动 ===")
        before = proj.read_text(encoding="utf-8")
        for bad, label in (({"template": "../../evil.dotx"}, "目录穿越"),
                           ({"template": "sub/dir.dotx"}, "带子目录"),
                           ({"template": "nope.dotx"}, "文件不存在"),
                           ({"template": "说明.txt"}, "非 .dotx/.docx 扩展名"),
                           ({"template": "   "}, "纯空值")):
            code, d = req("POST", "/config/word_template", bad)
            check("%s → 400" % label, code == 400 and not d.get("ok"),
                  (code, str(d)[:160]))
            check("%s 的报错给了出路（不是裸异常）" % label,
                  bool(str(d.get("error") or "").strip()), str(d.get("error"))[:120])
        check("非法输入后 project.yaml 逐字未改",
              proj.read_text(encoding="utf-8") == before)

        print("\n=== 4. 带 templates/ 前缀的写法同样接受（与配置里存的形态一致）===")
        code, d = req("POST", "/config/word_template", {"template": DEFAULT})
        check("POST 带前缀 → 200", code == 200 and d.get("ok"), (code, str(d)[:180]))
        code, d = req("GET", "/config/word_template")
        check("回读 = 出厂模板", d.get("current") == DEFAULT, repr(d.get("current")))

        print("\n=== 5. 恢复默认（reset: true）===")
        code, d = req("POST", "/config/word_template", {"template": "备用模板.docx"})
        check("先切到备用模板", code == 200 and d.get("ok"), (code, str(d)[:120]))
        code, d = req("POST", "/config/word_template", {"reset": True})
        check("恢复默认 → 200", code == 200 and d.get("ok"), (code, str(d)[:180]))
        code, d = req("GET", "/config/word_template")
        check("恢复后 current = 出厂默认", d.get("current") == DEFAULT, repr(d.get("current")))
        for i in range(3):
            req("POST", "/config/word_template", {"reset": True})
        text = proj.read_text(encoding="utf-8")
        check("反复恢复默认后键仍唯一（无重复键）",
              text.count("word_template:") == 1, text.count("word_template:"))
        check("反复改写后注释仍在", "这行注释必须活下来" in text)

        print("\n=== 6. 空值请求的报错要点名出路 ===")
        code, d = req("POST", "/config/word_template", {})
        check("空请求体 → 400", code == 400 and not d.get("ok"), (code, str(d)[:160]))
        check("报错提到「恢复默认」", "恢复默认" in str(d.get("error")), str(d.get("error"))[:160])

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
