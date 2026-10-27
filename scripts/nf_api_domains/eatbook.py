# -*- coding: utf-8 -*-
"""吃书域：把一本写完的书抽成正典四表（GUI 入口；2026-10-27 集成自 SP-1024）。

## 五个端点

| 端点 | 作用 | 会不会花钱 |
|---|---|---|
| `GET /eatbook/list` | 已建的吃书目录（章数/已摘要/运行态/花费） | 否 |
| `GET /eatbook/status?name=` | 单个目录的进度与各表条数（GUI 轮询） | 否 |
| `POST /eatbook/preview` | 只数标题/字数/预计调用数，**不写盘** | 否 |
| `POST /eatbook/init` | 拆章建目录（同名已存在 → 409，不覆盖） | 否 |
| `POST /eatbook/run` | 后台跑管线（`start_job`，与其他任务互斥） | **是** |
| `POST /eatbook/merge` | 并进 setting.json：默认只预演，`yes:true` 才写（写前备份） | 否 |

## 三条纪律（与域模块同源）

1. 只 `return (status, payload)`，响应由 `nf_api._send` 发出；
2. 路径一律 `api.ROOT / ...`：吃书目录、setting.json、词表都按 ROOT 解析，
   `--root` 场景下不会静默读写另一个项目；
3. 花钱的那一步（`/run`）必须进 `start_job`：与流水线共用一把锁，
   不会出现「一边吃书一边 stage4」两个任务抢 `data/`。
"""
import json
from pathlib import Path

import nf_api as api
from utils.eatbook import runner as E
from utils.eatbook.llm import LLMCallError, make_call, new_stats


def _eatbook_root() -> Path:
    return api.ROOT / "data" / "eatbook"


def _safe_name(name: str) -> str:
    n = str(name or "").strip()
    if not n or "/" in n or "\\" in n or ".." in n or n in (".", ".."):
        raise ValueError("目录名非法：%r" % name)
    return n


def _camp(name: str) -> Path:
    return _eatbook_root() / _safe_name(name)


def _summary(camp: Path) -> dict:
    man = E.load_manifest(camp)
    chapters = man.get("chapters") or []
    done = sum(1 for c in chapters if (camp / c.get("summary", "")).exists())
    counts = {}
    for fname in ("characters.json", "settings.json", "timeline.json",
                  "foreshadow.json"):
        p = camp / fname
        try:
            counts[fname] = len(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else 0
        except Exception:                                      # noqa: BLE001
            counts[fname] = -1
    cost = {}
    cp = camp / "cost.json"
    if cp.exists():
        try:
            cost = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            cost = {}
    return {"name": man.get("name") or camp.name,
            "source": man.get("source"),
            "chapters": len(chapters), "summarized": done,
            "counts": counts, "cost": cost,
            "state": E.read_state(camp),
            "report_exists": (camp / "report.md").exists()}


def handle_eatbook_list(h):
    root = _eatbook_root()
    items = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if not (d / "manifest.json").exists():
                continue
            try:
                items.append(_summary(d))
            except Exception as e:                             # noqa: BLE001
                items.append({"name": d.name, "error": "%s: %s" % (type(e).__name__, e)})
    return 200, {"ok": True, "root": "data/eatbook", "items": items}


def handle_eatbook_status(h):
    q = h._query() if hasattr(h, "_query") else {}
    name = (q.get("name") or [""])[0]
    if not name:
        return 400, {"ok": False, "error": "name 必填"}
    camp = _camp(name)
    if not (camp / "manifest.json").exists():
        return 404, {"ok": False, "error": "没有这个吃书目录：%s" % name}
    return 200, {"ok": True, **_summary(camp)}


def handle_eatbook_preview(h, body):
    try:
        body = body or {}
        path = str(body.get("path") or "").strip()
        if not path:
            return 400, {"ok": False, "error": "path 必填（书稿 .txt 路径）"}
        p = Path(path)
        if not p.is_absolute():
            p = api.ROOT / path
        info = E.preview_book(p, pattern=str(body.get("pattern") or ""),
                              max_chapters=int(body.get("max_chapters") or 0),
                              batch_size=int(body.get("batch_size") or 30))
        return 200, {"ok": True, **info}
    except FileNotFoundError as e:
        return 404, {"ok": False, "error": str(e)}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_eatbook_init(h, body):
    try:
        body = body or {}
        path = str(body.get("path") or "").strip()
        if not path:
            return 400, {"ok": False, "error": "path 必填（书稿 .txt 路径）"}
        p = Path(path)
        if not p.is_absolute():
            p = api.ROOT / path
        name = str(body.get("name") or "").strip()
        if name:
            name = _safe_name(name)
        # 同名已存在单独判 409：让前端能说「换个名字或先删」，而不是笼统 400
        effective = name or Path(p).stem
        if (_eatbook_root() / effective / "manifest.json").exists():
            return 409, {"ok": False,
                         "error": "吃书目录已存在：data/eatbook/%s（换名字或先删掉）"
                                  % effective}
        camp = E.split_book(p, name=name, pattern=str(body.get("pattern") or ""),
                            max_chapters=int(body.get("max_chapters") or 0),
                            root=_eatbook_root())
        return 200, {"ok": True, **_summary(camp),
                     "message": "已拆章 → data/eatbook/%s" % camp.name}
    except FileExistsError as e:
        return 409, {"ok": False, "error": str(e)}
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_eatbook_run(h, body):
    """后台跑吃书管线。**会花 token**，与流水线共用 `start_job` 互斥。"""
    try:
        body = body or {}
        name = _safe_name(body.get("name"))
        camp = _camp(name)
        if not (camp / "manifest.json").exists():
            return 404, {"ok": False, "error": "没有这个吃书目录：%s" % name}
        stages = body.get("stages")
        if isinstance(stages, str):
            stages = [int(x) for x in stages.replace("，", ",").split(",") if x.strip()]
        stages = [int(x) for x in (stages or list(E.STAGES))]
        force = bool(body.get("force"))
        batch_size = int(body.get("batch_size") or 30)
        tail = int(body.get("tail") or 0)
        wordlist = str(body.get("wordlist") or "").strip() or None

        cfg, _ = api.load_all()

        def _fn():
            client = api._client_for_env(cfg, "default")
            stats = new_stats()
            try:
                call = make_call(client, stats=stats)
            except LLMCallError as e:
                E._state(camp, state="failed", stage=0, stage_name="失败",
                         error=str(e))
                return False, str(e)
            try:
                res = E.run(camp, call, stages=stages, force=force,
                            batch_size=batch_size, consistency_tail=tail,
                            wordlist_path=wordlist, stats=stats)
            except Exception as e:                             # noqa: BLE001
                return False, "%s: %s" % (type(e).__name__, e)
            detail = ("吃书完成：%d 章；角色 %s · 设定 %s · 时间线 %s · 伏笔 %s；"
                      "花费 %.4f 元（%d 次调用）"
                      % (res.get("stages", {}).get("2", {}).get("done", 0),
                         (camp / "characters.json").exists()
                         and len(json.loads((camp / "characters.json").read_text(encoding="utf-8"))) or 0,
                         (camp / "settings.json").exists()
                         and len(json.loads((camp / "settings.json").read_text(encoding="utf-8"))) or 0,
                         (camp / "timeline.json").exists()
                         and len(json.loads((camp / "timeline.json").read_text(encoding="utf-8"))) or 0,
                         (camp / "foreshadow.json").exists()
                         and len(json.loads((camp / "foreshadow.json").read_text(encoding="utf-8"))) or 0,
                         stats.get("cost_yuan", 0.0), stats.get("calls", 0)))
            if res.get("failed"):
                return False, detail + "；%d 项失败（详见报告）" % len(res["failed"])
            return True, detail

        jid, err = api.start_job("eatbook_%s" % name, _fn)
        if err:
            return 409, {"ok": False, "error": err}
        return 202, {"ok": True, "job_id": jid, "name": name,
                     "message": "已开始吃书（后台跑，进度看 /eatbook/status）"}
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_eatbook_merge(h, body):
    try:
        body = body or {}
        name = _safe_name(body.get("name"))
        camp = _camp(name)
        if not (camp / "manifest.json").exists():
            return 404, {"ok": False, "error": "没有这个吃书目录：%s" % name}
        yes = bool(body.get("yes"))
        setting = api.ROOT / "data" / "setting" / "setting.json"
        report = E.merge_into_setting(camp, setting, dry_run=not yes)
        return 200, {"ok": True, "written": yes, **report}
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    except Exception as e:                                     # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/eatbook/list", handle_eatbook_list),
    ("GET", "/eatbook/status", handle_eatbook_status),
    ("POST", "/eatbook/preview", handle_eatbook_preview),
    ("POST", "/eatbook/init", handle_eatbook_init),
    ("POST", "/eatbook/run", handle_eatbook_run),
    ("POST", "/eatbook/merge", handle_eatbook_merge),
)
