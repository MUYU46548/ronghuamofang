# -*- coding: utf-8 -*-
"""首跑向导出厂预设端点自检（GET /factory/presets、POST /factory/preset/apply）。零 LLM。

为什么这些断言重要（roadmap v0.20「首跑向导合并」+ 用户 2026-10-21 指令
「试跑我自己会做…直接做剩余待办」）：

- **试跑要有素材可写**：向导选中预设即铺设配套素材 + 配置，否则首跑是空跑
  （AGENTS.md：materials/raw 为空时提醒用户，不要空跑）；
- **铺设是破坏性动作**：会快照 → 归档当前项目 → 铺素材 → 写 project.yaml。
  与 /project/create 完全同族，故必须 ①列入 agent_guard 禁用清单（Agent 一条
  命令就能搬空用户工作区）②端点自身再要 GUI 双因子；
- **清单只读**：GET 只列非隐藏预设，不写任何字节。

覆盖：
  GET  /factory/presets       清单只读、只列非隐藏
  POST /factory/preset/apply  双因子闸 + 真铺设（素材落盘 + 配置写入）
  POST /project/create        **铺设后创建（preset_applied）不得把刚铺的素材归档走**
                              —— 2026-10-24 用户实测：向导铺设→创建→试跑 9 秒即败
                              （materials/raw 被归档进书档，阶段1 报「无素材」）
  POST /project/create        空骨架工作区创建后**不得**堆出空壳归档
                              「示例书名（待填写）」（占位名不是一本书）
  POST /project/create        冷启动（占位名 + 手放素材 + 无数据）创建 → 素材留在工作区

**不碰真实仓库**：复制 scripts/config/prompts/examples 到临时根起 nf_api。

用法：python tests/http/test_factory_preset_api_http.py
"""
import io
import json
import os
import re
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
sys.path.insert(0, str(ROOT / "scripts"))
PY = sys.executable
PORT = 8937
BASE = "http://127.0.0.1:%d" % PORT
TEST_TOKEN = "nf-test-gui-token-" + "0123456789abcdef" * 2
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=60, gui=False, source=None, token=None):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    headers = {"Content-Type": "application/json"}
    if gui:
        headers["X-Mofang-Source"] = "gui"
        headers["X-Mofang-Token"] = TEST_TOKEN
    if source:
        headers["X-Mofang-Source"] = source
        if token is not None:
            headers["X-Mofang-Token"] = token
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {}
        return e.code, payload


def _force_agent_mode_off(system_yaml: Path):
    """把临时根的 gates.agent_mode 改为 false（按行定向替换，保留注释）。

    本仓 system.yaml 默认 agent_mode=true（用户实机开着），HTTP 测试的裸请求
    （无 GUI 双因子）会被 Agent 守卫 403 —— 那时测的是守卫，不是被测流程。
    """
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


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_fpreset_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    # ⚠ config/project.yaml **不随仓库分发**（CI 干净检出没有，本机才有）——
    # 铺设要走 set_book_fields 定向改写它，缺文件 = FileNotFoundError → 500
    # （2026-10-23 起 CI 连红的其中一条就是这么来的；同坑见 test_factory_api_http）。
    # 本地验证 CI 形态：NF_TEST_USE_EXAMPLE_CONFIG=1 强制走这条兜底。
    if os.environ.get("NF_TEST_USE_EXAMPLE_CONFIG") or not (tmp / "config" / "project.yaml").exists():
        shutil.copy2(ROOT / "config" / "project.yaml.example",
                     tmp / "config" / "project.yaml")
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    # 出厂预设在 examples/presets/（打包态由 payload 注入）——夹具必须带上，
    # 否则 GET 返回空清单，「试跑有素材可写」这条判据无从验起。
    shutil.copytree(ROOT / "examples", tmp / "examples",
                    ignore=shutil.ignore_patterns("__pycache__"))
    _force_agent_mode_off(tmp / "config" / "system.yaml")
    for d in ("data/state", "data/outline", "materials/raw"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    return tmp


def files_of(d):
    """材料目录里的文件名集合（用于比对铺设前后）。"""
    p = Path(d)
    return sorted(x.name for x in p.iterdir()) if p.is_dir() else []


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    from utils import agent_guard
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=dict(os.environ, NF_GUI_TOKEN=TEST_TOKEN))
    try:
        ready = False
        for _ in range(60):
            try:
                code, _h = req("GET", "/health")
                if code == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.5)
        check("nf_api 起来了", ready)
        if not ready:
            return 1

        print("\n【1】GET /factory/presets：清单只读、只列非隐藏")
        code, d = req("GET", "/factory/presets")
        check("GET 200", code == 200 and d.get("ok"), (code, d))
        presets = d.get("presets") or []
        check("清单非空（夹具带了 examples/presets/）", len(presets) > 0, len(presets))
        ids = {p.get("id") for p in presets}
        check("不含隐藏用例（mill-dirty）", "mill-dirty" not in ids, sorted(ids))
        check("每项带 name/genre/chapters（向导卡片要显示）",
              all(k in p for p in presets for k in ("name", "genre", "chapters")), presets[:1])
        before_mats = files_of(tmp / "materials" / "raw")
        code2, _d2 = req("GET", "/factory/presets")
        check("连查两次结果一致（纯读，无副作用）",
              code2 == 200 and files_of(tmp / "materials" / "raw") == before_mats)

        print("\n【2】铺设是破坏性动作：无 GUI 双因子一律 403")
        before_mats = files_of(tmp / "materials" / "raw")
        code, d = req("POST", "/factory/preset/apply", {"id": "rail-end"})
        check("无任何 GUI 头 → 403", code == 403, (code, d))
        code, d = req("POST", "/factory/preset/apply", {"id": "rail-end"},
                      source="gui")
        check("**伪造来源头但无 token → 403**（红队 2026-10-06 复现的绕过路径）",
              code == 403, (code, d))
        check("被拒绝时**素材一个没铺**",
              files_of(tmp / "materials" / "raw") == before_mats,
              files_of(tmp / "materials" / "raw"))

        print("\n【3】列入 agent_guard 禁用清单（判据只有一份，与 do_POST 同源）")
        check("is_forbidden_in_agent_mode('/factory/preset/apply') 为真",
              agent_guard.is_forbidden_in_agent_mode("/factory/preset/apply"))
        check("只读清单不在禁用名单（Agent 可以盘点，不能动手）",
              agent_guard.is_forbidden_in_agent_mode("/factory/presets") is False)

        print("\n【4】缺 id → 400（GUI 双因子齐备时）")
        code, d = req("POST", "/factory/preset/apply", {}, gui=True)
        check("空 body → 400 且点名缺 id", code == 400 and "id" in str(d.get("error")),
              (code, d))

        print("\n【5】真铺设（GUI 双因子）：素材落盘 + 配置写入")
        code, d = req("POST", "/factory/preset/apply", {"id": "rail-end"}, gui=True)
        check("铺设 200", code == 200 and d.get("ok"), (code, d))
        after_mats = files_of(tmp / "materials" / "raw")
        check("素材真的铺进 materials/raw/", len(after_mats) > len(before_mats),
              (before_mats, after_mats))
        check("project.yaml 被写入（书名非出厂占位）",
              "铁轨尽头" in (tmp / "config" / "project.yaml").read_text(encoding="utf-8")
              or "rail" in (tmp / "config" / "project.yaml").read_text(encoding="utf-8").lower())
        check("返回 log 有人可读的铺设轨迹", "铺设" in str(d.get("log", "")) or d.get("log"), d.get("log"))

        print("\n【6】不存在的预设 id → 400（不许静默铺个空的）")
        code, d = req("POST", "/factory/preset/apply", {"id": "no-such-preset"}, gui=True)
        check("未知 id → 400", code == 400, (code, d))

        # ------------------------------------------------------------------
        # 【7】铺设 → 创建（preset_applied）：刚铺的素材必须原样保留
        # 事故现场（2026-10-24 用户实测）：向导第 0 步铺设、第 3 步创建，
        # /project/create 把 materials/（归档根项）连同刚铺的素材一起归档走，
        # 新书阶段 1 9 秒即败：「materials/raw 无素材且无既有设定集」。
        # ------------------------------------------------------------------
        print("\n【7】铺设后创建（preset_applied=true）：素材保留 + 不新增归档 + 阶段1预检非空")
        import yaml
        preset_mats = files_of(tmp / "materials" / "raw")
        check("前置：铺设后 materials/raw 有素材", len(preset_mats) > 0, preset_mats)
        books_dir = tmp / "data" / "books"
        books_before = sorted(p.name for p in books_dir.iterdir()) if books_dir.exists() else []
        code, d = req("POST", "/project/create", {
            "name": "预设新书", "genre": "悬疑", "chapters": 8,
            "archive_current": False, "preset_applied": True,
        })
        check("创建 200（铺设时已归档过旧项目，archive_current=false 不该被拦）",
              code == 200 and d.get("ok"), (code, d))
        check("materials/raw 素材一个没少（事故现场：被归档进书档 → 阶段1 必败）",
              files_of(tmp / "materials" / "raw") == preset_mats,
              (preset_mats, files_of(tmp / "materials" / "raw")))
        books_after = sorted(p.name for p in books_dir.iterdir()) if books_dir.exists() else []
        check("没有新增任何归档条目（工作区本身就是新项目）",
              books_after == books_before, (books_before, books_after))
        book = (yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
                or {}).get("book") or {}
        check("书名/章数改写落地（只改 config，不动工作区）",
              book.get("name") == "预设新书" and book.get("chapters") == 8, book)
        check("出厂标记仍在（init_empty 会清它 → preset_applied 必须跳过 init_empty）",
              (tmp / "data" / "state" / "factory_project.json").exists())
        # 阶段 1 的失败判据就是「scan_materials 空 + 无既有设定集」——直接打这条判据
        from stage1_consolidate import scan_materials
        manifest = scan_materials(tmp / "materials" / "raw",
                                  tmp / "data" / "setting" / "normalized")
        check("阶段1 预检输入非空（scan_materials 命中素材卡，不再「无素材」必败）",
              len(manifest) > 0, len(manifest))

        print("\n【8】preset_applied 不绕过参数护栏（非法输入仍 400 且零改动）")
        code, d = req("POST", "/project/create", {
            "name": "坏书", "chapters": 0, "preset_applied": True, "archive_current": False,
        })
        check("章数 0 → 400", code == 400 and not d.get("ok"), (code, d))
        check("素材与书名零改动",
              files_of(tmp / "materials" / "raw") == preset_mats
              and (yaml.safe_load((tmp / "config" / "project.yaml").read_text(encoding="utf-8"))
                   or {}).get("book", {}).get("name") == "预设新书")

        print("\n【9】对照：不带 preset_applied 的正常归档路径仍然完好（真书照归档）")
        code, d = req("POST", "/project/create", {
            "name": "对照书", "chapters": 5, "archive_current": True,
        })
        check("创建 200", code == 200 and d.get("ok"), (code, d))
        arch = books_dir / "预设新书"
        check("预设项目按真书名归档（含素材）",
              (arch / "materials" / "raw").is_dir()
              and len(files_of(arch / "materials" / "raw")) == len(preset_mats),
              files_of(arch / "materials" / "raw") if (arch / "materials" / "raw").exists() else None)
        check("**没有**堆出空壳归档「示例书名（待填写）」",
              not (books_dir / "示例书名（待填写）").exists(),
              sorted(p.name for p in books_dir.iterdir()) if books_dir.exists() else None)

        print("\n【10】冷启动：占位书名 + 手放素材 + 无数据 → 素材必须留在工作区")
        # 把工作区摆成「全新安装、用户只放了素材、还没起过书名」的形态
        proj = tmp / "config" / "project.yaml"
        proj.write_text(re.sub(r'(?m)^(\s*name:\s*).*$',
                               r'\1"示例书名（待填写）"',
                               proj.read_text(encoding="utf-8"), count=1),
                        encoding="utf-8")
        (tmp / "materials" / "raw").mkdir(parents=True, exist_ok=True)
        (tmp / "materials" / "raw" / "手放卡.md").write_text("# 手放的素材卡\n正文\n",
                                                             encoding="utf-8")
        code, d = req("POST", "/project/create", {
            "name": "真名书", "chapters": 4, "archive_current": False,
        })
        check("创建 200（工作区没有数据，不该被归档护栏拦下）",
              code == 200 and d.get("ok"), (code, d))
        check("手放的素材留在工作区（占位名不是书，不许被自动归档搬走）",
              (tmp / "materials" / "raw" / "手放卡.md").exists(),
              files_of(tmp / "materials" / "raw"))
        check("仍然没有空壳归档「示例书名（待填写）」",
              not (books_dir / "示例书名（待填写）").exists(),
              sorted(p.name for p in books_dir.iterdir()) if books_dir.exists() else None)
        book = (yaml.safe_load(proj.read_text(encoding="utf-8")) or {}).get("book") or {}
        check("新书名落地", book.get("name") == "真名书", book.get("name"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
