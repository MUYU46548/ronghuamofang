# -*- coding: utf-8 -*-
"""locked 违例检查（`obsidian_bridge.check_locked_violations`）自检。

## 为什么需要这套用例

`check_consistency.locked_violations` 自 2026-09-21 起**恒为空列表**，
而 CLI 却打印「locked 违例：0 个」—— 看起来像检查通过，实际什么都没查。
2026-10-01 把这个检查接上了（只做**结构性**违逆，语义冲突仍不碰）。

本用例守两件事：

1. **判据真的会命中** —— missing / lock_lost / renamed 三条各自的正反例。
   只写"不报错"的用例毫无意义：一个永远返回空列表的实现也能通过。
2. **不生效时必须说"没查"** —— vault 无 locked 条目、设定集不存在，
   这两种情况下 `checked=False`，绝不能把空结果伪装成"通过"。
   （历史教训：本项目的头号死罪是假成功。）

全程离线、零 token、零网络，不写项目数据（临时目录 + 显式传入路径）。
用法：python tests/unit/test_locked_violations.py
"""
import json
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import obsidian_bridge as ob          # noqa: E402
from utils import setting_schema as ss  # noqa: E402

PASS, FAIL = [], []

VAULT_PATH = "03 设定\\01 人物\\01 主要角色\\露汐.md"
OTHER_PATH = "03 设定\\01 人物\\01 主要角色\\罗霄.md"


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    if cond:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  → {extra}")


def _vault(locked=True, name="露汐"):
    """构造最小 vault_data（不依赖 scan_vault，隔离被测函数）。"""
    return {"characters": [{"name": name, "path": VAULT_PATH,
                            "type": "人物", "tags": ["主角"],
                            "locked": locked, "relations": [], "snippet": "研究冰封术。"}]}


def _write_setting(root, characters):
    p = root / "setting.json"
    p.write_text(json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
    return p


def run_case(label, vault_data, setting_chars, setting_exists=True):
    root = Path(tempfile.mkdtemp(prefix="nf_lk_"))
    try:
        sp = root / "setting.json"
        if setting_exists:
            sp = _write_setting(root, setting_chars)
        else:
            sp = root / "not_there.json"
        rep = ob.check_locked_violations(vault_data=vault_data, setting_path=sp)
        print(f"  · {label}: checked={rep['checked']} "
              f"matched={rep['matched']} violations="
              f"{[v['type'] for v in rep['violations']]}")
        return rep
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    print("=" * 62)
    print("  locked 违例检查自检（离线）")
    print("=" * 62)

    # ---------------- 判据 1：missing ----------------
    print("\n【1】missing —— locked 条目在设定集中消失")
    full = [{"name": "露汐", "path": VAULT_PATH, "locked": True,
             "role": "研究员", "traits": ["冷静"]}]
    rep = run_case("完整保留", _vault(), full)
    check("locked 条目被完整保留时不报违例", rep["violations"] == [],
          rep["violations"])
    check("完整保留时 checked=True 且 matched=1",
          rep["checked"] is True and rep["matched"] == 1,
          f"checked={rep['checked']} matched={rep['matched']}")

    rep = run_case("条目缺失", _vault(), [{"name": "罗霄", "path": OTHER_PATH}])
    check("locked 条目缺失 → 报 missing",
          any(v["type"] == "missing" for v in rep["violations"]), rep["violations"])
    check("缺失时 matched=0", rep["matched"] == 0, rep["matched"])

    # ---------------- 判据 2：lock_lost ----------------
    print("\n【2】lock_lost —— 条目还在，但 locked 标志丢了（静默降级）")
    rep = run_case("丢锁定", _vault(),
                   [{"name": "露汐", "path": VAULT_PATH, "locked": False,
                     "role": "研究员"}])
    check("同名条目 locked 丢失 → 报 lock_lost",
          any(v["type"] == "lock_lost" for v in rep["violations"]), rep["violations"])

    rep = run_case("locking 写成字符串 'true'", _vault(),
                   [{"name": "露汐", "path": VAULT_PATH, "locked": "true"}])
    check("locked 为字符串 'true' → 视为真，不误报",
          rep["violations"] == [], rep["violations"])

    rep = run_case("缺 locked 字段", _vault(), [{"name": "露汐", "path": VAULT_PATH}])
    check("条目没有 locked 字段 → 报 lock_lost（默认已解锁）",
          any(v["type"] == "lock_lost" for v in rep["violations"]), rep["violations"])

    # ---------------- 判据 3：renamed ----------------
    print("\n【3】renamed —— 同一 vault 路径，名字被换")
    rep = run_case("同路径改名", _vault(),
                   [{"name": "露熙", "path": VAULT_PATH, "locked": True}])
    check("同 path 换名 → 报 renamed",
          any(v["type"] == "renamed" for v in rep["violations"]), rep["violations"])

    rep = run_case("别名形态（露汐 → 露汐（神格））", _vault(),
                   [{"name": "露汐（神格）", "locked": True}])
    check("别名形态不算改名（base_name 相同）", rep["violations"] == [],
          rep["violations"])

    # ---------------- 诚实性：未生效必须说"没查" ----------------
    print("\n【4】未生效时不得伪装成「通过」")
    rep = run_case("vault 无 locked 条目", _vault(locked=False), full)
    check("vault 无 locked 条目 → checked=False", rep["checked"] is False,
          rep["checked"])
    check("vault 无 locked 条目 → reason 说明未生效（空结果 ≠ 无违例）",
          "未生效" in rep.get("reason", "") and rep["violations"] == [],
          rep.get("reason", ""))

    rep = run_case("设定集尚未生成", _vault(), full, setting_exists=False)
    check("设定集不存在 → checked=False", rep["checked"] is False, rep["checked"])
    check("设定集不存在 → reason 说明无从比较",
          "不存在" in rep.get("reason", "") and rep["violations"] == [],
          rep.get("reason", ""))

    # ---------------- 判据复用：locked 语义只有一份 ----------------
    print("\n【5】locked 语义单点实现（防止两处判据漂移）")
    check("setting_schema.is_locked 与 _truthy_locked 同实现",
          all(ss.is_locked(e) == ss._truthy_locked(e)
              for e in ({"locked": True}, {"locked": "false"}, {"locked": 1},
                        {"lock": "yes"}, {}, {"locked": "是"})),
          "is_locked 与 _truthy_locked 结果不一致")

    print("\n" + "=" * 62)
    print(f"  通过 {len(PASS)} / 失败 {len(FAIL)}")
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
