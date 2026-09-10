# -*- coding: utf-8 -*-
"""章节审查脚本（P0 审稿→修稿闭环，Phase 1）。

在 stage4/stage5 完成后调用，生成结构化审查报告：
- data/outline/review_report.json（机器可读）
- data/outline/review_report.md（人可读）

审查维度（确定性 + LLM）：
1. 字数合规 / quality 自评（确定性）
2. 大纲契合度（LLM）
3. 角色言行一致性（LLM）
4. 与前文衔接（LLM）
5. 文风合规（确定性：禁用元素扫描）

用法：
  python scripts/chapter_review.py [--scope raw|checked|refined]
  python scripts/chapter_review.py --scope checked --report data/outline/review_report.json
  python scripts/chapter_review.py --scope raw --dry-run  # 仅生成任务文件不跑 LLM
"""
import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime

# 把 scripts/ 加入路径，方便直接运行
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from utils.file_io import read_text, write_text
from utils.template_loader import load_template
from utils.summary_chain import load_rolling
from utils.llm_client import make_client

OUTLINE_DIR = Path("data/outline/chapters")
SETTING_PATH = Path("data/setting/setting.json")
ROLLING_PATH = Path("data/summaries/rolling.md")
GLOBAL_OUTLINE_PATH = Path("data/outline/global.md")

# 文风违规模式
STYLE_PATTERNS = [
    (re.compile(r"(?<![一-鿿])你(?![一-鿿])"), "第二人称\"你\""),
    (re.compile(r"像[^，。]+像[^，。]+"), "比喻连缀"),
    (re.compile(r"仿佛[^，。]+仿佛[^，。]+"), "比喻连缀（仿佛）"),
    (re.compile(r"她?他?心想：?[\"\"']"), "直白心理剖析"),
    (re.compile(r"---"), "分隔线（疑似排版残留）"),
]


def pick_scope_dir(scope):
    """按优先级选择审查目录：用户指定 > refined > checked > raw。"""
    if scope:
        return Path(f"data/chapters/{scope}")
    for name in ("refined", "checked", "raw"):
        d = Path(f"data/chapters/{name}")
        if d.exists() and list(d.glob("*.md")):
            return d
    return None


def scan_style_issues(text):
    """扫描文风违规，返回 [(type, detail)]。"""
    hits = []
    for pat, name in STYLE_PATTERNS:
        for m in pat.finditer(text):
            snippet = text[max(0, m.start() - 5):min(len(text), m.end() + 5)]
            hits.append((name, f"...{snippet}..."))
            break  # 每类违规只报一次
    return hits


def deterministic_checks(chapter_path, outline_path):
    """运行确定性检查，返回 dict。"""
    text = read_text(chapter_path)
    result = {
        "exists": True,
        "word_count": 0,
        "quality": None,
        "title": None,
        "title_ok": False,
        "word_ok": False,
        "style_issues": [],
        "outline_exists": outline_path.exists() if outline_path else False,
    }

    # 标题
    title_m = re.search(r"^##\s*第\s*(\d+)\s*章.*$", text, re.MULTILINE)
    if title_m:
        result["title"] = title_m.group(0).lstrip("# ")
        result["title_ok"] = True

    # 字数
    stripped = re.sub(r"[*`>_|\[\]()!\-]", "", text)
    stripped = re.sub(r"\s+", "", stripped)
    result["word_count"] = len(stripped)

    # quality
    qm = re.search(r"<!--\s*quality:\s*(\d+(?:\.\d+)?)\s*/\s*10\s*-->", text)
    if qm:
        result["quality"] = float(qm.group(1))

    # 字数范围检查（使用配置默认值）
    cfg = _load_cfg()
    target = cfg.get("chapter", {}).get("target_words", [2000, 3000])
    result["word_ok"] = target[0] <= result["word_count"] <= target[1]
    result["target_min"] = target[0]
    result["target_max"] = target[1]

    # 文风扫描
    result["style_issues"] = scan_style_issues(text)

    return result


def _load_cfg():
    """加载 system.yaml 配置。"""
    import yaml
    try:
        return yaml.safe_load(read_text("config/system.yaml")) or {}
    except FileNotFoundError:
        return {}


def _load_proj():
    """加载 project.yaml 配置。"""
    import yaml
    try:
        return yaml.safe_load(read_text("config/project.yaml")) or {}
    except FileNotFoundError:
        return {}


def build_review_prompt(chapters_content, setting_path):
    """构建审查任务文件内容。"""
    _, body = load_template("chapter_review.md", {
        "path_setting": setting_path.resolve(),
        "path_rolling": ROLLING_PATH.resolve(),
        "chapters_content": chapters_content,
        "path_report": Path("data/outline/review_report.json").resolve(),
    })
    return body


def format_chapter_content(n, chapter_path, outline_path, det):
    """格式化单章内容供 LLM 审查。"""
    lines = [f"### 第 {n} 章", ""]

    # 确定性检查摘要
    lines.append("#### 确定性检查结果")
    lines.append(f"- 字数: {det['word_count']} 字"
                 f"{' ✓' if det['word_ok'] else ' ✗ (范围 ' + str(det['target_min']) + '-' + str(det['target_max']) + ')'}")
    lines.append(f"- quality 自评: {det['quality'] if det['quality'] is not None else '缺失'}/10")
    lines.append(f"- 标题: {det['title'] or '缺失'}")
    if det["style_issues"]:
        for issue_type, snippet in det["style_issues"]:
            lines.append(f"- 文风嫌疑: {issue_type} ({snippet})")
    lines.append("")

    # 大纲
    if outline_path.exists():
        outline_text = read_text(outline_path)
        lines.append("#### 本章大纲")
        lines.append(outline_text[:1500])
        lines.append("")
    else:
        lines.append("#### 本章大纲（缺失）")
        if GLOBAL_OUTLINE_PATH.exists():
            lines.append(f"（回退使用整体大纲 {GLOBAL_OUTLINE_PATH}）")
        lines.append("")

    # 正文
    lines.append("#### 正文")
    text = read_text(chapter_path)
    lines.append(text[:6000])  # 上限 6K，防膨胀
    if len(text) > 6000:
        lines.append(f"\n（正文过长，截断至 6K / 共 {len(text)} 字符）")
    lines.append("")
    return "\n".join(lines)


def run_review(scope, report_path, dry_run, client=None):
    """主审查流程。"""
    scope_dir = pick_scope_dir(scope)
    if scope_dir is None or not scope_dir.exists():
        print(f"[chapter_review] 无可用章节目录（scope={scope}）")
        return False, "无章节可审查"

    chapters = sorted(scope_dir.glob("*.md"))
    if not chapters:
        print(f"[chapter_review] {scope_dir} 下无 .md 文件")
        return False, "无章节可审查"

    # 加载滚动摘要
    rolling = load_rolling(ROLLING_PATH) if ROLLING_PATH.exists() else {"global_summary": "", "chapters": {}}

    # 逐章确定性检查
    chapter_data = []
    for chap in chapters:
        try:
            n = int(chap.stem)
        except ValueError:
            continue
        outline_path = OUTLINE_DIR / f"{n:02d}.md"
        det = deterministic_checks(chap, outline_path)
        chapter_data.append({
            "n": n,
            "chapter_path": chap,
            "outline_path": outline_path,
            "det": det,
        })

    if not chapter_data:
        print("[chapter_review] 无有效章节（文件名需为 NN.md）")
        return False, "无有效章节"

    # 构建审查 prompt
    chapters_content_parts = []
    for cd in chapter_data:
        chapters_content_parts.append(
            format_chapter_content(cd["n"], cd["chapter_path"], cd["outline_path"], cd["det"])
        )
    # 附加滚动摘要上下文
    rolling_context = "\n### 滚动摘要（审查衔接时参考）\n"
    if rolling.get("global_summary"):
        rolling_context += f"\n#### 全书摘要\n{rolling['global_summary']}\n"
    if rolling.get("chapters"):
        rolling_context += "\n#### 近期章节摘要\n"
        for no in sorted(rolling["chapters"])[-5:]:
            rolling_context += f"- 第{no}章: {rolling['chapters'][no][:200]}\n"
    chapters_content_parts.append(rolling_context)

    full_content = "\n---\n".join(chapters_content_parts)
    prompt = build_review_prompt(full_content, SETTING_PATH)

    # 写入任务文件
    task_dir = Path("data/state/tasks")
    task_dir.mkdir(parents=True, exist_ok=True)
    task_path = task_dir / "chapter_review_task.md"
    write_text(task_path, prompt)
    print(f"[chapter_review] 任务文件: {task_path}")

    if dry_run:
        print("[chapter_review] --dry-run：未调用 LLM")
        return True, "dry-run（仅任务文件）"

    # 调用 LLM
    if client is None:
        cfg = _load_cfg()
        client = make_client(cfg, "reviewer")

    result = client.run_task(task_path)
    if result["exit_code"] != 0:
        return False, f"LLM 审查失败: {result.get('stdout_tail', '')[:200]}"

    # 读取报告（从 stdout 解析 JSON，兼容 FILE 协议与裸 JSON）
    json_data = _extract_json_from_output(result.get("stdout_tail", ""))
    if json_data is None:
        return False, f"无法从 LLM 输出解析审查 JSON（前 500 字符: {result.get('stdout_tail', '')[:500]}）"

    # 补充元数据
    json_data["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    json_data["scope"] = scope_dir.name
    json_data["deterministic"] = {
        str(cd["n"]): cd["det"] for cd in chapter_data
    }

    # 写入 JSON 报告
    report_path = Path(report_path)
    write_text(report_path, json.dumps(json_data, ensure_ascii=False, indent=2))

    # 渲染 Markdown
    md = render_markdown_report(json_data, chapter_data, scope_dir.name)
    md_path = report_path.with_suffix(".md")
    write_text(md_path, md)
    print(f"[chapter_review] 报告: {report_path} / {md_path}")

    # 摘要
    total = len(json_data.get("chapters", []))
    with_findings = sum(1 for c in json_data.get("chapters", []) if c.get("findings"))
    errors = sum(
        1 for c in json_data.get("chapters", [])
        for f in c.get("findings", []) if f.get("severity") == "error"
    )
    warns = sum(
        1 for c in json_data.get("chapters", [])
        for f in c.get("findings", []) if f.get("severity") == "warn"
    )
    print(f"[chapter_review] {total} 章 | {with_findings} 章有问题 | "
          f"{errors} error / {warns} warn")
    return True, "审查完成"


def _extract_json_from_output(text):
    """从 LLM 输出中提取 JSON，兼容 FILE 协议与裸 JSON。"""
    # 尝试直接解析
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试从 FILE 协议提取
    m = re.search(r"===FILE:.*?\n(.*?)===END===", text, re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 尝试从 ```json 围栏提取
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 尝试找最后一个 { ... } 块
    start = text.rfind('{"chapters"')
    if start >= 0:
        end = text.rfind('}') + 1
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    return None


def render_markdown_report(json_data, chapter_data, scope_name):
    """渲染人可读的 Markdown 报告。"""
    lines = [
        "# 章节审查报告",
        "",
        f"- 生成时间: {json_data.get('generated_at', datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))}",
        f"- 审查范围: {scope_name}/",
        f"- 章节数: {len(json_data.get('chapters', []))}",
        "",
    ]

    # 确定性检查汇总表
    lines.append("## 确定性检查汇总")
    lines.append("")
    lines.append("| 章 | 标题 | 字数 | quality | 文风嫌疑 |")
    lines.append("|----|------|------|---------|---------|")
    for cd in chapter_data:
        det = cd["det"]
        title = (det["title"] or "?")[:15]
        word_mark = "✓" if det["word_ok"] else "✗"
        q = f"{det['quality']:.0f}" if det["quality"] is not None else "-"
        style = f"{len(det['style_issues'])} 项" if det["style_issues"] else "-"
        lines.append(f"| {cd['n']} | {title} | {det['word_count']} {word_mark} | {q} | {style} |")
    lines.append("")

    # 问题清单
    lines.append("## LLM 审查问题")
    lines.append("")
    has_issues = False
    for chap in json_data.get("chapters", []):
        findings = chap.get("findings", [])
        if not findings:
            continue
        has_issues = True
        lines.append(f"### 第 {chap['n']} 章")
        lines.append("")
        for f in findings:
            sev_icon = {"error": "🔴", "warn": "🟡", "info": "🔵"}.get(f.get("severity", "info"), "⚪")
            lines.append(f"- {sev_icon} **{f.get('type', 'other')}**: {f.get('detail', '')}")
            if f.get("suggested_action"):
                lines.append(f"  - 建议: {f['suggested_action']}")
        lines.append("")

    if not has_issues:
        lines.append("无 LLM 标记问题 ✓")
        lines.append("")

    # 下一步提示
    lines.append("## 下一步")
    lines.append("")
    lines.append("审阅上述报告后，运行批量精修：")
    lines.append("```")
    lines.append("python scripts/batch_refine.py --report data/outline/review_report.json")
    lines.append("```")
    lines.append("")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 章节审查（P0 闭环 Phase 1）")
    parser.add_argument("--scope", choices=["raw", "checked", "refined"], default=None,
                        help="审查目录（默认自动检测：refined > checked > raw）")
    parser.add_argument("--report", default="data/outline/review_report.json",
                        help="报告输出路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅生成任务文件，不调用 LLM")
    args = parser.parse_args()

    ok, msg = run_review(args.scope, args.report, args.dry_run)
    print(f"[chapter_review] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
