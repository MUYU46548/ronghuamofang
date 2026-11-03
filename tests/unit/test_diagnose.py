# -*- coding: utf-8 -*-
"""诊断入口（C 组“运行侧三态 → 诊断”，2026-11-03）。

## 要治的病

界面原来只有“补救建议”（只列失败项）：用户看不到**运行态**（在跑 / 失联残留 /
空闲），也看不到“体检到底查了哪些项、哪些通过”。

## 判据（每条都能变红）

- D1 返回四块齐全：doctor（全量 items）/ runner / facts / issues+remedies
- D2 体检明细是**全量**（含通过项），checked = len(items)
- D3 运行态判据来自 pid 死活（不是“有没有 running 字样”）：
     活 pid → alive；死 pid → stale；没记录 → 都 False
- D4 环境事实读得到引擎（system.yaml 的 engine）
- D5 issues 只含失败项，remedies 每条都有可执行 command
- D6 端点与界面接线（/remedy → build_remedy；App.vue 用 doctor.items / runner）

用法：python tests/unit/test_diagnose.py
"""
import io
import json
import os
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


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main():
    print("=" * 66)
    print("诊断入口")
    print("=" * 66)

    from nf_api_domains.misc import build_remedy

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "config").mkdir(parents=True)
        (root / "config" / "system.yaml").write_text(
            "engine: hermes\n", encoding="utf-8")
        (root / "config" / "project.yaml").write_text(
            "book:\n  name: \"诊断测试\"\n  chapters: 2\n", encoding="utf-8")

        print("\n[D1] 没跑过 → 四块齐全、运行态空闲")
        r = build_remedy(root)
        check("ok=True", r.get("ok") is True)
        for k in ("doctor", "runner", "facts", "issues", "remedies"):
            check("有 %s" % k, k in r)
        check("没记录 → alive/stale 都是 False",
              r["runner"]["alive"] is False and r["runner"]["stale"] is False,
              r["runner"])
        check("runner.why 说清没记录", "没有记录" in str(r["runner"].get("why", "")),
              r["runner"].get("why"))

        print("\n[D2] 体检明细是全量（含通过项）")
        items = r["doctor"]["items"]
        check("items 是列表且非空", isinstance(items, list) and len(items) > 0, len(items))
        check("checked == len(items)", r["doctor"]["checked"] == len(items),
              (r["doctor"]["checked"], len(items)))
        check("每项带 name/ok/detail",
              all({"name", "ok", "detail"} <= set(x) for x in items),
              items[:1])

        print("\n[D3] 运行态按 pid 死活判定")
        pid_file = root / "data" / "state" / "progress.json"
        _write(pid_file, {"runner": {"pid": os.getpid(), "since": "2026-11-03 20:00",
                                     "stage": 4}, "stages": {}})
        r2 = build_remedy(root)
        check("活 pid → alive", r2["runner"]["alive"] is True, r2["runner"])

        _write(pid_file, {"runner": {"pid": 999999, "since": "2026-11-01 09:00",
                                     "stage": 4}, "stages": {}})
        r3 = build_remedy(root)
        check("死 pid → stale（不是 running）",
              r3["runner"]["stale"] is True and r3["runner"]["alive"] is False,
              r3["runner"])
        check("失联说明带 pid 与时间",
              "999999" in str(r3["runner"].get("why", "")),
              r3["runner"].get("why"))

        print("\n[D4] 环境事实")
        check("engine 读到 hermes", r3["facts"]["engine"] == "hermes", r3["facts"])
        check("带解释器与数据根",
              bool(r3["facts"].get("python")) and r3["facts"].get("root") == str(root),
              r3["facts"])
        check("progress_exists 反映真实", r3["facts"]["progress_exists"] is True)

        print("\n[D5] 问题 → 建议")
        check("issues 只含失败项",
              all(not it["ok"] for it in items if it["name"] in
                  [i["name"] for i in r3["issues"]]))
        check("remedies 每条有 command",
              all(r.get("command") for r in r3["remedies"]), r3["remedies"][:2])

    print("\n[D6] 接线")
    misc = (ROOT / "scripts" / "nf_api_domains" / "misc.py").read_text(encoding="utf-8")
    check("handle_remedy 调 build_remedy", "build_remedy(api.ROOT)" in misc)
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("界面展示运行态", "remedyResult?.runner" in app)
    check("界面展示体检明细", "remedyResult?.doctor?.items" in app)
    check("复制文本含运行态与体检", "[运行态]" in app and "[体检]" in app)

    print("\n" + "=" * 66)
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
