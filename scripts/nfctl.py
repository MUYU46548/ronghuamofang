# -*- coding: utf-8 -*-
"""nfctl — Agent 面向的绒花墨坊只读控制台。

给外部 Agent（Hermes / WorkBuddy 等）一个薄而确定的入口，避免三件事：

  1. 手拼 curl —— 引号、URL 编码、超时、JSON 解析、连接拒绝的处理都得自己写；
  2. 逐个跑 status 类脚本再把结果手工拼起来；
  3. 靠猜理解 data/ 下的产物结构与命名。

**只读**：不写任何文件、不调 LLM、零 token。
跑阶段 / 审批 / 打回 / 改配置 / 改提示词 / 删改产物一律走既有脚本 ——
它们带着快照、备份、确认与审计逻辑（`reject.py`、`approve.py`、`snapshot.py` …），
在这里重造等于把那些保护绕过，所以刻意不做。

用法（workdir = 项目根，Python 用项目 .venv）：

    python scripts/nfctl.py status [--json]         # 一屏全景：项目/阶段/进度/成本/素材/产物
    python scripts/nfctl.py check  [--json]         # 环境自检：密钥/配置/端口/gates/重复键
    python scripts/nfctl.py doctor [--json]         # 数据一致性自检：产物完整性/孤儿文件/数据库
    python scripts/nfctl.py serve  [--port 8766]    # 启动本地调试看板（零依赖）
    python scripts/nfctl.py api <GET路径> [--json]  # 只读转发到 nf_api（服务需在跑）

退出码：0 = 成功；1 = 参数或读取错误；2 = 后端不可用（仅 api 子命令）。
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

API_HOST = "127.0.0.1"
API_PORT = 8765
API_TIMEOUT = 8

# 与 orchestrator.STAGES 对应的人类可读名（仅用于展示，不参与任何判断）
STAGE_NAMES = {
    1: "素材→设定集",
    2: "整体大纲",
    3: "逐章大纲",
    4: "逐章写作",
    5: "逻辑检查",
    6: "润色",
    7: "Word 成品",
    8: "Markdown 分卷导出",
}


# ---------------------------------------------------------------- 基础工具
def _read_yaml(path: Path, default=None):
    """宽容读 YAML：文件缺失/语法坏都返回 default，绝不抛。"""
    try:
        import yaml
    except ImportError:
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return default


def _find_duplicate_keys(path: Path):
    """扫描 YAML 重复键，返回 [(行号, 键路径)]。

    为什么单独做这件事：YAML 的重复键**后者静默覆盖前者**，不报错、不告警。
    2026-09-23 实测踩到 —— config/system.yaml 的 gates.agent_mode 同时存在
    true 与 false，生效的是后面那个 false，而界面/代码里看着"设过了"。
    这类"看起来配好了、实际没生效"的坑必须能被机器一眼看出来。
    """
    try:
        import yaml
    except ImportError:
        return []
    try:
        node = yaml.compose(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    dups = []

    def walk(n, prefix=""):
        if isinstance(n, yaml.MappingNode):
            seen = set()
            for k, v in n.value:
                key = str(getattr(k, "value", ""))
                if key in seen:
                    dups.append((k.start_mark.line + 1, prefix + key))
                seen.add(key)
                walk(v, prefix + key + ".")
        elif isinstance(n, yaml.SequenceNode):
            for item in n.value:
                walk(item, prefix)

    if node is not None:
        walk(node)
    return dups


def _count_files(dirpath: Path, pattern: str = "*.md", exclude=("README.md",)):
    """数目录下的产物文件。目录不存在 = 0（空项目是合法状态，不是错误）。"""
    if not dirpath.is_dir():
        return 0
    return sum(1 for p in dirpath.glob(pattern) if p.name not in exclude)


def _api_running(port: int = API_PORT) -> bool:
    """探测 nf_api 是否在监听（只连一下，不请求）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((API_HOST, port)) == 0


def _api_get(path: str, port: int = API_PORT):
    """只读转发 GET。返回 (ok, payload)；任何异常都变成 (False, {error})。"""
    url = "http://%s:%d%s" % (API_HOST, port, path)
    try:
        # 显式标注来源：Agent 调用（与 GUI 的 X-Mofang-Source: gui 区分开，
        # 便于 nf_api 侧审计/守卫按来源分流）
        req = urllib.request.Request(url, headers={"X-Mofang-Source": "agent"})
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            return True, json.loads(raw)
        except json.JSONDecodeError:
            return True, {"raw": raw[:2000]}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            body = {"error": "HTTP %s" % e.code}
        return False, body
    except Exception as e:
        return False, {"error": "%s: %s" % (type(e).__name__, e)}


def _env_key_state(root: Path):
    """检查各 provider 声明的 api_key_env 是否已在 .env 里有非空值。

    **只报有无，绝不回显值**（密钥红线）。
    """
    sysconf = _read_yaml(root / "config" / "system.yaml", {}) or {}
    providers = sysconf.get("providers") or {}
    wanted = []
    if isinstance(providers, dict):
        for name, p in providers.items():
            if isinstance(p, dict) and p.get("api_key_env"):
                wanted.append((name, str(p["api_key_env"])))

    values = {}
    env_file = root / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()

    return {env_name: bool(values.get(env_name)) for _, env_name in wanted}, [e for _, e in wanted]


# ---------------------------------------------------------------- status
def collect_status(root: Path) -> dict:
    """离线全景（不需要 nf_api 在跑）。全部来自磁盘产物。"""
    proj = _read_yaml(root / "config" / "project.yaml", {}) or {}
    book = proj.get("book") or {}
    sysconf = _read_yaml(root / "config" / "system.yaml", {}) or {}
    gates = sysconf.get("gates") or {}

    out = {
        "root": str(root),
        "project": {
            "name": book.get("name"),
            "genre": book.get("genre"),
            "chapters": book.get("chapters"),
            "target_words": book.get("target_words"),
            "style": book.get("style"),
            "name_pending": (book.get("name") in (None, "", "（待填写）")),
        },
        "stage": {},
        "progress": {},
        "cost": {},
        "pending_approval": [],
        "materials": {},
        "setting": {},
        "artifacts": {},
        "api": {"running": _api_running(), "port": API_PORT},
    }

    # 阶段与进度（ProgressManager 只读使用；它的 save() 才是写）
    try:
        from utils.progress_manager import ProgressManager

        pm = ProgressManager(root / "data" / "state" / "progress.json")
        stages = {}
        for key, st in (pm.data.get("stages") or {}).items():
            stages[key] = {
                "status": st.get("status"),
                "approved": bool(st.get("approved", False)),
                "finished_at": st.get("finished_at"),
            }
        cur = pm.data.get("current_stage", 1)
        done_ch = pm.completed_chapters(4)
        out["stage"] = {
            "current": cur,
            "name": STAGE_NAMES.get(int(cur) if str(cur).isdigit() else 0, "?"),
            "stages": stages,
        }
        out["progress"] = {
            "chapters_done": len(done_ch),
            "chapters_failed": len(pm.failed_chapters(4)),
            "last_modified": pm.data.get("last_modified"),
        }
        budget = pm.budget()
        limit = budget.get("limit_yuan") or 0
        spent = budget.get("spent_yuan") or 0.0
        out["cost"] = {
            "spent_yuan": round(float(spent), 4),
            "limit_yuan": limit,
            "ratio": round(float(spent) / limit, 4) if limit else None,
            "paused": bool(budget.get("paused")),
        }
        # 待人工确认 = 「被 gates 要求审批」且「该阶段已完成」且「尚未审批」
        for s in (gates.get("require_approval") or []):
            st = stages.get(str(s)) or {}
            if st.get("status") == "done" and not st.get("approved"):
                out["pending_approval"].append(int(s))
    except Exception as e:
        out["stage"] = {"error": "%s: %s" % (type(e).__name__, e)}

    # 素材
    out["materials"] = {
        "raw_cards": _count_files(root / "materials" / "raw"),
        "scraps": _count_files(root / "materials" / "original_scraps"),
    }

    # 设定集：用 setting_schema.is_character 区分人物与非人物条目
    setting_file = root / "data" / "setting" / "setting.json"
    setting_info = {"exists": setting_file.exists()}
    if setting_file.exists():
        try:
            data = json.loads(setting_file.read_text(encoding="utf-8"))
            chars = data.get("characters") or []
            setting_info["entries"] = len(chars)
            try:
                from utils.setting_schema import is_character

                setting_info["characters"] = sum(1 for c in chars if is_character(c))
                setting_info["non_character"] = len(chars) - setting_info["characters"]
            except Exception:
                pass  # is_character 不可用时只报总数
            setting_info["world"] = len(data.get("world") or [])
            setting_info["plot_fragments"] = len(data.get("plot_fragments") or [])
        except Exception as e:
            setting_info["error"] = "%s: %s" % (type(e).__name__, e)
    out["setting"] = setting_info

    # 产物
    outline = root / "data" / "outline" / "global.md"
    chapters_dir = root / "data" / "chapters"
    word_files = sorted((root / "output").glob("*.docx")) if (root / "output").is_dir() else []
    out["artifacts"] = {
        "outline_exists": outline.exists(),
        "outline_chars": len(outline.read_text(encoding="utf-8", errors="replace")) if outline.exists() else 0,
        "chapters": {
            "raw": _count_files(chapters_dir / "raw"),
            "checked": _count_files(chapters_dir / "checked"),
            "refined": _count_files(chapters_dir / "refined"),
        },
        "word": [p.name for p in word_files],
    }
    return out


def render_status(s: dict) -> str:
    p, st, art = s["project"], s["stage"], s["artifacts"]
    lines = []
    lines.append("绒花墨坊 · %s" % (p.get("name") or "(未命名)"))
    if p.get("name_pending"):
        lines.append("  ! 书名仍是「（待填写）」—— config/project.yaml 尚未填")
    lines.append("  类型: %s   目标章数: %s   目标字数: %s" % (p.get("genre"), p.get("chapters"), p.get("target_words")))
    lines.append("  项目根: %s" % s["root"])

    lines.append("")
    if "error" in st:
        lines.append("阶段: 读取失败 —— %s" % st["error"])
    else:
        lines.append("当前阶段: %s · %s" % (st.get("current"), st.get("name")))
        mark = {"done": "[x]", "running": "[~]", "failed": "[!]", "pending": "[ ]"}
        for k in sorted((st.get("stages") or {}).keys(), key=lambda x: int(x) if x.isdigit() else 99):
            row = st["stages"][k]
            nm = STAGE_NAMES.get(int(k), "?") if k.isdigit() else "?"
            extra = []
            if row.get("approved"):
                extra.append("已审批")
            if row.get("finished_at"):
                extra.append(str(row["finished_at"])[:16])
            lines.append("  %s 阶段%s %s%s" % (
                mark.get(row.get("status"), "[?]"), k, nm,
                ("  (" + " / ".join(extra) + ")") if extra else "",
            ))

    cost = s.get("cost") or {}
    if cost:
        lines.append("")
        lines.append("花费: ¥%s / ¥%s%s%s" % (
            cost.get("spent_yuan"), cost.get("limit_yuan"),
            ("（%.1f%%）" % (cost["ratio"] * 100)) if cost.get("ratio") is not None else "",
            "  ⚠ 已熔断暂停" if cost.get("paused") else "",
        ))
    if s.get("pending_approval"):
        lines.append("待人工确认的审批门: 阶段 %s" % ", ".join(str(x) for x in s["pending_approval"]))
    else:
        lines.append("待人工确认的审批门: 无")

    pr = s.get("progress") or {}
    lines.append("")
    lines.append("章节进度: 已完成 %s 章，失败 %s 章" % (pr.get("chapters_done", 0), pr.get("chapters_failed", 0)))
    lines.append("产物: 大纲%s（%s 字）· 正文 raw %s / checked %s / refined %s · Word %s 个" % (
        "有" if art.get("outline_exists") else "无", art.get("outline_chars"),
        art["chapters"].get("raw"), art["chapters"].get("checked"), art["chapters"].get("refined"),
        len(art.get("word") or []),
    ))
    mat, sett = s["materials"], s["setting"]
    lines.append("素材: 设定卡 %s 张 · 碎片 %s 条" % (mat.get("raw_cards"), mat.get("scraps")))
    if sett.get("exists"):
        lines.append("设定集: 条目 %s（人物 %s / 其他 %s）· world %s · 碎片 %s" % (
            sett.get("entries"), sett.get("characters", "?"), sett.get("non_character", "?"),
            sett.get("world"), sett.get("plot_fragments"),
        ))
    else:
        lines.append("设定集: 尚未生成（data/setting/setting.json 不存在）")

    lines.append("")
    lines.append("nf_api: %s（127.0.0.1:%s）" % ("运行中" if s["api"]["running"] else "未运行", s["api"]["port"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- check
def collect_check(root: Path) -> dict:
    """环境自检：能不能跑、缺什么、有没有静默陷阱。"""
    res = {"root": str(root), "items": [], "blocking": [], "warnings": []}

    def add(name, ok, detail, blocking=False, warning=False):
        res["items"].append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            (res["blocking"] if blocking else res["warnings"]).append(name)

    # Python 解释器
    venv_py = root / ".venv" / "Scripts" / "python.exe"
    add("venv python", venv_py.exists(), str(venv_py) if venv_py.exists() else "缺失：%s" % venv_py, blocking=True)

    # system.yaml（含重复键检测）
    sys_path = root / "config" / "system.yaml"
    sysconf = _read_yaml(sys_path, None)
    add("config/system.yaml", isinstance(sysconf, dict), "可解析" if isinstance(sysconf, dict) else "缺失或语法错误")
    dups = _find_duplicate_keys(sys_path)
    if dups:
        # 重复键 = 静默覆盖，按阻塞处理（配置看着对、实际不是）
        res["items"].append({
            "name": "YAML 重复键",
            "ok": False,
            "detail": "；".join("第 %s 行 %s" % (ln, k) for ln, k in dups) + "（后者会静默覆盖前者）",
        })
        res["blocking"].append("YAML 重复键")

    # project.yaml
    proj = _read_yaml(root / "config" / "project.yaml", {}) or {}
    book = proj.get("book") or {}
    name = book.get("name")
    add("书名已填写", name not in (None, "", "（待填写）"), "name=%r" % name, warning=True)

    # API Key（只看有无）
    key_state, wanted = _env_key_state(root)
    if wanted:
        ok = any(key_state.values())
        add("API Key 已配置", ok, "；".join("%s=%s" % (k, "有" if v else "空") for k, v in key_state.items()),
            blocking=True)
    else:
        add("API Key 已配置", False, "config/system.yaml 未声明 api_key_env", warning=True)

    # 素材
    cards = _count_files(root / "materials" / "raw")
    scraps = _count_files(root / "materials" / "original_scraps")
    add("素材非空", cards + scraps > 0, "materials/raw %s 张 · original_scraps %s 条" % (cards, scraps), warning=True)

    # 端口
    running = _api_running()
    add("nf_api 端口 %s" % API_PORT, True,
        "已在监听" if running else "未监听（CLI 类操作不需要它；GUI/流式才需要）")

    # gates 关键开关（默认关的破坏性动作，列出来以免误以为会自动跑）
    gates = (sysconf or {}).get("gates") or {}
    res["gates"] = {
        "agent_mode": gates.get("agent_mode"),
        "require_approval": gates.get("require_approval"),
        "auto_rewrite": gates.get("auto_rewrite"),
        "setting_refine_auto": gates.get("setting_refine_auto"),
        "auto_refine": gates.get("auto_refine"),
        "review_after_stage4": gates.get("review_after_stage4"),
        "proofread_after_polish": gates.get("proofread_after_polish"),
    }
    return res


def render_check(c: dict) -> str:
    lines = ["环境自检 · %s" % c["root"], ""]
    for it in c["items"]:
        lines.append("  %s %s: %s" % ("[ok]" if it["ok"] else "[!!]", it["name"], it["detail"]))
    lines.append("")
    g = c.get("gates") or {}
    lines.append("gates: agent_mode=%s · require_approval=%s · auto_rewrite=%s · setting_refine_auto=%s · auto_refine=%s" % (
        g.get("agent_mode"), g.get("require_approval"), g.get("auto_rewrite"),
        g.get("setting_refine_auto"), g.get("auto_refine"),
    ))
    lines.append("阻塞项: %s" % (", ".join(c["blocking"]) if c["blocking"] else "无"))
    lines.append("提醒项: %s" % (", ".join(c["warnings"]) if c["warnings"] else "无"))
    if c["blocking"]:
        lines.append("→ 有阻塞项时不要开跑；先按上面 detail 修掉。")
    return "\n".join(lines)


# ---------------------------------------------------------------- doctor
def collect_doctor(root: Path) -> dict:
    """数据一致性自检：产物完整性、章节文件一致性、成本记录完整性。

    与 check 的区别：check 查「能不能跑」（环境），doctor 查「数据对不对」（产物）。
    """
    res = {"root": str(root), "items": [], "blocking": [], "warnings": []}

    def add(name, ok, detail, blocking=False, warning=False):
        res["items"].append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            (res["blocking"] if blocking else res["warnings"]).append(name)

    # 章节文件一致性
    chapters_dir = root / "data" / "chapters"
    raw_dir = chapters_dir / "raw"
    checked_dir = chapters_dir / "checked"
    refined_dir = chapters_dir / "refined"

    raw_files = sorted([f.name for f in raw_dir.glob("*.md")]) if raw_dir.is_dir() else []
    checked_files = sorted([f.name for f in checked_dir.glob("*.md")]) if checked_dir.is_dir() else []
    refined_files = sorted([f.name for f in refined_dir.glob("*.md")]) if refined_dir.is_dir() else []

    # 阶段4 完成 → raw 应有文件
    progress_file = root / "data" / "state" / "progress.json"
    stage4_done = False
    stage5_done = False
    stage6_done = False
    if progress_file.exists():
        try:
            prog = json.loads(progress_file.read_text(encoding="utf-8"))
            stages = prog.get("stages") or {}
            stage4_done = (stages.get("4") or {}).get("status") == "done"
            stage5_done = (stages.get("5") or {}).get("status") == "done"
            stage6_done = (stages.get("6") or {}).get("status") == "done"
        except Exception:
            pass

    if stage4_done:
        add("raw 章节文件", len(raw_files) > 0,
            "%d 个文件" % len(raw_files) if raw_files else "阶段4已完成但 raw/ 为空",
            blocking=not raw_files)
    if stage5_done:
        add("checked 章节文件", len(checked_files) >= len(raw_files),
            "raw %d / checked %d" % (len(raw_files), len(checked_files)),
            blocking=len(checked_files) < len(raw_files))
    if stage6_done:
        add("refined 章节文件", len(refined_files) >= len(raw_files),
            "raw %d / refined %d" % (len(raw_files), len(refined_files)),
            blocking=len(refined_files) < len(raw_files))

    # 孤儿文件：在 raw/checked/refined 里但不在另一个里
    raw_set = set(raw_files)
    checked_set = set(checked_files)
    refined_set = set(refined_files)
    orphans_raw = raw_set - checked_set
    orphans_checked = checked_set - raw_set
    if stage5_done and (orphans_raw or orphans_checked):
        add("raw/checked 一致性", False,
            "仅在 raw: %s；仅在 checked: %s" % (
                ", ".join(sorted(orphans_raw)[:3]) or "无",
                ", ".join(sorted(orphans_checked)[:3]) or "无"),
            warning=True)

    # 大纲文件
    outline = root / "data" / "outline" / "global.md"
    add("全局大纲", outline.exists(),
        "%d 字" % len(outline.read_text(encoding="utf-8", errors="replace")) if outline.exists() else "不存在",
        blocking=not outline.exists())

    outline_chapters_dir = root / "data" / "outline" / "chapters"
    outline_chapters = sorted([f.name for f in outline_chapters_dir.glob("*.md")]) if outline_chapters_dir.is_dir() else []
    add("逐章大纲", len(outline_chapters) > 0,
        "%d 个文件" % len(outline_chapters) if outline_chapters else "不存在（阶段3 未跑）")

    # 成本记录一致性
    db_path = root / "logs" / "runs.db"
    if db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            # runs 表：不应有 status='running' 的脏行
            dirty_runs = conn.execute(
                "SELECT id, started_at, finished_at FROM runs WHERE status='running'"
            ).fetchall()
            add("runs 脏行", len(dirty_runs) == 0,
                "%d 条 running 脏行（应始终为 0）" % len(dirty_runs),
                blocking=len(dirty_runs) > 0)
            # cost_log 表：不应有负数 cost
            neg_cost = conn.execute(
                "SELECT COUNT(*) FROM cost_log WHERE cost_yuan < 0"
            ).fetchone()[0]
            add("cost_log 负数", neg_cost == 0,
                "%d 条负数记录" % neg_cost,
                blocking=neg_cost > 0)
            # chapter_log 表：不应有 error 非空但 status='ok' 的矛盾行
            bad_ch = conn.execute(
                "SELECT COUNT(*) FROM chapter_log WHERE status='ok' AND error IS NOT NULL AND error != ''"
            ).fetchone()[0]
            add("chapter_log 矛盾行", bad_ch == 0,
                "%d 条 status=ok 但 error 非空" % bad_ch,
                warning=True)
            conn.close()
        except Exception as e:
            add("数据库检查", False, "%s: %s" % (type(e).__name__, e), warning=True)

    # 孤儿文件检测：data/ 下不该存在的文件
    allowed_patterns = {
        "data/chapters": ["*.md"],
        "data/outline": ["*.md", "*.json"],
        "data/setting": ["*.json", "*.md"],
        "data/state": ["*.json", "*.jsonl", "*.log"],
        "data/summaries": ["*.md"],
    }
    orphans = []
    for subdir, patterns in allowed_patterns.items():
        d = root / subdir
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.is_file() and not any(f.match(p) for p in patterns):
                orphans.append(str(f.relative_to(root)))
    add("孤儿文件", len(orphans) == 0,
        "%d 个不在预期模式的文件" % len(orphans) if orphans else "无",
        warning=True)
    if orphans:
        res["orphan_files"] = orphans[:10]

    return res


def render_doctor(d: dict) -> str:
    lines = ["数据一致性自检 · %s" % d["root"], ""]
    for it in d["items"]:
        lines.append("  %s %s: %s" % ("[ok]" if it["ok"] else "[!!]", it["name"], it["detail"]))
    lines.append("")
    lines.append("阻塞项: %s" % (", ".join(d["blocking"]) if d["blocking"] else "无"))
    lines.append("提醒项: %s" % (", ".join(d["warnings"]) if d["warnings"] else "无"))
    if d["blocking"]:
        lines.append("→ 数据有问题，先修再跑。")
    else:
        lines.append("→ 数据一致性 OK。")
    return "\n".join(lines)


# ---------------------------------------------------------------- serve
def start_serve(root: Path, port: int = 8766):
    """启动本地调试看板（零依赖，类似方寸的 tegula serve）。

    提供：
    - GET /          → HTML 调试面板
    - GET /api/status → JSON 全景
    - GET /api/check  → JSON 环境自检
    - GET /api/doctor → JSON 数据一致性自检
    - GET /api/logs   → 最近 N 行日志
    """
    import http.server
    import threading

    class DebugHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 静默

        def _send_json(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html, status=200):
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/" or self.path == "/index.html":
                self._send_html(self._render_dashboard())
            elif self.path == "/api/status":
                self._send_json(collect_status(root))
            elif self.path == "/api/check":
                self._send_json(collect_check(root))
            elif self.path == "/api/doctor":
                self._send_json(collect_doctor(root))
            elif self.path == "/api/logs":
                self._send_json(self._get_logs())
            else:
                self._send_json({"error": "not found"}, 404)

        def _get_logs(self):
            log_file = root / "logs" / "nf_api_child.log"
            if not log_file.exists():
                return {"lines": [], "total": 0}
            try:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                return {"lines": lines[-100:], "total": len(lines)}
            except Exception as e:
                return {"error": str(e)}

        def _render_dashboard(self):
            s = collect_status(root)
            c = collect_check(root)
            d = collect_doctor(root)

            def badge(ok):
                return '<span style="color:green">OK</span>' if ok else '<span style="color:red">FAIL</span>'

            html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>绒花墨坊调试面板</title>
<style>
body{font-family:system-ui,sans-serif;max-width:900px;margin:20px auto;padding:0 16px;background:#1a1a2e;color:#eee}
h1{color:#e94560}h2{color:#0f3460;margin-top:24px}
table{width:100%;border-collapse:collapse;margin:8px 0}
td,th{padding:6px 8px;border:1px solid #333;text-align:left}
th{background:#16213e}tr:nth-child(even){background:#0f3460}
.ok{color:green}.bad{color:red}.warn{color:orange}
a{color:#e94560}
</style></head><body>
<h1>绒花墨坊调试面板</h1>
<p>项目根: %(root)s</p>

<h2>环境自检</h2>
<table><tr><th>项</th><th>状态</th><th>详情</th></tr>
%(check_rows)s</table>

<h2>数据一致性</h2>
<table><tr><th>项</th><th>状态</th><th>详情</th></tr>
%(doctor_rows)s</table>

<h2>状态概览</h2>
<table>
<tr><th>书名</th><td>%(name)s</td></tr>
<tr><th>当前阶段</th><td>%(stage)s · %(stage_name)s</td></tr>
<tr><th>花费</th><td>¥%(spent)s / ¥%(limit)s（%(ratio)s%%）</td></tr>
<tr><th>章节</th><td>完成 %(chapters_done)s 章，失败 %(chapters_failed)s 章</td></tr>
<tr><th>产物</th><td>大纲 %(outline_chars)s 字 · raw %(raw)s / checked %(checked)s / refined %(refined)s</td></tr>
</table>

<h2>最近日志</h2>
<pre id="logs" style="background:#0f3460;padding:8px;max-height:300px;overflow:auto;font-size:12px">%(logs)s</pre>
<p><a href="/api/status">JSON Status</a> · <a href="/api/check">JSON Check</a> · <a href="/api/doctor">JSON Doctor</a></p>
</body></html>""" % {
                "root": s["root"],
                "check_rows": "".join(
                    '<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                        it["name"], badge(it["ok"]), it["detail"]
                    ) for it in c["items"]
                ),
                "doctor_rows": "".join(
                    '<tr><td>%s</td><td>%s</td><td>%s</td></tr>' % (
                        it["name"], badge(it["ok"]), it["detail"]
                    ) for it in d["items"]
                ),
                "name": s["project"].get("name") or "(未命名)",
                "stage": s["stage"].get("current", "?"),
                "stage_name": s["stage"].get("name", "?"),
                "spent": s.get("cost", {}).get("spent_yuan", 0),
                "limit": s.get("cost", {}).get("limit_yuan", 0),
                "ratio": (s.get("cost", {}).get("ratio") or 0) * 100,
                "chapters_done": s.get("progress", {}).get("chapters_done", 0),
                "chapters_failed": s.get("progress", {}).get("chapters_failed", 0),
                "outline_chars": s.get("artifacts", {}).get("outline_chars", 0),
                "raw": s.get("artifacts", {}).get("chapters", {}).get("raw", 0),
                "checked": s.get("artifacts", {}).get("chapters", {}).get("checked", 0),
                "refined": s.get("artifacts", {}).get("chapters", {}).get("refined", 0),
                "logs": "\n".join(self._get_logs().get("lines", [])),
            }
            return html

    server = http.server.HTTPServer(("127.0.0.1", port), DebugHandler)
    print("绒花墨坊调试面板: http://127.0.0.1:%d" % port)
    print("  /           → HTML 调试面板")
    print("  /api/status → JSON 全景")
    print("  /api/check  → JSON 环境自检")
    print("  /api/doctor → JSON 数据一致性自检")
    print("  /api/logs   → 最近 100 行日志")
    print("Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


# ---------------------------------------------------------------- test
# 退出码不可靠的测试（main() 不返回非零 / 靠打印文本报告结果）：
# 运行器解析 stdout 里的 FAIL 标记兜底。
NO_EXITCODE_TESTS = {"test_thinking_compat.py"}
# 默认会发起真实 LLM 调用的测试：统一强制 --offline，防测试运行器烧 token。
FORCE_OFFLINE_TESTS = {"test_thinking_compat.py"}


def run_tests(root: Path, pattern: str = "test_*.py", verbose: bool = False) -> dict:
    """统一测试运行器：发现并运行 tests/ 下所有自定义测试脚本。

    本项目测试是自定义运行器（main() + PASS/FAIL 计数），不是 pytest。
    返回 {"total": N, "passed": N, "failed": N, "errors": [...]}。
    """
    import subprocess

    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return {"total": 0, "passed": 0, "failed": 0, "errors": ["tests/ 目录不存在"]}

    # 收集所有 test_*.py 文件（排除 e2e/ —— 那些需要 Playwright + 特殊环境）
    test_files = sorted(tests_dir.glob(f"unit/{pattern}")) + sorted(tests_dir.glob(f"http/{pattern}"))
    if not test_files:
        return {"total": 0, "passed": 0, "failed": 0, "errors": ["未找到测试文件"]}

    python_exe = root / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)  # 回退到当前解释器

    results = {"total": 0, "passed": 0, "failed": 0, "errors": [], "details": []}

    for tf in test_files:
        rel = tf.relative_to(root)
        results["total"] += 1
        cmd = [str(python_exe), str(tf)]
        if tf.name in FORCE_OFFLINE_TESTS:
            cmd.append("--offline")            # 强制离线：绝不真实调 LLM
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1200,
                cwd=str(root),
            )
            # 自定义运行器：exit 0 = 全 PASS，非 0 = 有 FAIL
            passed = proc.returncode == 0
            # 退出码不可靠的测试：解析 stdout 的失败标记兜底
            if passed and tf.name in NO_EXITCODE_TESTS:
                fail_markers = [l for l in proc.stdout.splitlines()
                                if l.strip().startswith("失败项") and "失败项: " in l
                                and l.split("失败项: ", 1)[1].strip()]
                passed = not fail_markers
            if passed:
                results["passed"] += 1
                if verbose:
                    results["details"].append({"file": str(rel), "status": "PASS", "output": proc.stdout[-500:]})
            else:
                results["failed"] += 1
                # 提取 FAIL 行
                fail_lines = [l for l in proc.stdout.splitlines() if "FAIL" in l or "ERROR" in l]
                results["errors"].append({
                    "file": str(rel),
                    "exit_code": proc.returncode,
                    "failures": fail_lines[:10],
                    "stderr": proc.stderr[-300:] if proc.stderr else "",
                })
        except subprocess.TimeoutExpired:
            results["failed"] += 1
            results["errors"].append({"file": str(rel), "error": "超时（120s）"})
        except Exception as e:
            results["failed"] += 1
            results["errors"].append({"file": str(rel), "error": str(e)})

    return results


def render_test_results(d: dict) -> str:
    lines = ["统一测试运行器", ""]
    lines.append("  总计: %d  通过: %d  失败: %d" % (d["total"], d["passed"], d["failed"]))
    if d["errors"]:
        lines.append("")
        lines.append("失败详情:")
        for err in d["errors"]:
            lines.append("  — %s" % err.get("file", "?"))
            if "error" in err:
                lines.append("    错误: %s" % err["error"])
            if "failures" in err:
                for f in err["failures"][:5]:
                    lines.append("    %s" % f)
    if d["failed"] == 0 and d["total"] > 0:
        lines.append("")
        lines.append("→ 全部通过。")
    return "\n".join(lines)


# ---------------------------------------------------------------- main
def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # --json / --root 在子命令**前或后**都要能用。
    # argparse 默认只认前置（`nfctl.py --json status`），但最自然的写法是
    # `nfctl.py status --json` —— 后者不认就等于给 Agent 埋了个坑。
    # 两处都挂，且都用 SUPPRESS：加上不设默认值，避免子解析器把前置写法
    # 已经设好的 True 覆盖回 False（argparse 的经典坑）。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help="项目根（默认取本脚本所在项目；用于对其它书档做只读检查）")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="输出机器可读 JSON")

    ap = argparse.ArgumentParser(description="绒花墨坊 Agent 只读控制台", parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[common], help="一屏全景（离线，不需要 nf_api）")
    sub.add_parser("check", parents=[common], help="环境自检（含 YAML 重复键）")
    sub.add_parser("doctor", parents=[common], help="数据一致性自检（产物完整性/孤儿文件/数据库）")
    p_serve = sub.add_parser("serve", parents=[common], help="启动本地调试看板（零依赖）")
    p_serve.add_argument("--port", type=int, default=8766, help="端口（默认 8766）")
    p_test = sub.add_parser("test", parents=[common], help="统一测试运行器（跑 tests/ 下所有自定义测试）")
    p_test.add_argument("--pattern", default="test_*.py", help="测试文件匹配模式（默认 test_*.py）")
    p_test.add_argument("--verbose", action="store_true", help="显示每个测试的详细输出")
    p_api = sub.add_parser("api", parents=[common], help="只读转发 GET 到 nf_api")
    p_api.add_argument("path", help="如 /state、/health、/outline/trend、/costs/summary")
    p_api.add_argument("--port", type=int, default=API_PORT)

    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if getattr(args, "root", None) else DEFAULT_ROOT
    as_json = bool(getattr(args, "json", False))

    if args.cmd == "status":
        data = collect_status(root)
        print(json.dumps(data, ensure_ascii=False, indent=2) if as_json else render_status(data))
        return 0

    if args.cmd == "check":
        data = collect_check(root)
        print(json.dumps(data, ensure_ascii=False, indent=2) if as_json else render_check(data))
        return 0

    if args.cmd == "doctor":
        data = collect_doctor(root)
        print(json.dumps(data, ensure_ascii=False, indent=2) if as_json else render_doctor(data))
        return 0

    if args.cmd == "test":
        data = run_tests(root, pattern=args.pattern, verbose=args.verbose)
        print(json.dumps(data, ensure_ascii=False, indent=2) if as_json else render_test_results(data))
        return 0 if data["failed"] == 0 else 1

    if args.cmd == "serve":
        start_serve(root, port=args.port)
        return 0

    if args.cmd == "api":
        if not args.path.startswith("/"):
            print("路径需以 / 开头，例如 /state", file=sys.stderr)
            return 1
        ok, payload = _api_get(args.path, args.port)
        if not ok and not _api_running(args.port):
            msg = ("nf_api 未在 127.0.0.1:%s 运行（如需 /state、/outline/trend 等实时数据，"
                   "先起服务：.venv/Scripts/python.exe scripts/nf_api.py）") % args.port
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False, indent=2) if as_json else msg)
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
