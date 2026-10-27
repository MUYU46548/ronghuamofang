# -*- coding: utf-8 -*-
"""阶段产物判定（唯一真源，2026-10-11 欠账甲2）。

## 为什么独立成模块

`STAGE_ARTIFACTS` 此前住在 `nf_api.py`，但**完成判定的咽喉在 orchestrator** ——
而 nf_api 顶层 `from orchestrator import run`（nf_api → orchestrator 单向），
orchestrator 反向 import nf_api 会成环。故把产物表与判定抽到 utils 单源，
nf_api 与 orchestrator 各自引用同一份。

## 判据（确定性，零 LLM）

- 路径不存在 → 记「缺失」
- 目录存在但空 → 记「空目录」（原行为保留）
- **文件存在但为空/全空白 → 记「空文件」（本次新增）** —— 0 行产物此前会
  被 exists() 放行并一路走完审批门（2026-10-11 实证事故：0 行 polish_report），
  完成判定必须看得见「有文件但没内容」。
"""
from pathlib import Path

# 各阶段「应该有」的产物（目录只判是否为空；文件判存在且非空）
STAGE_ARTIFACTS = {
    1: ["data/setting/setting.json"],
    2: ["data/outline/global.md"],
    3: ["data/outline/chapters"],
    4: ["data/chapters/raw"],
    5: ["data/chapters/checked"],
    6: ["data/chapters/refined"],
    7: ["output"],
    # 阶段 8（Markdown 分卷导出）此前**不在表里** → `missing_artifacts(8)` 恒返空，
    # 等于"永远算有产物"。它的真实产物是 output/{书名}_markdown/（路径含书名，
    # 无法写成静态相对路径），故与阶段 7 同判 output/ 非空 —— 粗粒度但不会再真空过。
    # 精确到分卷目录需要动态路径，留给 summarize_artifacts 的目录展开去做。
    8: ["output"],
}


def missing_artifacts(stage, root):
    """返回该阶段缺失/为空的产物路径列表（确定性，不读语义）。

    root = 判定基准目录。**必须与产物写入端同域**：阶段产物由 stage* 以
    相对路径写在 orchestrator 进程 CWD 下（GUI spawn 时 cwd=项目根），
    所以 orchestrator 判 "."、nf_api 判它的 ROOT —— 各自与自己的写入/读取域对齐。
    """
    missing = []
    for rel in STAGE_ARTIFACTS.get(stage, []):
        p = Path(root) / rel
        if not p.exists():
            missing.append(rel)
        elif p.is_dir():
            if not any(p.iterdir()):
                missing.append(rel + "（空目录）")
        else:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            if not content.strip():
                missing.append(rel + "（空文件）")
    return missing


# ---------------------------------------------------------------- 上游输入（自由模式）
# 阶段可读名。原先 `chapter_handoff.STAGE_NAMES` 里也有一份 —— 两处并存必漂移，
# 故这里当唯一真源、那边改为 re-export。
STAGE_NAMES = {
    1: "素材归并", 2: "大纲", 3: "分章大纲",
    4: "写作", 5: "检查", 6: "润色", 7: "Word", 8: "Markdown导出",
}

# 合法阶段号的**唯一真源**（2026-10-27）。
# 为什么要有：阶段 8 早已在 orchestrator（STAGES/range(from,9)）、progress_manager
# （STAGE_KEYS）、/state（build_state）里生效，但 GUI 侧的几个写端点仍写死
# `1 <= stage <= 7` —— 于是阶段 8 显示「待审批」，点确认得到 400「stage 须为 1-7」。
# 用户原话：「阶段8无法放行（提示stage须为1-7，代码打架）」。判据只留这一份，
# 新增阶段时改 STAGE_NAMES 一处即可，不再各写一份数字字面量。
VALID_STAGES = tuple(sorted(STAGE_NAMES))

# 各阶段「开工前要吃」的**上游**产物 —— 与 STAGE_ARTIFACTS 的方向相反：
# 后者是"这阶段该留下什么"（完成判定用），这里是"这阶段开工前必须已有什么"
# （自由模式执行前拦人用）。条目 = (产出该产物的上游阶段, 相对路径)。
#
# · all：全都要非空，缺一个就拦
# · any：至少一层可用。阶段 7/8 走 refined > checked > raw **逐章**回退链
#   （stage7.merge_book / stage8.get_available_chapters），只要有任一层就能出稿
#   —— 硬要三层齐全会把"导入毛坯直接出稿"这条正路拦死。
# 阶段 1 不列：素材来自 materials/raw，由用户放置，不是流水线产物。
STAGE_INPUTS = {
    2: {"all": [(1, "data/setting/setting.json")]},
    3: {"all": [(2, "data/outline/global.md")]},
    4: {"all": [(3, "data/outline/chapters")]},
    5: {"all": [(4, "data/chapters/raw")]},
    6: {"all": [(5, "data/chapters/checked")]},
    7: {"any": [(4, "data/chapters/raw"), (5, "data/chapters/checked"),
                (6, "data/chapters/refined")]},
    8: {"any": [(4, "data/chapters/raw"), (5, "data/chapters/checked"),
                (6, "data/chapters/refined")]},
}


def _usable(path):
    """非空判据（与 missing_artifacts 同口径：目录看有没有条目，文件看有没有字）。"""
    p = Path(path)
    if not p.exists():
        return False
    if p.is_dir():
        return any(p.iterdir())
    try:
        return bool(p.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def missing_inputs(stage, root, will_produce=None):
    """该阶段开工前缺的**上游**产物。返回 dict 列表，空 = 够料可跑。

    每条：{"path", "produced_by", "kind": "all"|"any", "group": [同组全部路径]}
    kind="any" 表示该组是"任一层即可"，组内全部不可用才报（且整组一起报，
    让人一次看清 raw/checked/refined 三层都没有这个事实）。

    `will_produce` = **本次同一批里会执行的阶段号集合**。自由模式勾「检查+润色+
    出稿」时，checked 是这次跑阶段5 才产出的，开工前磁盘上当然还没有 —— 不带上
    这个集合就会把正常组合误拦（漏掉它 = 勾了上下游就跑不动，正是本功能要解的
    痛点）。判定：**磁盘上已有** 或 **本次会产出**，两者满足其一即算够料。
    """
    spec = STAGE_INPUTS.get(stage)
    if not spec:
        return []
    will = set(will_produce or ())
    problems = []

    def ok(producer, rel):
        return producer in will or _usable(Path(root) / rel)

    for producer, rel in (spec.get("all") or []):
        if not ok(producer, rel):
            problems.append({"path": rel, "produced_by": producer,
                             "kind": "all", "group": [rel]})
    any_req = spec.get("any") or []
    if any_req and not any(ok(p, rel) for p, rel in any_req):
        group = [rel for _, rel in any_req]
        for producer, rel in any_req:
            problems.append({"path": rel, "produced_by": producer,
                             "kind": "any", "group": group})
    return problems


# ------------------------------------------------ 产物概况（GUI 收件箱「产物概况」行）
# 为什么在后端算：收件箱里 stage 3/4/7 的产物是**目录**（data/outline/chapters/、
# data/chapters/raw/、output/），而 Electron 的 readPreview 只读单个文件 —— 目录
# 里的章节数/字数前端根本数不出来。实测后果：trial book 停在 stage 4 审批门时，
# 收件箱那一行「产物概况」永远空白（2026-10-24 用户复现）。行数/字数在后端按
# 确定性规则数，前端只负责渲染。


def _docx_chars(path):
    """docx 正文字符数（段落 + 表格单元格）。

    ## 为什么必须真读（2026-10-26，用户第 8 条）

    旧实现遇到 `.docx` 直接 `continue`（注释写「二进制：只计数不读字符」），
    而阶段 7 的产物 `output/` 里**只有 docx** —— 于是 files=1、chars=0，
    界面显示「output (1 篇 · 0 字)」。用户原话：「阶段7 output (1 篇 · 0 字)，
    疑似GUI 统计字数的方式有问题」。产物明明 56KB、10 章齐全、体检全过，
    界面上却像空的 —— 这是把"我们没数"谎报成"里面没有"。

    缺 python-docx 或文件损坏时**返回 0 并让调用方标注**（不静默当成 0 字）。
    """
    try:
        import docx
    except Exception:                                        # noqa: BLE001
        return 0
    try:
        d = docx.Document(str(path))
        n = sum(len(p.text) for p in d.paragraphs)
        for t in d.tables:
            for row in t.rows:
                for cell in row.cells:
                    n += len(cell.text)
        return n
    except Exception:                                        # noqa: BLE001
        return 0


def summarize_artifacts(stage, root):
    """返回该阶段产物的概况（确定性，零 LLM）。每项：

    `{path, exists, kind, lines, files, chars}`

    - 文件：`kind="file"`，`lines`=行数；缺失/空文件 `lines=0`。
    - 目录：`kind="dir"`，`files`=条目数、`chars`=文本文件总字符数（docx 只计数）。
    - 缺失：`exists=False`，其余计数为 0。
    """
    out = []
    for rel in STAGE_ARTIFACTS.get(stage, []):
        p = Path(root) / rel
        item = {"path": rel, "exists": p.exists(), "kind": "file",
                "lines": 0, "files": 0, "chars": 0}
        if not p.exists():
            out.append(item)
            continue
        if p.is_dir():
            item["kind"] = "dir"
            for f in p.iterdir():
                if not f.is_file():
                    continue
                item["files"] += 1
                if f.suffix.lower() == ".docx":
                    # docx 也是**有正文的产物**：不数它，阶段 7 就显示「0 字」
                    item["chars"] += _docx_chars(f)
                    continue
                try:
                    txt = f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                item["chars"] += len(txt)
        else:
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                txt = ""
            item["lines"] = txt.count("\n") + 1 if txt else 0
            item["chars"] = len(txt)
        out.append(item)
    return out
