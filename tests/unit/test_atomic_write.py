# -*- coding: utf-8 -*-
"""原子写：断电/失败不许留下半截稿（C 组 ⑤，2026-11-03 复核）。

## 背景：台账这条写错了

台账里“原子写入 + 文件事务”一直挂着 ❌，理由是"全仓只有 run_lock.takeover
一处 os.replace"。**那个结论是 grep 出来的，没看实现**：`utils/file_io.write_text`
从 2026-09-14 起就是 `tmp 写入 → replace` 的原子写，`ProgressManager.save()` 与
所有正文落盘都走它。台账按"没搜到"下结论，和 2026-11-02 那次把指南针判成 ❌
是同一类错误（对账要取证"用户能不能用到"，不是"我搜没搜到"）。

## 这次真正补的缺口

原子写有个漏：**失败路径不清理 tmp**。写一半抛异常（磁盘满 / 权限 / 进程被杀在
replace 之前）会留下 `xx.md.tmp` —— 而 `glob("*.md")` 读不到它，用户永远不知道
目录里躺着一份半截稿。现在 except 里清掉，原文件保持原样。

## 判据（每条都能变红）

- A1 写完内容正确、无 `.tmp` 残留
- A2 replace 失败 → 抛异常、**原文件一字不动**、无 `.tmp` 残留
- A3 父目录不存在时自动建（并发/首跑常见）
- A4 `atomic=False` 时仍能写（逃生门没坏）

用法：python tests/unit/test_atomic_write.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from utils import file_io                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    print("原子写（C 组 ⑤）")

    tmp = Path(tempfile.mkdtemp())

    print("\n【1】正常写")
    p = tmp / "a" / "b.md"
    file_io.write_text(p, "第一行\n第二行\n")
    check("A1 内容正确", p.read_text(encoding="utf-8") == "第一行\n第二行\n")
    check("A1 无 .tmp 残留", not list(tmp.rglob("*.tmp")), list(tmp.rglob("*.tmp")))
    check("A3 父目录被自动创建", p.parent.is_dir())

    print("\n【2】失败路径（replace 炸掉）")
    good = tmp / "keep.md"
    file_io.write_text(good, "原来的内容，不许动\n")
    real_replace = Path.replace

    def boom(self, target):
        raise OSError("模拟：磁盘满 / 权限不足")

    Path.replace = boom
    raised = False
    try:
        file_io.write_text(good, "新内容，不该落盘\n")
    except OSError:
        raised = True
    finally:
        Path.replace = real_replace
    check("A2 抛出异常（不静默吞掉）", raised)
    check("A2 原文件一字不动", good.read_text(encoding="utf-8") == "原来的内容，不许动\n",
          good.read_text(encoding="utf-8"))
    check("A2 无 .tmp 残留（这次补的就是它）",
          not list(tmp.rglob("*.tmp")), list(tmp.rglob("*.tmp")))

    print("\n【3】逃生门")
    p2 = tmp / "c.md"
    file_io.write_text(p2, "非原子\n", atomic=False)
    check("A4 atomic=False 仍可写", p2.read_text(encoding="utf-8") == "非原子\n")

    print("\n【4】接线（复核台账那条 ❌ 是错的）")
    src = (ROOT / "scripts" / "utils" / "file_io.py").read_text(encoding="utf-8")
    check("A5 write_text 默认 atomic=True", "atomic=True" in src and "tmp.replace(p)" in src)
    prog = (ROOT / "scripts" / "utils" / "progress_manager.py").read_text(encoding="utf-8")
    check("A5 progress.save 走 write_text（不是裸 open）",
          "write_text(self.path" in prog)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
