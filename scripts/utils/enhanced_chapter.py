# -*- coding: utf-8 -*-
"""加强模式：单章拆段落群生成（第一波防御 · 任务三，2026-10-22）。

施工单口径：设置中新增「加强模式」开关，默认关闭；开启后单章生成改为
**章 → 段落 → 段落群**，逐段群生成后拼装成章；字数预算按段群分配下放
（各段群配额之和 = 章节目标字数）。关闭时走原有整章流程，零影响。

## 为什么拆群能提高字数达标率

整章一次性生成 5000~9000 字时，模型的注意力与输出预算摊在超长目标上，
尾段常被草草收束（实测短稿几乎都是"开头正常、后半塌方"）。拆成
2~8 个段群后，每群只背 1500 字左右的配额 —— 单群目标小、可校验、
可单独打回重试，短了立刻知道是哪一群短。

## 段群级 checkpoint（与任务二的"已完成段落落盘"是同一件事）

每群写盘 `data/chapters/_parts/NN/gK.md`（**立即落盘，不等整章**）。
进程被杀 / 断网后重启：已合格的群**跳过重调**，只补写缺的群 ——
整章模式下"段落"粒度无从谈起（LLM 一次写整章，中途死 = 全丢），
拆群之后段落级断点续跑才成立。

## 组装纪律（防双标题 / 双注释）

- 标题：由**第 1 群**任务要求写 `## 第N章 章名`；组装时剥掉所有群里的
  章标题行，只保留第一处 —— 真模型/FakeClient 都可能每群带标题，
  不剥就会拼出 K 个标题（直接撞 verify_chapter 的标题判据）。
- quality / summary 注释：由**最后一群**任务要求写；组装时收集全部群的
  注释行、去重后统一附到章末 —— 同理防 K 份注释。
"""
import math
import re
from pathlib import Path

from utils.file_io import read_text, write_text
from utils.verify_chapter import count_cn_words
from utils.template_loader import load_template

PARTS_DIR = Path("data/chapters/_parts")

TITLE_RE = re.compile(r"^##?\s*第\s*\d+\s*章.*$", re.M)
COMMENT_LINE_RE = re.compile(r"^<!--\s*(quality|summary):.*?-->\s*$", re.M | re.I)

MIN_PART_RATIO = 0.5        # 群产物低于配额 50% 视为不合格（可打回）
GROUP_RETRY = 1             # 单群打回重试上限（整章级打回由 stage4 的循环负责）
DEFAULT_GROUP_WORDS = 1500  # 单群目标字数（决定拆几群）
MIN_GROUPS, MAX_GROUPS = 2, 8


def plan_groups(target_low, target_high, group_words=None):
    """按章节目标字数规划段群配额。返回 [{'idx': 1, 'quota': 1800}, ...]。

    配额之和 == 章节目标字数（取区间中值）—— 施工单「各段群配额之和 =
    章节目标字数」。余数摊给前面的群（先写的角色多担一点，避免最后一群
    拿到零头还要"补满"）。
    """
    group_words = int(group_words or DEFAULT_GROUP_WORDS)
    mid = (int(target_low) + int(target_high)) // 2
    k = max(MIN_GROUPS, min(MAX_GROUPS, int(round(mid / max(400, group_words)))))
    base, rem = divmod(mid, k)
    return [{"idx": i + 1, "quota": base + (1 if i < rem else 0)}
            for i in range(k)]


def parts_dir(n):
    return PARTS_DIR / ("%02d" % n)


def clear_parts(n):
    """作废某章的段群缓存（打回重生成后，盘上的旧群不能再当断点续跑的判据）。"""
    import shutil
    d = parts_dir(n)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def part_path(n, idx):
    return parts_dir(n) / ("g%d.md" % idx)


def part_ok(path, quota):
    """段群产物是否合格（存在 + 字数 >= 配额下限比例）。断点续跑的判据。"""
    p = Path(path)
    if not p.exists():
        return False
    try:
        return count_cn_words(read_text(p)) >= int(quota * MIN_PART_RATIO)
    except Exception:                                     # noqa: BLE001
        return False


def build_group_task(cfg, proj, n, group, groups, prev_tail,
                     outline_path, feedback=None):
    """单个段群的写作任务文本。模板优先，缺失时抛 FileNotFoundError
    （与 stage4_expand 的内置兜底不同：段群是新功能，装机端模板没跟上时
    宁可显式失败，也不要拿劣质兜底文本烧真模型钱）。"""
    book = proj.get("book", {})
    last = group["idx"] == len(groups)
    # 文风块与整章模式同源（stage4._style_blocks：文体 + 形式判据裁剪），
    # 惰性导入避免与 stage4_writing 的循环依赖。
    from stage4_writing import _style_blocks
    style_rules, para_rules, _form_app = _style_blocks(proj)
    variables = {
        "n": n,
        "g": group["idx"],
        "k": len(groups),
        "quota": group["quota"],
        # 区间形态同时服务两处：真模型拿它当目标带宽；FakeClient 按
        # 「字数 X-Y 字」正则取目标字数（夹具兼容，勿改成裸数字）。
        "quota_low": int(group["quota"] * 0.85),
        "quota_high": int(group["quota"] * 1.15),
        "is_first": "是" if group["idx"] == 1 else "否",
        "is_last": "是" if last else "否",
        "style_rules": style_rules or "（按项目默认文风）",
        "para_rules": para_rules or "",
        "title_line": ("## 第%d章 章节名（贴合本章大纲拟定）\n\n" % n
                       if group["idx"] == 1 else ""),
        "comment_lines": ("\n正文最后另起两行写：\n"
                          "<!-- quality: X/10 -->（对**整章**的自评分）\n"
                          "<!-- summary: 本章关键事件与角色状态摘要（约100字） -->\n"
                          if last else ""),
        "book_name": book.get("name", "未命名"),
        "prev_tail": prev_tail or "（本章第一段群，上接上一章结尾）",
        "path_outline": Path(outline_path).resolve(),
        "path_setting": Path("data/setting/setting.json").resolve(),
        "path_output": part_path(n, group["idx"]).resolve(),
        "feedback": (("## 上一段群校验反馈（本轮必须修复）\n" + feedback)
                     if feedback else ""),
    }
    _, body = load_template("stage4_group.md", variables)
    return body


def _strip_parts(parts):
    """组装前清洗：标题只留第一处、注释行收集去重、群间空行分隔。"""
    titles, comments, bodies = [], [], []
    seen_comments = set()
    for text in parts:
        for m in COMMENT_LINE_RE.finditer(text):
            line = m.group(0).strip()
            if line not in seen_comments:
                seen_comments.add(line)
                comments.append(line)
        body = TITLE_RE.sub("", text)
        body = COMMENT_LINE_RE.sub("", body)
        body = body.strip()
        if body:
            bodies.append(body)
        for m in TITLE_RE.finditer(text):
            t = m.group(0).strip()
            if t not in titles:
                titles.append(t)
    return titles, comments, "\n\n".join(bodies)


def assemble(n, out_path):
    """把段群产物拼装成章。返回 (标题行 or '', 组装是否完整)。"""
    d = parts_dir(n)
    files = sorted(d.glob("g*.md")) if d.exists() else []
    if not files:
        return "", False
    parts = [read_text(f) for f in files]
    titles, comments, body = _strip_parts(parts)
    head = (titles[0] + "\n\n") if titles else ("## 第%d章 章节名（待补）\n\n" % n)
    tail = ("\n\n" + "\n".join(comments)) if comments else ""
    write_text(out_path, head + body + tail + "\n")
    return head.strip(), bool(titles) and bool(comments)


def write_chapter_groups(cfg, proj, n, client, task_dir,
                         outline_path, prev_tail, run_hook=None,
                         chapter_feedback=None):
    """加强模式写一章：逐段群生成 → 立即落盘 → 拼装。

    run_hook(task_path) -> result dict：由调用方注入（stage4 在里面做
    记账/熔断检查）；返回 result 的 exit_code != 0 时中止后续群。
    缺省直接 client.run_task（测试/独立调用用）。
    chapter_feedback：整章级打回反馈（stage4 的重生成循环注入），
    下传给**每个**段群任务 —— 整章不过关时往往每群都有份。
    """
    book_cfg = cfg.get("chapter", {})
    low, high = book_cfg.get("target_words", [2000, 3000])[:2]
    groups = plan_groups(low, high, book_cfg.get("enhanced_group_words"))
    d = parts_dir(n)
    d.mkdir(parents=True, exist_ok=True)

    agg = {"tokens": 0, "tokens_out": 0, "requests": 0,
           "provider": getattr(client, "provider", "unknown"),
           "model": getattr(client, "model", "unknown"), "exit_code": 0}
    run = run_hook or (lambda p: client.run_task(p))

    for group in groups:
        pp = part_path(n, group["idx"])
        if part_ok(pp, group["quota"]):
            # 段群级断点续跑：上一轮已落盘且合格的群不重调（零重复零丢失）
            print(f"[enhanced] 第{n}章 群{group['idx']}/{len(groups)}"
                  f" 已在盘且合格，跳过重调")
            continue
        feedback = None
        for attempt in range(1, GROUP_RETRY + 2):
            _fb_parts = [x for x in (chapter_feedback, feedback) if x]
            task = client.write_task(
                task_dir, "stage4_ch%02d_g%d.md" % (n, group["idx"]),
                build_group_task(cfg, proj, n, group, groups, prev_tail,
                                 outline_path,
                                 feedback="\n".join(_fb_parts) or None))
            result = run(task)
            agg["tokens"] += int(result.get("tokens") or 0)
            agg["tokens_out"] += int(result.get("tokens_out") or 0)
            agg["requests"] += int(result.get("requests") or 1)
            if result.get("exit_code") != 0:
                agg["exit_code"] = result["exit_code"]
                agg["error"] = ("段群 %d 生成失败（已落盘的群保留，"
                                "重启后从断点续跑）" % group["idx"])
                return agg
            if part_ok(pp, group["quota"]):
                break
            got = count_cn_words(read_text(pp)) if pp.exists() else 0
            feedback = ("本群要求 %d 字，实得 %d 字（低于配额 %d%%）。"
                        "请写满配额再落盘。"
                        % (group["quota"], got, int(MIN_PART_RATIO * 100)))
            print(f"[enhanced] 第{n}章 群{group['idx']} 字数 {got}"
                  f" < 配额 {group['quota']}×{MIN_PART_RATIO:.0%} —— 打回重写（{attempt}/{GROUP_RETRY + 1}）")
        else:
            print(f"[enhanced] ⚠ 第{n}章 群{group['idx']} 重试后仍不足，"
                  "按现状拼装（整章校验会兜底）")

    chap = Path("data/chapters/raw") / ("%02d.md" % n)
    title, complete = assemble(n, chap)
    print(f"[enhanced] 第{n}章 拼装完成：{len(groups)} 群 → {chap}"
          f"（标题={'有' if title else '缺'}）")
    return agg
