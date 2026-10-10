# -*- coding: utf-8 -*-
"""多书归档范围自检（`switch_book.py`）。

## 为什么需要这套用例

2026-10-01 之前 `--archive` 只搬 `data/` 下的 8 项，**`config/` 与 `materials/`
完全不归档** —— 于是"换书"这个动作会静默丢掉 `config/project.yaml`（书名/类型/
章节数）与 `materials/`（素材与素材卡）。用户看到的是"归档成功"，代价下次才发现。

本次把两者纳入范围，同时守住一条底线：**应用级配置不能跟着消失**。
`config/system.yaml`（引擎/模型/预算/vault 路径）描述的是"这台机器怎么跑"，
归档后若不复制回工作区，工作区就缺 `config/system.yaml`，界面直接起不来。

## 判据（全部在**临时项目根**上真实执行，不碰本仓库）

1. 归档后书档有 `config/` 与 `materials/`；`data/` 的 8 项照旧被搬走
2. 归档后**工作区仍有** `config/system.yaml` / `system.local.yaml`（应用级保留）
3. 归档后工作区**不再有** `config/project.yaml` / `materials/`（书档级已走）
4. 恢复后 `config/project.yaml` 与 `materials/` 回来
5. 反证：**老格式归档**（没有 config/ 的）恢复时不报错，且不删掉工作区现有的
   `config/system.yaml` —— 逐项合并而非整体替换

全程离线、零 token。
用法：python tests/unit/test_switch_book_scope.py
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  → {extra}")


def build_root(prefix="nf_sb_"):
    """临时项目根：scripts/switch_book.py + config/ + materials/ + data/。"""
    root = Path(tempfile.mkdtemp(prefix=prefix))
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "switch_book.py", root / "scripts" / "switch_book.py")
    # utils/ 是必需的：归档后要把 project.yaml 重置成骨架，走的是
    # utils.project_config.set_book_fields（与 UI 改写同一判据，不重写一份）
    shutil.copytree(REPO / "scripts" / "utils", root / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    (root / "config").mkdir()
    (root / "config" / "system.yaml").write_text("engine: direct\n", encoding="utf-8")
    (root / "config" / "system.local.yaml").write_text("vault_path: ''\n", encoding="utf-8")
    (root / "config" / "project.yaml").write_text(
        "book:\n  name: 测试书\n  genre: 科幻\n  chapters: 12\n", encoding="utf-8")
    (root / "materials" / "raw").mkdir(parents=True)
    (root / "materials" / "raw" / "素材.md").write_text("素材正文\n", encoding="utf-8")
    (root / "materials" / "vault_links.md").write_text("链接\n", encoding="utf-8")
    (root / "data" / "setting").mkdir(parents=True)
    (root / "data" / "setting" / "setting.json").write_text("{}", encoding="utf-8")
    (root / "data" / "progress.json").write_text(
        json.dumps({"project": "测试书"}, ensure_ascii=False), encoding="utf-8")
    return root


def _gbk_ok(ch):
    """单个字符能否用 cp936 编码（控制台输出纪律的判据）。"""
    try:
        ch.encode("gbk")
        return True
    except Exception:                                       # noqa: BLE001
        return False


def run_switch(root, *args):
    p = subprocess.run([sys.executable, str(root / "scripts" / "switch_book.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(root), timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_switch_from(root, cwd, *args):
    """从**指定 CWD** 跑（默认 root）—— 用来验「项目根取自脚本位置，不是 CWD」这条地雷。"""
    p = subprocess.run([sys.executable, str(root / "scripts" / "switch_book.py"), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(cwd), timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    print("=" * 62)
    print("  多书归档范围自检（离线，临时项目根）")
    print("=" * 62)

    # ---------------- 0. 「要动哪个根」必须说清（事故后加的护栏）----------------
    print("\n【0】从别的目录跑：必须大声报出将操作的根，且不许静默动手")
    root0 = build_root("nf_sb_cwd_")
    try:
        outside = Path(tempfile.mkdtemp(prefix="nf_sb_outside_"))
        try:
            before = sorted(p.name for p in (root0 / "config").iterdir())
            code, out = run_switch_from(root0, outside, "--archive")
            # 路径比对吃两种形式：产品打印 resolve 后的路径（PROJECT_ROOT =
            # Path(__file__).resolve()...），测试手里是 mkdtemp 原始串；
            # CI runner 上两者字符串不等（8.3 短名 / 大小写归一，10-10 假红根因）。
            check("输出里报出**将操作的项目根**",
                  any(f in out for f in (str(root0), str(Path(root0).resolve()))),
                  (root0, out[:200]))
            check("CWD 与项目根不同时必须提示",
                  "与项目根不同" in out
                  and any(f.rstrip("\\/") in out for f in
                          (str(outside), str(Path(outside).resolve()))),
                  (outside, out[:260]))
            check("不加 --yes → 拒绝执行（退非零）", code != 0, code)
            check("被拒绝时**一个文件都没动**",
                  sorted(p.name for p in (root0 / "config").iterdir()) == before)
            check("输出不含非 GBK 字符（cp936 管道下不许在打印阶段崩）",
                  all(_gbk_ok(ch) for ch in out),
                  [ch for ch in out if not _gbk_ok(ch)][:5])
        finally:
            shutil.rmtree(outside, ignore_errors=True)
    finally:
        shutil.rmtree(root0, ignore_errors=True)

    root = build_root()
    try:
        # ---------------- 1. 归档 ----------------
        print("\n【1】归档：config/ 与 materials/ 是否跟着走")
        code, out = run_switch(root, "--archive", "--yes")
        print("  " + out.strip().replace("\n", "\n  "))
        check("归档命令返回 0", code == 0, f"exit={code}")
        dest = root / "data" / "books" / "测试书"
        check("书档目录已建立", dest.is_dir(), str(dest))
        check("书档内出现 config/", (dest / "config").is_dir())
        check("书档内出现 materials/", (dest / "materials").is_dir())
        check("书档内出现 data 产物（setting/ 与 progress.json）",
              (dest / "setting").is_dir() and (dest / "progress.json").exists())
        meta = {}
        try:
            meta = json.loads((dest / "_meta.json").read_text(encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            pass
        check("_meta.json 记录了 root_items 与格式版本",
              meta.get("root_items") == ["config", "materials"] and meta.get("format") == 2,
              str(meta))

        # ---------------- 2. 应用级配置必须留在工作区 ----------------
        print("\n【2】归档后工作区仍可运行（应用级配置保留）")
        check("工作区仍有 config/system.yaml（**不随书走**）",
              (root / "config" / "system.yaml").exists())
        check("工作区仍有 config/system.local.yaml",
              (root / "config" / "system.local.yaml").exists())
        # project.yaml 本体随书走，但工作区必须留一份**骨架**：新建项目走的是
        # 定向改写（读原文件再改），文件不在就直接 FileNotFoundError → 500。
        check("书档保留了原 project.yaml（含原书名）",
              (dest / "config" / "project.yaml").exists()
              and "测试书" in (dest / "config" / "project.yaml").read_text(encoding="utf-8"))
        skel = root / "config" / "project.yaml"
        check("工作区 project.yaml 已重置为空白骨架（旧书名不残留）",
              skel.exists() and "示例书名（待填写）" in skel.read_text(encoding="utf-8")
              and "测试书" not in skel.read_text(encoding="utf-8"),
              skel.read_text(encoding="utf-8")[:160] if skel.exists() else "(文件不存在)")
        check("工作区**不再有** materials/（书档级已归档）",
              not (root / "materials").exists())
        check("工作区 data/ 产物已清空（progress.json 不在）",
              not (root / "data" / "progress.json").exists())

        # ---------------- 3. 恢复 ----------------
        print("\n【3】恢复：书档级内容回到工作区")
        code, out = run_switch(root, "--restore", "测试书", "--yes")
        print("  " + out.strip().replace("\n", "\n  "))
        check("恢复命令返回 0", code == 0, f"exit={code}")
        check("config/project.yaml 已恢复", (root / "config" / "project.yaml").exists())
        check("恢复的 project.yaml 含原书名",
              "测试书" in (root / "config" / "project.yaml").read_text(encoding="utf-8"))
        check("materials/ 已恢复（素材文件在位）",
              (root / "materials" / "raw" / "素材.md").exists())
        check("data/progress.json 已恢复", (root / "data" / "progress.json").exists())
        check("恢复后 config/system.yaml 未被弄丢",
              (root / "config" / "system.yaml").exists())
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # ---------------- 4. 反证：老格式归档不至于毁掉工作区配置 ----------------
    print("\n【4】反证：老格式归档（无 config/）恢复时不得删掉工作区配置")
    root = build_root("nf_sb_old_")
    try:
        old = root / "data" / "books" / "旧书"
        (old / "setting").mkdir(parents=True)
        (old / "setting" / "setting.json").write_text("{}", encoding="utf-8")
        (old / "_meta.json").write_text(
            json.dumps({"book": "旧书", "items": ["setting"], "format": 1},
                       ensure_ascii=False), encoding="utf-8")
        code, out = run_switch(root, "--restore", "旧书", "--yes")
        print("  " + out.strip().replace("\n", "\n  "))
        check("老格式归档仍能恢复（返回 0）", code == 0, f"exit={code}")
        check("**工作区 config/system.yaml 未被老格式恢复删掉**",
              (root / "config" / "system.yaml").exists(),
              "逐项合并失效 —— 书档没有的配置把工作区那份删了")
        # 注意：restore 会先把当前书（测试书）自动归档，所以 project.yaml 理应
        # 被搬进「测试书」书档，而不是留在工作区 —— 这里判的是"没被静默丢掉"。
        check("当前书的 project.yaml 被自动归档收进书档（不是被静默丢掉）",
              (root / "data" / "books" / "测试书" / "config" / "project.yaml").exists(),
              "自动归档没有把当前书的 project.yaml 收进书档")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
