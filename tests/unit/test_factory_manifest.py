# -*- coding: utf-8 -*-
"""`utils/factory_manifest` 自检：清单读写合并 / 样例检测 / 清理硬边界。

## 为什么要有它

0.6.7「出厂数据与用户数据分治」的全部判据都在这个模块里，而它的失败形态是
**静默的**：检测漏报 → 用户以为自己的素材里没有出厂卡（继续被污染）；
误报 → 用户自己的卡被搬进回收站（丢数据）。这两类都不能只靠手点 GUI 验收。

用例全部在临时目录里造 sample 与 workspace，**不碰真实仓库的 materials/**。
（真实仓库的 materials/raw/ 里确实有样例卡 —— 那正是本功能要治的现场，
 但清理动作绝不能在测试里对它执行。）

用法：python tests/unit/test_factory_manifest.py
"""
import hashlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import factory_manifest as fm   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_tree(root: Path, files: dict) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return root


# ============================================================ 1. sha / 记录
def case_sha_and_records(tmp):
    print("\n【1】sha256 与播种记录")
    d = build_tree(tmp / "seed", {"scripts/a.py": "print(1)\n",
                                  "prompts/b.md": "# b\n"})
    check("sha256_file 已知值（abc）",
          sha(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    # 直接对真实文件算，和 hashlib 对齐
    f = d / "scripts" / "a.py"
    check("sha256_file 与 hashlib 结果一致",
          fm.sha256_file(f) == hashlib.sha256(f.read_bytes()).hexdigest())

    recs = fm.build_seed_records(d, ["scripts/a.py", "prompts/b.md", "missing/x.md"])
    check("build_seed_records 记录 2 条（缺失文件跳过）", len(recs) == 2, recs)
    check("记录形状 {path, sha256} 且路径是 POSIX 相对串",
          all(set(r) == {"path", "sha256"} and not r["path"].startswith("/")
              and "\\" not in r["path"] for r in recs), recs)
    check("sha 值与实测一致",
          sorted(r["sha256"] for r in recs) == sorted([
              hashlib.sha256(f.read_bytes()).hexdigest(),
              hashlib.sha256((d / "prompts" / "b.md").read_bytes()).hexdigest()]))


# ============================================================ 2. 清单读写合并
def case_manifest_roundtrip(tmp):
    print("\n【2】清单 write / read / 合并")
    ws = tmp / "ws1"
    ws.mkdir()
    check("无清单时 read 返回 {}（不抛）", fm.read_manifest(ws) == {})

    m1 = fm.write_manifest(ws, {"scripts/nf_api.py": "a" * 64,
                                "prompts/keep.md": "k" * 64}, seed_version=21)
    check("首次写入落盘 data/state/factory_manifest.json",
          (ws / "data" / "state" / "factory_manifest.json").is_file())
    check("返回体含 seed_version / generated_at / files",
          m1.get("seed_version") == 21 and isinstance(m1.get("generated_at"), str)
          and len(m1.get("files") or {}) == 2, m1)

    # 合并：新增 + 覆盖，旧记录保留
    fm.write_manifest(ws, {"scripts/nf_api.py": "b" * 64,
                           "prompts/stage4_writing.md": "c" * 64},
                      seed_version=22)
    back = fm.read_manifest(ws)
    check("合并语义：同 key 覆盖",
          back["files"]["scripts/nf_api.py"] == "b" * 64, back["files"])
    check("合并语义：新 key 追加",
          back["files"]["prompts/stage4_writing.md"] == "c" * 64, back["files"])
    check("合并语义：上一轮的旧 key 保留（不被整份覆盖）",
          back["files"].get("prompts/keep.md") == "k" * 64, back["files"])
    check("seed_version 取较大者（21 → 22）", back.get("seed_version") == 22, back.get("seed_version"))

    # 回滚不倒退：用更小的 seed_version 再写一次
    fm.write_manifest(ws, {"x/y.md": "d" * 64}, seed_version=21)
    check("seed_version 不因重写回退（保持 22）",
          fm.read_manifest(ws).get("seed_version") == 22,
          fm.read_manifest(ws).get("seed_version"))

    # 损坏的清单不炸读侧
    bad = tmp / "ws2"
    bad.mkdir()
    (bad / "data" / "state").mkdir(parents=True)
    (bad / "data" / "state" / "factory_manifest.json").write_text("{ not json",
                                                                  encoding="utf-8")
    check("损坏清单 → read 返回 {}（读侧绝不抛）", fm.read_manifest(bad) == {})
    check("损坏清单上再写 → 按空清单合并成功",
          len(fm.write_manifest(bad, {"a/b.md": "e" * 64}).get("files") or {}) == 1)


# ============================================================ 3. 样例检测
SAMPLE_FILES = {
    "materials/潮神_角色卡.md": "# 潮神\n出厂内容\n",
    "materials/回声_角色卡.md": "# 回声\n出厂内容\n",
    "materials/潮崩_概念卡.md": "# 潮崩\n出厂内容\n",
    "project.yaml": "book:\n  name: 雾港核心\n",
}


def case_detect(tmp):
    print("\n【3】样例检测（2 个一致 + 1 个改过 + 1 个无关 → 只报 2 个）")
    sample = build_tree(tmp / "examples" / "sample-book", SAMPLE_FILES)
    ws = tmp / "ws3"
    build_tree(ws, {
        # 与样例逐字节一致 → 命中
        "materials/raw/潮神_角色卡.md": SAMPLE_FILES["materials/潮神_角色卡.md"],
        "materials/original_scraps/回声_角色卡.md": SAMPLE_FILES["materials/回声_角色卡.md"],
        # 样例改过一个字（用户动过 = 用户数据）→ 不命中
        "materials/raw/潮崩_概念卡.md": "# 潮崩\n用户改过的内容\n",
        # 与样例无关（用户自己写的）→ 不命中
        "materials/raw/我自己的卡_角色卡.md": "# 自创角色\n",
        # 工作区其他数据：根本不进检测范围
        "data/outline/global.md": "# 大纲\n",
        "data/state/progress.json": "{}",
        # 归档回收站里的样例副本：排除
        "data/books/_trash/old__20260101/materials/raw/潮神_角色卡.md":
            SAMPLE_FILES["materials/潮神_角色卡.md"],
    })

    hits = fm.detect_sample_matches(ws, sample)
    paths = [h["path"] for h in hits]
    check("只报 2 个逐字节一致的文件", len(hits) == 2, paths)
    check("命中的正是那 2 个（改过/无关/回收站都不在内）",
          paths == ["materials/original_scraps/回声_角色卡.md",
                    "materials/raw/潮神_角色卡.md"], paths)
    check("结果按 path 排序", paths == sorted(paths), paths)
    check("带 sample_rel（样例内的相对路径）与 sha256",
          all(h.get("sample_rel") in ("materials/潮神_角色卡.md",
                                      "materials/回声_角色卡.md")
              and len(h.get("sha256", "")) == 64 for h in hits), hits)
    check("路径一律 POSIX 相对串（无反斜杠、不以 / 开头）",
          all("\\" not in p and not p.startswith("/") for p in paths), paths)

    check("样例目录缺失 → []", fm.detect_sample_matches(ws, tmp / "nope") == [])
    check("sample_root=None → []", fm.detect_sample_matches(ws, None) == [])
    check("工作区没有 materials/ → []",
          fm.detect_sample_matches(tmp / "ws_empty", sample) == [])

    # 反证：把用户卡改回与样例一致 → 立刻命中（检测只认内容，不认文件名）
    # ⚠️ 必须 write_bytes：write_text 在 Windows 会把 \n 翻成 \r\n，逐字节比对必然不中
    user_card = ws / "materials" / "raw" / "我自己的卡_角色卡.md"
    user_card.write_bytes(SAMPLE_FILES["materials/回声_角色卡.md"].encode("utf-8"))
    check("改写成样例内容即命中（判据是内容不是文件名）",
          len(fm.detect_sample_matches(ws, sample)) == 3,
          [h["path"] for h in fm.detect_sample_matches(ws, sample)])


# ============================================================ 4. 清理
def case_clean(tmp):
    print("\n【4】清理：只动匹配、保相对路径、越界拒绝、幂等")
    sample = build_tree(tmp / "examples4" / "sample-book", SAMPLE_FILES)
    ws = tmp / "ws4"
    build_tree(ws, {
        "materials/raw/潮神_角色卡.md": SAMPLE_FILES["materials/潮神_角色卡.md"],
        "materials/raw/回声_角色卡.md": SAMPLE_FILES["materials/回声_角色卡.md"],
        "materials/raw/我自己的卡_角色卡.md": "# 自创角色\n",
        "materials/original_scraps/碎句.txt": "用户私人碎片\n",
        "data/state/progress.json": '{"stages": {"1": {"status": "done"}}}',
        "config/project.yaml": 'book:\n  name: 旧书\n',
    })
    hits = fm.detect_sample_matches(ws, sample)
    check("清理前检出 2 个", len(hits) == 2, [h["path"] for h in hits])

    res = fm.clean_factory(ws, hits)
    moved = res.get("moved") or []
    trash = Path(res.get("trash_dir") or "")
    check("恰好移动了 2 个命中文件", len(moved) == 2, moved)
    check("回收站目录名形如 factory__YYYYMMDD_HHMMSS",
          trash.name.startswith("factory__") and len(trash.name) == len("factory__") + 15,
          trash.name)
    check("回收站位于 data/books/_trash/ 下",
          trash.parent.name == "_trash" and trash.parent.parent.name == "books", str(trash))
    check("**保持原相对路径**（materials/raw/... 原样落在回收站里）",
          (trash / "materials/raw/潮神_角色卡.md").is_file()
          and (trash / "materials/raw/回声_角色卡.md").is_file(),
          sorted(str(p.relative_to(trash)) for p in trash.rglob("*") if p.is_file()))
    check("工作区里的命中文件已不在", not (ws / "materials/raw/潮神_角色卡.md").exists())
    check("**未匹配文件原地不动**",
          (ws / "materials/raw/我自己的卡_角色卡.md").is_file()
          and (ws / "materials/original_scraps/碎句.txt").is_file())
    check("**progress.json 不动**", (ws / "data/state/progress.json").is_file()
          and "done" in (ws / "data/state/progress.json").read_text(encoding="utf-8"))
    check("config/ 不动", (ws / "config/project.yaml").is_file())
    check("清理后再检测 → 0 命中", fm.detect_sample_matches(ws, sample) == [])

    # 幂等：同一批命中再跑一次
    res2 = fm.clean_factory(ws, hits)
    check("重复调用幂等（已移走的跳过，moved 为空）", res2.get("moved") == [],
          res2.get("moved"))
    check("幂等后回收站文件仍完整",
          (trash / "materials/raw/潮神_角色卡.md").is_file())

    # 硬边界：materials/ 之外一律拒绝（一个都不动）
    before = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
    for bad in ({"path": "config/project.yaml"},
                {"path": "data/state/progress.json"},
                {"path": "../materials/x.md"},
                {"path": "C:/windows/system.ini"}):
        try:
            fm.clean_factory(ws, [bad])
            check("越界路径拒绝: %s" % bad["path"], False, "没有抛错")
        except ValueError as e:
            check("越界路径拒绝: %s" % bad["path"], "materials/" in str(e), str(e)[:120])
    after = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if p.is_file())
    check("越界尝试后工作区零改动", before == after)

    # 空清单 → 空结果（不建回收站也不报错）
    res3 = fm.clean_factory(ws, [])
    check("空命中列表 → moved 为空", res3.get("moved") == [], res3)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="nf_factory_"))
    try:
        case_sha_and_records(tmp)
        case_manifest_roundtrip(tmp)
        case_detect(tmp)
        case_clean(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + "、".join(FAIL))
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
