# -*- coding: utf-8 -*-
"""engine: self 下“要模型”的按钮必须说人话（2026-11-04，试跑第 19/21 条）。

## 要治的病（两条，同一个根）

作者为了避开 hermes 十分钟卡死，把引擎切成了 `engine: self`。而 self 的定义是
**程序不调用任何模型**，于是：

1. 所有“AI 加工”按钮（大纲精修 / 提交精修 / 段落重写 / 整章重写 / 自动重写 /
   第三条证据候选 / 定向候选 / 语义审稿）必然失败 —— 失败时露出的却是给开发者
   看的那句“发起调用的阶段代码走错了分支 —— 应在 orchestrator 的 self 分支里用
   emit_tasks”。作者没走错分支，他只是点了按钮。
2. 更糟的是 `/stage/N/run` 的入口有个 `_client_for_env(...)` 预检：self 下
   **直接 500**，而 self 的正确行为是“出任务书”（分支在 orchestrator 里）。
   等于 self 引擎下连“运行阶段”都点不动。

## 判据（每条都能变红）

- B1 self → 人话：点名“只出任务书”、给出两条出路（切引擎 / 用任务书），
  且**不出现**“走错了分支”这种开发者措辞
- B2 direct / hermes → 不拦（返回空串）
- B3 清单里每条路径在 nf_api 里**真有路由**（防清单写错路径 → 拦不住）
- B4 `/stage/N/run` 不在清单（self 下它该出任务书，不是失败）
- B5 do_POST 真的接了判据（返回 400 + `engine: self` + 审计留痕）
- B6 `/stage/N/run` 的客户端预检按引擎分流（self 下不构造客户端）
- B8 `_client_or_none_for_self` 只有一份实现、三处跑阶段路径都接上
- B9 真机结论留档：self 夹具起 18786 端口实测 —— `POST /stage/1/run` 返回
  **202 + job_id**（此前是 500），`POST /refine/chapter` 返回 400 + 人话

用法：python tests/unit/test_self_engine_block.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import engine as E                                      # noqa: E402
import nf_api                                                      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("" if cond else "  → %s" % (detail,))))


def main():
    print("=" * 66)
    print("engine: self 下的前置拒绝（人话）+ stage 预检分流")

    print("\n【B1】self → 人话，且不是开发者措辞")
    msg = E.needs_model_block_reason({"engine": "self"})
    check("B1 有话说", bool(msg.strip()), msg)
    check("B1 点名“只出任务书”", "只出任务书" in msg, msg)
    check("B1 给出两条出路（切引擎 / 用任务书）",
          "运行引擎" in msg and "任务书" in msg, msg)
    check("B1 不出现开发者措辞（走错了分支 / emit_tasks / run_stage）",
          not any(x in msg for x in ("走错了分支", "emit_tasks", "run_stage")), msg)

    print("\n【B2】direct / hermes / 缺键 → 不拦")
    for eng in ("direct", "hermes", "", None):
        cfg = {"engine": eng} if eng is not None else {}
        check("B2 %r 放行" % (eng,), E.needs_model_block_reason(cfg) == "",
              E.needs_model_block_reason(cfg))

    print("\n【B3】清单里每条路径都真有路由（防写错路径 → 拦不住）")
    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    missing = [p for p in nf_api.NEEDS_MODEL_ENDPOINTS
               if ('p == "%s"' % p) not in src]
    check("B3 清单与路由一致", not missing, missing)
    check("B3 清单至少覆盖用户点过的 4 个按钮",
          all(x in nf_api.NEEDS_MODEL_ENDPOINTS for x in
              ("/refine/outline/node", "/chapters/rewrite",
               "/review/semantic", "/auto_rewrite/run")), sorted(nf_api.NEEDS_MODEL_ENDPOINTS))
    # 2026-11-08（0.9.1 验收第 9 条）：大纲精修在 self 下**不再拦**，
    # 它改走“出任务书”（见 tests/unit/test_self_refine_outline.py）。
    # 谁把它加回名单，就等于把外部 agent 改大纲这条路重新堵死。
    # 2026-11-08：提精修（/refine/chapter）也改成了按钮级派单（任务书 + 草稿）。
    check("B3.0b /refine/chapter 不在名单（self 下要能出任务书）",
          "/refine/chapter" not in nf_api.NEEDS_MODEL_ENDPOINTS,
          sorted(nf_api.NEEDS_MODEL_ENDPOINTS))
    check("B3.0 /refine/outline 不在名单（self 下要能出任务书）",
          "/refine/outline" not in nf_api.NEEDS_MODEL_ENDPOINTS,
          sorted(nf_api.NEEDS_MODEL_ENDPOINTS))
    # 2026-11-08（0.9.1 验收第 13 条）：作者在 self 下点“运行审查”，此前是静默失败
    # （进 job 创建 → _client_for_env 抛 → 500/无提示）。这两条必须同族前置拦下。
    check("B3.1 /review/run 与 /batch_refine/run 已登记（不再静默 500）",
          all(x in nf_api.NEEDS_MODEL_ENDPOINTS for x in
              ("/review/run", "/batch_refine/run")),
          sorted(nf_api.NEEDS_MODEL_ENDPOINTS))

    print("\n【B4】/stage/N/run 不在清单（self 下该出任务书，不是失败）")
    check("B4 清单不含 /stage/4/run",
          nf_api.needs_model_endpoint("/stage/4/run") is False)
    check("B4 清单不含 /stage/1/run",
          nf_api.needs_model_endpoint("/stage/1/run") is False)

    print("\n【B5】do_POST 接线：400 + engine=self + 审计")
    check("B5 调了 needs_model_endpoint", "if needs_model_endpoint(p):" in src)
    check("B5 返回 400", '"ok": False, "error": _reason, "engine": "self"' in src)
    check("B5 留审计（engine-self）", 'detail="engine-self"' in src)
    check("B5 提示去哪切引擎", "设置 → 运行引擎" in src)

    print("\n【B6】/stage/N/run 的客户端预检按引擎分流")
    m = re.search(r"if _re_stage\(cfg\) != \"self\":\s*\n\s*_client_for_env\(cfg, \"default\"\)",
                  src)
    check("B6 self 下跳过 _client_for_env（否则点“运行阶段”直接 500）", m is not None)
    check("B6 旧的无条件预检已不存在",
          src.count('_client_for_env(cfg, "default")  # 测试模式预检') == 1,
          src.count('_client_for_env(cfg, "default")  # 测试模式预检'))

    print("\n【B8】“跑阶段”路径：self 下不构造客户端（否则点运行阶段直接 500）")
    check("B8 self → None（不构造）",
          nf_api._client_or_none_for_self({"engine": "self"}, "default") is None)
    check("B8 接线：三处跑阶段路径都用它",
          src.count('_client_or_none_for_self(cfg, "default")') >= 3,
          src.count('_client_or_none_for_self(cfg, "default")'))
    check("B8 定义处只有一份（不各写一套）",
          src.count("def _client_or_none_for_self(") == 1)

    print("\n【B9】真机验证结论（起 18786 端口真跑过，写在这里防回退）")
    check("B9 docstring 记着“202 + job_id”这条真机结论",
          "202 + job_id" in (__doc__ or ""), (__doc__ or "")[:60])

    print("\n【B7】反向：真的让 make_client 在 self 下炸（防有人把它改成静默回落）")
    try:
        from utils.llm_client import make_client
        make_client({"engine": "self"}, "default")
        check("B7 self 下 make_client 必须抛错", False, "居然没抛")
    except RuntimeError as e:
        check("B7 self 下 make_client 抛 RuntimeError", "self" in str(e), str(e)[:80])
    except Exception as e:                                     # noqa: BLE001
        check("B7 self 下 make_client 抛 RuntimeError", False, type(e).__name__)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
