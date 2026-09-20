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
ROUTES = (
    ("GET", "/jobs/{id}", handle_jobs),
    ("GET", "/review/report", handle_review_report),
    ("GET", "/review/decisions", handle_review_decisions),
    ("GET", "/prompts/list", handle_prompts_list),
    ("GET", "/prompts/get", handle_prompts_get),
    ("GET", "/proofread/report", handle_proofread_report),
    ("GET", "/estimate", handle_estimate),
    ("GET", "/book/pacing", handle_book_pacing),
    ("GET", "/chapters/history", handle_chapters_history),
)
