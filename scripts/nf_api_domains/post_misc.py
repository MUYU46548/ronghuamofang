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
from utils.file_io import read_text


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

        proj = get_project_config() or {}
        book = proj.get("book", {})
        book_name = (body.get("book_name") or book.get("name") or "").strip() or "未命名"
        author = book.get("author", "") or ""
        page_break = bool(((load_config_yaml(str(api.ROOT / "config" / "system.yaml"))
                            or {}).get("chapter") or {}).get("page_break", True))
        tmpl = (book.get("word_template") or "").strip()
        template_path = api.ROOT / tmpl if tmpl and (api.ROOT / tmpl).exists() else None

        out_dir = api.ROOT / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        book_md = api.ROOT / "data" / "merged" / "book.md"
        n = s7.merge_book(api.ROOT / "data" / "chapters" / "refined",
                          api.ROOT / "data" / "chapters" / "checked",
                          api.ROOT / "data" / "chapters" / "raw",
                          book_md, book_name)
        # dotx→docx 转换缓存目录钉到项目根（stage7 里是 CWD 相对路径，API 进程 CWD 不一定是项目根）
        s7.TEMPLATE_CACHE = api.ROOT / "data" / "tmp" / "template_cache"
        docx_path = out_dir / f"{book_name}_完整版.docx"
        s7.md_to_docx(read_text(book_md), docx_path, book_title=book_name,
                      author=author, template_path=str(template_path) if template_path else None,
                      page_break=page_break)
        if not docx_path.exists() or docx_path.stat().st_size == 0:
            return 500, {"ok": False, "error": "docx 未生成或为空"}
        return 200, {"ok": True, "message": f"已导出 {n} 章 → {docx_path}",
                     "path": str(docx_path), "chapters": n}
    except FileNotFoundError as e:                          # 章节目录不存在/为空 = 业务结果
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("POST", "/export/markdown", handle_export_markdown),
    ("POST", "/export/word", handle_export_word),
)
