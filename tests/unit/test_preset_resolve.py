# -*- coding: utf-8 -*-
"""预设目录解析链（preset_apply.find_presets_dir）判据。

## 这条锁拦的是什么（2026-10-24 装机实测事故）

打包态主进程把 nf_api 起在**工作区播种的 scripts 副本**上
（`console/main/index.js`: `apiScript = ws/scripts/nf_api.py`），于是
`DEFAULT_ROOT = SCRIPTS_DIR.parent` 落在工作区 —— 而播种只带
scripts/prompts/templates、**不带 examples/**。旧实现只查
`root/examples/presets` 与 `DEFAULT_ROOT/examples/presets` 两个候选，
双双落空 → 装机版首跑向导「选预设」恒空，报
「找不到 examples/presets/（打包态应由 payload 注入）」，
预设能力在安装包里整体失效。源码态 DEFAULT_ROOT 恰好是仓库根，测不出来。

唯一能命中打包态的候选是主进程注入的 `NF_SAMPLE_ROOT`
（= `resources/payload/examples`）。本用例把这条解析链钉死：

 1. env 命中即返（且优先于 root / DEFAULT_ROOT —— 打包态唯一救命候选）
 2. env 指向空处 → 落回 root / DEFAULT_ROOT（不许因为 env 写错就整体消失）
 3. 无 env 的源码态行为与旧版一致（root 优先，DEFAULT_ROOT 兜底）
 4. 事故现场回放：工作区无 examples + env 指 payload → 必须找到
 5. **随包位置反推**（2026-10-25 补）：无 env 时从解释器位置
    （`<install>/resources/runtime/python/python.exe`）反推出
    `<install>/resources/payload/examples/presets` —— 环境变量只在
    「Electron 主进程 spawn nf_api」这一条路上有，CLI / MCP 垫片代拉 /
    排障脚本都没有；实测活的 nf_api 能列 4 套预设，而终端里同一函数报
    「找不到 examples/presets/」。这条锁保证「env 断开 ≠ 预设功能整体失效」。

用法：python tests/unit/test_preset_resolve.py
"""
import io
import os
import sys
import tempfile
import shutil
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import preset_apply as pa                                            # noqa: E402
from utils import app_paths as apath                                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:140]) if (detail and not cond) else ""))


def _mk_presets(base, n=1):
    """造一个 payload 形态的 examples/presets/<id>。"""
    pdir = Path(base) / "presets"
    for i in range(n):
        d = pdir / ("p%d" % i)
        d.mkdir(parents=True, exist_ok=True)
        (d / "preset.yaml").write_text("id: p%d\nname: N%d\n" % (i, i), encoding="utf-8")
    return pdir


def main():
    tmp = Path(tempfile.mkdtemp(prefix="preset_resolve_"))
    old_env = os.environ.get("NF_SAMPLE_ROOT")
    try:
        print("[1] env 命中即返（打包态唯一救命候选，且优先于 root）")
        payload = tmp / "install" / "resources" / "payload" / "examples"
        want = _mk_presets(payload)
        ws = tmp / "workspace"                      # 工作区：无 examples/
        ws.mkdir(parents=True)
        os.environ["NF_SAMPLE_ROOT"] = str(payload)
        got = pa.find_presets_dir(ws)
        check("env 命中 → 返回 payload/presets", got == want, got)

        print("[2] env 优先级高于 root（root 也有时仍取 env —— 打包态 root 是工作区的误植）")
        ws2 = tmp / "repo_like"
        _mk_presets(ws2 / "examples")
        got2 = pa.find_presets_dir(ws2)
        check("root 与 env 并存 → 取 env", got2 == want, got2)

        print("[3] env 指向空处 → 落回 root / DEFAULT_ROOT（env 写错不许整体消失）")
        os.environ["NF_SAMPLE_ROOT"] = str(tmp / "nowhere")
        got3 = pa.find_presets_dir(ws2)
        check("env 失效 → 落回 root/examples/presets", got3 == ws2 / "examples" / "presets", got3)

        print("[4] 无 env 的源码态：root 优先，DEFAULT_ROOT 兜底（与旧版行为一致）")
        os.environ.pop("NF_SAMPLE_ROOT", None)
        got4 = pa.find_presets_dir(ws2)
        check("无 env → root 优先", got4 == ws2 / "examples" / "presets", got4)
        got5 = pa.find_presets_dir(ws)               # workspace 无 examples
        check("无 env 且 root 无 → DEFAULT_ROOT（仓库 examples/presets）",
              got5 == ROOT / "examples" / "presets", got5)

        print("[5] 事故现场回放：工作区播种 scripts 但无 examples + env 指 payload → 找到")
        payload2 = tmp / "inst2" / "resources" / "payload" / "examples"
        want2 = _mk_presets(payload2, n=2)
        os.environ["NF_SAMPLE_ROOT"] = str(payload2)
        got6 = pa.find_presets_dir(ws)
        check("装机形态 → 找到 2 套预设的目录", got6 == want2, got6)
        check("list_presets 能列出（端到端不只 is_dir）",
              len(pa.list_presets(got6)) == 2, pa.list_presets(got6))

        print("[6] 随包位置反推：无 env + 装机布局 → 从解释器反推出 payload（2026-10-25）")
        os.environ.pop("NF_SAMPLE_ROOT", None)
        inst = tmp / "inst3"
        payload3 = inst / "resources" / "payload" / "examples"
        want3 = _mk_presets(payload3, n=3)
        fake_py = inst / "resources" / "runtime" / "python" / "python.exe"
        fake_py.parent.mkdir(parents=True, exist_ok=True)
        fake_py.write_bytes(b"")
        old_exe, old_default = sys.executable, pa.DEFAULT_ROOT
        try:
            sys.executable = str(fake_py)
            # 装机态的真实形态：脚本住在工作区，DEFAULT_ROOT 也落在工作区（那里没有 examples）
            pa.DEFAULT_ROOT = ws
            got7 = pa.find_presets_dir(ws)          # env 无 + root 无 + DEFAULT_ROOT 无
        finally:
            sys.executable = old_exe
            pa.DEFAULT_ROOT = old_default
        check("无 env 装机形态 → 反推到 resources/payload/examples/presets",
              got7 == want3, got7)
        check("反推命中的目录 list_presets 也能列（端到端不只 is_dir）",
              bool(got7) and len(pa.list_presets(got7)) == 3, got7)

        print("[7] 反推落空 → 行为与旧版一致（不抛错、不返回不存在的路径）")
        try:
            sys.executable = str(tmp / "bare" / "python.exe")   # 祖先链上无 payload
            got8 = pa.find_presets_dir(ws)
        finally:
            sys.executable = old_exe
        check("反推落空 → 仍走 DEFAULT_ROOT（仓库 examples/presets）", got8 == ROOT / "examples" / "presets", got8)

        print("[8] app_paths 单元：两种拼法都认 / 候选去重 / 源码态不抢优先级")
        dirs = apath.payload_example_dirs(exe=str(fake_py),
                                         src=str(ROOT / "scripts" / "utils" / "app_paths.py"))
        check("含 <install>/resources/payload/examples", payload3 in dirs, dirs[:4])
        inst_root = tmp / "inst_flat"               # 另一种布局：payload 就在安装根下
        payload4 = inst_root / "payload" / "examples"
        _mk_presets(payload4, n=1)
        flat_py = inst_root / "runtime" / "python" / "python.exe"
        flat_py.parent.mkdir(parents=True, exist_ok=True)
        flat_py.write_bytes(b"")
        dirs2 = apath.payload_example_dirs(exe=str(flat_py), src=str(ROOT / "scripts" / "utils" / "app_paths.py"))
        check("含 <install>/payload/examples（扁平布局）", payload4 in dirs2, dirs2[:4])
        check("候选无重复", len(dirs2) == len(set(map(str, dirs2))), len(dirs2))
        repo_cands = [d for d in dirs2 if str(d).startswith(str(ROOT))]
        check("源码态祖先链上的 payload 候选都不存在（④ 抢不走 ②/③ 的优先级）",
              bool(repo_cands) and all(not d.is_dir() for d in repo_cands), repo_cands)
    finally:
        if old_env is None:
            os.environ.pop("NF_SAMPLE_ROOT", None)
        else:
            os.environ["NF_SAMPLE_ROOT"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n==== 结果: PASS %d / FAIL %d ====" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
