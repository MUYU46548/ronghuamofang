# -*- coding: utf-8 -*-
"""副产物管理台自检（`scripts/artifacts.py`，零 LLM、零网络，2026-10-03）。

用户第 4 条："非报告类副产物找个专门的地方丢，用户自己决定复用、导出或清理。"

本用例守四件事：
1. **名录不漏不越界**：每个 id 唯一、路径是相对项目根的**相对**路径（不许 `..` 逃逸）、
   每条都写清「是什么 / 怎么复用 / 安全等级」，等级只在 safe|backup|keep 里取；
2. **keep 不许清**：`--clean` 对 keep 等级一律拒绝，并把"该用哪个工具"指出来
   （快照用 snapshot.py、归档书用 switch_book.py）；
3. **默认 dry-run**：不加 `--yes` 一个文件都不许删（与 `snapshot.py --restore` 同惯例）；
4. **导出真的能用**：`--export` 打出的 zip 里能找到登记的文件，且**不改动源文件**。

全部在临时根上跑（复制脚本进临时根 → `--root` 指向它），真实工作区零触碰。

用法：python tests/unit/test_artifacts.py
"""
import io
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def build_root():
    """临时根：脚本 + 几个真实的副产物目录（safe / backup / keep 各一份）。"""
    root = Path(tempfile.mkdtemp(prefix="nf_artifacts_"))
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "artifacts.py", root / "scripts" / "artifacts.py")
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    (root / "data" / "state" / "tasks").mkdir(parents=True)
    for i in range(3):
        (root / "data" / "state" / "tasks" / ("task%d.md" % i)).write_text(
            "任务 %d" % i, encoding="utf-8")
    (root / "prompts" / "history").mkdir(parents=True)
    (root / "prompts" / "history" / "stage4_20260101.md").write_text("旧模板", encoding="utf-8")
    (root / "data" / "books" / "旧书").mkdir(parents=True)
    (root / "data" / "books" / "旧书" / "chapter.md").write_text("正文", encoding="utf-8")
    (root / "data" / "outline").mkdir(parents=True)
    (root / "data" / "outline" / "review_report.json").write_text("{}", encoding="utf-8")
    return root


def run(root, *args):
    return subprocess.run([PY, str(root / "scripts" / "artifacts.py"), "--root", str(root),
                           *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)


def case_registry():
    print("\n【1】名录本身：唯一、相对、写清用途、等级合法")
    src = (REPO / "scripts" / "artifacts.py").read_text(encoding="utf-8")
    sys.path.insert(0, str(REPO / "scripts"))
    import importlib
    mod = importlib.import_module("artifacts")
    importlib.reload(mod)
    kinds = list(mod.ARTIFACT_KINDS)
    ids = [k["id"] for k in kinds]
    check("id 唯一", len(ids) == len(set(ids)), ids)
    check("每条都有 what/reuse/grade/path",
          all(all(k.get(f) for f in ("what", "reuse", "grade", "path")) for k in kinds))
    check("等级只在 safe|backup|keep 里取",
          all(k["grade"] in mod.GRADES for k in kinds), sorted({k["grade"] for k in kinds}))
    check("路径都是相对路径且不逃逸项目根",
          all(not Path(k["path"]).is_absolute() and ".." not in Path(k["path"]).parts
              for k in kinds), [k["path"] for k in kinds])
    check("三种等级都真的有登记项（分类不是摆设）",
          {k["grade"] for k in kinds} == set(mod.GRADES))
    check("源码里没有把私人绝对路径写死（路径全走 --root/相对）",
          "E:\\" not in src and "/home/" not in src)


def case_list_and_json():
    print("\n【2】--list / --json：看得见（含不存在的项也不崩）")
    root = build_root()
    try:
        p = run(root)
        check("--list 退 0", p.returncode == 0, p.stdout[-200:])
        check("列出了 safe 项（tasks）", "tasks" in p.stdout)
        check("列出了 keep 项（archived_books）", "archived_books" in p.stdout)
        check("标出了安全等级（[可清]/[备份]/[勿删]）",
              "[可清]" in p.stdout and "[备份]" in p.stdout and "[勿删]" in p.stdout)
        check("不存在的项标「暂无」而不是报错", "（暂无）" in p.stdout)
        check("给了清理与导出入口",
              "--clean" in p.stdout and "--export" in p.stdout)

        pj = run(root, "--json")
        import json
        d = json.loads(pj.stdout)
        check("--json 可解析且带 root/kinds/registry",
              d.get("root") and isinstance(d.get("kinds"), list) and d.get("registry"))
        tasks = [k for k in d["kinds"] if k["id"] == "tasks"][0]
        check("--json 报出真实文件数（3）", tasks["files"] == 3, tasks)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_clean_guards():
    print("\n【3】清理守则：keep 拒绝 / 默认 dry-run / --yes 才真删")
    root = build_root()
    try:
        before = (root / "data" / "books" / "旧书" / "chapter.md").read_text(encoding="utf-8")
        p = run(root, "--clean", "archived_books")
        check("keep 等级 → 拒绝且退非零", p.returncode != 0 and "拒绝清理" in p.stdout,
              p.stdout[-200:])
        check("拒绝时指出该用哪个工具（switch_book）", "switch_book" in p.stdout, p.stdout)
        check("拒绝后文件仍在",
              (root / "data" / "books" / "旧书" / "chapter.md").read_text(encoding="utf-8")
              == before)

        p = run(root, "--clean", "tasks")
        check("不加 --yes → dry-run 退 0", p.returncode == 0, p.stdout[-200:])
        check("dry-run 明说没删东西", "dry-run" in p.stdout, p.stdout[:120])
        check("dry-run 后文件仍在",
              len(list((root / "data" / "state" / "tasks").glob("*.md"))) == 3)

        p = run(root, "--clean", "tasks", "--keep-last", "1", "--yes")
        check("--yes 真删且退 0", p.returncode == 0, p.stdout[-200:])
        left = sorted(f.name for f in (root / "data" / "state" / "tasks").glob("*.md"))
        check("--keep-last 1 → 只留 1 个", len(left) == 1, left)

        p2 = run(root, "--clean", "prompts_history", "--yes")
        check("backup 等级允许清理（但要显式 --yes）", p2.returncode == 0, p2.stdout[-160:])
        check("backup 文件确实被删",
              not list((root / "prompts" / "history").glob("*.md")))
        check("keep 的其它项不受影响",
              (root / "data" / "outline" / "review_report.json").exists())
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_export():
    print("\n【4】导出：zip 里有登记的文件，且不动源文件")
    root = build_root()
    out = Path(tempfile.mkdtemp(prefix="nf_artifacts_out_"))
    try:
        p = run(root, "--export", "tasks,archived_books", "--to", str(out))
        check("导出退 0", p.returncode == 0, p.stdout[-200:])
        zips = list(out.glob("*.zip"))
        check("生成了一个 zip", len(zips) == 1, [z.name for z in zips])
        names = zipfile.ZipFile(zips[0]).namelist() if zips else []
        check("zip 里有 tasks 的文件",
              any(n.startswith("data/state/tasks/") for n in names), names[:4])
        check("zip 里也有归档书的文件",
              any("data/books/" in n for n in names), names[:6])
        check("源文件未被改动",
              len(list((root / "data" / "state" / "tasks").glob("*.md"))) == 3
              and (root / "data" / "books" / "旧书" / "chapter.md").exists())

        p = run(root, "--export", "不存在的id", "--to", str(out))
        check("未知 id → 退非零 + 列出可用 id",
              p.returncode != 0 and "可用" in p.stdout, p.stdout[:160])
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def main():
    print("=" * 62)
    print("  副产物管理台自检")
    print("=" * 62)
    case_registry()
    case_list_and_json()
    case_clean_guards()
    case_export()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
