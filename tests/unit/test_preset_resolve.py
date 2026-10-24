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
