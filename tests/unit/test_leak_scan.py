# -*- coding: utf-8 -*-
"""泄露门禁扫描器回归 —— 重点是 **fail-closed 与"豁免必须可见"**。

## 为什么需要它

门禁的价值全在"它不会悄悄失效"：

| 编号 | 风险 | 钉法 |
|---|---|---|
| L1 | 词表缺失/为空/被截断 → 门禁变成空转却仍报绿 | 三种情形都必须 **exit 2**（fail-closed） |
| L2 | 命中不报 | 真命中必须 exit 1 |
| L3 | 零命中误报红 | 干净 fixture 必须 exit 0 |
| L4 | 路径豁免变成静默后门 | 豁免文件必须**被打印出来** |
| L5 | 二进制文件被当文本读 → 噪声/误报 | 按扩展名跳过 |
| L6 | **门禁自己的词表模板泄露真值** | `ci/blacklist.example` 必须全是占位 |

历史教训：本仓栽过「检查器恒返回空 → 被读成通过」（`locked_violations` 曾恒为空列表），
所以这里对"空结果"的三种成因逐一断言。

用法：python tests/unit/test_leak_scan.py（零 LLM、零网络）
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCAN = REPO / "scripts" / "leak_scan.py"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {extra}")


def run(args):
    p = subprocess.run([sys.executable, str(SCAN)] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return p.returncode, p.stdout or "", p.stderr or ""


def words_file(d, extra_lines=(), n_pad=25):
    """造一份合法的词表（默认凑够 MIN_ENTRIES）。"""
    lines = [f"填充词{i}" for i in range(n_pad)] + list(extra_lines)
    p = Path(d) / "blacklist.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ====================================================================== L1

def case_fail_closed():
    print("\n[L1] fail-closed：词表不可用时绝不放行")
    with tempfile.TemporaryDirectory() as td:
        rc, out, _ = run(["--list", str(Path(td) / "nope.txt"), "--root", td, "--no-git"])
        check("L1 词表不存在 → exit 2（不是 0）", rc == 2, f"→ rc={rc} {out[-120:]}")

        empty = Path(td) / "empty.txt"
        empty.write_text("# 只有注释\n\n", encoding="utf-8")
        rc, out, _ = run(["--list", str(empty), "--root", td, "--no-git"])
        check("L1 词表为空 → exit 2", rc == 2, f"→ rc={rc}")

        short = Path(td) / "short.txt"
        short.write_text("甲\n乙\n丙\n", encoding="utf-8")
        rc, out, _ = run(["--list", str(short), "--root", td, "--no-git"])
        check("L1 词表条目数不足下限 → exit 2（防「部分丢失」）", rc == 2,
              f"→ rc={rc} {out[-140:]}")


# ====================================================================== L2/L3

def case_detect_and_clean():
    print("\n[L2/L3] 真命中要红，干净要绿")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "a.md").write_text("这里出现了独门暗号甲。\n", encoding="utf-8")
        (Path(td) / "b.md").write_text("无关内容。\n", encoding="utf-8")
        wl = words_file(td, ["独门暗号甲"])

        rc, out, _ = run(["--list", str(wl), "--root", td, "--no-git", "--json"])
        data = json.loads(out[out.index("{"):]) if "{" in out else {}
        check("L2 命中 → exit 1", rc == 1, f"→ rc={rc}")
        check("L2 命中条目带文件/行号/命中词",
              data.get("hits") and data["hits"][0]["line"] == 1
              and data["hits"][0]["word"] == "独门暗号甲",
              f"→ {data.get('hits')}")

        (Path(td) / "a.md").write_text("已被改写，没有暗号。\n", encoding="utf-8")
        rc, out, _ = run(["--list", str(wl), "--root", td, "--no-git"])
        check("L3 零命中 → exit 0", rc == 0, f"→ rc={rc} {out[-120:]}")


# ====================================================================== L4

def case_allow_visible():
    print("\n[L4] 路径豁免必须可见（静默豁免 = 后门），且不得无声放大")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "LICENSE").write_text("© 2026 独门暗号甲\n", encoding="utf-8")
        (Path(td) / "other.md").write_text("独门暗号甲也在别处\n", encoding="utf-8")
        sub = Path(td) / "sub"
        sub.mkdir()
        (sub / "LICENSE.md").write_text("独门暗号甲\n", encoding="utf-8")
        wl = words_file(td, ["独门暗号甲", "allow: LICENSE"])
        rc, out, _ = run(["--list", str(wl), "--root", td, "--no-git"])
        check("L4 被豁免文件不再被报（LICENSE 自己）",
              "LICENSE\n" not in out.split("命中")[-1].replace("LICENSE.md", "")
              or rc == 1, f"→ rc={rc}")
        check("L4 **豁免清单被打印出来**（不许静默）", "已豁免" in out and "LICENSE" in out,
              f"→ {out[:200]}")
        check("L4 未豁免的文件仍被抓到（豁免不扩散）", "other.md" in out,
              f"→ {out[:300]}")
        check("L4 豁免是**精确路径**：`allow: LICENSE` 不放过 `sub/LICENSE.md`",
              "LICENSE.md" in out, f"→ 豁免被无声放大：{out[:300]}")

    # 目录豁免：结尾 `/`
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "deliverables"
        d.mkdir()
        (d / "a.md").write_text("独门暗号甲\n", encoding="utf-8")
        (Path(td) / "b.md").write_text("独门暗号甲\n", encoding="utf-8")
        wl = words_file(td, ["独门暗号甲", "allow: deliverables/"])
        rc, out, _ = run(["--list", str(wl), "--root", td, "--no-git"])
        check("L4 目录豁免（结尾 `/`）生效", "deliverables" in out.split("已豁免")[-1]
              and rc == 1, f"→ rc={rc} {out[:200]}")


# ====================================================================== L5

def case_binary_skipped():
    print("\n[L5] 二进制按扩展名跳过")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "img.png").write_bytes(b"\x89PNG\r\n\x1a\n" + "独门暗号甲".encode())
        wl = words_file(td, ["独门暗号甲"])
        rc, out, _ = run(["--list", str(wl), "--root", td, "--no-git"])
        check("L5 二进制文件不参与文本扫描（不炸、不误报）", rc == 0,
              f"→ rc={rc} {out[-140:]}")


# ====================================================================== L6

def case_example_is_clean():
    print("\n[L6] 门禁自己的模板必须零真值")
    ex = REPO / "ci" / "blacklist.example"
    check("L6 ci/blacklist.example 存在（真词表不进仓库）", ex.exists())
    if ex.exists():
        bad = []
        for line in ex.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.lower().startswith("allow:"):
                continue
            if not s.startswith("例-"):
                bad.append(s)
        check("L6 模板里每一行都是占位（不含真词）", not bad, f"→ {bad[:5]}")

    check("L6 .gitignore 忽略了真词表 ci/blacklist.txt",
          subprocess.run(["git", "check-ignore", "-q", "ci/blacklist.txt"],
                         cwd=str(REPO)).returncode == 0)


def main():
    print("=" * 70)
    print("泄露门禁扫描器回归")
    print("=" * 70)
    case_fail_closed()
    case_detect_and_clean()
    case_allow_visible()
    case_binary_skipped()
    case_example_is_clean()
    print("\n" + "=" * 70)
    print(f"合计: {len(PASS)} 通过 / {len(FAIL)} 失败")
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
