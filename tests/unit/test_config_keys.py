# -*- coding: utf-8 -*-
"""出厂配置补键与旧值升级：判据（2026-11-28，按作者问卷 C1=B / C2=B）。

## 这组判据守什么

真机对账：出厂 system.yaml 158 键 vs 老工作区 113 键。缺的 46 个里包括
`dsh.text_writing`（纯文本写作）、`gates.segment_approval`（分段审批）——
**功能在仓库里做了，老用户机器上永远不生效**。

作者定的两条（问卷 C1=B / C2=B）：
- C1=B：补缺失键 + **值等于旧出厂值的也升级**，用户改过的一个字不动；
- C2=B：缺失不再静默兼容，关键键缺失要报。

## 三条硬判据

1. **绝不整份 dump**：写完之后，原文件的注释行必须**逐字还在**（本仓历史事故：
   `yaml.safe_dump` 把 system.yaml 的 170 行注释吃光了，用户点一次下拉就没了）。
2. **用户改过的键一个字不动**：造一个用户把 target_words 改成 [7000,12000]
   的工作区，补完后它必须还是 [7000,12000]。
3. **值等于旧出厂值的要升级**：造一个 target_words 还是旧出厂 [2000,5500] 的，
   补完必须变成新出厂值。

⚠️ 全程在临时目录里跑，不碰真实工作区。

用法：python tests/unit/test_config_keys.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "utils"))

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS  " + name)
    else:
        FAIL += 1
        print("  FAIL  " + name + ("  → " + str(detail)[:300] if detail else ""))


# 一个老工作区的最小 system.yaml：有注释、缺新键、含一个用户改过的值
OLD_WS = """\
# 绒花墨坊配置（这是**用户看得见的注释**，不许被吃掉）
# 第二行注释：止烧口径写在注释里

# 单章字数（旧出厂值）
chapter:
  target_words: [2000, 5500]
  # 拆群开关
  enhanced_mode: false

budget:
  token_limit:
    max_total_tokens: 10000000

# 用户自己填的库路径
obsidian:
  vault_path: "E:/我的库"
"""

# 出厂文件：多出若干新键，且 target_words 已是新值
FACTORY = """\
# 绒花墨坊配置（出厂）
chapter:
  target_words: [4000, 7200]
  enhanced_mode: false
  # 单群目标字数（决定一章拆几群）
  enhanced_group_words: 1500
  regen_token_limit: 400000
  # 卷大小
  volume_size: 20

budget:
  token_limit:
    max_total_tokens: 500000000
    chapter_max_tokens: 3000000

# 分段审批：全部分段通过才放行阶段2
gates:
  segment_approval: true
  semantic_review: false

dsh:
  # 纯文本写作：一次 completion
  text_writing: true
  writing_boundary: true
  timeout: 1800

# 上下文记忆策略
context:
  memory:
    rolling_chars: 2600
    recall_top_k: 6

# 用户自己填的库路径
obsidian:
  vault_path: ""
"""


def build(tmp, ws_text=None, fa_text=None):
    ws = tmp / "ws"
    fa = tmp / "factory"
    (ws / "config").mkdir(parents=True)
    (fa / "config").mkdir(parents=True)
    (ws / "config" / "system.yaml").write_text(ws_text or OLD_WS, encoding="utf-8")
    (fa / "config" / "system.yaml").write_text(fa_text or FACTORY, encoding="utf-8")
    return ws, fa


def main():
    print("=" * 70)
    print("出厂配置补键与旧值升级")
    print("=" * 70)

    import yaml
    from utils import config_keys as ck

    # ---------- [1] 对账：缺失/升级/用户改过 分得清 ----------
    print("\n[1] 对账：三类必须分清")
    tmp = Path(tempfile.mkdtemp(prefix="nf_ck_"))
    try:
        ws, fa = build(tmp)
        d = ck.diff_keys(ws, fa)
        check("认出缺失键", "gates.segment_approval" in d["missing"], d["missing"])
        check("认出缺失键（dsh 段）", "dsh.text_writing" in d["missing"])
        up = {x["key"] for x in d["upgradable"]}
        check("**认出“值等于旧出厂值”的键（应升级）**",
              "chapter.target_words" in up, sorted(up))
        check("用户改过的键被单独列出（不是 missing 也不是 upgradable）",
              "obsidian.vault_path" in d["changed_by_user"], d["changed_by_user"])
        check("用户改过的键不在 upgradable 里",
              "obsidian.vault_path" not in up, sorted(up))

    # ---------- [2] 实际执行 ----------
        print("\n[2] 执行：补缺失 + 升旧值 + 不动用户值")
        before_lines = (ws / "config" / "system.yaml").read_text(encoding="utf-8").split("\n")
        before_comments = [l for l in before_lines if l.lstrip().startswith("#")]
        r = ck.apply(ws, fa)
        check("执行成功", r.get("ok"), r.get("error"))
        after_text = (ws / "config" / "system.yaml").read_text(encoding="utf-8")
        after_flat = ck._parse_flat(after_text)

        check("补上了 gates.segment_approval",
              after_flat.get("gates.segment_approval") is True,
              after_flat.get("gates.segment_approval"))
        check("补上了 dsh.text_writing",
              after_flat.get("dsh.text_writing") is True,
              after_flat.get("dsh.text_writing"))
        check("补上了 context.memory.rolling_chars",
              after_flat.get("context.memory.rolling_chars") == 2600,
              after_flat.get("context.memory.rolling_chars"))
        check("**旧出厂值被升级**（target_words → 新出厂）",
              ck._norm(after_flat.get("chapter.target_words")) == ck._norm([4000, 7200]),
              after_flat.get("chapter.target_words"))
        check("**用户改过的值一个字不动**（vault_path）",
              after_flat.get("obsidian.vault_path") == "E:/我的库",
              after_flat.get("obsidian.vault_path"))
        check("用户自己设的 max_total_tokens 也没被改成出厂值",
              after_flat.get("budget.token_limit.max_total_tokens") == 10000000,
              after_flat.get("budget.token_limit.max_total_tokens"))

        # ---------- [3] 注释必须逐字还在（本仓历史事故） ----------
        print("\n[3] 注释不许被吃掉（整份 dump 会删光注释）")
        for c in before_comments:
            check("注释仍在：%s" % c.strip()[:38], c in after_text, "被吃掉了")

        # ---------- [4] 写出来的必须是合法 YAML，且结构对 ----------
        print("\n[4] 结果文件合法性与结构")
        try:
            parsed = yaml.safe_load(after_text)
            ok_yaml = isinstance(parsed, dict)
        except Exception as e:                                # noqa: BLE001
            ok_yaml = False
            parsed = {}
            check("YAML 可解析", False, e)
        check("YAML 可解析且顶层是映射", ok_yaml)
        if ok_yaml:
            check("gates 是映射（不是标量）", isinstance(parsed.get("gates"), dict),
                  type(parsed.get("gates")).__name__)
            mem = ((parsed.get("context") or {}).get("memory") or {})
            check("context.memory 补成嵌套结构（不是散在文件末尾）",
                  mem.get("rolling_chars") == 2600, mem)
            check("dsh 段结构完整", isinstance(parsed.get("dsh"), dict)
                  and parsed["dsh"].get("timeout") == 1800, parsed.get("dsh"))

        # ---------- [5] 幂等：再跑一次什么都不该改 ----------
        print("\n[5] 幂等（跑第二遍不许再动任何东西）")
        text_after_first = after_text
        r2 = ck.apply(ws, fa)
        check("第二遍成功", r2.get("ok"), r2.get("error"))
        check("**第二遍没有新增**", not (r2.get("added") or []), r2.get("added"))
        check("**第二遍没有升级**", not (r2.get("upgraded") or []), r2.get("upgraded"))
        check("文件逐字未变",
              (ws / "config" / "system.yaml").read_text(encoding="utf-8")
              == text_after_first)

        # ---------- [6] C2=B：关键键缺失要报 ----------
        print("\n[6] C2=B：关键键缺失要列出来（不再静默兼容）")
        miss2 = ck.critical_missing(ws)
        texts = {m["key"] for m in miss2}
        check("补完后关键键不再报缺（max_total_tokens 用户有值）",
              "budget.token_limit.max_total_tokens" not in texts, texts)
        # 造一个什么都缺的
        ws2 = tmp / "ws2"
        (ws2 / "config").mkdir(parents=True)
        (ws2 / "config" / "system.yaml").write_text("chapter:\n  enhanced_mode: false\n",
                                                    encoding="utf-8")
        miss3 = ck.critical_missing(ws2)
        keys3 = {m["key"] for m in miss3}
        check("空配置：关键键全部列出", len(miss3) >= 5, sorted(keys3))
        check("每条都带“为什么缺失有害”",
              all(m.get("why") for m in miss3), miss3[:2])
        check("segment_approval 在列（真机踩过的那个）",
              "gates.segment_approval" in keys3, sorted(keys3))

        # ---------- [7] 备份 ----------
        print("\n[7] 写前备份")
        check("产出了备份文件", bool(r.get("backup")) and Path(r["backup"]).exists(),
              r.get("backup"))
        if r.get("backup") and Path(r["backup"]).exists():
            bak = Path(r["backup"]).read_text(encoding="utf-8")
            check("备份是**改动前**的内容",
                  "segment_approval" not in bak, "备份里已经含新键 —— 备份时机错了")

        # ---------- [8] dry_run 不写盘 ----------
        print("\n[8] dry_run 绝不写盘")
        tmp2 = Path(tempfile.mkdtemp(prefix="nf_ck_dry_"))
        ws3, fa3 = build(tmp2)
        before3 = (ws3 / "config" / "system.yaml").read_text(encoding="utf-8")
        r3 = ck.apply(ws3, fa3, dry_run=True)
        check("dry_run 报告会做什么", len(r3.get("added") or []) > 0, r3.get("added"))
        check("**文件一字未改**",
              (ws3 / "config" / "system.yaml").read_text(encoding="utf-8") == before3)
        shutil.rmtree(tmp2, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    print("结果：PASS=%d  FAIL=%d" % (PASS, FAIL))
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
