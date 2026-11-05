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
import threading
import time
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.cost_tracker import pause_detail as _pause_detail
from utils.verify_chapter import check_chapter, count_cn_words, is_chapter_complete
from utils.summary_chain import append_chapter_summary, extract_prev_tail, compress_recent, load_rolling, write_rolling
from utils.template_loader import load_template
from utils.writing_rules import block as _writing_rules_block


def _dialogue_ratio_block(cfg):
    """对话段占比上限（2026-11-05，试跑第 26 条）：接在作品约束后面。

    失败不炸整章（作者没配就用默认 35%，配坏了就当没有）。
    """
    try:
        from utils.writing_rules import numeric_block
        return numeric_block(cfg)
    except Exception:                                          # noqa: BLE001
        return ""


from utils.style_analyzer import (
    MIN_TEXT_LEN as STYLE_REF_MIN_PROSE, extract_style_samples, build_style_notes_section,
    prose_char_count,
)
from utils.progress_manager import ProgressManager
from utils.setting_schema import (
    base_name, build_character_card, normalize_character,
)
from utils.config_io import load_config_yaml
from utils.text_norm import normalize_chapter_file
from utils import intervene
from utils import error_contract

SUMMARY_RE = re.compile(r"<!--\s*summary:\s*(.+?)\s*-->", re.IGNORECASE | re.S)

# progress.json 的写入锁（2026-10-27）：文件进度看门狗是**独立线程**，与
# stream 回调 `_on_piece` 可能同时 save()。不加锁会两个线程各写整份 JSON，
# 轻则丢一次更新，重则写出半截文件。粒度只包住“改字段 + save”。
_PROGRESS_LOCK = threading.Lock()

# 范文注入上限（字）。6000 是**注入成本与收益的拐点**：few-shot 只抽 3 段/≤900 字，
# 原文超出的部分只有 stage6 的统计用得上，而每条章节任务都要为它付 token。
# 真实作品越写越长，所以从这里改成可配（book.style_reference_max_chars），
# **且超长必须出声** —— 静默截断会让用户以为"整章都喂进去了"（2026-10-19）。
STYLE_REF_LIMIT_DEFAULT = 6000
STYLE_REF_LIMIT_MIN = 1000
STYLE_REF_LIMIT_MAX = 40000
STYLE_REF_PLACEHOLDER = "（未提供，请严格按下方文风要求写作）"


def _style_ref_limit(book):
    """读注入上限，越界夹到 [1000, 40000]，坏值回落默认。"""
    raw = (book or {}).get("style_reference_max_chars", STYLE_REF_LIMIT_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = STYLE_REF_LIMIT_DEFAULT
    return max(STYLE_REF_LIMIT_MIN, min(STYLE_REF_LIMIT_MAX, n))


def build_style_reference_block(book):
    """范文 → 注入文本。返回 (text, meta)。

    两件事都必须**出声**（项目纪律：静默降级比没有更糟）：
      1. 配了路径但文件不存在 → WARN，按"未提供"写作；
      2. 原文超过注入上限 → WARN，写明原文字数 / 实际注入 / 被丢弃的字数。

    meta 供自检与测试读：{configured, exists, chars, injected, limit, truncated}。
    """
    ref = str((book or {}).get("style_reference") or "").strip()
    meta = {"configured": bool(ref), "exists": False, "chars": 0,
            "injected": 0, "limit": _style_ref_limit(book), "truncated": 0}
    if not ref:
        return STYLE_REF_PLACEHOLDER, meta
    if not Path(ref).exists():
        print("[stage4] WARN style_reference 指向的文件不存在：%s —— 本次按“未提供范文”写作"
              % ref)
        return STYLE_REF_PLACEHOLDER, meta
    text = read_text(ref).strip()
    meta["exists"] = True
    meta["chars"] = len(text)
    # 只有占位符/标题 = 作者还没粘正文。**不能**把占位说明当范文贴进每章任务
    # （它会进统计特征与 few-shot 候选池），所以按"未提供"处理并出声。
    prose = prose_char_count(text)
    if prose < STYLE_REF_MIN_PROSE:
        print("[stage4] WARN 范文文件 %s 里只有占位符/标题（有效正文 %d 字，少于 %d）"
              "—— 本次按“未提供范文”写作，请把正文粘进去"
              % (ref, prose, STYLE_REF_MIN_PROSE))
        meta["injected"] = 0
        return STYLE_REF_PLACEHOLDER, meta
    limit = meta["limit"]
    if len(text) > limit:
        meta["truncated"] = len(text) - limit
        print("[stage4] WARN 范文 %d 字，超过注入上限 %d 字，末尾 %d 字**不会注入**"
              "（要全量注入就调 config/project.yaml 的 book.style_reference_max_chars，"
              "范围 %d~%d）"
              % (len(text), limit, meta["truncated"],
                 STYLE_REF_LIMIT_MIN, STYLE_REF_LIMIT_MAX))
        text = text[:limit]
    meta["injected"] = len(text)
    return text, meta


def _norm(path, n=None):
    """就地归一化章节文件的中文标点（半角引号/直角引号/单破折号/省略号…）。

    幂等、零 token。失败只打印不阻断 —— 标点归一化是**清洁**工作，
    它坏了不应该拖垮写作主链路（正文还在，脏标点最多回到修复前的状态）。
    """
    try:
        changed, counts = normalize_chapter_file(path)
    except Exception as e:                                    # noqa: BLE001
        print(f"[text_norm] 第{n}章标点归一化失败（不影响主流程）: {type(e).__name__}: {e}")
        return
    if changed:
        total = sum(counts.values())
        detail = "、".join(f"{k}={v}" for k, v in counts.items() if v)
        print(f"[stage4] 第{n}章标点归一化 {total} 处（{detail}）" if n
              else f"[text_norm] 归一化 {total} 处（{detail}）")


def _state_report(path, n=None):
    """一致性账本增量更新（P3，2026-10-19）：确定性、零 token、只报不拦。

    每章落盘后把这一章的事件并进 `data/state/story_state.json`，再打印
    **本章**新增的冲突（死而复生 / 重复死亡 / 物品复现 / 时间倒退）。
    账本是跨章累计的：判"第 3 章出现了第 2 章死的人"必须有前面章节的账，
    单章看不出来。

    只打印不阻断 —— 写作阶段的任务是"把稿写出来"，闸门在 quality_checklist。
    增量而不是全量重扫：全量重扫 100 章会让每章落盘多花几秒，且对并发不友好。
    """
    try:
        from utils import story_state as ss
        state, mine = ss.update_chapter(
            Path(".").resolve(), n, read_text(path),
            Path("data/setting/setting.json").resolve())
        if mine:
            print(f"[stage4] 第{n}章一致性账本：{len(mine)} 条冲突")
            for c in mine:
                print(f"[stage4]   {c['detail']}")
        else:
            print(f"[stage4] 第{n}章一致性账本：无冲突（累计 {len(state['events'])} 条事件）")
    except Exception as e:                                    # noqa: BLE001
        print(f"[story_state] 第{n}章账本更新失败（不影响主流程）: {type(e).__name__}: {e}")


def _homo_report(path, n=None):
    """同质化/退化检测（P1，2026-10-19）：零 token、只报不拦。

    为什么在这里而不是 stage5：stage5 是 LLM 审稿（要花钱、且它读不出"同质"——
    模型看自己的产物只会说"结构完整"）。这项是确定性指标，放在落盘后第一时间出，
    用户审稿时手上就有一份"哪里在打转"的清单。

    判据实现全在 utils/homogeneity（stage6 也调同一份），这里只是带 tag 的薄包装；
    失败只打印不阻断 —— 与 text_norm 同一条原则：检测器坏了不该拖垮写作。
    """
    try:
        from utils.homogeneity import report_to_console
        report_to_console(path, n, Path("data/setting/setting.json").resolve(), tag="stage4")
    except Exception as e:                                    # noqa: BLE001
        print(f"[homogeneity] 第{n}章检测失败（不影响主流程）: {type(e).__name__}: {e}")

# 知识库注入的“一次性提示”开关。
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
    可能是几十章 —— 不在章间查，按了“停止”也要跑完整个阶段才生效。
    延迟导入 nf_api 防循环依赖（nf_api 顶层 import orchestrator，orchestrator
    模块级 import 本文件）；CLI 独立跑本文件时 nf_api 不可导入 → 降级“未请求”。
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


def _run_task_safe(client, task, on_piece=None, **kw):
    """client.run_task 的异常兜底（第一波防御 · 任务二，2026-10-22）。

    ## 为什么优先走流式（2026-10-26，用户第 6 条“点停止停不下来”）

    非流式 `run_task` 要等**整章**返回才可能检查停止标志 —— 一章几千字，等十几分钟，
    用户点了“停止”界面毫无动静（原话：“依旧点停止停不下来，绝了”）。
    `run_task_stream` 每读一小块就查一次 `stop_flag`，中断能在几秒内收手，
    而且半截正文**不落盘**（`_apply_ops` 被 stop 跳过），断点续跑不会捡到废稿。

    ## 2026-10-30：hermes（agent 模式）也走这条分支

    以前这里写着 `provider != "hermes"` —— 理由是“它的会话续接只接在 run_task 上”。
    实际代价是：**在 agent 模式下按停止，要等这一章跑完（最长 `hermes.timeout`=900 秒）**，
    因为 `run_task` 是 `communicate(timeout=900)` 一把阻塞，中途根本没有检查点。
    而 `HermesClient.run_task_stream` 早就实现了“逐行读 stream-json + stop 即杀进程树”，
    只是没被接进来（会话续接在流式里传 session_id 即可，`_build_cmd` 会拼 --resume）。

    所以现在所有引擎统一走流式；`session_id` 照原样透传（不悄悄 pop：
    实现若漏参数应当场 TypeError，而不是静默丢上下文）。

    ## 越权写盘检测（同批）

    `client.writes_files` 为真（hermes / 任何用文件工具写正文的子会话）时，
    调用前后各做一次快照比对，把“写了 data/** 之外的文件”变成事实打印出来。
    这是**检测不是沙箱**（真边界在 hermes 自己的权限设置里，见 utils/write_guard）。

    ⚠️ 只兜“客户端调用”这一步：任务构造（write_task/build_chapter_task）
    的异常仍要穿透 —— 那是代码/输入缺陷，吞掉只会让每章都悄悄失败。
    """
    stream_ok = (
        on_piece is not None
        and hasattr(client, "run_task_stream")
        and not kw.get("dry_run")
    )
    # 写盘边界快照（只对“子会话自己写文件”的引擎；direct 的写入经 allowed_paths 白名单）
    _wg_before = None
    if getattr(client, "writes_files", False) and not kw.get("dry_run"):
        try:
            from utils import write_guard
            _wg_before = write_guard.snapshot()
        except Exception as e:                                # noqa: BLE001
            print(f"[write_guard] 快照失败（本次不做越权写盘检测）: "
                  f"{type(e).__name__}: {e}")
            _wg_before = None
    try:
        if stream_ok:
            res = client.run_task_stream(task, on_piece, _stop_requested, **kw)
        else:
            res = client.run_task(task, **kw)
    except Exception as e:                                    # noqa: BLE001
        print(f"[stage4] ⚠ 子会话调用异常（已按失败处理，进度保留）: "
              f"{type(e).__name__}: {str(e)[:200]}")
        return {"exit_code": 1, "stdout_tail": "", "tokens": 0, "tokens_out": 0,
                "cost_yuan": 0.0, "estimated": True,
                "provider": getattr(client, "provider", "unknown"),
                "model": getattr(client, "model", "unknown"),
                "requests": 0, "error": f"{type(e).__name__}: {e}",
                "transport_error": True}
    if _wg_before is not None:
        try:
            from utils import write_guard
            _viol = write_guard.diff(_wg_before)
            if _viol:
                write_guard.report(_viol, task_name=str(getattr(task, "name", task)))
                if isinstance(res, dict):
                    res = dict(res)
                    res["write_violations"] = _viol
        except Exception as e:                                # noqa: BLE001
            print(f"[write_guard] WARN 越权写盘检测失败（不影响写作）: "
                  f"{type(e).__name__}: {e}")
    return res


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
    """从逐章大纲的“涉及角色”解析角色名列表。

    ## 支持两种真实格式（2026-10-18 修正）

    1. **多行列表**（当前流水线产出的真实形态）::

           - 涉及角色：
             - 沈砚（c015）：主角，落魄机关术师；被读数图与七锁摹痕一步步推出废屋。
             - 程砧（c016）：边城守卫队长；以军令与读数图促成同行。

    2. 单行顿号：`- 涉及角色：苏芷、观澜`
    3. 混合：`luxi 苏芷（写病历）、小林（夜班护士）`

    ⚠️ **旧实现只取“涉及角色”那一行**，多行格式下后续两行被整行丢掉，
    于是只剩 `- 沈砚（c015）：主角…` 一句，再按顿号切就得到
    `['-', '沈砚：主角', '落魄机关术师', …]` —— 真正的角色名 `沈砚` 反而不在里面，
    带括号的描述文字全成了"名字"。后果：角色卡恒空、设定条目恒空，
    而上层不报错（匹配不到就返回空串）——**静默失效**。
    """
    m = re.search(r"涉及角色[：:]", outline_text)
    if not m:
        return []
    tail = outline_text[m.end():]
    lines = tail.split("\n")
    chunks = []
    first = lines[0].strip() if lines else ""
    if first:
        chunks.append(first)                       # 单行格式：冒号后的内容在本行
    for ln in lines[1:]:                           # 多行列表：缩进的 `- xxx` 项
        s = ln.strip()
        if not s:
            if chunks:                             # 空行后若已有内容则结束（段落边界）
                break
            continue
        if s.startswith(("-", "*", "·")):
            chunks.append(s.lstrip("-*· ").strip())
            continue
        if chunks:                                 # 非列表行 → 本节结束
            break

    # 逐项两段法（2026-10-18 第四次修正）。
    #
    # ⚠️ 教训记录：这条函数改了四版，前三版都是“按顿号切整行”，每修好一种格式
    # 又漏另一种 —— `沈砚（c015）：主角，落魄机关术师；…` 一切就是
    # `['沈砚（c015）', '主角', '落魄机关术师', …]`，描述碎片全成了"名字"；
    # `玄翎卫统领（本章新登场，无设定集编号）` 的括号内含逗号，剥括号后残出
    # `无设定集编号）`。
    #
    # 现在的口径（**先砍描述，再切名字**）：
    #   1. 每一项先按 `：` 砍一刀 —— 冒号后全是描述，直接丢
    #   2. 剩下的名字区按顿号/逗号切
    #   3. 每个 token 里，括号内的 ASCII 串**单独收为 id 候选**
    #      （⚠️ 不能丢：`_name_matches` 会拿 `nc["id"]` 比对，`c001` 是功能性匹配键，
    #       不是噪音 —— 2026-10-18 就是把它误删导致 test_setting_schema 红了一条）
    #   4. 中文名：剥括号后按空白切，只收 2-8 个汉字
    ASCII_ID = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{1,15}")
    names = []
    for item in chunks:
        s = item.strip()
        if not s:
            continue
        # 1. 冒号后是描述，砍掉（`沈砚（c015）：主角，落魄机关术师` → `沈砚（c015）`）
        name_zone = re.split(r"[：:]", s, 1)[0]
        # 2. 名字区按顿号/逗号/斜杠/分号切
        for tok in re.split(r"[,，、/；;]+", name_zone):
            tok = tok.strip().strip("-*·").strip()
            if not tok:
                continue
            # 3. 括号内的 ASCII 串收为 id 候选（`c001 苏芷（写病历）` 的 c001、写病历里若含 id）
            for cid in re.findall(r"[（(]\s*([A-Za-z][A-Za-z0-9_\-]{1,15})\s*[)）]", tok):
                names.append(cid)
            # 剥括号，剩下的空白切分（`c001 苏芷` → ['c001', '苏芷']）
            bare = re.sub(r"[（(][^）)]*[)）]", "", tok).strip()
            for piece in bare.split():
                piece = piece.strip().strip("-*·").strip()
                if not piece:
                    continue
                # 纯 ASCII token 就是 id 前缀（`c001`），收为 id 候选
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{1,15}", piece):
                    names.append(piece)
                # 中文名：2-8 个汉字，不含任何非汉字
                elif 2 <= len(piece) <= 8 and not re.search(r"[^\u4e00-\u9fff]", piece):
                    names.append(piece)
    out, seen = [], set()
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _extract_world_context(setting_path, outline_text, involved=None, budget=4500):
    """从 setting.json 提取与本章相关的 world / timeline / plot_fragments 条目，**内联**进任务。

    ## 为什么必须内联（2026-10-18 P1）

    此前只有**角色卡**内联，其余一律只给 `{{path_setting}}` 让子会话自己 `read_file`。
    实测真实设定集（《雾港核心》）69 KB，含 `world`（18 地点 / 16 势力 / 19 世界规则 /
    8 物品）、`timeline`（15 条）、`plot_fragments`（24 条）——
    **这些条目一个都没进任务**，模型读多少、读哪条完全不受控。
    用户的判断正是：“设定库里扔了一堆语料都学不明白吗？”

    根因不是模型不学，是**没给它可学的东西**。内联后设定上下文从"一个路径"
    变成"本章相关的具体条目"，且与角色卡一样可被校验、可被计数。

    ## 匹配口径（保守，宁缺勿滥）

    - 条目名**出现在大纲正文**里 → 收录（地点/势力/规则/物品同规则）
    - `timeline` 事件文本含涉及角色名 → 收录（时间线是 locked 硬约束，漏了会写错年份）
    - `plot_fragments` 的 summary 含涉及角色名 → 收录；另附**未启用**（status=unused）
      的前 3 条作为“可用素材”——它们是 stage1 归并时留下的候选情节
    - 每条 desc 截断，总预算 `budget` 字；匹配不到就返回空，**不塞无关条目**
      （把整个设定集灌进去等于没灌：模型会跳读，且白烧输入 token）
    """
    try:
        setting = json.loads(read_text(setting_path))
    except Exception:                                        # noqa: BLE001
        return ""
    involved = involved or []
    outline = outline_text or ""

    def _hit(text):
        """条目相关性：正文提到条目名，或事件/情节提到涉及角色名。"""
        if not text:
            return False
        if any(n and n in outline for n in involved) and any(n in text for n in involved if n):
            return True
        return False

    def _render(entry, name_field="name", desc_field="desc", desc_cap=300):
        name = str(entry.get(name_field) or "").strip()
        if not name:
            return None
        typ = str(entry.get("type") or "").strip()
        desc = str(entry.get(desc_field) or "").strip()
        if len(desc) > desc_cap:
            desc = desc[:desc_cap] + "…"
        locked = "（locked，不可违逆）" if entry.get("locked") else ""
        head = f"- {name}" + (f"（{typ}）" if typ else "") + locked
        return head + (f"：{desc}" if desc else "")

    world = setting.get("world") or {}
    if isinstance(world, dict):
        buckets = [("地点", world.get("locations") or []),
                   ("势力", world.get("factions") or []),
                   ("世界规则", world.get("magic_system") or []),
                   ("物品", world.get("items") or [])]
    elif isinstance(world, list):                     # 兼容扁平 schema
        buckets = [("设定", world)]
    else:
        buckets = []

    sections = []
    total = 0

    def _push(title, lines):
        nonlocal total
        lines = [l for l in lines if l]
        if not lines:
            return
        block = f"### {title}\n" + "\n".join(lines)
        if total + len(block) > budget:
            return
        sections.append(block)
        total += len(block)

    for label, entries in buckets:
        lines = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "")
            if not name or name not in outline:
                continue
            lines.append(_render(e))
        _push(f"本章相关{label}", lines)

    # 时间线：locked 事件是硬约束，漏了会把年份/因果写错
    tl_lines = []
    for ev in (setting.get("timeline") or []):
        if not isinstance(ev, dict):
            continue
        text = str(ev.get("event") or "")
        if not _hit(text):
            continue
        year = ev.get("year")
        lock = "（locked）" if ev.get("locked") else ""
        body = text if len(text) <= 300 else text[:300] + "…"
        tl_lines.append(f"- {('[%s] ' % year) if year else ''}{body}{lock}")
    _push("本章相关时间线", tl_lines)

    # 情节碎片：命中涉及角色的 + 未启用的前 3 条（候选素材）
    pf_lines = []
    unused_left = 3
    for pf in (setting.get("plot_fragments") or []):
        if not isinstance(pf, dict):
            continue
        summary = str(pf.get("summary") or "")
        if not summary:
            continue
        status = str(pf.get("status") or "").strip()
        if _hit(summary):
            tag = f"（{status}）" if status else ""
        elif status == "unused" and unused_left > 0:
            unused_left -= 1
            tag = "（未启用，可作候选素材）"
        else:
            continue
        body = summary if len(summary) <= 300 else summary[:300] + "…"
        pf_lines.append(f"- {body}{tag}")
    _push("情节碎片", pf_lines)

    if not sections:
        return ""
    return ("### 相关设定条目（已按本章大纲筛过，写作时参考；**locked 条目不可违逆**）\n\n"
            + "\n\n".join(sections))


def _extract_character_cards_for_chapter(setting_path, outline_path, budget=3000):
    """从 setting.json 提取当前章节涉及的角色卡片段。

    匹配逻辑：从大纲解析“涉及角色”，与 setting.json 的 characters 逐个匹配，
    返回格式化的角色卡文本（供任务文件注入）。

    **schema 兼容**：setting.json 有**两个生产者**，字段集不同 ——
    stage1（真实流水线）产出 `role/traits/relations`，vault 联动产出
    `type/tags/snippet/locked`。本函数此前只认后者，导致真实流水线下
    “身份”与“性格”全丢。现统一走 `utils.setting_schema.normalize_character`，
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
    """角色是否落在大纲“涉及角色”名单里（容忍别名与括号描述）。"""
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


_STYLE_WARNED = set()


def _style_blocks(proj):
    """文风/段落规则：**只有 `book.style_rule` 显式选了才注入**（R22 拆锁）。

    默认空 → `{{style_rules}}` 渲染为空。作者没选，就不许系统替他定腔调。
    """
    from utils import writing_form as _wf
    app = _wf.applicability(proj)
    chosen = str(((proj or {}).get("book") or {}).get("style_rule") or "").strip()
    style, para, warn = _wf.style_blocks(app["code"], chosen=chosen)
    if not chosen and app["code"] not in _STYLE_WARNED:
        _STYLE_WARNED.add(app["code"])
        print("[stage4] 未选文风规格（book.style_rule 为空）—— 本轮不注入任何默认风格块")
    if warn and app["code"] not in _STYLE_WARNED:
        _STYLE_WARNED.add(app["code"])
        print("[stage4] " + warn)
    return style, para, app


def build_chapter_task(cfg, proj, n, outline_path, setting_path, rolling_path, prev_tail):
    target = cfg.get("chapter", {}).get("target_words", [2000, 3000])
    tail_section = "\n\n".join(prev_tail) if prev_tail else "（无上一章，本章为开篇）"
    book = proj.get("book", {})

    # 风格注入（超长截断/文件缺失都会 WARN，见 build_style_reference_block）
    style_ref = (book.get("style_reference") or "").strip()
    style_text, _style_meta = build_style_reference_block(book)
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
        from obsidian_bridge import (
            scan_vault, inject_context, is_vault_configured, get_vault_path)
        if not is_vault_configured():
            _kb_notice_once(
                "[stage4] 提示：未配置 obsidian.vault_path，本次写作**未启用**知识库"
                "词条注入（属未启用，非失败）。配置后可在 GUI“设置”页填写本地路径。")
        else:
            outline_text = _safe_read(outline_path, budget=8000)
            rm = re.search(r"涉及角色[：:]\s*(.+)", outline_text)
            query = rm.group(1)[:100] if rm else outline_text[:100]

            # 通道A（既有）：name/tags 精确匹配 —— 标准布局下快、带 locked 语义
            ctx_a = inject_context(query, vault_data=scan_vault(), top_k=5)

            # 通道B（2026-10-24）：**全文倒排召回** —— 扫描准入靠目录名的老通道
            # 对非标准布局的 vault 一条都扫不到（实测某真实库 scan_vault = 0 条），
            # 而正文里的关键词本该命中。这里索引 name + 正文全文，不看目录名、
            # 不要求 frontmatter。
            # 并联而非替换：A 的 locked 语义与既定行为原样保留，B 只补 A 的盲区。
            # 独立降级：B 出任何问题都不许连坐 A（真正的加法不改法）。
            ctx_b = ""
            try:
                from utils import kb_index
                hits = kb_index.recall(query, get_vault_path(),
                                       top_k=5, max_chars=600)
                b_parts = []
                for _p, name, snip, score in hits:
                    if name and name in ctx_a:      # A 已覆盖的不重复注入
                        continue
                    # 上一级目录名（如“行星”“01 武器装备”）帮模型分辨类别；
                    # 分数只留 1 位小数 —— 原样打 29.014470149006385 是噪声。
                    folder = Path(_p).parent.name
                    where = f"{folder}／" if folder and folder != "." else ""
                    b_parts.append(f"### {name}（{where}全文检索命中 {score:.1f}）\n{snip}\n")
                ctx_b = "\n".join(b_parts)
            except Exception as be:                          # noqa: BLE001
                _kb_notice_once(
                    f"[stage4] 提示：全文检索通道不可用（不影响正典词条注入）: "
                    f"{type(be).__name__}: {be}")

            kb_context = (ctx_a + "\n" + ctx_b).strip()
            if kb_context:
                kb_context = "### 相关正典词条（写作时参考，locked 条目不可违逆）\n\n" + kb_context
            else:
                _kb_notice_once(
                    "[stage4] 提示：vault 已配置但两条检索通道（词条名精确匹配 + "
                    "正文全文检索）都未匹配到相关词条 —— 未注入 KB 上下文"
                    "（可能是章节“涉及角色”行缺失，或本章内容与正典词条无交集）。")
    except Exception as e:                       # noqa: BLE001
        print(f"[stage4] ⚠ 知识库注入失败（本章将无 KB 正典上下文）: "
              f"{type(e).__name__}: {e}")

    # 角色卡锁定：从 setting.json 提取当前章节涉及的角色
    character_cards = _extract_character_cards_for_chapter(setting_path, outline_path)

    # 相关设定条目内联（2026-10-18 P1）：world / timeline / plot_fragments 此前
    # **只给一个文件路径**，模型读多少、读哪条不受控 —— 实测设定集 69 KB，
    # 这些条目一个都没进任务。现按本章大纲筛过后内联（见 _extract_world_context）。
    outline_text_for_ctx = _safe_read(outline_path, budget=8000)
    world_context = _extract_world_context(
        setting_path, outline_text_for_ctx, involved=_involved_names(outline_text_for_ctx))

    # 记忆策略（2026-10-20，台账三·上下文 memory policy）：
    # L1 固定层（大纲/角色卡/上一章尾/设定条目）由上面的块提供，**不裁**；
    # 这里补 L2 滚动层（全书摘要 + 最近章节，按预算裁）与 L3 召回层
    # （伏笔/角色/状态/关系四维，按相关度 top-K）。裁了什么、召回几条都写进块里，
    # 不静默 —— 否则用户看到"有上下文"而其实早被裁光了。
    memory_block = ""
    try:
        from utils.memory_policy import build_memory
        _mem = build_memory(
            n, outline_text_for_ctx, setting_path, rolling_path,
            Path("data/state/story_state.json"), cfg)
        memory_block = _mem["block"]
        _st = _mem["stats"]
        _by = _st["recall"]["by_dim"]
        print("[stage4] 第%d章记忆：L2 %d 章（省略 %d 章）· L3 召回 %d 条"
              "（伏笔%d/角色%d/状态%d/关系%d）"
              % (n, len(_st["rolling"]["chapters"]), len(_st["rolling"]["omitted"]),
                 _st["recall"]["recalled"], _by.get("foreshadow", 0),
                 _by.get("character", 0), _by.get("state", 0), _by.get("relation", 0)))
    except Exception as e:                              # noqa: BLE001
        print(f"[stage4] 记忆上下文组装失败（本章退回固定层级）: "
              f"{type(e).__name__}: {e}")
        memory_block = ""

    # 分支样例锚点（第 4 件 · 选项可视化）：用户在章大纲里选定的“样例文本”
    # 注入成 few-shot 参照。读章大纲里的优化点锚点，只挑 kind=sample 的 ——
    # 讨论意见/字段改写类锚点是给**人**看的决策记录，混进去会污染写作模型。
    # 全量注入不做 top-K 截断（用户口径：整套写作用不了多少 token，样例文是
    # 精心磨出来的，截断可能丢掉关键语气/节奏信息）。
    try:
        from utils.chapter_samples import chapter_samples
        chapter_samples_block = chapter_samples(n)
    except Exception as e:                              # noqa: BLE001
        # 注入失败不阻断写作（同 kb_context 的三态纪律：这里属“失败”要打印）
        print(f"[stage4] ⚠ 样例锚点注入失败（本章将无样例参照）: "
              f"{type(e).__name__}: {e}")
        chapter_samples_block = ""

    # 参考片段（decompose_samples）：原有功能，2026-10-24 我插引用块时曾把它
    # 整块顶掉，导致 build_chapter_task 抛 NameError（全量回归 test_stage4_appearances
    # 抓出）。两个 try 块各自独立 —— 任一失败只清各自的变量，互不牵连。
    try:
        from utils.style_profile import load_snippets_block
        decompose_samples = load_snippets_block()
    except Exception as e:                              # noqa: BLE001
        print(f"[stage4] 参考片段注入失败（本章将无片段参照）: "
              f"{type(e).__name__}: {e}")
        decompose_samples = ""

    # 大纲点名的素材原文（2026-10-24 OB34 素材实测后补）：用户在大纲里写
    # “（碎片2026-4-8）”是**取材指令**，此前没有任何机械通道保证它进上下文
    # （bigram 召回只看字面重合，点名不等于命中）。这里逐章解析并内联原文。
    # 纪律同 kb_context：解析失败不阻断写作，但必须打印 —— 静默丢弃等于让
    # 取材指令失效而无人知。
    try:
        from utils.citations import resolve_citations, build_citation_block
        _cites = resolve_citations(
            outline_text_for_ctx,
            roots=["materials/original_scraps", "materials/raw"])
        citations_block = build_citation_block(_cites)
        if _cites:
            _hit = sum(1 for c in _cites if c.get("found"))
            print("[stage4] 第%d章大纲点名素材 %d 张（命中 %d，缺 %d）"
                  % (n, len(_cites), _hit, len(_cites) - _hit))
    except Exception as e:                              # noqa: BLE001
        print(f"[stage4] ⚠ 大纲引用解析失败（本章将不注入点名素材）: "
              f"{type(e).__name__}: {e}")
        citations_block = ""

    style_rules, para_rules, _form_app = _style_blocks(proj)
    # 叙事深化（v0.7.2）：本章所在深化段的节奏形状。占位符在 --- 分隔线之后，
    # 不碰前缀缓存区。存量卷纲/散文文体 → 空串（模板里那一行渲染成空行）。
    try:
        from utils import story_deepen as _sd
        deepen_context = _sd.context_for(n, int(book.get("chapters") or 0) or 10,
                                         cfg, proj)[1]
    except Exception as e:                              # noqa: BLE001
        print(f"[stage4] 叙事深化块组装失败（本章将无深化上下文）: "
              f"{type(e).__name__}: {e}")
        deepen_context = ""
    # 讨论结论（2026-11-03 讨论构件）：作者拍板过的约束必须进任务文件 ——
    # 这是"讨论真的落实到正文"的唯一机械保证。没有已定稿讨论时渲染成空串，
    # 不占提示词；组装失败不阻断写作，但必须打印（静默丢弃 = 讨论白讨论）。
    try:
        from utils import discussion as _disc
        discussion_block = _disc.render_for_prompt(n)
        if discussion_block:
            print("[stage4] 第%d章注入讨论结论（约束 %d 条）"
                  % (n, sum(len(v) for v in _disc.load_constraints(n).values())))
    except Exception as e:                              # noqa: BLE001
        print(f"[stage4] ⚠ 讨论结论注入失败（本章将不带讨论约束）: "
              f"{type(e).__name__}: {e}")
        discussion_block = ""

    _, body = load_template("stage4_writing.md", {
        "n": n,
        "discussion_block": discussion_block,
        "style_rules": style_rules,
        "para_rules": para_rules,
        "book_name": book.get("name", "未命名"),
        "path_outline": Path(outline_path).resolve(),
        "path_setting": Path(setting_path).resolve(),
        "path_rolling": Path(rolling_path).resolve(),
        "path_project": Path("config/project.yaml").resolve(),
        "prev_tail": tail_section,
        "deepen_context": deepen_context,
        "memory_block": memory_block,
        "style_reference": style_text,
        "style_samples": style_samples,
        "style_notes": style_notes,
        "writing_rules": _writing_rules_block() + _dialogue_ratio_block(cfg),
        "kb_context": kb_context,
        "citations_block": citations_block,
        "chapter_samples": chapter_samples_block,
        "decompose_samples": decompose_samples,
        "character_cards": character_cards,
        "world_context": world_context,
        "min_words": target[0],
        "max_words": target[1],
        "path_output": Path(f"data/chapters/raw/{n:02d}.md").resolve(),
    })
    return body


def build_expand_task(cfg, proj, n, cur_words, low, high):
    """字数不足时的定向扩写任务（2026-10-17 第14次试跑修复）。

    为什么不是整章重写：实测同章字数不足重跑 9 次仍复现同样的短稿 ——
    模型收到"重写本章"会走同一条生成路径，短是系统性的。改成"读原稿、
    只补量"后目标明确（插细节不动骨架），成功率高得多，且原稿已过的
    校验项（标题/注释/风格）不会被重写破坏。

    模板优先（prompts/stage4_expand.md），装机端 payload 未含该模板时
    用内置兜底文本 —— 升级后 seedWorkspace 会补上模板，兜底只服务过渡期。
    """
    book = proj.get("book", {})
    draft = Path(f"data/chapters/raw/{n:02d}.md").resolve()
    style_notes = build_style_notes_section(book.get("style_notes", ""))
    variables = {
        "n": n, "cur_words": cur_words,
        "min_words": low, "max_words": high,
        "path_draft": draft, "style_notes": style_notes,
    }
    try:
        _, body = load_template("stage4_expand.md", variables)
        return body
    except FileNotFoundError:
        print("[stage4] 提示：prompts/stage4_expand.md 缺失（装机端 payload 未更新？）"
              "—— 用内置扩写指令兜底")
        return (
            "# 阶段 4 任务：扩写第 {n} 章至字数下限\n\n"
            "现有稿件只有 {cur_words} 字，低于要求下限 {min_words} 字。"
            "请先用 read_file 读取现有稿件，然后在保留既有情节、结构、对话的"
            "前提下扩写正文，使总字数达到 {min_words}~{max_words} 字之间。\n\n"
            "## 扩写要求\n"
            "- 只增不删：既有段落全部保留，扩写内容插在语义衔接处\n"
            "- 扩写手法：补充动作细节、环境的具体感官呈现、对话的往还回合、"
            "物件与机关的运转过程\n"
            "- 严禁新增关键情节、新角色、新冲突；不得改动结尾走向\n"
            "- 保留文末 summary 与 quality 注释行，原样不动\n\n"
            "## 文风笔记（最高优先级）\n{style_notes}\n\n"
            "## 硬性禁止\n"
            "- 禁止占位符（XXX/TODO/待补充）；禁止 AI 腔套话\n"
            "- 禁止 Markdown 表格、图片、代码块\n"
            "- 禁止复读：同一描写不得换个说法再来一遍\n\n"
            "## 输入文件（用 read_file 读取）\n"
            "- 现有稿件: {path_draft}\n\n"
            "## 输出\n"
            "将扩写后的完整稿件（含原注释行）写回文件: {path_draft}\n"
        ).format(**variables)


def _stash_partial(n, chap_path, buf):
    """把“按停止时已经收到的字”落成残稿（2026-11-02，不可用清单第 3 条）。

    ## 之前的定价

    停止 = 当前章半截**直接丢弃**。用户原话的意思是：停一次亏一整章几千字，
    所以理性选择就是不停 —— 这就是"只能丢进去一直跑"的由来。

    ## 现在

    半截落到 `data/state/truncated/chNN_停在_<时间>.txt`，走的是**同一套**残稿机制
    （列表 / 读 / 采纳 / 清 都已存在，只是从没接在"用户停止"这条路径上）。
    采纳只写进 `raw/NN.md` + 侧车标记，`is_chapter_complete` 仍判“未完成”——
    残稿就是残稿，还得过审稿。

    返回 `(path, chars)`；没东西可存时 `(None, 0)`。失败只告警：
    **不能因为存残稿失败就把"停止"本身变成错误**。
    """
    from utils import truncation
    text = "".join(buf or []).strip()
    if not text:
        # agent / hermes：正文由子会话 write_file 落盘，文本流只是碎念。
        # 文件已经在了就是残稿 —— 不再复制一份（两份"真相"会打架）。
        try:
            p = Path(chap_path)
            if p.exists():
                text = read_text(p).strip()
        except Exception:                                    # noqa: BLE001
            text = ""
        if text:
            return str(chap_path), len(text)
        return None, 0
    try:
        truncation.TRUNC_DIR.mkdir(parents=True, exist_ok=True)
        path = truncation.TRUNC_DIR / (
            "ch%02d_停在_%s.txt" % (n, time.strftime("%Y%m%d_%H%M%S")))
        write_text(path, text)
        return str(path), len(text)
    except Exception as e:                                   # noqa: BLE001
        print(f"[stage4] WARN 残稿落盘失败（不影响停止本身）: {e!r}")
        return None, 0


def parse_chapter_scope(spec):
    """把 ``"1-3,5"`` 解析成 ``{1,2,3,5}``；空/None → None（表示不限制）。

    ## 为什么要有它（2026-11-02，不可用清单第 1 条）

    阶段 4 一旦开始就把**所有**未完成章一口气写完：只有阶段粒度，没有章粒度。
    于是"先写三章看看"这件事做不到，后面所有"停下来看看"都无从谈起。

    格式刻意只支持两种写法：单章（``5``）与闭区间（``1-3``），逗号分隔。
    **不猜**：``abc``、``3-1``、``0`` 一律抛 ValueError 并说清怎么写 ——
    静默忽略等于让用户以为限了范围、实际整本跑完（这正是要治的病）。
    """
    if spec is None:
        return None
    s = str(spec).strip()
    if not s:
        return None
    out = set()
    for part in s.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                raise ValueError("章号区间写法不对：%r（要 1-3 这种）" % part)
            if lo < 1 or hi < 1:
                raise ValueError("章号从 1 开始：%r" % part)
            if lo > hi:
                raise ValueError("区间起点大于终点：%r" % part)
            out.update(range(lo, hi + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                raise ValueError("章号写法不对：%r（要 3 或 1-3）" % part)
            if n < 1:
                raise ValueError("章号从 1 开始：%r" % part)
            out.add(n)
    return out or None


def _take_intervention(n):
    """取走"作者当场要求"这条一次性指令（2026-11-02，清单第 4 条）。

    只在本章适用时才 consume —— 给"第 7 章"的指令不能被第 3 章吃掉。
    拿不到就返回 None（读失败不抛：插话通道坏了不该拖垮写作）。
    """
    try:
        data = intervene.read()
        if not intervene.applies_to(data, n):
            return None
        return intervene.consume()
    except Exception:                                        # noqa: BLE001
        return None


def _regen_over_budget(used_tokens, limit):
    """这一章的补长是不是已经超线（2026-11-02，清单第 6 条）。

    单独抽出来是为了可单测：这段判断埋在 run_stage 里的话，只能靠"真烧 200 万
    token"来验证 —— 那等于不验证。`limit <= 0` = 不设限。
    """
    try:
        return bool(limit and limit > 0 and used_tokens >= limit)
    except TypeError:
        return False


def _apply_scope(todo, chapters):
    """按章范围缩 todo。返回 (这一轮要写的, 被跳过的章号)。

    纯函数 —— 行为可单测。放在 run_stage 里内联的话，只有"真跑一次模型"
    才能验证它，那等于永远不验证（要花 token）。
    """
    if not chapters:
        return list(todo), []
    want = [n for n in todo if n in chapters]
    out = sorted(set(chapters) - set(todo))
    return want, out


def parts_state(parts_dir, chapter_no, total_groups=0):
    """盘上真实进度：这章已落盘几群 / 共几群 / 合计多少字（零 token）。

    为什么要读盘而不是读内存：加强模式是"逐群落盘"，`_parts/NN/gK.md` 就是
    事实本身。2026-11-03 真机：第 1 章 4 群约 5900 字在盘，系统里等于没写 ——
    用户看到"这章没写"，而 90% 的字已经在盘上了。
    """
    d = Path(parts_dir) / ("%02d" % int(chapter_no))
    done = words = 0
    if d.is_dir():
        for p in sorted(d.glob("g*.md")):
            try:
                words += count_cn_words(read_text(p))
                done += 1
            except Exception:                                # noqa: BLE001
                continue
    try:
        total = int(total_groups or 0)
    except (TypeError, ValueError):
        total = 0
    return {"done": done, "total": total, "words": words}


def note_parts(parts_dir, raw_dir, progress, chapter_no, total_groups=0):
    """把段群进度写进 `progress.stages["4"].parts`（有拼装稿的章就不再挂"未拼装"）。

    返回写进去的那条记录（没写则 None）。零 token、纯读盘，随时可重放。
    """
    try:
        st = progress.data.setdefault("stages", {}).setdefault("4", {})
        bucket = st.setdefault("parts", {})
        key = str(int(chapter_no))
        if (Path(raw_dir) / ("%02d.md" % int(chapter_no))).exists():
            bucket.pop(key, None)
            info = None
        else:
            info = parts_state(parts_dir, chapter_no, total_groups)
            if info["done"]:
                bucket[key] = info
            else:
                bucket.pop(key, None)
                info = None
        progress.save()
        return info
    except Exception:                                        # noqa: BLE001
        return None


def _scope_completion(scope, done_ns, total):
    """按范围完成 vs 整本完成（2026-11-03，作者拍板：按范围完成 = 部分完成）。

    返回 ``(left_outside, left_inside)``：

    · ``left_inside`` —— 你要的章里还没写完的。非空 = 这一轮**没**完成（失败/中断语义）。
    · ``left_outside`` —— 范围之外还没写完的。``left_inside`` 空 + 这个非空 =
      **部分完成**：这一轮该写的都写了，但整本还没齐，阶段**不许标 done**。

    ``scope`` 为空 = 不限范围，两项都返回空（整本跑，语义与从前一致）。
    纯函数，行为可单测（真跑模型验证一次要几百万 token，等于不验证）。
    """
    if not scope:
        return [], []
    try:
        total = int(total)
    except (TypeError, ValueError):
        return [], []
    all_ns = set(range(1, total + 1))
    inside = sorted({int(x) for x in scope} & all_ns)
    outside = sorted(all_ns - set(inside))
    done = {int(x) for x in (done_ns or ())}
    return [n for n in outside if n not in done], [n for n in inside if n not in done]


def _chapter_status(check, quality_threshold):
    """章节状态（2026-11-02，不可用清单第 5 条）：确定性判据优先，自评分只作参考。

    ## 旧写法有两个洞

    旧：``needs_rewrite = quality < threshold; status = ok / needs_rewrite``

    1. **字数不达标也算 ok**：字数区间写在 `check.errors` 里、`check.ok` 已经是 False，
       但 status 压根没看 `check.ok`。实测 10 章全部 2000 上下（下限 5000）、
       全部判 ok、自评 8~9 分 —— 用户看到“完成”。
    2. **自评分是唯一判据**：P5 的 H2 说"模型自评分不得单独决定任何转移"，
       这里却是它一个人说了算。

    现在三档：
    - ``invalid``：确定性判据不过（字数区间 / 占位符 / 退化 / 标点）→ 不许是 ok；
    - ``needs_rewrite``：确定性过了，但模型自评低于阈值（**参考项**）；
    - ``ok``：两者都过。
    """
    if not getattr(check, "ok", True):
        return "invalid"
    if check.quality is not None and check.quality < quality_threshold:
        return "needs_rewrite"
    return "ok"


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None,
              chapters=None):
    client = client or make_client(cfg, "writer")
    task_dir = task_dir or "data/state/tasks"
    total = int(proj.get("book", {}).get("chapters", 10))
    raw_dir = Path("data/chapters/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)
    rolling_path = Path("data/summaries/rolling.md")
    # 滚动摘要骨架预创建（2026-10-17 第14次试跑修复）：首章开跑前 rolling.md
    # 尚不存在，而写作任务把它列为"用 read_file 读取"的输入 —— 子会话读不到
    # 输入会直接放弃写作（"章节文件未生成"连败的一半根因）。骨架=空摘要，
    # 与 load_rolling 对缺失文件的解析结果逐字一致，不改任何下游语义。
    if not rolling_path.exists():
        write_rolling(rolling_path, {"global_summary": "", "chapters": {}})
        print(f"[stage4] 初始化滚动摘要骨架 → {rolling_path}")
    quality_threshold = int(cfg.get("chapter", {}).get("quality_threshold", 6))
    # 单章补长上限（2026-11-02，清单第 6 条）。0 = 不设限。
    regen_token_limit = int(cfg.get("chapter", {}).get("regen_token_limit", 400000) or 0)
    max_recent = int(cfg.get("chapter", {}).get("summary_every", 5))

    completed = progress.completed_chapters(4)
    # 断点续跑：只保留文件真正完成的章节（防止半成品被跳过）
    from utils import writing_form as _wf4
    _para_ok = _wf4.applicability(proj).get("para_structure", True)
    verified = [n for n in completed
                if is_chapter_complete(raw_dir / f"{n:02d}.md",
                                       *cfg.get("chapter", {}).get("target_words", [2000, 3000]),
                                       para_structure=_para_ok)]
    skipped = set(completed) - set(verified)
    if skipped:
        print(f"[stage4] 半成品章节重跑（文件存在但校验未通过）: {sorted(skipped)}")

    failed = progress.failed_chapters(4)
    failed_ns = {f["n"] for f in failed}
    todo = [n for n in range(1, total + 1) if n not in verified or n in failed_ns or n in skipped]

    # 讨论定稿晚于正文 → 该章必须重写（2026-11-03 讨论构件，③"接着推进"的落点）。
    # 判据只看 mtime：作者在讨论里改了结论、正文还是旧稿时，靠人记得"我改过"是不可靠的
    # —— 2026-10-25《潮汐档案》03/05 章就是这么变成"讨论过、稿子没变"的。
    try:
        from utils import discussion as _disc
        _stale = _disc.stale_chapters(raw_dir)
        if _stale:
            _stale_ns = sorted({n for n, _p, _at in _stale})
            print("[stage4] 讨论已定稿但正文更旧 —— 这些章本轮重写：%s"
                  % _stale_ns)
            todo = sorted(set(todo) | set(_stale_ns))
    except Exception as e:                              # noqa: BLE001
        print(f"[stage4] ⚠ 讨论时效核对失败（本轮不据此重写）: "
              f"{type(e).__name__}: {e}")

    # ---- 按范围完成 = 部分完成（2026-11-03，作者拍板）----
    # 判据只看**盘上事实**：raw/NN.md 是否合格（与断点续跑同一判据），不信 progress
    # 的完成标记 —— 标记可能因崩溃而落后于文件。
    _tw_scope = cfg.get("chapter", {}).get("target_words", [2000, 3000])[:2]

    def _done_ns():
        done = set(progress.completed_chapters(4))
        for _p in raw_dir.glob("*.md"):
            if not _p.stem.isdigit():
                continue
            _n = int(_p.stem)
            if _n in done:
                continue
            try:
                if is_chapter_complete(_p, *_tw_scope, para_structure=_para_ok):
                    done.add(_n)
            except Exception:                                # noqa: BLE001
                pass
        return done

    def _partial_or_done(scope, note):
        """这一轮该写的都写完了：整本齐 → done；范围内齐、范围外缺 → partial。"""
        left_out, left_in = _scope_completion(scope, _done_ns(), total)
        if left_in:
            # 范围里还有没写完的：交给调用方按失败/中断处理，不在这里下结论。
            return None
        if left_out:
            msg = ("stage4 **按范围完成**（部分完成）：你要的第 %s 章都写好了；"
                   "本书还有 %d 章没写（%s）。阶段 4 **不标完成** —— 看完这几章、"
                   "把剩下的章号填进范围再跑即可（断点续跑，不会重写已完成章）%s"
                   % (",".join(str(x) for x in sorted(scope)), len(left_out),
                      ",".join(str(x) for x in left_out), note))
            print("[stage4] " + msg)
            progress.set_stage(4, "partial",
                               partial_scope=sorted(int(x) for x in scope),
                               chapters_left=left_out,
                               note=note or None)
            return False, msg
        return True

    # 开跑前先对账一次（2026-11-03）：把盘上**已落盘但还没拼装**的段群补进 progress，
    # 这样崩溃/中断过后，界面也能说清"这章只差最后一群"，而不是一片空白。
    try:
        _pdir = raw_dir.parent / "_parts"
        if _pdir.is_dir():
            for _d in sorted(_pdir.iterdir()):
                if _d.is_dir() and _d.name.isdigit():
                    # 这里必须用**模块级** note_parts：内层的 _note_parts 还没定义
                    # （它在下面才 def），提前调用会 NameError（被 quality_gate 抓住）。
                    note_parts(_pdir, raw_dir, progress, int(_d.name), total_groups=0)
    except Exception:                                        # noqa: BLE001
        pass

    # 章范围（2026-11-02，不可用清单第 1 条）：只写你要的那几章。
    # 放在 todo 算完之后缩范围，不改上面任何一条判据 —— 已完成 / 半成品 / 失败
    # 的语义全部照旧，只是"这一轮写哪些"多了一个上限。
    if chapters:
        todo, _out = _apply_scope(todo, chapters)
        print(f"[stage4] 限写 {len(todo)} 章：{todo}")
        if _out:
            print(f"[stage4] 跳过（已完成 / 超出本书章数）：{_out}")
        if not todo:
            _msg = ("指定的章都无需重写（已完成或超出本书章数 %d）—— "
                    "没有调用任何模型" % total)
            # 范围内都齐 ≠ 整本齐：整本齐才是 done，否则记 partial（作者 2026-11-03 拍板）
            _verdict = _partial_or_done(chapters, "（本轮没有调用任何模型）")
            return (_msg if _verdict is None else _verdict), _msg

    # 崩溃窗口恢复（step 级 checkpoint，2026-10-21）：**磁盘有合格正文、但 progress
    # 没标 done** 的章 = 上轮在“写作落盘”之后、“标记完成”之前崩了（进程被杀 /
    # 断电 / 熔断恰好落在中间）。这类章的正文是好的，重调 LLM 重写一遍纯属浪费，
    # 还可能换来一版更差的。处理：走 _finalize 的幂等重放 —— 补摘要 + 补出场 +
    # 补记账，再标完成，全程零 LLM 调用。
    #
    # 为什么摘要必须补而不是跳过：滚动摘要是**下一章的输入**。崩溃前它没写进
    # rolling.md，下一章就会拿不到本章摘要。而 append_chapter_summary 是同章
    # 覆盖语义 —— 这里补一次是安全的；真正会污染全局摘要的是“重调 LLM 重跑整章”
    # （多压一行进 global_summary）。所以判据是：正文合格 → 只补账，不重写。
    _tw = cfg.get("chapter", {}).get("target_words", [2000, 3000])[:2]
    _raw_seen = {int(p.stem) for p in raw_dir.glob("*.md") if p.stem.isdigit()}
    orphan_ns = sorted(
        n for n in _raw_seen
        if n not in completed and n not in failed_ns
        and is_chapter_complete(raw_dir / f"{n:02d}.md", *_tw, para_structure=_para_ok))
    if orphan_ns:
        print(f"[stage4] 检测到 {len(orphan_ns)} 章“正文已在盘、账未记”"
              f"（上轮崩溃遗留）：{orphan_ns} —— 幂等补账，不重调 LLM")
        # 孤儿章必须进 todo：否则“全是孤儿”时下面的 not todo 会误判成
        # “跳过（已完成）”，崩溃恢复路径永远走不到。
        todo = sorted(set(todo) | set(orphan_ns))
    _orphan_set = set(orphan_ns)

    # “只写指定章”（2026-10-26，GUI“重写本章”）：GUI 那边已经把这章的
    # raw 文件移走并清了完成标记，这里再把 todo 收窄到指定章。
    # 为什么用环境变量而不是加参数：orch_run 的签名是四个阶段路径共用的，
    # 为一个 GUI 动作改公共签名不划算；而 start_job 保证同一时刻只有一个 job
    # 在跑（互斥），不存在两个 stage4 抢同一个变量。
    _only_env = os.environ.get("NF_STAGE4_ONLY", "").strip()
    if _only_env:
        try:
            _only_ns = {int(x) for x in _only_env.split(",") if x.strip().isdigit()}
        except ValueError:
            _only_ns = set()
        if _only_ns:
            before = list(todo)
            todo = [x for x in todo if x in _only_ns]
            print(f"[stage4] 只写指定章：{sorted(_only_ns)}"
                  f"（原本待写 {before}，收窄为 {todo}）")
            if not todo:
                print(f"[stage4] 指定章 {sorted(_only_ns)} 不在待写列表里"
                      "（盘上已有合格正文且标记完整）—— 无需重写")
            if len(_only_ns) == 1:
                # 单章重写标记：/state 据此把分母从“全书章数”换成“这一章”，
                # 前端才不会把“重写第 5 章”画成永恒的 99%（见 stage_status）。
                try:
                    _st4 = progress.data.setdefault("stages", {}).setdefault("4", {})
                    _st4["rewrite_chapter"] = next(iter(_only_ns))
                    progress.save()
                except Exception as e:                       # noqa: BLE001
                    print(f"[stage4] 写入 rewrite_chapter 失败（不影响写作）: "
                          f"{type(e).__name__}: {e}")

    if not todo:
        # 这里可能刚写过 rewrite_chapter（指定章已合格、无需重写）—— 一并清掉，
        # 否则 /state 会一直以为"正在重写第 N 章"。
        try:
            progress.data.get("stages", {}).get("4", {}).pop("rewrite_chapter", None)
        except Exception:                                    # noqa: BLE001
            pass
        progress.set_stage(4, "done")
        return True, "stage4 跳过（已完成）"

    progress.set_stage(4, "running")
    low, high = cfg.get("chapter", {}).get("target_words", [2000, 3000])[:2]
    # R01 预算—篇幅联动（2026-10-30 补齐）：stage4 此前是唯一**既没有容量校验、
    # 也从不设 max_tokens** 的阶段（提示词里的 25000 无人读取）。装不下时模型会
    # 自己删内容，表现为“每章都短一截”，而 finish_reason 还是 stop —— 既有那道
    # “截断即失败”的防线**看不见**（与 stage5 实测 5967→2186 同一机制）。
    # 这里至少拦住“目标字数根本装不进本阶段输出上限”的配置，并把实际生效值打出来。
    from utils.judge.budget import check_capacity, effective_max_tokens
    _limit4 = effective_max_tokens(cfg, 4)
    _ok4, _why4, _cap4 = check_capacity(proj, 4, max_tokens=_limit4, words=int(high))
    if not _ok4:
        print("[stage4] " + _why4)
        progress.set_stage(4, "failed", error=_why4)
        return False, _why4
    print(f"[stage4] 篇幅预算校验通过：输出上限 {_limit4}，单章目标 {low}-{high} 字"
          f"（需要 ≥{_cap4.get('needed')} token）")
    # 篇幅口径与生成方式的匹配告警（2026-10-26，用户：“弱模型无论怎么骂就是写不长
    # ……所以才需要加强模式来强行拼凑”）。不阻断，只说清楚"你可能需要它"。
    if low >= 4000 and not bool(cfg.get("chapter", {}).get("enhanced_mode", False)):
        print(f"[stage4] 提示：目标字数下限 {low} 字，但加强模式未开启"
              "（chapter.enhanced_mode=false）。"
              "弱模型整章单发常在两三千字就收尾；出现这类失败时本阶段会自动兜底"
              "转加强模式，也可以在“设置 → 加强模式”里预先开启。")
    # 连续失败止损阈值（0 = 关闭）。见 _fail 的说明。
    max_consec_fail = int(cfg.get("gates", {}).get("stage4_max_consecutive_failures", 3))
    # 打回重生成上限（第一波防御 · 任务一）。0 = 关闭打回（行为退回本改动之前：
    # 校验不过直接判失败章）。
    regen_max = int(cfg.get("gates", {}).get("hard_validate_regen_rounds", 2))
    consec_fail = 0
    expanded = set()   # 本轮已扩写过的章节（每章至多一次，防空转）
    aborted = ""
    done_this_round = 0

    # ---- 流式写作的字数回传（2026-10-26，用户第 3/4/6 条）----
    # 用户原话：“job 状态一直 running，我无法知道内部进度”“卡在 29% 让人觉得坏了”。
    # 有了流式回调，界面可以显示“正在写第 6 章 · 已 1200 字”，而不是一个死数字。
    # 清掉上一轮的 paused_at：它只在"这一轮按暂停"时有意义，
    # 留着会让下一次运行一开始就被 orchestrator 当成"已暂停"收尾。
    try:
        progress.data.setdefault("stages", {}).setdefault(str(4), {}).pop("paused_at", None)
        progress.save()
    except Exception:                                        # noqa: BLE001
        pass

    _ch_state = {"chars": 0, "last_write": 0.0, "buf": []}
    # `buf`：本章已收到的**正文片段**（2026-11-02，不可用清单第 3 条）。
    # 只累计不给用户看 —— 用户按“停止”时把它落成残稿，这样"停一下"不再等于
    # 白写一整章。只在文本流就是正文时累计；agent 引擎的正文在文件里，走另一条。`
    # 文本流是不是正文？（2026-10-30）
    #   direct：正文随流产出 → text 就是进度；
    #   hermes/agent：正文由子会话 write_file 落盘，text 只是助手碎念
    #   （"我先读取任务文件…"），把它当字数 = 界面显示“已写 1200 字”而正文 0 字。
    _text_is_prose = not bool(getattr(client, "writes_files", False))

    def _piece_progress(chapter_no, text_is_prose=True):
        """流式回调：累计文本流，并兼任“活动心跳”。

        分工（2026-10-30 定）：
          · `current_text_chars`：永远是原始文本流长度，排障用；
          · `current_chars`：**只在文本流就是正文时**才用它（直连引擎）；
            agent 模式下由 `_file_progress` 按产物文件回报真实字数 ——
            宁可显示 0 字也不许把碎念当正文（那是在骗用户）。
          · `current_chars_at`：任何事件（含工具事件）都刷新，前端据此显示
            “N 秒无活动”，用来区分“正在写文件”与“卡死”。
        """
        def _on_piece(piece):
            if piece:
                _ch_state["chars"] += len(piece)
                if text_is_prose:
                    # 只收正文流：agent 模式下 piece 是助手碎念（"我先读取任务文件…"），
                    # 收进来会变成一份由碎念组成的"残稿"，比没有更糟。
                    _ch_state["buf"].append(piece)
                    if len(_ch_state["buf"]) > 4000:      # 防极端长跑吃内存
                        _ch_state["buf"] = ["".join(_ch_state["buf"][-2000:])]
            now = time.time()
            if now - _ch_state["last_write"] < 1.5:
                return
            _ch_state["last_write"] = now
            try:
                with _PROGRESS_LOCK:
                    st = progress.data.setdefault("stages", {}).setdefault("4", {})
                    st["current_chapter"] = chapter_no
                    st["current_text_chars"] = _ch_state["chars"]
                    if text_is_prose and st.get("current_chars_source") != "file":
                        st["current_chars"] = _ch_state["chars"]
                        st["current_chars_source"] = "text"
                    # 最后更新时间（2026-10-27）：只说“已写 1 字”用户分不清是刚起步
                    # 还是模型思考/网络卡住。前端据此显示“N 秒无活动”。
                    st["current_chars_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    progress.save()
            except Exception:                                # noqa: BLE001
                pass
        return _on_piece

    def _file_progress(chapter_no):
        """产物文件进度回调：`current_chars` 用**文件里真实字数**。

        hermes / 外部 agent 用文件工具写正文时，text 事件不是正文；文件才是。
        这个回调由两处喂：
          · 加强模式：`write_chapter_groups` 的看门狗按 `_parts/NN/gK.md`；
          · 整章单发：`_poll_file(chap_path)` 按 `raw/NN.md`（写盘后才会有）。
        """
        def _on_file(words, size, group=None):
            try:
                with _PROGRESS_LOCK:
                    st = progress.data.setdefault("stages", {}).setdefault("4", {})
                    st["current_chapter"] = chapter_no
                    st["current_chars"] = int(words)
                    st["current_group_words"] = int(words)
                    st["current_group_bytes"] = int(size)
                    st["current_chars_source"] = "file"
                    st["current_chars_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    if group is not None:
                        st["current_group"] = int(group.get("idx") or 0) or st.get("current_group")
                    progress.save()
                # 段群进度进账（2026-11-03）：按盘上 gK.md 的个数与合计字数
                _note_parts(chapter_no)
            except Exception:                                # noqa: BLE001
                pass
        return _on_file

    def _poll_file(path, cb, stop_ev):
        """整章单发时轮询 `raw/NN.md`：文件一出现/一长大就回报真实字数。"""
        last = None
        while not stop_ev.wait(2.0):
            try:
                if not path.exists():
                    continue
                size = path.stat().st_size
                if size == last:
                    continue
                last = size
                cb(count_cn_words(read_text(path)), size)
            except Exception:                                # noqa: BLE001
                pass

    def _note_parts(chapter_no):
        """把段群进度写进 progress（实现见模块级 note_parts，便于单测）。"""
        note_parts(raw_dir.parent / "_parts", raw_dir, progress, chapter_no,
                   total_groups=int(_ch_state.get("total_groups") or 0))

    def _group_progress(chapter_no):
        """段群开始回调（2026-10-27，加强模式）：把“第几群 / 本群配额”写进进度。

        为什么需要：整章 5 群、每群十几分钟，只报整章字数的话，用户看到
        “已写 1 字”无法判断是在第 1 群刚起步还是第 3 群卡住。按群归零后，
        界面能说“第 2/5 群 · 本群 800/1400 字”——这是可核对的事实。
        """
        def _on_group(group, total_groups):
            _ch_state["chars"] = 0
            _ch_state["last_write"] = 0.0
            _ch_state["group"] = group["idx"]
            try:
                with _PROGRESS_LOCK:
                    st = progress.data.setdefault("stages", {}).setdefault("4", {})
                    st["current_chapter"] = chapter_no
                    st["current_group"] = int(group["idx"])
                    st["total_groups"] = int(total_groups)
                    st["current_group_quota"] = int(group["quota"])
                    st["current_chars"] = 0
                    st["current_group_words"] = 0
                    st["current_group_bytes"] = 0
                    st["current_text_chars"] = 0
                    st["current_chars_source"] = "pending"
                    st["current_chars_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    progress.save()
                # 段群进度进账（2026-11-03）：让"已落盘 4/5 群"在界面上看得见
                _note_parts(chapter_no)
            except Exception:                                # noqa: BLE001
                pass
        return _on_group

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

    # B2：章间会话续接（默认关）—— 只有“引擎开了续接”且“上一章成功”时才传下去
    chapter_session = None
    # ---- _finalize：写盘之后的全部收尾（摘要/出场/记账/标完成）----
    # 抽成一处：主循环成功路径与崩溃恢复路径共用，避免两份同步逻辑漂移。
    # 全程零 LLM 调用。result=None 表示“本轮没有新的 LLM 调用”（崩溃恢复重放），
    # 此时不做熔断累加也不重复记账（上一轮已记过）。
    def _finalize(n, chap_path, result, check):
        text = read_text(chap_path)
        sm = SUMMARY_RE.search(text)
        summary = sm.group(1).strip() if sm else f"（第{n}章，未附摘要）"
        # append_chapter_summary 是同章覆盖语义：重放不会污染 global_summary
        # （重调 LLM 重写整章才会，那条路已被“正文合格就不重写”堵死）。
        append_chapter_summary(rolling_path, n, summary, max_recent=max_recent)
        sync_appearances_after_chapter(text, n, total)

        # 确定性判据（字数/占位符/退化/标点）优先，自评分只作参考 —— 见 _chapter_status
        status = _chapter_status(check, quality_threshold)
        needs_rewrite = status == "needs_rewrite"
        progress.mark_chapter_done(n, 4)
        if status == "invalid":
            # 不写 ok、也不自动重写（重写=烧钱，属"补长"那一件事，另开）。
            # 断点续跑仍会重跑它：is_chapter_complete 本身就含字数区间。
            _bad = progress.data.setdefault("stages", {}).setdefault("4", {}) \
                .setdefault("invalid", {})
            _bad[str(n)] = {"why": "；".join(list(check.errors)[:3]),
                            "words": check.word_count, "quality": check.quality}
            progress.save()
            print(f"[stage4] 第{n}章判**不合格**（{check.word_count} 字，"
                  f"自评 {check.quality}）：{'；'.join(list(check.errors)[:2])}")
        if needs_rewrite:
            nr = progress.data["stages"]["4"].setdefault("needs_rewrite", [])
            if n not in nr:
                nr.append(n)
            progress.save()

        cost_est = 0.0
        if result is not None and db and run_id:
            cost_est = cost.estimate_cost_yuan(
                result["tokens"], result["tokens_out"], model=result.get("model"),
                provider=result.get("provider"), role=result.get("model_key"),
                cache_read=result.get("cache_read", 0)) if cost else 0.0
            db.log_chapter(run_id, 4, n, status, quality=check.quality, cost_yuan=cost_est)
        if result is not None and cost and not result.get("already_charged"):
            # 章与章之间必须查熔断：orchestrator 只在阶段之间查，而一章就能烧掉几元，
            # 不在这里拦，限额对“一次跑几十章”这种最该保护的场景恰好完全失效。
            # 加强模式例外：段群调用已逐群 charge 过（_group_run），这里再 charge
            # 会把一章记两遍账 —— 熔断检查也已在群间做过。
            if cost.charge_cost(run_id, 4, n, result) == "pause":
                _detail = _pause_detail(cost, run_id)
                print(f"[stage4] 熔断停止[{_detail['reason']}]：{_detail['message']}"
                      "—— 停止本阶段")
                progress.data.setdefault("budget", {})["paused"] = True
                progress.save()
                return "budget"
        print(f"[stage4] 第{n}章完成 {check.summary()}"
              + ("（崩溃恢复：补账，未重调 LLM）" if result is None else ""))
        return None

    for n in todo:
        # P0-4（2026-10-01）：章间检查停止请求 —— 不在这里查，按了“停止”
        # 也要跑完整个阶段（几十章）才轮到 orchestrator 的阶段边界检查。
        if _stop_requested():
            print(f"[stage4] 收到停止请求，停在第{n}章开始前"
                  "（已完成章节保留，断点续跑不重写）")
            aborted = "stopped"
            break
        # 施工权被夺（2026-11-02，P5 · L-2 的另一半）：作者在界面点“接管”后，
        # 锁文件的 owner 变成 human —— 流水线必须**真的停手**，否则"接管"只是
        # 改了一个谁都不看的文件，模型还在照跑照计费。
        # 放在章前检查：一章之内不再打断，避免留下半截（残稿落地是另一条，别混）。
        try:
            from utils import lock_guard
            _stop_why = lock_guard.check_pipeline_should_stop("data/state/run.lock")
        except Exception:                                    # noqa: BLE001
            _stop_why = None        # 判锁坏了不该拖垮写作（护栏在 API 侧）
        if _stop_why:
            print(f"[stage4] {_stop_why}")
            aborted = "preempted"
            break
        # “正在写第 N 章”（2026-10-26，用户“永恒 29%”/“Agent 跑起来完全看不见”）：
        # 这一行进 progress.json 后，/state 的 stages[4].current_chapter 就能回答
        # “现在写到第几章、总共几章”。此前 progress 只在**章写完之后**才动，
        # 一章写十几分钟期间外部只看到 state=running、别的什么都没有。
        # 失败不影响写作主链路（进度观测坏了不该拖垮出稿）。
        # 顺带重置流式字数（2026-10-26）：整章单发与拆群生成共用同一个计数，
        # 放在章开头统一重置，两种模式都不会把上一章的字数带进来。
        _ch_state["chars"] = 0
        _ch_state["last_write"] = 0.0
        _ch_state["group"] = 0
        _ch_state["buf"] = []      # 换章清空：残稿是"这一章"的半截，不能带上一章的字
        # 本章起点 token（用来算"为这一章补长烧了多少"）
        try:
            _ch_state["tok0"] = cost.tokens_used(run_id) if (cost and run_id) else 0
        except Exception:                                # noqa: BLE001
            _ch_state["tok0"] = 0
        try:
            _st4 = progress.data.setdefault("stages", {}).setdefault("4", {})
            _st4["current_chapter"] = n
            # 清掉上一章/上一轮遗留的段群字段：整章单发模式下它们没有意义，
            # 留着会让界面把“第 2/5 群”显示到下一章头上。
            for _k in ("current_group", "total_groups", "current_group_quota",
                       "current_chars", "current_chars_at", "current_chars_source",
                       "current_group_words", "current_group_bytes", "current_text_chars"):
                _st4.pop(_k, None)
            progress.save()
        except Exception as e:                               # noqa: BLE001
            print(f"[stage4] 写入 current_chapter 失败（不影响写作）: {type(e).__name__}: {e}")
        chap_path = raw_dir / f"{n:02d}.md"
        if n in _orphan_set:
            # 崩溃恢复：正文已在盘且合格，跳过一切 LLM 准备与调用，直接补账。
            _fin = _finalize(n, chap_path, None, check_chapter(chap_path, low, high))
            if _fin == "budget":
                aborted = "budget"
                break
            consec_fail = 0
            done_this_round += 1
            continue
        prev_path = raw_dir / f"{n - 1:02d}.md"
        prev_tail = extract_prev_tail(prev_path, 4) if prev_path.exists() else []
        # 章大纲按需补齐（2026-10-20，滚动规划）：stage3 只铺到"当前卷 + 下一卷"，
        # 写到窗口外时这里补一章。缺大纲就写，等于让模型即兴编一整章的走向 ——
        # 那是最贵的一种失败（写完才发现方向不对）。
        outline_rel = f"data/outline/chapters/{n:02d}.md"
        try:
            from stage3_chapter_outline import outline_ok, generate_chapters
            # ⚠️ 只在**文件不存在**时补齐。存在但字段不合格**绝不重写** ——
            # 那是上游（stage3）该修的东西，在写作阶段静默覆盖既有大纲会
            # 把已审阅过的产物换掉（实测踩到：夹具里少一个“功能”字段就触发了
            # 全量重写，章大纲里的角色名整批被替成占位名）。
            if not Path(outline_rel).exists():
                print(f"[stage4] 第{n}章大纲不在滚动窗口内，按需补齐…")
                _ok3, _msg3 = generate_chapters(cfg, proj, [n], client, task_dir, cost, run_id)
                if not _ok3:
                    if _fail(n, f"章大纲补齐失败：{_msg3}"):
                        aborted = "systemic"
                        break
                    continue
            elif not outline_ok(outline_rel):
                print(f"[stage4] 提示：第{n}章大纲字段不全（缺核心事件/涉及角色/功能之一），"
                      "按现状使用，不改写（该修的是 stage3）")
        except Exception as e:                             # noqa: BLE001
            print(f"[stage4] 章大纲补齐失败（将按缺失继续）: {type(e).__name__}: {e}")
        _task_text = build_chapter_task(cfg, proj, n, outline_rel,
                                        "data/setting/setting.json",
                                        "data/summaries/rolling.md", prev_tail)
        # 作者当场要求（2026-11-02，清单第 4 条）：运行中插一句，只对本章生效，
        # 用完即删。不改大纲、不重写已写的章。
        _iv = _take_intervention(n)
        if _iv:
            _task_text += intervene.render_block(_iv)
            print(f"[stage4] 本章执行作者当场要求（{len(_iv['instruction'])} 字）："
                  f"{_iv['instruction'][:50]}")
            try:
                progress.data.setdefault("stages", {}).setdefault("4", {}) \
                    .setdefault("intervened", {})[str(n)] = {
                        "instruction": _iv["instruction"][:300],
                        "at": _iv.get("at", "")}
                progress.save()
            except Exception:                            # noqa: BLE001
                pass
        task = client.write_task(task_dir, f"stage4_ch{n:02d}.md", _task_text)
        # ---- 加强模式（第一波防御 · 任务三，2026-10-22）----
        # chapter.enhanced_mode=true 时本章改为“拆段落群 → 逐群生成 → 拼装”，
        # 每群立即落盘 data/chapters/_parts/（段群级断点续跑，与任务二的
        # "已完成段落落盘"是同一件事）。默认 false = 走下面的整章单发，零影响。
        enhanced = bool(cfg.get("chapter", {}).get("enhanced_mode", False))
        # 段群调用 hook（2026-10-26 提前到这里）：整章单发与拆群生成共用一个，
        # 因为“字数不够时自动转加强模式”也要用它。
        _bstate = {"paused": False}

        def _group_run(tp):
            """段群调用：异常兜底 + 逐群记账 + 逐群查熔断（一章多群，
            不在群间查，限额对加强模式恰好失效）。

            ⚠️ `on_piece` 必须传：加强模式是长章的主路径，不传就等于
            “拆群时点停止没反应”——恰好在最需要停的时候失效。
            """
            r = _run_task_safe(client, tp,
                               on_piece=_piece_progress(n, _text_is_prose))
            if cost and cost.charge_cost(run_id, 4, n, r) == "pause":
                _bstate["paused"] = True
                r = dict(r)
                r["exit_code"] = 98          # 非零哨兵：write_chapter_groups 即停
            return r

        if enhanced:
            from utils.enhanced_chapter import write_chapter_groups
            print(f"[stage4] 第{n}章加强模式：拆段落群生成（chapter.enhanced_mode=true）")
            result = write_chapter_groups(cfg, proj, n, client, task_dir,
                                          outline_rel, prev_tail,
                                          run_hook=_group_run,
                                          on_group=_group_progress(n),
                                          on_file_progress=_file_progress(n),
                                          should_stop=_stop_requested)
            # 段群调用已在 _group_run 里逐群记账 —— _finalize 只补 log，
            # 不再重复 charge（否则一章记两遍账）。
            result["already_charged"] = True
            _note_parts(n)          # 拼装完 → 清掉"未拼装"条目；未拼装则记下真实群数
            if _bstate["paused"]:
                _detail = _pause_detail(cost, run_id)
                print(f"[stage4] 熔断停止[{_detail['reason']}]：{_detail['message']}"
                      "—— 停止本阶段（加强模式段群调用后）")
                progress.data.setdefault("budget", {})["paused"] = True
                progress.save()
                aborted = "budget"
                break
        else:
            # B2：session_id 只在引擎开启续接时才有意义（direct 客户端收下但忽略）
            # 整章单发也要看文件：hermes 用 write_file 写 raw/NN.md，text 事件不是正文。
            _fp_stop = threading.Event()
            threading.Thread(target=_poll_file,
                             args=(chap_path, _file_progress(n), _fp_stop),
                             daemon=True).start()
            try:
                result = _run_task_safe(client, task,
                                        on_piece=_piece_progress(n, _text_is_prose),
                                        session_id=chapter_session)
            finally:
                _fp_stop.set()
        # 用户主动中断（2026-10-26）：半截正文不落盘（llm_client 的 _apply_ops 已被
        # stop 跳过）。这**不是**失败 —— 记成失败会触发连败止损，把"你按了停止"
        # 误报成"系统性问题"，还可能让后续章节全部停摆。
        if result.get("stopped"):
            _p_path, _p_chars = _stash_partial(n, chap_path, _ch_state.get("buf"))
            if _p_path:
                print(f"[stage4] 第{n}章写作被用户中断 —— 已收到的 {_p_chars} 字"
                      f"已存为残稿: {_p_path}")
                print("[stage4] 残稿可在界面“残稿”里看一眼：采纳为草稿 / 丢掉。"
                      "采纳后仍算未完成，还得过审稿。")
                try:
                    _st4p = progress.data.setdefault("stages", {}).setdefault("4", {})
                    _st4p.setdefault("partial", {})[str(n)] = {
                        "path": _p_path, "chars": _p_chars,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                    progress.save()
                except Exception:                            # noqa: BLE001
                    pass
            else:
                print(f"[stage4] 第{n}章写作被用户中断（一个字都还没收到，无可存残稿）")
            aborted = "stopped"
            break
        if result["exit_code"] != 0:
            # 传输类异常（断网/503 耗尽）要说清自己 —— 否则用户按“子会话退出码
            # 非零”去查提示词/产物，而真因是网络（任务二排障口径）。
            # 失败原因必须带出来（2026-11-03 错误契约）：旧版这里只写“子会话退出码
            # 非零”，把 llm_client 抓到的 stderr 报文整条丢掉 —— progress.error 与
            # 界面上一个字都没有，排障只能猜。utils.error_contract 挑出报文里最该
            # 看的那一行（含上游 HTTP 码）。
            _why = error_contract.describe(result)
            if result.get("transport_error"):
                _why += "——检查网络与服务商状态后重跑即从断点续跑"
            if _fail(n, _why, [f"[stage4] 第{n}章子会话失败：{_why}"]):
                aborted = "consecutive_failures"
                break
            continue

        if not chap_path.exists():
            if _fail(n, "章节文件未生成", [f"[stage4] 第{n}章未生成章节文件"]):
                aborted = "consecutive_failures"
                break
            continue

        # 中文标点归一化（2026-10-18）：模型产出的半角引号 / 直角引号 / 单破折号
        # 在**校验之前**修掉。实测《雾港核心》三章：76 个半角 "、67 对“”——
        # stage6 提示词写着“全角标点”也不管用，故改为确定性替换（utils/text_norm）。
        _norm(chap_path, n)
        # 同质化/退化检测（P1）：零 token、写 data/state/homogeneity/<章>.json、只报不拦
        _homo_report(chap_path, n)
        # 一致性账本（P3）：把本章事件并进跨章账本，只报不拦
        _state_report(chap_path, n)
        check = check_chapter(chap_path, low, high)
        # ---- 打回重生成循环（第一波防御 · 任务一，2026-10-22）----
        # 校验不过关 → 自动打回重生成，上限 gates.hard_validate_regen_rounds（默认 2）。
        # 每轮策略：字数只差一点（>= 下限 60%）→ 定向扩写（读原稿补量，实证比整章
        # 重写有效）；其余（差太多 / 退化 / 标点不过关）→ 整章重生成，并把上一轮
        # 的问题清单写进任务反馈，让模型知道要修什么。
        # 仍不过关 → 停在“待人工”，绝不混入合格品（_fail 记失败章）。
        # 每轮调用都记账 + 查熔断（与扩写同口径：一章多轮调用同样可能烧穿限额）。
        regen_rounds = 0
        regen_actions = []
        auto_enhanced = False      # 本章是否已自动兜底过一次（防无限拆群）
        while not check.ok and not aborted and regen_rounds < regen_max:
            # 单章补长上限（2026-11-02，清单第 6 条）：实测一章补长能烧到 200 万 token
            # 还失败，而同样任务直连只要 6.5 万。超线就停手、说清"补不上去"，
            # 不许烧到 run 级熔断才发现（那时钱已经花了）。
            if regen_token_limit > 0 and cost and run_id:
                try:
                    _used = cost.tokens_used(run_id) - _ch_state.get("tok0", 0)
                except Exception:                        # noqa: BLE001
                    _used = 0
                if _regen_over_budget(_used, regen_token_limit):
                    print(f"[stage4] 第{n}章补长已烧 {_used} token"
                          f"（单章上限 {regen_token_limit}）—— 停手，这一章补不上去")
                    print("[stage4]    先看模型与提示词，别继续烧；"
                          "调大写 config/system.yaml 的 chapter.regen_token_limit 再试")
                    check.errors.append(
                        f"补长超出单章 token 上限（已用 {_used} ≥ {regen_token_limit}），"
                        "停手待人工处理")
                    break
            regen_rounds += 1
            # ---- 字数差太多 → 用加强模式补量（2026-10-26）----
            # 用户原话：“有些弱模型无论怎么骂就是写不长，像后台有个看不见的开关
            # 拧死了一样。所以才需要那个加强模式来强行拼凑。”
            #
            # 对这类失败，“整章重生成”是**无效**的：同一个模型、同一个目标、
            # 同一套提示词，只会再短一次（白烧一遍输入）。拆段落群才解得开 ——
            # 每群只背 1500 字配额，弱模型也做得到，拼起来才够长。
            #
            # ⚠️ 但这招比“定向扩写”贵（拆 k 群 = k 次调用），所以**只在差太多时**
            # 用：字数已到下限 60% 时先试扩写（1 次），扩写补不上再拆群。
            # 判据与 can_expand 同一个阈值，两个分支不会互相打架。
            if ((not check.degenerate) and check.word_count < low
                    and check.word_count < int(low * 0.6)
                    and not enhanced and not auto_enhanced):
                auto_enhanced = True
                action = "加强模式补量"
                print(f"[stage4] 第{n}章字数 {check.word_count} < {low}，"
                      "整章重生成对弱模型无效 —— 自动转加强模式（拆段落群逐群补足）")
                from utils.enhanced_chapter import (write_chapter_groups as _wcg,
                                                    clear_parts as _cp)
                _cp(n)
                gres = _wcg(cfg, proj, n, client, task_dir, outline_rel, prev_tail,
                            run_hook=_group_run, on_group=_group_progress(n),
                            on_file_progress=_file_progress(n),
                            should_stop=_stop_requested)
                gres["already_charged"] = True
                regen_actions.append(action)
                if _bstate["paused"]:
                    aborted = "budget"
                    break
                if gres.get("stopped"):
                    aborted = "stopped"
                    break
                if chap_path.exists():
                    _norm(chap_path, n)
                    check = check_chapter(chap_path, low, high)
                continue
            can_expand = (not check.degenerate
                          and check.word_count < low
                          and check.word_count >= int(low * 0.6)
                          and n not in expanded)
            if can_expand:
                expanded.add(n)
                action = f"扩写第{regen_rounds}轮"
                print(f"[stage4] 第{n}章校验不过（字数 {check.word_count} < {low}）"
                      f" —— 打回：定向扩写（第 {regen_rounds}/{regen_max} 轮）")
                gtask = client.write_task(task_dir, f"stage4_ch{n:02d}_expand.md",
                                          build_expand_task(cfg, proj, n,
                                                            check.word_count, low, high))
            else:
                action = f"重生成第{regen_rounds}轮"
                print(f"[stage4] 第{n}章校验不过（{check.errors[:2]}）"
                      f" —— 打回：整章重生成（第 {regen_rounds}/{regen_max} 轮）")
                _fb = ("\n\n## 上一轮校验反馈（本轮必须逐条修复）\n"
                       + "".join(f"- {e}\n" for e in check.errors[:5]))
                if enhanced:
                    # 加强模式：整章打回也走拆群 —— 单发重写会绕开拆群语义，
                    # 而旧段群缓存必须作废（否则 part_ok 会把不过关的群当断点复用）。
                    from utils.enhanced_chapter import write_chapter_groups, clear_parts
                    clear_parts(n)
                    gres = write_chapter_groups(
                        cfg, proj, n, client, task_dir, outline_rel, prev_tail,
                        run_hook=_group_run, chapter_feedback=_fb.strip(),
                        on_group=_group_progress(n),
                        on_file_progress=_file_progress(n),
                        should_stop=_stop_requested)
                    gres["already_charged"] = True
                    regen_actions.append(action)
                    if _bstate["paused"]:
                        aborted = "budget"
                        break
                    if gres.get("exit_code") != 0 and not chap_path.exists():
                        break
                    _norm(chap_path, n)
                    check = check_chapter(chap_path, low, high)
                    continue
                gtask = client.write_task(task_dir, f"stage4_ch{n:02d}.md",
                                          build_chapter_task(cfg, proj, n,
                                                             outline_rel,
                                                             "data/setting/setting.json",
                                                             "data/summaries/rolling.md",
                                                             prev_tail) + _fb)
            regen_actions.append(action)
            _fp_stop = threading.Event()
            threading.Thread(target=_poll_file,
                             args=(chap_path, _file_progress(n), _fp_stop),
                             daemon=True).start()
            try:
                # 打回/扩写同样走流式（2026-10-30）：不带 on_piece 就退回阻塞式
                # run_task，“停止”在重试轮里会再失灵一次 —— 而重试恰恰是最长的路径。
                gres = _run_task_safe(client, gtask,
                                      on_piece=_piece_progress(n, _text_is_prose))
            finally:
                _fp_stop.set()
            if cost:
                if cost.charge_cost(run_id, 4, n, gres) == "pause":
                    _detail = _pause_detail(cost, run_id)
                    print(f"[stage4] 熔断停止[{_detail['reason']}]：{_detail['message']}"
                          "—— 停止本阶段（打回重生成调用后）")
                    progress.data.setdefault("budget", {})["paused"] = True
                    progress.save()
                    aborted = "budget"
                    break
            if gres["exit_code"] == 0 and chap_path.exists():
                _norm(chap_path, n)
                check = check_chapter(chap_path, low, high)
        if aborted == "budget":
            # 中途熔断：本章处置无意义（下轮续跑会重打回），不落留痕定论
            break
        # 留痕（任务一）：字数、问题清单、处置（通过/重生成后通过/待人工）
        from utils.validation_log import log_chapter_validation
        if check.ok:
            log_chapter_validation(n, check, "passed",
                                   regen_rounds=regen_rounds, actions=regen_actions)
        else:
            log_chapter_validation(n, check, "regenerated",
                                   regen_rounds=regen_rounds, actions=regen_actions)
        if not check.ok:
            # 退化（正文够长但内容是垃圾）单独标注：这类失败与“字数不足”
            # 的处置不同 —— 重试往往没用，多半是模型/提示词层面出了问题。
            _manual = (f"（已打回重生成 {regen_rounds} 轮仍未过关，停在**待人工**；"
                       "留痕见 data/state/validation/）" if regen_rounds
                       else "")
            if check.degenerate:
                detail = "正文退化: " + "; ".join(check.degenerate)
                extra = [f"[stage4] 第{n}章正文退化（字数 {check.word_count} 达标但内容无效）:"]
                extra += [f"          - {d}" for d in check.degenerate]
                extra.append(f"          指标: {check.degen_metrics}")
                hit = _fail(n, detail + _manual, extra)
            else:
                hit = _fail(n, "校验失败: " + "; ".join(check.errors) + _manual,
                            [f"[stage4] 第{n}章校验失败: {check.errors[:2]}{_manual}"])
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

        # 收尾（摘要/出场/记账/标完成）统一走 _finalize —— 与崩溃恢复路径共用，
        # 避免两份同步逻辑漂移。
        if _finalize(n, chap_path, result, check) == "budget":
            aborted = "budget"
            break
        # “写完这章就停”（2026-11-02，F04）：与“中断”是两件事 ——
        # 中断立刻掐断（半截存残稿），这个等本章入稿+标完成后再停。
        # 放在 _finalize 之后：状态、摘要、记账都已经落好，断了也不丢东西。
        try:
            from utils import pause_flag
            if pause_flag.is_requested():
                _pl = pause_flag.consume()
                print("[stage4] 收到“写完这章就停”：第%d章已完整入稿，就此停手"
                      "（点运行会从第%d章继续）" % (n, n + 1))
                _ = _pl
                aborted = "paused"
                break
        except Exception:                                    # noqa: BLE001
            pass

    # 阶段结束（无论 done/failed/中断）都清掉“正在写第 N 章”——
    # 否则断点续跑前 /state 会一直显示上一轮停在的那个章号，看起来像卡住了。
    try:
        _st4 = progress.data.get("stages", {}).get("4", {})
        for _k in ("current_chapter", "current_chars", "current_chars_at",
                   "current_group", "total_groups", "current_group_quota",
                   "current_chars_source", "current_group_words",
                   "current_group_bytes", "current_text_chars",
                   "rewrite_chapter"):
            _st4.pop(_k, None)
        # 显式落盘：下面的 set_stage 各分支都会 save，但把“观测字段清理”
        # 依赖后续调用保存，等于让下次加分支的人踩同一个坑。
        progress.save()
    except Exception:                                        # noqa: BLE001
        pass
    if aborted:
        remaining_failed = progress.failed_chapters(4)
        if aborted == "paused":
            # 与 stopped 同口径：不是失败，是"在断点上下车"。
            # paused_at 是给 orchestrator 看的信号：它必须**在自动重试之前**收尾，
            # 否则"暂停"会被重试机制立刻续上（那就等于没停）。
            progress.set_stage(4, "pending",
                               paused_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            return False, ("stage4 已按你的要求**在本章结束后停手**（本轮完成 %d 章）。"
                           "刚写的那一章是完整的、已计入完成；"
                           "点“运行”会从下一章继续（断点续跑，不重写已完成的章）"
                           % done_this_round)
        if aborted == "stopped":
            # 用户主动中断 ≠ 章节失败（2026-10-26）：状态回到 pending，**不标 failed**。
            # 标 failed 会让界面显示“阶段 4 失败”，用户以为自己把东西弄坏了；
            # 而且 failed 语义是"跑过但没成功"，中断语义是"还没跑完"，两回事。
            progress.set_stage(4, "pending")
            _partial = (progress.data.get("stages", {}).get("4", {}) or {}).get("partial") or {}
            _kept = ("；本次已收到的半截存进了残稿（data/state/truncated/），"
                     "可在界面采纳或丢弃" if _partial else "")
            return False, ("stage4 被**用户中断**（本轮完成 %d 章）—— "
                           "断点续跑不会重写已完成章节%s" % (done_this_round, _kept))
        if aborted == "preempted":
            # 作者接管施工权（2026-11-02）：这也不是失败，是**人把笔拿走了**。
            # 同样回 pending；话要说清是谁停的、怎么继续 —— 否则用户看到
            # "阶段 4 失败"会以为自己弄坏了，或者以为系统崩了。
            progress.set_stage(4, "pending")
            return False, ("stage4 因**作者接管施工权**停手（本轮完成 %d 章）—— "
                           "已完成的章保留，当前章未开始；"
                           "要继续请在界面点“释放施工权”后重跑（断点续跑）"
                           % done_this_round)
        # 阶段级 error 带上第一条失败原因（2026-11-03）：orchestrator 的重试判据与
        # 界面都读它 —— 只写 failed_count 等于让上层"知道几章挂了、不知道为什么"。
        _first_why = error_contract.first_failure(progress, 4)
        progress.set_stage(4, "failed", failed_count=len(remaining_failed),
                           error=_first_why or None)
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
        _first_why = error_contract.first_failure(progress, 4)
        progress.set_stage(4, "failed", failed_count=len(remaining_failed),
                           error=_first_why or None)
        return False, f"stage4 有 {len(remaining_failed)} 章失败" + (
            f"（首条：{_first_why}）" if _first_why else "")
    # 整本齐 → done；只把你要的章写完、范围外还缺 → partial（不标完成）。
    if chapters:
        _verdict = _partial_or_done(chapters, "")
        if _verdict is False:
            return False, f"stage4 按范围完成（本轮写了第 {done_this_round} 章）"
        # ⚠️ True 不能在这里 return（2026-11-04 修）：_partial_or_done 只在
        # partial 分支里写 progress，True 分支什么都不写 —— 提前 return 会让
        # 阶段状态永远停在 running（界面上像卡死），而正文其实已经写完。
    progress.set_stage(4, "done")
    return True, f"stage4 完成（{total} 章）"


# ---------------------------------------------------------------- engine: self
def _self_target_words(cfg):
    """本项目的单章字数区间（与 run_stage 取同一个键与同一个兜底）。"""
    return tuple(cfg.get("chapter", {}).get("target_words", [2000, 3000])[:2])


def _self_pending(cfg, proj, chapters=None):
    """待写章号（与 run_stage 的断点判据同一条：`is_chapter_complete`）。

    ⚠️ 刻意**不**复用 run_stage 内部的 todo 计算（那段还混着失败章/孤儿章/
    讨论时效等只在"真跑"时才有意义的判据）。self 模式下"这章要不要写"只看
    盘上正文合不合格 —— 这是唯一可信的事实，也是执行方唯一能影响的东西。
    """
    from utils import writing_form as _wf
    total = int(proj.get("book", {}).get("chapters", 10))
    para_ok = _wf.applicability(proj).get("para_structure", True)
    tw = _self_target_words(cfg)
    raw_dir = Path("data/chapters/raw")
    pending = [n for n in range(1, total + 1)
               if not is_chapter_complete(raw_dir / ("%02d.md" % n), *tw,
                                          para_structure=para_ok)]
    if chapters:
        pending, _out = _apply_scope(pending, chapters)
    return total, pending


def self_done(cfg, proj, progress=None, chapters=None, **_):
    """全部待写章都合格才算阶段 4 完成（**不能**用 raw 目录非空当判据）。"""
    total, pending = _self_pending(cfg, proj, chapters)
    if pending:
        return False, ("还有 %d 章没写或不合格：%s" % (len(pending), pending[:8]))
    return True, "全书 %d 章正文齐备" % total


def settle_self(cfg=None, proj=None, progress=None, **_):
    """把“正文已在盘、账未记”的章补账（零 token，确定性）。

    为什么必须有：`engine: self` 下没人跑 run_stage，所以**摘要不会进
    rolling.md、出场记录不会更新、progress 不会记完成**。而滚动摘要正是下一章
    任务书的输入 —— 不补，第 N+1 章的任务书就会缺上一章的摘要。
    只补"还没记过的"章（以 rolling.md 里有没有这一章为准），重复调用不重复记。
    """
    total = int(((proj or {}).get("book") or {}).get("chapters", 0) or 0)
    raw_dir = Path("data/chapters/raw")
    if not raw_dir.is_dir():
        return
    rolling_path = Path("data/summaries/rolling.md")
    if not rolling_path.exists():
        write_rolling(rolling_path, {"global_summary": "", "chapters": {}})
    max_recent = int(((cfg or {}).get("chapter") or {}).get("summary_every", 5))
    from utils import writing_form as _wf
    para_ok = _wf.applicability(proj or {}).get("para_structure", True)
    tw = _self_target_words(cfg or {})
    settled = []
    for p in sorted(raw_dir.glob("*.md")):
        if not p.stem.isdigit():
            continue
        n = int(p.stem)
        rolling = load_rolling(rolling_path)
        if n in (rolling.get("chapters") or {}):
            continue
        # 只给**合格**的章补账：半成品正文补摘要 = 把"这章写完了"写进账本，
        # 而它其实还缺字数/段落结构（那正是下一轮要重写的那一章）。
        if not is_chapter_complete(p, *tw, para_structure=para_ok):
            continue
        try:
            text = read_text(p)
        except Exception as e:                              # noqa: BLE001
            print("[stage4] 补账跳过第%d章（读取失败）：%s" % (n, type(e).__name__))
            continue
        sm = SUMMARY_RE.search(text)
        summary = sm.group(1).strip() if sm else "（第%d章，未附摘要）" % n
        try:
            append_chapter_summary(rolling_path, n, summary, max_recent=max_recent)
        except Exception as e:                              # noqa: BLE001
            print("[stage4] 第%d章摘要补记失败：%s: %s" % (n, type(e).__name__, e))
            continue
        try:
            sync_appearances_after_chapter(text, n, total or None)
        except Exception as e:                              # noqa: BLE001
            print("[stage4] 第%d章出场记录补记失败（不影响正文）：%s" % (n, type(e).__name__))
        settled.append(n)
        if progress is not None:
            try:
                progress.mark_chapter_done(n, stage=4)
            except Exception as e:                          # noqa: BLE001
                print("[stage4] 第%d章完成标记补记失败：%s" % (n, type(e).__name__))
    if settled:
        print("[stage4] 补账完成：第 %s 章的摘要/出场/完成标记已补（零 token）" % settled)


def emit_tasks(cfg, proj, progress=None, task_dir=None, chapters=None, **_):
    """产出阶段 4 的任务书：**一次只出下一章**。

    为什么不是一次性把待写章都出完：每章任务书要吃“上一章末尾衔接段”和
    “滚动摘要”，而这些只有上一章真写完之后才存在。一次发 20 份 = 后 19 份
    的衔接段与摘要全是空的，写出来必然断气。写法因此定为"一章一交回"：
    写完 → 重跑 → 出下一章任务书。
    """
    from utils import self_tasks as st
    total, pending = _self_pending(cfg, proj, chapters)
    if not pending:
        return st.summarize(4, [], done=True, hint="全书 %d 章正文齐备" % total)

    settle_self(cfg, proj, progress)          # 先把已落盘章的摘要补进 rolling.md
    raw_dir = Path("data/chapters/raw")
    rolling_path = Path("data/summaries/rolling.md")
    if not rolling_path.exists():
        write_rolling(rolling_path, {"global_summary": "", "chapters": {}})
    n = pending[0]
    prev_path = raw_dir / ("%02d.md" % (n - 1))
    prev_tail = extract_prev_tail(prev_path, 4) if prev_path.exists() else []
    outline_rel = "data/outline/chapters/%02d.md" % n
    if not Path(outline_rel).exists():
        print("[stage4] 提示：第%d章大纲不在滚动窗口内（缺 %s）—— 任务书按缺失继续，"
              "建议先跑阶段 3 补齐" % (n, outline_rel))
    body = build_chapter_task(cfg, proj, n, outline_rel, "data/setting/setting.json",
                              "data/summaries/rolling.md", prev_tail)
    low, high = _self_target_words(cfg)
    criteria = ("data/chapters/raw/%02d.md 有标题、正文 %d~%d 字（含 10%% 容差），"
                "文末保留 `<!-- summary: ... -->` 与 quality 注释；不得写占位符"
                % (n, low, high))
    task = st.write_task(4, "ch%02d.md" % n, body, ["data/chapters/raw/%02d.md" % n],
                         criteria,
                         note="本章写完再重跑 —— 下一章的任务书要用本章末尾衔接段与摘要",
                         task_dir=task_dir, stage_name="写作")
    return st.summarize(4, [task],
                        hint="本次只出第 %d 章的任务书（待写 %d 章，全书 %d 章）。"
                             "写完重跑即补账并出下一章。" % (n, len(pending), total))


def main():
    parser = argparse.ArgumentParser(description="NovelForge 阶段4：逐章写作")
    parser.add_argument("--task-dir", default="data/state/tasks")
    parser.add_argument("--chapters", default=None,
                        help="只写这几章：1-3 或 1,3,5 或 1-3,7（不填=全部未完成章）")
    args = parser.parse_args()
    cfg = load_config_yaml("config/system.yaml")
    proj = load_config_yaml("config/project.yaml")
    progress = ProgressManager("data/state/progress.json")
    try:
        scope = parse_chapter_scope(args.chapters)
    except ValueError as e:
        print(f"[stage4] 章范围写错了：{e}")
        return 2
    ok, msg = run_stage(cfg, proj, progress, None, None, task_dir=args.task_dir,
                        chapters=scope)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
