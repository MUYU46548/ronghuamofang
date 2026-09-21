# -*- coding: utf-8 -*-
"""大纲域（GET 读侧）：结构化视图、版本历史、节点级 diff、多方案草稿、分章大纲。

## 边界

只承载**读**端点。`/outline/save`、`/outline/chapters/save`、`/outline/refine`
等写端点留在 `do_POST`（涉及「先备份到 `data/outline/history/` 再覆盖」的红线）。

## 版本号约定

`v1` / `v2` 是**整数版本号**（0 = 最初版），由 `outline_panel.resolve_version()`
映射到 `data/outline/history/` 下的实际文件。缺少 `v1` 时返回 400 —— 因为
「跟哪个版本比」是无法默认的语义决策，猜一个基准会让用户看到误导性的 diff。

## /outline/drafts 附带四节原文

列表本身来自 `list_drafts()`，但前端拼合预览需要四节正文，所以这里额外
调 `parse_global()` 把 `acts` 挂上去。**逐份 try/except**：某份草稿格式坏了
不能让整个列表 500——用户需要看到列表才能删掉那份坏草稿。
"""
import nf_api as api


def handle_outline_structure(h):
    """结构化大纲视图：解析 global.md + 贴 outline_review 评分。

    `?review=0` 跳过体检（体检要跑确定性检查，切换视图时不必要）。
    """
    try:
        from utils import outline_panel as op
        q = h._query()
        only = (q.get("review") or ["1"])[0] not in ("0", "false", "no")
        return 200, op.build_structure(run_review=only)
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_outline_history(h):
    """版本列表（data/outline/history/ 下的历史大纲）。"""
    try:
        from utils import outline_panel as op
        return 200, {"versions": op.list_versions()}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_outline_diff(h):
    """结构化**节点级** diff（不是文本行 diff）。

    节点级而非行级，是因为大纲的语义单位是「情节点」——一行文本挪了位置
    在行 diff 里是「删除+新增」，在节点 diff 里才能正确显示为「移动」。
    """
    try:
        from utils import outline_panel as op
        q = h._query()
        v1 = (q.get("v1") or [""])[0]
        v2 = (q.get("v2") or [""])[0]
        if not v1:
            return 400, {"error": "缺少参数 v1（对比基准版本）"}
        f1 = op.resolve_version(v1)
        f2 = op.resolve_version(v2) or op.resolve_version("0")
        if not f1 or not f2:
            return 404, {"error": "版本不存在: v1=" + str(v1) + " v2=" + str(v2)}
        data = op.diff_outlines(f1, f2)
        data["v1"] = int(v1) if str(v1).isdigit() else 0
        data["v2"] = int(v2) if str(v2).isdigit() else 0
        return 200, data
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_outline_trend(h):
    """迭代趋势 + 与上一版对比 + 收敛判断（确定性，零 token）。

    回答「还要不要再迭代一轮」—— 这是 `/outline/diff`（节点级文本差异）
    之外的**质量指标视角**：diff 告诉你哪几行变了，趋势告诉你**每轮有没有
    实质进展**（问题数 / 碎片数 / 平均分的变化），并给出结论：

      done         最新版已达标（问题=0 且碎片=0）→ 可以开工
      improving    最近几轮持续改善 → 可继续
      stalled      最近几轮完全没变 → 收益递减，建议换策略或开工
      mixed        有升有降 → 建议回看是哪几条在反复
      insufficient 版本不足 2 个 → 无法判断趋势

    参数 `window`（默认 3）：看最近几轮。
    """
    try:
        import outline_review as ov
        q = h._query()
        raw_window = (q.get("window") or ["3"])[0]
        try:
            window = max(2, min(int(raw_window), 20))
        except (TypeError, ValueError):
            window = 3

        setting = "data/setting/setting.json"
        series = ov.review_series("data/outline/global.md", setting)
        status, note = ov.convergence_verdict(series, window=window)
        cmp = ov.compare_with_previous("data/outline/global.md", setting)
        return 200, {
            "series": series,
            "window": window,
            "status": status,
            "note": note,
            "compare": cmp,          # None = 无历史备份（不假装已对比）
            "has_backup": ov.latest_backup() is not None,
        }
    except FileNotFoundError as e:
        return 400, {"error": "大纲不存在（先跑 stage2）: " + str(e)[:120]}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_outline_drafts(h):
    """多方案 draft 列表 + 每份的四节原文（供前端拼合预览）。"""
    try:
        from utils import outline_panel as op
        drafts = op.list_drafts()
        df = op.draft_files()
        for d in drafts:
            try:
                from utils.file_io import read_text as _rt
                import utils.outline_struct as _osr
                d["acts"] = _osr.parse_global(_rt(op.draft_path(d["id"])))["acts"]
            except Exception:                               # noqa: BLE001
                pass        # 单份草稿坏掉不影响列表（用户要靠列表才能删它）
        return 200, {"drafts": drafts, "files": df}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_list(h):
    """分章大纲列表（data/outline/chapters/*.md）。

    标题取正文首行（去掉 `#`）。章节号从**文件名** `NN.md` 取，不从内容取 ——
    内容里的编号可能被模型写乱，文件名是系统的真相来源。
    """
    try:
        chapters_dir = api.ROOT / "data" / "outline" / "chapters"
        if not chapters_dir.exists():
            return 200, {"chapters": []}
        chapters = []
        for f in sorted(chapters_dir.glob("*.md")):
            content = f.read_text(encoding="utf-8")
            title = content.split("\n")[0].lstrip("#").strip() if content else f.stem
            chapters.append({"file": f.name, "n": int(f.stem), "title": title,
                             "size": f.stat().st_size})
        return 200, {"chapters": chapters}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_get(h):
    """读取单章大纲（?n=3 → 03.md）。

    章节号补零成两位：文件名规范是 `%02d.md`，三位的书会退化为 `100.md`
    但读取方一律用 `%02d` 格式化，所以两者不会错位。
    """
    try:
        q = h._query()
        n = (q.get("n") or [""])[0]
        if not n.isdigit():
            return 400, {"error": "n 必须为数字"}
        chapter_path = api.ROOT / "data" / "outline" / "chapters" / ("%02d.md" % int(n))
        if not chapter_path.exists():
            return 404, {"error": "第 %s 章大纲不存在" % n}
        content = chapter_path.read_text(encoding="utf-8")
        return 200, {"ok": True, "n": int(n), "content": content}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/outline/structure", handle_outline_structure),
    ("GET", "/outline/history", handle_outline_history),
    ("GET", "/outline/diff", handle_outline_diff),
    ("GET", "/outline/drafts", handle_outline_drafts),
    ("GET", "/outline/chapters/list", handle_chapters_list),
    ("GET", "/outline/chapters/get", handle_chapters_get),
)
