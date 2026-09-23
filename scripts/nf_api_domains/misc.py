# -*- coding: utf-8 -*-
"""杂项域：运行环境 / 日志 / 关于 / 章节完整性 / 成本实时视图。

这是 P2 拆分的**第一个域模块**，用来把「薄转发 + 纯函数 handler」的范式立起来。
挑这组端点的理由：体量最大（`/logs/tail` 31 行、`/about` 31 行、
`/costs/summary` 41 行）、互不依赖、且无写入副作用 —— 搬错了会立刻被
HTTP 契约测试抓到，却不会损坏用户数据。

## 迁移清单（原 nf_api.do_GET 分支 → 本模块函数）

| 分支 | 函数 | 说明 |
|------|------|------|
| `/costs/summary` | `handle_costs_summary` | 按阶段/模型聚合 |
| `/env/open` | `handle_env_open` | 返回 .env 路径（缺失则建模板） |
| `/batch_refine/progress` | `handle_batch_refine_progress` | 批量精修进度 |
| `/chapters/quality` | `handle_chapters_quality` | 逐章 quality 评分 |
| `/chapters/verify` | `handle_chapters_verify` | 断点续跑：逐章完成度 |
| `/logs/tail` | `handle_logs_tail` | 运行日志尾部 |
| `/about` | `handle_about` | 版本 / 环境 / 路径 |
| `/costs/streaming` | `handle_costs_streaming` | 当前 run 实时消耗 |

## 与原实现的一致性约束

搬运时**逐行对齐**原分支，唯一改动是：`self._send(code, payload)` → `return code, payload`。
所有副作用（建 .env、建目录）保留原样，因为这些端点原本就是「读操作带幂等初始化」。
"""
from . import contract  # noqa: F401  （仅为声明协议来自本包）


def handle_costs_summary(h):
    """按阶段/模型聚合成本（原 do_GET `elif p == "/costs/summary"`）。"""
    import nf_api as api

    db_path = api.ROOT / "logs" / "runs.db"
    if not db_path.exists():
        return 200, {"by_stage": [], "by_model": [], "totals": {}}
    db = api.RunDB(db_path)
    try:
        STAGE_NAMES = {1: "素材归并", 2: "整体大纲", 3: "逐章大纲", 4: "逐章写作",
                       5: "逻辑检查", 6: "润色", 7: "Word"}
        by_stage = []
        for r in db.conn.execute(
                "SELECT stage, COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),"
                " COALESCE(SUM(cache_read),0), COALESCE(SUM(cost_yuan),0), COUNT(*),"
                " COALESCE(SUM(estimated),0)"
                " FROM cost_log GROUP BY stage ORDER BY stage"):
            by_stage.append({
                "stage": r[0], "name": STAGE_NAMES.get(r[0], str(r[0])),
                "tokens_in": r[1], "tokens_out": r[2], "cache_read": r[3],
                "cost_yuan": round(r[4], 4), "calls": r[5], "estimated": r[6],
            })
        by_model = []
        for r in db.conn.execute(
                "SELECT model, COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),"
                " COALESCE(SUM(cache_read),0), COALESCE(SUM(cost_yuan),0), COUNT(*),"
                " COALESCE(SUM(estimated),0)"
                " FROM cost_log GROUP BY model ORDER BY COALESCE(SUM(cost_yuan),0) DESC"):
            by_model.append({
                "model": r[0], "tokens_in": r[1], "tokens_out": r[2], "cache_read": r[3],
                "cost_yuan": round(r[4], 4), "calls": r[5], "estimated": r[6],
            })
        tot = db.conn.execute(
            "SELECT COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0),"
            " COALESCE(SUM(cache_read),0), COALESCE(SUM(cost_yuan),0), COUNT(*),"
            " COALESCE(SUM(estimated),0)"
            " FROM cost_log").fetchone()
        totals = {
            "tokens_in": tot[0], "tokens_out": tot[1], "cache_read": tot[2],
            "cost_yuan": round(tot[3], 4), "calls": tot[4], "estimated": tot[5],
        }
        return 200, {"by_stage": by_stage, "by_model": by_model, "totals": totals}
    finally:
        db.close()


def handle_env_open(h):
    """返回 .env 真实路径（原 do_GET `elif p == "/env/open"`）。

    文件不存在时创建模板 —— 原行为如此：GUI 点「打开 .env」不该只看到
    「不存在」而不知所措。
    """
    import nf_api as api

    env_path = api.ROOT / ".env"
    if not env_path.exists():
        env_path.write_text("TOKENHUB_API_KEY=\n", encoding="utf-8")
    return 200, {"env_path": str(env_path), "exists": True}


def handle_batch_refine_progress(h):
    """批量精修进度（原 do_GET `elif p == "/batch_refine/progress"`）。"""
    import nf_api as api

    progress_path = api.ROOT / "data" / "state" / "batch_refine_progress.json"
    if not progress_path.exists():
        return 200, {"status": "idle", "message": "无正在进行的批量精修任务"}
    try:
        return 200, api.json.loads(api.nf_read_text(progress_path))
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_quality(h):
    """逐章 quality 评分（原 do_GET `elif p == "/chapters/quality"`）。"""
    import nf_api as api

    try:
        import auto_rewrite as ar_mod
        cfg, proj = api.load_all()
        total = int(proj.get("book", {}).get("chapters", 10))
        raw_dir = api.ROOT / "data" / "chapters" / "raw"
        chapters = []
        for n in range(1, total + 1):
            path = raw_dir / f"{n:02d}.md"
            q = ar_mod.parse_quality(path)
            chapters.append({"n": n, "path": str(path), "quality": q})
        return 200, {"ok": True, "total": total, "chapters": chapters}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_verify(h):
    """断点续跑检查：逐章是否**真正完成**（原 do_GET `elif p == "/chapters/verify"`）。

    注意用 `is_chapter_complete` 而非「文件存在」—— 半成品章节（写到一半中断）
    若被当成已完成，续跑会跳过它，成品里就会出现断章。
    """
    import nf_api as api

    try:
        cfg, proj = api.load_all()
        target_words = cfg.get("chapter", {}).get("target_words", [2000, 3000])
        total = int(proj.get("book", {}).get("chapters", 10))
        raw_dir = api.ROOT / "data" / "chapters" / "raw"
        chapters = []
        for n in range(1, total + 1):
            path = raw_dir / f"{n:02d}.md"
            ok_complete = api.is_chapter_complete(path, *target_words)
            chapters.append({"n": n, "path": str(path), "complete": ok_complete})
        return 200, {
            "ok": True,
            "total": total,
            "completed_count": sum(1 for c in chapters if c["complete"]),
            "chapters": chapters,
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_logs_tail(h):
    """运行日志尾部（原 do_GET `elif p == "/logs/tail"`）。

    Electron 主进程把 nf_api 的 stdout/stderr 追加到
    `%LOCALAPPDATA%/Temp/nf_api_child.log`，orchestrator 的 print 也在里面。
    阶段失败时 GUI 的「查看日志」读它排障（纯只读，不落盘）。
    """
    import nf_api as api

    try:
        lines = int((h._query().get("lines") or ["200"])[0])
    except (ValueError, TypeError):
        lines = 200
    lines = max(1, min(2000, lines))

    # 优先暴露 utils.run_log 的规范路径，保持「写入方」与「读取方」同源；
    # 该模块不存在时退回原候选链（老 workspace 可能还没被 seedWorkspace 刷新）。
    cands = []
    try:
        from utils.run_log import get_log_path
        cands.append(get_log_path())
    except Exception:                                       # noqa: BLE001
        pass
    localapp = api.os.environ.get("LOCALAPPDATA")
    if localapp:
        cands.append(api.Path(localapp) / "Temp" / "nf_api_child.log")
    cands.append(api.Path(api.os.environ.get("TEMP", ".")) / "nf_api_child.log")
    cands.append(api.ROOT / "logs" / "nf_api.log")
    log_path = next((c for c in cands if c.exists()), cands[0])
    if not log_path.exists():
        return 200, {"ok": True, "exists": False, "path": str(log_path),
                     "lines": [],
                     "hint": "未找到运行日志。通过控制台启动（Electron）时日志写入 "
                             "%LOCALAPPDATA%/Temp/nf_api_child.log；手动起 nf_api 时输出在终端。"}
    try:
        # 日志混编码（GBK 控制台输出 + UTF-8 混合）→ 容错解码，绝不抛异常打断响应
        text = log_path.read_bytes().decode("utf-8", errors="replace")
    except OSError as e:
        return 200, {"ok": True, "exists": False, "path": str(log_path), "lines": [],
                     "hint": "读取日志失败: " + str(e)[:200]}
    all_lines = text.splitlines()
    return 200, {"ok": True, "exists": True, "path": str(log_path),
                 "total_lines": len(all_lines), "lines": all_lines[-lines:]}


def handle_about(h):
    """关于页数据：版本、运行环境、路径（原 do_GET `elif p == "/about"`）。"""
    import nf_api as api

    try:
        ver = "dev"
        pkg = api.ROOT / "console" / "package.json"
        if pkg.exists():
            try:
                ver = api.json.loads(pkg.read_text(encoding="utf-8")).get("version", "dev")
            except Exception:                               # noqa: BLE001
                ver = "dev"
        cfg = {}
        try:
            cfg = api.pc_get_config() or {}
        except Exception:                                   # noqa: BLE001
            cfg = {}
        return 200, {
            "ok": True,
            "app": "绒花墨坊",
            "internal_name": "NovelForge",
            "version": ver,
            "python": api.sys.version.split()[0],
            "platform": api.sys.platform,
            "project_root": str(api.ROOT),
            "data_dir": str(api.ROOT / "data"),
            "books_dir": str(api.ROOT / "data" / "books"),
            "output_dir": str(api.ROOT / "output"),
            "config": (cfg.get("book") or {}),
            "repo": "https://github.com/MUYU46548/ronghuamofang",
            "endpoints": ["/health", "/state", "/about"],
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_costs_streaming(h):
    """实时花费：当前 run 累计消耗 + 预算进度（原 do_GET `elif p == "/costs/streaming"`）。"""
    import nf_api as api

    try:
        return 200, api.build_streaming_cost()
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_costs_rates(h):
    """读取合并后的定价表（RATES + 自定义覆盖）。"""
    import nf_api as api

    try:
        from utils.cost_tracker import get_merged_rates, RATES, load_custom_rates
        merged = get_merged_rates()
        # 标注来源：custom = GUI 编辑器覆盖，default = 源码刊例价
        custom_names = set(load_custom_rates().keys())
        result = {}
        for name, rate in merged.items():
            result[name] = dict(rate)
            result[name]["source"] = "custom" if name in custom_names else "default"
        return 200, {"ok": True, "rates": result,
                     "built_in": list(RATES.keys()),
                     "custom": list(custom_names)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_costs_rates_save(h, body):
    """保存定价表（GUI 定价编辑器写入）。"""
    import nf_api as api

    try:
        from utils.cost_tracker import save_custom_rates
        rates = body.get("rates", {})
        if not isinstance(rates, dict):
            return 400, {"ok": False, "error": "rates 必须为对象"}
        # 校验每条格式
        cleaned = {}
        for name, rate in rates.items():
            if not isinstance(rate, dict):
                continue
            entry = {}
            for key in ("in", "out", "cache_read"):
                if key in rate:
                    try:
                        entry[key] = float(rate[key])
                    except (ValueError, TypeError):
                        pass
            if "in" in entry and "out" in entry:
                cleaned[str(name)] = entry
        save_custom_rates(cleaned)
        return 200, {"ok": True, "saved": len(cleaned)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档；实际分发在 nf_api.do_GET 的 elif 链里）
ROUTES = (
    ("GET", "/costs/summary", handle_costs_summary),
    ("GET", "/costs/streaming", handle_costs_streaming),
    ("GET", "/costs/rates", handle_costs_rates),
    ("POST", "/costs/rates", handle_costs_rates_save),
    ("GET", "/env/open", handle_env_open),
    ("GET", "/batch_refine/progress", handle_batch_refine_progress),
    ("GET", "/chapters/quality", handle_chapters_quality),
    ("GET", "/chapters/verify", handle_chapters_verify),
    ("GET", "/logs/tail", handle_logs_tail),
    ("GET", "/about", handle_about),
)
