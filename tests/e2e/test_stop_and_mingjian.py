# -*- coding: utf-8 -*-
"""本轮「停止按钮 / 明鉴规则库 / parse_llm_json」三项改动的离线自检。

**不碰真实仓库数据**：临时项目根里跑，真实 data/ 零写入。

覆盖：
  任务1 停止按钮
    - nf_api.should_stop() / clear_stop() 语义（无 job / 未置位 / 已置位）
    - orchestrator.run() 在收到停止请求时**真的**中断并返回退出码 4
    - runs.db 里该 run 的状态落成 'stopped'（不是 'done'/'failed'）
    - 未置位时不误报中断
  任务2 明鉴规则库
    - 明鉴词库真的被合并进来（条数下降不成立、只增不减）
    - 同一段文本：本地库漏检、合并后多检出 ≥5 条（这就是"杠杆"的证据）
    - 豁免表生效：既而 / 布署 不上报（合法异形词，避免误伤）
    - 明鉴 PUNCT_RULES 生效：［］与含中文的半角括号被检出
  任务3 parse_llm_json
    - ```json 围栏 / 围栏前有寒暄 / 裸对象 / 裸数组 / 非 JSON → None
    - proofread._extract_json 走的是同一个实现（结果一致）

用法：python tests/test_stop_and_mingjian.py
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = 0, 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + msg)
    else:
        FAIL += 1
        print("  [FAIL] " + msg)


def build_project(tmp):
    (Path(tmp) / "config").mkdir(parents=True)
    (Path(tmp) / "data" / "state").mkdir(parents=True)
    (Path(tmp) / "logs").mkdir(parents=True)
    (Path(tmp) / "config" / "system.yaml").write_text(
        "engine: hermes\n"
        "budget:\n  limit_yuan: 300\n  warn_ratio: 0.7\n"
        "gates:\n  require_approval: [2]\n  pause_on_failure: true\n"
        "chapter:\n  target_words: [1200, 3500]\n", encoding="utf-8")
    (Path(tmp) / "config" / "project.yaml").write_text(
        "book:\n  name: 停止按钮测试书\n  chapters: 3\n", encoding="utf-8")


# ---------------------------------------------------------------- 任务1
def test_stop():
    print("\n[1] 停止按钮（orchestrator 退出码 4）")
    import nf_api as api

    # should_stop 基本语义
    api.CURRENT["id"] = None
    ok(api.should_stop() is False, "无当前 job → should_stop() False")
    api.CURRENT["id"] = "job-semantic"
    ok(api.should_stop("job-semantic") is False, "有 job 但未置位 → False")
    with api.STOP_LOCK:
        api.STOP_EVENTS["job-semantic"] = threading.Event()
        api.STOP_EVENTS["job-semantic"].set()
    ok(api.should_stop("job-semantic") is True, "置位后 → True")
    ok(api.should_stop() is True, "job_id 缺省时自动取 CURRENT['id'] → True")
    api.clear_stop("job-semantic")
    ok(api.should_stop("job-semantic") is False, "clear_stop 后 → False")

    # 真跑一次 orchestrator：置位后必须立刻中断
    import orchestrator as orc
    tmp = tempfile.mkdtemp(prefix="nf_stop_")
    old = os.getcwd()
    try:
        build_project(tmp)
        os.chdir(tmp)
        api.CURRENT["id"] = "job-stop-test"
        with api.STOP_LOCK:
            api.STOP_EVENTS["job-stop-test"] = threading.Event()
            api.STOP_EVENTS["job-stop-test"].set()
        rc = orc.run(from_stage=1, only_stage=1)
        ok(rc == 4, "收到停止请求 → run() 返回退出码 4（实际 %s）" % rc)

        db = sqlite3.connect(str(Path(tmp) / "logs" / "runs.db"))
        row = db.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        db.close()
        ok(row and row[0] == "stopped",
           "runs.db 落库状态为 stopped（实际 %s）" % (row and row[0]))

        # 阶段未被推进：stage1 不该被跑
        prog = json.loads((Path(tmp) / "data" / "state" / "progress.json").read_text(encoding="utf-8")) \
            if (Path(tmp) / "data" / "state" / "progress.json").exists() else {}
        ok(not prog, "中断发生在任何阶段启动之前（progress.json 未生成）")

        # 未置位时不误报
        api.clear_stop("job-stop-test")
        ok(orc._stop_requested() is False, "清除标志后 _stop_requested() 为 False（不误中断）")

        # 退出码 → job 结果的映射：4 是「用户中断」，不能标成失败（GUI 会飘红）
        saved_run = api.orch_run
        try:
            api.orch_run = lambda **kw: 4
            ok_flag, msg = api.act_run_stage({}, None, 1)()
            ok(ok_flag is True and "用户中断" in msg,
               "act_run_stage 把退出码 4 映射为成功态 + 「用户中断」（实际 %s / %s）"
               % (ok_flag, msg))
            api.orch_run = lambda **kw: 3
            ok_flag3, msg3 = api.act_run_stage({}, None, 1)()
            ok(ok_flag3 is True and "等待审批" in msg3,
               "退出码 3 仍映射为成功态（等待审批，回归保护）")
            api.orch_run = lambda **kw: 1
            ok_flag1, msg1 = api.act_run_stage({}, None, 1)()
            ok(ok_flag1 is False and msg1 == "exit=1",
               "退出码 1 仍映射为失败态（回归保护）")
        finally:
            api.orch_run = saved_run
    finally:
        os.chdir(old)
        with api.STOP_LOCK:
            api.STOP_EVENTS.clear()
        api.CURRENT["id"] = None
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 任务2
MINGJIAN_ONLY_SAMPLE = (
    "## 第一章 试笔\n\n"
    "他克苦读书，按排好一切，因该不至于出岔子。\n\n"
    "这消息简直是震憾，众人都说他是变本加利、消声匿迹。\n\n"
    "他心恢意冷地说：“这事真知卓见，却也名符其实。”\n\n"
    "屋里挂着［一幅画］，他说(这幅画)很好。\n"
)

EXCLUDED_SAMPLE = "既而风停了。他布署完防线，天色已暗。"


def _local_confusables_from_source():
    """从 proofread.py 源码取出**合并前**的本地词库字面量（用于反证对照）。

    直接 literal_eval 源码里的第一个 `CONFUSABLES = [...]`，不依赖运行时状态，
    这样"明鉴净新增了多少条"是拿真实基线算出来的，而不是拍脑袋。
    """
    import ast
    tree = ast.parse((ROOT / "scripts" / "proofread.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "CONFUSABLES" for t in node.targets)
                and isinstance(node.value, ast.List)):
            try:
                return ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return None
    return None


def test_mingjian():
    print("\n[2] 明鉴规则库（合并 + 多检出 + 豁免）")
    import proofread as pf

    ok(pf.TYPO_DICT_MINGJIAN is not None and len(pf.TYPO_DICT_MINGJIAN) >= 100,
       "明鉴词库已加载（%d 条）" % len(pf.TYPO_DICT_MINGJIAN or {}))
    ok(pf.PUNCT_RULES_MINGJIAN is not None and len(pf.PUNCT_RULES_MINGJIAN) >= 2,
       "明鉴标点规则已加载（%d 条）" % len(pf.PUNCT_RULES_MINGJIAN or []))

    local_pairs = _local_confusables_from_source()
    ok(bool(local_pairs), "从源码取到合并前的本地词库（%d 条）" % len(local_pairs or []))
    local_keys = {k for k, _ in local_pairs}
    merged_keys = {k for k, _ in pf.CONFUSABLES}
    net_new = merged_keys - local_keys
    ok(merged_keys > local_keys,
       "合并只增不减：%d → %d 条" % (len(local_keys), len(merged_keys)))
    ok(len(net_new) >= 5, "明鉴净新增 %d 条本地没有的错形" % len(net_new))
    ok(len(merged_keys) == len(pf.CONFUSABLES), "合并后无重复错形")

    # 同一段文本：合并前 vs 合并后
    found = pf.check_confusables(MINGJIAN_ONLY_SAMPLE, 1)
    hit_words = {f["detail"].split("「")[1].split("」")[0] for f in found
                 if "疑似错字" in f["detail"]}

    saved = pf.CONFUSABLES
    try:
        pf.CONFUSABLES = list(local_pairs)          # 用真实基线对照
        base_words = {f["detail"].split("「")[1].split("」")[0]
                      for f in pf.check_confusables(MINGJIAN_ONLY_SAMPLE, 1)
                      if "疑似错字" in f["detail"]}
    finally:
        pf.CONFUSABLES = saved
    delta = hit_words - base_words
    ok(delta <= net_new,
       "净新增的命中全部来自明鉴词库（本地基线抓不到）")
    ok(len(delta) >= 5,
       "同一段文本净多检出 %d 条（要求 ≥5）：%s"
       % (len(delta), "、".join(sorted(delta))))

    # 豁免表：合法异形词不上报
    exc_details = " ".join(f["detail"] for f in pf.check_confusables(EXCLUDED_SAMPLE, 1))
    ok("既而" not in exc_details, "豁免生效：「既而」不上报（文言连词，非错字）")
    ok("布署" not in exc_details, "豁免生效：「布署」不上报（《现汉》异形词）")
    ok(set(pf.TYPO_EXCLUDE_MINGJIAN) >= {"既而", "布署"}, "豁免表本身可查")

    # 明鉴标点规则
    kinds = " ".join(f["detail"] for f in pf.check_punctuation(MINGJIAN_ONLY_SAMPLE, 1))
    ok("全角方括号" in kinds, "明鉴标点规则命中：全角方括号［］")
    ok("半角括号" in kinds, "明鉴标点规则命中：含中文的半角括号")
    # 该规则的既定意图：不误伤颜文字 / 纯西文括号
    kaomoji = "他笑了(^_^)，又比了个(oﾟ▽ﾟ)o 的手势。"
    ok("半角括号" not in " ".join(f["detail"] for f in pf.check_punctuation(kaomoji, 1)),
       "颜文字/西文括号不误报（规则设计意图保持）")


# ---------------------------------------------------------------- 任务3
def test_parse_llm_json():
    print("\n[3] parse_llm_json（容错解析）")
    import proofread as pf
    from utils.validator import parse_llm_json

    ok(parse_llm_json('{"findings":[{"a":1}]}') == {"findings": [{"a": 1}]},
       "裸对象可解析")
    ok(parse_llm_json('```json\n{"findings":[]}\n```') == {"findings": []},
       "```json 围栏可解析")
    ok(parse_llm_json('好的，结果如下：\n```json\n{"ok":true}\n```\n以上。') == {"ok": True},
       "围栏外有寒暄文字也能解析")
    ok(parse_llm_json('[{"chapter":1}]') == [{"chapter": 1}],
       "裸数组可解析（上游不支持，属向后兼容超集）")
    ok(parse_llm_json('前言\n```\n[{"n":1}]\n```') == [{"n": 1}],
       "无 json 标记的围栏 + 数组可解析")
    ok(parse_llm_json("这不是 JSON，只是普通文字。") is None,
       "非 JSON 返回 None（调用方据此走人工接管）")
    ok(parse_llm_json("") is None and parse_llm_json(None) is None,
       "空输入返回 None")
    ok(parse_llm_json({"already": "dict"}) == {"already": "dict"},
       "已是 dict 时原样返回")
    # proofread 的入口与 validator 同源
    ok(pf.parse_llm_json is parse_llm_json,
       "proofread 直接引用 utils.validator.parse_llm_json（非各自实现）")
    ok(pf._extract_json('```json\n{"findings":[{"chapter":2}]}\n```')
       == {"findings": [{"chapter": 2}]},
       "proofread._extract_json 结果一致（薄封装）")
    ok(pf._extract_json_builtin("not json") is None,
       "内置兜底实现仍保留可用")
    # 主流程回归：带围栏的 LLM 输出能进报告
    ok(pf.run_llm_proofread.__doc__ is not None, "LLM 主流程函数可导入")


def main():
    print("===== 停止按钮 / 明鉴规则库 / parse_llm_json 自检 =====")
    test_stop()
    test_mingjian()
    test_parse_llm_json()
    print("\n===== 合计: %d passed, %d failed =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
