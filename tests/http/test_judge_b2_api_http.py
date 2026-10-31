# -*- coding: utf-8 -*-
"""B2 整本否掉的端点契约（2026-10-31）。

用户拍板「B2 加按钮」后，这里钉住四件事：

- A1 理由太短 → 400（否掉整本是留档决定，不是随手一点）
- A2 理由够 → 200，落盘；`/judge/report` 回传（界面横幅靠它）
- A3 撤销 → 200，当前记录清空、历史留档
- A4 两个端点都在 `FORBIDDEN_IN_AGENT_MODE` 里 —— 与事件勾选、逐句裁定同族：
     外部 Agent 可以读（report 带 book_reject），不可以代替作者签字

用法：python tests/http/test_judge_b2_api_http.py
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
PORT = 8955
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []

BODY = """## 第1章 夜雨

他在档案室盖章。他把回执压平，拿起印章，在纸角上按下去。

他核对回执上的编号。他一行一行往下看，看到第三行停住。

他抽出一张字条。他把字条摊在桌上，压在回执底下。
"""

OUTLINE = """- 核心事件：
  1. 他在档案室盖章
  2. 他核对回执编号
- 涉及角色：沈砚
"""


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=60, headers=None):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    h = {"Content-Type": "application/json", "X-Mofang-Source": "gui"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
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
    tmp = Path(tempfile.mkdtemp(prefix="nf_b2http_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    for d in ("data/state", "data/setting", "data/chapters/raw",
              "data/outline/chapters", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "chapters" / "raw" / "01.md").write_text(BODY, encoding="utf-8")
    (tmp / "data" / "outline" / "chapters" / "01.md").write_text(OUTLINE, encoding="utf-8")
    (tmp / "config" / "project.yaml").write_text(
        'book:\n  name: "B2端点验收"\n  chapters: 1\n', encoding="utf-8")
    import re
    sys_yaml = tmp / "config" / "system.yaml"
    txt = sys_yaml.read_text(encoding="utf-8")
    sys_yaml.write_text(re.sub(r"agent_mode:\s*true", "agent_mode: false", txt),
                        encoding="utf-8")
    return tmp


def main():
    tmp = build_project()
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

        print("\n【A1】理由太短 → 400")
        for bad in ("", "太平", "   "):
            code, d = req("POST", "/judge/book_reject", {"reason": bad})
            check("拒绝 %r" % (bad,), code == 400, (code, d.get("error")))
        code, d = req("POST", "/judge/book_reject", {})
        check("缺 reason 字段也拒", code == 400, (code, d.get("error")))

        print("\n【A2】理由够 → 落盘 + report 回传")
        code, d = req("POST", "/judge/book_reject",
                      {"reason": "整本没有冲突也没有推进，重写比改划算"})
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        check("回执带 message（用户要知道发生了什么）",
              "整本否掉" in (d.get("message") or ""), d.get("message"))
        code, rep = req("GET", "/judge/report")
        br = rep.get("book_reject") or {}
        check("report 回传 book_reject（界面横幅靠它）",
              br.get("reason") == "整本没有冲突也没有推进，重写比改划算", br)
        check("带时间与署名", bool(br.get("at")) and br.get("by") == "author", br)

        print("\n【A3】撤销 → 清当前、留历史")
        code, d = req("POST", "/judge/book_reject/clear", {})
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        code, rep2 = req("GET", "/judge/report")
        check("当前记录已清空", not rep2.get("book_reject"), rep2.get("book_reject"))
        hist = rep2.get("book_reject_history") or []
        check("历史里留了一条（曾经否过不消失）", len(hist) == 1, hist)
        code, d = req("POST", "/judge/book_reject/clear", {})
        check("再撤销 → 400（明确说没有记录，不静默成功）", code == 400, (code, d))

        print("\n【A4】Agent 禁用名单")
        sys.path.insert(0, str(ROOT / "scripts"))
        from utils import agent_guard as AG
        check("/judge/book_reject 在 FORBIDDEN_IN_AGENT_MODE 里",
              "/judge/book_reject" in AG.FORBIDDEN_IN_AGENT_MODE)
        check("/judge/book_reject/clear 也在",
              "/judge/book_reject/clear" in AG.FORBIDDEN_IN_AGENT_MODE)
        check("只读的 /judge/report 不在名单里（Agent 可以读作者判断）",
              "/judge/report" not in AG.FORBIDDEN_IN_AGENT_MODE)

        # ---------------------------------------------------------------- A5
        # 2026-10-31 用户追加：「整本否掉帮我加个一键归档的快捷按钮，
        # 直接一键清理产物风险太高。归档之后是用户自己的事情。」
        # 所以这里钉的是「搬走」而不是「删掉」—— 而且搬完还能原样恢复。
        print("\n【A5】一键归档：搬家，不是删除")
        code, d = req("POST", "/judge/book_archive", {})
        check("缺 confirm → 400（防误点搬空工作区）", code == 400,
              (code, d.get("error")))

        code, d = req("POST", "/judge/book_archive", {"confirm": True, "reason": "测过一遍，先收起来"})
        check("归档 200 且 ok", code == 200 and d.get("ok") is True, d)
        check("回执写明搬去哪（data/books/书名）",
              d.get("path") == "data/books/B2端点验收", d.get("path"))
        check("回执明说「是搬走不是删掉」",
              "搬走" in (d.get("next") or "") and "删除归档" in (d.get("next") or ""),
              d.get("next"))

        arch = tmp / "data" / "books" / "B2端点验收"
        check("归档目录建立", arch.is_dir())
        check("正文进了归档（没被删）",
              (arch / "chapters" / "raw" / "01.md").is_file())
        check("config 跟着书走（恢复后还是这本书的配置）",
              (arch / "config" / "project.yaml").is_file())
        check("materials 跟着书走", (arch / "materials").is_dir())
        check("工作区里已经没有那一章（确实搬空了）",
              not (tmp / "data" / "chapters" / "raw" / "01.md").is_file())

        meta_p = arch / "_meta.json"
        meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.is_file() else {}
        check("_meta.json 记了归档原因（半年后还看得懂）",
              meta.get("why") == "测过一遍，先收起来", meta.get("why"))
        check("_meta.json 记了书名与时间",
              meta.get("book") == "B2端点验收" and bool(meta.get("archived_at")), meta)

        code, d2 = req("POST", "/judge/book_archive", {"confirm": True})
        check("工作区已空时再点 → 明确说「无需归档」，不报错也不覆盖",
              code == 200 and "无需归档" in (d2.get("message") or ""),
              (code, d2.get("message") or d2.get("error")))
        check("原归档没被这次点击动过（meta 还在）",
              (arch / "_meta.json").is_file())

        code, lst = req("GET", "/project/list")
        names = [x.get("name") for x in (lst.get("archived") or [])]
        check("/project/list 能看见这份归档", "B2端点验收" in names, names)

        code, d3 = req("POST", "/project/restore", {"name": "B2端点验收", "yes": True})
        check("可以恢复回来（归档不是终点）", code == 200 and d3.get("ok") is True, d3)
        check("恢复后正文回到工作区",
              (tmp / "data" / "chapters" / "raw" / "01.md").is_file())

        check("/judge/book_archive 在 FORBIDDEN_IN_AGENT_MODE 里（Agent 不得搬空工作区）",
              "/judge/book_archive" in AG.FORBIDDEN_IN_AGENT_MODE)

        # ---------------------------------------------------------------- A6
        # I6 三稿对照（2026-10-31）：正文全文只在 text=1 时回。
        print("\n【A6】三稿对照：默认不带正文，text=1 才带")
        code, d = req("GET", "/judge/destroy?n=1")
        check("200 且 ok", code == 200 and d.get("ok") is True, d)
        check("默认**不**带正文（省 1.8 万字符的体积）", "texts" not in d,
              list(d.keys()))
        check("统计仍在（shrink / present）",
              isinstance(d.get("destroy", {}).get("shrink"), dict),
              d.get("destroy", {}).get("shrink"))

        code, d = req("GET", "/judge/destroy?n=1&text=1")
        texts = d.get("texts") or {}
        check("text=1 带回 raw 全文",
              isinstance(texts.get("raw"), str) and "档案室盖章" in texts["raw"],
              (texts.get("raw") or "")[:40])
        check("没有检查稿 → checked 为 null（不是空字符串）",
              texts.get("checked") is None, texts.get("checked"))
        check("没有润色稿 → refined 为 null", texts.get("refined") is None)
        check("present 如实为 false，不冒充“没变化”",
              d["destroy"]["present"] == {"raw": True, "checked": False,
                                          "refined": False},
              d["destroy"]["present"])

        # 造一份检查稿：证据是「真的读第二份文件」，而不是把 raw 复制一份充数
        (tmp / "data" / "chapters" / "checked").mkdir(parents=True, exist_ok=True)
        (tmp / "data" / "chapters" / "checked" / "01.md").write_text(
            "## 第1章 夜雨\n\n他在档案室盖章。\n\n他核对编号。\n", encoding="utf-8")
        code, d2 = req("GET", "/judge/destroy?n=1&text=1")
        t2 = d2.get("texts") or {}
        check("有了检查稿 → 带回它自己的正文",
              isinstance(t2.get("checked"), str) and "核对编号" in t2["checked"],
              (t2.get("checked") or "")[:40])
        check("raw 没被覆盖（三份是各自的文件）",
              "核对回执" in (t2.get("raw") or ""), (t2.get("raw") or "")[:40])
        check("缩水率算出来了（raw→checked 有值）",
              isinstance(d2["destroy"]["shrink"].get("raw->checked"), (int, float)),
              d2["destroy"]["shrink"])
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
