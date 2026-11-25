# -*- coding: utf-8 -*-
"""DSH 唯一化 + 自检端点的判据（2026-11-24）。

## 为什么需要这套用例

三件事都是"会静默出错、而且只有作者倒霉时才发现"的类型：

1. `DshClient` 缺 `provider` → 成本按 `unknown` 记账 → 走订阅（本该 ¥0）却记出金额；
2. dsh 没有 max_tokens 旋钮，stage4 却拿"我们设不了的上限"判容量 → 未调用就判失败；
3. `/state` 不透出 `stages[].error` → 界面上的失败永远只剩一句 "exit=1"。

外加两个新端点的纯函数（preflight / receipt）：它们必须在**没有模型、没有网络**时
也能算出确定结论 —— 否则"自检"本身就成了又一个要人盯的东西。

全程离线：不调模型、不花额度、不写项目数据（临时目录）。
用法：python tests/unit/test_dsh_only_and_selftest.py
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + (("  —— " + str(extra)) if extra else ""))


print("=" * 66)
print("【D1】DshClient 自报身份：provider / display_name / max_tokens_knob")
print("=" * 66)
from utils.dsh_client import DshClient                                 # noqa: E402

c = DshClient(dsh_bin="dsh")
check("D1a provider == 'dsh'（成本归零分支靠它命中）", c.provider == "dsh", c.provider)
check("D1b provider 是类属性（不必实例化也能读）", DshClient.provider == "dsh")
check("D1c display_name 有值", bool(DshClient.display_name))
check("D1d max_tokens_knob 为 False（dsh 命令行没有这个旋钮）",
      DshClient.max_tokens_knob is False, DshClient.max_tokens_knob)

# 反证：真去算一次成本，provider="dsh" 必须归零。改了 provider 名字这条会红。
from utils.cost_tracker import estimate_cost_yuan                         # noqa: E402

_est = estimate_cost_yuan(100000, 20000, model="dsh-default",
                          provider="dsh", role="writer")
check("D1e 订阅引擎算出来是 0 元", float(_est or 0) == 0.0, _est)

print()
print("=" * 66)
print("【D2】stage4 容量闸门：没旋钮的引擎必须跳过（否则未跑先判失败）")
print("=" * 66)
src4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
check("D2a 判据挂在客户端能力上（getattr(client, 'max_tokens_knob'...)",
      'getattr(client, "max_tokens_knob"' in src4)
check("D2b 跳过分支真的绕开了 check_capacity",
      src4.index("篇幅容量校验跳过") < src4.index("check_capacity(proj, 4"))
check("D2c 有旋钮时仍走原校验（ensure 没被整体删掉）",
      "_ok4, _why4, _cap4 = check_capacity" in src4)

print()
print("=" * 66)
print("【D3】/state 透出 stages[].error（界面要说得出为什么失败）")
print("=" * 66)
import nf_api as api                                                   # noqa: E402


class _FakeProg:
    def __init__(self, stages):
        self.data = {"stages": stages}


prog = _FakeProg({"4": {"status": "failed", "error": "目标字数 7200 装不进输出上限 4000"}})
st = api.stage_status(prog, 4, total_chapters=10)
check("D3a error 出现在 /state 的阶段字段里",
      st.get("error", "").startswith("目标字数"), st)
check("D3b 长 error 截断到 800 字（不能把 traceback 灌进 2.5 秒一次的轮询）",
      len(api.stage_status(_FakeProg({"4": {"status": "failed", "error": "x" * 5000}}), 4)["error"]) <= 800)
check("D3c 没有 error 时不硬塞一个空串",
      "error" not in api.stage_status(_FakeProg({"4": {"status": "done"}}), 4))

print()
print("=" * 66)
print("【D4】自检体检 preflight：结论必须来自事实，不能靠猜")
print("=" * 66)
from nf_api_domains import selftest as S                                # noqa: E402

_cfg = {"engine": "dsh", "dsh": {"profile": "headless"},
        "book": {"name": "测试书", "chapters": 5}}
_orig_load = api.load_all


def _fake_load():
    return [_cfg, None, None]


api.load_all = _fake_load
try:
    pf = S.build_preflight(_cfg)
    keys = [it["key"] for it in pf["items"]]
    check("D4a 五项齐（许可/引擎/DSH/配置档/书/素材/前置）",
          all(k in keys for k in ("license", "engine", "dsh", "profile", "book",
                                  "materials", "prereq")), keys)
    eng = [it for it in pf["items"] if it["key"] == "engine"][0]
    check("D4b engine=dsh 时该项通过", eng["ok"] is True, eng)
    # 反证：换成 direct 必须红，否则“引擎固定 dsh”在体检里根本没有判据
    # 2026-11-25（F8）：direct 已下线，resolve_engine 把它收敛成 dsh ——
    # 这条反证改成"收敛说明"：引擎项现在只区分 dsh / 非 dsh，不再有 direct 分支。
    pf2 = S.build_preflight({**_cfg, "engine": "direct"})
    eng2 = [it for it in pf2["items"] if it["key"] == "engine"][0]
    check("D4c engine=direct 已下线 → 收敛为 dsh（不再是独立选项）",
          eng2["ok"] is True and "dsh" in eng2["detail"], eng2)
    check("D4d can_run 是各项的与（有 block 项红就不能跑）",
          pf2["can_run"] is False, pf2["can_run"])
    pr = [it for it in pf["items"] if it["key"] == "prereq"][0]
    check("D4e 前置阶段没跑完时点名是哪几个", pr["ok"] is False and "1" in pr["detail"], pr)
    # profile 判据（2026-11-24 翻转）：desktop 档被 DSH 拒绝命令行调用，
    # 填它等于跑不起来 —— 体检必须红，不能给绿。
    pf_head = [it for it in pf["items"] if it["key"] == "profile"][0]
    check("D4f headless 档通过", pf_head["ok"] is True, pf_head)
    pf_desk = S.build_preflight({**_cfg, "dsh": {"profile": "desktop"}})
    it_desk = [it for it in pf_desk["items"] if it["key"] == "profile"][0]
    check("D4g desktop 档必须红（命令行调不通）",
          it_desk["ok"] is False and it_desk["level"] == "block", it_desk)
finally:
    api.load_all = _orig_load

print()
print("=" * 66)
print("【D5】验收收据 receipt：读盘得结论，不需要人看 progress.json")
print("=" * 66)
_tmp = Path(tempfile.mkdtemp(prefix="nf_selftest_"))
try:
    _raw = _tmp / "data" / "chapters" / "raw"
    _raw.mkdir(parents=True)
    (_raw / "01.md").write_text("# 第1章 开头\n\n" + ("字" * 4500) + "\n", encoding="utf-8")

    _orig_root = api.ROOT
    api.ROOT = _tmp
    try:
        r = S.build_receipt(1)
        check("D5a 没有 runs.db 时不出假结论（level=none）",
              r["verdict"]["level"] == "none", r["verdict"])
        check("D5b 仍能报出正文汉字数（读盘，不依赖日志）",
              (r.get("file") or {}).get("hanzi", 0) >= 4000, r.get("file"))
        check("D5c 缺文件时明确说不存在（不当成 0 字）",
              (S.build_receipt(99).get("file") or {}).get("exists") is False,
              S.build_receipt(99).get("file"))
    finally:
        api.ROOT = _orig_root
finally:
    import shutil
    shutil.rmtree(_tmp, ignore_errors=True)

print()
print("=" * 66)
print("【D6】审批门写口支持 material_autonomy（原来只能手改 YAML）")
print("=" * 66)
srcp = (ROOT / "scripts" / "nf_api_domains" / "project.py").read_text(encoding="utf-8")
check("D6a 写口接受 material_autonomy", 'body.get("material_autonomy"' in srcp)
check("D6b 两个键一次写入（一次备份、一次回读校验）",
      'vals = {"require_approval": clean}' in srcp and 'vals["material_autonomy"]' in srcp)
check("D6c 不传该键时不动它（老调用方与测试零影响）",
      "if isinstance(auto, bool):" in srcp)

srcv = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
check("D6d 界面有开关并会提交它",
      "material_autonomy: !!gateCfg.value.material_autonomy" in srcv)
check("D6e 向导默认 DSH（不再逼用户选、也不再建议 direct）",
      'const initEngine = ref("dsh")' in srcv
      and "第一次试跑建议" not in srcv)

print()
print("=" * 66)
print("【D7】审批门闭环：确认放行后自己往下跑")
print("=" * 66)
check("D7a approve 带 continueRun 参数", "continueRun = true" in srcv)
check("D7b 确认后调用 doRunStage（不弹二次预估框）",
      'await doRunStage(nx.stage)' in srcv)
check("D7c 收件箱主按钮是“确认放行并继续”", "确认放行并继续" in srcv)
check("D7d 保留“只确认”旁路", "只确认" in srcv)

src5 = (ROOT / "scripts" / "nf_api_domains" / "stage5.py").read_text(encoding="utf-8")
check("D7e stage5 合成完会推进阶段状态（否则永远 exit=3 打转）",
      "_advance_if_finished" in src5 and "set_stage(5, \"done\")" in src5)

print()
print("-" * 66)
print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + "；".join(FAIL))
    sys.exit(1)
