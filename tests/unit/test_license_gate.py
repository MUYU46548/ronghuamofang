# -*- coding: utf-8 -*-
"""许可协议总闸：**未确认就不许调模型**（2026-11-18）。

## 为什么要有这条

审计（`docs/审计-AI调用入口与授权闸-20261118.md`）之前，“用户同意过免责声明”
只存在渲染层 localStorage，后端 / MCP / CLI 一处都不读 —— 任何本机进程
一条 POST 或一次脚本调用就能让程序开始花钱。现在判据落在
`scripts/utils/license.py`，本用例钉住四件事：

  L1 未确认：`make_client()` 必须抛错（不是打 WARN、不是静默降级）
  L2 签过：`record()` 落盘后 status.acked=True，且文件里记的是**服务端**的版本与指纹
  L3 条款改版：版本或指纹不符 → 旧记录失效（stale），重新拦
  L4 测试豁免：`NF_LICENSE_ACK=1` 才放行，且 state 里标 bypass（不是假装签过）
  L5 两边同源：Python 常量必须与 `console/src/disclaimer.js` 一致（改文案忘同步 → 红）

反证（会变红）：把 `make_client` 里的许可判定删掉 → L1 直接红；
把 Python 常量改成任意别的值 → L5 红。

用法：python tests/unit/test_license_gate.py
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import license as lic              # noqa: E402
from utils.llm_client import make_client      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def fresh_root():
    tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="nf_lic_")))
    (tmp / "config").mkdir(parents=True)
    return tmp


def main():
    # 反证前提：本用例必须跑在**没有**测试豁免的环境里，否则 L1/L3 永远绿。
    os.environ.pop(lic.ENV_BYPASS, None)
    tmp = fresh_root()
    os.environ["NF_ROOT"] = str(tmp)
    try:
        print("\n【L1】未确认：调模型必须当场失败")
        st = lic.state()
        check("state.acked=False", st["acked"] is False, st)
        check("bypass=False", st["bypass"] is False, st)
        raised = ""
        try:
            make_client({"engine": "direct", "providers": {}})
        except RuntimeError as e:                            # noqa: BLE001
            raised = str(e)
        check("make_client 抛 RuntimeError", bool(raised), raised[:120])
        check("拒文说清去哪确认（不是开发者黑话）",
              "桌面端" in raised and "同意" in raised, raised[:160])

        print("\n【L2】签字：记录落盘、版本与指纹由服务端决定")
        ok, res = lic.record(source="gui")
        check("record 成功", ok is True, res)
        p = lic.ack_path()
        check("同意记录写在 config/license_ack.json",
              p == tmp / "config" / "license_ack.json", str(p))
        data = json.loads(p.read_text(encoding="utf-8"))
        check("版本 = 服务端常量", str(data.get("version")) == lic.LICENSE_VERSION, data)
        check("指纹 = 服务端常量", data.get("sha") == lic.LICENSE_CONTENT_SHA, data)
        check("带签署时间与来源", bool(data.get("at")) and data.get("source") == "gui", data)
        check("签后 acked=True", lic.state()["acked"] is True, lic.state())
        check("签后 make_client 不再被许可闸拦",
              _not_license_error({"engine": "direct", "providers": {}}), "仍抛许可错")

        print("\n【L3】条款改版：旧记录自动失效（stale）")
        data["version"] = "0.9"                     # 模拟“签的是旧版”
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        st = lic.state()
        check("旧版本 → acked=False", st["acked"] is False, st)
        check("旧版本 → stale=True（界面据此说‘条款已更新’）", st["stale"] is True, st)
        data["version"] = lic.LICENSE_VERSION
        data["sha"] = "fedcba987654"                # 模拟“正文改过、版本没升”
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        check("指纹不符 → 同样失效", lic.state()["acked"] is False, lic.state())

        print("\n【L4】测试豁免：只认 NF_LICENSE_ACK=1，而且要标出来")
        lic.clear()
        check("清掉记录后回到未确认", lic.state()["acked"] is False, lic.state())
        os.environ[lic.ENV_BYPASS] = "0"
        check("值不是 1 不生效", lic.state()["acked"] is False, lic.state())
        os.environ[lic.ENV_BYPASS] = "1"
        st = lic.state()
        check("=1 时放行", st["acked"] is True, st)
        check("明确标 bypass（不伪装成‘用户签过’）", st["bypass"] is True, st)
        check("bypass 时 ack 记录仍为空", not st.get("ack"), st.get("ack"))
        os.environ.pop(lic.ENV_BYPASS, None)

        print("\n【L5】两边同源：Python 常量 = console/src/disclaimer.js")
        js = (ROOT / "console" / "src" / "disclaimer.js").read_bytes()
        text = js.decode("utf-8")
        m = re.search(r'export const DISCLAIMER_VERSION\s*=\s*([0-9.]+)', text)
        check("能读到前端版本号", bool(m), text[:80])
        if m:
            check("版本一致（%s）" % lic.LICENSE_VERSION,
                  str(m.group(1)) == lic.LICENSE_VERSION, m.group(1))
        m2 = re.search(r'export const DISCLAIMER_CONTENT_SHA\s*=\s*"([0-9a-f]{12})"', text)
        check("能读到前端指纹", bool(m2), "")
        if m2:
            check("指纹一致（%s）" % lic.LICENSE_CONTENT_SHA,
                  m2.group(1) == lic.LICENSE_CONTENT_SHA, m2.group(1))
            # 指纹本身也要与正文一致（改字忘重算 → 红）
            MARK = b"export const DISCLAIMER_CONTENT_SHA"
            kept = [l for l in js.split(b"\n") if not l.startswith(MARK)]
            actual = hashlib.sha256(b"\n".join(kept)).hexdigest()[:12]
            check("前端指纹与正文一致", actual == m2.group(1),
                  "recorded=%s actual=%s" % (m2.group(1), actual))

        print("\n【L6】self_check 说人话（nfctl check 用）")
        ok2, why = lic.self_check()
        check("未确认时 ok=False", ok2 is False, why)
        check("文案指向桌面端而不是报错码", "桌面端" in why, why)
        lic.record(source="gui")
        ok3, why3 = lic.self_check()
        check("确认后 ok=True 且带版本", ok3 is True and "v" in why3, why3)

        print("\n【L7】授权之后：三个“开钱闸”旋钮仍在 Agent 禁用名单里")
        # 审计 §4.3：许可闸管“能不能开始花钱”，管不到“钱从哪个池走”。
        # 这三条必须留在 FORBIDDEN_IN_AGENT_MODE，否则签完字 Agent 仍能改计费通道。
        from utils.agent_guard import FORBIDDEN_IN_AGENT_MODE
        for p in ("/config/engine", "/config/provider", "/env/set"):
            check("%s 在禁用名单" % p, p in FORBIDDEN_IN_AGENT_MODE,
                  sorted(FORBIDDEN_IN_AGENT_MODE))
    finally:
        os.environ.pop("NF_ROOT", None)
        os.environ.pop(lic.ENV_BYPASS, None)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\ntest_license_gate: %d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


def _not_license_error(cfg):
    """make_client 是否**因为别的原因**失败（而不是许可闸）。

    许可签过之后它仍可能因 provider 配置不全而抛错 —— 那时错误文案里不该出现
    “确认许可协议”。用这个判据把“闸放行了”和“构造客户端又失败”分开。
    """
    try:
        make_client(cfg)
    except (SystemExit, RuntimeError) as e:
        # make_client 对“配置不全”用的是 SystemExit（见 DirectClient._validate_creds），
        # 对“许可未确认”用的是 RuntimeError —— 两种都要看文案：
        # 只要不是许可那句，就说明闸放行了。
        return "许可协议" not in str(e)
    except Exception:                                        # noqa: BLE001
        return True
    return True


if __name__ == "__main__":
    raise SystemExit(main())
