# -*- coding: utf-8 -*-
"""词表（病灶词 / 合规词）的唯一入口 —— 谁在用什么词、词从哪来、坏表怎么办。

## 为什么需要这一层

词表此前只有“改配置文件”一条路（`quality.cliche_wordlist` 指向一个 txt），
病灶词还能用内置 20 条兜底，合规词则**根本没有通道**。作者要换词得手改 YAML + 建文件，
改错（路径写错 / 表写成空 / 编码不是 UTF-8）时系统只会“扫描无命中”——
看起来像稿子变干净了，实际是闸门静默消失。

所以这里把三件事定死：

1. **落盘位置固定**（用户数据，不进 Git）：`data/wordlists/cliche.txt`、
   `data/wordlists/compliance.txt`；
2. **优先级只有一条**：GUI 文件 > `config/system.yaml` 的 `quality.cliche_wordlist` > 内置 20 条。
   每一档生效与否都**报出 source**，界面和 CLI 说得清“你现在用的是哪一档”；
3. **坏表不许静默变空**：空表 / 全是注释 / 正则编译失败 → 回落下一档，并把 warning
   带回调用方（返回结构里带 `warning`，不是 print）。清空表 = 明确的“我不用自定义词表”，
   回落内置 20 条并告警，而不是“零命中”。

## 词表格式（两份表同款，一行一条）

- 普通行 = 字面词；
- `regex:` 前缀 = 正则（如 `regex:仿佛[^，。！？\\n]{0,12}一般`）；
- `#` 开头 = 注释；空行忽略。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils.book_extract.wordlist import Term, make_term

__all__ = [
    "CLICHE_REL", "COMPLIANCE_REL", "MAX_CHARS", "MAX_LINE_CHARS",
    "parse_wordlist", "load_cliche", "load_compliance", "describe", "save",
    "compliance_hits",
]

CLICHE_REL = "data/wordlists/cliche.txt"
COMPLIANCE_REL = "data/wordlists/compliance.txt"

# 上限不是“防黑客”，是防手滑：把一整本书粘进词表，会让每章扫描变成 O(词数 × 章长)。
MAX_CHARS = 200_000
MAX_LINE_CHARS = 200

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _p(root: str | Path, rel: str) -> Path:
    return Path(root) / rel


def parse_wordlist(text: str) -> Tuple[List[Term], List[str]]:
    """把词表原文解析成 Term 列表；返回 (terms, warnings)。

    坏行（正则编译失败 / 超长）**跳过并记 warning** —— 一条坏行不该让整张表作废，
    但也不能假装没发生（那正是“词表静默变空”的老毛病）。
    """
    terms: List[Term] = []
    warnings: List[str] = []
    seen = set()
    src = "" if text is None else str(text)
    for no, line in enumerate(src.splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if len(raw) > MAX_LINE_CHARS:
            warnings.append("第 %d 行超过 %d 字符，已跳过" % (no, MAX_LINE_CHARS))
            continue
        try:
            term = make_term(raw)
        except re.error as e:
            warnings.append("第 %d 行正则无效（%s），已跳过" % (no, e))
            continue
        if term is None or term.name in seen:
            continue
        seen.add(term.name)
        terms.append(term)
    return terms, warnings


def _read(p: Path) -> Tuple[Optional[str], str]:
    """读词表文件；返回 (原文 or None, 错误说明)。读不到就是 None，不抛。"""
    try:
        if not p.exists():
            return None, ""
        return p.read_text(encoding="utf-8"), ""
    except OSError as e:
        return None, "读取失败：%s" % e
    except UnicodeDecodeError:
        return None, "不是 UTF-8 编码（请另存为 UTF-8）"


def _load_file(p: Path) -> Tuple[List[Term], bool, List[str]]:
    """读一个词表文件：返回 (terms, 文件在不在, warnings)。"""
    text, err = _read(p)
    if text is None:
        return [], False, ([err] if err else [])
    terms, warns = parse_wordlist(text)
    if not terms:
        warns.append("词表是空的（或全是注释）：%s" % p.name)
    return terms, True, warns


def load_cliche(root: str | Path = ".", cfg: Optional[Dict[str, Any]] = None
                ) -> Dict[str, Any]:
    """病灶词表：GUI 文件 > config 路径 > 内置 20 条。

    返回 `{terms, source, path, warning}`；`source` ∈ gui | config | builtin。
    """
    root_p = Path(root)
    terms, exists, warns = _load_file(_p(root_p, CLICHE_REL))
    if terms:
        return {"terms": terms, "source": "gui", "path": CLICHE_REL,
                "warning": "；".join(warns) if warns else ""}

    cfg_rel = ""
    try:
        if cfg is None:
            from utils.config_io import load_config_yaml
            cfg = load_config_yaml(str(root_p / "config" / "system.yaml")) or {}
        cfg_rel = str(((cfg.get("quality") or {}).get("cliche_wordlist") or "")).strip()
    except Exception as e:                                   # noqa: BLE001
        warns.append("读配置失败：%s: %s" % (type(e).__name__, str(e)[:120]))

    if cfg_rel:
        cp = Path(cfg_rel)
        if not cp.is_absolute():
            cp = root_p / cp
        c_terms, c_exists, c_warns = _load_file(cp)
        warns.extend(c_warns)
        if c_terms:
            return {"terms": c_terms, "source": "config", "path": str(cfg_rel),
                    "warning": "；".join(warns) if warns else ""}
        if not c_exists:
            warns.append("config 里的词表路径不存在：%s" % cfg_rel)

    from utils.book_extract.wordlist import default_wordlist
    builtin = default_wordlist()
    warns.insert(0, "已回落内置 %d 条病灶词" % len(builtin))
    return {"terms": builtin, "source": "builtin", "path": "",
            "warning": "；".join(warns)}


def load_compliance(root: str | Path = ".") -> Dict[str, Any]:
    """合规词表：只有 GUI 文件一档。

    没有文件 = **未配置**（不是故障）：校对里这条通道完全静默，不报告也不告警 ——
    “没启用”和“配错了”必须分得开（产品判据：可选外部资源三态）。
    """
    terms, exists, warns = _load_file(_p(Path(root), COMPLIANCE_REL))
    if not exists:
        return {"terms": [], "source": "", "path": "", "warning": ""}
    return {"terms": terms, "source": "gui" if terms else "",
            "path": COMPLIANCE_REL,
            "warning": "；".join(warns) if warns else ""}


def describe(root: str | Path = ".", cfg: Optional[Dict[str, Any]] = None
             ) -> Dict[str, Any]:
    """界面用的现状：两份表的原文 + 生效档位 + 条数 + 告警。"""
    root_p = Path(root)
    cl_text, cl_err = _read(_p(root_p, CLICHE_REL))
    co_text, co_err = _read(_p(root_p, COMPLIANCE_REL))
    cl = load_cliche(root_p, cfg)
    co = load_compliance(root_p)
    return {
        "ok": True,
        "cliche": {
            "text": cl_text or "",
            "path": CLICHE_REL,
            "exists": cl_text is not None,
            "terms": len(cl["terms"]),
            "source": cl["source"],
            "source_label": {"gui": "界面词表", "config": "配置文件指定的词表",
                             "builtin": "内置 20 条"}.get(cl["source"], cl["source"]),
            "warning": cl["warning"],
            "error": cl_err,
        },
        "compliance": {
            "text": co_text or "",
            "path": COMPLIANCE_REL,
            "exists": co_text is not None,
            "terms": len(co["terms"]),
            "source": co["source"] or "unconfigured",
            "source_label": "界面词表" if co["source"] else "未配置（不检查）",
            "warning": co["warning"],
            "error": co_err,
        },
        "format_note": "一行一条；# 开头是注释；regex: 前缀表示正则。",
        "limits": {"max_chars": MAX_CHARS, "max_line_chars": MAX_LINE_CHARS},
    }


def _validate_text(name: str, text: str) -> List[str]:
    """校验一份词表原文；返回错误清单（空 = 通过）。"""
    errs: List[str] = []
    if len(text) > MAX_CHARS:
        errs.append("%s 太长（%d 字符 > %d），词表是“几十到几百条”的东西，不是一本书"
                    % (name, len(text), MAX_CHARS))
    m = _CONTROL_RE.search(text)
    if m:
        errs.append("%s 含不可见控制字符（位置 %d），请从纯文本重新粘贴"
                    % (name, m.start()))
    for no, line in enumerate(text.splitlines(), 1):
        if len(line) > MAX_LINE_CHARS:
            errs.append("%s 第 %d 行 %d 字符超过上限 %d"
                        % (name, no, len(line), MAX_LINE_CHARS))
            break
    return errs


def save(root: str | Path, cliche: Optional[str] = None,
         compliance: Optional[str] = None) -> Dict[str, Any]:
    """写两份词表（只为传了的那份写盘）。

    校验不通过抛 ``ValueError``（调用方翻 400）；空表**允许**保存 ——
    它是“我不用自定义词表”的一次明确表态，由 `load_cliche` 回落内置并告警。
    """
    root_p = Path(root)
    errs: List[str] = []
    payloads: List[Tuple[Path, str, str]] = []
    if cliche is not None:
        if not isinstance(cliche, str):
            raise ValueError("cliche 必须是字符串")
        errs += _validate_text("病灶词表", cliche)
        payloads.append((_p(root_p, CLICHE_REL), cliche, "病灶词表"))
    if compliance is not None:
        if not isinstance(compliance, str):
            raise ValueError("compliance 必须是字符串")
        errs += _validate_text("合规词表", compliance)
        payloads.append((_p(root_p, COMPLIANCE_REL), compliance, "合规词表"))
    if not payloads:
        raise ValueError("没有要保存的词表（cliche / compliance 至少传一个）")
    if errs:
        raise ValueError("；".join(errs))

    written = []
    for path, text, label in payloads:
        # 统一换行、去掉尾部空行：同一个词表在不同编辑器里存成 CRLF/LF 两种，
        # 不该让“保存了一次”变成 diff 满屏（也不该让行号对不上）。
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        if normalized:
            normalized += "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(normalized, encoding="utf-8", newline="\n")
        tmp.replace(path)                     # 原子替换：写一半崩掉不会留下半张表
        terms, warns = parse_wordlist(normalized)
        written.append({"label": label, "path": str(path.relative_to(root_p)).replace("\\", "/"),
                        "terms": len(terms), "warning": "；".join(warns)})

    return {"ok": True, "saved": written,
            "message": "；".join("%s %d 条" % (w["label"], w["terms"]) for w in written)}


def compliance_hits(text: str, terms: List[Term], limit_per_term: int = 20
                    ) -> List[Dict[str, Any]]:
    """合规词命中清单（只报告）：[{name, count, samples:[...]}]，按次数降序。"""
    out: List[Dict[str, Any]] = []
    for term in terms or []:
        try:
            hits = list(term.pattern.finditer(text or ""))
        except Exception:                                    # noqa: BLE001
            continue
        if not hits:
            continue
        samples = []
        for m in hits[:limit_per_term]:
            s = max(0, m.start() - 6)
            samples.append((text[s:m.end() + 6] or "").replace("\n", " "))
        out.append({"name": term.name, "count": len(hits), "samples": samples[:3]})
    out.sort(key=lambda x: -x["count"])
    return out
