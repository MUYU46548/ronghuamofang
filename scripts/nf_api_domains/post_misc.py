# -*- coding: utf-8 -*-
"""POST 杂项域：不属于其它域的写端点。

放这里的判据是「**没有同类**」——一旦某类端点超过 3 个，就应当另开一个域模块
（像 models / outline / materials 那样）。这个模块存在的意义是给零散端点一个
明确归宿，而不是让它们堆在 `nf_api.py` 的 elif 链里。

当前成员：
- `/export/markdown`  分卷导出 Markdown（P3 多平台发布；2026-10-17 起支持
  `include_frontmatter`，并修复 GUI 传 book_name=null 造出「None_markdown」目录）
- `/export/word`      导出 Word 成品（2026-10-17 复盘 P0：导出面板「双格式」此前
  是假选项——只出 Markdown。现复用 stage7 的合并+模板转换，同步执行、不写进度）

## 为什么 `per_vol` 要在入口处转 int

`body.get("per_vol")` 可能来自 JSON（int）也可能来自表单（str）。
在**这里**转而不是在 `stage8_markdown_export` 里转，是因为
「HTTP 层负责把外部输入规整成域函数要的类型」是更清晰的边界；
导出模块不该关心自己是被 HTTP 调的还是被 CLI 调的。
"""
import nf_api as api
from datetime import datetime
from utils.file_io import read_text


def _export_history_items():
    """扫 output/ 得出「导出过什么」（零 token、只读盘上事实）。

    ## 为什么不能靠"谁调用了导出端点"来记（2026-10-26，用户第 10 条）

    用户原话：「阶段8已经跑完了，暂无导出记录，过于幽默。由此可见整个 GUI 和
    程序本体是极度割裂的」。真实原因是导出记录只存在于前端内存 ref，且只在
    **走导出面板**时 unshift —— orchestrator 跑阶段 8、Agent 调 CLI、重开页面，
    三种情况都会让它变空。界面于是对着一个装满产物的 output/ 说"暂无记录"。

    判据改成：**output/ 里有什么就说什么** —— 不管是谁、从哪条通道产出的。
    """
    out_dir = api.ROOT / "output"
    items = []
    if not out_dir.is_dir():
        return items
    for p in sorted(out_dir.iterdir()):
        try:
            st = p.stat()
        except OSError:
            continue
        item = {
            "name": p.name,
            "path": str(p),
            "mtime": st.st_mtime,
            "mtime_text": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
        # 相对 ROOT 的路径：Electron 的 open-artifact 走白名单校验 + resolveExisting，
        # 传相对路径最稳（绝对路径在 --root 场景下可能落在白名单外被拒）。
        try:
            item["rel"] = str(p.relative_to(api.ROOT)).replace("\\", "/")
        except ValueError:
            item["rel"] = p.name
        if p.is_dir() and p.name.lower().endswith("_markdown"):
            try:
                vols = [q for q in p.iterdir() if q.is_file()
                        and q.name.lower().endswith(".md")]
            except OSError:
                vols = []
            item.update({"kind": "markdown", "files": len(vols),
                         "chars": 0})
            items.append(item)
        elif p.suffix.lower() == ".docx":
            item.update({"kind": "word", "files": 1,
                         "chars": 0, "size": st.st_size})
            items.append(item)
    items.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
    return items


def handle_exports_history(h):
    """`GET /exports/history` —— 最近导出（**盘上事实**，不依赖谁调用过导出）。

    对应用户第 10 条：「阶段8已经跑完了，暂无导出记录」。阶段 8 由 orchestrator
    跑，不经过导出面板，所以前端那份内存记录永远是空的。这里直接扫 output/。
    """
    try:
        items = _export_history_items()
        return 200, {
            "ok": True,
            "items": items,
            "count": len(items),
            "note": ("读的是 output/ 目录的实际内容（含 orchestrator 跑阶段 7/8 的产物），"
                     "与「本次会话里点过导出按钮」无关。"),
        }
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_export_markdown(h, body):
    """Markdown 分卷导出。

    `per_vol` 缺省 5 章一卷。`book_name` 缺省时由 stage8 回退 project.yaml 的
    book.name（**不再**让 null 直通成「None_markdown」目录）。返回 `(200, {...})`
    或 `(400, {...})`——「导出失败」是**业务**结果（比如还没写章节），不是服务器
    错误，所以走 400 而不是 500。
    """
    try:
        import stage8_markdown_export as s8
        per_vol = int(body.get("per_vol") or 5)
        book_name = body.get("book_name") or None
        ok, msg, path = s8.export_markdown(book_name=book_name,
                                           chapters_per_vol=per_vol,
                                           include_frontmatter=bool(body.get("include_frontmatter")))
        return (200 if ok else 400), {"ok": ok, "message": msg, "path": path}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_export_word(h, body):
    """Word 成品导出（导出面板「仅 Word / 双格式」分支）。

    复用 stage7 的 `merge_book` + `md_to_docx`，**不写 progress、不打快照** ——
    导出是用户的即时动作，不是流水线阶段；progress 语义仍归 orchestrator 跑
    stage7 时负责。无章节可合并按业务失败返回 400（不是 500）。
    """
    try:
        import stage7_convert as s7
        from utils.config_io import load_config_yaml
        from utils.project_config import get_project_config
        from utils.export_check import (check_merged_md, check_template,
                                        check_docx_output)

        proj = get_project_config() or {}
        book = proj.get("book", {})
        book_name = (body.get("book_name") or book.get("name") or "").strip() or "未命名"
        author = book.get("author", "") or ""
        page_break = bool(((load_config_yaml(str(api.ROOT / "config" / "system.yaml"))
                            or {}).get("chapter") or {}).get("page_break", True))
        tmpl = (book.get("word_template") or "").strip()
        # 与 stage7.run_stage 同口径：配置了模板就按存在性校验（缺失 = 错误，
        # 不再静默降级成硬编码样式 —— 用户以为用了模板其实没有）
        template_path = (api.ROOT / tmpl) if tmpl else None

        out_dir = api.ROOT / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        book_md = api.ROOT / "data" / "merged" / "book.md"
        n = s7.merge_book(api.ROOT / "data" / "chapters" / "refined",
                          api.ROOT / "data" / "chapters" / "checked",
                          api.ROOT / "data" / "chapters" / "raw",
                          book_md, book_name)

        # ---- 导出前校验（与流水线 stage7 同一套判据）：不过关不出文件 ----
        # 导出面板这条路以前直连 merge+docx，绕过了 stage7.run_stage 里挂的
        # 三层校验 —— 界面导出的成品不经任何拦截（2026-10-23 抓出）。
        # 修法：同一份 utils/export_check 判据在这里再挂一遍，不另写判据。
        expect = int(book.get("chapters", 0) or 0) or None
        md_text = read_text(book_md)
        md_errs, md_warns = check_merged_md(md_text, expect_chapters=expect)
        tmpl_errs, tmpl_warns = ([], [])
        if template_path is not None:
            tmpl_errs, tmpl_warns = check_template(template_path)
        for w in md_warns + tmpl_warns:
            print(f"[export/word] 导出警告：{w}")
        all_errs = md_errs + tmpl_errs
        if all_errs:
            _named = "；".join(all_errs[:3]) + ("…" if len(all_errs) > 3 else "")
            return 400, {"ok": False,
                         "error": f"导出前校验未通过（{len(all_errs)} 项）—— 未出文件：{_named}",
                         "details": all_errs}

        # dotx→docx 转换缓存目录钉到项目根（stage7 里是 CWD 相对路径，API 进程 CWD 不一定是项目根）
        s7.TEMPLATE_CACHE = api.ROOT / "data" / "tmp" / "template_cache"
        docx_path = out_dir / f"{book_name}_完整版.docx"
        s7.md_to_docx(md_text, docx_path, book_title=book_name,
                      author=author, template_path=str(template_path) if template_path else None,
                      page_break=page_break)
        if not docx_path.exists() or docx_path.stat().st_size == 0:
            return 500, {"ok": False, "error": "docx 未生成或为空"}

        # ---- 导出后校验（成品层）：不过就删文件 ----
        out_errs, out_warns = check_docx_output(docx_path, md_text=md_text)
        for w in out_warns:
            print(f"[export/word] 成品警告：{w}")
        if out_errs:
            try:
                docx_path.unlink()
            except Exception:                                # noqa: BLE001
                pass
            _named = "；".join(out_errs[:3]) + ("…" if len(out_errs) > 3 else "")
            return 400, {"ok": False,
                         "error": f"成品校验未通过（{len(out_errs)} 项）—— 残次品已删除：{_named}",
                         "details": out_errs}
        return 200, {"ok": True, "message": f"已导出 {n} 章 → {docx_path}",
                     "path": str(docx_path), "chapters": n}
    except FileNotFoundError as e:                          # 章节目录不存在/为空 = 业务结果
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/exports/history", handle_exports_history),
    ("POST", "/export/markdown", handle_export_markdown),
    ("POST", "/export/word", handle_export_word),
)
