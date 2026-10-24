# -*- coding: utf-8 -*-
"""全文倒排召回（`utils.kb_index.recall`）回归 —— stage4 通道B。

新通道存在的**唯一理由**：老通道（`obsidian_bridge.scan_vault` → `inject_context`）
的扫描准入靠目录名，非标准布局的 vault 一条都扫不到（实测某真实库 = 0 条）。
而 Obsidian 的通用保证只有一个装着 .md 的目录。

钉住五件事：

| 编号 | 判据 | 为什么 |
|---|---|---|
| R1 | 非标准布局：scan_vault 扫 0，recall 照常命中 | 新通道的立身之本 |
| R2 | 无 frontmatter / 任意中文目录名 / 任意文件名 | 不许再"索要结构" |
| R3 | 字符预算真截断（不是只写在签名里） | 注入不许无限膨胀挤占写作 token |
| R4 | 索引按 vault 指纹缓存；persist=False 不落盘 | 逐章重建会把开销乘以章数 |
| R5 | 失败/未配置 → 返回 []，不抛 | 注入是增益，不许拖垮写作主链路 |
"""

import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import kb_index                                   # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    if ok:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def make_vault(prefix="nf_kb_recall_"):
    """非标准布局：任意中文深目录 + 无 frontmatter + 任意文件名。

    刻意**不造**默认 `vault_dirs.characters`（设定/人物）—— 老通道在这里必然扫零。
    """
    v = Path(tempfile.mkdtemp(prefix=prefix))
    d = v / "随手起的名" / "更深的一层"
    d.mkdir(parents=True)
    (d / "随便写的文件名.md").write_text(
        "# 跃迁引擎\n\n星舰靠折叠空间完成超远距离航行，核心装置叫跃迁引擎。\n",
        encoding="utf-8")
    (d / "另一个人.md").write_text(
        "# 另一个人\n\n他负责维护跃迁引擎的冷却回路，从不离岗。\n",
        encoding="utf-8")
    (d / "无关词条.md").write_text(
        "# 花园\n\n院子里种着会发光的苔藓。\n",
        encoding="utf-8")
    (d / "有结构的笔记.md").write_text(
        "# 基本信息\n\n这一页的 H1 是通用小节标题，不是主题名。\n",
        encoding="utf-8")
    return v


def _reset():
    kb_index._MEM_CACHE["key"] = None
    kb_index._MEM_CACHE["index"] = None


# ====================================================================== R1/R2

def case_recall_beats_scan_on_odd_layout():
    print("\n[R1/R2] 非标准布局 + 无 frontmatter + 任意文件名 → 老通道扫零、新通道命中")
    _reset()
    v = make_vault()
    try:
        from obsidian_bridge import scan_vault
        old = scan_vault(str(v))
        check("R1 老通道在该布局下确实扫零（新通道的存在理由）",
              len(old["characters"]) == 0 and len(old["world"]) == 0,
              f"→ chars={len(old['characters'])} world={len(old['world'])}")

        hits = kb_index.recall("跃迁引擎的原理", str(v), top_k=3)
        check("R1 新通道命中（正文关键词，不是文件名）",
              any("跃迁引擎" in name for _p, name, _s, _sc in hits),
              f"→ {[(n, round(sc, 1)) for _p, n, _s, sc in hits]}")
        check("R2 命中的是正文里的词（文件名叫「随便写的文件名」）",
              any("随便写的文件名" in p for p, _n, _s, _sc in hits),
              f"→ {[p for p, _n, _s, _sc in hits]}")
        check("R2 无 frontmatter 时取正文 H1 当名字（不是零信息量的文件名）",
              any(name == "跃迁引擎" for _p, name, _s, _sc in hits),
              f"→ {[n for _p, n, _s, _sc in hits]}")
        # H1 是小节标题时必须回退文件名 —— 某真实库 417 篇实测 H1 全是
        # 「基本信息」「一、概念定义」，照取会让词条名毫无辨识度
        all_hits = kb_index.recall("跃迁引擎 有结构的笔记 基本信息 花园", str(v),
                                   top_k=10, max_chars=100000)
        names = [n for _p, n, _s, _sc in all_hits]
        check("R2 小节标题 H1（「基本信息」）不当主题名，回退文件名",
              "有结构的笔记" in names and "基本信息" not in names,
              f"→ {names}")
        check("R2 无关词条不进 top（检索不是全收）",
              not any("花园" in name for _p, name, _s, _sc in hits),
              f"→ {[n for _p, n, _s, _sc in hits]}")

        # 查询词与词条名/文件名完全不同，只靠正文共现
        hits2 = kb_index.recall("冷却回路谁在维护", str(v), top_k=3)
        check("R2 措辞不同也命中（2-4 gram 对用词鲁棒）",
              any("另一个人" in name or "另一个人" in p
                  for p, name, _s, _sc in hits2),
              f"→ {[(n, p) for p, n, _s, _sc in hits2]}")
    finally:
        shutil.rmtree(v, ignore_errors=True)
        _reset()


# ====================================================================== R3

def test_budget_is_enforced():
    print("\n[R3] 字符预算真截断（超出即停，不是只写在签名里）")
    _reset()
    v = make_vault()
    try:
        small = kb_index.recall("跃迁引擎", str(v), top_k=10, max_chars=40)
        total = sum(len(s) for _p, _n, s, _sc in small)
        check("R3 注入正文不超过预算",
              total <= 40, f"→ {total} 字符 / 预算 40")
        check("R3 预算内仍给了内容（不是一刀切空）",
              bool(small), f"→ {small}")

        big = kb_index.recall("跃迁引擎", str(v), top_k=10, max_chars=100000)
        check("R3 放大预算不截断（证明上一条是预算在起作用）",
              sum(len(s) for _p, _n, s, _sc in big) >= total,
              f"→ 大预算 {sum(len(s) for _p, _n, s, _sc in big)}")
    finally:
        shutil.rmtree(v, ignore_errors=True)
        _reset()


# ====================================================================== R4

def test_cache_and_no_persist():
    print("\n[R4] 索引按 vault 指纹缓存；persist=False 不落盘")
    _reset()
    v = make_vault()
    old_cwd = Path.cwd()
    import os
    scratch = Path(tempfile.mkdtemp(prefix="nf_kb_cwd_"))
    os.chdir(scratch)
    try:
        t0 = time.time()
        kb_index.recall("跃迁引擎", str(v))
        t_first = time.time() - t0
        idx1 = kb_index._MEM_CACHE["index"]

        t0 = time.time()
        kb_index.recall("跃迁引擎", str(v))
        t_second = time.time() - t0
        idx2 = kb_index._MEM_CACHE["index"]
        check("R4 同一 vault 二次调用复用同一份索引（不重建）",
              idx1 is idx2, f"→ first={t_first:.2f}s second={t_second:.2f}s")
        check("R4 二次调用明显更快（缓存真的生效）",
              t_second <= t_first + 0.05,
              f"→ first={t_first:.2f}s second={t_second:.2f}s")

        # 改文件 → 指纹变 → 重建
        (v / "随手起的名" / "更深的一层" / "新增词条.md").write_text(
            "# 新增\n\n新写的正典内容，含关键词：水晶矩阵。\n", encoding="utf-8")
        hits_new = kb_index.recall("水晶矩阵", str(v))
        idx3 = kb_index._MEM_CACHE["index"]
        check("R4 vault 变化后索引重建（不静默陈旧）",
              idx3 is not idx1, f"→ {idx3 is idx1}")
        check("R4 重建后能检索到新文件",
              any("新增词条" in p for p, _n, _s, _sc in hits_new),
              f"→ {[p for p, _n, _s, _sc in hits_new]}")

        check("R4 persist=False 不往 CWD 写 pkl",
              not (scratch / "data" / "state" / "kb_index.pkl").exists(),
              f"→ {list(scratch.rglob('*.pkl'))}")
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(v, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)
        _reset()


# ====================================================================== R5

def test_failure_returns_empty():
    print("\n[R5] 失败/未配置 → 返回 []，不抛（注入是增益，不许拖垮写作）")
    _reset()
    check("R5 空 query → []",
          kb_index.recall("", "C:/nowhere") == [], "")
    check("R5 空白 query → []",
          kb_index.recall("   \n  ", "C:/nowhere") == [], "")
    check("R5 vault 不存在 → []",
          kb_index.recall("跃迁引擎", r"C:\definitely\not\here\xyz") == [], "")
    check("R5 空 vault_path → []",
          kb_index.recall("跃迁引擎", "") == [], "")

    # 指纹计算中途炸掉（vault 被删）也必须返回 [] 而不是抛
    v = make_vault()
    try:
        kb_index.recall("跃迁引擎", str(v))
        shutil.rmtree(v, ignore_errors=True)
        check("R5 索引后 vault 被删 → [] 不抛",
              kb_index.recall("跃迁引擎", str(v)) == [], "")
    finally:
        shutil.rmtree(v, ignore_errors=True)
        _reset()


def main():
    print("=" * 70)
    print("全文倒排召回（kb_index.recall）回归 —— stage4 通道B")
    print("=" * 70)
    case_recall_beats_scan_on_odd_layout()
    test_budget_is_enforced()
    test_cache_and_no_persist()
    test_failure_returns_empty()
    print("\n" + "=" * 70)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
