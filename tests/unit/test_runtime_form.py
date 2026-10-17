# -*- coding: utf-8 -*-
"""runtime_form() 判据 + /state 形态自报接线（2026-10-17 指引层修复）。

背景：十三次试跑全部死在「外部 Agent 分不清源码态/安装态、CLI 用裸 python」上。
修复：`GET /state` 自报 `form`（source/installed）+ `runtime_python`（能跑管线的
解释器），任务简报与 kickoff 引用它选通道选解释器。本用例锁两件事：

1. `runtime_form()` 判据本身：有 .git/.venv = source，否则 installed（可注入根）；
2. 接线不回退：build_state 响应真的带这两个字段，且 runtime_python 指向存在的解释器。
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import nf_api  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:200]) if detail else ""))


def main():
    orig = nf_api.ROOT
    try:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            nf_api.ROOT = t
            check("无 .git/.venv → installed", nf_api.runtime_form() == "installed",
                  nf_api.runtime_form())
            (t / ".venv").mkdir()
            check("有 .venv → source", nf_api.runtime_form() == "source")
            (t / ".venv").rmdir()
            (t / ".git").mkdir()
            check("有 .git → source", nf_api.runtime_form() == "source")
    finally:
        nf_api.ROOT = orig

    # 接线静态断言：build_state 里真放了这两个字段（防顺手删掉不报错）
    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("/state 响应带 form 字段", '"form": runtime_form()' in src)
    check("/state 响应带 runtime_python 字段", '"runtime_python": sys.executable' in src)

    # 真调一次 build_state（只读本仓配置与进度，零副作用）
    st = nf_api.build_state()
    check("build_state 响应含 form 且取值合法",
          st.get("form") in ("source", "installed"), st.get("form"))
    rp = st.get("runtime_python")
    check("build_state 响应含 runtime_python 且指向真实解释器",
          bool(rp) and Path(rp).exists(), rp)

    print("\n通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
