# -*- coding: utf-8 -*-
"""新手引导（I-1，2026-11-03）：把“”变成后端可判定的序列。

## 要治的病

作者原话：\"随便找个破游戏的新手教程都更好\"。旧界面只有一张静态五步卡：
五条并列的文本，没有“”，第一次跑阶段 4、
遇到审批门时仍要人猜。

## 口径（确定性、零 token）

- 每一步的 `state` 只有三种：`done` / `current` / `todo`；
- **序列严格**：第一个非 done 的才是 `current`，其后全是 `todo` —— 不让界面
  同时亮起三个“”；
- 判据全部来自盘上事实（project.yaml / materials 目录 / progress.json /
  output/*.docx），不调模型、不猜；
- 审批门独立成一步（`gate`）：存在 `done && !approved` 的阶段时它排到写作
  之前 —— **不放行就不该继续烧 token**；
- 每一步都带 `how`（\"点哪里\"）与 `action`（前端据此跳页 / 高亮目标），
  界面不用自己编文案。

## 为什么判据不写在界面里

界面里判“”只能调一堆端点再拼，且同一件事会与后端各写一份口径
（\"素材 3 张\"与\"素材 0 张\"同时出现是这类事故的典型症状）。这里一份判据，
`/onboarding/status` 直接回给界面和外部 Agent，谁都不用猜。
"""
import nf_api as api


def _stage(progress, n):
    return (progress.data.get("stages", {}) or {}).get(str(n), {}) or {}


def _count_files(dirpath, suffixes=(".md", ".txt", ".json")):
    """目录里的卡数（不递归、跳过 `_` 前缀与 `_backup`）。"""
    try:
        if not dirpath.is_dir():
            return 0
        return sum(1 for p in dirpath.iterdir()
                   if p.is_file() and p.suffix.lower() in suffixes
                   and not p.name.startswith(("_", ".")))
    except OSError:
        return 0


def _model_ready(cfg):
    """模型是否可用 → (bool, 依据文案)。

    - `engine: hermes`：凭据由 Hermes 统一管理 → 算就绪；
    - `engine: direct`：当前 provider 的 `api_key_env` 在进程环境里必须有值。
      只报变量名，**绝不**回显密钥（脱敏红线同 /models/available）。
    """
    engine = str((cfg or {}).get("engine") or "").strip()
    if engine == "hermes":
        return True, "Hermes 子会话（凭据由 Hermes 管理）"
    if engine == "self":
        # 自任务模式不调用任何模型 → 没有 Key 也算就绪（否则新手引导会在
        # “配好 API Key”这一步卡住，而这一步在 self 下根本不需要）。
        return True, "自任务模式（只出任务书，不调用模型）"
    model = ((cfg or {}).get("model") or {}).get("default") or {}
    pid = str(model.get("provider") or "").strip()
    mid = str(model.get("id") or "").strip()
    prov = ((cfg or {}).get("providers") or {}).get(pid) or {}
    env = str(prov.get("api_key_env") or "").strip()
    if not pid or not mid:
        return False, "未选择默认模型"
    if env and not api.os.environ.get(env):
        return False, "环境变量 %s 为空（%s / %s）" % (env, pid, mid)
    return True, "%s / %s" % (pid, mid)


def snapshot():
    """盘上事实（只读）。测试可直接构造 dict 调 `compute_steps`。"""
    cfg, proj = {}, {}
    try:
        cfg, proj = api.load_all()
    except Exception:                                        # noqa: BLE001
        pass
    book = (proj or {}).get("book", {}) or {}

    progress = api.ProgressManager(api.ROOT / "data" / "state" / "progress.json")

    gates = []
    for n in range(1, 10):
        st = _stage(progress, n)
        if st.get("status") == "done" and not st.get("approved") and not st.get("skipped"):
            gates.append({"stage": n, "at": st.get("finished_at") or ""})

    setting_json = api.ROOT / "data" / "setting" / "setting.json"
    global_md = api.ROOT / "data" / "outline" / "global.md"
    raw_dir = api.ROOT / "data" / "chapters" / "raw"

    model_ok, model_why = _model_ready(cfg)
    return {
        "book_name": str(book.get("name") or "").strip(),
        "book_chapters": int(book.get("chapters") or 0) or 0,
        "materials_cards": _count_files(api.ROOT / "materials" / "raw"),
        "materials_scraps": _count_files(api.ROOT / "materials" / "original_scraps"),
        "model_ready": model_ok,
        "model_why": model_why,
        "setting_exists": setting_json.is_file() and setting_json.stat().st_size > 2,
        "outline_exists": global_md.is_file() and global_md.stat().st_size > 2,
        "chapters_raw": _count_files(raw_dir),
        "word_exists": bool(list(api.ROOT.glob("output/*.docx"))) if (api.ROOT / "output").is_dir() else False,
        "stages": {n: _stage(progress, n) for n in range(1, 10)},
        "gates": gates,
        "has_progress": (api.ROOT / "data" / "state" / "progress.json").is_file(),
    }


PLACEHOLDER_BOOK = "示例书名（待填写）"


def compute_steps(snap):
    """纯函数：snapshot → 引导步骤（可单测，不碰盘）。"""
    stages = snap.get("stages") or {}
    gates = snap.get("gates") or []
    book = str(snap.get("book_name") or "").strip()

    def st(n):
        return stages.get(n) or {}

    def done(n, disk=False):
        return st(n).get("status") == "done" or bool(disk)

    raw_ch = int(snap.get("chapters_raw") or 0)
    gate_open = bool(gates)

    steps = [
        {
            "id": "project",
            "title": "建项目",
            "done": bool(book) and book != PLACEHOLDER_BOOK,
            "evidence": ("书名：%s" % book) if book and book != PLACEHOLDER_BOOK
                        else ("书名还是占位符" if book else "还没有项目"),
            "how": "“”页签 → ＋ 新建项目；填书名 / 类型 / 章数",
            "action": {"kind": "new_project"},
        },
        {
            "id": "materials",
            "title": "放素材",
            "done": int(snap.get("materials_cards") or 0) > 0,
            "evidence": "素材卡 %d 张 / 碎片 %d 条" % (
                int(snap.get("materials_cards") or 0),
                int(snap.get("materials_scraps") or 0)),
            "how": "“”页签 → 放素材卡到 materials/raw/；随手写的碎片丢进 original_scraps/",
            "action": {"kind": "tab", "tab": "materials"},
        },
        {
            "id": "model",
            "title": "配模型",
            "done": bool(snap.get("model_ready")),
            "evidence": str(snap.get("model_why") or ""),
            "how": "“”→ 模型与引擎：引擎 / 供应商 / 默认模型",
            "action": {"kind": "tab", "tab": "settings", "settings_tab": "models"},
        },
        {
            "id": "stage1",
            "title": "阶段 1 · 素材 → 设定集",
            "done": done(1, snap.get("setting_exists")),
            "evidence": "设定集已生成" if snap.get("setting_exists") else "还没有设定集",
            "how": "“”→ 一键工作流 → ⏎ 执行下一步",
            "action": {"kind": "tab", "tab": "pipeline", "spot": "flow-run"},
        },
        {
            "id": "stage2",
            "title": "阶段 2 · 整体大纲",
            "done": done(2, snap.get("outline_exists")),
            "evidence": "大纲已生成" if snap.get("outline_exists") else "还没有大纲",
            "how": "同上“”；想先在“”页用体检报告看问题",
            "action": {"kind": "tab", "tab": "outline"},
        },
        {
            "id": "gate",
            "title": "审批放行",
            "done": bool(stages.get(1, {}).get("approved")) and not gate_open,
            "evidence": ("有 %d 处等你审：阶段 %s（阶段 3 不设门）" % (
                len(gates), "、".join(str(g["stage"]) for g in gates))) if gate_open
                        else (("已放行阶段 %s" % "、".join(
                            str(n) for n in (1, 2, 3, 4, 5, 6, 7, 8)
                            if (stages.get(n) or {}).get("approved")))
                              or "阶段 2 / 6 完成时会在这里停一次（阶段 3 不设门）"),
            "how": "“”→ 逐个点“”；不满意就“”并写原因",
            "action": {"kind": "tab", "tab": "inbox", "spot": "gate-list"},
        },
        {
            "id": "stage4",
            "title": "阶段 4 · 逐章写作",
            "done": done(4, raw_ch > 0),
            "evidence": ("已落盘 %d 章" % raw_ch) if raw_ch else "还没有正文",
            "how": "“”→ 执行下一步（会一章一章写）；进度看章节页签",
            "action": {"kind": "tab", "tab": "pipeline", "spot": "flow-run"},
        },
        {
            "id": "export",
            "title": "导出成品",
            "done": bool(snap.get("word_exists")),
            "evidence": "output/ 已有 Word" if snap.get("word_exists") else "还没有 Word 成品",
            "how": "“”→ 📄 Word 成品；或“”页签选范围",
            "action": {"kind": "tab", "tab": "export"},
        },
    ]

    # 序列严格：第一个未完成 = current，其余未完成 = todo。
    # 例外：有审批门时 gate 抢到 current（它在序列里本来就在 stage4 之前）。
    current_set = False
    for s in steps:
        if s["done"]:
            s["state"] = "done"
            continue
        if gate_open and s["id"] == "gate":
            s["state"] = "current"
            current_set = True
            continue
        if not current_set:
            s["state"] = "current"
            current_set = True
        else:
            s["state"] = "todo"

    if gate_open:
        # gate 之前的一律按事实标 done（跑到哪就是哪），gate 才是唯一的“”
        for s in steps:
            if s["id"] == "gate":
                break
            s["state"] = "done" if s["done"] else s["state"]
            if s["state"] == "current":
                s["state"] = "todo"

    done_n = sum(1 for s in steps if s["state"] == "done")
    nxt = next((s for s in steps if s["state"] == "current"), None)
    return {
        "steps": steps,
        "next": nxt,
        "progress": {"done": done_n, "total": len(steps)},
        "gates": gates,
    }


def status():
    snap = snapshot()
    out = compute_steps(snap)
    out.update({
        "ok": True,
        "book": snap.get("book_name") or "",
        "has_progress": bool(snap.get("has_progress")),
        "target_chapters": snap.get("book_chapters") or 0,
    })
    return 200, out


def handle_onboarding_status(h):
    """GET /onboarding/status → 序列引导（零 token、确定性）。"""
    try:
        return status()
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/onboarding/status", handle_onboarding_status),
)
