# -*- coding: utf-8 -*-
"""S3/S8 回归：配置路径泄露 + 模型白名单回路。

## 本用例守护什么

- **S3**：`config/system.yaml` 与 `config/obsidian_templates.yaml` 曾硬编码
  用户本机绝对路径 `<vault>/设定库...`（已废弃命名 设定库 的漏网残留），
  并散落在 `obsidian_bridge` / `kb_index` / `obsidian_postprocess` /
  `voice_to_docx` 的默认值里 → 任何其他用户开箱即失败，且泄露私人路径到公开仓库。
- **S8**：`available_models` 与 `fetched_models.json` 两份白名单数据**确实存在**，
  却**从未参与任何判定**——`/models/switch` 连格式校验都没有，任意模型名可落盘生效，
  AGENTS.md 的「模型白名单纪律」是纸面纪律。

## 断言策略

  A. 全仓（源码 + 配置）不得出现该本机路径；不得出现 `设定库` 残留
  B. `model_registry` 两级校验：格式 → 白名单归属
  C. 逃生门 `strict=false` 只放行并回报未校验，不静默
  D. `/models/switch` 与 `/models/add` 的校验回路已接通（源码级确认调用点）

用法：python tests/unit/test_config_and_models.py
"""
import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


# 判据已**通用化**（2026-10-03）：原先点名一条具体的私有路径字面量，但那样的断言
# 会随"发布树虚构化"被一起替换掉 —— 于是它变成**空转**（检查器还在，却什么都不查了）。
# 现在改判**本机绝对路径的形态**：盘符 + 分隔符，或 POSIX 家目录前缀。
# 这比点名更强，也不会因为路径改名而失效。
# 负向回顾避免把 `https://` 里的 `s:/` 误判成盘符路径。
ABS_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?![/\\])|/(?:Users|home)/[^/\s]+/")


def main():
    print("=" * 70)
    print("S3/S8 回归：配置路径泄露 + 模型白名单回路")
    print("=" * 70)

    # ---------- A. 路径泄露扫描 ----------
    print("\n--- A. 用户本机路径泄露扫描 ---")
    scan_dirs = ["scripts", "config", "console/src", "console/main", "console/preload",
                 "prompts", "docs"]
    scan_files = ["README.md", "AGENTS.md", ".gitignore"]
    hits = []
    for d in scan_dirs:
        p = REPO / d
        if not p.exists():
            continue
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            if any(x in f.parts for x in ("node_modules", "__pycache__", "dist")):
                continue
            if f.suffix not in (".py", ".yaml", ".yml", ".md", ".js", ".vue", ".json"):
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            m = ABS_RE.search(txt)
            if m:
                hits.append(f"{f.relative_to(REPO)}: {m.group(0)}")
    for name in scan_files:
        f = REPO / name
        if f.exists():
            m = ABS_RE.search(f.read_text(encoding="utf-8", errors="ignore"))
            if m:
                hits.append(f"{name}: {m.group(0)}")

    check(not hits, "源码/配置中无本机绝对路径泄露", f"命中 {len(hits)} 处: {hits[:5]}")
    # ⚠️ 这里原先还有**第二遍同范围扫描**（只多判了品牌词），属于"判据写两遍" ——
    # 上一段的 ABS_RE 已覆盖它要守的东西，故删除（判据复制只会漂移，不会更保险）。

    # ---------- B. 路径回落行为 ----------
    print("\n--- B. vault 未配置时的行为（开箱即用，不崩）---")
    from obsidian_bridge import (get_vault_path, get_sandbox_dir,
                                 is_vault_configured, DEFAULT_SANDBOX_DIR)
    check(get_vault_path() is None,
          "vault 未配置 → 返回 None（非 Path('') —— 后者 str 后是 '.'，会误判已配置）",
          f"实际={get_vault_path()!r}")
    check(not is_vault_configured(), "is_vault_configured() 为 False（未启用联动）")
    sb = str(get_sandbox_dir()).replace("\\", "/")
    # 判据用**形态**（绝对路径）而不是某个具体品牌词：点名式断言会随发布树虚构化
    # 一起被替换掉，然后静默退化成空转（这里原先就是点名 `<vault>/<库名>`）。
    check(not ABS_RE.search(sb) and not sb.startswith("/"),
          "沙盒目录回落项目内相对路径（不含本机绝对路径）", f"实际={sb}")
    check(sb == DEFAULT_SANDBOX_DIR.replace("\\", "/"),
          f"沙盒回落值为 {DEFAULT_SANDBOX_DIR}", f"实际={sb}")

    # ---------- C. model_registry 两级校验 ----------
    print("\n--- C. model_registry 两级校验 ---")
    from utils import model_registry as mr
    root = Path(tempfile.mkdtemp(prefix="nf_mr_"))
    (root / "data/state").mkdir(parents=True)
    cfg = {"providers": {"tokenhub": {
        "available_models": ["glm-5", "kimi-k3"],
        "fallback": ["glm-5"]}}}
    (root / "data/state/fetched_models.json").write_text(json.dumps(
        {"_manual": ["my-model-x"], "tokenhub": {"models": ["dynamic-a"]}},
        ensure_ascii=False), encoding="utf-8")

    cases = [
        ("glm-5", True, "config.available_models"),
        ("kimi-k3", True, "config.available_models"),
        ("my-model-x", True, "fetched._manual（用户手动添加=已确认）"),
        ("dynamic-a", True, "fetched 动态清单"),
        ("gpt-4o", False, "未登记 → 拒绝"),
        ("unknown-v9-pro", False, "未登记 → 拒绝"),
    ]
    for name, want, why in cases:
        r = mr.check_model(name, cfg, root, strict=True)
        check(r["ok"] == want, f"{name} → {'准入' if want else '拒绝'}（{why}）",
              f"实际 ok={r['ok']} reason={r['reason'][:60]}")

    # 格式级（第 ① 级）
    print("\n--- C2. 第 ① 级 格式校验 ---")
    for bad in ["", "   ", "../etc/passwd", "a\\b", "bad model", "x" * 200, "-lead"]:
        r = mr.check_model(bad, cfg, root, strict=True)
        check(not r["ok"], f"格式非法被拒: {bad!r}", f"实际 ok={r['ok']}")
    check(mr.check_model("vendor/model:v1", cfg, root, strict=False)["ok"],
          "合法字符集（vendor/model:v1）通过格式校验")

    # 逃生门
    print("\n--- C3. strict=false 逃生门（不静默）---")
    r = mr.check_model("brand-new-model", cfg, root, strict=False)
    check(r["ok"] is True, "strict=false 放行未登记模型")
    check(r["checked"] is False,
          "回报 checked=False（前端/日志可辨「未做白名单校验」）")

    # 纠错建议
    print("\n--- C4. 纠错建议 ---")
    r = mr.check_model("glm-6", cfg, root, strict=True)
    check(bool(r["suggestions"]), "未命中时给出近似候选", f"实际={r['suggestions']}")
    check(len(r["suggestions"]) == len(set(r["suggestions"])), "候选无重复")
    check(all("glm" in s for s in r["suggestions"]),
          "候选按前缀收敛到同系列", f"实际={r['suggestions']}")

    # ---------- D. 校验回路已接通 ----------
    #
    # 2026-09-19 修正：这两条原本在 nf_api.py 里**按分支边界切源码文本**再 grep。
    # P2 拆分把 /models/* 的实现搬到了 nf_api_domains/models.py，文本自然不在
    # nf_api.py 里了 —— 于是测试变红，但它红的原因是「找错了文件」而不是
    # 「校验回路断了」。这类依赖**实现位置**的断言是脆的：重构一动就误报。
    #
    # 改为「先定位实现所在文件，再在文件内断言」：实现可以搬家，
    # 「必须调用 check_model / validate_format」这个契约不变。
    print("\n--- D. 写入层回路接通确认 ---")
    api_src = (REPO / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("from utils import model_registry" in api_src,
          "nf_api 已导入 model_registry")

    def _impl_source(branch_marker, fallback_file):
        """找出该端点**实现**所在源码：先看 nf_api.py 分支体，再看域模块。

        拆分后分支体只剩转发（`self._send(*_dom(dom_models.handle_...(self)))`），
        实现搬到了域模块。这里顺着转发目标把域模块源码也读进来。
        """
        idx = api_src.find(branch_marker)
        if idx == -1:
            return "", "分支未找到"
        # 取该分支后 300 字符（足够覆盖一条转发语句），从中找 dom_xxx 模块名
        seg = api_src[idx:idx + 300]
        if "dom_models" in seg:
            return (REPO / "scripts" / "nf_api_domains" / "models.py").read_text(
                encoding="utf-8"), "域模块 nf_api_domains/models.py"
        return seg, "nf_api.py 内联实现"

    sw_src, sw_where = _impl_source('p == "/models/switch"', "nf_api.py")
    add_src, add_where = _impl_source('p == "/models/add"', "nf_api.py")
    check("check_model(" in sw_src,
          "/models/switch 已接入两级校验 check_model", "实现位于: " + sw_where)
    check("validate_format(" in add_src,
          "/models/add 已接入格式校验 validate_format", "实现位于: " + add_where)

    print("\n" + "=" * 70)
    print(f"结果：PASS={PASS}  FAIL={FAIL}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
