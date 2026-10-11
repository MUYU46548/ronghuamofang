# -*- coding: utf-8 -*-
"""完成判定挂产物 + 跳过分支（2026-10-11 欠账甲2）。

## 为什么需要它

15:52 实证事故：**0 行 polish_report.md 走完审批门** —— 旧判据只有 `exists()`
（文件在、内容空 = 放行）。甲2 要求完成判定分两态：
「真完成」= 产物存在**且非空**；「人工跳过」= 显式 skip 标记 + 理由，GUI 两态可辨。

## 判据

A. `utils/stage_artifacts.missing_artifacts` 六态：缺失 / 空文件 / 全空白文件 /
   正常文件 / 空目录 / 有货目录；未登记阶段 → 空列表。
B. `nf_api.missing_artifacts`（ROOT 包装）：`_set_root(夹具)` 后判定跟着 ROOT 走。
C. `orchestrator.artifact_gate`：空产物 → 返回错误串**且**阶段被标 failed；
   正常产物 → None 且状态不变。
D. 咽喉插桩顺序（静态）：run() 里 `artifact_gate` 调用必须在 `_post_stage` 之前
   —— 顺序反了就等于给已进审批门的产物补章，闸门形同虚设。
E. 跳过分支全链：`act_skip_stage` → progress 带 skipped/skip_reason/approved →
   `stage_status` 透出 skipped 与 skip_reason（GUI 两态可辨的数据契约）。

全程离线、零 LLM、零费用。
用法：python tests/unit/test_stage_artifact_gate.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.pop("NF_ROOT", None)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def main():
    from utils.stage_artifacts import missing_artifacts, STAGE_ARTIFACTS
    from utils.progress_manager import ProgressManager

    tmp = Path(tempfile.mkdtemp(prefix="nf_artifact_gate_"))
    rel = lambda *a: tmp.joinpath(*a)   # noqa: E731

    print("【A】missing_artifacts 六态")
    # 准备 stage1 的 setting.json 四种形态，逐态断言
    rel("data", "setting").mkdir(parents=True)
    check("缺失 → 记缺失", missing_artifacts(1, tmp) == ["data/setting/setting.json"],
          missing_artifacts(1, tmp))
    rel("data", "setting", "setting.json").write_text("", encoding="utf-8")
    check("0 字节空文件 → 记空文件", missing_artifacts(1, tmp) == ["data/setting/setting.json（空文件）"],
          missing_artifacts(1, tmp))
    rel("data", "setting", "setting.json").write_text("  \n\t\n", encoding="utf-8")
    check("全空白文件 → 记空文件", missing_artifacts(1, tmp) == ["data/setting/setting.json（空文件）"],
          missing_artifacts(1, tmp))
    rel("data", "setting", "setting.json").write_text("{\"ok\": 1}", encoding="utf-8")
    check("正常文件 → 放行", missing_artifacts(1, tmp) == [], missing_artifacts(1, tmp))
    rel("data", "outline", "chapters").mkdir(parents=True)
    check("空目录 → 记空目录", missing_artifacts(3, tmp) == ["data/outline/chapters（空目录）"],
          missing_artifacts(3, tmp))
    rel("data", "outline", "chapters", "01.md").write_text("# 第1章", encoding="utf-8")
    check("有货目录 → 放行", missing_artifacts(3, tmp) == [], missing_artifacts(3, tmp))
    check("未登记阶段（如 8）→ 空列表", missing_artifacts(8, tmp) == [], missing_artifacts(8, tmp))
    check("产物表覆盖 1-7 阶段", set(STAGE_ARTIFACTS) == set(range(1, 8)), sorted(STAGE_ARTIFACTS))

    print("【B】nf_api 包装：判定跟 ROOT 走")
    import nf_api
    other = Path(tempfile.mkdtemp(prefix="nf_artifact_other_"))
    nf_api._set_root(other)
    try:
        # other 根下 setting.json 不存在 → 即使 cwd 是仓库、tmp 里有文件也不算数
        check("ROOT 未命中 → 记缺失", nf_api.missing_artifacts(1) == ["data/setting/setting.json"],
              nf_api.missing_artifacts(1))
        (other / "data" / "setting").mkdir(parents=True, exist_ok=True)
        (other / "data" / "setting" / "setting.json").write_text("{}\n", encoding="utf-8")
        check("ROOT 命中 → 放行", nf_api.missing_artifacts(1) == [], nf_api.missing_artifacts(1))
    finally:
        nf_api._set_root(ROOT)

    print("【C】orchestrator.artifact_gate 分支")
    import orchestrator
    pg_path = tmp / "data" / "state" / "progress.json"
    pg_path.parent.mkdir(parents=True, exist_ok=True)
    progress = ProgressManager(str(pg_path))
    rel("data", "setting", "setting.json").write_text("", encoding="utf-8")   # 置空
    # C0：未标完成的阶段不判（FakeStage/替身不碰 progress —— 闸门只推翻"完成标记"）
    err0 = orchestrator.artifact_gate(1, progress, root=str(tmp))
    check("未标完成 → None（不越权替替身定失败）", err0 is None, err0)
    check("且状态保持非 failed", progress.stage_status(1) != "failed",
          progress.stage_status(1))
    # C1：标了完成但产物空 → 推翻
    progress.set_stage(1, "done")
    err = orchestrator.artifact_gate(1, progress, root=str(tmp))
    check("done+空产物 → 返回错误串", bool(err) and "产物校验未过" in (err or ""), err)
    check("done+空产物 → 推翻为 failed", progress.stage_status(1) == "failed",
          progress.stage_status(1))
    # C2：done + 正常产物 → 放行
    rel("data", "setting", "setting.json").write_text("{\"ok\": 1}", encoding="utf-8")
    progress.set_stage(1, "done")
    err2 = orchestrator.artifact_gate(1, progress, root=str(tmp))
    check("done+正常产物 → None 放行", err2 is None, err2)
    check("状态仍为 done", progress.stage_status(1) == "done", progress.stage_status(1))

    print("【D】静态：artifact_gate 插在 _post_stage 之前（顺序即生命线）")
    src = (ROOT / "scripts" / "orchestrator.py").read_text(encoding="utf-8")
    i_gate = src.find("artifact_gate(n, progress)")
    i_post = src.find("_code = _post_stage(n)")
    check("两个锚点都在", i_gate != -1 and i_post != -1, (i_gate, i_post))
    check("gate 先于 post_stage", 0 < i_gate < i_post, (i_gate, i_post))

    print("【E】跳过分支全链（act_skip_stage → /state 透出）")
    # ⚠️ ProgressManager 构造时缓存 self.data → 必须在 skip 落盘**之后**再实例化，
    # 否则读到的是旧快照（本文件首版就踩了这个顺序坑）。
    nf_api._set_root(tmp)
    try:
        ok, msg = nf_api.act_skip_stage(6, reason="上游素材不足，经用户确认放行")
        check("skip 返回 True", ok is True, msg)
        check("msg 含人工跳过语义", "人工跳过" in msg and "产物缺失" in msg, msg)
        progress2 = ProgressManager(str(pg_path))
        st = nf_api.stage_status(progress2, 6)
        check("/state 透出 skipped=true", st.get("skipped") is True, st)
        check("/state 透出 skip_reason", st.get("skip_reason") == "上游素材不足，经用户确认放行", st)
        check("skip 即显式放行（approved=true）", st.get("approved") is True, st)
        check("skip 后 6 阶段产物缺失被点名", "data/chapters/refined" in msg, msg)
        # 两态可辨的数据契约：跳过态 status 仍是 done，但 skipped 标记把它与真完成区分开
        check("两态可辨：done+skipped ≠ 真完成", st.get("status") == "done" and st.get("skipped"), st)
    finally:
        nf_api._set_root(ROOT)

    print("\n  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
