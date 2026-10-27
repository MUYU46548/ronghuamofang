# -*- coding: utf-8 -*-
"""章节正文读写域 —— 用户「自带编辑器」的后端（2026-10-26）。

## 为什么新开一个域模块

用户第 1 条：「我之前提的画线批改之类的都没有，而且编辑还要强制用外部编辑器
打开，没有自带编辑器。」
第 2 条：「第二阶段产物依旧令人崩溃，完全不是一个适合审阅、适合精修、适合讨论
的形态。是整个前端的设计有问题。」

`/chapters/*` 此前已有 7 个端点，散在 `misc.py`（quality/verify）与 `refine.py`
（paragraph 四件套）。本次一口气加 3 个（layers / content / save），按 AGENTS
「某类端点超过 3 个就另开一个域模块，不要堆回 elif 链」收拢到这里 —— 后续
「划线批注」「多版本合并」「三栏审阅台」也住这个文件，不再往 3400 行的分发器里塞。

## 分工：别和"花 token 的模型改稿"混了

- `refine_chapter` / `refine_paragraph` = **LLM 改稿**（用户提意见，模型重写，烧 token）
- 本模块 = **用户手改落盘**（零 token、确定性）

两者共用同一个备份格式 `data/chapters/history/chNN_vM.md`，所以"回退"只有一条
通道，不会出现两套历史（两套历史必然漂移，谁先谁后都说不清）。

## 三条纪律

1. **路径一律经 api.ROOT**。`refine_chapter.HISTORY_DIR = Path("data/chapters/history")`
   是相对路径常量；API 进程 CWD 不等于 ROOT 时会静默写进**另一本书** ——
   这是本仓踩过四次的老坑，不在这里重犯。
2. **手改不改阶段状态**。progress 只归 orchestrator；用户改稿不等于重跑阶段，
   改完 stage 6 仍是 done（产物还在），但下游成品需要重跑阶段 7。
3. **落盘前备份旧稿 + 归一化标点**，与流水线同一套 `utils/text_norm`（幂等）。
"""
import re
import time

import nf_api as api

# 三层正文（refined 是成品层，checked 是逻辑检查后，raw 是原稿）
LAYERS = ("raw", "checked", "refined")
_LAYER_DIR = {"raw": "raw", "checked": "checked", "refined": "refined"}

# 单章正文上限：超过这个量级多半是误粘贴了整本（编辑器层挡一下，别把盘写爆）
MAX_CHARS = 200_000


def _chapter_path(n, layer):
    return api.ROOT / "data" / "chapters" / _LAYER_DIR[layer] / ("%02d.md" % n)


def _history_dir():
    return api.ROOT / "data" / "chapters" / "history"


def _next_version(n):
    d = _history_dir()
    d.mkdir(parents=True, exist_ok=True)
    nums = []
    for p in d.glob("ch%02d_v*.md" % n):
        m = re.search(r"_v(\d+)\.md$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def _backup(n, path):
    """把当前稿存进 history（空稿不备份）。返回备份文件名或 None。

    备份是**写前**动作：先存旧稿再覆盖，这样"保存"本身可回退（与
    `refine_chapter` 的同一约定）。备份失败不阻断保存（丢一个兜底点，
    不该让用户改了一半的内容存不进去），但要如实返回 None 让界面说清。
    """
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        v = _next_version(n)
        dst = _history_dir() / ("ch%02d_v%d.md" % (n, v))
        dst.write_text(text, encoding="utf-8")
        return dst.name
    except OSError:
        return None


def _target_words():
    """当前单章字数区间（给界面提示"比下限少多少"）。读不到就用出厂默认。"""
    try:
        cfg, _ = api.load_all()
        tw = (cfg.get("chapter") or {}).get("target_words") or []
        if isinstance(tw, (list, tuple)) and len(tw) >= 2:
            return [int(tw[0]), int(tw[1])]
    except Exception:                                        # noqa: BLE001
        pass
    return [5000, 9000]


def _count_words(text):
    try:
        from utils.verify_chapter import count_cn_words
        return int(count_cn_words(text))
    except Exception:                                        # noqa: BLE001
        return len(re.findall(r"[\u4e00-\u9fff]", text))


def _byte_len(text):
    """UTF-8 字节数（用户口径的「整章字节数」）。"""
    return len((text or "").encode("utf-8"))


def _parts_stats(n):
    """加强模式的段群统计（2026-10-27）：每群字数/字节 + 合计。

    「群字数」是判断 `chapter.enhanced_group_words`（默认 1500）该不该调的唯一
    依据 —— 用户原话：「run 完一章后，请把群字数和整章字节数发给我」。这些数字
    本来就在盘上（data/chapters/_parts/NN/gK.md），只是从没被读出来给人看。
    """
    d = api.ROOT / "data" / "chapters" / "_parts" / ("%02d" % n)
    out = []
    if d.is_dir():
        for f in sorted(d.glob("g*.md")):
            m = re.search(r"g(\d+)\.md$", f.name)
            if not m:
                continue
            try:
                t = f.read_text(encoding="utf-8")
            except OSError:
                continue
            out.append({"idx": int(m.group(1)), "words": _count_words(t),
                        "bytes": _byte_len(t)})
    return out


def _title_of(text):
    m = re.search(r"^#{1,6}[ \t]*(第[ \t]*\d+[ \t]*章[^\n]*)$", text, re.M)
    return m.group(1).strip() if m else ""


def _parse_n(h):
    """从 query 取章号；非法返回 (None, 错误消息)。"""
    raw = (h._query().get("n") or [""])[0]
    if not str(raw).isdigit() or int(raw) < 1:
        return None, "n 必填且为正整数（章号）"
    return int(raw), ""


def handle_chapters_layers(h):
    """`GET /chapters/layers?n=3` —— 三层正文的现状（编辑器打开时先看这个）。

    返回每层：是否存在、字数、标题、mtime、是否等于当前最高层。
    为什么不让前端自己 readPreview：那条 IPC 只能读、不数中文、也不认识
    「refined 才是当前稿」这条业务规则；这些判据放后端可单测。
    """
    try:
        n, err = _parse_n(h)
        if n is None:
            return 400, {"ok": False, "error": err}
        out = {}
        last_layer = ""          # 最近改动的层（按 mtime）
        last_mtime = -1.0
        for layer in LAYERS:
            p = _chapter_path(n, layer)
            item = {"layer": layer, "path": str(p), "exists": False,
                    "chars": 0, "bytes": 0, "words": 0, "title": "", "mtime": 0,
                    "mtime_text": ""}
            if p.exists():
                try:
                    text = p.read_text(encoding="utf-8")
                    st = p.stat()
                except OSError:
                    text, st = "", None
                item.update({
                    "exists": True,
                    "chars": len(text),
                    "bytes": _byte_len(text),
                    "words": _count_words(text),
                    "title": _title_of(text),
                })
                if st is not None:
                    item["mtime"] = st.st_mtime
                    item["mtime_text"] = time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
                    if st.st_mtime > last_mtime:
                        last_mtime = st.st_mtime
                        last_layer = layer
            out[layer] = item
        # 「当前稿」= **最高存在的层**（refined > checked > raw），不是 mtime 最新那层。
        # 为什么：三层是同一条产物的三个加工阶段，用户要审阅/精修的是成品层。
        # 用 mtime 裁决会在「三份同一秒写盘」时随机落到 raw（实测夹具就是这么写的，
        # 而且真实流水线里三层的 mtime 常常只差毫秒）—— 编辑器于是默认打开原稿，
        # 用户在错的那层改半天。
        current = ""
        for layer in reversed(LAYERS):
            if out[layer]["exists"]:
                current = layer
                break
        return 200, {
            "ok": True,
            "n": n,
            "layers": out,
            "current_layer": current,
            "last_modified_layer": last_layer,
            "target_words": _target_words(),
            "note": "raw=原稿 · checked=逻辑检查后 · refined=润色后（成品层，编辑器默认打开）",
        }
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_content(h):
    """`GET /chapters/content?n=3&layer=refined` —— 读一层正文全文。

    与 `/chapters/layers` 分开：列表页只要元数据（一次拉 40 章也不重），
    编辑器只读**一章一层**的全文。混在一个端点里会让列表带上几十万字。
    """
    try:
        n, err = _parse_n(h)
        if n is None:
            return 400, {"ok": False, "error": err}
        layer = (h._query().get("layer") or ["refined"])[0]
        if layer not in LAYERS:
            return 400, {"ok": False, "error": "layer 须为 raw / checked / refined"}
        p = _chapter_path(n, layer)
        if not p.exists():
            return 404, {"ok": False, "error": "第 %d 章的 %s 稿不存在" % (n, layer)}
        text = p.read_text(encoding="utf-8")
        st = p.stat()
        return 200, {
            "ok": True, "n": n, "layer": layer, "content": text,
            "chars": len(text), "bytes": _byte_len(text), "words": _count_words(text),
            "title": _title_of(text),
            "mtime": st.st_mtime,
            "mtime_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "target_words": _target_words(),
            # 段群明细（仅 raw 层；重写完成后的 toast 用它报「群字数 + 整章字节」）
            "parts": _parts_stats(n) if layer == "raw" else [],
        }
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_save(h, body):
    """`POST /chapters/save {n, layer, content, expected_mtime?}` —— 手改正文落盘。

    这是用户第 1 条的正解：不经过任何模型，把编辑器里的字存回去。

    四个护栏：
    - `expected_mtime`：编辑器开着时流水线可能正好重跑完这章。带了就比对，
      不一致返回 409（**不覆盖别人刚写的**），前端提示"重新加载"。
    - 缺章标题自动补：用户常常只贴正文，缺 `## 第N章` 会被下游校验判失败。
      补的时候按 `chapter.title_format` 渲染，与流水线同一套口径。
    - 标点归一化：落盘前跑 `utils/text_norm`（幂等），成品不留半角引号。
    - 写前备份旧稿到 `data/chapters/history/chNN_vM.md`。
    """
    try:
        body = body or {}
        raw_n = body.get("n")
        try:
            n = int(raw_n)
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "n 必填且为正整数（章号）"}
        if n < 1:
            return 400, {"ok": False, "error": "n 必填且为正整数（章号）"}
        layer = str(body.get("layer") or "").strip()
        if layer not in LAYERS:
            return 400, {"ok": False, "error": "layer 须为 raw / checked / refined"}
        content = body.get("content")
        if not isinstance(content, str):
            return 400, {"ok": False, "error": "content 必须是字符串"}
        if not content.strip():
            # 空稿会让下游把"没内容"当成"没写过"，断点续跑于是重写整章 ——
            # 要清空请走版本回退，别用保存。
            return 400, {"ok": False, "error": "内容为空。若要清空本章，请用「历史」回退到旧版本"}
        if len(content) > MAX_CHARS:
            return 400, {"ok": False,
                         "error": "内容过长（%d 字符，上限 %d）—— 单章不会这么长，"
                                  "是不是粘贴了整本？" % (len(content), MAX_CHARS)}

        p = _chapter_path(n, layer)
        # 并发保护：只在调用方主动给了 expected_mtime 时校验（向后兼容）
        expected = body.get("expected_mtime")
        if expected is not None and p.exists():
            try:
                cur_mtime = p.stat().st_mtime
                if abs(cur_mtime - float(expected)) > 0.001:
                    return 409, {
                        "ok": False,
                        "error": "这一章刚被其他操作改写过（可能是流水线重跑），"
                                 "为避免覆盖请你重新加载后再保存",
                        "mtime": cur_mtime,
                    }
            except (TypeError, ValueError, OSError):
                pass

        from utils.text_norm import (normalize_chapter_title,
                                     normalize_chinese_punctuation_with_counts)

        title_added = False
        if not re.search(r"^#{1,6}[ \t]*第[ \t]*\d+[ \t]*章", content, re.M):
            fmt = "第{n}章"
            try:
                from utils.text_norm import load_chapter_title_format
                fmt = (load_chapter_title_format() or "第{n}章").replace("{title}", "")
            except Exception:                                # noqa: BLE001
                pass
            head = fmt.replace("{n}", str(n)).strip() or ("第%d章" % n)
            content = "## " + head + "\n\n" + content.lstrip()
            title_added = True

        # 标题格式归一化 + 标点归一化（幂等；与 stage4/7 同一实现）
        content, _title_fixed = normalize_chapter_title(content)
        content, counts = normalize_chinese_punctuation_with_counts(content)

        backup = _backup(n, p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        st = p.stat()

        tw = _target_words()
        words = _count_words(content)
        warn = ""
        if words < tw[0]:
            warn = "字数 %d 低于下限 %d 字（%s）—— 下游体检会因此报不合格" % (
                words, tw[0], "按当前篇幅口径")
        elif words > tw[1]:
            warn = "字数 %d 超过上限 %d 字" % (words, tw[1])

        return 200, {
            "ok": True,
            "n": n,
            "layer": layer,
            "chars": len(content),
            "words": words,
            "title_added": title_added,
            "normalized": {k: v for k, v in counts.items() if v},
            "backup": backup,
            "mtime": st.st_mtime,
            "mtime_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
            "target_words": tw,
            "warning": warn,
            "message": "已保存第 %d 章 %s 稿（%d 字）%s"
                       % (n, layer, words, ("；" + warn) if warn else ""),
        }
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


def handle_chapters_rewrite(h, body):
    """`POST /chapters/rewrite {n}` —— 重写整章（走阶段 4，会花 token）。

    这才是"整章重写"的正路。此前的通道各有各的问题：
    - `POST /refine/chapter` 是**精修**（在旧稿上改），不是重写，而且不触发加强模式
    - 手动删文件再跑 orchestrator：对用户来说是不可接受的命令行操作

    要点（判据在 `nf_api.act_rewrite_chapter`）：旧稿先移到
    `data/chapters/rewrite_backup/`，再只跑阶段 4 的这一章。走的是正常写作链路，
    所以 `chapter.enhanced_mode` / 字数区间 / 加强模式自动兜底全部生效。
    """
    try:
        body = body or {}
        try:
            n = int(body.get("n"))
        except (TypeError, ValueError):
            return 400, {"ok": False, "error": "n 必填且为正整数（章号）"}
        if n < 1:
            return 400, {"ok": False, "error": "n 必填且为正整数（章号）"}
        cfg, _ = api.load_all()
        jid, err = api.start_job("rewrite_ch%d" % n, api.act_rewrite_chapter(cfg, n))
        if err:
            return 409, {"ok": False, "error": err}
        return 202, {"ok": True, "job_id": jid, "chapter": n,
                     "message": "已开始重写第 %d 章（旧稿已备份到 "
                                "data/chapters/rewrite_backup/）" % n}
    except Exception as e:                                   # noqa: BLE001
        return 500, {"ok": False, "error": type(e).__name__ + ": " + str(e)[:200]}


ROUTES = (
    ("GET", "/chapters/layers", handle_chapters_layers),
    ("GET", "/chapters/content", handle_chapters_content),
    ("POST", "/chapters/save", handle_chapters_save),
    ("POST", "/chapters/rewrite", handle_chapters_rewrite),
)
