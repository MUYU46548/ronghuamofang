# -*- coding: utf-8 -*-
"""判定基准集（正例稿）的回归锁（2026-11-04）。

## 锁什么

1. **不勾免责声明不许导入**（版权/使用范围的确认必须留痕）。
2. 太短的文本拒收（正例要够一段完整文字）。
3. 同一内容不重复导入（sha256 去重）。
4. 移除 = 进 `_removed/`（可恢复），不是删除。
5. 回测的 "verified" 判据是**条目数 > 0**，不是"目录存在"。

用法：python tests/unit/test_benchmark.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import benchmark as B  # noqa: E402
from utils.judge import backtest as BT  # noqa: E402

PASS, FAIL = [], []
BODY = "雨落在青石板上，他把伞收起来，抖了抖水才进门。" * 30   # ~800 字


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  → " + str(detail)) if detail and not cond else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bench_"))
    try:
        print("== 1. 空清单 ==")
        d = B.list_entries(root=tmp)
        check("空清单 verified=False", d["verified"] is False and d["count"] == 0, d)
        check("带免责声明文本与版本", bool(d["disclaimer"]["text"]) and bool(d["disclaimer"]["version"]), None)
        check("回测侧 benchmark_count=0", BT.benchmark_count(tmp) == 0, BT.benchmark_count(tmp))

        print("== 2. 没勾免责声明 → 拒 ==")
        ok, msg, ent = B.import_text("测试", BODY, False, root=tmp)
        check("拒绝导入", ok is False and "免责声明" in msg, msg)
        check("目录里没有条目", B.count(tmp) == 0, B.count(tmp))

        print("== 3. 太短 → 拒 ==")
        ok, msg, _ = B.import_text("短", "太短了", True, root=tmp)
        check("拒绝短文本", ok is False and "太短" in msg, msg)

        print("== 4. 正常导入 ==")
        ok, msg, ent = B.import_text("我的正例", BODY, True, source="测试", root=tmp)
        check("导入成功", ok and ent and ent["name"] == "我的正例", msg)
        f = tmp / "data/judge/benchmark/files" / (ent["id"] + ".md")
        check("副本落盘", f.exists() and f.read_text(encoding="utf-8") == BODY.strip(), str(f))
        check("清单 count=1", B.count(tmp) == 1, B.count(tmp))
        check("verified=True", B.list_entries(root=tmp)["verified"] is True, None)
        check("回测侧 benchmark_count=1", BT.benchmark_count(tmp) == 1, BT.benchmark_count(tmp))
        check("免责声明版本已记录", ent["license"]["version"] == B.DISCLAIMER_VERSION, ent.get("license"))

        print("== 5. 重复导入 → 拒 ==")
        ok, msg, _ = B.import_text("换个名字", BODY, True, root=tmp)
        check("同内容不重复导入", ok is False and "已经导入过" in msg, msg)
        check("条目数仍是 1", B.count(tmp) == 1, B.count(tmp))

        print("== 6. 名字清洗 ==")
        ok, msg, ent2 = B.import_text('a/b:c*?"<>|', BODY + "另一段。", True, root=tmp)
        check("导入第二份", ok, msg)
        check("名字不含路径符号", not any(c in ent2["name"] for c in '\\/:*?"<>|'), ent2["name"])

        print("== 7. 移除 → 可恢复 ==")
        ok, msg, _ = B.remove(ent["id"], root=tmp)
        check("移除成功", ok, msg)
        check("count 减一", B.count(tmp) == 1, B.count(tmp))
        check("文件进 _removed", (tmp / "data/judge/benchmark/_removed" / (ent["id"] + ".md")).exists(), None)
        lst = B.list_entries(root=tmp, include_removed=True)
        check("已移除清单能看到", [x["id"] for x in lst.get("removed") or []] == [ent["id"]], lst.get("removed"))

        ok, msg, _ = B.restore(ent["id"], root=tmp)
        check("恢复成功", ok, msg)
        check("count 回到 2", B.count(tmp) == 2, B.count(tmp))
        check("已移除清单清空",
              B.list_entries(root=tmp, include_removed=True).get("removed") == [], None)

        print("== 8. API 层 ==")
        import nf_api
        import nf_api_domains.benchmark as API_B
        nf_api.ROOT = tmp
        st, d = API_B.handle_benchmark_list(None)
        check("GET 清单 200 且 2 份", st == 200 and d["count"] == 2, d)
        st, d = API_B.handle_benchmark_import(None, {
            "name": "接口正例", "text": BODY + "第三份。", "accept_license": True})
        check("POST 导入 200", st == 200 and d["count"] == 3, d)
        st, d = API_B.handle_benchmark_import(None, {"name": "x", "text": BODY, "accept_license": False})
        check("没勾确认 → 400", st == 400, d)
        st, d = API_B.handle_benchmark_remove(None, {"id": ent["id"]})
        check("POST 移除 200", st == 200 and d["count"] == 2, d)
        st, d = API_B.handle_benchmark_restore(None, {"id": ent["id"]})
        check("POST 恢复 200", st == 200 and d["count"] == 3, d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
