# -*- coding: utf-8 -*-
"""NovelForge 主调度器（orchestrator）。

职责（架构文档 v2 2.2/2.3）：
- 加载 config/system.yaml + config/project.yaml；
- 初始化 progress.json / runs.db / cost_tracker；
- 按序执行阶段 1→7，跳过已完成（断点续跑）；
- 阶段 2 审批门：未 approved 时下游不启动；
- 预算熔断：cost 状态 pause 时停止全部调度。

用法：
  python scripts/orchestrator.py               # 全流程（断点续跑）
  python scripts/orchestrator.py --from 4      # 从阶段4开始
  python scripts/orchestrator.py --stage 7     # 只跑阶段7

退出码：
  0 = 全部完成
  1 = 阶段失败（gates.pause_on_failure 时暂停）
  2 = 预算熔断
  3 = 等待审批 / 等待审阅
  4 = 用户中断（GUI「停止」）
"""
import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from utils.progress_manager import ProgressManager
from utils.db import RunDB
from utils.cost_tracker import CostTracker
from utils.config_io import load_pipeline_config

import stage1_consolidate as s1
import stage2_outline as s2
import stage3_chapter_outline as s3
import stage4_writing as s4
import stage5_check as s5
import stage6_polish as s6
import stage7_convert as s7
import stage8_markdown_export as s8
import snapshot as snap

STAGES = {1: s1, 2: s2, 3: s3, 4: s4, 5: s5, 6: s6, 7: s7, 8: s8}

# stage 5.5 校对（proofread.py）
def _approval_stages(cfg):
    """本次运行的有效审批门集合。

    逻辑在 `utils.approval_policy.approval_stages`（**单一来源**）—— 这里只是把
    cfg 剥出 gates 的薄包装。原先这套判据在 dry-run 预演 / 实际执行 / 启动横幅
    三处各写一份（外加 nfctl 的待审批统计），改一处忘其余就会
    「预演说会停、实跑不停」或「status 里看不到待审批的 stage1」。
    """
    from utils.approval_policy import approval_stages
    return approval_stages((cfg or {}).get("gates"))


def _run_proofread(cfg, progress):
    """阶段5.5：校对（确定性 + 可选 LLM）。返回 (ok, report_path)"""
    try:
        import proofread as pr
        report_path = "data/outline/proofread_report.json"
        ok, msg = pr.run_proofread(
            scope=None,
            report_path=report_path,
            use_llm=bool(cfg.get("gates", {}).get("proofread_llm", False)),
            dry_run=False,
        )
        if ok:
            progress.set_proofread_report(report_path)
        return ok, msg
    except Exception as e:
        return False, str(e)


def run_material_review(progress):
    """stage1 成功后自动生成素材完备度体检报告（失败不阻断，同 vault_links 模式）。

    返回 (thin_names, warn_names)，供 stage2 审批门提醒使用。
    """
    try:
        import material_review as mr
        char_results, world_items, thin_names, warn_names = \
            mr.review("data/setting/setting.json", "data/setting/normalized")
        out = "data/setting/material_review.md"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        from utils.file_io import write_text
        write_text(out, mr.format_report(char_results, world_items, thin_names, warn_names,
                                         "data/setting/setting.json", "data/setting/normalized"))
        if thin_names:
            print(f"[orchestrator] 素材体检：{len(thin_names)} 个碎片角色"
                  f"（{'、'.join(thin_names)}），报告 → {out}")
        else:
            print(f"[orchestrator] 素材体检：无碎片角色，报告 → {out}")
        return thin_names, warn_names
    except Exception as e:
        print(f"[orchestrator] 素材体检失败（不影响流程）: {e}")
        return [], []


def get_data_root():
    """数据根目录：支持 NOVELFORGE_DATA_DIR 环境变量把 data/ 移到项目外。

    默认返回项目内 data/；设置环境变量后指向外部目录（用户数据与源码分离）。
    """
    env = os.environ.get("NOVELFORGE_DATA_DIR", "").strip()
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path("data")


def load_config():
    """读 (cfg, proj)。**全部走严格 loader**（utils/config_io）。

    2026-10-03：原先三处都是裸 `yaml.safe_load` —— system.yaml / system.local.yaml /
    project.yaml 的重复键会**静默取后值**。止烧阈值（budget.token_limit.*）就在
    system.yaml 里：静默覆盖 = 闸门看着设好了、实际没生效（与 hermes 下 ¥ 记账恒 0
    是同一类"静默失效"）。判定与合并逻辑统一在 config_io.load_pipeline_config。
    """
    return load_pipeline_config()


def _safe_stdout():
    """把 stdout 切到 UTF-8，**让 print 永远不会打死流水线**（实现见 utils/console.py）。

    2026-10-03 实测（本机即复现）：stdout 是**管道**时 Python 用本地编码（cp936），
    而 ¥ / ⚠️ / 💰 / ✅ 这些字符**不在 GBK 里** →
    `UnicodeEncodeError: 'gbk' codec can't encode character '\\xa5'`。
    管道正是 GUI（Electron spawn）与后台任务捕获输出的形态，于是
    `engine: hermes` 下 orchestrator **第一行启动横幅就崩**（实测 dry-run 直接退出码 1）。
    注意：TTY 下 Python 用 UTF-8（PEP 528），所以这个问题只在「被捕获」时才现形 ——
    典型的「本地手跑好好的，GUI 一跑就挂」。

    项目已有同一做法（`nfctl.py` 的 `sys.stdout.reconfigure(encoding="utf-8")`），
    现统一收敛到 `utils.console.ensure_utf8_stdout()`（单一来源）。
    """
    from utils.console import ensure_utf8_stdout
    ensure_utf8_stdout()


def _warn_rewrite_conflict(cfg):
    """auto_rewrite 与 auto_refine 同时开启会改同一批章节 → 启动时明确告警。"""
    gates = cfg.get("gates", {}) or {}
    if gates.get("auto_rewrite") and gates.get("auto_refine"):
        print("[orchestrator] 告警：gates.auto_rewrite 与 gates.auto_refine 同时开启，"
              "两者都会改写同一批章节。")
        print("            以 auto_rewrite 为先；batch_refine 请仅处理审稿报告中的"
              "非 quality 类问题，避免重复改稿。")


def _stop_requested():
    """GUI 是否请求了停止（协作式）。

    延迟导入 nf_api：nf_api 在模块顶层 `from orchestrator import run`，
    顶层反向 import 会成环。CLI 直接跑 orchestrator 时 nf_api 不可导入，
    此处静默降级为「未请求停止」，不影响命令行用法。
    """
    try:
        from nf_api import should_stop
    except Exception:                                       # noqa: BLE001
        return False
    try:
        return bool(should_stop())
    except Exception:                                       # noqa: BLE001
        return False


def should_auto_retry(gates, retry_count, truncated):
    """是否该自动重试。返回 (bool, 原因)。判据只有这一份（便于单测）。

    **截断类失败一律不重试**（2026-10-03）：同一个 `max_tokens` 上限再跑一次，
    只会再截断一次 —— 白烧一倍输入（几万~几十万 token）。这类失败要的是
    "调大 `budget.token_limit.per_request_max_tokens` / 改提示词"，不是再赌一次。
    """
    if not (gates or {}).get("auto_retry", True):
        return False, "auto_retry 已关闭"
    if truncated:
        return False, ("上一次失败是**输出被 max_tokens 截断**（finish_reason=length）"
                       "→ 不重试：同一上限只会再截断一次，白烧一倍输入。处理：调大 "
                       "config/system.yaml 的 budget.token_limit.per_request_max_tokens，"
                       "或减小单次任务量 / 改提示词")
    if retry_count >= int((gates or {}).get("auto_retry_max_rounds", 2)):
        return False, "已达重试轮次上限"
    return True, ""


def run(from_stage=1, only_stage=None, client=None, verbose=False, dry_run=False):
    _safe_stdout()      # 管道 stdout 下 ¥/emoji 会把启动横幅直接搞崩（见其 docstring）
    cfg, proj = load_config()
    _warn_rewrite_conflict(cfg)
    _tl = (cfg.get("budget", {}) or {}).get("token_limit") or {}
    _engine = cfg.get("engine")
    if _engine == "hermes":
        # 刻意不用 U+00A5 的 ¥：它不在 GBK 里（管道 stdout 下必崩）。￥（全角）安全。
        print("[orchestrator] engine=hermes：LLM 走 agent 子会话（订阅流量，model.* 不适用）；"
              "cost 记 0 + 真实 token 留痕 → **￥ 预算熔断对本引擎不生效（刻意语义）**，"
              "止烧靠 token 级熔断")
    if _tl.get("enabled"):
        print(f"[orchestrator] token 级熔断：单请求输出上限="
              f"{_tl.get('per_request_max_tokens') or '不限'}"
              f"（hermes 无该旋钮 → 仅告警" +
              ("并熔断" if _tl.get("per_request_pause_hermes") else "") + "）"
              f"｜本 run 累计上限={_tl.get('max_total_tokens') or '不限'} token")
    else:
        # ⚠️ 本行**不要**出现 U+00A5 的 ¥ / emoji：它们不在 GBK 里，而这一行在
        # 「被捕获的 stdout」（GUI/后台任务）下是 cp936 → 一行日志就能把启动搞崩
        # （本轮实测踩到：engine=direct + 无 token_limit 时启动即 UnicodeEncodeError）。
        # _safe_stdout() 已兜一道，这里再保证字符本身安全（双保险）。
        print("[orchestrator] 注意：token 级熔断未开启（budget.token_limit.enabled=false）"
              "：engine=" + str(_engine) + " 下若金额也不计费（hermes），"
              "则**本轮没有任何止烧闸门**")
    # 误开 auto_rewrite 会在审稿前先跑一轮重写（同一批章节改两遍 = 双倍烧），
    # 启动时把生效值打出来，别让人靠翻 YAML 猜。
    print(f"[orchestrator] gates: auto_rewrite={bool(cfg.get('gates', {}).get('auto_rewrite'))}"
          f" auto_refine={bool(cfg.get('gates', {}).get('auto_refine'))}"
          f" review_after_stage4={bool(cfg.get('gates', {}).get('review_after_stage4'))}"
          f" material_autonomy={bool(cfg.get('gates', {}).get('material_autonomy'))}")
    if verbose:
        os.environ["NOVELFORGE_DEBUG"] = "1"
        print("[orchestrator] verbose：将把每次 LLM 请求/响应原文落盘 data/state/llm_raw/")
        print(f"[orchestrator] verbose：engine={cfg.get('engine')} 预算={cfg.get('budget', {}).get('limit_yuan')}"
              f" 审批门={_approval_stages(cfg)}")
        for k, v in (cfg.get("model") or {}).items():
            print(f"[orchestrator] verbose：model.{k} = {v.get('provider', '?')}/{v.get('id', '?')}")
    progress = ProgressManager("data/state/progress.json")
    # 多书隔离（P0.7 #10）：progress 记录的书名与 project.yaml 不一致时提示
    prev_book = progress.data.get("project", "")
    book_name = proj.get("book", {}).get("name", "")
    if prev_book and prev_book != book_name:
        print(f"[orchestrator] 注意：progress 记录的书名「{prev_book}」"
              f"与 project.yaml「{book_name}」不一致")
        print("             换书请用: python scripts/switch_book.py --archive "
              f"(归档「{prev_book}」) / --restore \"书名\"；继续将按 project.yaml 处理")
    progress.data["project"] = book_name
    db = RunDB("logs/runs.db")
    run_id = None
    finished = False   # 正常路径已显式收尾置 True；兜底据此判断是否需补偿

    def _finalize(status, code):
        """收尾：写 runs 状态 + 标记已完成，返回退出码（幂等）。"""
        nonlocal finished
        try:
            db.finish_run(run_id, status)
        except Exception as e:                              # noqa: BLE001
            print(f"[orchestrator] 警告：写 runs 状态失败（{e}），交由 finally 兜底")
        finished = True
        return code

    try:
        budget = cfg.get("budget", {})
        limit_yuan = float(os.environ.get("BUDGET_LIMIT_YUAN") or budget.get("limit_yuan", 300))
        cost = CostTracker(db, limit_yuan=limit_yuan,
                           warn_ratio=budget.get("warn_ratio", 0.7),
                           # token 级熔断（P0 止烧）：hermes 下 ¥ 恒 0 → 真正的闸门在这里。
                           # 判据仍在 CostTracker.status()（单一来源），此处只做接线。
                           token_limit=budget.get("token_limit"))

        if dry_run:
            # 预演模式：只打印执行计划，不调 LLM、不写产物、不建 runs 记录。
            # 判定逻辑与实跑完全一致（断点/审批门/预算），保证「预演=实跑会走到的路径」。
            print("[orchestrator] === dry-run 预演开始 ===")
            planned = []
            for n in ([only_stage] if only_stage else range(from_stage, 9)):
                if progress.stage_status(n) == "done" and not only_stage:
                    print(f"[dry-run] 阶段{n} 已完成，将跳过")
                    continue
                req = _approval_stages(cfg)
                pending = [s for s in req
                           if n > s and progress.stage_status(s) == "done"
                           and not progress.is_approved(s)]
                if pending:
                    print(f"[dry-run] 阶段{n} 前会停在审批门：{pending}（需先 approve.py --stage "
                          f"{' '.join(map(str, pending))}）")
                    break
                planned.append(n)
                print(f"[dry-run] 阶段{n} 将执行")
            print(f"[dry-run] 计划执行 {len(planned)} 个阶段：{planned or '（无，全部跳过或被审批门拦住）'}")
            print("[dry-run] 预计费用请用 estimate_tokens.py 查看（按历史均值/字符折算）")
            print("[orchestrator] === dry-run 预演结束（未产生任何实际调用/写入）===")
            return 0

        run_id = db.start_run(plan_json=f"from={from_stage} only={only_stage}")

        def _post_stage(n):
            """阶段成功后的后置动作（快照 / 素材体检 / 自动重写 / 审稿门 / 校对）。

            返回退出码：3 = 停下等人工审阅；非 3 非零 = 后置检查失败（审稿门
            fail-closed，P0-2）；None = 继续往下跑。

            ## 为什么抽成函数（2026-10-01）

            原先这整块写在「首次就成功」的 `if ok:` 分支里，于是
            **「首次失败 → 自动重试成功」**的路径会**整块跳过**它 —— 最严重的是
            `review_after_stage4`：本应在 stage4 跑完后停下等人工审稿，被跳过后
            流水线直接冲进 stage5/6，用户根本没机会看那几十章的审查结果。
            而 `auto_retry` 默认开，首次失败是常见路径，不是边角情况。
            """
            # 重跑成功 → 清除打回标记
            st_now = progress.data["stages"].get(str(n), {})
            if st_now.pop("rejected", None) is not None:
                progress.save()
            try:
                snap.snapshot(f"stage{n}_done")  # 阶段成功 → 快照
            except Exception as e:
                print(f"[orchestrator] 快照失败（不影响流程）: {e}")
            if n == 1:
                # P1.5：stage1 归并完成后自动生成素材体检报告（不阻断）
                run_material_review(progress)
                # P1.5-3：auto-thin 闭环（**默认关**）。
                # 会自动改设定集，故必须用户显式开启；
                # 失败不阻断 —— stage2 审批门仍有提醒兜底。
                if (cfg.get("gates", {}) or {}).get("setting_refine_auto", False):
                    try:
                        import setting_refine as srfy
                        max_rounds = int((cfg.get("gates", {}) or {}).get(
                            "setting_refine_max_rounds",
                            srfy.DEFAULT_MAX_ROUNDS))
                        ok_sr, msg_sr, _st = srfy.run_auto_thin(
                            cfg, proj, client=client,
                            task_dir="data/state/tasks",
                            max_rounds=max_rounds)
                        print(f"[orchestrator] 设定补全(auto-thin) 结果: {msg_sr}")
                    except Exception as e:
                        print(f"[orchestrator] 设定补全(auto-thin) 失败（不影响流程）: {e}")
            if n == 4 and (cfg.get("gates", {}) or {}).get("auto_rewrite", False):
                # 方向3 质量自评闭环：重写 quality<阈值 的章节
                # 必须排在 review_after_stage4 审稿分支之前（F5），
                # 使审查看到的已是重写后的章节。失败不阻断流程。
                try:
                    import auto_rewrite as ar
                    ok_ar, msg_ar, _stats = ar.run_auto_rewrite(
                        cfg, proj, progress, db=db, cost=cost, run_id=run_id,
                        client=client, task_dir="data/state/tasks")
                    print(f"[orchestrator] 自动重写结果: {msg_ar}")
                except Exception as e:
                    print(f"[orchestrator] 自动重写失败（不影响流程）: {e}")
            if n == 4 and cfg.get("gates", {}).get("review_after_stage4", False):
                # P0 审稿→修稿闭环：stage4 完成后自动调用审查
                try:
                    import chapter_review as cr
                    print("[orchestrator] stage4 完成，启动章节审查...")
                    ok_rev, msg_rev = cr.run_review(
                        scope=None,
                        report_path="data/outline/review_report.json",
                        dry_run=False,
                        client=client,
                    )
                    print(f"[orchestrator] 审查结果: {msg_rev}")
                    if ok_rev:
                        progress.set_review_report("data/outline/review_report.json")
                        print("[orchestrator] 请审阅 data/outline/review_report.md，"
                              "然后运行: python scripts/batch_refine.py")
                        db.finish_run(run_id, "waiting_review")
                        return 3  # 复用 waiting_approval 语义（等待用户审阅）
                    # P0-2 fail-closed（2026-10-01）：审查跑挂 / JSON 解析失败 = 审稿门
                    # 失守。旧逻辑静默 return None → 流水线直接冲进 stage5，用户根本
                    # 不知道审查没发生。门必须挡住，失败原因必须给人看。
                    print("[orchestrator] 审查失败 —— 审稿门 fail-closed：暂停流水线"
                          "（先修 chapter_review 输出可解析性，再重跑收尾）")
                    return 1
                except Exception as e:
                    # P0-2 fail-closed：异常同样不许静默跳过审稿门
                    print(f"[orchestrator] 审查异常 —— 审稿门 fail-closed，暂停流水线: {e}")
                    return 1
            # stage 5.5：校对（润色后、Word 前）
            if n == 5 and cfg.get("gates", {}).get("proofread_after_polish", False):
                try:
                    print("[orchestrator] stage5 完成，启动校对...")
                    ok_pr, msg_pr = _run_proofread(cfg, progress)
                    print(f"[orchestrator] 校对结果: {msg_pr}")
                    if ok_pr:
                        print("[orchestrator] 请审阅 data/outline/proofread_report.md")
                except Exception as e:
                    print(f"[orchestrator] 校对失败（不影响流程）: {e}")
            return None

        order = [only_stage] if only_stage else range(from_stage, 9)

        def _budget_gate(where=""):
            """阶段边界 / 重试前后的**统一**熔断闸门。返回退出码 2 或 None。

            判据只有一份（`CostTracker.status_detail`）：金额（direct 按量计费）与
            token（engine: hermes 走订阅流量、¥ 记账恒 0 → 真正的闸门是 token）。
            熔断必须**说清是哪条判据命中 + 该调哪个键** —— 否则用户面对
            「预算超限（已用 0.00 元）」只能瞎猜（hermes 下旧日志就是这个形状）。
            """
            detail = cost.status_detail(run_id)
            if detail["state"] != "pause":
                return None
            print(f"[orchestrator] 熔断暂停[{detail['reason']}]{where}：{detail['message']}"
                  f"（累计 token={detail['tokens']}，已用 {detail['spent_yuan']:.2f} 元）")
            print("[orchestrator]   处理：调大 config/system.yaml 的 "
                  "budget.token_limit.max_total_tokens（金额口径则调 budget.limit_yuan）"
                  " → 清 data/state/progress.json 的 budget.paused"
                  " → python scripts/orchestrator.py --from N 续跑（已完成阶段不重跑）")
            progress.data["budget"]["paused"] = True
            progress.save()
            return 2

        for n in order:
            # 用户中断（GUI「停止」）：每轮阶段开始前检查，置位则不再启动新阶段。
            # 已经跑完的阶段照常保留（断点续跑语义不变）。
            if _stop_requested():
                print("[orchestrator] 收到停止请求，中断")
                return _finalize("stopped", 4)  # 退出码 4：用户中断
            # 预算熔断
            _code = _budget_gate(f"（阶段{n} 启动前）")
            if _code is not None:
                return _finalize("paused", _code)
            # 打回提示：该阶段曾被打回（reject.py），重跑前告知原因
            st_n = progress.data["stages"].get(str(n), {})
            if st_n.get("rejected"):
                print(f"[orchestrator] 阶段{n} 曾被打回"
                      f"（{st_n.get('rejected_at', '')}）：{st_n['rejected']}，正在重跑")
            # 审批门：已完成但未人工确认的阶段 → 暂停（判据在 _approval_stages，单一来源）
            req = _approval_stages(cfg)
            pending_approvals = [s for s in req
                                 if n > s and progress.stage_status(s) == "done"
                                 and not progress.is_approved(s)]
            if pending_approvals:
                for s in pending_approvals:
                    if s == 1:
                        print("[orchestrator] 阶段1（素材归并）未获人工确认，暂停 —— "
                              "**陪跑模式**（gates.material_autonomy=false）。")
                        print("[orchestrator]   下一步：① 看冲突清单 "
                              "`GET /setting/conflicts`（或 GUI 素材面板）；"
                              "② 拍板 `nf_set_material_status` 或 "
                              "`material_review.py --reject \"名字\" --reason \"…\"`；"
                              "③ 确认 `approve.py --stage 1`。"
                              "想让它自动继续，把 gates.material_autonomy 设为 true。")
                    elif s == 2:
                        print("[orchestrator] 阶段2 未获人工确认，暂停。"
                              "请审阅 data/outline/global.md 后在主会话确认"
                              "（approve.py --stage 2）")
                    elif s == 6:
                        print("[orchestrator] 阶段6（润色）未获人工确认，暂停。"
                              "请审阅 data/chapters/refined/ 后在主会话确认"
                              "（approve.py --stage 6）；不满意可用"
                              "reject.py --stage 6 \"意见\" 打回重跑")
                    else:
                        print(f"[orchestrator] 阶段{s} 未获人工确认，暂停。"
                              f"（approve.py --stage {s}）")
                # P1.5：审批前若设定集有碎片角色，提醒先补全（可配开关关闭）
                if 2 in pending_approvals and cfg.get("gates", {}).get(
                        "setting_refine_reminder", True):
                    try:
                        import material_review as mr
                        _, _, thin_names, _ = mr.review("data/setting/setting.json",
                                                        "data/setting/normalized")
                        if thin_names:
                            print("[orchestrator] 提醒：设定集存在碎片角色"
                                  f"（{'、'.join(thin_names)}）。"
                                  "建议先审 data/setting/material_review.md，"
                                  "并跑 python scripts/setting_refine.py 补全后再审批。")
                    except Exception as e:
                        print(f"[orchestrator] 补全提醒检查失败（不影响流程）: {e}")
                return _finalize("waiting_approval", 3)
            # 断点：已完成阶段跳过
            if progress.stage_status(n) == "done" and not only_stage:
                print(f"[orchestrator] 阶段{n} 已完成，跳过")
                continue
            print(f"\n[orchestrator] ==== 开始阶段 {n} ====")
            # 安全护栏：阶段执行前快照，执行中崩掉可恢复
            if not only_stage:
                try:
                    snap.snapshot(f"before_stage{n}")
                    print(f"[orchestrator] 阶段{n} 前快照已保存")
                except Exception as e:
                    print(f"[orchestrator] 阶段前快照失败（不影响流程）: {e}")
            # 记录阶段开始时刻：自动重试前用它判定"这次失败之后有没有新的截断标记"
            _stage_started_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            ok, msg = STAGES[n].run_stage(cfg, proj, progress, db, cost, client=client,
                                          task_dir="data/state/tasks", run_id=run_id)
            print(f"[orchestrator] 阶段{n} 结果: {msg}")
            # P0-4：阶段内请求的停止（stage4 章间检查等）立即收尾 —— 不再等下一个
            # 阶段边界，也绝不带着停止继续跑后置钩子（审稿等 LLM 动作）。
            if _stop_requested():
                print("[orchestrator] 阶段后收到停止请求，中断")
                return _finalize("stopped", 4)
            if ok:
                # 后置钩子统一走 _post_stage（快照 / 素材体检 / 自动重写 / 审稿门 / 校对）。
                # 抽成函数的理由见其 docstring：原先写在这一分支里，导致
                # 「首次失败 → 自动重试成功」的路径整块跳过 —— 包括本该停下来
                # 等人工审稿的 review_after_stage4。
                _code = _post_stage(n)
                if _code is not None:
                    # 3 = 等人工审阅；其他非零 = 后置检查失败（审稿门 fail-closed，P0-2）
                    return _finalize("waiting_review" if _code == 3 else "failed", _code)
            if not ok:
                # 自动重试（默认开）：阶段失败后自动重试，减少人工干预。
                # 整段包 try/except：重试是**失败兜底机制**，它自己绝不能成为最脆的一环。
                # 任何异常（含历史存量缺陷）都必须收敛为「退出码 1 + runs 已收尾」，
                # 而不是穿透 run() 把 runs 留在 status='running' 脏状态。
                try:
                    gates = cfg.get("gates", {}) or {}
                    retry_delay = float(gates.get("auto_retry_delay_seconds", 3))
                    retry_backoff = float(gates.get("auto_retry_backoff", 2))
                    retry_count = 0
                    # 截断类失败不重试（见 should_auto_retry）：判据来自
                    # data/state/truncated/last.json —— 本次阶段开始之后有没有新的截断标记。
                    from utils import truncation
                    stage_started = _stage_started_iso
                    while True:
                        _truncated = truncation.after(stage_started)
                        _do, _why = should_auto_retry(gates, retry_count, _truncated)
                        if not _do:
                            if _why:
                                print("[orchestrator] 不自动重试：%s" % _why)
                            break
                        retry_count += 1
                        # 记录重试次数到 progress.json（GUI 可显示"自动重试中"）
                        progress.set_stage(n, "retrying", retry_count=retry_count,
                                           retry_started_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
                        delay = retry_delay * (retry_backoff ** (retry_count - 1))
                        print(f"[orchestrator] 阶段{n} 失败，第{retry_count}次自动重试（等待 {delay:.0f}s）...")
                        time.sleep(delay)
                        # 重试前检查停止请求
                        if _stop_requested():
                            print("[orchestrator] 重试前收到停止请求，中断")
                            return _finalize("stopped", 4)
                        # 重试前检查预算熔断
                        _code = _budget_gate(f"（阶段{n} 重试前）")
                        if _code is not None:
                            return _finalize("paused", _code)
                        # 执行重试
                        ok, msg = STAGES[n].run_stage(cfg, proj, progress, db, cost, client=client, run_id=run_id)
                        print(f"[orchestrator] 阶段{n} 重试结果: {msg}")
                        if ok:
                            break
                except Exception as e:                      # noqa: BLE001
                    print(f"[orchestrator] 自动重试过程异常（已兜底，不中断收尾）: "
                          f"{type(e).__name__}: {e}")
                    ok = False
                if not ok:
                    if cfg.get("gates", {}).get("pause_on_failure", True):
                        print(f"[orchestrator] 阶段失败（已重试 {retry_count} 次），暂停等待处理（可重跑或人工介入）")
                        return _finalize("failed", 1)
                else:
                    # 重试成功 → 后置钩子**必须照跑**（审稿门 / 校对 / 自动重写都在里面）。
                    # 漏了这一步，"重试成功的阶段"就偷偷降级成"只跑了 run_stage"。
                    print(f"[orchestrator] 阶段{n} 重试后成功，补跑后置动作")
                    # 与首次成功路径同语义：停止请求先于后置钩子生效（P0-4）
                    if _stop_requested():
                        print("[orchestrator] 重试成功后收到停止请求，中断")
                        return _finalize("stopped", 4)
                    _code = _post_stage(n)
                    if _code is not None:
                        # 3 = 等人工审阅；其他非零 = 后置检查失败（审稿门 fail-closed，P0-2）
                        return _finalize("waiting_review" if _code == 3 else "failed", _code)
            # 阶段末尾的预算检查（S10 修复）。
            #
            # 此处原先只 `print` 一个状态就往下走 —— 计算了 `state` 却从不据此停机，
            # 是死代码。后果：熔断**只在下一阶段启动前**生效，而最后一个阶段
            # （阶段8 Markdown 导出）跑完后 `state == "pause"` 会被直接吞掉，
            # 最终 `_finalize("done", 0)` 宣告"全部完成"，GUI 显示成功。
            # 实际已超预算，属**静默失败**。现在与阶段起始处的熔断保持同一语义。
            _code = _budget_gate(f"（阶段{n} 结束后）")
            if _code is not None:
                return _finalize("paused", _code)
            _detail = cost.status_detail(run_id)
            print(f"[orchestrator] 当前成本: {_detail['spent_yuan']:.4f} 元"
                  f"（状态 {_detail['state']}）"
                  f"｜本 run 累计 token: {_detail['tokens']}")

        # 件4：质量验收闸门（gates.quality_gate，默认开）。
        #
        # 为什么必须在宣告「全部完成」**之前**跑：
        # 本项目最忌讳的失败是「门禁失守还盖绿灯」—— 明鉴悲剧（五十万字债）就是
        # 完成通知发出后才发现整批章节是垃圾。章节产物存在 ≠ 章节产物合格。
        # 这里复用 quality_checklist.py（确定性、零 token），非零退出即红阻断。
        #
        # 只在**全流程**收尾跑（only_stage 为空）：单阶段重跑（--stage N）时
        # 全书未必齐备，拿整书清单去拦单阶段是误伤。
        if only_stage is None and cfg.get("gates", {}).get("quality_gate", True):
            try:
                import subprocess
                print("\n[orchestrator] 质量验收闸门：运行 scripts/quality_checklist.py ...")
                rc = subprocess.call([sys.executable, "scripts/quality_checklist.py"])
                if rc != 0:
                    print(f"[orchestrator] 🔴 质量验收未通过（退出码 {rc}）→ **阻断完成状态**；"
                          "请按上面的问题清单修复后重跑（可用 --stage N 定点重跑）")
                    return _finalize("failed", 1)
                print("[orchestrator] 🟢 质量验收通过")
            except Exception as e:                              # noqa: BLE001
                # 闸门自身坏掉（脚本缺失/环境异常）→ 大声告警但放行：
                # 已完成的书不该因一次工具故障被判不合格（失败要可解释，不能靠误伤）。
                print(f"[orchestrator] ⚠️ 质量验收闸门执行异常，已放行（请人工核对）: "
                      f"{type(e).__name__}: {e}")

        print("\n[orchestrator] 全部阶段完成 ✅")
        return _finalize("done", 0)
    finally:
        # 兜底幂等补偿：正常路径已 _finalize 过 → 此处的 UPDATE 命中 0 行、无副作用；
        # 异常穿透路径（含未来新增的崩溃点）→ 把残留的 status='running' 脏行收敛为 'crashed'。
        # 读侧（nf_api /costs/streaming）不再需要靠「绕过 running 过滤」来容错。
        if not finished and run_id is not None:
            try:
                if db.finish_run_if_running(run_id, "crashed"):
                    print(f"[orchestrator] 检测到异常退出，已将 run {run_id} 标记为 crashed")
            except Exception as e:                          # noqa: BLE001
                print(f"[orchestrator] 警告：崩溃兜底收尾失败: {e}")
        db.close()


def main():
    parser = argparse.ArgumentParser(description="NovelForge 主调度器")
    parser.add_argument("--from", dest="from_stage", type=int, default=1, help="起始阶段")
    parser.add_argument("--stage", type=int, default=None, help="只运行指定阶段")
    parser.add_argument("--verbose", action="store_true", help="输出详细调试信息（LLM 请求/响应摘要、阶段内部状态）")
    parser.add_argument("--dry-run", action="store_true", help="预演模式：跳过所有 LLM 调用和文件写入，只打印将执行的操作")
    args = parser.parse_args()
    if args.verbose:
        print("[orchestrator] verbose 模式已开启")
    if args.dry_run:
        print("[orchestrator] dry-run 模式已开启（跳过 LLM 调用和文件写入）")
    sys.exit(run(args.from_stage, args.stage, verbose=args.verbose, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
