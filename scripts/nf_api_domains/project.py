# -*- coding: utf-8 -*-
"""项目域（GET）：健康检查、状态快照、多书列表、项目结构树、配置读取。

## 为什么 /health 与 /state 放在“项目”域

它们回答的是同一个问题——“当前项目处于什么状态、能不能开工”。
GUI 启动时的冷启动引导、书本切换、初始化向导都读这一族。

## 关于 /project/status 里的 `stages`

`stages` 来自 `api.build_state()`，而其它字段是**文件系统探测**（路径 exists +
glob 计数）。两者刻意不合并：`stages` 是“流程走到哪”，exists 是“盘上有什么”。
实践中二者可能不一致（比如用户手删了产物但没重置状态），这是**有信息量的不一致**，
前端据此可以提示“状态与实际产物不符，建议打回重跑”。

## /config/project 的历史缺陷

该端点曾经**只有 POST 分支、没有 GET 分支**，而 GUI 用的是 GET → 一律 404
→ 初始化向导与“素材为空”提醒永远不触发。读操作必须挂在 do_GET 上。
"""
import json

import nf_api as api


def handle_health(h):
    """存活探测 + 当前后台任务（GUI 用它判断要不要轮询）。"""
    cur = api.CURRENT["id"]
    job = api.JOBS.get(cur) if cur else None
    return 200, {"ok": True, "current_job": cur,
                 "current_kind": (job or {}).get("kind"),
                 "allow_fake": api.ALLOW_FAKE}


def handle_state(h):
    """全站状态快照（阶段进度 / 审批位 / 预算 / 质量门），GUI 主轮询入口。"""
    try:
        return 200, api.build_state()
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_whoami(h):
    """`GET /whoami` —— 服务端认为“你是谁”（2026-11-04，作者问“以后分得清谁在调用吗”）。

    为什么要有它：作者点了按钮失败时，界面只有一句“失败/被拒”。他无法判断
    究竟是“我不是作者”（来源闸）、还是“功能坏了”。这个端点把服务端的判定
    原样说出来 —— 三种拒因、来源声明、有没有带令牌、当前引擎与 agent_mode。

    **不含任何令牌值**（只报有无），也不泄露通道细节：它只回答“你是谁”。
    """
    headers = getattr(h, "headers", None) or {}
    from utils import agent_guard
    from utils.engine import resolve_engine
    code, why = agent_guard.gui_refusal(headers)
    cfg, _proj = api.load_all()
    try:
        claim = agent_guard.normalize_source(
            headers.get(agent_guard.GUI_SOURCE_HEADER, ""))
    except Exception:                                        # noqa: BLE001
        claim = ""
    return 200, {
        "ok": True,
        "trusted_gui": code is None,
        "refusal_code": code,
        "refusal_reason": why,
        "source_claim": claim,
        "token_present": bool(headers.get(agent_guard.GUI_TOKEN_HEADER, "")),
        "agent_mode": bool(((cfg.get("gates") or {}).get("agent_mode"))),
        "engine": resolve_engine(cfg),
        "project_root": str(api.ROOT),
        "note": ("这是服务端的判定，用来解释“为什么这个按钮只让作者点”；"
                 "不含任何令牌值。"),
    }

def handle_project_list(h):
    """多书列表（data/books/ 归档 + 当前书）。"""
    try:
        return 200, api.sb_mod.list_books()
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_project_status(h):
    """项目结构树（左侧导航用）：状态 + 各产物是否存在。

    只做“存在性 + 计数”，不做内容解析——导航栏刷新频率高，
    解析内容是各页面自己的事。
    """
    try:
        project_status = {
            "book": (api.load_all()[1] or {}).get("book", {}),
            "stages": api.build_state().get("stages", []),
            "setting_exists": (api.ROOT / "data" / "setting" / "setting.json").exists(),
            "outline_exists": (api.ROOT / "data" / "outline" / "global.md").exists(),
            "chapters_count": len(list((api.ROOT / "data" / "outline" / "chapters").glob("*.md")))
            if (api.ROOT / "data" / "outline" / "chapters").exists() else 0,
            "drafts_exist": (api.ROOT / "data" / "chapters" / "raw").exists()
            and any((api.ROOT / "data" / "chapters" / "raw").glob("*.md")),
            "refined_exist": (api.ROOT / "data" / "chapters" / "refined").exists()
            and any((api.ROOT / "data" / "chapters" / "refined").glob("*.md")),
            "word_exists": bool(list((api.ROOT / "output").glob("*.docx"))),
            "materials_count": len(list((api.ROOT / "materials" / "raw").glob("*")))
            if (api.ROOT / "materials" / "raw").exists() else 0,
        }
        return 200, project_status
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_project(h):
    """读取 config/project.yaml 的归一化视图（向导 / 空素材提醒靠它）。"""
    try:
        from utils.project_config import get_project_config
        return 200, {"ok": True, "config": get_project_config()}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_style_notes(h):
    """用户风格笔记（config/project.yaml 的 book.style_notes）。

    单独开一个端点而不是塞进 /config/project：风格笔记可能很长，
    且用户在写作页签里改得频繁，没必要为它重读整个 yaml。
    """
    try:
        return 200, {"ok": True, "content": api.pc_get_style_notes(),
                     "path": "config/project.yaml", "field": "book.style_notes"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 风格范文（GUI 粘贴窗口）
# 2026-10-19：范文原来只能“用编辑器打开 materials/style_sample.md 粘”，用户提的
# 更对——直接在面板里粘贴，且它属于**用户数据**（materials/ 是书档级内容，换书跟着走）。
#
# 设计取舍：
#   · 存文件而不是塞 project.yaml：范文可达数万字，塞 yaml 会让每次读配置都变慢，
#     且 yaml 里多行标量的转义/缩进极易被手改坏；文件还能被 stage4 直接读。
#   · 不设 GUI-only：这是**创作决策**（写什么范文），不是成本/安全闸门，
#     外部 Agent 可以代写（与 target_words 同口径）。
#   · 写入一律限在工作区内（拒绝 ../ 与绝对路径外溢）；写前备份，保留最近 20 份。
DEFAULT_STYLE_REF_PATH = "materials/style_sample.md"
STYLE_REF_HISTORY_DIR = "data/state/style_reference_history"
STYLE_REF_HISTORY_KEEP = 20
STYLE_REF_MAX_CHARS = 40000          # 与 stage4 的注入上限上限一致（STYLE_REF_LIMIT_MAX）
# 范文（样例稿）使用声明：同意记录与拆解声明分开存 —— 两者事实不同：
# 拆解“不存原文”，范文“必须存盘”（否则写作阶段没法注入）。一份声明不能盖两件事。
SAMPLE_CONSENT_REL = "data/state/style_sample_consent.json"


def _sample_consent():
    p = api.ROOT / SAMPLE_CONSENT_REL
    if not p.exists():
        return {"agreed": False, "at": None}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {"agreed": bool(d.get("agreed")), "at": d.get("at")}
    except Exception:                                       # noqa: BLE001
        return {"agreed": False, "at": None}


def _set_sample_consent(agreed):
    p = api.ROOT / SAMPLE_CONSENT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"agreed": bool(agreed),
                             "at": api.datetime.now().isoformat(timespec="seconds")},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return _sample_consent()


def handle_style_sample_consent(h):
    """样例稿使用声明的同意状态（GET）。范文框据此决定“保存”要不要先弹声明。"""
    try:
        return 200, dict({"ok": True}, **_sample_consent())
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 文风规格（R22 拆锁）
# 2026-10-29：默认**不注入**任何风格规格；作者显式选一个文体代码才注入。
STYLE_RULE_OPTIONS = [
    {"code": "", "label": "不注入（推荐默认）"},
    {"code": "narrative", "label": "叙事（极简客观派）"},
    {"code": "essay", "label": "散文"},
    {"code": "letter", "label": "书信"},
    {"code": "episodic", "label": "单元"},
]


def _style_rule_value():
    from utils.project_config import get_project_config
    try:
        cfg = get_project_config() or {}
    except Exception:                                       # noqa: BLE001
        cfg = {}
    return str(((cfg.get("book") or {}).get("style_rule") or "")).strip()


def handle_config_style_rule(h):
    return 200, {"ok": True, "value": _style_rule_value(),
                 "options": STYLE_RULE_OPTIONS,
                 "note": "空 = 不注入任何风格规格（默认）；选了才把对应文风块写进 stage4"}


def handle_config_style_rule_set(h, body):
    value = str((body or {}).get("value") or "").strip()
    allowed = [o["code"] for o in STYLE_RULE_OPTIONS]
    if value not in allowed:
        return 400, {"ok": False,
                     "error": "未知的文风规格：%r（可选：%s）"
                              % (value, "、".join(x or "不注入" for x in allowed))}
    from utils.project_config import set_book_fields
    ok, msg = set_book_fields({"style_rule": value})
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "value": value,
                 "message": ("已改为不注入" if not value
                             else "已选择“%s”—— 下一章写作开始注入"
                                  % next(o["label"] for o in STYLE_RULE_OPTIONS
                                         if o["code"] == value))}


def handle_style_sample_consent_set(h, body):
    """记录/撤回样例稿声明（POST {agreed: true|false}）。撤回后保存重新被拦。"""
    try:
        agreed = bool((body or {}).get("agreed"))
        return 200, dict({"ok": True}, **_set_sample_consent(agreed))
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def _style_ref_target():
    """配置里的范文路径 → (绝对路径, 相对路径, 是否已配置)。"""
    from utils.project_config import get_project_config
    try:
        cfg = get_project_config() or {}
    except Exception:                                       # noqa: BLE001
        cfg = {}
    rel = str(((cfg.get("book") or {}).get("style_reference") or "")).strip()
    configured = bool(rel)
    rel = (rel or DEFAULT_STYLE_REF_PATH).replace("\\", "/")
    return api.ROOT / rel, rel, configured


def _style_ref_inside_root(p):
    """范文本必须落在工作区内 —— 否则 GUI 一次粘贴就能往任意路径写用户正文。"""
    try:
        api.Path(str(p)).resolve().relative_to(api.Path(str(api.ROOT)).resolve())
        return True
    except ValueError:
        return False


def _style_ref_limits():
    """注入上限（与 stage4 同源）：book.style_reference_max_chars，夹在 1000~40000。"""
    from utils.project_config import get_project_config
    try:
        cfg = get_project_config() or {}
        raw = ((cfg.get("book") or {}).get("style_reference_max_chars"))
        n = int(raw) if raw not in (None, "") else 6000
    except Exception:                                       # noqa: BLE001
        n = 6000
    return max(1000, min(STYLE_REF_MAX_CHARS, n))


def _style_ref_payload(text, rel, configured):
    from utils.style_analyzer import MIN_TEXT_LEN, prose_char_count
    limit = _style_ref_limits()
    prose = prose_char_count(text)
    return {
        "ok": True,
        "path": rel,
        "configured": configured,
        "text": text,
        "chars": len(text),
        "prose_chars": prose,
        "min_prose": MIN_TEXT_LEN,
        "limit": limit,
        "effective": prose >= MIN_TEXT_LEN,
        # 超出注入上限的部分 stage4 不会用（那里会 WARN），这里先让用户看见
        "truncated": max(0, len(text) - limit),
        "consent": _sample_consent(),
    }


def handle_config_style_reference(h):
    """读范文正文 + 字数口径（GUI“范文”编辑框的数据源）。"""
    p, rel, configured = _style_ref_target()
    text = ""
    if p.exists():
        try:
            from utils.file_io import read_text
            text = read_text(str(p))
        except Exception as e:                              # noqa: BLE001
            return 500, {"ok": False, "path": rel,
                         "error": "范文读取失败：%s: %s" % (type(e).__name__, str(e)[:160])}
    return 200, _style_ref_payload(text, rel, configured)


def handle_config_style_reference_set(h, body):
    """保存范文正文（GUI 粘贴 → 落盘 → 配置指向它）。

    写前把旧内容备份到 `data/state/style_reference_history/`（保留最近 20 份）——
    范文是作者原文，误覆盖的代价比多存一份大得多。
    """
    body = body or {}
    if "text" not in body:
        return 400, {"ok": False, "error": "缺少 text 字段（清空请传空字符串）"}
    text = str(body.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
    # 声明门（2026-10-29）：写正文前先确认“样例稿由你提供、片段会随任务发给模型服务商”。
    # 为什么拦在后端而不是只做前端弹层：外部 Agent 也调这个端点，声明不能只靠 GUI 自觉。
    # 清空（空串）不拦 —— 那是撤回，不该再被声明挡一次。
    if text.strip() and not _sample_consent().get("agreed"):
        return 403, {"ok": False, "need_consent": True,
                     "error": "保存范文前需先确认“样例稿使用声明”："
                              "正文会保存在本机工作区，其中的片段会随写作任务发给模型服务商。"}
    if len(text) > STYLE_REF_MAX_CHARS:
        return 400, {"ok": False,
                     "error": "范文过长（%d 字 > 上限 %d）—— 建议给 2~3 个场景切片，"
                              "多篇合并到一个文件" % (len(text), STYLE_REF_MAX_CHARS)}
    p, rel, configured = _style_ref_target()
    if not _style_ref_inside_root(p):
        return 400, {"ok": False,
                     "error": "范文路径必须落在工作区内：%s（当前配置 book.style_reference）"
                              % rel}
    try:
        from utils.file_io import read_text, write_text
        if p.exists():
            try:
                old = read_text(str(p))
            except Exception:                               # noqa: BLE001
                old = ""
            if old.strip() and old != text:
                hist = api.Path(STYLE_REF_HISTORY_DIR)
                hist.mkdir(parents=True, exist_ok=True)
                stamp = api.datetime.now().strftime("%Y%m%d_%H%M%S")
                (hist / ("reference_%s.md" % stamp)).write_text(old, encoding="utf-8")
                olds = sorted(hist.glob("reference_*.md"))
                for f in olds[:-STYLE_REF_HISTORY_KEEP]:
                    try:
                        f.unlink()
                    except OSError:
                        pass
        write_text(str(p), text if (not text or text.endswith("\n")) else text + "\n")
        if not configured:
            from utils.project_config import set_book_fields
            ok_c, msg_c = set_book_fields({"style_reference": rel})
            if not ok_c:
                return 400, {"ok": False,
                             "error": "范文已写入，但 project.yaml 指向失败：%s" % msg_c}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}

    payload = _style_ref_payload(text, rel, True)
    if not text.strip():
        payload["message"] = "已清空范文 —— 写作阶段不再注入范文"
    elif payload["prose_chars"] < payload["min_prose"]:
        payload["message"] = ("已保存，但有效正文只有 %d 字（少于 %d）—— **暂不生效**，"
                              "写作时会按“未提供范文”处理"
                              % (payload["prose_chars"], payload["min_prose"]))
    elif payload["truncated"]:
        payload["message"] = ("已保存：有效正文 %d 字；超出注入上限 %d 字，stage4 只注入前 %d 字"
                              % (payload["prose_chars"], payload["truncated"], payload["limit"]))
    else:
        payload["message"] = ("已保存：有效正文 %d 字（stage4 注入上限 %d 字）"
                              % (payload["prose_chars"], payload["limit"]))
    return 200, payload


# ---------------------------------------------------------------- Word 模板
# 件五（GUI 模板入口，A 方案）后端：清单来自磁盘 templates/，当前值来自
# project.yaml 的 book.word_template —— 两者**分开回**：当前值指向的模板可能
# 已被移走（配置陈旧），前端据此提示“文件不在”而不是让下拉悄悄显示空值。
DEFAULT_WORD_TEMPLATE = "templates/小说格式模板V1.3.dotx"   # 出厂默认（恢复默认的落点）
TPL_DIR = "templates"      # 模板目录名（配置值里存的是 `templates/<文件名>`）
TPL_PREFIX = TPL_DIR + "/"


def _word_templates_dir():
    """模板目录（**必须经 api.ROOT 解析**：--root 场景下 CWD 不等于项目根）。"""
    return api.ROOT / TPL_DIR


def _list_word_templates():
    """templates/ 下的 .dotx/.docx 清单（按名字排序，带大小与修改时间）。"""
    d = _word_templates_dir()
    files = []
    if d.is_dir():
        for p in sorted(d.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and p.suffix.lower() in (".dotx", ".docx"):
                st = p.stat()
                files.append({"name": p.name, "size": st.st_size,
                              "mtime": api.time.strftime("%Y-%m-%d %H:%M",
                                                         api.time.localtime(st.st_mtime)),
                              "path": TPL_PREFIX + p.name})
    return files


def handle_config_word_template(h):
    """Word 成品模板读取（config/project.yaml → book.word_template，件五 GUI 入口）。

    与 /config/style_notes 分开的理由相同：模板清单要扫盘（glob + stat），
    没必要为它重读整份 project.yaml，也没必要把整份 yaml 塞给前端。
    """
    try:
        from utils.project_config import get_project_config
        files = _list_word_templates()
        try:
            cfg = get_project_config() or {}
        except Exception:
            cfg = {}
        book = cfg.get("book") if isinstance(cfg.get("book"), dict) else {}
        cur = str(book.get("word_template") or "").strip()
        cur_name = cur.replace("\\", "/").rsplit("/", 1)[-1] if cur else ""
        return 200, {"ok": True, "current": cur, "default": DEFAULT_WORD_TEMPLATE,
                     "current_exists": bool(cur_name) and any(
                         f["name"] == cur_name for f in files),
                     "files": files, "dir": TPL_DIR,
                     "path": "config/project.yaml", "field": "book.word_template"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_word_template_set(h, body):
    """选择 / 恢复 Word 成品模板 —— 定向改写 book.word_template（写前自动备份）。

    只接受 **templates/ 目录里真实存在的文件名**：配置里的值会被 stage7 当路径
    用（`Path(tmpl).exists()`），放行不存在或带目录的值等于给成品链埋一个
    “静默退回硬编码样式”的坑；`../` 这类写法一律拒在门外。
    """
    body = body or {}
    name = str(body.get("template") or "").strip()
    if bool(body.get("reset")):
        name = DEFAULT_WORD_TEMPLATE
    norm = name.replace("\\", "/")
    if norm.startswith(TPL_PREFIX):
        norm = norm[len(TPL_PREFIX):]
    if not norm:
        return 400, {"ok": False, "error": "未选择模板（要回到出厂模板请点“恢复默认”）"}
    if "/" in norm or "\\" in norm or ".." in norm:
        return 400, {"ok": False, "error": "模板名非法（只允许 %s 下的文件名）: %s" % (TPL_PREFIX, name)}
    if not norm.lower().endswith((".dotx", ".docx")):
        return 400, {"ok": False, "error": "只支持 .dotx / .docx 模板: %s" % norm}
    if norm not in {f["name"] for f in _list_word_templates()}:
        return 400, {"ok": False,
                     "error": "%s 下没有这个文件: %s（请先放进模板目录再选）" % (TPL_PREFIX, norm)}
    value = TPL_PREFIX + norm
    try:
        from utils.project_config import set_book_fields
        ok, msg = set_book_fields({"word_template": value})
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    if not ok:
        return 400, {"ok": False, "error": msg}
    return 200, {"ok": True, "current": value,
                 "message": "%s（book.word_template，旧版已备份到 config/history/）" % msg,
                 "path": "config/project.yaml", "field": "book.word_template"}


def _gui_only(h, what):
    """设置类写入必须来自**可信的 GUI 本体**（判据唯一实现：`_guard.gui_only`）。

    实现搬到 `nf_api_domains/_guard.py`（2026-11-04）：新增的“服务商增删改”
    也要同一条判据，照抄一份就会各自漂移。这里保留这个名字，只为不动既有调用点。
    """
    from utils.gui_guard import gui_only
    return gui_only(h, what)


def handle_config_token_limit(h):
    """读 token 级熔断阈值（budget.token_limit）+ 出厂预设 + 合法区间。

    为什么要有这个读口：engine: hermes 下金额阈值恒不生效（订阅流量记账恒 0），
    止烧全靠这里的 token 上限；要**调阈值**就必须先看得见当前值与它的边界。
    """
    try:
        from utils import cost_tracker
        cfg = api.load_all()[0]
        raw = ((cfg.get("budget") or {}).get("token_limit")) or {}
        return 200, {
            "ok": True,
            "current": cost_tracker.normalize_token_limit(raw),
            "preset": dict(cost_tracker.TOKEN_LIMIT_PRESET),
            "bounds": {k: list(v) for k, v in cost_tracker.TOKEN_LIMIT_BOUNDS.items()},
            "path": "config/system.yaml",
            "field": "budget.token_limit",
            "note": ("engine: hermes 下金额阈值无效（订阅流量记账恒 0）；"
                     "止烧靠这里的 token 上限。改完对**下一次运行**生效，"
                     "正在跑的一轮不受影响。"),
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_token_limit_set(h, body):
    """写 token 级熔断阈值（合法性与出厂预设见 utils/cost_tracker）。

    校验在 cost_tracker（语义唯一来源），落盘在 config_io（定向改写 + 备份 + 回读）。
    越界值一律 400 拒绝：把 max_total_tokens 改成 0 等于**让闸门静默消失**，
    与本轮修掉的“hermes 下 ¥ 记账恒 0”是同一类坑。
    """
    guard = _gui_only(h, "止烧阈值（budget.token_limit）")
    if guard:
        return guard
    try:
        from utils import cost_tracker
        from utils.config_io import set_section_scalars
        ok, msg, values = cost_tracker.validate_token_limit(body or {})
        if not ok:
            return 400, {"ok": False, "error": msg}
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        w_ok, w_msg = set_section_scalars(("budget", "token_limit"), values, path=sys_yaml)
        if not w_ok:
            return 400, {"ok": False, "error": "写入失败：" + w_msg}
        return 200, {"ok": True, "current": values, "message": "已更新止烧阈值（" + w_msg + "）",
                     "note": "对下一次运行生效；正在跑的一轮不受影响。"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 单章字数区间（chapter.target_words）的出厂预设与合法边界。
#
# 为什么出厂下限是 5000 而不是 2000（2026-10-18 用户取证）：
# 旧默认 [2000, 5500] 让实测 2033 / 2439 字的章**直接过检**，用户判定
# “一章两三千字根本不合格”。用户既定口径是“单章 5000~15000、常态 ~8000”。
# 注意：总量仍应由**章数**控制，抬高这里只是把单章篇幅拉到合格线。
TARGET_WORDS_PRESET = [5000, 9000]
TARGET_WORDS_BOUNDS = {"low": (500, 20000), "high": (500, 30000)}


def _validate_target_words(body):
    """校验单章字数区间。返回 (ok, msg, values)。

    判据与 cost_tracker.validate_token_limit 同一取向：**越界一律拒绝并说清区间**
    —— 手工把配置改坏（0 / 负数 / 上限小于下限）会让写作阶段的字数校验静默失效。
    """
    body = body or {}
    if "low" not in body or "high" not in body:
        return False, "缺少 low / high（需同时提供单章字数下限与上限）", {}
    try:
        low = int(body["low"])
        high = int(body["high"])
    except (TypeError, ValueError):
        return False, "low / high 必须是整数", {}
    lo_b, hi_b = TARGET_WORDS_BOUNDS["low"], TARGET_WORDS_BOUNDS["high"]
    if not (lo_b[0] <= low <= lo_b[1]):
        return False, "下限 %s 越界（合法区间 %d~%d）" % (low, lo_b[0], lo_b[1]), {}
    if not (hi_b[0] <= high <= hi_b[1]):
        return False, "上限 %s 越界（合法区间 %d~%d）" % (high, hi_b[0], hi_b[1]), {}
    if low > high:
        return False, "下限 %d 不能大于上限 %d" % (low, high), {}
    return True, "", {"low": low, "high": high}


def handle_config_target_words(h):
    """读单章字数区间（chapter.target_words）+ 出厂预设 + 合法区间。

    为什么要有这个读口：用户此前只能手改 system.yaml 才能调篇幅，
    “绒花墨坊似乎没有显示设置每章字数要求的地方”—— 要能调就得先看得见。
    """
    try:
        cfg = api.load_all()[0]
        raw = ((cfg.get("chapter") or {}).get("target_words")) or []
        # 容错：坏配置（dict / 非法形态）不炸读口，如实回显现状
        cur = list(raw) if isinstance(raw, (list, tuple)) else ([] if isinstance(raw, dict)
                                                                else [raw])
        return 200, {
            "ok": True,
            "current": cur,
            "preset": list(TARGET_WORDS_PRESET),
            "bounds": {k: list(v) for k, v in TARGET_WORDS_BOUNDS.items()},
            "path": "config/system.yaml",
            "field": "chapter.target_words",
            "note": ("单章篇幅区间（提示词按它约束，校验按它判定）。"
                     "**全书总量请用章数控制**，不要靠抬高这里。"
                     "改完对下一次运行生效。"),
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_target_words_set(h, body):
    """写单章字数区间。合法性校验在本文件 `_validate_target_words`（判据唯一），
    落盘走 `utils.config_io.set_section_scalars`（定向改写 + 备份 + 回读）。

    不设 GUI-only：它是**创作决策**（篇幅口径），不是止烧/审批那类安全闸门。
    Agent 可以代为设置，但越界值一律 400 拒绝 —— 与 token_limit 同一取向。
    """
    ok, msg, values = _validate_target_words(body)
    if not ok:
        return 400, {"ok": False, "error": msg}
    # ⚠️ 必须落成**列表** `[low, high]`，不能落成 dict。
    # 消费方全是 list 语义：stage4 的 `low, high = target[:2]`、
    # quality_checklist 的 `tw[0]`、`build_chapter_task` 的 `*target_words` 解包、
    # verify_chapter 的 `is_chapter_complete(path, *target_words)`。
    # 落 dict 会全线 TypeError/KeyError —— 而且 key 顺序还可能反（low/high 由
    # validate 返回的 dict 顺序决定），静默写错值。
    tw_list = [values["low"], values["high"]]
    try:
        from utils.config_io import set_section_scalars
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        w_ok, w_msg = set_section_scalars(("chapter",), {"target_words": tw_list},
                                          path=sys_yaml)
        if not w_ok:
            return 400, {"ok": False, "error": "写入失败：" + w_msg}
        return 200, {"ok": True, "current": tw_list,
                     "message": "已更新单章字数区间（" + w_msg + "）",
                     "note": "对下一次运行生效；正在跑的一轮不受影响。"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


TITLE_FORMAT_DEFAULT = "第{n}章 {title}"


def handle_config_title_format(h):
    """读章标题格式（chapter.title_format）+ 出厂默认。

    用户 2026-10-26 第 12 条：“做个章节名格式自定义不难吧？”——
    格式本身是**创作决策**（有人喜欢 `第10章 潜七十二`，有人喜欢 `第十章 潜七十二`），
    所以给读口和写口，判据只有一条：必须含 `{n}`（不带章号的格式没法保证章号不丢）。
    """
    try:
        cfg = api.load_all()[0]
        raw = ((cfg.get("chapter") or {}).get("title_format") or "").strip()
        return 200, {
            "ok": True,
            "current": raw or TITLE_FORMAT_DEFAULT,
            "preset": TITLE_FORMAT_DEFAULT,
            "path": "config/system.yaml",
            "field": "chapter.title_format",
            "placeholders": {"{n}": "章号（必需）", "{title}": "章名（可省略）"},
            "note": ("只影响章标题行的渲染（流水线落盘后与 stage7 合并时归一化），"
                     "不改正文。留空回落默认。改完对下一次落盘/合并生效。"),
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_title_format_set(h, body):
    """写章标题格式。与 target_words 同口径：创作决策，Agent 可代设，越界 400。"""
    fmt = str((body or {}).get("format") or "").strip()
    if not fmt:
        return 400, {"ok": False, "error": "format 不能为空（留空请传 preset 默认值）"}
    if "{n}" not in fmt:
        return 400, {"ok": False,
                     "error": "format 必须含占位符 {n}（否则章号会丢失）。"
                              "例：第{n}章 {title}"}
    if "\n" in fmt or "\r" in fmt:
        return 400, {"ok": False, "error": "format 必须是单行"}
    if len(fmt) > 40:
        return 400, {"ok": False, "error": "format 过长（上限 40 字符）"}
    try:
        from utils.config_io import set_section_scalars
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        w_ok, w_msg = set_section_scalars(("chapter",), {"title_format": fmt},
                                          path=sys_yaml)
        if not w_ok:
            return 400, {"ok": False, "error": "写入失败：" + w_msg}
        return 200, {"ok": True, "current": fmt,
                     "message": "已更新章标题格式（" + w_msg + "）",
                     "note": "对下一次落盘/合并生效；已生成的 docx 需重跑阶段 7。"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_vault_scan(h):
    """读知识库扫描面：顶层目录 + 各自篇数 + 是否被排除。

    给 GUI“知识库扫描”用。**通用性是这里的第一要求**：本端点只报告"你的库里
    有哪些目录、各多少篇、哪些没进检索"，代码里不出现任何具体目录名 —— 用户的
    库怎么组织由用户说了算，界面上看得到、勾得掉，不需要改配置文件。
    """
    try:
        from obsidian_bridge import scan_vault_preview
        prev = scan_vault_preview()
        prev["ok"] = True
        return 200, prev
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 知识库路径读写
# 2026-10-31：设置页只有“知识库扫描”，**没有填库路径的入口** —— 用户第一次接库
# 只能手改 config/system.yaml + system.local.yaml（还得自己知道 local 覆盖优先）。
# 这条路对"只用过笔记软件"的人来说根本走不通。
#
# 写盘落点：`config/system.local.yaml` 的 `obsidian.vault_path`。
# 为什么不写 system.yaml：库路径是**私人本机信息**（会把用户名/盘符带进
# 公开仓库），而 local 已被 .gitignore 忽略，与本仓既有口径一致。

def _ensure_local_yaml():
    """确保 `config/system.local.yaml` 存在（不存在就写抬头骨架）。

    与 skip_dirs 端点同一口径：骨架里的注释在说"这里放的是私人配置"，
    否则用户打开文件只看到几个键、不知道哪些能随便写。
    """
    fp = api.ROOT / "config" / "system.local.yaml"
    if not fp.exists():
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(_VAULT_LOCAL_SKELETON, encoding="utf-8")
    return fp


def _yaml_cfg(path):
    """读一份配置 YAML。

    - **收绝对 Path**（调用处 `api.ROOT / ...`）：本仓已禁止把相对路径裸传给函数，
      `--root` 场景下会静默读另一个项目的配置；
    - 走 `config_io.load_config_yaml`（严格：重复键 / 语法错会报），这里吞掉并给空 dict ——
      这个端点只是“告诉界面配置从哪来”，不该因为一份坏 YAML 就 500。
      （坏配置本身会被 GET /config/project 与 nfctl status 暴露出来。）
    """
    try:
        from utils.config_io import load_config_yaml
        return load_config_yaml(path, default={}) or {}
    except Exception:                                        # noqa: BLE001
        return {}


def _count_md(base, limit=20000):
    """数 markdown 篇数（有上限，库很大时不把界面拖死）。返回 (n, truncated)。"""
    import os as _os
    n = 0
    for _dirpath, dirnames, filenames in _os.walk(str(base)):
        # 跳过体积大又必然没有笔记的目录（.git 里可能有几千个对象）
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        for f in filenames:
            if f.lower().endswith((".md", ".markdown")):
                n += 1
                if n >= limit:
                    return n, True
    return n, False


def _vault_info():
    """当前生效的库路径 + 它是从哪来的 + 盘上实际情况。"""
    from obsidian_bridge import get_vault_path, DEFAULT_VAULT_PATH
    p = get_vault_path()
    local_raw = ((_yaml_cfg(api.ROOT / "config" / "system.local.yaml")
                  .get("obsidian") or {}).get("vault_path") or "")
    sys_raw = ((_yaml_cfg(api.ROOT / "config" / "system.yaml")
                .get("obsidian") or {}).get("vault_path") or "")
    if str(local_raw).strip():
        source = "local"
    elif str(sys_raw).strip():
        source = "system"
    else:
        source = "none"
    info = {
        "ok": True,
        "path": "" if p is None else str(p).replace("\\", "/"),
        "configured": p is not None,
        "source": source,
        "default": str(DEFAULT_VAULT_PATH or "").replace("\\", "/"),
        "local_override": str(local_raw or "").strip(),
        "system_value": str(sys_raw or "").strip(),
        "field": "obsidian.vault_path（写在 config/system.local.yaml，不进 Git）",
    }
    if p is None:
        info.update({"exists": False, "is_dir": False, "md_count": 0,
                     "truncated": False, "looks_like_obsidian": False,
                     "note": "还没填库路径 —— 填了才能扫描与导入设定集。"})
        return info
    exists, is_dir = p.exists(), p.is_dir()
    md, trunc = (0, False)
    if is_dir:
        md, trunc = _count_md(p)
    info.update({
        "exists": exists, "is_dir": is_dir, "md_count": md, "truncated": trunc,
        # `.obsidian/` 是 Obsidian 的库标记 —— 只作提示，不作门槛
        # （有人用纯文件夹当库、拿别的编辑器写 markdown，那也是合法库）
        "looks_like_obsidian": bool(is_dir and (p / ".obsidian").is_dir()),
        "note": ("" if is_dir else "路径存在但不是目录；也可能库被移走了。"),
    })
    return info


def handle_config_vault_get(h):
    """`GET /config/vault` —— 库路径现状（含来源 / 篇数 / 是否像 Obsidian 库）。"""
    try:
        return 200, _vault_info()
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_vault_set(h, body):
    """`POST /config/vault {path}` —— 填/改/清空库路径。

    校验按"错了会静默跑偏"的顺序排：

    | 拒绝条件 | 为什么 |
    |---|---|
    | 非绝对路径 | 相对路径的基准是**进程 CWD**，换启动方式就指向别处 |
    | 不存在 / 不是目录 | 存下去之后每次扫描都空，用户会以为库是空的 |
    | 盘根（整块盘都当库） | 一次扫描几十万文件，界面卡死 |
    | 落在工作区内 | 会把 `data/`、`materials/` 当成笔记收进设定集 |
    """
    body = body or {}
    if body.get("clear"):
        try:
            from utils.config_io import set_section_scalars
            from utils.setting_schema import reset_obsidian_config_cache
            ok, msg = set_section_scalars(("obsidian",), {"vault_path": ""},
                                          path=_ensure_local_yaml())
            if not ok:
                return 400, {"ok": False, "error": "写入失败：" + msg}
            reset_obsidian_config_cache()
            return 200, {"ok": True, "message": "已清空库路径（可随时重填）"}
        except Exception as e:                               # noqa: BLE001
            return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}

    raw = str(body.get("path") or "").strip().strip('"').strip("'")
    if not raw:
        return 400, {"ok": False, "error": "path 不能为空（要清空请传 clear=true）"}
    from pathlib import Path as _P
    p = _P(raw).expanduser()
    if not p.is_absolute():
        return 400, {"ok": False,
                     "error": "请填绝对路径（例如 …/图书馆/我的知识库）——"
                              "相对路径会随启动位置漂移"}
    try:
        p = p.resolve()
    except OSError:
        pass
    if not p.exists():
        return 400, {"ok": False, "error": "路径不存在：%s" % p}
    if not p.is_dir():
        return 400, {"ok": False, "error": "这是一个文件，不是目录：%s" % p}
    anchor = _P(p.anchor) if p.anchor else None
    if anchor is not None and p == anchor:
        return 400, {"ok": False,
                     "error": "别把整个磁盘当知识库（一次扫描几十万文件，界面会卡死）"}
    try:
        root = api.ROOT.resolve()
        if p == root or p.is_relative_to(root):
            return 400, {"ok": False,
                         "error": "这个目录在项目工作区里面 —— 会把 data/、materials/ "
                                  "当成笔记收进设定集。请选项目外面的库目录。"}
        if root.is_relative_to(p):
            # 反向（库包含工作区）：允许，但要提醒 —— Obsidian 官方也不建议库套库
            pass
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from utils.config_io import set_section_scalars
        from utils.setting_schema import reset_obsidian_config_cache
        val = p.as_posix()          # 正斜杠：YAML 里可读，也不用担心反斜杠转义
        ok, msg = set_section_scalars(("obsidian",), {"vault_path": val},
                                      path=_ensure_local_yaml())
        if not ok:
            return 400, {"ok": False, "error": "写入失败：" + msg}
        # 写完立刻清缓存：否则 scan_vault 还会用旧路径（2026-10-31 修的那个坑）
        reset_obsidian_config_cache()
        info = _vault_info()
        warn = ""
        if not (p / ".obsidian").is_dir():
            warn = "这个目录没有 .obsidian（不像是 Obsidian 库）—— 如果是有 markdown 的普通文件夹，照样能用。"
        # 保存 = 设定当前库，同时登记进书签列表（用户换回来时不用重找路径）
        try:
            _doc = _load_vaults_doc()
            if not any(_same_path(x.get("path"), p.as_posix()) for x in _doc["items"]):
                _doc["items"].append({
                    "path": p.as_posix(), "name": p.name, "skip_dirs": _user_skip_dirs(),
                    "added_at": __import__("datetime").datetime.now()
                        .strftime("%Y-%m-%d %H:%M:%S"),
                })
                _save_vaults_doc(_doc)
        except Exception as e:                               # noqa: BLE001
            # 登记失败不影响"路径已保存"这件事本身，但**必须出声** ——
            # 静默吞掉会让"保存了却不在列表里"变成又一个界面骗人的 bug。
            print("[vault] 登记库书签失败（路径已保存）:", type(e).__name__, e)
        return 200, dict(info, ok=True, warning=warn,
                         message="已保存库路径；下次扫描立刻生效（无需重启）")
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 多库（A 模型，2026-10-31）
# 用户拍板选 A：**一次只有一个库生效**，界面上快速切换（Obsidian 式）。
#
# ## 为什么这个模型
#
# 三家成熟产品（Obsidian / Joplin / DEVONthink）的调研写在 roadmap 附录。选 A 的理由：
# 本项目导入设定集是**整体替换**语义。同时挂多个库、同名条目并存，会让"沈砚"出现两次，
# 一次误合并就覆盖掉用户手写的条目 —— 那是改用户的设定，不是功能。
# A 只把"换库"做顺，风险为零。
#
# ## 存哪、谁是权威
#
# - **当前库只有一个真源**：`config/system.local.yaml` 的 `obsidian.vault_path`
#   （既有语义不变，`scan_vault` / `get_vault_path` 一个字节都不用改）；
# - `data/state/vaults.json` 只存**书签**（已知库列表）与**每库的排除项**。
#   为什么不学 Obsidian 把配置放进库内（`.obsidian/`）：**我们从不写用户的库**
#   （既有红线），所以库级配置只能存在我们自己的工作区里。
#
# ## 切换时做什么
#
# ① 把当前库的排除项回写到它的书签；② 写 `vault_path` = 目标库；
# ③ 把目标库的排除项写回 `system.local.yaml`；④ 清配置缓存（否则下一次扫描读旧值）。
VAULTS_STATE_REL = "data/state/vaults.json"


def _vaults_file():
    return api.ROOT / VAULTS_STATE_REL


def _same_path(a, b):
    """路径相等判断（Windows 大小写不敏感 + 正反斜杠混用）。"""
    na = str(a or "").replace("\\", "/").rstrip("/").lower()
    nb = str(b or "").replace("\\", "/").rstrip("/").lower()
    return bool(na) and na == nb


def _load_vaults_doc():
    """读书签列表。**坏文件不抛**：库列表丢了只是不方便，不该让整个设置页 500。"""
    fp = _vaults_file()
    if not fp.is_file():
        return {"items": []}
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not isinstance(d.get("items"), list):
            return {"items": []}
        d["items"] = [x for x in d["items"] if isinstance(x, dict) and x.get("path")]
        return d
    except Exception:                                        # noqa: BLE001
        return {"items": []}


def _save_vaults_doc(doc):
    fp = _vaults_file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _current_vault_path():
    from obsidian_bridge import get_vault_path
    p = get_vault_path()
    return p.as_posix() if p else ""


def _user_skip_dirs():
    """当前生效的排除项里，**除掉默认四项**之后的部分（= 用户自己勾的那几个）。

    为什么要减默认项：`get_skip_dirs()` 是“默认 + local 追加”，把整份存进书签
    再套到另一个库上，等于把`.obsidian` 之类也当成"用户选择"带来带去。
    """
    from obsidian_bridge import get_skip_dirs
    # 默认项的正主在 setting_schema（obsidian_bridge 只是转手）——
    # 第一版从 obsidian_bridge 导 `_DEFAULT_SKIP_DIRS`，它没有这个名字，
    # 而调用点包在 try/except 里 → **登记静默失败**，列表永远是空的。
    from utils.setting_schema import _DEFAULT_SKIP_DIRS
    defaults = set(_DEFAULT_SKIP_DIRS)
    return [x for x in get_skip_dirs() if x not in defaults]


def _apply_vault(path, skip_dirs):
    """把“当前库”切到 path，并把 skip_dirs 写进 local（空列表 = 清掉用户项）。"""
    from utils.config_io import set_section_scalars
    from utils.setting_schema import reset_obsidian_config_cache
    ok, msg = set_section_scalars(("obsidian",),
                                  {"vault_path": path, "skip_dirs": list(skip_dirs or [])},
                                  path=_ensure_local_yaml())
    if not ok:
        return False, msg
    reset_obsidian_config_cache()
    return True, msg


def _vaults_public():
    """给界面看的列表：当前库 + 每项是否还在盘上。"""
    doc = _load_vaults_doc()
    cur = _current_vault_path()
    items = []
    for it in doc["items"]:
        from pathlib import Path as _P
        p = _P(it["path"])
        items.append({
            "path": it["path"],
            "name": it.get("name") or p.name,
            "exists": p.is_dir(),
            "looks_like_obsidian": (p / ".obsidian").is_dir() if p.is_dir() else False,
            "skip_dirs": it.get("skip_dirs") or [],
            "current": _same_path(it["path"], cur),
            "added_at": it.get("added_at"),
        })
    return {"ok": True, "current": cur, "items": items,
            "count": len(items),
            "model": "single",      # 一次只有一个库生效（A 模型）
            "note": ("同一时刻只有一个库生效；切换会保留每个库自己的排除项。"
                     "换库不影响任何笔记文件。")}


def handle_config_vaults_get(h):
    """`GET /config/vaults` —— 已知库列表 + 当前是哪个。"""
    try:
        return 200, _vaults_public()
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_vaults_add(h, body):
    """`POST /config/vaults/add {path, name?}` —— 只登记，不切换。"""
    body = body or {}
    raw = str(body.get("path") or "").strip()
    if not raw:
        return 400, {"ok": False, "error": "path 必填"}
    from pathlib import Path as _P
    p = _P(raw).expanduser()
    if not p.is_absolute():
        return 400, {"ok": False, "error": "请填绝对路径"}
    try:
        p = p.resolve()
    except OSError:
        pass
    if not p.is_dir():
        return 400, {"ok": False, "error": "目录不存在或不是目录：%s" % p}
    doc = _load_vaults_doc()
    for it in doc["items"]:
        if _same_path(it["path"], p.as_posix()):
            return 200, dict(_vaults_public(), message="这个库已经在列表里了")
    doc["items"].append({
        "path": p.as_posix(),
        "name": str(body.get("name") or "").strip() or p.name,
        "skip_dirs": [],
        "added_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_vaults_doc(doc)
    return 200, dict(_vaults_public(), message="已加入库列表（还没切换）")


def handle_config_vaults_switch(h, body):
    """`POST /config/vaults/switch {path}` —— 换库（A 模型的唯一动作）。

    顺序有意：**先回写当前库的排除项，再切** —— 反过来的话当前库的排除项会丢。
    """
    body = body or {}
    raw = str(body.get("path") or "").strip()
    if not raw:
        return 400, {"ok": False, "error": "path 必填"}
    doc = _load_vaults_doc()
    item = None
    for it in doc["items"]:
        if _same_path(it["path"], raw):
            item = it
            break
    if item is None:
        return 400, {"ok": False,
                     "error": "这个库不在列表里（先“加入列表”，或直接用路径输入框保存）"}
    from pathlib import Path as _P
    if not _P(item["path"]).is_dir():
        return 400, {"ok": False,
                     "error": "库目录不见了：%s（可能被移动或删除）" % item["path"]}
    cur = _current_vault_path()
    if _same_path(cur, item["path"]):
        return 200, dict(_vaults_public(), message="已经是这个库了")
    # ① 当前库的排除项回写书签
    if cur:
        cur_item = None
        for it in doc["items"]:
            if _same_path(it["path"], cur):
                cur_item = it
                break
        if cur_item is not None:
            cur_item["skip_dirs"] = _user_skip_dirs()
    # ②③ 切换 + 套用目标库的排除项
    ok, msg = _apply_vault(item["path"], item.get("skip_dirs") or [])
    if not ok:
        return 400, {"ok": False, "error": "切换失败：" + msg}
    _save_vaults_doc(doc)
    return 200, dict(_vaults_public(),
                     message="已切到“%s”" % (item.get("name") or item["path"]),
                     scan=None)


def handle_config_vaults_remove(h, body):
    """`POST /config/vaults/remove {path}` —— 从列表移出。

    **只删书签，不动笔记文件**；当前库不许直接移出（先切走），
    否则界面上会出现"当前库不在列表里"的怪状态。
    """
    body = body or {}
    raw = str(body.get("path") or "").strip()
    if not raw:
        return 400, {"ok": False, "error": "path 必填"}
    doc = _load_vaults_doc()
    cur = _current_vault_path()
    if _same_path(raw, cur):
        return 400, {"ok": False,
                     "error": "这是当前正在用的库 —— 先切到别的库，再把它移出列表"}
    before = len(doc["items"])
    doc["items"] = [it for it in doc["items"] if not _same_path(it["path"], raw)]
    if len(doc["items"]) == before:
        return 400, {"ok": False, "error": "列表里没有这个库"}
    _save_vaults_doc(doc)
    return 200, dict(_vaults_public(),
                     message="已从列表移出（笔记文件一个都没动）")


# system.local.yaml 可能从来没被创建过（用户没改过本地覆盖）——写入前先补骨架。
# 抬头注释要说明"这里的目录名是私人的"，否则用户会以为可以随便放进 system.yaml。
_VAULT_LOCAL_SKELETON = (
    "# 本地配置覆盖（不进 Git）\n"
    "# 模型选择、API Key 路径、本地沙盒目录等个人配置放这里。\n"
    "# 你个人知识库的目录名也写在这里 —— 它们是你的私人结构，不该进公开仓库。\n"
)


def _validate_skip_dirs(body):
    """校验排除目录入参。返回 (ok, msg, names)。

    只收**目录名**（不含路径分隔符 / `..`）：判据是“拿名字去比对路径的每一段”，
    收路径既无意义又给路径穿越留口子。长度上限防把整段路径粘进来。
    """
    body = body or {}
    raw = body.get("exclude")
    if not isinstance(raw, list):
        return False, "exclude 必须是目录名列表（数组）", None
    out = []
    for item in raw:
        s = str(item).strip()
        if not s:
            continue
        if any(ch in s for ch in ("/", "\\", "..")):
            return False, ("只接受目录名、不接受路径（收到 %r）——"
                           "请从扫描面里选目录" % s), None
        if len(s) > 100:
            return False, "目录名过长（%d 字符 > 100），可能是把整段路径粘进来了" % len(s), None
        out.append(s)
    return True, "", sorted(set(out))


def handle_config_vault_skip_dirs(h, body):
    """写排除目录（`obsidian.skip_dirs`）。

    落 **system.local.yaml** 而不是 system.yaml：里面是用户的私有库目录名，
    跟着 system.yaml 进 Git 等于把私人库结构推上公开仓（本项目已有红线）。
    备份落 config/history/，该目录已被 .gitignore 的 `history/` 规则覆盖。

    不设 GUI-only：它决定"哪些内容算正典"，与 target_words / style_reference
    同属**创作决策**，不是止烧/审批那类安全闸门 —— Agent 可以代配，
    但代配同样只写 local，永不触碰进 Git 的配置。
    """
    ok, msg, names = _validate_skip_dirs(body)
    if not ok:
        return 400, {"ok": False, "error": msg}
    try:
        from utils.config_io import set_section_scalar
        from utils.setting_schema import reset_obsidian_config_cache
        local_yaml = api.ROOT / "config" / "system.local.yaml"
        if not local_yaml.exists():
            local_yaml.parent.mkdir(parents=True, exist_ok=True)
            local_yaml.write_text(_VAULT_LOCAL_SKELETON, encoding="utf-8")
        w_ok, w_msg = set_section_scalar("obsidian", "skip_dirs", names,
                                         path=local_yaml)
        if not w_ok:
            return 400, {"ok": False, "error": "写入失败：" + w_msg}
        # config_io 自身不缓存（每次读文件）；要清的是 setting_schema 的配置缓存，
        # 否则下一次 get_skip_dirs() 读到的还是旧值（用户改完界面上没变化）
        reset_obsidian_config_cache()
        from obsidian_bridge import scan_vault_preview
        prev = scan_vault_preview()
        return 200, {"ok": True, "exclude": names,
                     "message": "已更新扫描排除项（%s）" % w_msg,
                     "scan": prev,
                     "note": "对下一次检索生效；正在跑的一轮不受影响。"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ENHANCED_MODE_DEFAULT = False


def _validate_enhanced_mode(body):
    """校验加强模式开关入参。返回 (ok, msg, value)。

    只收严格布尔（true/false）：`"true"` / `1` 这类松散形态一律拒绝 ——
    开关类字段静默把字符串当真值，会让用户以为关了其实开着（烧钱翻倍）。
    """
    body = body or {}
    if "enabled" not in body:
        return False, "缺少 enabled（布尔：true 开 / false 关）", None
    v = body["enabled"]
    if not isinstance(v, bool):
        return False, "enabled 必须是布尔值 true/false（收到 %s）" % type(v).__name__, None
    return True, "", v


def handle_config_enhanced_mode(h):
    """读加强模式开关（chapter.enhanced_mode）。

    加强模式 = 单章拆段落群生成（utils/enhanced_chapter.py）：逐群生成、
    每群落盘、拼装成章后走同一套校验。默认关 —— 施工单口径“只手动开启”。
    """
    try:
        cfg = api.load_all()[0]
        cur = bool((cfg.get("chapter") or {}).get("enhanced_mode",
                                                  ENHANCED_MODE_DEFAULT))
        gw = int((cfg.get("chapter") or {}).get("enhanced_group_words", 1500))
        return 200, {
            "ok": True,
            "enabled": cur,
            "group_words": gw,
            "path": "config/system.yaml",
            "field": "chapter.enhanced_mode",
            "note": ("加强模式：单章拆段落群生成（每群约 %d 字），"
                     "段群级断点续跑。关闭时整章单发（默认）。"
                     "对下一次运行生效。" % gw),
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_enhanced_mode_set(h, body):
    """写加强模式开关。不设 GUI-only（与 target_words 同口径：创作决策，
    Agent 可代设）；严格布尔校验，非法值 400 拒绝。
    """
    ok, msg, value = _validate_enhanced_mode(body)
    if not ok:
        return 400, {"ok": False, "error": msg}
    try:
        from utils.config_io import set_section_scalars
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        w_ok, w_msg = set_section_scalars(("chapter",), {"enhanced_mode": value},
                                          path=sys_yaml)
        if not w_ok:
            return 400, {"ok": False, "error": "写入失败：" + w_msg}
        return 200, {"ok": True, "enabled": value,
                     "message": "加强模式已" + ("开启" if value else "关闭") + "（" + w_msg + "）",
                     "note": "对下一次运行生效；正在跑的一轮不受影响。"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_form(h):
    """文体（config/project.yaml 的 book.form）读取 + 可选项。

    为什么必须给 GUI 一个入口（2026-10-20）：文体决定**哪些判据适用** ——
    散文项目不该被要求"情节连贯/人物关系"，也不该要求"小目标/悬念钩子"。
    但此前只能在 yaml 里手写 `form: essay` —— 对不写代码的作者等于不存在。
    """
    try:
        from utils import writing_form as wf
        from utils.project_config import get_project_config
        try:
            cfg = get_project_config() or {}
        except Exception:                                   # noqa: BLE001
            cfg = {}
        raw = str(((cfg.get("book") or {}).get("form") or "")).strip()
        app = wf.applicability({"book": {"form": raw}}) if raw else wf.applicability({})
        return 200, {
            "ok": True,
            "form": raw or None,
            "effective": app["code"],
            "default": wf.DEFAULT_FORM,
            "path": "config/project.yaml",
            "field": "book.form",
            "options": [{"code": c,
                         "label": wf.form_spec(c)["label"],
                         "desc": wf.form_spec(c)["desc"],
                         # 写法指导（stage4 文风块）是否已随文体切换
                         # 用**文件存在**判断（回落叙事也会返回非空正文，见 style_files_exist）
                         "style_wired": wf.style_files_exist(c),
                         } for c in wf.FORMS],
            "warning": app.get("warning") or None,
            "describe": wf.describe(app),
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_form_set(h, body):
    """写入文体 → 定向改写 project.yaml 的 book.form（保留注释，写前备份）。"""
    val = body.get("form")
    if val is None:
        return 400, {"ok": False, "error": "缺少 form 字段"}
    if not isinstance(val, str):
        return 400, {"ok": False, "error": "form 必须为字符串"}
    try:
        from utils.project_config import set_book_fields
        ok, msg = set_book_fields({"form": val.strip()})
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}
    if not ok:
        return 400, {"ok": False, "error": msg}
    # 回读校验：写进去的东西必须能读回来（写坏就报错，不留半成品）
    # ⚠️ 路径必须走 PC.PROJECT_YAML（ROOT 绝对拼接）：裸相对路径在打包态
    # 按 CWD 解析，而 CWD 不是 ROOT（test_root_path_discipline 抓过这一条）。
    try:
        from utils import project_config as _pc
        got = str(((_pc.load_project_yaml(_pc.PROJECT_YAML) or {})
                   .get("book") or {}).get("form") or "").strip()
    except Exception:                                       # noqa: BLE001
        got = ""
    if got != val.strip():
        return 500, {"ok": False, "error": "写入后回读不一致（配置可能已损坏，见 config/history/ 备份）"}
    return 200, {"ok": True, "form": got, "message": "文体已切换为 " + got}


def handle_config_agent_mode(h):
    """读取当前 Agent 模式开关（config/system.yaml 的 gates.agent_mode）。"""
    try:
        cfg = api.load_all()[0]
        return 200, {"ok": True, "agent_mode": bool(cfg.get("gates", {}).get("agent_mode", False)),
                     "path": "config/system.yaml", "field": "gates.agent_mode"}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_config_agent_mode_set(h, body):
    """设置 Agent 模式开关（config/system.yaml 的 gates.agent_mode）。

    安全守卫：Agent 模式只能从 GUI 手动开启，外部 Agent 不得调用此端点。
    检测方式：请求必须携带 X-Mofang-Source: gui 头（由 Electron preload 注入）。

    2026-10-03：落盘改走 `utils.config_io.set_section_scalar`（原先这里自带一段
    正则替换 + 手工备份 —— 同一件事第二份实现，且它不会回读校验）。
    """
    guard = _gui_only(h, "Agent 模式")
    if guard:
        return guard
    try:
        from utils.config_io import set_section_scalar
        enable = bool(body.get("agent_mode", False))
        sys_yaml = api.ROOT / "config" / "system.yaml"
        if not sys_yaml.exists():
            return 400, {"ok": False, "error": "config/system.yaml 不存在"}
        ok, msg = set_section_scalar("gates", "agent_mode", enable, path=sys_yaml)
        if not ok:
            return 400, {"ok": False, "error": "写入失败：" + msg}
        return 200, {"ok": True, "agent_mode": enable,
                     "message": "Agent 模式已%s（%s）" % ("开启" if enable else "关闭", msg)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# Agent 模式下各操作族的**人话说明**。键 = 端点路径前缀；判据唯一来源仍是
# utils.agent_guard.is_forbidden_in_agent_mode —— 这里只补“为什么”，不改判据。
# ⚠️ 判据绝不能在这里复述一份：两份名单必然漂移（前端锁了后端没锁 / 反过来），
#    而漂移的表现正是用户 2026-10-24 抱怨的“客户端里分不清哪些走 Agent 哪些走内部”。
_AGENT_PATH_NOTES = {
    "/approve": "审批放行 —— 判定权归用户，Agent 不得代批",
    "/reject": "打回重跑 —— 同审批族，会清下游产物",
    "/project/create": "新建项目 —— 会归档当前项目并清空工作区",
    "/project/archive": "归档当前项目 —— 搬空工作区到书库",
    "/project/restore": "恢复归档书 —— 覆盖当前工作区",
    "/project/init": "首启初始化 —— 写入工作区骨架",
    "/project/archive/delete": "删除归档 —— 抹掉成书数据（进回收站）",
    "/config/agent_mode": "切换 Agent 模式本身 —— Agent 不得开关自己的模式",
    "/config/token_limit": "止烧阈值 —— Agent 不得抬高烧钱上限",
    "/factory/clean": "清理出厂样例 —— 移动用户素材文件",
    "/factory/preset/apply": "铺设出厂预设 —— 快照+归档+铺素材，同新建项目",
    "/factory/project/reset": "出厂项目还原 —— 清书状态+重铺素材",
    "/stage/skip": "跳过阶段 —— 会把 approved 真写进 progress.json",
    "/import/docx": "导入整本 Word 稿 —— 批量覆盖章节文件",
    "/outline/segments/set": "分段审批标记 —— 勾一段等于替用户读一段",
    "/outline/discussion/review": "讨论提案审批 —— 会写回章大纲",
    "/plan/select": "规划拍板四态 —— AI 可提案，拍板归用户",
    "/plan/write": "规划拍板四态 —— 同上",
    "/plan/drop": "规划拍板四态 —— 同上",
    "/plan/revive": "规划拍板四态 —— 同上",
}


def _agent_path_note(path):
    """给某个端点路径配一句人话（最长前缀优先；无匹配返回 None）。"""
    best, note = 0, None
    for prefix, txt in _AGENT_PATH_NOTES.items():
        if str(path).startswith(prefix) and len(prefix) > best:
            best, note = len(prefix), txt
    return note


def handle_agent_permissions(h):
    """Agent 模式权限清单（只读）：`GET /agent/permissions`。

    为什么要有：Agent 模式下 GUI 锁哪些按钮，此前是**前端各处硬编码**，而判据的
    唯一真源是 utils.agent_guard.FORBIDDEN_IN_AGENT_MODE —— 两边没有共享通道。
    用户 2026-10-24 的原话：“在客户端内根本分不清哪些会走 agent 哪些直接走内部
    配置 API，很混乱”。这里把权威名单 + 人话说明一次性交给前端渲染，前端不再
    自己维护第二份名单（两份名单必然漂移，漂移 = 这次抱怨的根因）。

    返回三类，让界面能画出明确分界：
      - forbidden：Agent 模式下**外部 Agent 调用会被 403** 的端点（判定权归用户）
      - internal：GUI 内部直连 API 的常规功能（不受 Agent 模式影响）
      - run_locked：Agent 模式下 GUI **运行按钮**锁定（防双跑，非权限拦截）
    """
    try:
        from utils import agent_guard
        forbidden = []
        for path in sorted(agent_guard.FORBIDDEN_IN_AGENT_MODE):
            forbidden.append({"path": path, "why": _agent_path_note(path)
                              or "用户拍板类操作"})
        return 200, {
            "ok": True,
            "agent_mode_enabled": api.load_all()[0].get("gates", {}).get(
                "agent_mode", False),
            "forbidden": forbidden,
            "internal": [
                {"path": "/stage/{n}/run", "why": "跑阶段 —— 生成类，Agent 可驱动"},
                {"path": "/outline/save", "why": "保存大纲 —— 落草稿/沙盒，可回退"},
                {"path": "/refine/*", "why": "AI 精修 —— 改前自动备份 + 可撤销"},
                {"path": "/setting/current", "why": "读设定集 —— 只读"},
                {"path": "/costs/*", "why": "成本查询 —— 只读"},
            ],
            "run_locked": [
                {"what": "全自动 / 流式全自动 / Word 成品 / 快速运行",
                 "why": "运行入口统一锁：Agent 模式下运行由外部 Agent 驱动，"
                        "GUI 再点一次会双跑（两条流水线各烧一倍 token）"},
                {"what": "审批 / 打回 / 停止 / 跳过",
                 "why": "不受影响 —— 这几件事本来就只归桌面端"},
            ],
            "note": "判据唯一来源 utils.agent_guard.FORBIDDEN_IN_AGENT_MODE；"
                    "本端点只做展示，不改判据。",
        }
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 归档查看 / 删除
# 为什么在“项目”域：/project/list 已在此，归档的“看”与“删”是它的自然延伸 ——
# 此前 GUI 每行只有“恢复”，既看不了内容也删不掉，归档变成死胡同。
#
# 路径纪律：一律 api.ROOT 拼接（AGENTS.md“路径 IO 必须经 ROOT”）。
# 不用 switch_book 的模块常量：那套常量按**脚本位置**解析，--root 场景下
# 会静默指向另一个项目 —— 与 nf_api 里 Path("data/...") 是同一类坑。

_MAX_ARCHIVE_FILE = 300_000        # 单文件预览上限（300 KB，超了只报体积不给内容）
_MAX_ARCHIVE_ENTRIES = 800         # 目录树条目上限（防归档巨大时把响应撑爆）


def _query1(h, key):
    """取查询串单值（?name=xxx）。"""
    q = h._query()
    vals = q.get(key) or []
    return str(vals[0]).strip() if vals else ""


def _books_root():
    return (api.ROOT / "data" / "books").resolve()


def _archive_dir(name):
    """书名 → 归档目录。返回 (path|None, error|None)。

    安全要点：
    · sanitize 只替换 `\\/:*?"<>|`，**不拦 `..`** —— 必须显式拒绝；
    · 解析后再用 is_relative_to 卡一次，双保险（任何漏网字符都出不了根）。
    """
    import switch_book as sb
    clean = sb.sanitize(name)
    if not name or not clean or ".." in clean or clean in (".", "未命名"):
        return None, "非法书名: %r" % (name,)
    base = _books_root()
    d = (base / clean).resolve()
    if not d.is_relative_to(base):
        return None, "路径越界: %r" % (name,)
    if not d.is_dir():
        return None, "未找到归档: %s（用 GET /project/list 查看）" % clean
    return d, None


def handle_archive_tree(h):
    """归档内容清单：`GET /project/archive/tree?name=X`。

    只读。给“归档项目查看”用 —— 想删之前先看清楚里面有什么，
    也是“恢复出来才能看”这个死胡同的替代出口。
    """
    name = _query1(h, "name")
    if not name:
        return 400, {"ok": False, "error": "name 必填"}
    d, err = _archive_dir(name)
    if err:
        return 404, {"ok": False, "error": err}
    entries, total, truncated = [], 0, False
    try:
        for p in sorted(d.rglob("*")):
            if p.is_dir():
                continue
            if len(entries) >= _MAX_ARCHIVE_ENTRIES:
                truncated = True
                break
            try:
                size = p.stat().st_size
            except OSError:
                size = -1
            entries.append({"path": p.relative_to(d).as_posix(), "size": size})
            total += max(size, 0)
    except OSError as e:
        return 500, {"ok": False, "error": "读取归档失败: %s" % e}
    return 200, {"ok": True, "name": name, "root": str(d),
                 "entries": entries, "count": len(entries),
                 "total_size": total, "truncated": truncated}


def handle_archive_file(h):
    """归档内单文件预览：`GET /project/archive/file?name=X&path=rel`。

    只读 + 三道闸：路径不许越出归档根、超大文件只报体积、二进制不回内容
    （下载 .docx 之类对“看一眼”没有意义，还会把 JSON 响应变成乱码）。
    """
    name, rel = _query1(h, "name"), _query1(h, "path")
    if not name or not rel:
        return 400, {"ok": False, "error": "name 与 path 均必填"}
    d, err = _archive_dir(name)
    if err:
        return 404, {"ok": False, "error": err}
    target = (d / rel).resolve()
    if not target.is_relative_to(d):
        return 400, {"ok": False, "error": "路径越界: %r" % rel}
    if not target.is_file():
        return 404, {"ok": False, "error": "归档内无此文件: %s" % rel}
    try:
        size = target.stat().st_size
    except OSError as e:
        return 500, {"ok": False, "error": str(e)}
    if size > _MAX_ARCHIVE_FILE:
        return 413, {"ok": False, "error": "文件过大（%d B > %d B），请用“恢复”后查看"
                                            % (size, _MAX_ARCHIVE_FILE)}
    data = target.read_bytes()
    if b"\x00" in data[:4096]:
        return 415, {"ok": False, "error": "二进制文件，不支持在线预览: %s" % rel}
    return 200, {"ok": True, "name": name, "path": rel, "size": size,
                 "content": data.decode("utf-8", errors="replace")}


def handle_archive_delete(h, body):
    """删除归档：`POST /project/archive/delete` {name, confirm, purge?}。

    破坏性操作的四道闸：
    1. **GUI 来源**（`_gui_only`）：与止烧阈值同一档，与 agent_mode 无关也拦 ——
       外部 Agent 不该有能力抹掉用户的成书数据；
    2. **confirm 必须逐字等于书名**：防手滑 / 防脚本无脑重放；
    3. **默认不真删**：移入 `data/books/_trash/<书名>__<时间戳>`（列表不再显示），
       `purge=true` 才真删 —— 用户对数据丢失极度敏感，回收站是默认形态；
    4. **返回落点路径**：真删时明确告知不可恢复，回收站时告知还能捞回来。
    """
    guard = _gui_only(h, "归档删除")
    if guard:
        return guard
    body = body or {}
    name = str(body.get("name") or "").strip()
    confirm = str(body.get("confirm") or "").strip()
    purge = bool(body.get("purge"))
    if not name:
        return 400, {"ok": False, "error": "name 必填"}
    if confirm != name:
        return 400, {"ok": False, "error": "请输入归档名“%s”以确认删除" % name}
    d, err = _archive_dir(name)
    if err:
        return 404, {"ok": False, "error": err}
    import shutil
    try:
        if purge:
            shutil.rmtree(str(d))
            return 200, {"ok": True, "mode": "purged", "name": name,
                         "message": "已彻底删除“%s”（%s）—— 不可恢复" % (name, d)}
        ts = api.time.strftime("%Y%m%d_%H%M%S", api.time.localtime())
        trash = _books_root() / "_trash"
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / ("%s__%s" % (d.name, ts))
        shutil.move(str(d), str(target))
        return 200, {"ok": True, "mode": "trashed", "name": name,
                     "trash_path": str(target),
                     "message": "已移出归档列表“%s”→ 回收站 %s（要彻底清除请手工删除该目录）"
                                % (name, target)}
    except OSError as e:
        return 500, {"ok": False, "error": "删除失败: %s" % e}


# ---------------------------------------------------------------- 写作预设（2026-10-21）
def handle_writing_presets_get(h):
    """`GET /config/writing_presets` —— 预设清单 + 当前生效值 + 切换历史。只读。"""
    try:
        from utils import writing_presets as wp
        return 200, {"ok": True,
                     "presets": wp.list_presets(api.ROOT),
                     "current": wp.current_values(api.ROOT),
                     "history": wp.history(api.ROOT),
                     "keys": list(wp.PRESET_KEYS)}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_writing_presets_save(h, body):
    """`POST /config/writing_presets/save {id, data}` —— 保存/覆盖一套预设。

    创作决策（存一套写作要求），非安全闸门，Agent 可代设；未知键/非法值一律 400。
    """
    try:
        from utils import writing_presets as wp
        body = body or {}
        ok, msg = wp.save_preset(body.get("id"), body.get("data"), root=api.ROOT)
        return (200 if ok else 400), ({"ok": True, "message": msg} if ok
                                      else {"ok": False, "error": msg})
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_writing_presets_apply(h, body):
    """`POST /config/writing_presets/apply {id}` —— 切换到某预设。

    切换 = 校验 → 留 history → 写回 project.yaml（定向改写 + 备份 + 回读校验）。
    与 target_words/style_reference 同口径：创作决策，Agent 可代设。
    """
    try:
        from utils import writing_presets as wp
        ok, msg = wp.apply_preset((body or {}).get("id"), root=api.ROOT)
        return (200 if ok else 400), ({"ok": True, "message": msg} if ok
                                      else {"ok": False, "error": msg})
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------- 出厂预设（2026-10-21）
def handle_factory_presets_list(h):
    """`GET /factory/presets` —— 列出出厂预设（只读）。首跑向导的“选预设”用。"""
    try:
        import preset_apply as pa
        pdir = pa.find_presets_dir(api.ROOT)
        if not pdir:
            return 200, {"ok": True, "presets": [],
                         "note": "找不到 examples/presets/（打包态应由 payload 注入）"}
        rows = [r for r in pa.list_presets(pdir) if not r.get("hidden") and not r.get("error")]
        return 200, {"ok": True, "presets": rows}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_factory_preset_apply(h, body):
    """`POST /factory/preset/apply {id, name?, archive_current?}` —— 铺设出厂预设。

    ⚠️ **破坏性**（与 /project/create 同族）：会快照 → 归档当前项目 → 铺素材
    + 写 project.yaml。故：①列入 FORBIDDEN_IN_AGENT_MODE（Agent 不得自铺，
    否则一条命令就能搬空用户工作区）；②端点自身再要 GUI 来源头（双因子）。
    """
    # 双因子复用 _gui_only（不自调 is_trusted_gui）：判据只许有一份，
    # 否则与 do_POST 的禁用名单各自漂移就重新裂出后门。
    refused = _gui_only(h, "铺设出厂预设")
    if refused:
        return refused
    try:
        import preset_apply as pa
        pid = str((body or {}).get("id") or "").strip()
        if not pid:
            return 400, {"ok": False, "error": "缺少 id"}
        import io as _io
        import contextlib as _ctx
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            rc = pa.apply(pid, api.ROOT,
                           new_name=(body or {}).get("name") or None,
                           replace=bool((body or {}).get("archive_current", True)))
        tail = "\n".join(buf.getvalue().splitlines()[-12:])
        if rc != 0:
            return 400, {"ok": False, "error": "铺设失败（退出码 %s）" % rc, "log": tail}
        return 200, {"ok": True, "message": "预设“%s”已铺设" % pid, "log": tail}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# ---------------------------------------------------------------------------
# 作品级写作约束（M12，2026-11-04）：每本书一份，随书走；空 = 不注入
# ---------------------------------------------------------------------------

def handle_writing_rules(h):
    """GET /writing_rules → 这本书的作品写作约束（零 token）。

    `exists=false` 是**正常状态**（作者还没写），不是故障 —— 界面必须明说“还没写”，
    不能显示成“已配置但内容为空”（空配置会被读成“检查过了”）。
    """
    try:
        from utils import writing_rules as wr
        info = wr.load(str(api.ROOT))
        info["ok"] = True
        info["max_chars"] = wr.MAX_CHARS
        info["note"] = "作品级写作约束：随书走，写进 stage4 / stage6 的写作任务；空则不注入"
        return 200, info
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_writing_rules_save(h, body):
    """POST /writing_rules {text} → 保存（备份 + 原子写 + 回读校验）。

    属**创作决策**（同 `chapter.target_words`）：Agent 可代设，不进禁止名单；
    空文本 = 清空（删文件，而不是留一个空文件让它被误读成“已配置”）。
    """
    body = body if isinstance(body, dict) else {}
    if "text" not in body:
        return 400, {"ok": False, "error": "text 必填（空字符串 = 清空约束）"}
    try:
        from utils import writing_rules as wr
        ok, msg, info = wr.save(body.get("text"), str(api.ROOT))
        return (200 if ok else 400), {"ok": ok, "message": msg, **info,
                                      "max_chars": wr.MAX_CHARS}
    except Exception as e:                                    # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/health", handle_health),
    ("GET", "/state", handle_state),
    ("GET", "/whoami", handle_whoami),
    ("GET", "/project/list", handle_project_list),
    ("GET", "/project/status", handle_project_status),
    ("GET", "/project/archive/tree", handle_archive_tree),
    ("GET", "/project/archive/file", handle_archive_file),
    ("POST", "/project/archive/delete", handle_archive_delete),
    ("GET", "/config/project", handle_config_project),
    ("GET", "/config/style_notes", handle_config_style_notes),
    ("GET", "/config/style_reference", handle_config_style_reference),
    ("POST", "/config/style_reference", handle_config_style_reference_set),
    ("GET", "/config/word_template", handle_config_word_template),
    ("POST", "/config/word_template", handle_config_word_template_set),
    ("GET", "/config/agent_mode", handle_config_agent_mode),
    ("POST", "/config/agent_mode", handle_config_agent_mode_set),
    ("GET", "/agent/permissions", handle_agent_permissions),
    ("GET", "/config/token_limit", handle_config_token_limit),
    ("POST", "/config/token_limit", handle_config_token_limit_set),
    ("GET", "/config/target_words", handle_config_target_words),
    ("POST", "/config/target_words", handle_config_target_words_set),
    # 章标题格式（2026-10-26，用户第 12 条）：创作决策，Agent 可代设
    ("GET", "/config/title_format", handle_config_title_format),
    ("POST", "/config/title_format", handle_config_title_format_set),
    # 知识库扫描面（2026-10-24）：目录名从用户库里读、界面上勾选排除 ——
    # 代码不猜任何库结构（曾因把某个真实库的目录名当通例写进代码而返工）
    ("GET", "/config/vault_scan", handle_config_vault_scan),
    ("POST", "/config/vault_skip_dirs", handle_config_vault_skip_dirs),
    ("GET", "/config/vault", handle_config_vault_get),
    ("POST", "/config/vault", handle_config_vault_set),
    ("GET", "/config/vaults", handle_config_vaults_get),
    ("POST", "/config/vaults/add", handle_config_vaults_add),
    ("POST", "/config/vaults/switch", handle_config_vaults_switch),
    ("POST", "/config/vaults/remove", handle_config_vaults_remove),
    # 加强模式（第一波防御 · 任务三，2026-10-22）：单章拆段落群生成开关
    ("GET", "/config/enhanced_mode", handle_config_enhanced_mode),
    ("POST", "/config/enhanced_mode", handle_config_enhanced_mode_set),
    # 写作预设（2026-10-21，v0.10.3）：多套写作要求存档与切换
    ("GET", "/config/writing_presets", handle_writing_presets_get),
    ("POST", "/config/writing_presets/save", handle_writing_presets_save),
    ("POST", "/config/writing_presets/apply", handle_writing_presets_apply),
    # 作品级写作约束（M12，2026-11-04）：每本书一份，随书走；空文件不注入
    ("GET", "/writing_rules", handle_writing_rules),
    ("POST", "/writing_rules", handle_writing_rules_save),
    # 出厂预设（2026-10-21）：首跑向导“选预设”；apply 是破坏性动作（GUI 双因子）
    ("GET", "/factory/presets", handle_factory_presets_list),
    ("POST", "/factory/preset/apply", handle_factory_preset_apply),
)
