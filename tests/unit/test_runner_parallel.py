# -*- coding: utf-8 -*-
"""测试运行器的并行调度：端口分组必须把**抢同一端口**的用例串起来。

## 本用例守护什么

`nfctl test` 2026-11-28 起默认并行（20 核上全量 399 秒 → 88 秒）。并行的唯一
真风险是**用例写死了固定端口**（本仓 38 处，8917–8963），同端口并发会双绑/
健康检查打到别人的实例 → 假红。实测：无脑 12 线程把 3 个 HTTP 用例打红。

所以调度按端口把用例分组成“组内串行、组间并发”。这里锁死四件事：

1. **端口识别要宽**：`PORT = 8941`、`PORT_SELF = 8941`、`API_PORT`、`MCP_PORT`、
   `127.0.0.1:8941` 都要认。窄正则（只认 `PORT =`）会把
   `test_outline_branches_api_http.py`（`PORT_SELF/PORT_FAKE`）漏掉 —— 这正是
   第一版实现的分组错误，实测被这个用例抓到。
2. **端口集合部分相交也要并组**：`{8941}` 与 `{8941, 8942}` 签名不同，但抢同一个
   端口。只按签名分组会让它们并发 → 双绑。必须取**端口连通分量**。
3. **无端口的用例各自独立**（不能被并进同一个大组，否则并行度退化成 1）。
4. `--jobs 1` 必须能退回完全串行（排查并行动机时用）。

判据只有一份：本用例**直接调用** `nfctl.ports_used_by` / `nfctl.group_tests_by_port`，
不自己重算一遍（判据写两遍只会漂移 —— 本仓前科 test_style_v2）。[5] 是反向验证：
把分组退回缺陷版，[2] 那条硬判据必须能抓到，否则它是空转。

用法：python tests/unit/test_runner_parallel.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASS = 0
FAIL = 0


def check(cond, label, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + label)
    else:
        FAIL += 1
        print("  FAIL  " + label + ("  → " + str(detail)[:300] if detail else ""))


def main():
    print("=" * 70)
    print("测试运行器并行调度：端口分组")
    print("=" * 70)

    import nfctl

    # [1] 端口识别宽度（走真实实现 ports_used_by，不自己重算正则）
    print("\n[1] 端口识别必须宽于 `PORT =`（走 nfctl.ports_used_by）")
    tmp = Path(tempfile.mkdtemp(prefix="nf_par_probe_"))
    samples = [
        ("PORT = 8941\n", {"8941"}),
        ("PORT_SELF = 8941\n", {"8941"}),
        ("API_PORT = 8799\n", {"8799"}),
        ("MCP_PORT = 8796\n", {"8796"}),
        ("DEAD_PORT = 59999\n", set()),          # 刻意"必然无人监听"，不算占用
        ('u = "http://127.0.0.1:8934/x"\n', {"8934"}),
        ("PORT_SELF = 8941\nPORT_FAKE = 8942\n", {"8941", "8942"}),
    ]
    for i, (text, want) in enumerate(samples):
        f = tmp / ("probe_%d.py" % i)
        f.write_text(text, encoding="utf-8")
        got = nfctl.ports_used_by(f)
        check(got == want, "识别 %-42s → %s"
              % (text.strip().replace("\n", "; ")[:42], sorted(want) or "不占用"), got)

    # [2] 真实仓库：端口相交必须同组（调真实实现 group_tests_by_port）
    print("\n[2] 端口相交的用例必须同组（调 nfctl.group_tests_by_port）")
    all_tests = sorted((REPO / "tests" / "unit").glob("test_*.py")) + \
                sorted((REPO / "tests" / "http").glob("test_*.py"))
    check(len(all_tests) > 300, "扫到全部用例（%d 个）" % len(all_tests),
          "少于 300 说明收集面变了，判据要重核")

    groups = nfctl.group_tests_by_port(all_tests)
    group_of = {}
    for gi, batch in enumerate(groups):
        for f in batch:
            group_of[f] = gi

    # 覆盖守恒：每个用例恰好出现一次（并行不许漏跑或跑两遍）
    check(len(group_of) == len(all_tests),
          "每个用例都被分到组里（%d 个）" % len(all_tests),
          "只覆盖 %d 个" % len(group_of))
    check(sum(len(b) for b in groups) == len(all_tests),
          "分组无重复（合计 %d）" % sum(len(b) for b in groups),
          "有重复：合计 %d ≠ %d" % (sum(len(b) for b in groups), len(all_tests)))

    port_files = [f for f in all_tests if nfctl.ports_used_by(f)]
    check(len(port_files) > 20, "识别出用端口的用例（%d 个）" % len(port_files),
          "过少说明识别退化")
    # 硬判据：任意两个"端口有交集"的用例必须同组
    viol = []
    for i, a in enumerate(port_files):
        for b in port_files[i + 1:]:
            if nfctl.ports_used_by(a) & nfctl.ports_used_by(b):
                if group_of[a] != group_of[b]:
                    viol.append((a.name, b.name,
                                 sorted(nfctl.ports_used_by(a) & nfctl.ports_used_by(b))))
    check(not viol, "无“端口相交却分到不同组”的用例（并发会双绑）", viol[:4])

    # 已知真实冲突对必须同组（回归锚点，来自实测假红）
    def same(a, b):
        fa = REPO / "tests" / "http" / a
        fb = REPO / "tests" / "http" / b
        if not (fa.exists() and fb.exists()):
            return None
        return group_of[fa] == group_of[fb]

    r = same("test_factory_api_http.py", "test_outline_branches_api_http.py")
    check(r is not False, "8941 组：factory_api 与 outline_branches 同组"
          "（后者用 PORT_SELF=8941 + PORT_FAKE=8942，窄正则必漏）", r)
    r = same("test_project_wizard_api_http.py", "test_segment_approval_api_http.py")
    check(r is not False, "8934 组：wizard 与 segment_approval 同组", r)
    r = same("test_book_extract_api_http.py", "test_review_structure_api_http.py")
    check(r is not False, "8947 组：book_extract 与 review_structure 同组", r)

    # [3] 无端口用例不能被并成一个大组（并行度会退化成 1）
    print("\n[3] 无端口用例各自独立（并行度不许退化）")
    solo = [f for f in all_tests if not nfctl.ports_used_by(f)]
    solo_groups = {group_of[f] for f in solo}
    check(len(solo_groups) == len(solo),
          "无端口用例 %d 个 → %d 个独立组" % (len(solo), len(solo_groups)),
          "有 %d 个被错误合并" % (len(solo) - len(solo_groups)))
    check(len(solo) > 100, "无端口用例仍是多数（%d 个）" % len(solo),
          "若这条红，说明端口识别过度匹配（把普通数字当端口）")

    # [4] --jobs 契约：1 = 完全串行（排查用），0 = 自动
    print("\n[4] --jobs 契约")
    src = (REPO / "scripts" / "nfctl.py").read_text(encoding="utf-8")
    check('--jobs' in src and 'p_test.add_argument("--jobs"' in src,
          "CLI 暴露 --jobs 参数")
    body = src.split("def run_tests(", 1)[1] if "def run_tests(" in src else ""
    check("jobs <= 0" in body and "os.cpu_count()" in body,
          "0 = 自动取 CPU 数（上限 12）")
    check("jobs == 1 or len(groups) == 1" in body,
          "1 = 完全串行（等价旧行为，排查并行动机时用）")
    check("ThreadPoolExecutor" in body, "组间用线程池并发（组内串行）")

    # [5] 反向验证：退回缺陷版分组，[2] 那条硬判据必须能抓到它（否则是空转）
    print("\n[5] 反向验证：故意退回“只按签名分组”→ [2] 的判据必须抓得到")

    def buggy_group_by_port(test_files):
        """缺陷版：只把签名相同的并组，不做端口连通分量（实现中真犯过的错）。"""
        buckets = {}
        for tf in test_files:
            ps = nfctl.ports_used_by(tf)
            key = tuple(sorted(ps)) if ps else ("__solo__", tf.name)
            buckets.setdefault(key, []).append(tf)
        return list(buckets.values())

    bad = buggy_group_by_port(all_tests)
    bad_of = {}
    for gi, batch in enumerate(bad):
        for f in batch:
            bad_of[f] = gi
    bad_viol = []
    for i, a in enumerate(port_files):
        for b in port_files[i + 1:]:
            if nfctl.ports_used_by(a) & nfctl.ports_used_by(b):
                if bad_of[a] != bad_of[b]:
                    bad_viol.append((a.name, b.name))
    check(len(bad_viol) > 0,
          "缺陷版确实会漏并（%d 对）—— 说明 [2] 的判据不是空转" % len(bad_viol),
          "若这条红：[2] 无论实现对错都会通过，是假判据")
    check(len(groups) < len(bad),
          "正确版组数 %d < 缺陷版 %d（真的并了组）" % (len(groups), len(bad)),
          "两者相等说明合并没生效")

    print("\n" + "=" * 70)
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
