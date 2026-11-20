# -*- coding: utf-8 -*-
"""C5 花销闸（2026-11-20 作者拍板：“外部 Agent 跑肯定花钱啊”）。

这份用例守三件事，缺一件这条闸就是摆设：

1. **判定**：没授权 → 拒；self 引擎 / --allow-fake → 放行（物理上不花钱）；
   授权有次数与到期，用完、过期都拒；判不出引擎 fail-closed。
2. **不可自签**：`/config/agent_spend` 在 `FORBIDDEN_IN_AGENT_MODE` 里 ——
   Agent 若能给自己签额度，闸等于不存在。
3. **覆盖**：扫 nf_api 的路由 → 该分支调到的 handler 是否真的构造模型客户端；
   命中就必须在 `MONEY_ENDPOINTS`（或 `NOT_MONEY_WITH_REASON` 里写明零 token 的理由）。
   **新增花钱端点漏登记会在这里变红** —— 否则闸只挡得住今天这 23 个。

反证纪律：判据本身也要能失败。`test_gate_can_fail` 把一次放行改成拒，确认断言真的会红。
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import agent_guard as G  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


# ---------------------------------------------------------------- 1. 判定
print("\n[1] 花销闸判定")

tmp = tempfile.mkdtemp(prefix="spend_")
CFG_SELF = {"engine": "self"}
CFG_DSH = {"engine": "dsh"}

check("非花钱端点不管（/state）",
      G.money_gate(CFG_DSH, "/state", root=tmp) == {
          "blocked": False, "applies": False, "reason": "not-money", "engine": "", "status": {}})
check("单阶段运行动态路径命中 /stage/4/run", G.is_money_endpoint("/stage/4/run"))
check("越界阶段号不命中 /stage/9/run", not G.is_money_endpoint("/stage/9/run"))

g = G.money_gate(CFG_DSH, "/stage/4/run", root=tmp)
check("dsh 引擎 + 无授权 → 拒", g["blocked"] and g["reason"] == "no-grant", g)
check("拒因里带引擎名（用户要知道钱从哪走）", g["engine"] == "dsh")

g = G.money_gate(CFG_SELF, "/stage/4/run", root=tmp)
check("self 引擎 → 放行（只出任务书、不调模型）",
      (not g["blocked"]) and g["reason"] == "self", g)

g = G.money_gate(CFG_DSH, "/stage/4/run", fake=True, root=tmp)
check("--allow-fake → 放行（FakeClient 零花销）",
      (not g["blocked"]) and g["reason"] == "fake", g)

G.grant_spend(2, 1, root=tmp)
g1 = G.money_gate(CFG_DSH, "/stage/4/run", root=tmp)
g2 = G.money_gate(CFG_DSH, "/chapters/rewrite", root=tmp)
g3 = G.money_gate(CFG_DSH, "/review/run", root=tmp)
check("授权后前两次放行", (not g1["blocked"]) and (not g2["blocked"]), (g1["reason"], g2["reason"]))
check("授权次数递减（第 2 次后剩 0）", g2["status"].get("remaining") == 0, g2["status"])
check("次数用完 → 拒（exhausted）", g3["blocked"] and g3["reason"] == "exhausted", g3)

G.revoke_spend(root=tmp)
check("撤销后立刻拒", G.money_gate(CFG_DSH, "/stage/4/run", root=tmp)["blocked"])

# 到期
G.grant_spend(5, 0.1, root=tmp)
import json  # noqa: E402
gp = Path(tmp) / G.SPEND_GRANT_PATH
data = json.loads(gp.read_text(encoding="utf-8"))
data["expires_ts"] = 1.0          # 1970 年就过期了
gp.write_text(json.dumps(data), encoding="utf-8")
g = G.money_gate(CFG_DSH, "/stage/4/run", root=tmp)
check("过期 → 拒（expired）", g["blocked"] and g["reason"] == "expired", g)

# 授权文件损坏 → fail-closed
gp.write_text("{ this is not json", encoding="utf-8")
check("授权文件损坏 → 按未授权处理",
      G.money_gate(CFG_DSH, "/stage/4/run", root=tmp)["reason"] == "no-grant")

# 判不出引擎 → fail-closed
import utils.engine as _eng  # noqa: E402
_orig = _eng.resolve_engine


def _boom(_cfg):
    raise RuntimeError("engine 读不出来")


_eng.resolve_engine = _boom
try:
    g = G.money_gate(CFG_DSH, "/stage/4/run", root=tmp)
    check("判不出引擎 → 拒（fail-closed，不猜）",
          g["blocked"] and g["reason"] == "engine-unknown", g)
finally:
    _eng.resolve_engine = _orig

check("拒文写明“别再重试、报告作者”（不给绕过暗示）",
      "不要重试" in G.spend_refusal_message("/stage/4/run", g)
      and "桌面端" in G.spend_refusal_message("/stage/4/run", g))

# ---------------------------------------------------------------- 2. 不可自签
print("\n[2] Agent 不得给自己签额度")
check("/config/agent_spend 在禁单里",
      G.is_forbidden_in_agent_mode("/config/agent_spend"))
check("/config/agent_spend 不在花钱清单里（它是零 token 的授权动作）",
      not G.is_money_endpoint("/config/agent_spend"))

# ---------------------------------------------------------------- 3. 覆盖
print("\n[3] 覆盖：新房钱端点漏登记会红")

api_src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
lines = api_src.splitlines()

# 3a 路由 → 该分支调到的 handle_/act_ 函数
branches = []
cur = None
for l in lines:
    m = re.match(r'\s*(?:el)?if p == "([^"]+)"', l)
    if m:
        if cur:
            branches.append(cur)
        cur = (m.group(1), [])
    elif re.match(r"\s*(elif|else)\b", l):
        if cur:
            branches.append(cur)
            cur = None
    elif cur is not None:
        cur[1].append(l)
if cur:
    branches.append(cur)

# 3b 函数体（域模块 + nf_api）
bodies = {}
for f in list((ROOT / "scripts" / "nf_api_domains").glob("*.py")) + [ROOT / "scripts" / "nf_api.py"]:
    s = f.read_text(encoding="utf-8")
    for mm in re.finditer(r"\ndef ((?:handle|act)_[a-z0-9_]+)\(", s):
        seg = s[mm.end():]
        nx = re.search(r"\ndef ", seg)
        bodies[mm.group(1)] = seg[: nx.start() if nx else len(seg)]

CLIENT = re.compile(r"_client_for_env|_client_or_none_for_self|make_client|DshClient|orchestrator\.run")
_memo = {}


def builds_client(fn, depth=0):
    if fn in _memo:
        return _memo[fn]
    b = bodies.get(fn)
    if b is None or depth > 3:
        return False
    hit = bool(CLIENT.search(b))
    if not hit:
        for callee in set(re.findall(r"\b((?:handle|act)_[a-z0-9_]+)\(", b)):
            if callee != fn and builds_client(callee, depth + 1):
                hit = True
                break
    _memo[fn] = hit
    return hit


discovered = []
for path, body in branches:
    txt = "\n".join(body)
    fns = set(re.findall(r"\b((?:handle|act)_[a-z0-9_]+)\(", txt))
    if any(builds_client(f) for f in fns):
        discovered.append(path)

uncovered = sorted(
    p for p in discovered
    if p not in G.MONEY_ENDPOINTS
    and p not in G.NOT_MONEY_WITH_REASON
    and not G.MONEY_STAGE_RE.match(p)
)
check("自动发现的花钱端点全部被覆盖", not uncovered,
      "漏登记: " + ", ".join(uncovered) if uncovered else "共 %d 条" % len(discovered))

# 3c 清单里的每条路径必须真实存在（写错 = 闸对空打）
all_paths = {p for p, _ in branches}
missing = sorted(p for p in G.MONEY_ENDPOINTS if p not in all_paths)
check("MONEY_ENDPOINTS 里每条路径都在 nf_api 有路由", not missing,
      "不存在: " + ", ".join(missing) if missing else "共 %d 条" % len(G.MONEY_ENDPOINTS))

# 3d 豁免表不许留空理由（空理由 = 下一个人的假账）
bad_reason = sorted(p for p, why in G.NOT_MONEY_WITH_REASON.items() if not str(why or "").strip())
check("豁免表每条都写了“为什么零 token”", not bad_reason, bad_reason)


# ---------------------------------------------------------------- 4. 反证：判据本身会红
print("\n[4] 反证（判据必须能失败）")
tmp2 = tempfile.mkdtemp(prefix="spend_fail_")
G.grant_spend(1, 1, root=tmp2)
ok_now = not G.money_gate(CFG_DSH, "/stage/4/run", root=tmp2)["blocked"]
G.revoke_spend(root=tmp2)
ok_after = G.money_gate(CFG_DSH, "/stage/4/run", root=tmp2)["blocked"]
check("同一句断言在撤销后翻转（说明判据读的是真状态，不是常量）",
      ok_now and ok_after, (ok_now, ok_after))

print("\n" + "=" * 60)
print("花销闸：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
