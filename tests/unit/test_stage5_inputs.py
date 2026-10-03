# -*- coding: utf-8 -*-
"""stage5 输入构造自检（件7，2026-09-29）。

背景（哨兵实跑取证 + 三方复核成立的翻案）：stage5/stage6 产出空壳**不是模型能力问题**，
是**输入构造缺陷** —— 章节与设定集从未进入 prompt：

  1) `stage5_check.py` L52 批次列表只写**裸文件名**（`- 01.md`，无路径）→
     llm_client 按 CWD 找不到 → 判 missing；
  2) `prompts/stage5_check.md` L16 设定集行**不是列表行**（解析器只认 `- ` 开头）→
     该行从未被内联。

冒烟佐证：stage5 输入仅 2,048 token（真内联应 1 万+）；对照组 stage4 构造正确、
输入 35,135 token、同模型正常。

本自检既验证修复生效，也用**反证**（复刻修复前的构造）证明这不是「本来就没问题」。

全程离线、零网络、零费用。
用法：python tests/unit/test_stage5_inputs.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils.llm_client import extract_input_paths, inline_inputs   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


CHAPTER_TEXT = "第一章正文：沈砚在边城营地醒来，窗外落雪。"
SETTING = {"characters": [{"name": "沈砚"}, {"name": "程砧"}]}


def build(tmp, setting_line_prefix="- "):
    """搭临时项目根（含 prompts/ 模板与 data/ 输入）。返回 (root, proj)。"""
    root = Path(tmp)
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "data" / "setting").mkdir(parents=True, exist_ok=True)
    (root / "data" / "chapters" / "raw").mkdir(parents=True, exist_ok=True)
    (root / "data" / "chapters" / "checked").mkdir(parents=True, exist_ok=True)

    tpl = (REPO / "prompts" / "stage5_check.md").read_text(encoding="utf-8")
    if setting_line_prefix != "- ":
        # 反证用：把修复后加的 "- " 前缀去掉，回到修复前的模板形态
        tpl = tpl.replace("- 设定集: {{path_setting}}", "设定集: {{path_setting}}")
    (root / "prompts" / "stage5_check.md").write_text(tpl, encoding="utf-8")

    (root / "data" / "setting" / "setting.json").write_text(
        json.dumps(SETTING, ensure_ascii=False), encoding="utf-8")
    (root / "data" / "chapters" / "raw" / "01.md").write_text(CHAPTER_TEXT, encoding="utf-8")
    return root, {"book": {"name": "件7测试书", "chapters": 1}}


def render(root, proj, bare_name):
    """复刻 run_stage 的任务构造；bare_name=True 复刻修复前的裸文件名写法。"""
    import stage5_check as s5
    raw_dir = Path("data/chapters/raw")
    batch = sorted(raw_dir.glob("*.md"))
    if bare_name:
        batch_files = "\n".join(f"- {f.name}" for f in batch)      # 修复前
    else:
        batch_files = "\n".join(f"- {f.resolve()}" for f in batch)  # 修复后
    body = s5.build_check_task(proj, raw_dir, "data/setting/setting.json",
                               Path("data/chapters/checked"), batch_files, 1, 1)
    return body


def main():
    print("===== stage5 输入构造自检（件7）=====")
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="nf_s5in_")
    try:
        os.chdir(tmp)
        root, proj = build(tmp)

        print("\n[A] 修复后：章节与设定集都应真进入 prompt")
        body = render(root, proj, bare_name=False)
        refs = extract_input_paths(body)
        new_body, missing = inline_inputs(body)
        check("输入引用解析出 2 条（章节 + 设定集）", len(refs) == 2, refs)
        check("无 missing（输入不再被判缺失）", missing == [], missing)
        check("章节正文已内联", CHAPTER_TEXT in new_body)
        check("设定集正文已内联（角色名可见）", "沈砚" in new_body and "程砧" in new_body)
        check("带内联输入区标记", "## 内联输入" in new_body)

        print("\n[B] 反证：复刻修复前的构造，必须复现「输入缺失」")
        body_old = render(root, proj, bare_name=True)
        _, missing_old = inline_inputs(body_old)
        check("裸文件名 → 章节被判 missing（证明修复不是「本来就没问题」）",
              any("01.md" in m for m in missing_old), missing_old)

        print("\n[C] 反证：设定集行去掉 '- ' 前缀 → 不再被解析为输入")
        tmp2 = tempfile.mkdtemp(prefix="nf_s5in2_")
        try:
            os.chdir(tmp2)
            root2, proj2 = build(tmp2, setting_line_prefix="")
            body2 = render(root2, proj2, bare_name=False)
            refs2 = extract_input_paths(body2)
            check("设定集行非列表行 → 只剩 1 条引用（章节）", len(refs2) == 1, refs2)
        finally:
            os.chdir(tmp)
            shutil.rmtree(tmp2, ignore_errors=True)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n===== 合计: %d passed, %d failed =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
