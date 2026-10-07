# -*- coding: utf-8 -*-
"""`/factory/list` + `/factory/clean` 的 HTTP 端到端自检（0.6.7 出厂/用户数据分治）。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起 nf_api
（--allow-fake + --root），素材与样例夹具也全建在临时目录里。

覆盖：
  GET  /factory/list   200 形状（ok / manifest / matches / sample_root_exists）
                       + **零副作用**（盘点前后工作区逐字节不变）
  POST /factory/clean  缺 confirm → 400 且给可行动提示（不落地任何改动）
  POST /factory/clean  confirm → 200，命中文件真进 data/books/_trash/factory__<ts>/
                       （保持原相对路径），**改过的 / 无关的 / progress.json 不动**，
                       回读 /factory/list 命中数归零
  守卫                 /factory/clean 在 agent_guard.FORBIDDEN_IN_AGENT_MODE 内、
                       /factory/list 不在（只读不拦），agent_mode 关闭下正常走通

用法：python tests/http/test_factory_api_http.py
"""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
PORT = 8941
BASE = "http://127.0.0.1:%d" % PORT

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "http"))

PASS, FAIL = [], []

# 样例随包内容（与工作区里那两张卡逐字节一致）
SMP_A = "# 潮神\n出厂示例卡 A\n"
SMP_B = "# 回声\n出厂示例卡 B\n"
SMP_C = "# 潮崩\n出厂示例卡 C\n"


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def _force_agent_mode_off(system_yaml: Path):
    """把临时根的 gates.agent_mode 改为 false（按行定向替换，保留注释）。"""
    p = Path(system_yaml)
    if not p.exists():
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    out, replaced = [], False
    for ln in lines:
        if ln.strip().startswith("agent_mode:"):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append(indent + "agent_mode: false   # 测试环境强制标准模式")
            replaced = True
        else:
            out.append(ln)
    if replaced:
        p.write_text("\n".join(out) + "\n", encoding="utf-8")


def req(method, path, body=None, timeout=60):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {}
        return e.code, payload


def w(p: Path, text: str):
    """写 UTF-8 原字节（不用 write_text：Windows 下它会把 \\n 翻成 \\r\\n，
    逐字节比对的夹具就再也不是「逐字节一致」了）。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_factory_api_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    _force_agent_mode_off(tmp / "config" / "system.yaml")

    # 样例随包（检测的比对真源）：ROOT/examples/sample-book/materials/*.md
    w(tmp / "examples" / "sample-book" / "materials" / "潮神_角色卡.md", SMP_A)
    w(tmp / "examples" / "sample-book" / "materials" / "回声_角色卡.md", SMP_B)
    w(tmp / "examples" / "sample-book" / "materials" / "潮崩_概念卡.md", SMP_C)
    w(tmp / "examples" / "sample-book" / "project.yaml", "book:\n  name: 雾港核心\n")

    # 工作区素材：2 张逐字节一致 + 1 张改过 + 1 张无关
    w(tmp / "materials" / "raw" / "潮神_角色卡.md", SMP_A)
    w(tmp / "materials" / "raw" / "回声_角色卡.md", SMP_B)
    w(tmp / "materials" / "raw" / "潮崩_概念卡.md", "# 潮崩\n用户改过一个字\n")
    w(tmp / "materials" / "raw" / "我自己的卡_角色卡.md", "# 自创角色\n")
    w(tmp / "data" / "state" / "progress.json",
      '{"stages": {"1": {"status": "done"}}}')
    w(tmp / "data" / "outline" / "global.md", "# 大纲\n")

    # 出厂清单（播种账本）：供 GET /factory/list 的 manifest 视图断言
    from utils.factory_manifest import write_manifest
    write_manifest(tmp, {"scripts/nf_api.py": "a" * 64,
                         "prompts/stage1_material.md": "b" * 64}, seed_version=22)
    return tmp


def snap(root: Path):
    """工作区全部文件的 {相对路径: sha} 快照（判「零副作用」用）。"""
    import hashlib
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out[str(p.relative_to(root)).replace("\\", "/")] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return out


def main():
    tmp = build_project()
    log = io.open(tmp / "nf_api.log", "w", encoding="utf-8")
    env = dict(__import__("os").environ)
    env.pop("NF_ROOT", None)            # 用户级 NF_ROOT 会把临时根钉回真实仓库
    env.pop("NF_SAMPLE_ROOT", None)     # 样例目录必须由本测试的临时夹具说了算
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake", "--root", str(tmp)],
                            cwd=str(tmp), stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        for _ in range(60):
            time.sleep(0.5)
            try:
                if req("GET", "/health", timeout=3)[0] == 200:
                    break
            except Exception:
                pass
        else:
            print("服务未能启动，见 " + str(tmp / "nf_api.log"))
            return 1

        print("=== 1. GET /factory/list：200 与形状 ===")
        before = snap(tmp)
        code, d = req("GET", "/factory/list")
        check("GET /factory/list → 200 + ok", code == 200 and d.get("ok") is True,
              (code, str(d)[:200]))
        check("manifest 视图含 seed_version / count",
              isinstance(d.get("manifest"), dict)
              and d["manifest"].get("seed_version") == 22
              and d["manifest"].get("count") == 2, d.get("manifest"))
        check("sample_root_exists=true（样例随包目录被找到）",
              d.get("sample_root_exists") is True and d.get("sample_root"), d.get("sample_root"))
        check("命中 2 个逐字节一致的文件（改过的 / 无关的不算）",
              d.get("match_count") == 2 and len(d.get("matches") or []) == 2,
              d.get("matches"))
        check("命中的都是 materials/ 开头的 POSIX 相对路径",
              all(m.get("path", "").startswith("materials/") and "\\" not in m["path"]
                  for m in d.get("matches") or []), d.get("matches"))
        check("每条命中带 sample_rel 与 sha256",
              all(m.get("sample_rel") and len(m.get("sha256", "")) == 64
                  for m in d.get("matches") or []), d.get("matches"))
        after = snap(tmp)
        check("**零副作用**：盘点前后工作区逐字节不变", before == after,
              sorted(set(before) ^ set(after)))
        check("盘点没有写/建回收站",
              not (tmp / "data" / "books" / "_trash").exists())

        print("\n=== 2. POST /factory/clean 缺 confirm → 400（不落地） ===")
        code, d = req("POST", "/factory/clean", {})
        check("缺 confirm → 400", code == 400 and d.get("ok") is False, (code, str(d)[:200]))
        check("400 文案可行动（点名确认与 confirm 字段）",
              "confirm" in str(d.get("error")) and "勾选" in str(d.get("error")),
              str(d.get("error"))[:200])
        check("400 后工作区零改动", snap(tmp) == before)
        check("400 后没有建回收站", not (tmp / "data" / "books" / "_trash").exists())

        print("\n=== 3. confirm → 真移进 _trash（保相对路径） ===")
        code, d = req("POST", "/factory/clean", {"confirm": True})
        check("confirm → 200 + ok", code == 200 and d.get("ok") is True, (code, str(d)[:240]))
        moved = sorted(d.get("moved") or [])
        check("moved 正是那 2 个命中文件",
              moved == ["materials/raw/回声_角色卡.md", "materials/raw/潮神_角色卡.md"], moved)
        trash = Path(d.get("trash_dir") or "")
        check("trash_dir 在 data/books/_trash/factory__* 下",
              trash.is_dir() and trash.parent.name == "_trash"
              and trash.name.startswith("factory__"), str(trash))
        check("回收站里保持原相对路径",
              (trash / "materials" / "raw" / "潮神_角色卡.md").is_file()
              and (trash / "materials" / "raw" / "回声_角色卡.md").is_file())
        check("工作区里的命中文件已不在",
              not (tmp / "materials" / "raw" / "潮神_角色卡.md").exists()
              and not (tmp / "materials" / "raw" / "回声_角色卡.md").exists())
        check("改过的样例卡**不动**（那是用户数据）",
              (tmp / "materials" / "raw" / "潮崩_概念卡.md").read_bytes()
              .decode("utf-8") == "# 潮崩\n用户改过一个字\n")
        check("无关的用户卡不动",
              (tmp / "materials" / "raw" / "我自己的卡_角色卡.md").is_file())
        check("progress.json 不动",
              json.loads((tmp / "data" / "state" / "progress.json").read_text("utf-8"))
              .get("stages", {}).get("1", {}).get("status") == "done")
        check("message 带回收站路径（用户下一步就问能不能捞回）",
              "factory__" in str(d.get("message")), str(d.get("message"))[:200])

        print("\n=== 4. 回读 /factory/list ===")
        code, d2 = req("GET", "/factory/list")
        check("清理后命中数归零", code == 200 and d2.get("match_count") == 0
              and (d2.get("matches") or []) == [], (code, d2.get("match_count")))
        check("清单不受清理影响（账本照旧）",
              (d2.get("manifest") or {}).get("count") == 2, d2.get("manifest"))

        print("\n=== 5. 守卫：清理只许用户本人（只读盘点不拦） ===")
        from utils import agent_guard
        check("/factory/clean 在 FORBIDDEN_IN_AGENT_MODE 内",
              agent_guard.is_forbidden_in_agent_mode("/factory/clean") is True)
        check("/factory/list 不在禁用名单（只读不拦）",
              agent_guard.is_forbidden_in_agent_mode("/factory/list") is False)
        check("本例 agent_mode 关闭，端点是正常走通而非被豁免放过（200 已实证）",
              agent_guard.agent_mode_enabled(
                  {"gates": {"agent_mode": False}}) is False)

        print("=" * 62)
        print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
        if FAIL:
            print("失败项: " + "、".join(FAIL))
        print("=" * 62)
        return 1 if FAIL else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
