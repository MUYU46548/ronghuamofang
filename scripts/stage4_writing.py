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

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.cost_tracker import pause_detail as _pause_detail
from utils.verify_chapter import check_chapter, count_cn_words, is_chapter_complete
from utils.summary_chain import append_chapter_summary, extract_prev_tail, compress_recent, load_rolling, write_rolling
from utils.template_loader import load_template
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

SUMMARY_RE = re.compile(r"<!--\s*summary:\s*(.+?)\s*-->", re.IGNORECASE | re.S)

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
        print("[stage4] WARN style_reference 指向的文件不存在：%s —— 本次按「未提供范文」写作"
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
              "—— 本次按「未提供范文」写作，请把正文粘进去"
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


def _run_task_safe(client, task, **kw):
    """client.run_task 的异常兜底（第一波防御 · 任务二，2026-10-22）。

    断网 / 503 风暴 / DNS 挂了这类**网络级故障**：llm_client 内部重试
    （providers.*.retries，默认 5 次，指数退避）耗尽后会 raise RuntimeError ——
    异常若穿透 run_stage，整个 run 会被 orchestrator 的 finally 收敛成
    `crashed`，而不是「挂起当前章、保存进度、等恢复后续跑」。

    现在统一在这里转成 `exit_code=1` 的失败结果：走的就是既有的
    「子会话失败 → mark_chapter_failed → 连败止损」路径 —— 已完成章节
    的 progress 逐章落盘，重启后断点续跑不重做它们。

    ⚠️ 只兜「客户端调用」这一步：任务构造（write_task/build_chapter_task）
    的异常仍要穿透 —— 那是代码/输入缺陷，吞掉只会让每章都悄悄失败。
    """
    try:
        return client.run_task(task, **kw)
    except Exception as e:                                    # noqa: BLE001
        print(f"[stage4] ⚠ 子会话调用异常（已按失败处理，进度保留）: "
              f"{type(e).__name__}: {str(e)[:200]}")
        return {"exit_code": 1, "stdout_tail": "", "tokens": 0, "tokens_out": 0,
                "cost_yuan": 0.0, "estimated": True,
                "provider": getattr(client, "provider", "unknown"),
                "model": getattr(client, "model", "unknown"),
                "requests": 0, "error": f"{type(e).__name__}: {e}",
                "transport_error": True}


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
    """从逐章大纲的「涉及角色」解析角色名列表。

    ## 支持两种真实格式（2026-10-18 修正）

    1. **多行列表**（当前流水线产出的真实形态）::

           - 涉及角色：
             - 沈砚（c015）：主角，落魄机关术师；被读数图与七锁摹痕一步步推出废屋。
             - 程砧（c016）：边城守卫队长；以军令与读数图促成同行。

    2. 单行顿号：`- 涉及角色：苏芷、观澜`
    3. 混合：`luxi 苏芷（写病历）、小林（夜班护士）`

    ⚠️ **旧实现只取「涉及角色」那一行**，多行格式下后续两行被整行丢掉，
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
    # ⚠️ 教训记录：这条函数改了四版，前三版都是「按顿号切整行」，每修好一种格式
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
    用户的判断正是：「设定库里扔了一堆语料都学不明白吗？」

    根因不是模型不学，是**没给它可学的东西**。内联后设定上下文从"一个路径"
    变成"本章相关的具体条目"，且与角色卡一样可被校验、可被计数。

    ## 匹配口径（保守，宁缺勿滥）

    - 条目名**出现在大纲正文**里 → 收录（地点/势力/规则/物品同规则）
    - `timeline` 事件文本含涉及角色名 → 收录（时间线是 locked 硬约束，漏了会写错年份）
    - `plot_fragments` 的 summary 含涉及角色名 → 收录；另附**未启用**（status=unused）
      的前 3 条作为「可用素材」——它们是 stage1 归并时留下的候选情节
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


_STYLE_WARNED = set()


def _style_blocks(proj):
    """文体对应的文风/段落规则（未接的文体回落叙事并告警一次）。"""
    from utils import writing_form as _wf
    app = _wf.applicability(proj)
    style, para, warn = _wf.style_blocks(app["code"])
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

    # 分支样例锚点（第 4 件 · 选项可视化）：用户在章大纲里选定的「样例文本」
    # 注入成 few-shot 参照。读章大纲里的优化点锚点，只挑 kind=sample 的 ——
    # 讨论意见/字段改写类锚点是给**人**看的决策记录，混进去会污染写作模型。
    # 全量注入不做 top-K 截断（用户口径：整套写作用不了多少 token，样例文是
    # 精心磨出来的，截断可能丢掉关键语气/节奏信息）。
    try:
        from utils.chapter_samples import chapter_samples
        chapter_samples_block = chapter_samples(n)
    except Exception as e:                              # noqa: BLE001
        # 注入失败不阻断写作（同 kb_context 的三态纪律：这里属「失败」要打印）
        print(f"[stage4] ⚠ 样例锚点注入失败（本章将无样例参照）: "
              f"{type(e).__name__}: {e}")
        chapter_samples_block = ""

    try:
        from utils.style_profile import load_snippets_block
        decompose_samples = load_snippets_block()
    except Exception as e:                              # noqa: BLE001
        print(f"[stage4] 参考片段注入失败（本章将无片段参照）: "
              f"{type(e).__name__}: {e}")
        decompose_samples = ""

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
    _, body = load_template("stage4_writing.md", {
        "n": n,
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
        "kb_context": kb_context,
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


def run_stage(cfg, proj, progress, db, cost, client=None, task_dir=None, run_id=None):
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

    # 崩溃窗口恢复（step 级 checkpoint，2026-10-21）：**磁盘有合格正文、但 progress
    # 没标 done** 的章 = 上轮在「写作落盘」之后、「标记完成」之前崩了（进程被杀 /
    # 断电 / 熔断恰好落在中间）。这类章的正文是好的，重调 LLM 重写一遍纯属浪费，
    # 还可能换来一版更差的。处理：走 _finalize 的幂等重放 —— 补摘要 + 补出场 +
    # 补记账，再标完成，全程零 LLM 调用。
    #
    # 为什么摘要必须补而不是跳过：滚动摘要是**下一章的输入**。崩溃前它没写进
    # rolling.md，下一章就会拿不到本章摘要。而 append_chapter_summary 是同章
    # 覆盖语义 —— 这里补一次是安全的；真正会污染全局摘要的是「重调 LLM 重跑整章」
    # （多压一行进 global_summary）。所以判据是：正文合格 → 只补账，不重写。
    _tw = cfg.get("chapter", {}).get("target_words", [2000, 3000])[:2]
    _raw_seen = {int(p.stem) for p in raw_dir.glob("*.md") if p.stem.isdigit()}
    orphan_ns = sorted(
        n for n in _raw_seen
        if n not in completed and n not in failed_ns
        and is_chapter_complete(raw_dir / f"{n:02d}.md", *_tw, para_structure=_para_ok))
    if orphan_ns:
        print(f"[stage4] 检测到 {len(orphan_ns)} 章「正文已在盘、账未记」"
              f"（上轮崩溃遗留）：{orphan_ns} —— 幂等补账，不重调 LLM")
        # 孤儿章必须进 todo：否则「全是孤儿」时下面的 not todo 会误判成
        # 「跳过（已完成）」，崩溃恢复路径永远走不到。
        todo = sorted(set(todo) | set(orphan_ns))
    _orphan_set = set(orphan_ns)

    if not todo:
        progress.set_stage(4, "done")
        return True, "stage4 跳过（已完成）"

    progress.set_stage(4, "running")
    low, high = cfg.get("chapter", {}).get("target_words", [2000, 3000])[:2]
    # 连续失败止损阈值（0 = 关闭）。见 _fail 的说明。
    max_consec_fail = int(cfg.get("gates", {}).get("stage4_max_consecutive_failures", 3))
    # 打回重生成上限（第一波防御 · 任务一）。0 = 关闭打回（行为退回本改动之前：
    # 校验不过直接判失败章）。
    regen_max = int(cfg.get("gates", {}).get("hard_validate_regen_rounds", 2))
    consec_fail = 0
    expanded = set()   # 本轮已扩写过的章节（每章至多一次，防空转）
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
    # ---- _finalize：写盘之后的全部收尾（摘要/出场/记账/标完成）----
    # 抽成一处：主循环成功路径与崩溃恢复路径共用，避免两份同步逻辑漂移。
    # 全程零 LLM 调用。result=None 表示「本轮没有新的 LLM 调用」（崩溃恢复重放），
    # 此时不做熔断累加也不重复记账（上一轮已记过）。
    def _finalize(n, chap_path, result, check):
        text = read_text(chap_path)
        sm = SUMMARY_RE.search(text)
        summary = sm.group(1).strip() if sm else f"（第{n}章，未附摘要）"
        # append_chapter_summary 是同章覆盖语义：重放不会污染 global_summary
        # （重调 LLM 重写整章才会，那条路已被「正文合格就不重写」堵死）。
        append_chapter_summary(rolling_path, n, summary, max_recent=max_recent)
        sync_appearances_after_chapter(text, n, total)

        needs_rewrite = check.quality is not None and check.quality < quality_threshold
        status = "needs_rewrite" if needs_rewrite else "ok"
        progress.mark_chapter_done(n, 4)
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
            # 不在这里拦，限额对「一次跑几十章」这种最该保护的场景恰好完全失效。
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
        # P0-4（2026-10-01）：章间检查停止请求 —— 不在这里查，按了「停止」
        # 也要跑完整个阶段（几十章）才轮到 orchestrator 的阶段边界检查。
        if _stop_requested():
            print(f"[stage4] 收到停止请求，停在第{n}章开始前"
                  "（已完成章节保留，断点续跑不重写）")
            aborted = "stopped"
            break
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
            # 把已审阅过的产物换掉（实测踩到：夹具里少一个「功能」字段就触发了
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
        task = client.write_task(task_dir, f"stage4_ch{n:02d}.md",
                                 build_chapter_task(cfg, proj, n,
                                                    outline_rel,
                                                    "data/setting/setting.json",
                                                    "data/summaries/rolling.md",
                                                    prev_tail))
        # ---- 加强模式（第一波防御 · 任务三，2026-10-22）----
        # chapter.enhanced_mode=true 时本章改为「拆段落群 → 逐群生成 → 拼装」，
        # 每群立即落盘 data/chapters/_parts/（段群级断点续跑，与任务二的
        # "已完成段落落盘"是同一件事）。默认 false = 走下面的整章单发，零影响。
        enhanced = bool(cfg.get("chapter", {}).get("enhanced_mode", False))
        if enhanced:
            from utils.enhanced_chapter import write_chapter_groups
            _bstate = {"paused": False}

            def _group_run(tp):
                """段群调用：异常兜底 + 逐群记账 + 逐群查熔断（一章多群，
                不在群间查，限额对加强模式恰好失效）。"""
                r = _run_task_safe(client, tp)
                if cost and cost.charge_cost(run_id, 4, n, r) == "pause":
                    _bstate["paused"] = True
                    r = dict(r)
                    r["exit_code"] = 98          # 非零哨兵：write_chapter_groups 即停
                return r

            print(f"[stage4] 第{n}章加强模式：拆段落群生成（chapter.enhanced_mode=true）")
            result = write_chapter_groups(cfg, proj, n, client, task_dir,
                                          outline_rel, prev_tail,
                                          run_hook=_group_run)
            # 段群调用已在 _group_run 里逐群记账 —— _finalize 只补 log，
            # 不再重复 charge（否则一章记两遍账）。
            result["already_charged"] = True
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
            result = _run_task_safe(client, task, session_id=chapter_session)
        if result["exit_code"] != 0:
            # 传输类异常（断网/503 耗尽）要说清自己 —— 否则用户按「子会话退出码
            # 非零」去查提示词/产物，而真因是网络（任务二排障口径）。
            _why = ("网络/传输异常（内部重试已耗尽）—— 检查网络与服务商状态后"
                    "重跑即从断点续跑" if result.get("transport_error")
                    else "子会话退出码非零")
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
        # 在**校验之前**修掉。实测《雾港核心》三章：76 个半角 "、67 对「」——
        # stage6 提示词写着「全角标点」也不管用，故改为确定性替换（utils/text_norm）。
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
        # 仍不过关 → 停在「待人工」，绝不混入合格品（_fail 记失败章）。
        # 每轮调用都记账 + 查熔断（与扩写同口径：一章多轮调用同样可能烧穿限额）。
        regen_rounds = 0
        regen_actions = []
        while not check.ok and not aborted and regen_rounds < regen_max:
            regen_rounds += 1
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
                        run_hook=_group_run, chapter_feedback=_fb.strip())
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
            gres = _run_task_safe(client, gtask)
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
            # 退化（正文够长但内容是垃圾）单独标注：这类失败与「字数不足」
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
    cfg = load_config_yaml("config/system.yaml")
    proj = load_config_yaml("config/project.yaml")
    progress = ProgressManager("data/state/progress.json")
    ok, msg = run_stage(cfg, proj, progress, None, None, task_dir=args.task_dir)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
