# -*- coding: utf-8 -*-
"""「输入文件」段格式校验自检（P1，2026-10-03）。零 LLM、零网络。

## 为什么需要

`extract_input_paths` 只认**列表行**（`- 名称: <路径>`）。段落里出现别的写法时，
旧实现**静默跳过**：任务照跑、prompt 里却没有这些输入 → 模型基于缺失信息产出
空壳或胡编，而日志里一个字的异常都没有。

这不是假设：2026-09-29 stage5 空稿事故的根因之一就是
`prompts/stage5_check.md` 的设定集行**漏写了 `- ` 前缀** →「该行从未被内联」，
而 stage5 照打"逻辑检查完成"（同一类病还有目录引用指向不存在目录：内联 0 个文件，
一样不吭声）。

判据：**格式不符必须当场停**，报错要能指出「哪一行、原文是什么、为什么没被接受、
该怎么写」。用例正反两面都钉：坏格式必须抛且行号准确；好格式一个都不许误伤
（含真实模板的现网写法）。

用法：python tests/unit/test_input_section_format.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.llm_client import (InputSectionFormatError,  # noqa: E402
                              extract_input_paths, format_input_section_error,
                              inline_inputs)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def raises_format(body, task_path=None):
    """调用 inline_inputs，返回 (是否抛, 异常或结果)。"""
    try:
        return False, inline_inputs(body, task_path=task_path)
    except InputSectionFormatError as e:
        return True, e


# ============================================================ 1. 静默 → 报错
def case_bad_format_now_raises():
    print("\n【1】漏写 `- ` 前缀的路径行：必须**明确报错**，不再静默漏内联")
    body = ("# 任务\n\n"
            "## 输入文件（用 read_file 读取）\n"      # 第 3 行
            "本批需处理的章节：\n"                    # 第 4 行（纯说明，无路径 → 正常）
            "data/chapters/raw/01.md\n"              # 第 5 行（漏前缀，含路径 → 必须报）
            "- 设定集: data/setting/setting.json\n"  # 第 6 行（合法）
            "\n## 任务\n干活\n")
    bad, res = raises_format(body, task_path="data/state/tasks/stage5_check_batch1.md")
    check("格式不符 → 抛 InputSectionFormatError（不再静默）", bad, res)
    msg = str(res)
    check("报错指出**行号**（第 5 行）", "第 5 行" in msg, msg)
    check("报错复述**原文**（人一眼认出是哪条）", "data/chapters/raw/01.md" in msg, msg)
    check("报错说明**为什么**没被接受", "非列表行" in msg, msg)
    check("报错给出**正确写法**（可照着改）", "`- 名称: <路径>`" in msg, msg)
    check("报错点名任务文件（run_task 会传 task_path）",
          "任务文件: data/state/tasks/stage5_check_batch1.md" in msg, msg)
    check("纯说明行（无路径）不误报", "本批需处理的章节" not in msg, msg)

    # 诊断接口本身也带着同样的信息（供其它调用方复用，不各写一遍）
    diag = {}
    refs = extract_input_paths(body, diag)
    check("extract_input_paths 仍返回可解析的那条（不因坏行丢掉好行）",
          [p for p, _ in refs] == ["data/setting/setting.json"], refs)
    check("诊断标出段起始行号", diag["section_line"] == 3, diag["section_line"])
    check("诊断里 skipped 恰好 1 条且行号准确",
          len(diag["skipped"]) == 1 and diag["skipped"][0]["line"] == 5, diag["skipped"])


def case_missing_dir_now_raises():
    print("\n【2】目录引用指向不存在的目录：也必须报（旧实现内联 0 个文件不吭声）")
    body = ("## 输入文件（用 read_file 读取）\n"
            "- 碎片目录: /definitely/not/here/ 下的 *.md\n"
            "- 设定集: data/setting/setting.json\n")
    bad, res = raises_format(body)
    check("目录不存在 → 抛错", bad, res)
    check("报错说明是目录引用问题", "目录不存在" in str(res), str(res))
    check("诊断里 is_dir 语义未变（好行照常通过）",
          extract_input_paths(body)[-1][0] == "data/setting/setting.json",
          extract_input_paths(body))


# ============================================================ 3. 不误伤
def case_good_formats_untouched():
    print("\n【3】反证：合法写法一个都不许误伤（含现网模板的真实形态）")
    bodies = {
        "普通列表行": ("## 输入文件（用 read_file 读取）\n"
                       "- 本章大纲: data/outline/chapters/01.md\n"
                       "- 设定集: data/setting/setting.json（仅参考涉及本章条目）\n"),
        "章节列举行": ("## 输入文件（用 read_file 读取）\n"
                       "- 第1章 → data/outline/chapters/01.md\n"
                       "- 第2章 → data/outline/chapters/02.md\n"),
        "缩进续行说明": ("## 输入文件（用 read_file 读取）\n"
                         "- 已定稿设定（canon 快照）: data/setting/canon.json\n"
                         "  （上一轮已拍板的定稿，勿重复推导）\n"),
        "空输入（无任何引用）": "## 输入文件（用 read_file 读取）\n（本轮无输入）\n",
        "没有输入段（纯输出任务）": "# 任务\n把结果写到 data/x.md\n",
        "无路径的列表说明行": ("## 输入文件（用 read_file 读取）\n"
                               "- 待归并素材（**逐个**读取；已定稿的不在此列）:\n"),
    }
    for name, body in bodies.items():
        bad, res = raises_format(body)
        check("合法形态不抛错：" + name, bad is False, res)


def case_real_template_shapes():
    print("\n【4】现网模板形态抽查（prompts/ 里真实的输入段写法）")
    import os
    origin = os.getcwd()
    try:
        # 用真实模板渲染一遍常见形态（占位符替换成真实存在/不存在的路径都行：
        # 本用例只关心**格式**判定，不关心文件在不在）
        shape = [("stage2_global_outline.md",
                  {"path_setting": "/x/data/setting/setting.json",
                   "path_manifest": "/x/materials/manifest.json",
                   "path_project": "/x/config/project.yaml"}),
                 ("stage3_chapter_outline.md",
                  {"path_global_outline": "/x/data/outline/global.md",
                   "path_setting": "/x/data/setting/setting.json",
                   "path_project": "/x/config/project.yaml",
                   "listing": "- 第1章 → data/outline/chapters/01.md"}),
                 ("stage5_check.md",
                  {"batch_files": "- /x/data/chapters/raw/01.md",
                   "path_setting": "/x/data/setting/setting.json",
                   "batch_index": 1, "total_batches": 1}),
                 ("stage6_polish.md",
                  {"listing": "- 第1章: /x/data/chapters/checked/01.md",
                   "path_setting": "/x/data/setting/setting.json",
                   "path_refined": "/x/data/chapters/refined",
                   "vol_index": 1, "total_volumes": 1}),
                 ("chapter_review.md",
                  {"path_setting": "/x/data/setting/setting.json",
                   "path_rolling": "/x/data/summaries/rolling.md",
                   "chapters_content": "### 第 1 章\n正文\n",
                   "path_report": "/x/data/outline/review_report.json"})]
        for name, vars_ in shape:
            from utils.template_loader import load_template
            _, body = load_template(name, vars_)
            diag = {}
            refs = extract_input_paths(body, diag)
            check("模板 %s：输入段无格式错误" % name,
                  diag.get("skipped") == [], diag.get("skipped"))
            check("模板 %s：解析出至少 1 条输入" % name, len(refs) >= 1, refs)
    finally:
        os.chdir(origin)


# ============================================================ 5. 报错文案
def case_error_message_shape():
    print("\n【5】报错文案：多条坏行一次报全（别让人改一条跑一次）")
    body = ("## 输入文件（用 read_file 读取）\n"
            "a/b/one.md\n"
            "- 好的: data/setting/setting.json\n"
            "c/d/two.md\n")
    bad, res = raises_format(body)
    msg = str(res)
    check("两条坏行都报出来", "one.md" in msg and "two.md" in msg, msg)
    check("报错首行给出总数", "2 处" in msg, msg)
    diag = {"skipped": [{"line": 1, "text": "x", "path": "p", "reason": "r"}],
            "section_line": 3}
    check("format_input_section_error 可独立复用（单一文案来源）",
          "第 1 行" in format_input_section_error(diag), format_input_section_error(diag))


def main():
    print("=" * 62)
    print("  「输入文件」段格式校验自检（P1）")
    print("=" * 62)
    case_bad_format_now_raises()
    case_missing_dir_now_raises()
    case_good_formats_untouched()
    case_real_template_shapes()
    case_error_message_shape()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
