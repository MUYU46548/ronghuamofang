# -*- coding: utf-8 -*-
"""DSH 三端诊断的判据（2026-11-26）。

为什么单独立一条：`/dsh/diagnose` 要回答“插件装了没 / 进了 bundles 没 /
跟当前 DSH 对得上没”。如果它只会说 ok，那这个页面就是装饰。
本用例用临时 DSH home 把四种坏情况钉住（零网络、零 Electron）：

1. 正常：插件在 deps + bundles，版本/compat 都能读；
2. 没进 bundles：装了也不加载 → bad，且 action 要指出加 bundles；
3. 没装插件：bad；
4. 插件没登记当前 DSH 版本：unknown，不许装 ok；
5. 读不到 DSH 版本：dsh bad，compat unknown（缺一侧版本就不下结论）。

用法：python tests/unit/test_dsh_diag.py
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from nf_api_domains import dsh_diag  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def make_home(bundled=True, plugin=True, version="0.3.0", releases=None,
              name="dsh-ronghuamofang"):
    tmp = Path(tempfile.mkdtemp(prefix="dsh_diag_"))
    prof = tmp / "profiles" / "desktop"
    (prof).mkdir(parents=True, exist_ok=True)
    deps = {name: "link:E:/CODE/CangKu/dsh-novelforge"} if plugin else {}
    (prof / "package.json").write_text(json.dumps({
        "dependencies": deps,
        "dsh": {"profile": {"bundles": ([name] if bundled else []) + ["dsh-fangcun"]}},
    }, ensure_ascii=False), encoding="utf-8")
    if plugin:
        pdir = prof / "node_modules" / name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "package.json").write_text(json.dumps({
            "name": name,
            "version": version,
            "dsh": {"compatibility": {
                "dsh": ">=0.2.1-rc.2",
                "dshReleases": releases if releases is not None else {"0.2.2-rc.5": "compatible"},
            }},
        }, ensure_ascii=False), encoding="utf-8")
    return tmp


def by_key(out, key):
    return next((c for c in out["checks"] if c["key"] == key), {})


def main():
    tmp = make_home()
    out = dsh_diag.diagnose(home=tmp, app_version="0.9.15", dsh_version="0.2.2-rc.5")
    check("T1 正常：四项都 ok", all(c["status"] == "ok" for c in out["checks"]),
          [(c["key"], c["status"]) for c in out["checks"]])
    check("T1 插件版本/规格读到了",
          out["plugin"]["version"] == "0.3.0" and out["plugin"]["spec"].startswith("link:"),
          out["plugin"])
    check("T1 compat 报 compatible", "compatible" in by_key(out, "compat")["detail"],
          by_key(out, "compat"))

    # 没进 bundles：装了也不加载
    tmp2 = make_home(bundled=False)
    out2 = dsh_diag.diagnose(home=tmp2, app_version="0.9.15", dsh_version="0.2.2-rc.5")
    p2 = by_key(out2, "plugin")
    check("T2 没进 bundles → bad", p2["status"] == "bad", p2)
    check("T2 action 指出加 bundles", "bundles" in p2["action"], p2["action"])

    # 没装插件
    tmp3 = make_home(plugin=False)
    out3 = dsh_diag.diagnose(home=tmp3, app_version="0.9.15", dsh_version="0.2.2-rc.5")
    p3 = by_key(out3, "plugin")
    check("T3 没装插件 → bad", p3["status"] == "bad", p3)

    # 没登记当前 DSH：unknown，不许装 ok
    tmp4 = make_home(releases={"0.2.2-rc.3": "verified"})
    out4 = dsh_diag.diagnose(home=tmp4, app_version="0.9.15", dsh_version="0.2.2-rc.5")
    c4 = by_key(out4, "compat")
    check("T4 没登记 → unknown", c4["status"] == "unknown", c4)
    check("T4 明说没验过", "没登记" in c4["detail"], c4["detail"])

    # 读不到 DSH：dsh bad，compat 不下结论
    tmp5 = make_home()
    out5 = dsh_diag.diagnose(home=tmp5, app_version="0.9.15", dsh_version="")
    check("T5 无 DSH → dsh bad", by_key(out5, "dsh")["status"] == "bad", by_key(out5, "dsh"))
    check("T5 无 DSH → compat unknown", by_key(out5, "compat")["status"] == "unknown",
          by_key(out5, "compat"))

    # 旧名安装（改名前）仍要认出来，不能报“没装”
    tmp6 = make_home(name="dsh-novelforge")
    out6 = dsh_diag.diagnose(home=tmp6, app_version="0.9.15", dsh_version="0.2.2-rc.5")
    p6 = by_key(out6, "plugin")
    check("T6 旧名 dsh-novelforge 仍认：ok", p6["status"] == "ok", p6)
    check("T6 报的是实际装的那个名字", out6["plugin"]["name"] == "dsh-novelforge",
          out6["plugin"]["name"])

    for p in (tmp, tmp2, tmp3, tmp4, tmp5, tmp6):
        shutil.rmtree(p, ignore_errors=True)

    print("-" * 60)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
