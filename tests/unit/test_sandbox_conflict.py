# -*- coding: utf-8 -*-
"""沙盒冲突预检（2026-11-17，沙盒 P0）：零 token、只读、缺数据说“无从比较”。

## 判据

- A 同名 → 警告；现有词条是锁定设定时额外点名（不能靠这条覆盖）；
- B 近名（包含且长度差 ≤2）→ 提示；
- C 名字来源按 frontmatter → 第一个 H1 → 文件名 stem 依次回落；
- D 设定集缺口条目录 → checked=False + 原因（**不许**说成“无冲突”）；
- E 只读：setting.json 一个字节不动；
- F 队列真的接了预检（读侧把 conflict 挂到每条上），界面有入口。

用法：python tests/unit/test_sandbox_conflict.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import sandbox_conflict as sc                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def _mk_root(with_setting=True, corrupt=False):
    tmp = Path(tempfile.mkdtemp(prefix="nf_sbc_"))
    d = tmp / "data" / "setting"
    d.mkdir(parents=True)
    if with_setting:
        payload = {"characters": [
            {"id": "c001", "name": "沈砚", "role": "主角", "locked": True},
            {"id": "c002", "name": "顾川", "role": "配角"},
        ], "world": [{"name": "望江楼", "type": "地点"}]}
        (d / "setting.json").write_text(
            "坏文件" if corrupt else json.dumps(payload, ensure_ascii=False),
            encoding="utf-8")
    return tmp


def main():
    print("=" * 62)
    print("沙盒冲突预检（零 token / 只读 / 缺数据可见）")
    tmp = _mk_root()
    entries, why = sc.load_entries(tmp)
    check("A0 读得到设定集条目（角色 2 + 世界 1）", len(entries) == 3 and not why, (len(entries), why))
    before = (tmp / "data" / "setting" / "setting.json").read_bytes()

    print("\n【A】同名 / 锁定设定")
    fm = "---\nname: 沈砚\ntype: 角色\n---\n\n# 沈砚\n\n他回来了。\n"
    r = sc.check_item(fm, "角色/沈砚.md", entries, why)
    check("A1 同名 → warn", r["level"] == "warn" and any(c["kind"] == "same_name" for c in r["items"]), r)
    check("A2 锁定设定被点名",
          any("锁定" in c["detail"] for c in r["items"]), r["items"])
    check("A3 名字取自 frontmatter", r.get("name") == "沈砚", r.get("name"))

    print("\n【B】近名")
    near = "---\nname: 沈砚之\n---\n\n正文。\n"
    r2 = sc.check_item(near, "角色/沈砚之.md", entries, why)
    check("B1 近名 → info", r2["level"] == "info"
          and any(c["kind"] == "near_name" for c in r2["items"]), r2)
    check("B2 不会误当同名", not any(c["kind"] == "same_name" for c in r2["items"]), r2["items"])

    print("\n【C】名字来源回落")
    h1 = "# 望江楼\n\n雨落在檐上。\n"
    r3 = sc.check_item(h1, "世界/某处.md", entries, why)
    check("C1 没 frontmatter → 用第一个 H1", r3.get("name") == "望江楼", r3.get("name"))
    check("C2 H1 同名（世界条目）→ warn", r3["level"] == "warn", r3)
    r4 = sc.check_item("正文没有标题也没有 frontmatter。\n", "新角色.md", entries, why)
    check("C3 都没有 → 用文件名 stem", r4.get("name") == "新角色", r4.get("name"))

    print("\n【D】缺数据 / 坏文件 → 无从比较（不许当无冲突）")
    empty = _mk_root(with_setting=False)
    e2, why2 = sc.load_entries(empty)
    r5 = sc.check_item(fm, "角色/沈砚.md", e2, why2)
    check("D1 没有 setting.json → checked=False + 原因",
          r5["checked"] is False and "还没有" in r5["why"], r5)
    bad = _mk_root(corrupt=True)
    e3, why3 = sc.load_entries(bad)
    r6 = sc.check_item(fm, "角色/沈砚.md", e3, why3)
    check("D2 坏 setting.json → checked=False，说清读不出来",
          r6["checked"] is False and "读不出来" in r6["why"], r6)

    print("\n【E】只读")
    after = (tmp / "data" / "setting" / "setting.json").read_bytes()
    check("E1 setting.json 一个字节没变", before == after)

    print("\n【F】接线（队列 + 界面）")
    outline_src = (ROOT / "scripts" / "nf_api_domains" / "outline.py").read_text(encoding="utf-8")
    check("F1 队列读侧挂了冲突预检",
          "sandbox_conflict" in outline_src and 'it["conflict"]' in outline_src)
    ui = (ROOT / "console" / "src" / "SandboxQueue.vue").read_text(encoding="utf-8")
    check("F2 界面显示预检结果（三种状态都说清）",
          "设定冲突预检" in ui and "冲突预检不了" in ui and "没发现同名" in ui)

    print("\n" + "-" * 62)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
