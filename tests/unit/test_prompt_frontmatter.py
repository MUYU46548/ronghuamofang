# -*- coding: utf-8 -*-
"""提示词模板 frontmatter 体检：解析合法 + 死字段 `model` 不得回潮。

## 为什么

`prompts/*.md` 的 frontmatter 里曾长期躺着一行 `model: hy3  # 死代码…`。
全项目 20 个 `load_template()` 调用点**没有一个**读取 `meta["model"]` ——
模型真源是 `config/system.yaml` 的 `model.*`（按角色取）。死字段的危害不在
运行期，而在**误导**：GUI 提示词编辑器显示它、「一键复制阶段提示词」把它
连同正文一起塞进剪贴板，读的人（和外部 agent）会以为模板在挑模型。

2026-10-06 清理三份副本（源码树 / 桌面工作区 / 安装包 payload）后，
本用例负责**不让它回来**：任何人（或 agent）再往 frontmatter 里写 `model`
就地报错，并顺带守住 frontmatter 必须能被 yaml 严格解析
（`template_loader` 解析失败会退回默认 meta，是静默失败）。

用法：python tests/unit/test_prompt_frontmatter.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "prompts"

try:
    import yaml
except ImportError:                                     # pragma: no cover
    yaml = None

FM_RE = re.compile(r"\A---\s*[\r\n]+(.*?)[\r\n]+---", re.S)

# frontmatter 里**声明了也不会被读**的死键（真源见 config/system.yaml）
DEAD_KEYS = ("model",)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def case_frontmatter_parses():
    files = sorted(PROMPTS.glob("*.md"))
    check("prompts/ 模板存在", len(files) >= 10, "%d 个" % len(files))
    bad = []
    for p in files:
        m = FM_RE.match(p.read_text(encoding="utf-8"))
        if not m or yaml is None:
            continue
        try:
            yaml.safe_load(m.group(1))
        except Exception as e:                          # noqa: BLE001
            bad.append("%s: %s" % (p.name, e))
    check("frontmatter 全部可被 yaml 严格解析", not bad, "；".join(bad))


def case_no_dead_model_key():
    hits = []
    n_fm = 0
    for p in sorted(PROMPTS.glob("*.md")):
        m = FM_RE.match(p.read_text(encoding="utf-8"))
        if not m or yaml is None:
            continue
        n_fm += 1
        try:
            data = yaml.safe_load(m.group(1)) or {}
        except Exception:                               # noqa: BLE001
            continue               # 解析失败由上一个用例报
        for k in DEAD_KEYS:
            if k in data:
                hits.append("%s.%s" % (p.name, k))
    check("有 frontmatter 的模板 %d 个" % n_fm, n_fm > 0)
    check("frontmatter 不含死字段 model（真源 config/system.yaml 的 model.*）",
          not hits, "；".join(hits))


def case_loader_body_has_no_frontmatter():
    """load_template 返回的 body 不该把 frontmatter 混进任务文本。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from utils.template_loader import load_template
    except Exception as e:                              # noqa: BLE001
        check("template_loader 可导入", False, e)
        return
    name = "stage4_writing.md"
    p = PROMPTS / name
    if not p.exists():
        check("样例模板存在", False, name)
        return
    cwd = Path.cwd()
    try:
        import os
        os.chdir(ROOT)               # load_template 走相对路径 prompts/
        meta, body = load_template(name, {"n": 1})
    except Exception as e:                              # noqa: BLE001
        check("load_template(%s) 可调用" % name, False, e)
        return
    finally:
        os.chdir(cwd)
    check("load_template 返回 meta 字典", isinstance(meta, dict), type(meta))
    check("body 不以 frontmatter 分隔符开头", not body.lstrip().startswith("---"))
    check("body 含正文标题", "长篇小说写作任务" in body)


def main():
    print("=" * 62)
    print("  提示词模板 frontmatter 体检（死字段 model 防回潮）")
    print("=" * 62)
    case_frontmatter_parses()
    case_no_dead_model_key()
    case_loader_body_has_no_frontmatter()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   x " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
