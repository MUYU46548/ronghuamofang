# -*- coding: utf-8 -*-
"""run_log 回归自检：日志轮转（补齐 config/system.yaml 的 rotate_days 死配置）。

## 为什么需要它

`rotate_days` 曾长期是**死配置**——没有任何代码读它，
`%LOCALAPPDATA%/Temp/nf_api_child.log` 被 Electron 无界追加。
这类「配置写了但没人读」的缺陷不会报错、不会崩、单测也不会红，
只会让日志长到几百 MB 之后才开始有人抱怨。

## 本测试如何避免污染真实环境

`run_log` 的路径由 `LOCALAPPDATA` 环境变量决定（`_log_dir()`）。
本测试在**临时目录**里把 `LOCALAPPDATA` 指过去，所有写入都落在那里，
真实 `%LOCALAPPDATA%/Temp` 零触碰。

用法：python tests/unit/test_run_log.py
"""
import io
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


# --------------------------------------------------------------- 环境隔离

class Sandbox:
    """把 LOCALAPPDATA 指到临时目录，让 run_log 的所有读写都在沙箱里。"""

    def __enter__(self):
        self._old = os.environ.get("LOCALAPPDATA")
        self.dir = Path(tempfile.mkdtemp(prefix="run_log_test_"))
        os.environ["LOCALAPPDATA"] = str(self.dir)
        # 必须重新 import —— run_log 的 _log_dir() 是**运行时**读环境变量，
        # 不缓存，所以其实不用重载；但清掉 sys.modules 里的旧引用更保险，
        # 避免其它测试先 import 过导致状态残留。
        for m in [k for k in sys.modules if k.startswith("utils.run_log")]:
            del sys.modules[m]
        import utils.run_log as rl
        self.rl = rl
        return self

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


# --------------------------------------------------------------- 用例

def test_path_is_shared():
    """写入方与读取方必须看同一个文件（否则 /logs/tail 永远空）。"""
    print("\n[1] 路径来源唯一")
    with Sandbox() as sb:
        p = sb.rl.get_log_path()
        check("get_log_path() 落在 LOCALAPPDATA/Temp 下",
              p.parent.name == "Temp" or "Temp" in str(p), p)
        check("文件名为 nf_api_child.log（与 Electron logPath 一致）",
              p.name == "nf_api_child.log", p.name)


def test_append_and_tail():
    print("\n[2] 追加与读取")
    with Sandbox() as sb:
        rl = sb.rl
        check("append_line 默认级别 info 能写入", rl.append_line("hello") is True)
        rl.append_line("world", level="error")
        lines = rl.tail(10)
        check("tail 读回 2 行", len(lines) == 2, lines)
        check("行内含原文与级别标记",
              any("hello" in ln and "[INFO]" in ln for ln in lines), lines)
        check("error 级行带 [ERROR]", any("[ERROR]" in ln for ln in lines), lines)

        # 级别过滤：配置 info 时 debug 不该输出
        ok = rl.append_line("debug-hidden", level="debug", cfg={"logging": {"level": "info"}})
        check("配置 level=info 时 debug 被过滤（返回 False）", ok is False)
        ok2 = rl.append_line("shown", level="info", cfg={"logging": {"level": "info"}})
        check("同配置下 info 正常输出", ok2 is True)


def test_level_cfg_unpack_order():
    """历史缺陷：`_lvl, cfg_level = load_logging_cfg()` 把 rotate_days 当级别。

    `load_logging_cfg` 返回 (level, rotate_days)。曾把它 unpack 成
    `_lvl, cfg_level`，于是 cfg_level = 30（int）→ `_LEVELS.get(30, 20)`
    落默认值 20 → debug 被误判为「不该输出」。
    """
    print("\n[3] 配置解包顺序（历史缺陷回归）")
    with Sandbox() as sb:
        rl = sb.rl
        lvl, days = rl.load_logging_cfg({"logging": {"level": "debug", "rotate_days": 7}})
        check("load_logging_cfg 第一项是级别字符串", lvl == "debug", lvl)
        check("第二项是 rotate_days（int）", days == 7 and isinstance(days, int), days)
        ok = rl.append_line("dbg", level="debug",
                            cfg={"logging": {"level": "debug", "rotate_days": 7}})
        check("level=debug 配置下 debug 行真的写出（解包顺序正确）", ok is True)

        # 非法值回退默认，不得抛
        try:
            lvl2, days2 = rl.load_logging_cfg({"logging": {"level": "nope", "rotate_days": "x"}})
            check("非法 level 回退 info", lvl2 == "info", lvl2)
            check("非法 rotate_days 回退 30", days2 == 30, days2)
        except Exception as e:                              # noqa: BLE001
            check("非法配置不抛异常（回退默认）", False, repr(e))


def test_rotate_not_due():
    print("\n[4] 未超期不轮转")
    with Sandbox() as sb:
        rl = sb.rl
        rl.append_line("fresh")
        r = rl.rotate_if_needed({"logging": {"rotate_days": 30}})
        check("新日志不轮转", r["rotated"] is False, r["reason"])
        check("reason 说明未超期", "未超期" in r["reason"], r["reason"])


def test_rotate_due():
    print("\n[5] 超期轮转 + 归档清理")
    with Sandbox() as sb:
        rl = sb.rl
        log = rl.get_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("old content\n", encoding="utf-8")
        # 把 mtime 拨到 40 天前 → 超过 rotate_days=30
        old = time.time() - 40 * 86400
        os.utime(log, (old, old))

        # 另造两份超期归档（文件名带日期），验证会被清理
        stale = log.parent / ("nf_api_child.%s_120000.log"
                              % (datetime.now() - timedelta(days=90)).strftime("%Y%m%d"))
        stale.write_text("stale\n", encoding="utf-8")

        r = rl.rotate_if_needed({"logging": {"rotate_days": 30}})
        check("超期日志被轮转", r["rotated"] is True, r["reason"])
        check("归档文件已创建且含旧内容",
              r["archive"] is not None and r["archive"].exists()
              and "old content" in r["archive"].read_text(encoding="utf-8"),
              r["archive"])
        check("原日志被重建为空文件",
              log.exists() and log.stat().st_size == 0, log)
        check("超期归档被清理（90 天前的）",
              stale.exists() is False, [p.name for p in r["removed"]])
        check("清理只动自己人的文件（命名白名单生效）",
              all(rl.ARCHIVE_RE.match(p.name) for p in r["removed"]),
              [p.name for p in r["removed"]])


def test_rotate_disabled_and_dry_run():
    print("\n[6] 边界：显式关闭 / dry-run / 文件不存在")
    with Sandbox() as sb:
        rl = sb.rl
        r = rl.rotate_if_needed({"logging": {"rotate_days": 0}})
        check("rotate_days=0 → 显式关闭（不轮转）",
              r["rotated"] is False and "关闭" in r["reason"], r["reason"])

        r2 = rl.rotate_if_needed({"logging": {"rotate_days": 30}})
        check("日志不存在时不报错", r2["rotated"] is False, r2["reason"])

        log = rl.get_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("x\n", encoding="utf-8")
        old = time.time() - 40 * 86400
        os.utime(log, (old, old))
        before = log.read_text(encoding="utf-8")
        r3 = rl.rotate_if_needed({"logging": {"rotate_days": 30}}, dry_run=True)
        check("dry-run 报告将归档", "dry-run" in r3["reason"], r3["reason"])
        check("dry-run 未改动原文件", log.read_text(encoding="utf-8") == before)
        check("dry-run 未创建归档", r3["archive"] is not None
              and not r3["archive"].exists(), r3["archive"])


def test_never_raises():
    """轮转失败绝不阻断启动 —— 传垃圾 cfg 也不能抛。"""
    print("\n[7] 容错：坏配置不得抛异常")
    with Sandbox() as sb:
        rl = sb.rl
        for bad in ({"logging": None}, {"logging": "not-a-dict"}, {}, None):
            try:
                rl.rotate_if_needed(bad)
                ok = True
                err = ""
            except Exception as e:                          # noqa: BLE001
                ok = False
                err = repr(e)
            check("rotate_if_needed(%r) 不抛" % (bad,), ok, err)


def test_status():
    print("\n[8] log_status 汇总字段")
    with Sandbox() as sb:
        rl = sb.rl
        rl.append_line("for status")
        st = rl.log_status({"logging": {"level": "warn", "rotate_days": 5}})
        check("回报 level 与 rotate_days",
              st["level"] == "warn" and st["rotate_days"] == 5, st)
        check("exists 为 True 且 size > 0",
              st["exists"] and st["size_bytes"] > 0, st)
        # age_days 用「整数天差」计算，刚写的日志应为 0。
        #
        # 这里刻意断言 == 0 而不是 `in (0, 1)`：曾经写成 `in (0,1)` 来「防跨午夜」，
        # 结果**掩盖了一个真缺陷** —— `timedelta.days` 对负值向下取整，
        # 而 mtime 可能比 now() 晚哪怕 1 微秒（文件系统时间戳精度），
        # 于是刚写的日志算出 -1 天。断言要钉死契约，不能放宽到容忍坏值。
        check("age_days 下限为 0（全新日志不得为负）",
              st["age_days"] == 0, st["age_days"])
        check("archives 是列表", isinstance(st["archives"], list), st["archives"])

        # 负值回归：把 mtime 推到未来。若不做 max(0, ...) 收敛，这里会算出 -1。
        p = rl.get_log_path()
        future = time.time() + 5
        os.utime(p, (future, future))
        st2 = rl.log_status({"logging": {}})
        check("mtime 晚于 now 时 age_days 仍为 0（不出现 -1 天）",
              st2["age_days"] == 0, st2["age_days"])


def test_tail_tolerates_bad_encoding():
    print("\n[9] tail 容错（混编码不抛）")
    with Sandbox() as sb:
        rl = sb.rl
        p = rl.get_log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"good line\n\xff\xfe bad bytes\nlast line\n")
        lines = rl.tail(10)
        check("混编码文件仍能读出（errors=replace）", len(lines) == 3, lines)
        check("坏字节被替换而非抛异常",
              any("bad bytes" in ln for ln in lines), lines)
        check("lines=0 时至少返回 1 行（不返空）", len(rl.tail(0)) == 1,
              rl.tail(0))
        check("不存在的文件返回空列表",
              rl.tail(5, log_path=p.with_name("nope.log")) == [])


def main():
    test_path_is_shared()
    test_append_and_tail()
    test_level_cfg_unpack_order()
    test_rotate_not_due()
    test_rotate_due()
    test_rotate_disabled_and_dry_run()
    test_never_raises()
    test_status()
    test_tail_tolerates_bad_encoding()

    print("\n" + "=" * 68)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
