# -*- coding: utf-8 -*-
"""POST 杂项域：不属于其它域的写端点。

放这里的判据是「**没有同类**」——一旦某类端点超过 3 个，就应当另开一个域模块
（像 models / outline / materials 那样）。这个模块存在的意义是给零散端点一个
明确归宿，而不是让它们堆在 `nf_api.py` 的 elif 链里。

当前成员：
- `/export/markdown`  分卷导出 Markdown（P3 多平台发布）

## 为什么 `per_vol` 要在入口处转 int

`body.get("per_vol")` 可能来自 JSON（int）也可能来自表单（str）。
在**这里**转而不是在 `stage8_markdown_export` 里转，是因为
「HTTP 层负责把外部输入规整成域函数要的类型」是更清晰的边界；
导出模块不该关心自己是被 HTTP 调的还是被 CLI 调的。
"""
import nf_api as api


def handle_export_markdown(h, body):
    """Markdown 分卷导出。

    `per_vol` 缺省 5 章一卷。返回 `(200, {...})` 或 `(400, {...})`——
    「导出失败」是**业务**结果（比如还没写章节），不是服务器错误，
    所以走 400 而不是 500。
    """
    try:
        import stage8_markdown_export as s8
        per_vol = int(body.get("per_vol") or 5)
        book_name = body.get("book_name") or None
        ok, msg, path = s8.export_markdown(book_name=book_name,
                                           chapters_per_vol=per_vol)
        return (200 if ok else 400), {"ok": ok, "message": msg, "path": path}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("POST", "/export/markdown", handle_export_markdown),
)
