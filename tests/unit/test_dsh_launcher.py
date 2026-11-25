# -*- coding: utf-8 -*-
"""DSH 命令形状（方案 B，2026-11-25）：默认不变、换壳只改配置。

为什么要钉：这一层是为了“DSH 停更时把它搬走当内核”，代价是命令不再是硬编码的。
松的风险有两个方向：
  ① 抽象把**默认值**改坏了 —— 换壳没发生，先把正常路径弄坏；
  ② 配置里带了 `-` 时拼成两个 —— CLI 直接报参数错误，报错信息还看不出是墨坊拼的。
两条都只能靠断言发现。

用法：python tests/unit/test_dsh_launcher.py
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.dsh_client import DshClient                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(extra)) if (extra and not cond) else ""))


def main():
    default = DshClient(dsh_bin="dsh", profile="headless")._build_cmd()
    check("默认形状与旧硬编码逐字一致",
          default == ["dsh", "--profile", "headless", "--json",
                      "--approval-policy", "never", "-"], default)

    swapped = DshClient(dsh_bin="other-cli", base_args=["run", "--json"])._build_cmd()
    check("换壳只换前缀，选项与结尾 - 仍在",
          swapped == ["other-cli", "run", "--json", "--approval-policy", "never", "-"],
          swapped)

    with_dash = DshClient(dsh_bin="x", base_args=["run", "-"])._build_cmd()
    check("配置里自带 - 也不会拼成两个", with_dash.count("-") == 1, with_dash)
    check("且 - 在最后", with_dash[-1] == "-", with_dash)

    prof = DshClient(dsh_bin="dsh", profile="desk",
                     base_args=["--profile", "{profile}", "--json"])._build_cmd()
    check("{profile} 会被替换成实际 profile", "desk" in prof and "{profile}" not in prof, prof)

    empty = DshClient(dsh_bin="dsh", base_args=[])._build_cmd()
    check("空配置回落到默认形状（不是一条空命令）",
          empty[1:3] == ["--profile", "headless"], empty)

    print("\n" + "=" * 60)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    print("=" * 60)
    for f in FAIL:
        print("    - " + f)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
