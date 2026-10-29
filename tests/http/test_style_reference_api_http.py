# -*- coding: utf-8 -*-
"""范文粘贴端点自检（GET/POST /config/style_reference，2026-10-19）。零 LLM。

## 为什么要有这个端点

用户原话：「为什么不在绒花墨坊里直接搞个窗口一键粘贴进去？而且它应该跟着用户数据走」。
原先只能「用编辑器打开 materials/style_sample.md 粘」—— 对非程序员是一道坎。

## 钉什么

| 编号 | 钉什么 |
|---|---|
| S0 | **样例稿声明门**（2026-10-29）：没同意 → 保存 403 + 一个字节不写；同意后可写 |
| S1 | 未配置时回默认路径 `materials/style_sample.md`，且 configured=false |
| S2 | 保存后**文件落盘 + 配置自动指向它**（用户不用改 yaml） |
| S3 | 有效正文字数的口径与 stage4 同源（`prose_char_count`），不足 100 → 明确「暂不生效」 |
| S4 | 超长（>40000）→ 400 且**文件一个字没动** |
| S5 | 超出注入上限 → 回 `truncated`，前端能提示「只注入前 N 字」 |
| S6 | 覆盖写入前**备份旧版**到 data/state/style_reference_history/ |
| S7 | 清空 = 传空字符串（写作不再注入范文） |
| S8 | **路径必须落在工作区内**：`../evil.md` 一律 400，且工作区外不落盘 |
| S9 | 缺 `text` 字段 → 400（不许默认清空） |
| S10 | 外部 Agent **可以写**（创作决策，不是成本/安全闸门）——与 token_limit 相反 |
| S11 | **撤回声明**后保存重新 403；清空不受声明门限制（那是在撤回） |

**不碰真实仓库**：复制 scripts/config/prompts 到临时根起 nf_api。

用法：python tests/http/test_style_reference_api_http.py
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
sys.path.insert(0, str(ROOT / "scripts"))
PY = sys.executable
PORT = 8934
BASE = "http://127.0.0.1:%d" % PORT
PASS, FAIL = [], []

PROSE = "他把刀放下，说了句「走吧」，然后转身走进雨里。"
short_probe = PROSE * 8
MIN_YAML = """book:
  name: "范文测试书"
  genre: "奇幻"
  chapters: 3
  style_reference: ""
materials:
  dir: "materials/raw"
"""


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
        except Exception:                                    # noqa: BLE001
            payload = {}
        return e.code, payload


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_styleref_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    (tmp / "prompts").mkdir(parents=True, exist_ok=True)
    for d in ("data/state", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (tmp / "config" / "project.yaml").write_text(MIN_YAML, encoding="utf-8")
    return tmp


def set_ref_path(tmp, value):
    p = tmp / "config" / "project.yaml"
    t = p.read_text(encoding="utf-8")
    import re
    t = re.sub(r'style_reference:\s*"[^"]*"', 'style_reference: "%s"' % value, t)
    p.write_text(t, encoding="utf-8")


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ready = False
        for _ in range(60):
            try:
                if req("GET", "/health")[0] == 200:
                    ready = True
                    break
            except Exception:                                # noqa: BLE001
                time.sleep(0.5)
        check("nf_api 起来了", ready)
        if not ready:
            return 1

        print("\n【S0】样例稿声明门（2026-10-29）：没同意不许写正文")
        code, d = req("GET", "/style/sample_consent")
        check("GET 200 且默认未同意", code == 200 and d.get("ok")
              and d.get("agreed") is False, d)
        code, d = req("POST", "/config/style_reference", {"text": short_probe})
        check("未同意时保存 403", code == 403 and d.get("need_consent") is True,
              (code, (d.get("error") or "")[:80]))
        check("403 时一个字节都没写", not (tmp / "materials" / "style_sample.md").exists())
        code, d = req("POST", "/style/sample_consent", {"agreed": True})
        check("同意后 agreed=true", code == 200 and d.get("agreed") is True, d)
        check("同意记录落在工作区（换书后仍有效）",
              (tmp / "data" / "state" / "style_sample_consent.json").exists())

        print("\n【S1】未配置时的默认回显")
        code, d = req("GET", "/config/style_reference")
        check("GET 200 且未配置", code == 200 and d.get("ok")
              and d.get("configured") is False, d)
        check("默认路径 = materials/style_sample.md",
              d.get("path") == "materials/style_sample.md", d.get("path"))
        check("空文本 + prose_chars=0 + 不生效",
              d.get("text") == "" and d.get("prose_chars") == 0
              and d.get("effective") is False, d.get("prose_chars"))

        print("\n【S2/S3】保存短文：落盘 + 配置自动指向 + 有效正文判定")
        short = PROSE * 8                       # 原始 ~200 字，有效正文 ~120 字
        code, d = req("POST", "/config/style_reference", {"text": short})
        check("保存 200", code == 200 and d.get("ok"), d)
        f = tmp / "materials" / "style_sample.md"
        check("文件真的落盘了", f.exists() and f.read_text(encoding="utf-8").strip() == short.strip(),
              f)
        yaml_txt = (tmp / "config" / "project.yaml").read_text(encoding="utf-8")
        check("project.yaml 自动指向它（用户不用手改配置）",
              'style_reference: "materials/style_sample.md"' in yaml_txt, yaml_txt[:120])
        check("回显 effective=true（有效正文 ≥100）", d.get("effective") is True, d.get("prose_chars"))
        check("有效正文字数由后端算（与 stage4 同源）",
              d.get("prose_chars", 0) > 100 and d.get("prose_chars") < len(short),
              (d.get("prose_chars"), len(short)))

        print("\n【S3b】只有占位符 → 明确「暂不生效」")
        code, d = req("POST", "/config/style_reference",
                      {"text": "## 范文一\n\n（把正文粘到这里，粘完删掉这一行）\n"})
        check("占位符文本 effective=false", d.get("effective") is False, d.get("prose_chars"))
        check("message 点明暂不生效而不是静默", "暂不生效" in (d.get("message") or ""),
              d.get("message"))

        print("\n【S6】覆盖前备份旧版")
        code, d = req("POST", "/config/style_reference", {"text": PROSE * 20})
        hist = sorted((tmp / "data" / "state" / "style_reference_history").glob("reference_*.md"))
        # 原判据断言「恰好 1 份」——但它依赖前面 S2/S3b 各写了几次，
        # 用例第二次连跑就变红（实测 2 份）。备份判据要钉的是
        # 「覆盖前把上一版存下来了」，不是「历史目录总数」。
        check("覆盖前有备份（>=1 份）", len(hist) >= 1, len(hist))
        check("最新备份内容是上一版（占位符那版）",
              hist and "把正文粘到这里" in hist[-1].read_text(encoding="utf-8"),
              hist[-1].name if hist else "")

        print("\n【S5】超出注入上限 → truncated")
        long_text = PROSE * 150                  # ~3700 字，未超 40000，但可能超 6000 原始长度
        long_text = long_text + "。".join(["补充段落内容"] * 800)   # 保证 > 6000
        code, d = req("POST", "/config/style_reference", {"text": long_text})
        check("超上限时 truncated>0", d.get("truncated", 0) > 0, d.get("truncated"))
        check("message 说明只注入前 N 字", "只注入前" in (d.get("message") or ""),
              d.get("message"))

        print("\n【S4】超长（>40000）→ 400 且文件未被改动")
        before = f.read_text(encoding="utf-8")
        code, d = req("POST", "/config/style_reference", {"text": "一" * 40001})
        check("400 拒绝", code == 400, (code, (d.get("error") or "")[:60]))
        check("文件一个字没动", f.read_text(encoding="utf-8") == before)

        print("\n【S9】缺 text 字段 → 400（不许默认清空）")
        code, d = req("POST", "/config/style_reference", {})
        check("400 拒绝", code == 400, code)
        check("文件仍在", f.read_text(encoding="utf-8") == before)

        print("\n【S8】路径越界 → 400 且工作区外不落盘")
        set_ref_path(tmp, "../evil.md")
        code, d = req("POST", "/config/style_reference", {"text": PROSE * 10})
        check("400 拒绝（不许往工作区外写）", code == 400, (code, (d.get("error") or "")[:60]))
        check("工作区外没有生成文件", not (tmp.parent / "evil.md").exists())
        set_ref_path(tmp, "materials/style_sample.md")

        print("\n【S10】外部 Agent 可以写（创作决策，非安全闸门）")
        code, d = req("POST", "/config/style_reference", {"text": PROSE * 10})
        check("无 GUI 头也能写（与 token_limit 的 GUI-only 相反）", code == 200, code)

        print("\n【S11】撤回声明后保存重新被拦（清空不受限）")
        code, d = req("POST", "/style/sample_consent", {"agreed": False})
        check("撤回 200 且 agreed=false", code == 200 and d.get("agreed") is False, d)
        code, d = req("POST", "/config/style_reference", {"text": short_probe})
        check("撤回后保存 403（不是静默同意）",
              code == 403 and d.get("need_consent") is True, code)
        code, d = req("POST", "/config/style_reference", {"text": ""})
        check("清空不受声明门限制（那是在撤回）", code == 200, code)

        print("\n【S7】清空")
        code, d = req("POST", "/config/style_reference", {"text": ""})
        check("清空 200 且回显空", code == 200 and d.get("prose_chars") == 0, d.get("prose_chars"))
        check("message 说明不再注入", "不再注入" in (d.get("message") or ""), d.get("message"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:                                    # noqa: BLE001
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for name in FAIL:
        print("    -", name)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
