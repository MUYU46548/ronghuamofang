# -*- coding: utf-8 -*-
"""project.yaml 重复键 / 格式错误必须**报错**（P2，2026-10-03）。零 LLM。

## 为什么

PyYAML 对**重复键静默取后值**，不报错、不告警。这个坑本项目已经踩过两次：

1. `project.yaml` 的 `user_outline` 写了两遍 → 真实大纲被空值覆盖
   （2026-09-28，已由 `utils.project_config._UniqueKeyLoader` 修掉）；
2. **`switch_book.current_book_name` 漏网**：它一直用裸 `yaml.safe_load` 取
   `book.name`。换书是**破坏性操作**（归档当前 + 恢复目标），书名读错的后果是
   **把内容归档到错误书名的目录下**，而界面上一切正常 —— 这正是「看起来配好了、
   实际没生效」的典型形态。

本用例守三件事：
- 重复键 → `current_book_name()` **抛错**（不是静默取后值，也不是吞成空书名）；
- 缺失文件仍是合法的「未初始化」状态（返回 ""），不误报；
- CLI 层把错误收敛成**可行动的一行话**（不是 traceback），且**不执行归档**。

用法：python tests/unit/test_project_yaml_dup_keys.py
"""
import ast
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


DUP_YAML = ('book:\n  name: "第一本书"\n  name: "第二本书"\n  chapters: 3\n')
GOOD_YAML = 'book:\n  name: "正常的书"\n  chapters: 3\n'


def build_root(project_yaml=None, progress=None):
    """搭临时项目根。

    ⚠️⚠️ **必须把 switch_book.py 复制进临时根再跑**（2026-10-03 实测教训）：
    `switch_book.PROJECT_ROOT = Path(__file__).resolve().parent.parent` —— 它认的是
    **脚本文件所在位置**，不是 CWD！所以「chdir 到临时目录再跑真实仓库的
    scripts/switch_book.py」会**直接归档真实工作区**（本轮真踩到：测试把
    data/ + config/ + materials/ 全搬进了 data/books/{真书名}/，已当场恢复并逐文件
    核对无损）。与 test_switch_book_scope.py 同一套做法：复制脚本，让 __file__ 落在临时根。
    """
    tmp = Path(tempfile.mkdtemp(prefix="nf_dupkey_"))
    (tmp / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "switch_book.py", tmp / "scripts" / "switch_book.py")
    # utils/ 必需：归档后重置 project.yaml 走 utils.project_config.set_book_fields
    shutil.copytree(ROOT / "scripts" / "utils", tmp / "scripts" / "utils",
                    ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    (tmp / "config").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True)
    (tmp / "data" / "books").mkdir(parents=True)
    (tmp / "config" / "system.yaml").write_text("engine: direct\n", encoding="utf-8")
    if project_yaml is not None:
        (tmp / "config" / "project.yaml").write_text(project_yaml, encoding="utf-8")
    if progress is not None:
        (tmp / "data" / "state" / "progress.json").write_text(
            json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    return tmp


def run_in(tmp, code):
    """在临时根跑一段 python（sys.path 指向临时根的 scripts/）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(tmp), env=env,
                          timeout=120)
    return proc


PROBE = r'''
import sys, os
sys.path.insert(0, r"{root}/scripts")
os.chdir(r"{root}")
import switch_book as sb
# 护栏：确认本进程操作的是**临时根**（realpath 比较，容错大小写/短名）
assert os.path.realpath(str(sb.PROJECT_ROOT)) == os.path.realpath(r"{root}"), \
    "PROJECT_ROOT 未落在临时根，拒绝继续（防误操作真实工作区）: " + str(sb.PROJECT_ROOT)
try:
    name = sb.current_book_name()
    print("__RESULT__" + repr({{"ok": True, "name": name}}))
except Exception as e:
    print("__RESULT__" + repr({{"ok": False, "err": type(e).__name__ + ": " + str(e)}}))
'''


def probe(root):
    p = run_in(root, PROBE.format(root=str(root)))
    out = p.stdout or ""
    if "__RESULT__" not in out:
        return {"ok": None, "stdout": out[-400:], "stderr": (p.stderr or "")[-400:]}
    return ast.literal_eval(out.split("__RESULT__", 1)[1].splitlines()[0])


# ============================================================ 1. 重复键报错
def case_duplicate_keys_raise():
    print("\n【1】project.yaml 重复键 → 报错（不许静默取后值）")
    tmp = build_root(project_yaml=DUP_YAML)
    try:
        r = probe(tmp)
        check("current_book_name 抛错而非返回「第二本书」", r.get("ok") is False, r)
        check("错误类型是 ValueError（可被上层收敛成一行话）",
              "ValueError" in str(r.get("err")), r.get("err"))
        check("错误里点名重复键所在的文件", "project.yaml" in str(r.get("err")), r.get("err"))
        check("错误里带「Duplicate key」原文（PyYAML loader 的判据）",
              "Duplicate key" in str(r.get("err")), r.get("err"))
        check("错误给出修法与体检入口",
              "重复键" in str(r.get("err")) and "nfctl.py check" in str(r.get("err")),
              r.get("err"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_progress_does_not_mask_corruption():
    print("\n【2】progress.json 有书名时：仍然校验 project.yaml（坏配置不许静默）")
    tmp = build_root(project_yaml=DUP_YAML, progress={"project": "进行中的书"})
    try:
        r = probe(tmp)
        check("坏配置照样报错（同一份配置不该看运气静默）", r.get("ok") is False, r)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tmp = build_root(project_yaml=GOOD_YAML, progress={"project": "进行中的书"})
    try:
        r = probe(tmp)
        check("配置正常时 progress.json 提供的书名优先（按其文档语义）",
              r.get("ok") is True and r.get("name") == "进行中的书", r)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_good_and_missing():
    print("\n【3】反证：正常配置与「未初始化」都不许误报")
    tmp = build_root(project_yaml=GOOD_YAML)
    try:
        r = probe(tmp)
        check("正常 project.yaml → 读出书名", r.get("ok") is True
              and r.get("name") == "正常的书", r)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tmp = build_root(project_yaml=None)
    try:
        r = probe(tmp)
        check("没有 project.yaml → 返回空串（合法状态，不是错误）",
              r.get("ok") is True and r.get("name") == "", r)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================ 4. CLI 层
def case_cli_clean_error_and_no_archive():
    print("\n【4】CLI：报错是可行动的一行话，且**不执行归档**")
    tmp = build_root(project_yaml=DUP_YAML)
    try:
        (tmp / "data" / "setting").mkdir(parents=True, exist_ok=True)
        (tmp / "data" / "setting" / "setting.json").write_text("{}", encoding="utf-8")
        # 跑**临时根里的** switch_book.py（见 build_root 的 ⚠️ 说明：它认 __file__ 不认 CWD）
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [sys.executable, str(tmp / "scripts" / "switch_book.py"), "--archive", "--yes"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(tmp), env=env, timeout=120)
        check("退出码非零", proc.returncode == 1, proc.returncode)
        check("输出是「拒绝执行 + 原因」，不是 traceback",
              "拒绝执行" in proc.stdout and "Traceback" not in proc.stdout,
              proc.stdout[-400:])
        check("输出里能看到「Duplicate key」这类可定位信息",
              "Duplicate key" in proc.stdout or "重复键" in proc.stdout,
              proc.stdout[-300:])
        books = list((tmp / "data" / "books").iterdir())
        check("**没有产生任何归档**（破坏性操作被拦在动手之前）", books == [], books)
        check("工作区数据仍在原处（没被搬走）",
              (tmp / "data" / "setting" / "setting.json").exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================ 5. 回读校验
def case_writeback_validation_strict():
    print("\n【5】写后回读校验也必须是严格 loader（源码断言 + 行为）")
    src = (ROOT / "scripts" / "utils" / "project_config.py").read_text(encoding="utf-8")
    check("set_book_fields 回读用 load_project_yaml（不再裸 safe_load）",
          "back = load_project_yaml(PROJECT_YAML) or {}" in src)
    check("switch_book 里不再有裸 safe_load 调用",
          "yaml.safe_load(" not in
          (ROOT / "scripts" / "switch_book.py").read_text(encoding="utf-8"))
    # 行为：把 project.yaml 写成重复键 → set_book_fields 的回读校验必须判失败
    tmp = build_root(project_yaml=DUP_YAML)
    origin = os.getcwd()
    try:
        os.chdir(tmp)
        for mod in ("utils.project_config", "utils.file_io"):
            sys.modules.pop(mod, None)
        import importlib
        import utils.project_config as pcfg
        importlib.reload(pcfg)
        pcfg.PROJECT_YAML = tmp / "config" / "project.yaml"
        pcfg.BACKUP_DIR = tmp / "config" / "history"
        ok, msg = pcfg.set_book_fields({"chapters": 5})
        check("重复键文件上写入 → 回读校验判失败（不假装成功）", ok is False, msg)
        check("失败原因点明 YAML 解析问题", "解析失败" in msg, msg)
    finally:
        os.chdir(origin)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 62)
    print("  project.yaml 重复键检测自检（P2）")
    print("=" * 62)
    case_duplicate_keys_raise()
    case_progress_does_not_mask_corruption()
    case_good_and_missing()
    case_cli_clean_error_and_no_archive()
    case_writeback_validation_strict()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
