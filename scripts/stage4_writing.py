# -*- coding: utf-8 -*-
"""阶段 4：逐章写作（核心链路）。

每章一次独立子会话，上下文 = 本章大纲 + 设定集（文件引用，子会话按需读取）
+ 滚动摘要 rolling.md + 上一章末尾衔接段（v2 4.4）。

完成后：verify_chapter 校验 → quality<阈值 标记 needs_rewrite（P0 不重写）
→ 提取 summary 注释 append 到 rolling.md → 更新 progress/db/cost。
"""
import argparse
import json
import re
from pathlib import Path

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.cost_tracker import pause_detail as _pause_detail
from utils.verify_chapter import check_chapter, count_cn_words, is_chapter_complete
from utils.summary_chain import append_chapter_summary, extract_prev_tail, compress_recent, load_rolling, write_rolling
from utils.template_loader import load_template
from utils.style_analyzer import extract_style_samples, build_style_notes_section
from utils.progress_manager import ProgressManager
from utils.setting_schema import (
    base_name, build_character_card, normalize_character,
)

SUMMARY_RE = re.compile(r"<!--\s*summary:\s*(.+?)\s*-->", re.IGNORECASE | re.S)

# 知识库注入的「一次性提示」开关。
# `build_chapter_task` 每章调一次，逐章打印同一条提示会淹没日志；
# 但**完全不提示**又是静默失败（用户以为 KB 生效了）。折中：每进程提示一次。
_KB_NOTICE = {"shown": False}


def _kb_notice_once(msg):
    """同一条 KB 提示每进程只打印一次。"""
    if not _KB_NOTICE["shown"]:
        print(msg)
        _KB_NOTICE["shown"] = True


def _stop_requested():
    """GUI/外部是否请求停止（与 orchestrator._stop_requested 同语义）。

    P0-4（2026-10-01）：orchestrator 只在**阶段边界**查停止，而 stage4 一阶段
    可能是几十章 —— 不在章间查，按了「停止」也要跑完整个阶段才生效。
    延迟导入 nf_api 防循环依赖（nf_api 顶层 import orchestrator，orchestrator
    模块级 import 本文件）；CLI 独立跑本文件时 nf_api 不可导入 → 降级「未请求」。
    """
    try:
        from nf_api import should_stop
    except Exception:                                       # noqa: BLE001
        return False
    try:
        return bool(should_stop())
    except Exception:                                       # noqa: BLE001
        return False


def _safe_read(path, budget=20000):
    try:
        return read_text(path)[:budget]
    except Exception:
        return ""


def sync_appearances_after_chapter(chapter_text, chapter_no, total_chapters=None):
    """章节完成后同步出场记录（确定性，零 LLM）。

    两路都做，任何异常只告警不阻断流水线（出场记录属辅助产物）：
    1) obsidian_integrate.sync_chapter_appearances → setting.json 的 appearances 字段（幂等）
    2) appearances.refresh_appearances → data/state/appearances.json（全量重算，逐章精确）
    返回 True 表示 appearances.json 已刷新。
    """
    try:
        from obsidian_integrate import sync_chapter_appearances
        sync_chapter_appearances(chapter_text, chapter_no)
    except Exception as e:      # noqa: BLE001
        print("[stage4] [warn] 出场记录同步失败（setting.json，不影响流程）: "
              + type(e).__name__ + ": " + str(e)[:160])
    try:
        from appearances import refresh_appearances
        path, stats, total, _new = refresh_appearances(chapter_count=total_chapters)
        if path:
            print("[stage4] 出场记录已更新: " + str(path)
                  + "（" + str(len(stats)) + " 个角色 / " + str(total) + " 章）")
            return True
        print("[stage4] [warn] 出场记录暂无可统计内容（缺设定集角色或章节），已跳过")
    except Exception as e:      # noqa: BLE001
        print("[stage4] [warn] appearances.json 刷新失败（不影响流程）: "
              + type(e).__name__ + ": " + str(e)[:160])
    return False


def _involved_names(outline_text):
    """从逐章大纲的「涉及角色」行解析角色名列表。

    大纲里该项可能是 `苏芷、观澜`，也可能是 `luxi 苏芷（写病历）、小林（夜班护士）`。
    后者若整串拿去匹配 setting 的 name 永远匹配不上 → 角色卡恒为空。
    这里把「id 名（描述）」拆成候选名并剥掉括号描述。
    """
    rm = re.search(r"涉及角色[：:]\s*(.+)", outline_text)
    if not rm:
        return []
    names = []
    for chunk in re.split(r"[,，、/；;]", rm.group(1)):
        s = chunk.strip()
        if not s:
            continue
        # `luxi 苏芷（写病历）` → 先取描述括号内的内容当别名候选，再剥括号
        for inside in re.findall(r"[（(]([^）)]*)[）)]", s):
            inside = inside.strip()
            if inside:
                names.append(inside)
        bare = re.sub(r"[（(].*?[)）]", "", s).strip()
        # `luxi 苏芷` → 拆出 ASCII id 与中文名
        for tok in bare.split():
            tok = tok.strip()
            if tok:
                names.append(tok)
    out, seen = [], set()
    for n in names:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _extract_character_cards_for_chapter(setting_path, outline_path, budget=3000):
    """从 setting.json 提取当前章节涉及的角色卡片段。

    匹配逻辑：从大纲解析「涉及角色」，与 setting.json 的 characters 逐个匹配，
    返回格式化的角色卡文本（供任务文件注入）。

    **schema 兼容**：setting.json 有**两个生产者**，字段集不同 ——
    stage1（真实流水线）产出 `role/traits/relations`，vault 联动产出
    `type/tags/snippet/locked`。本函数此前只认后者，导致真实流水线下
    「身份」与「性格」全丢。现统一走 `utils.setting_schema.normalize_character`，
    两套都能读全（详见该模块 docstring 的字段对照表）。
    """
    try:
        setting = json.loads(read_text(setting_path))
        raw_characters = setting.get("characters", [])
        if not raw_characters:
            return ""

        involved = _involved_names(_safe_read(outline_path, budget=8000))
        if not involved:
            return ""

        cards = []
        for char in raw_characters:
            nc = normalize_character(char)
            if not nc:
                continue
            # 精确名 / id / 别名根名 三种匹配都认
            if not _name_matches(nc, involved):
                continue
            cards.append(build_character_card(nc))

        if not cards:
            return ""
        return ("### 本章涉及角色卡（写作时严格遵循，禁止违背性格/关系/禁止行为）\n\n"
                + "\n\n".join(cards))
    except Exception:
        return ""


def _name_matches(nc, involved):
    """角色是否落在大纲「涉及角色」名单里（容忍别名与括号描述）。"""
    names = {nc["name"], nc.get("id", "")}
    base = base_name(nc["name"])
    if base:
        names.add(base)
    for inv in involved:
        if not inv:
            continue
        if inv in names or base_name(inv) == base and base:
            return True
        # 描述性括注：`苏芷（写病历）` 已在上层拆出，这里再容忍一次包含关系
        if len(inv) >= 2 and (inv in nc["name"] or nc["name"] in inv):
            return True
    return False


def build_chapter_task(cfg, proj, n, outline_path, setting_path, rolling_path, prev_tail):
    target = cfg.get("chapter", {}).get("target_words", [2000, 3000])
    tail_section = "\n\n".join(prev_tail) if prev_tail else "（无上一章，本章为开篇）"
    book = proj.get("book", {})

    # 风格注入
    style_ref = (book.get("style_reference") or "").strip()
    style_text = read_text(style_ref).strip()[:6000] if style_ref and Path(style_ref).exists() else "（未提供，请严格按下方文风要求写作）"
    style_samples = ""
    if style_ref:
        current_text = "\n".join([tail_section, _safe_read(outline_path)])
        style_samples = extract_style_samples(style_ref, current_text=current_text)
    style_notes = build_style_notes_section(book.get("style_notes", ""))

    # P2.4 知识库上下文注入（使用 obsidian_bridge，只读 + 沙盒写入）
    #
    # ⚠️ 2026-09-21 修复**静默降级**：此前是裸 `except Exception: pass` ——
    # vault 未配置时 `scan_vault()` 抛 ValueError 被直接吞掉，用户配了路径但
    # 目录结构不符 / 路径写错时同样无声无息，KB 注入恒为空而**没有任何提示**。
    # 用户读到的是"正常写作"，实际正典上下文从未生效。
    #
    # 现按项目纪律区分三态（未启用 / 未命中 / 失败），每种都有明确输出：
    #   - 未配置 vault   → **未启用**（不是失败），提示一次
    #   - 已配置但未命中 → 提示一次（可能是章节涉及角色名与词条名对不上）
    #   - 抛异常         → **失败**，必须打印，不能吞
    kb_context = ""
    try:
        from obsidian_bridge import scan_vault, inject_context, is_vault_configured
        if not is_vault_configured():
            _kb_notice_once(
                "[stage4] 提示：未配置 obsidian.vault_path，本次写作**未启用**知识库"
                "词条注入（属未启用，非失败）。配置后可在 GUI「设置」页填写本地路径。")
        else:
            vault_data = scan_vault()
            outline_text = _safe_read(outline_path, budget=8000)
            rm = re.search(r"涉及角色[：:]\s*(.+)", outline_text)
            query = rm.group(1)[:100] if rm else outline_text[:100]
            kb_context = inject_context(query, vault_data=vault_data, top_k=5)
            if kb_context:
                kb_context = "### 相关正典词条（写作时参考，locked 条目不可违逆）\n\n" + kb_context
            else:
                _kb_notice_once(
                    "[stage4] 提示：vault 已配置但未匹配到相关词条 —— 未注入 KB 上下文"
                    "（可能是章节「涉及角色」行缺失，或词条名与角色名不一致）。")
    except Exception as e:                       # noqa: BLE001
        print(f"[stage4] ⚠ 知识库注入失败（本章将无 KB 正典上下文）: "
              f"{type(e).__name__}: {e}")

    # 角色卡锁定：从 setting.json 提取当前章节涉及的角色
    character_cards = _extract_character_cards_for_chapter(setting_path, outline_path)

    _, body = load_template("stage4_writing.md", {
        "n": n,
        "book_name": book.get("name", "未命名"),
        "path_outline": Path(outline_path).resolve(),
        "path_setting": Path(setting_path).resolve(),
        "path_rolling": Path(rolling_path).resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "prev_tail": tail_section,
        "style_reference": style_text,
        "style_samples": style_samples,
        "style_notes": style_notes,
        "kb_context": kb_context,
        "character_cards": character_cards,
        "min_words": target[0],
        "max_words": target[1],
        "path_output": Path(f"data/chapters/raw/{n:02d}.md").resolve(),
    })
    return body


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
    client = client or make_client(cfg, "writer")
    task_dir = task_dir or "data/state/tasks"
    total = int(proj.get("book", {}).get("chapters", 10))
    raw_dir = Path("data/chapters/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    rolling_path = Path("data/summaries/rolling.md")
    quality_threshold = int(cfg.get("chapter", {}).get("quality_threshold", 6))
    max_recent = int(cfg.get("chapter", {}).get("summary_every", 5))

    completed = progress.completed_chapters(4)
    # 断点续跑：只保留文件真正完成的章节（防止半成品被跳过）
    verified = [n for n in completed if is_chapter_complete(raw_dir / f"{n:02d}.md", *cfg.get("chapter", {}).get("target_words", [2000, 3000]))]
    skipped = set(completed) - set(verified)
    if skipped:
        print(f"[stage4] 半成品章节重跑（文件存在但校验未通过）: {sorted(skipped)}")

    failed = progress.failed_chapters(4)
    failed_ns = {f["n"] for f in failed}
    todo = [n for n in range(1, total + 1) if n not in verified or n in failed_ns or n in skipped]

    if not todo:
        progress.set_stage(4, "done")
        return True, "stage4 跳过（已完成）"

    progress.set_stage(4, "running")
    low, high = cfg.get("chapter", {}).get("target_words", [2000, 3000])[:2]
    # 连续失败止损阈值（0 = 关闭）。见 _fail 的说明。
    max_consec_fail = int(cfg.get("gates", {}).get("stage4_max_consecutive_failures", 3))
    consec_fail = 0
    aborted = ""
    done_this_round = 0

    def _fail(n, reason, extra=()):
        """记一次章节失败；连续失败达上限 → 返回 True（要求提前止损）。

        ## 为什么要止损（2026-10-01 新增）

        单章失败继续跑是合理的（偶发）。但**连续**失败几乎必然是系统性问题
        —— 模型不可用、提示词被改坏、输入构造出错。继续跑下去只会把废稿铺满
        `data/chapters/raw/`：100 章跑完只在最后报一句"有 100 章失败"，
        而这些半成品/废稿已经在盘上了。已完成的章节不受影响，修好后重跑
        会经断点续跑跳过它们。
        """
        nonlocal consec_fail
        progress.mark_chapter_failed(n, reason, 4)
        consec_fail += 1
        for line in extra:
            print(line)
        if max_consec_fail > 0 and consec_fail >= max_consec_fail:
            print(f"[stage4] ⚠ 连续 {consec_fail} 章失败 —— 判为系统性问题"
                  "（模型 / 提示词 / 输入），提前停止本阶段")
            print("[stage4]    已完成的章节不受影响；修好后重跑会从断点继续")
            return True
        return False

    # B2：章间会话续接（默认关）—— 只有「引擎开了续接」且「上一章成功」时才传下去
    chapter_session = None
    for n in todo:
        # P0-4（2026-10-01）：章间检查停止请求 —— 不在这里查，按了「停止」
        # 也要跑完整个阶段（几十章）才轮到 orchestrator 的阶段边界检查。
        if _stop_requested():
            print(f"[stage4] 收到停止请求，停在第{n}章开始前"
                  "（已完成章节保留，断点续跑不重写）")
            aborted = "stopped"
            break
        chap_path = raw_dir / f"{n:02d}.md"
        prev_path = raw_dir / f"{n - 1:02d}.md"
        prev_tail = extract_prev_tail(prev_path, 4) if prev_path.exists() else []
        task = client.write_task(task_dir, f"stage4_ch{n:02d}.md",
                                 build_chapter_task(cfg, proj, n,
                                                    f"data/outline/chapters/{n:02d}.md",
                                                    "data/setting/setting.json",
                                                    "data/summaries/rolling.md",
                                                    prev_tail))
        # B2：session_id 只在引擎开启续接时才有意义（direct 客户端收下但忽略）
        result = client.run_task(task, session_id=chapter_session)
        cost_est = (cost.estimate_cost_yuan(result["tokens"], result["tokens_out"],
                                            model=result.get("model"),
                                            provider=result.get("provider"),
                                            role=result.get("model_key"),
                                            cache_read=result.get("cache_read", 0))
                    if cost else 0)
        if result["exit_code"] != 0:
            if _fail(n, "子会话退出码非零", [f"[stage4] 第{n}章子会话失败"]):
                aborted = "consecutive_failures"
                break
            continue

        if not chap_path.exists():
            if _fail(n, "章节文件未生成", [f"[stage4] 第{n}章未生成章节文件"]):
                aborted = "consecutive_failures"
                break
            continue

        check = check_chapter(chap_path, low, high)
        if not check.ok:
            # 退化（正文够长但内容是垃圾）单独标注：这类失败与「字数不足」
            # 的处置不同 —— 重试往往没用，多半是模型/提示词层面出了问题。
            if check.degenerate:
                detail = "正文退化: " + "; ".join(check.degenerate)
                extra = [f"[stage4] 第{n}章正文退化（字数 {check.word_count} 达标但内容无效）:"]
                extra += [f"          - {d}" for d in check.degenerate]
                extra.append(f"          指标: {check.degen_metrics}")
                hit = _fail(n, detail, extra)
            else:
                hit = _fail(n, "校验失败: " + "; ".join(check.errors),
                            [f"[stage4] 第{n}章校验失败: {check.errors[:2]}"])
            if hit:
                aborted = "consecutive_failures"
                break
            continue

        # 本章成功 → 连败计数清零（"连续"才说明系统性问题）；本轮完成数同步 +1，
        # 这样熔断/止损消息里的"本轮完成 N 章"才是准的
        consec_fail = 0
        done_this_round += 1
        # B2：只把**成功**章节的 session 传下去 —— 失败章节的子会话没有产出，
        # 把它的上下文接给下一章只会把噪音带过去
        if result.get("session_id") and getattr(client, "session_continuation", False):
            chapter_session = result["session_id"]

        # 摘要提取 + 滚动维护
        text = read_text(chap_path)
        sm = SUMMARY_RE.search(text)
        summary = sm.group(1).strip() if sm else f"（第{n}章，未附摘要）"
        res = append_chapter_summary(rolling_path, n, summary, max_recent=max_recent)

        # quality 标记（P0 只标记不重写）
        needs_rewrite = check.quality is not None and check.quality < quality_threshold
        status = "needs_rewrite" if needs_rewrite else "ok"
        progress.mark_chapter_done(n, 4)
        if needs_rewrite:
            progress.data["stages"]["4"].setdefault("needs_rewrite", [])
            if n not in progress.data["stages"]["4"]["needs_rewrite"]:
                progress.data["stages"]["4"]["needs_rewrite"].append(n)
            progress.save()
        if db and run_id:
            db.log_chapter(run_id, 4, n, status, quality=check.quality, cost_yuan=cost_est)
        if cost:
            # ⚠️ 章与章之间必须查熔断：orchestrator 只在**阶段之间**检查预算，
            # 而 stage4 一章就能烧掉几元 —— 不在这里拦，300 元限额对
            # "一次跑 100 章"这种最需要保护的场景恰好**完全失效**。
            state = cost.charge_cost(run_id, 4, n, result)
            if state == "pause":
                # 熔断原因由 CostTracker.status_detail 给出（金额 or token）——
                # engine: hermes 下 ¥ 恒 0，真正会命中的是 token 累计，文案必须对上。
                _detail = _pause_detail(cost, run_id)
                print(f"[stage4] 💰 熔断停止[{_detail['reason']}]：{_detail['message']}"
                      "—— 停止本阶段")
                progress.data.setdefault("budget", {})["paused"] = True
                progress.save()
                aborted = "budget"
                break

        # 出场记录同步（P1.6：章节完成即更新 data/state/appearances.json；
        # 失败只告警，绝不阻断写作主线）
        sync_appearances_after_chapter(text, n, total)
        print(f"[stage4] 第{n}章完成 {check.summary()}")

    if aborted:
        remaining_failed = progress.failed_chapters(4)
        progress.set_stage(4, "failed", failed_count=len(remaining_failed))
        if aborted == "stopped":
            # 用户主动中断 ≠ 章节失败：只报中断事实（连败止损文案不适用）
            return False, ("stage4 被**用户中断**（本轮完成 %d 章）—— "
                           "断点续跑不会重写已完成章节" % done_this_round)
        if aborted == "budget":
            return False, ("stage4 因**预算熔断**停止（本轮完成 %d 章，累计失败 %d 章）。"
                           "调大 config/system.yaml 的 budget.token_limit.max_total_tokens"
                           "（金额口径则调 budget.limit_yuan）或缩小范围后重跑；"
                           "断点续跑不会重写已完成章节"
                           % (done_this_round, len(remaining_failed)))
        return False, ("stage4 **提前止损**（连续 %s 章失败，本轮完成 %d 章）—— "
                       "先排查模型 / 提示词 / 输入，再重跑；"
                       "断点续跑不会重写已完成章节"
                       % (max_consec_fail, done_this_round))

    remaining_failed = progress.failed_chapters(4)
    if remaining_failed:
        progress.set_stage(4, "failed", failed_count=len(remaining_failed))
        return False, f"stage4 有 {len(remaining_failed)} 章失败"
    progress.set_stage(4, "done")
    return True, f"stage4 完成（{total} 章）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段4：逐章写作")
    parser.add_argument("--task-dir", default="data/state/tasks")
    args = parser.parse_args()
    import yaml
    cfg = yaml.safe_load(read_text("config/system.yaml"))
    proj = yaml.safe_load(read_text("config/project.yaml"))
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(cfg, proj, progress, None, None, task_dir=args.task_dir)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
