# -*- coding: utf-8 -*-
"""作品级写作约束（每本书一份，M12 / `mofang.collab.rules_home`，零 token）。

## 要治的病

写作约束散在 `config/project.yaml` 的 `style_notes`、`prompts/` 模板与讨论结论里，
**没有“每本书一份”的文件**。新开的 agent 会话开工时不知道这本书的规矩（术语、禁忌、
称呼、视角、每章必须出现的东西），只能靠人再讲一遍。

## 口径

- 位置：`data/writing_rules.md`（**书内数据**，随书归档走；不进 Git，也不进 `config/`）。
  为什么不放 `config/`：`config/` 是进 Git 的出厂/项目配置，作者正文写进去会污染仓库。
- 注入：`stage4_writing` / `stage4_group` / `stage6_polish` 三个写作入口的提示词里
  各有一个 `{{writing_rules}}` 占位符；**文件为空 → 渲染成空串，不占上下文、不留空标题**。
- 写入：`save()` 先备份旧版到 `data/state/writing_rules_history/`（留 10 份），
  再原子写 + **回读校验** —— 写失败必须当场报错，不许“界面说保存了、盘上没有”。
- 上限 `MAX_CHARS=6000`：这段每章都要发一次，写成长篇论文等于每章多付一遍钱。
  超限**拒收并说明**，不静默截断（截断会把一条约束砍成半句，比没有更糟）。

## 权限

读与写都属**创作决策**（同 `chapter.target_words`）：Agent 可代设、可读，
但不进 `FORBIDDEN_IN_AGENT_MODE`。边界靠“越限拒收 + 回读校验”，不靠挡人。
"""
from __future__ import annotations

import time
from pathlib import Path

from utils.file_io import read_text, write_text

REL = Path("data") / "writing_rules.md"
HISTORY_REL = Path("data") / "state" / "writing_rules_history"
MAX_CHARS = 6000
MAX_HISTORY = 10

#: 提示词里的标题（三个模板共用同一段文案，改这里就改了全部）
BLOCK_TITLE = "## 作品写作约束（作者为这本书定的硬规矩，冲突时以本节为准）"


def rules_path(root=None):
    return (Path(root) if root else Path(".")) / REL


def _history_dir(root=None):
    return (Path(root) if root else Path(".")) / HISTORY_REL


def load(root=None):
    """读约束。返回 {exists, text, chars, mtime}；文件缺失是**正常状态**，不报错。"""
    p = rules_path(root)
    if not p.exists():
        return {"exists": False, "text": "", "chars": 0, "mtime": 0.0, "path": str(p)}
    try:
        text = read_text(p).strip()
        st = p.stat()
    except Exception as e:                                    # noqa: BLE001
        return {"exists": False, "text": "", "chars": 0, "mtime": 0.0,
                "path": str(p), "error": type(e).__name__ + ": " + str(e)[:160]}
    return {"exists": True, "text": text, "chars": len(text),
            "mtime": st.st_mtime, "path": str(p)}


def block(root=None):
    """提示词片段。**空文件返回空串** —— 作者没写，就不许系统替他造一段规矩。"""
    text = load(root).get("text") or ""
    if not text.strip():
        return ""
    if len(text) > MAX_CHARS:
        # 磁盘上的文件被外部编辑器写超了：注入时截断到上限并明确标注
        # （保存路径会拒收，但外部改文件拦不住 —— 宁可少注入，也不要撑爆上下文）。
        text = text[:MAX_CHARS] + "\n\n（以上为前 %d 字；全文超过上限 %d 字，已截断）" % (
            MAX_CHARS, MAX_CHARS)
    return BLOCK_TITLE + "\n" + text


def numeric_block(cfg=None):
    """篇幅配比的**硬指标**（2026-11-05，试跑第 26 条）。

    为什么单独一段而不是写进 writing_rules.md：那是我手写的自由文本，作者可以不写；
    这是产品口径（`chapter.max_dialogue_ratio`，默认 0.35），有默认值、能被阶段 5
    实测核对。两段并存：作者写的规矩在前，数值指标在后。

    0 / 缺省值非法 → 不注入（= 关闭），不编一个数字。
    """
    try:
        chap = (cfg or {}).get("chapter") or {}
        raw = chap.get("max_dialogue_ratio")
        v = float(raw) if raw is not None else 0.35
    except (TypeError, ValueError):
        v = 0.35
    if not (0 < v <= 1):
        return ""
    pct = int(round(v * 100))
    return (
        "\n\n## 篇幅配比硬指标（超线会被作者当成问题章）\n"
        "- **含对白的段落不超过全章段落的 %d%%**。对话是用来推进冲突的，不是用来凑字数。\n"
        "- 每段对话前后要有动作、环境或内心反应；连续两段以上纯对白、中间没有动作插入，"
        "读起来就是“说话记录”。\n" % pct
    )


def _backup(p, root=None):
    """旧版进 history（按时间戳，留最近 MAX_HISTORY 份）。"""
    if not p.exists():
        return ""
    d = _history_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / ("%s.md" % time.strftime("%Y%m%d_%H%M%S"))
    try:
        dest.write_text(read_text(p), encoding="utf-8")
    except Exception:                                        # noqa: BLE001
        return ""
    olds = sorted(d.glob("*.md"))
    for extra in olds[:-MAX_HISTORY]:
        try:
            extra.unlink()
        except OSError:
            pass
    return str(dest)


def save(text, root=None):
    """保存约束。返回 (ok, message, info)。

    - 空白内容 = **清空**（删掉文件而不是留一个空文件 —— 空文件会被误读成“已配置”）；
    - 超 `MAX_CHARS` → 拒收，说明上限（不截断）；
    - 顺序：备份 → 原子写 → **回读比对**；回读不一致按失败报。
    """
    raw = text if isinstance(text, str) else ""
    body = raw.replace("\r\n", "\n").strip()
    p = rules_path(root)
    if not body:
        if p.exists():
            backup = _backup(p, root)
            try:
                p.unlink()
            except OSError as e:
                return False, "清空失败：%s" % e, {}
            return True, "已清空作品写作约束（旧版备份：%s）" % (backup or "无"), {"cleared": True}
        return True, "本来就是空的", {"cleared": False}
    if len(body) > MAX_CHARS:
        return False, ("太长：%d 字，上限 %d 字（这段每章都会发给模型）。"
                       "请压缩后再保存。" % (len(body), MAX_CHARS)), {"chars": len(body)}
    p.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup(p, root)
    try:
        write_text(p, body + "\n")
    except Exception as e:                                    # noqa: BLE001
        return False, "写入失败：%s" % str(e)[:200], {}
    try:
        back = read_text(p).strip()
    except Exception as e:                                    # noqa: BLE001
        return False, "写入后回读失败：%s" % str(e)[:200], {}
    if back != body:
        return False, "写入后回读不一致（盘上内容与提交的不一致，已保留备份 %s）" % (backup or "无"), {}
    return True, "已保存（%d 字）%s" % (len(body), ("；旧版备份：" + backup) if backup else ""), {
        "chars": len(body), "backup": backup, "path": str(p)}
