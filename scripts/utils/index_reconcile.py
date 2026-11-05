# -*- coding: utf-8 -*-
"""索引与实物对账（零 token，纯确定性）。

## 为什么要这个东西

作者的素材目录里除了真素材，还躺着 `00_大纲收束索引.md`、`碎片填充计划.md` 这类
**登记表**。它们声称“共 96 篇 / 9 维齐全”或“已登记 24 个日期”，而磁盘上实际只有
41 张卡、18 个碎片日期 —— **两份元文件彼此也对不上**。

管线读素材一律以**文件系统为准**（`stage1_consolidate.scan_materials` 扫盘，
且用 `utils.meta_detect` 把元文件排掉）。所以元文件不参与生产，但它会**骗人**：
作者看索引以为 96 篇齐了，于是不再补素材。本模块的作用就是把这句话拆穿：
声称 vs 实物 → 差异清单。

## 判据

- 篇数：元文件里 `(\d+)\s*(篇|个碎片|条碎片|张卡|份素材)`，就地与该目录的实物数比。
- 日期：元文件里的所有日期 vs **全库**实物日期（文件名 + 正文前 5 行）——
  为什么跨目录比：`碎片填充计划` 常放在 raw 里却登记碎片的日期，就地比会假报
  “无一重合”（用户实测就是这么被误导的）。
- 维度（如“9 维齐全”）：文件系统证明不了，**如实标 unverifiable**，不猜。

## 边界

- 只读：不写任何文件、不调模型。
- 元文件的判据与 stage1 **同一份实现**（`utils.meta_detect`），不另写一套。
"""
import re
from datetime import datetime
from pathlib import Path

from utils.meta_detect import SUPPORTED_EXTS, is_meta_file, iter_material_files

# 篇数声称：带明确单位，避免把“24 个日期”算成篇数。
POSTS_RE = re.compile(
    r"(?:共|合计|总计|总共)?\s*[：:]?\s*(\d+)\s*"
    r"(?:篇|个碎片|条碎片|张卡|份素材|个素材)")
DIM_RE = re.compile(r"(\d+)\s*(?:个)?(?:维度|维)")

DATE_FULL_RE = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?")
DATE_MD_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
BARE_YMD_RE = re.compile(r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")

CONTENT_TS_HEAD_LINES = 5


def _valid(y, m, d):
    try:
        datetime(int(y), int(m), int(d))
        return True
    except (TypeError, ValueError):
        return False


def _date_keys(text, ref_year=None):
    """从一段文本抽出日期，返回 `[{exact, md}]`。

    `exact` 只在文本里带年份时给出（`2024-03-05`），否则 None；
    `md` 恒给出（`03-05`），供“一方没写年份”时弱匹配。
    """
    out = []
    for m in DATE_FULL_RE.finditer(text or ""):
        y, mo, d = m.groups()
        if _valid(y, mo, d):
            out.append({"exact": "%04d-%02d-%02d" % (int(y), int(mo), int(d)),
                        "md": "%02d-%02d" % (int(mo), int(d))})
    for m in DATE_MD_RE.finditer(text or ""):
        mo, d = m.groups()
        if _valid(ref_year or 2000, mo, d):
            out.append({"exact": None, "md": "%02d-%02d" % (int(mo), int(d))})
    for m in BARE_YMD_RE.finditer(text or ""):
        y, mo, d = m.groups()
        if _valid(y, mo, d):
            key = {"exact": "%s-%s-%s" % (y, mo, d), "md": "%s-%s" % (mo, d)}
            if key not in out:
                out.append(key)
    return out


def _dedup_dates(keys):
    seen, out = set(), []
    for k in keys:
        sig = k["exact"] or ("md:" + k["md"])
        if sig in seen:
            continue
        seen.add(sig)
        out.append(k)
    return out


def extract_claims(text):
    """从元文件正文抽“声称”：篇数 / 维度 / 日期集合。"""
    posts = None
    for m in POSTS_RE.finditer(text or ""):
        posts = int(m.group(1))
        break                      # 首次出现的总数声明最可信（登记表习惯写在开头）
    dims = None
    m = DIM_RE.search(text or "")
    if m:
        dims = int(m.group(1))
    return {
        "posts": posts,
        "dimensions": dims,
        "dates": _dedup_dates(_date_keys(text)),
    }


def _file_dates(path):
    """实物文件的日期：文件名优先，其次正文前 5 行（mtime 不可信，不参与）。"""
    keys = _date_keys(path.name)
    if keys:
        return _dedup_dates(keys)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    head = "\n".join([ln for ln in text.splitlines() if ln.strip()][:CONTENT_TS_HEAD_LINES])
    return _dedup_dates(_date_keys(head))


def _fmt_date(k):
    return k["exact"] or ("--" + k["md"])


def _match(claimed, actual_keys):
    """返回 (命中数, 未命中声称列表)。年份缺失时按 月-日 弱匹配。"""
    remaining = list(actual_keys)
    matched, missing = 0, []
    for c in claimed:
        hit = None
        for i, a in enumerate(remaining):
            if c["exact"] and a["exact"] and c["exact"] == a["exact"]:
                hit = i
                break
            if c["md"] == a["md"] and (c["exact"] is None or a["exact"] is None):
                hit = i
                break
        if hit is None:
            missing.append(c)
        else:
            matched += 1
            remaining.pop(hit)
    return matched, missing


def scan_scope(directory):
    """扫一个目录：实物文件 + 元文件（判据与 stage1 同源）。"""
    directory = Path(directory)
    files, warnings, meta_paths = iter_material_files(directory)
    actual = []
    for p in files:
        actual.append({"name": p.name, "dates": _file_dates(p)})
    metas = []
    for p in meta_paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        metas.append({"path": p, "claims": extract_claims(text)})
    return {"dir": directory, "actual": actual, "meta": metas, "warnings": warnings}


def reconcile(root, scopes):
    """对账主入口。

    `scopes` 是 `[(key, 相对目录或绝对路径), ...]`，例如
    `[("raw", "materials/raw"), ("scraps", "materials/original_scraps")]`。

    返回结构化结果（形状见模块顶部与测试），`verdict` ∈
    `consistent | mismatch | no_meta`。
    """
    root = Path(root)
    scanned, warnings = [], []
    for key, rel in scopes:
        d = Path(rel)
        if not d.is_absolute():
            d = root / rel
        s = scan_scope(d)
        scanned.append((key, rel, s))
        warnings.extend(s["warnings"])

    # 全库实物日期（跨 scope）—— 元文件常把碎片日期登记在 raw 目录里。
    all_actual_dates, actual_dates_by_scope = [], {}
    for key, rel, s in scanned:
        keys = []
        for item in s["actual"]:
            keys.extend(item["dates"])
        actual_dates_by_scope[key] = _dedup_dates(keys)
        all_actual_dates.extend(keys)
    all_actual_dates = _dedup_dates(all_actual_dates)

    meta_reports = []
    for key, rel, s in scanned:
        actual_posts = len(s["actual"])
        for meta in s["meta"]:
            claims = meta["claims"]
            issues = []
            bad = False
            diff = {
                "posts_actual": actual_posts,
                "posts_claimed": claims["posts"],
                "posts_delta": None,
                "dates_claimed": len(claims["dates"]),
                "dates_matched": 0,
                "dates_missing": [],
                "dates_extra": [],
                "dimensions": claims["dimensions"],
                "dimensions_verifiable": False,
            }
            if claims["posts"] is not None:
                diff["posts_delta"] = actual_posts - claims["posts"]
                if diff["posts_delta"] != 0:
                    bad = True
                    issues.append(
                        "声称 %d 篇，实物 %d 篇（差 %+d）"
                        % (claims["posts"], actual_posts, diff["posts_delta"]))
            if claims["dates"]:
                matched, missing = _match(claims["dates"], all_actual_dates)
                diff["dates_matched"] = matched
                diff["dates_missing"] = [_fmt_date(k) for k in missing]
                if missing:
                    bad = True
                    issues.append(
                        "声称的 %d 个日期里有 %d 个在任何素材文件里都找不到"
                        "（命中 %d 个）"
                        % (len(claims["dates"]), len(missing), matched))
                # 反向：实物里有、索引没登记（只报数，不逐条刷屏）
                claimed_sig = set((k["exact"] or k["md"]) for k in claims["dates"])
                extra = [k for k in all_actual_dates
                         if (k["exact"] or k["md"]) not in claimed_sig
                         and k["md"] not in
                         set(c["md"] for c in claims["dates"])]
                diff["dates_extra"] = [_fmt_date(k) for k in extra]
                if extra:
                    issues.append("实物里另有 %d 个日期未在索引中登记" % len(extra))
            if claims["dimensions"]:
                issues.append(
                    "声称“%d 维齐全”—— 文件系统证明不了维度完整性，"
                    "只能人工核对" % claims["dimensions"])
            rel_path = str(meta["path"])
            try:
                rel_path = str(meta["path"].relative_to(root)).replace("\\", "/")
            except ValueError:
                pass
            meta_reports.append({
                "scope": key,
                "name": meta["path"].name,
                "path": rel_path,
                "claims": {"posts": claims["posts"],
                           "dimensions": claims["dimensions"],
                           "dates": [_fmt_date(k) for k in claims["dates"]]},
                "diff": diff,
                "issues": issues,
                "consistent": not bad,
            })

    broken = [m for m in meta_reports if not m["consistent"]]
    if not meta_reports:
        verdict = "no_meta"
        summary = "素材目录里没有登记表（索引/清单），无需对账"
    elif broken:
        verdict = "mismatch"
        summary = "%d 份登记表与实物不一致：%s" % (
            len(broken), "；".join("%s（%s）" % (m["name"], m["issues"][0])
                                   for m in broken[:3]))
    else:
        verdict = "consistent"
        summary = "%d 份登记表的篇数与日期都能和实物对上" % len(meta_reports)

    return {
        "ok": True,
        "verdict": verdict,
        "consistent": verdict == "consistent",
        "summary": summary,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "actual": {
            "posts": {key: len(s["actual"]) for key, _rel, s in scanned},
            "dates": {key: [_fmt_date(k) for k in actual_dates_by_scope[key]]
                      for key, _rel, s in scanned},
            "dates_total": len(all_actual_dates),
        },
        "meta_files": meta_reports,
        "warnings": warnings,
    }
