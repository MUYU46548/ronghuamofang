# -*- coding: utf-8 -*-
"""运行时域（GET 读侧）：后台任务查询、审查/校对报告、提示词模板、预估、节奏、章节历史。

## 这几个端点为什么归一类

它们都是**「查一个已经算好的东西」**：job 状态、报告 JSON、模板正文、
token 预估、拆书产物、章节备份列表。没有一个是写操作，
且都只读项目内的既有产物（路径一律经 `api.ROOT` 解析）。

## 路径统一走 `api.ROOT`（2026-09-20 修）

本模块的 `/review/report`、`/proofread/report`、`/book/pacing` 历史上用的是
**相对路径** `Path("data/...")`，即依赖进程 CWD；而 `/chapters/history` 等
用的是 `api.ROOT / ...`。两种语义并存，在 `--root` 场景下会**静默读错项目**：

    cwd = 书本A（有 review_report.json）
    --root 书本B（没有该文件）
    → GET /review/report 返回 200 且内容是**书本A**的数据

生产环境（Electron）恰好没暴露它，因为主进程是 `spawn(..., {cwd: ws})` 且
同时传 `--root ws` —— CWD 与 ROOT 相等。但 `--root` 这个参数的存在本身
就说明设计允许两者不同，所以这是个**潜伏缺陷**：一旦有人真的用它指向别的
书档，读到的是 CWD 的书。

现已全部改为 `api.ROOT / ...`。注意：Electron 场景 CWD==ROOT，**行为不变**；
测试用临时根时 CWD 也是临时根，同样不变。由「第 7 节．无相对路径 IO」守护。

## /prompts/get 的白名单

`api.prompt_path(name)` 是唯一入口：只放行 `prompts/stage[1-7]_*.md`，
挡住子目录与 `../`。**不能**在这里另写一份路径拼接——
白名单只能有一个实现，否则两处会漂移。
"""
import nf_api as api
import json
from pathlib import Path


def _p(h):
    """路径（去掉 query 与尾部 `/`），与 `do_GET` 里那个 `p` 完全同源。

    域模块**不重算** `h.path.split("?")[0].rstrip("/")`，而是走这里，
    因为「p 是怎么来的」只该有一个定义——否则哪天 nf_api 改了归一化规则，
    这里会静默漂移，表现为「某些带斜杠的路径 404」。
    """
    return api.norm_path(h)


def handle_jobs(h):
    """后台任务状态（?/jobs/<id>）。

    与 `/state.current_job` 的区别：这里按 id 查**任意**任务，
    前端可同时跟踪多个（比如一边跑 stage4 一边跑校对）。
    """
    jid = _p(h)[len("/jobs/"):]
    job = api.JOBS.get(jid)
    if not job:
        return 404, {"error": "job 不存在: " + jid}
    return 200, job


def handle_review_report(h):
    """审稿报告 JSON（data/outline/review_report.json）。"""
    report_path = api.ROOT / "data" / "outline" / "review_report.json"
    if not report_path.exists():
        return 404, {"error": "no report yet"}
    try:
        return 200, api.json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": str(e)}


def handle_review_decisions(h):
    """用户对审稿发现的决策（accept/ignore）。

    文件不存在时返回**空决策列表 + 200**，而不是 404：
    「还没做决策」是正常初态，前端不该在控制台刷 404。
    """
    dec_path = api.ROOT / "data" / "outline" / "review_report.decisions.json"
    if not dec_path.exists():
        return 200, {"decisions": []}
    try:
        return 200, api.json.loads(dec_path.read_text(encoding="utf-8"))
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": str(e)}


def handle_prompts_list(h):
    """提示词模板列表（prompts/ 白名单内）。"""
    try:
        items = api.list_prompts()
        return 200, {"items": items, "count": len(items),
                     "dir": "prompts", "history_dir": "prompts/history"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_prompts_get(h):
    """读取单个模板：`/prompts/get?name=x` 或 `/prompts/get/x` 两种写法都收。

    返回 `backups` 列表（最近 8 个）——用户在 GUI 里改坏模板后要能一键回退，
    所以备份列表必须在**读取**时一并给出，不能等写的时候才告诉他有备份。
    """
    q = h._query()
    p = _p(h)
    name = ((q.get("name") or [""])[0] if p == "/prompts/get"
            else p[len("/prompts/get/"):])
    path = api.prompt_path(name)
    if not path:
        return 404, {"error": "模板不存在或不在白名单: " + str(name)
                              + "（须匹配 prompts/stage[1-7]_*.md，禁止子目录与 ../）"}
    try:
        content = path.read_text(encoding="utf-8")
        st = path.stat()
        return 200, {
            "name": path.name,
            "content": content,
            "size": st.st_size,
            "modified": api.time.strftime("%Y-%m-%d %H:%M:%S",
                                          api.time.localtime(st.st_mtime)),
            "backups": [b.name for b in api.list_prompt_backups(path.name)[-8:]],
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_prompts_restore(h, body):
    """把一个历史备份恢复成当前模板：`{name, backup}`。

    ## 为什么必须走端点、不能让用户拿文件管理器覆盖

    `save_prompt` 每次写入都会把**当前版本**再备份一次。用户手工覆盖的话，
    那份"改坏的版本"**不会进备份链** —— 出问题后连对照物都没有，也就无从判断
    到底改动了什么。本端点复用 `save_prompt`，备份链保持连续：
    回滚动作本身也留痕（旧版仍可在 history/ 里找到）。

    参数 `backup` 必须是**该模板自己的**备份文件名（从 `/prompts/get` 的
    `backups` 字段拿），防止借这个端点读任意文件。
    """
    import nf_api as api

    name = api.prompt_name_ok(str(body.get("name") or "").strip())
    if not name:
        return 400, {"ok": False,
                     "error": "模板名不在白名单（须匹配 prompts/stage[1-7]_*.md）"}
    backup = str(body.get("backup") or "").strip()
    if not backup:
        return 400, {"ok": False, "error": "backup 必填（见 /prompts/get 的 backups 字段）"}

    allowed = {b.name for b in api.list_prompt_backups(name)}
    if backup not in allowed:
        return 400, {"ok": False,
                     "error": "备份不存在或不属于该模板: " + backup,
                     "available": sorted(allowed)[-8:]}
    bak_path = api.PROMPTS_HISTORY_DIR / backup
    try:
        content = api.nf_read_text(bak_path)
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": "读取备份失败: " + str(e)[:120]}

    ok, msg = api.save_prompt(name, content)
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "name": name, "restored_from": backup,
                 "message": "已回滚 " + name + " ← " + backup
                            + "（回滚前的版本也已存入 prompts/history/，可再回退）",
                 "detail": msg}


def handle_proofread_report(h):
    """校对报告（stage 5.5）。

    schema 与 review_report 对齐（含 `suggested_action`），
    故 GUI 可直接复用审稿界面组件与决策链路。
    """
    pp = api.ROOT / "data" / "outline" / "proofread_report.json"
    if not pp.exists():
        return 404, {"error": "暂无校对报告",
                     "hint": "先运行校对：POST /proofread/run，"
                             "或 python scripts/proofread.py"}
    try:
        return 200, api.json.loads(api.nf_read_text(pp))
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_estimate(h):
    """生成前 token / 费用预估（确定性，零 LLM 调用）。

    ?stage=4 只估阶段4；?stages=1,2,3 估指定阶段；?no_history=1 强制字符折算。
    三种取参口径的优先级：`stage` > `stages`。
    """
    try:
        q = h._query()
        stage_raw = (q.get("stage") or [""])[0].strip()
        stages_raw = (q.get("stages") or [""])[0].strip()
        stages = None
        if stage_raw:
            stages = [int(stage_raw)]
        elif stages_raw:
            stages = [int(x) for x in stages_raw.split(",") if x.strip().isdigit()]
        no_hist = (q.get("no_history") or ["0"])[0].lower() in ("1", "true", "yes")
        return 200, api.estimate_mod.estimate(stages, use_history=not no_hist)
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_book_pacing(h):
    """章节节奏。

    `?source=current` 实时算本书（读 data/chapters/*）；
    缺省读拆书产物 data/state/book_pacing.json。
    """
    try:
        q = h._query()
        src = (q.get("source") or [""])[0].strip()
        if src == "current":
            scope = (q.get("scope") or [""])[0].strip() or None
            return 200, api.book_split_mod.analyze_project_chapters(scope)
        bp = api.ROOT / "data" / "state" / "book_pacing.json"
        if not bp.exists():
            return 404, {"ok": False, "error": "尚无拆书结果",
                         "hint": "POST /book/split {path} 导入参考书，"
                                 "或用 /book/pacing?source=current 看本书节奏"}
        return 200, api.json.loads(api.nf_read_text(bp))
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_homogeneity(h):
    """同质化/退化报告（P1，零 token 只读）。

    `GET /homogeneity`               → 汇总（每章字数、findings、跳过原因）
    `GET /homogeneity?chapter=3`     → 第 3 章完整报告（含 metrics 与逐条 detail）

    **不重算**：只读 stage4/stage6 落盘的 JSON。重算会把"落盘那一刻"的结论
    换成"现在这版正文"的结论，而诊断的价值就在于它记的是当时那一版。
    """
    from utils import homogeneity as homo
    try:
        q = h._query()
        chapter = (q.get("chapter") or [""])[0].strip()
        if chapter:
            rep = homo.load_report(api.ROOT, chapter)
            if rep is None:
                return 404, {
                    "ok": False,
                    "error": "没有第 %s 章的同质化报告" % chapter,
                    "hint": "跑过 stage4（初稿）或 stage6（润色）之后才有；"
                            "也可单独测: python scripts/utils/homogeneity.py <章节.md>",
                }
            return 200, {"ok": True, "chapter": chapter, "report": rep}
        data = homo.collect_reports(api.ROOT)
        return 200, dict({"ok": True}, **data)
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_review_ledger(h):
    """七维评审账本（2026-10-20，零 token 只读）。

    `GET /review/ledger`           → 汇总 + 逐章一行（均分/完整与否/未举证维度）
    `GET /review/ledger?chapter=3` → 单章七维明细（含举证原句与未举证原因）

    数据来源是 stage5 落盘的 `data/review/`（`utils.review_ledger`），
    本端点**只读不重算** —— 重算是模型行为，不该由看板触发。
    """
    from utils import review_ledger as rl
    base = Path(api.ROOT) / "data" / "review"
    try:
        q = h._query()
        chapter = (q.get("chapter") or [""])[0].strip()
        if chapter:
            try:
                n = int(chapter)
            except ValueError:
                return 400, {"ok": False, "error": "chapter 必须是整数"}
            p = base / ("%02d.json" % n)
            if not p.exists():
                return 404, {"ok": False, "error": "第 %d 章还没有七维评审" % n}
            return 200, dict({"ok": True}, **json.loads(p.read_text(encoding="utf-8")))
        rows = []
        for p in sorted(base.glob("*.json")):
            if p.name == "summary.json":
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows.append({
                "chapter": d.get("chapter"),
                "mean": d.get("mean"),
                # 审美五问（2026-10-20）：独立字段，老账本没有 → None
                "aesthetic_mean": (d.get("aesthetic") or {}).get("mean"),
                "aesthetic_complete": bool(d.get("aesthetic_complete")),
                "complete": bool(d.get("complete")),
                "verified_count": d.get("verified_count"),
                "unverified": d.get("unverified") or [],
                "missing": d.get("missing") or [],
                "scores": {k: v.get("score") for k, v in (d.get("dims") or {}).items()},
            })
        summary = {}
        sp = base / "summary.json"
        if sp.exists():
            try:
                summary = json.loads(sp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                summary = {}
        return 200, {
            "ok": True,
            "dimensions": [{"code": d["code"], "label": d["label"]} for d in rl.DIMENSIONS],
            "aesthetic_questions": [{"code": q["code"], "label": q["label"]} for q in rl.AESTHETIC],
            "summary": summary,
            "chapters": rows,
            "exists": bool(rows),
        }
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_story_state(h):
    """一致性账本（P3，零 token 只读）。

    `GET /story_state`          → 摘要 + 全部冲突
    `GET /story_state?chapter=3`→ 只取第 3 章

    账本由 stage4 逐章增量更新（`utils.story_state.update_chapter`），
    本端点**只读不重算** —— 重算会覆盖掉"写到那一章时"的判定。
    """
    from utils import story_state as ss
    try:
        q = h._query()
        chapter = (q.get("chapter") or [""])[0].strip()
        state = ss.load_state(api.ROOT)
        conflicts = state.get("conflicts") or []
        if chapter:
            try:
                n = int(chapter)
            except ValueError:
                return 400, {"ok": False, "error": "chapter 必须是整数"}
            mine = [c for c in conflicts if c.get("chapter") == n]
            return 200, {"ok": True, "chapter": n, "summary": ss.summarize(state),
                         "conflicts": mine}
        return 200, {
            "ok": True,
            "summary": ss.summarize(state),
            "events": len(state.get("events") or []),
            "updated_at": state.get("updated_at"),
            "conflicts": conflicts,
        }
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_decompose_consent(h):
    """拆解功能的声明状态（GET）。没同意时前端必须挡住入口。"""
    from utils import style_profile as sp
    try:
        return 200, dict({"ok": True}, **sp.consent_state(api.ROOT))
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_decompose_snippets(h):
    """已保存的参考片段（GET）。"""
    from utils import style_profile as sp
    try:
        data = sp.load_snippets(api.ROOT)
        return 200, {"ok": True, "saved": data}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_decompose_candidates(h, body):
    """抽取候选片段（只读、**不落盘**）。

    为什么候选不落盘：一旦写进工作区，它就会跟着快照、归档、打包走 ——
    那是"复制"不是"参考"。用户勾选之后才由 /decompose/snippets/save 写入。
    """
    from utils import style_profile as sp
    from utils.file_io import read_text
    try:
        p = str(body.get("path") or "").strip()
        if not p:
            return 400, {"ok": False, "error": "path 必填（要拆解的文件）"}
        path = Path(p)
        if not path.is_file():
            return 404, {"ok": False, "error": "文件不存在: %s" % p}
        if path.stat().st_size > 5 * 1024 * 1024:
            return 400, {"ok": False, "error": "文件超过 5MB，请先分卷"}
        text = read_text(path)
        try:
            per_kind = max(1, min(int(body.get("per_kind") or 2), 3))
        except (TypeError, ValueError):
            per_kind = 2
        picks = sp.extract_snippets(text, per_kind=per_kind)
        return 200, {
            "ok": True,
            "candidates": picks,
            "count": len(picks),
            "saved_default": [],
            "note": "候选只在内存，未写入磁盘；勾选后才会保存（默认一条都不选）",
        }
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_history(h):
    """章节历史版本：?n=3 → 第 3 章的备份列表（新 → 旧）。

    章节号必填且必须可解析为整数——`_pick_chapter_path()` 需要明确的 n，
    没有「默认章节」这种合理语义。
    """
    try:
        q = h._query()
        raw_n = (q.get("n") or q.get("chapter") or [""])[0]
        if not str(raw_n).strip().isdigit():
            return 400, {"ok": False, "error": "n 必填且为章节号（如 ?n=3）"}
        n = int(raw_n)
        versions = api.refine_ch_mod.list_versions(n)
        target = api.refine_ch_mod._pick_chapter_path(n)
        return 200, {
            "ok": True, "n": n,
            "current": str(target) if target else None,
            "history_dir": str(api.refine_ch_mod.HISTORY_DIR),
            "count": len(versions),
            "versions": versions,
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
# ---------------------------------------------------------------- 残稿（2026-10-20）
# 路径一律用 api.ROOT 绝对拼接：打包态的 CWD 不是工作区，相对路径会读错目录。
def _trunc_store():
    from utils import truncated_store as ts
    ts.TRUNC_DIR = Path(api.ROOT) / "data" / "state" / "truncated"
    ts.RAW_DIR = Path(api.ROOT) / "data" / "chapters" / "raw"
    return ts


def handle_truncated(h):
    """残稿清单（零 token 只读）。

    为什么要有：截断时我们**不落盘**（纪律正确），但人看不见那份 3000 字的开头，
    只看到「失败」。书童把这类叫「失败恢复候选」，这里给同一件事一个可见入口。
    """
    try:
        ts = _trunc_store()
        items = ts.list_items()
        return 200, {"ok": True, "items": items, "count": len(items),
                     "adopted": {str(k): v for k, v in ts.adopted_marks().items()},
                     "min_chars": ts.MIN_ADOPT_CHARS,
                     "dir": "data/state/truncated"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_truncated_read(h):
    """读一份残稿全文：`GET /truncated/item?name=xxx.txt`（只允许 basename）。"""
    try:
        q = h._query()
        name = (q.get("name") or [""])[0]
        ts = _trunc_store()
        return 200, {"ok": True, "name": Path(name).name, "text": ts.read_item(name)}
    except FileNotFoundError as e:
        return 404, {"ok": False, "error": str(e)}
    except Exception as e:                                  # noqa: BLE001
        return 400, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_truncated_adopt(h, body):
    """采纳为第 N 章草稿：`POST /truncated/adopt {name, chapter, replace?}`。

    采纳**不等于完成**：写进 raw 的同时打侧车标记，`is_chapter_complete` 见到标记
    仍判未完成（残稿就是残稿）。补齐后由用户点「已补齐」清标记。
    """
    try:
        ts = _trunc_store()
        ok, msg = ts.adopt((body or {}).get("name"), (body or {}).get("chapter"),
                           replace=bool((body or {}).get("replace")))
        return (200 if ok else 400), ({"ok": True, "message": msg} if ok
                                      else {"ok": False, "error": msg})
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_truncated_clear(h, body):
    """「已补齐」：清掉某章的自残稿标记，恢复正常完成判定。"""
    try:
        ts = _trunc_store()
        ok = ts.clear_mark((body or {}).get("chapter"))
        return (200 if ok else 404), (
            {"ok": True, "message": "已清除第 %s 章的残稿标记" % (body or {}).get("chapter")}
            if ok else {"ok": False, "error": "该章没有残稿标记"})
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 伏笔台账（2026-10-20）
def _foreshadow_mod():
    from utils import foreshadow as fs
    fs.OUTLINE_DIR = Path(api.ROOT) / "data" / "outline" / "chapters"
    fs.STATUS_PATH = Path(api.ROOT) / "data" / "state" / "foreshadow_status.json"
    return fs


def _foreshadow_stale_after():
    """阈值取 gates.foreshadow_stale（默认 30 章）；读不到就用默认。"""
    try:
        cfg = api.load_all()[0]
        v = ((cfg or {}).get("gates") or {}).get("foreshadow_stale")
        return int(v) if v is not None else None
    except Exception:                                       # noqa: BLE001
        return None


def _latest_chapter_no():
    """最新章节号（raw / checked / refined 里最大的）。台账用它算「闲置几章」。"""
    best = 0
    for sub in ("refined", "checked", "raw"):
        d = Path(api.ROOT) / "data" / "chapters" / sub
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            try:
                best = max(best, int(f.stem))
            except (TypeError, ValueError):
                continue
    return best or None


def handle_foreshadow(h):
    """伏笔台账（零 token 只读）：`GET /foreshadow`。

    数据来源只有两个确定性入口：章大纲的「悬念钩子」+ 后续章的文本重合。
    **回收不自动判定** —— 文本相似不等于解决了，自动判会让台账变谎话。
    """
    try:
        fs = _foreshadow_mod()
        data = fs.collect(stale_after=_foreshadow_stale_after(),
                          latest_chapter=_latest_chapter_no())
        return 200, {"ok": True, **data, "markdown": fs.render_markdown(data)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_foreshadow_status(h, body):
    """人工拍板：`POST /foreshadow/status {id, status, reason}`。

    closed / abandoned **必须写理由**：这是唯一能防止台账被随手清空的东西。
    人工标记压过自动判定（与素材裁决同口径）。
    """
    try:
        fs = _foreshadow_mod()
        ok, msg = fs.set_status((body or {}).get("id"), (body or {}).get("status"),
                                (body or {}).get("reason") or "",
                                stale_after=_foreshadow_stale_after(),
                                latest_chapter=_latest_chapter_no())
        return (200 if ok else 400), ({"ok": True, "message": msg} if ok
                                      else {"ok": False, "error": msg})
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 交接稿（2026-10-20）
def handle_handoff_export(h, body):
    """导出交接稿：`POST /handoff/export {chapter?}` → `data/handoff/第N章后.md`。

    v0.10「可带走」：把「写到哪了 + 接下来写什么」压成一份可粘给任何模型的
    markdown。纯确定性拼装（零 token），只写导出物，不改书稿与进度 ——
    与 `/export/markdown` 同族，故 Agent 可调。
    """
    try:
        from utils import handoff as ho
        chapter = (body or {}).get("chapter")
        data = ho.export(chapter=int(chapter) if chapter is not None else None,
                         root=Path(api.ROOT))
        return 200, {"ok": True, "path": data["path"], "chapter": data["chapter"],
                     "next_chapter": data["next_chapter"],
                     "problems": data["problems"], "text": data["text"]}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/jobs/{id}", handle_jobs),
    ("GET", "/review/report", handle_review_report),
    ("GET", "/review/decisions", handle_review_decisions),
    ("GET", "/prompts/list", handle_prompts_list),
    ("GET", "/prompts/get", handle_prompts_get),
    ("POST", "/prompts/restore", handle_prompts_restore),
    ("GET", "/proofread/report", handle_proofread_report),
    ("GET", "/estimate", handle_estimate),
    ("GET", "/book/pacing", handle_book_pacing),
    ("GET", "/homogeneity", handle_homogeneity),
    ("GET", "/story_state", handle_story_state),
    ("GET", "/review/ledger", handle_review_ledger),
    ("GET", "/decompose/consent", handle_decompose_consent),
    ("GET", "/decompose/snippets", handle_decompose_snippets),
    ("GET", "/chapters/history", handle_chapters_history),
    # 残稿可见可采纳（2026-10-20）
    ("GET", "/truncated", handle_truncated),
    ("GET", "/truncated/item", handle_truncated_read),
    ("POST", "/truncated/adopt", handle_truncated_adopt),
    ("POST", "/truncated/clear", handle_truncated_clear),
    # 伏笔台账（2026-10-20）
    ("GET", "/foreshadow", handle_foreshadow),
    ("POST", "/foreshadow/status", handle_foreshadow_status),
    # 交接稿（2026-10-20，v0.10）
    ("POST", "/handoff/export", handle_handoff_export),
)
