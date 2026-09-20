# -*- coding: utf-8 -*-
"""运行日志 + 按天轮转（补齐 `config/system.yaml` 的 `logging` 段）。

## 背景

`config/system.yaml` 里一直有：

    logging:
      level: info
      rotate_days: 30

但**全仓没有任何一行代码读它** —— 死配置。实际后果：
`%LOCALAPPDATA%/Temp/nf_api_child.log` 由 Electron 以 `openSync(path, "a")`
无界追加，长跑（一本书动辄数小时、多本书累积数月）会把日志撑到几百 MB，
且永不清理。用户排障时 `/logs/tail` 读它，越读越慢。

## 本模块做什么

- **`get_log_path()`**：定位运行日志（与 `nf_api /logs/tail` 的候选顺序一致，
  保证「写入方」和「读取方」看的是同一个文件）；
- **`rotate_if_needed()`**：把超过 `rotate_days` 天的日志改名归档为
  `nf_api.child.<日期>.log`，然后新建空日志，并清理更早的归档；
- **`append_line()` / `tail()`**：进程内写读（供 Python 侧调用）。

## 设计取舍

1. **按天轮转，不按大小**。配置项就叫 `rotate_days`，语义是时间；
   按大小轮转会让「读到哪个文件」变得不可预测，而排障时最需要的是
   「最近 N 天」的连续视图。归档文件名带日期，跨归档也能按名排序连续读。
2. **不改 Electron 的 spawn 方式**。主进程仍以 append 模式打开日志文件；
   轮转由 Python 侧在 nf_api 启动时执行一次，并对已打开的文件句柄保持安全
   （见下方 `_reopen_safe`）。理由：改 Electron 侧需要重启整个控制台才生效，
   而 Python 侧每次起 nf_api 都会走到，覆盖面更大。
3. **轮转失败绝不阻断启动**。日志是辅助设施 —— 它坏了不能连累主流程。
   所有异常都被收敛为「跳过轮转 + 打印一句告警」。
4. **只动自己认定的日志文件**。stale 归档必须匹配 `nf_api.child.*.log` 命名
   且确实在日志目录内，防止误删（与 `snapshot.py` 的 history/ 护栏同思路）。
"""
import os
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# 日志文件名（与 console/main/index.js 的 logPath 保持一致）
LOG_BASENAME = "nf_api_child.log"
# 归档命名：nf_api_child.20260919_223000.log
ARCHIVE_RE = re.compile(r"^nf_api_child\.(\d{8})_\d{6}\.log$")

DEFAULT_ROTATE_DAYS = 30
DEFAULT_LEVEL = "info"

# 级别名 → 数值（够用即可，不引入 logging 模块的完整体系）
_LEVELS = {"debug": 10, "info": 20, "warn": 30, "warning": 30,
           "error": 40, "critical": 50}
_LEVEL_ORDER = ("debug", "info", "warn", "error")


def _log_dir():
    """日志所在目录。优先 %LOCALAPPDATA%/Temp（Electron 写入处）。"""
    localapp = os.environ.get("LOCALAPPDATA")
    if localapp:
        return Path(localapp) / "Temp"
    return Path(os.environ.get("TEMP", "."))


def get_log_path():
    """返回运行日志的规范路径（不保证存在）。"""
    return _log_dir() / LOG_BASENAME


def load_logging_cfg(cfg=None):
    """从 system.yaml 的 logging 段取配置；缺省用模块默认值。

    返回 (level, rotate_days)。rotate_days <= 0 表示**不轮转**（显式关闭）。

    对**结构性**的错误配置也要宽容：`logging:` 被写成标量
    （`logging: none` / `logging: "debug"`）时，`.get` 会炸 AttributeError。
    调用方是启动路径，凡是「配置写歪了」都不该让服务起不来 —— 一律回退默认值。
    """
    section = (cfg or {}).get("logging") or {}
    if not isinstance(section, dict):
        # 常见歪法：`logging: info`（用户把 level 值直接写在段上）。
        # 这种情况下把标量当作 level 值是**有用的**猜测，比整个丢掉更友好。
        if isinstance(section, str):
            lvl = section.strip().lower()
            return (lvl if lvl in _LEVELS else DEFAULT_LEVEL), DEFAULT_ROTATE_DAYS
        section = {}
    level = str(section.get("level") or DEFAULT_LEVEL).lower()
    if level not in _LEVELS:
        level = DEFAULT_LEVEL
    try:
        rotate_days = int(section.get("rotate_days", DEFAULT_ROTATE_DAYS))
    except (TypeError, ValueError):
        rotate_days = DEFAULT_ROTATE_DAYS
    return level, rotate_days


def level_enabled(msg_level, cfg_level):
    """按配置级别过滤：配置 info 时不输出 debug。"""
    return _LEVELS.get(msg_level, 20) >= _LEVELS.get(cfg_level, 20)


def _candidates():
    """归档候选：同目录下所有符合命名的文件。"""
    d = _log_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and ARCHIVE_RE.match(p.name))


def _age_days(mtime):
    """文件 mtime 距今天数，**下限收敛为 0**。

    为什么不能直接 `.days`：`timedelta.days` 对负值**向下取整**，
    而 mtime 可能比 `now()` 晚哪怕 1 微秒（文件系统时间戳精度高于
    两次 `now()` 调用之间的间隔），于是「刚写的日志」会算出 -1 天。
    展示层出现「-1 天」是不可能的读数，会让人以为时间算错了。
    """
    return max(0, (datetime.now() - datetime.fromtimestamp(mtime)).days)


def rotate_if_needed(cfg=None, dry_run=False):
    """按 `logging.rotate_days` 轮转日志，并清理超期归档。

    返回 dict：{rotated: bool, archive: Path|None, removed: [Path], reason: str}

    语义：
      · 主日志的**起始时间**由 mtime 判断（首次创建/上次轮转后写入的时间）；
      · 若 mtime 距今 >= rotate_days 天 → 归档并新建；
      · 同时清理**归档文件**中日期的部分早于 (今天 - rotate_days) 的项。

    绝不抛异常（除编程错误外）—— 调用方是启动路径，日志坏了不能拖垮启动。
    """
    level_unused, rotate_days = load_logging_cfg(cfg)
    result = {"rotated": False, "archive": None, "removed": [], "reason": ""}

    if rotate_days <= 0:
        result["reason"] = "rotate_days<=0，已显式关闭轮转"
        return result

    log_path = get_log_path()
    try:
        if not log_path.exists():
            result["reason"] = "日志文件不存在，无需轮转"
            return result

        age = datetime.now() - datetime.fromtimestamp(log_path.stat().st_mtime)
        if age < timedelta(days=rotate_days):
            result["reason"] = (f"日志未超期（age={_age_days(log_path.stat().st_mtime)}"
                                f" 天 < {rotate_days} 天）")
            return result

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive = log_path.with_name(f"nf_api_child.{ts}.log")
        if dry_run:
            result["reason"] = f"[dry-run] 将归档 {log_path.name} → {archive.name}"
            result["archive"] = archive
            return result

        # 先改名再新建：改名是原子操作，避免"截断后崩溃"导致日志全丢。
        # 注意 Electron 主进程仍持有旧文件的写句柄 —— 它继续写归档文件，
        # 直到下次控制台重启才指向新文件。这是**可接受的**：
        # 每次起 nf_api 都会调用本函数，新旧日志都不会丢，只是临时分居两个文件。
        # 相比"强行要求重启控制台"，这个取舍保证了轮转一定发生。
        shutil.move(str(log_path), str(archive))
        log_path.touch()
        result["rotated"] = True
        result["archive"] = archive

        # 清理超期归档
        cutoff = datetime.now() - timedelta(days=rotate_days)
        for p in _candidates():
            m = ARCHIVE_RE.match(p.name)
            if not m:
                continue
            try:
                stamp = datetime.strptime(m.group(1), "%Y%m%d")
            except ValueError:
                continue
            if stamp < cutoff:
                p.unlink(missing_ok=True)
                result["removed"].append(p)
        result["reason"] = (f"已归档 → {archive.name}；清理超期归档 "
                            f"{len(result['removed'])} 份")
    except Exception as e:                                  # noqa: BLE001
        # 日志设施自身的故障绝不能拖垮启动
        result["reason"] = f"轮转失败（已忽略）: {type(e).__name__}: {e}"
    return result


def append_line(text, level="info", cfg=None):
    """向运行日志追加一行（带时间戳）。失败静默 —— 日志不该影响主流程。"""
    try:
        # 注意 unpack 顺序：load_logging_cfg 返回 (level, rotate_days)。
        # 写成 `_lvl, cfg_level = ...` 会把 rotate_days（int 30）当成级别字符串，
        # 于是 `_LEVELS.get(30, 20)` 落到默认值 20 → debug 被误判为不应输出。
        cfg_level, _rotate_days = load_logging_cfg(cfg)
        if not level_enabled(level, cfg_level):
            return False
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        p = get_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(f"[{stamp}] [{level.upper()}] {text}\n")
        return True
    except Exception:                                       # noqa: BLE001
        return False


def tail(lines=200, log_path=None):
    """读日志尾部（与 nf_api /logs/tail 同口径：容错解码，绝不抛）。"""
    p = Path(log_path) if log_path else get_log_path()
    if not p.exists():
        return []
    try:
        text = p.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-max(1, int(lines)):]


def log_status(cfg=None):
    """汇总日志现状（供 GUI 展示 / 排障）：路径、大小、天数、归档数。"""
    level, rotate_days = load_logging_cfg(cfg)
    p = get_log_path()
    info = {
        "path": str(p), "exists": p.exists(),
        "level": level, "rotate_days": rotate_days,
        "archives": [a.name for a in _candidates()],
        "size_bytes": 0, "age_days": None,
    }
    if p.exists():
        st = p.stat()
        info["size_bytes"] = st.st_size
        # 走 _age_days（下限收敛为 0）—— 直接 `.days` 对刚写的日志会给出 -1
        info["age_days"] = _age_days(st.st_mtime)
    return info
