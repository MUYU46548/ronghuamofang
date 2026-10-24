# -*- coding: utf-8 -*-
"""项目域（GET）：健康检查、状态快照、多书列表、项目结构树、配置读取。

## 为什么 /health 与 /state 放在「项目」域

它们回答的是同一个问题——「当前项目处于什么状态、能不能开工」。
GUI 启动时的冷启动引导、书本切换、初始化向导都读这一族。

## 关于 /project/status 里的 `stages`

`stages` 来自 `api.build_state()`，而其它字段是**文件系统探测**（路径 exists +
glob 计数）。两者刻意不合并：`stages` 是「流程走到哪」，exists 是「盘上有什么」。
实践中二者可能不一致（比如用户手删了产物但没重置状态），这是**有信息量的不一致**，
前端据此可以提示「状态与实际产物不符，建议打回重跑」。

## /config/project 的历史缺陷

该端点曾经**只有 POST 分支、没有 GET 分支**，而 GUI 用的是 GET → 一律 404
→ 初始化向导与「素材为空」提醒永远不触发。读操作必须挂在 do_GET 上。
"""
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


def handle_project_list(h):
    """多书列表（data/books/ 归档 + 当前书）。"""
    try:
        return 200, api.sb_mod.list_books()
    except Exception as e:                                  # noqa: BLE001
        return 500, {"error": type(e).__name__ + ": " + str(e)[:200]}


def handle_project_status(h):
    """项目结构树（左侧导航用）：状态 + 各产物是否存在。

    只做「存在性 + 计数」，不做内容解析——导航栏刷新频率高，
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
# 2026-10-19：范文原来只能「用编辑器打开 materials/style_sample.md 粘」，用户提的
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
    }


def handle_config_style_reference(h):
    """读范文正文 + 字数口径（GUI「范文」编辑框的数据源）。"""
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
                              "写作时会按「未提供范文」处理"
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
# 已被移走（配置陈旧），前端据此提示「文件不在」而不是让下拉悄悄显示空值。
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
    「静默退回硬编码样式」的坑；`../` 这类写法一律拒在门外。
    """
    body = body or {}
    name = str(body.get("template") or "").strip()
    if bool(body.get("reset")):
        name = DEFAULT_WORD_TEMPLATE
    norm = name.replace("\\", "/")
    if norm.startswith(TPL_PREFIX):
        norm = norm[len(TPL_PREFIX):]
    if not norm:
        return 400, {"ok": False, "error": "未选择模板（要回到出厂模板请点「恢复默认」）"}
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
    """设置类写入必须来自**可信的 GUI 本体**（来源头 + 主进程签发 token 双因子）。

    为什么与 agent_mode 无关也要求：这些是**成本/安全相关的设置**。
    外部 Agent 若能改预算与止烧阈值，就能抬高自己的闸门 —— 那等于没有闸门。

    为什么不能只认 `X-Mofang-Source: gui`：该头任何本机进程都可伪造（2026-10-06
    红队复现：伪造后 /project/archive/delete 返回 200 真删成功）。判据唯一实现是
    `agent_guard.is_trusted_gui`，与 `nf_api.do_POST` 的禁用名单**共用同一份**，
    否则两道闸各自漂移就重新裂出后门。

    拒文**零通道名**（V5）：不提缺哪个头、该填什么值 —— 拒文本身是攻击面。
    """
    from utils import agent_guard
    if agent_guard.is_trusted_gui(h.headers):
        return None
    return 403, {"ok": False,
                 "error": "%s 仅允许用户本人在桌面端手动修改；本次调用不是可信的 "
                          "GUI 来源。请停手并报告用户。" % what}


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
    与本轮修掉的「hermes 下 ¥ 记账恒 0」是同一类坑。
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
# 「一章两三千字根本不合格」。用户既定口径是「单章 5000~15000、常态 ~8000」。
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
    「绒花墨坊似乎没有显示设置每章字数要求的地方」—— 要能调就得先看得见。
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


def handle_config_vault_scan(h):
    """读知识库扫描面：顶层目录 + 各自篇数 + 是否被排除。

    给 GUI「知识库扫描」用。**通用性是这里的第一要求**：本端点只报告"你的库里
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


# system.local.yaml 可能从来没被创建过（用户没改过本地覆盖）——写入前先补骨架。
# 抬头注释要说明"这里的目录名是私人的"，否则用户会以为可以随便放进 system.yaml。
_VAULT_LOCAL_SKELETON = (
    "# 本地配置覆盖（不进 Git）\n"
    "# 模型选择、API Key 路径、本地沙盒目录等个人配置放这里。\n"
    "# 你个人知识库的目录名也写在这里 —— 它们是你的私人结构，不该进公开仓库。\n"
)


def _validate_skip_dirs(body):
    """校验排除目录入参。返回 (ok, msg, names)。

    只收**目录名**（不含路径分隔符 / `..`）：判据是「拿名字去比对路径的每一段」，
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
    每群落盘、拼装成章后走同一套校验。默认关 —— 施工单口径「只手动开启」。
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
# utils.agent_guard.is_forbidden_in_agent_mode —— 这里只补「为什么」，不改判据。
# ⚠️ 判据绝不能在这里复述一份：两份名单必然漂移（前端锁了后端没锁 / 反过来），
#    而漂移的表现正是用户 2026-10-24 抱怨的「客户端里分不清哪些走 Agent 哪些走内部」。
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
    用户 2026-10-24 的原话：「在客户端内根本分不清哪些会走 agent 哪些直接走内部
    配置 API，很混乱」。这里把权威名单 + 人话说明一次性交给前端渲染，前端不再
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
# 为什么在「项目」域：/project/list 已在此，归档的「看」与「删」是它的自然延伸 ——
# 此前 GUI 每行只有「恢复」，既看不了内容也删不掉，归档变成死胡同。
#
# 路径纪律：一律 api.ROOT 拼接（AGENTS.md「路径 IO 必须经 ROOT」）。
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

    只读。给「归档项目查看」用 —— 想删之前先看清楚里面有什么，
    也是「恢复出来才能看」这个死胡同的替代出口。
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
    （下载 .docx 之类对「看一眼」没有意义，还会把 JSON 响应变成乱码）。
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
        return 413, {"ok": False, "error": "文件过大（%d B > %d B），请用「恢复」后查看"
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
        return 400, {"ok": False, "error": "请输入归档名「%s」以确认删除" % name}
    d, err = _archive_dir(name)
    if err:
        return 404, {"ok": False, "error": err}
    import shutil
    try:
        if purge:
            shutil.rmtree(str(d))
            return 200, {"ok": True, "mode": "purged", "name": name,
                         "message": "已彻底删除「%s」（%s）—— 不可恢复" % (name, d)}
        ts = api.time.strftime("%Y%m%d_%H%M%S", api.time.localtime())
        trash = _books_root() / "_trash"
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / ("%s__%s" % (d.name, ts))
        shutil.move(str(d), str(target))
        return 200, {"ok": True, "mode": "trashed", "name": name,
                     "trash_path": str(target),
                     "message": "已移出归档列表「%s」→ 回收站 %s（要彻底清除请手工删除该目录）"
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
    """`GET /factory/presets` —— 列出出厂预设（只读）。首跑向导的「选预设」用。"""
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
        return 200, {"ok": True, "message": "预设「%s」已铺设" % pid, "log": tail}
    except Exception as e:                                  # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


# 本模块负责的端点（供自检与文档）
ROUTES = (
    ("GET", "/health", handle_health),
    ("GET", "/state", handle_state),
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
    # 知识库扫描面（2026-10-24）：目录名从用户库里读、界面上勾选排除 ——
    # 代码不猜任何库结构（曾因把某个真实库的目录名当通例写进代码而返工）
    ("GET", "/config/vault_scan", handle_config_vault_scan),
    ("POST", "/config/vault_skip_dirs", handle_config_vault_skip_dirs),
    # 加强模式（第一波防御 · 任务三，2026-10-22）：单章拆段落群生成开关
    ("GET", "/config/enhanced_mode", handle_config_enhanced_mode),
    ("POST", "/config/enhanced_mode", handle_config_enhanced_mode_set),
    # 写作预设（2026-10-21，v0.10.3）：多套写作要求存档与切换
    ("GET", "/config/writing_presets", handle_writing_presets_get),
    ("POST", "/config/writing_presets/save", handle_writing_presets_save),
    ("POST", "/config/writing_presets/apply", handle_writing_presets_apply),
    # 出厂预设（2026-10-21）：首跑向导「选预设」；apply 是破坏性动作（GUI 双因子）
    ("GET", "/factory/presets", handle_factory_presets_list),
    ("POST", "/factory/preset/apply", handle_factory_preset_apply),
)
