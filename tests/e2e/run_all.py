# -*- coding: utf-8 -*-
"""e2e 一键验收：起服务 → 跑套件 → 收尾，只杀自己起的 PID。

为什么要有它（2026-11-17，作者点名"改界面要手工起四个服务、失败还分不清真红还是环境红"）：

  1. `e2e_ux_verify.py` 要**四个**外部服务（静态 8091 / mock 8798 / 冷启动 mock 8797 /
     真后端 8799）。手工起服务时最容易漏一个，缺服务不会报错，只会以 30 秒超时的形式
     长出几十条假红 —— 看着像产品坏了，其实是没执行。
  2. mock 是有状态的假后端：上一轮"候选已采用"会留到下轮，按钮直接 disabled → 超时假红。
     本入口每轮**新起进程**，状态天然干净。
  3. 端口固定就会撞本机的常驻服务（8091 常被占）。撞了不许抢、不许杀别人的进程，
     所以这里**自动找空闲端口**，再通过环境变量告诉子脚本（子脚本已支持 env 覆盖）。

用法：
    python tests/e2e/run_all.py                 # 跑全部四套
    python tests/e2e/run_all.py --only ux        # 只跑全站 UX
    python tests/e2e/run_all.py --only metrics,discuss-api
    python tests/e2e/run_all.py --list           # 看有哪些套件
    python tests/e2e/run_all.py --keep-services  # 跑完留服务（调试用，自己负责收尾）

状态语义（项目纪律：缺依赖/缺服务 = 未执行 ≠ 失败）：
    PASS / FAIL / SKIP —— 只有 FAIL 让退出码非 0。
"""
from __future__ import annotations

import argparse
import io
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
E2E = ROOT / "tests" / "e2e"
DIST = ROOT / "console" / "renderer" / "dist"

# ---------------------------------------------------------------------------
# 套件登记表：一条记录 = 一个脚本 + 它要什么
#   stack=True  需要 run_all 起四个服务（静态 + mock + cold + 真后端）
#   browser=True 需要 playwright + 构建产物
#   ports       {环境变量名: 默认端口}，由本入口挑空闲端口后注入
# ---------------------------------------------------------------------------
SUITES = {
    "ux": {
        "script": "e2e_ux_verify.py",
        "desc": "全站 UX（工作流条 / 命令面板 / 快捷键 / 暗色 / 候选人 / 截图自检）",
        "stack": True,
        "browser": True,
        "env": {"NF_E2E_SITE": "http://127.0.0.1:{site}",
                "NF_E2E_MOCK": "http://127.0.0.1:{mock}",
                "NF_E2E_COLD": "http://127.0.0.1:{cold}",
                "NF_E2E_REAL": "http://127.0.0.1:{real}"},
    },
    "writing-rules": {
        "script": "e2e_writing_rules.py",
        "desc": "作品写作约束面板（迁移后：真规则/字数/保存请求/无 console 报错）",
        "stack": True,
        "browser": True,
        "env": {"NF_E2E_SITE": "http://127.0.0.1:{site}",
                "NF_E2E_MOCK": "http://127.0.0.1:{mock}"},
    },
    "metrics": {
        "script": "e2e_chapter_metrics_ui.py",
        "desc": "这一章体检面板（节级字数表 / 文风数 / 命中项变批注）",
        "stack": False,
        "browser": True,
        "env": {"NF_E2E_METRICS_SITE_PORT": "{site}",
                "NF_E2E_METRICS_API_PORT": "{metrics_api}"},
    },
    "discuss-ui": {
        "script": "e2e_discuss_ai_turn_ui.py",
        "desc": "讨论面板让 AI 说一轮（流式上屏 / 闭嘴按钮 / 真能停）",
        "stack": False,
        "browser": True,
        "env": {"NF_E2E_SITE_PORT": "{site}",
                "NF_E2E_DISCUSS_API_PORT": "{discuss_api}"},
    },
    "discuss-api": {
        "script": "e2e_discuss_ai_turn_api.py",
        "desc": "讨论端点真 HTTP（开题 / AI 轮 / 停止 / 残片落盘）",
        "stack": False,
        "browser": False,
        "env": {"NF_E2E_DISCUSS_API_PORT": "{discuss_api}"},
    },
    "stop": {
        "script": "e2e_stop_keeps_draft.py",
        "desc": "停止不亏稿（残稿落盘 / 已完成章不变 / CWD 隔离）",
        "stack": False,
        "browser": False,
        "env": {"NF_E2E_STOP_PORT": "{stop_api}"},
    },
    "para": {
        "script": "e2e_paragraph_refine.py",
        "desc": "段落级精修（采用真改稿 / 冲突不覆盖 / 放弃不动）",
        "stack": False,
        "browser": False,
        "env": {"NF_E2E_PARA_PORT": "{para_api}"},
    },
    "rewrite": {
        "script": "e2e_rewrite_lands.py",
        "desc": "整章重写（产物真落盘 / 只动目标章 / 失败自动放回旧稿）",
        "stack": False,
        "browser": False,
        "env": {"NF_E2E_REWRITE_PORT": "{rewrite_api}"},
    },
    "short": {
        "script": "e2e_short_chapter.py",
        "desc": "偏短章处置（严重偏短转加强模式 / 中度偏短转扩写，都不空转）",
        "stack": False,
        "browser": False,
        "env": {"NF_E2E_SHORT_PORT_A": "{short_api_a}",
                "NF_E2E_SHORT_PORT_B": "{short_api_b}"},
    },
    # 2026-11-20 收进来：这条链是 docs/evidence/full-chain-20261119.md 的证据来源
    # （阶段 1→8 + 真出 Word，40 章，零 token）。它自带工作区与服务，不依赖上面的
    # 四个服务；不进这个清单的话，"可复跑的证据"没人会记得跑，账又会烂回 G1 那种状态。
    "full": {
        "script": "e2e_full_chain.py",
        "desc": "全链零 token（阶段 1→8 + 出 Word / 40 章 / 两道审批门）",
        "stack": False,
        "browser": False,
        "env": {},
    },
}

# 默认端口：与各脚本历史默认值一致，便于对照旧文档
DEFAULT_PORTS = {"site": 8091, "mock": 8798, "cold": 8797, "real": 8799,
                 "metrics_api": 18767, "discuss_api": 18766, "stop_api": 18791,
                 "para_api": 18792, "rewrite_api": 18793,
                 "short_api_a": 18795, "short_api_b": 18796}


# ---------------------------------------------------------------------------
# 端口与探活
# ---------------------------------------------------------------------------
def _connectable(port: int) -> bool:
    """有人在听（含别人设了 SO_REUSEADDR 的监听）。"""
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def port_free(port: int) -> bool:
    """端口真空闲才算空闲。

    ⚠️ 只靠 bind 试探**不够**（2026-11-17 反证实测）：Windows 上对端 socket 设了
    `SO_REUSEADDR` 时，本进程再 bind 同一端口照样成功 —— 于是"空闲"判定通过，
    起服务时一撞就死。所以先 connect（有人在听 = 不空闲），再 bind（不设 REUSEADDR）
    兜住"已 bind 未 listen / TIME_WAIT"的残留。
    """
    if _connectable(port):
        return False
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(start: int, span: int = 40) -> int:
    """从 start 往后找空闲端口。找不到直接抛 —— 静默退回被占端口等于埋假红。"""
    for p in range(start, start + span):
        if port_free(p):
            return p
    raise RuntimeError("端口 %d~%d 全被占用，无法起 e2e 服务" % (start, start + span - 1))


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:                                       # noqa: BLE001
        return False


def wait_http(url: str, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if http_ok(url):
            return True
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------------------
# 服务栈（静态 + mock + 冷启动 mock + 真后端）
# ---------------------------------------------------------------------------
class Service:
    def __init__(self, name: str, cmd, health_url: str, log_path: Path):
        self.name = name
        self.cmd = cmd
        self.health_url = health_url
        self.log_path = log_path
        self.proc = None
        self.fh = None

    def start(self) -> bool:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.log_path, "wb")
        self.proc = subprocess.Popen(self.cmd, cwd=str(ROOT), stdout=self.fh,
                                     stderr=subprocess.STDOUT)
        return True

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=8)
            except Exception:                               # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:                           # noqa: BLE001
                    pass
        try:
            if self.fh is not None:
                self.fh.close()
        except OSError:
            pass


class Stack:
    """四个服务。端口在构造时已定为空闲端口。"""

    def __init__(self, py: str, ports: dict):
        self.py = py
        self.ports = ports
        self.services: list[Service] = []
        self.error = ""

    def _log(self, name):
        return ROOT / "Temp" / "e2e_run_all" / (name + ".log")

    def start_all(self) -> str:
        """起全部并等健康。返回空串=就绪；否则返回人话原因。"""
        p = self.ports
        specs = [
            ("static", [self.py, "-m", "http.server", str(p["site"]),
                        "--directory", str(DIST)],
             "http://127.0.0.1:%d/index.html" % p["site"]),
            ("mock", [self.py, str(E2E / "mock_nf_api_state.py"), "--port", str(p["mock"])],
             "http://127.0.0.1:%d/state" % p["mock"]),
            ("cold", [self.py, str(E2E / "mock_nf_api_state.py"), "--port", str(p["cold"]),
                      "--cold"],
             "http://127.0.0.1:%d/state" % p["cold"]),
            ("real", [self.py, str(ROOT / "scripts" / "nf_api.py"), "--port", str(p["real"]),
                      "--allow-fake", "--root", str(E2E / "fixture_project")],
             "http://127.0.0.1:%d/health" % p["real"]),
        ]
        for name, cmd, health in specs:
            svc = Service(name, cmd, health, self._log(name))
            svc.start()
            self.services.append(svc)
            # 端口刚才是空的，但抢跑（别的进程同时占）会让 python 立刻退出。
            # 立刻退出就当场说清，别等到下面 30 秒超时。
            time.sleep(0.25)
            if not svc.alive():
                self.error = "%s 服务起不来（端口 %d 可能刚被占）—— 日志：%s" % (
                    name, self.ports.get({"static": "site"}.get(name, name), 0), svc.log_path)
                return self.error
        down = [s.name for s in self.services if not wait_http(s.health_url)]
        if down:
            self.error = "服务未就绪：%s（日志在 Temp/e2e_run_all/）" % "、".join(down)
            return self.error
        return ""

    def stop_all(self):
        for s in self.services:
            s.stop()


# ---------------------------------------------------------------------------
# 单套件执行
# ---------------------------------------------------------------------------
def check_prereq(suite: dict) -> str:
    """返回空串=可跑；否则返回 SKIP 原因。"""
    script = E2E / suite["script"]
    if not script.exists():
        return "脚本不存在：%s" % suite["script"]
    if suite.get("browser"):
        import importlib.util
        if importlib.util.find_spec("playwright") is None:
            return "未装 playwright（pip install playwright && playwright install chromium）"
        if not (DIST / "index.html").exists():
            return "console/renderer/dist/index.html 不存在（先 npm run build）"
    return ""


def run_suite(key: str, suite: dict, py: str, stack: Stack | None, ports: dict,
              timeout: int) -> dict:
    skip = check_prereq(suite)
    if skip:
        return {"key": key, "status": "SKIP", "tail": skip, "secs": 0.0}

    env = dict(os.environ)
    for name, tpl in (suite.get("env") or {}).items():
        try:
            env[name] = tpl.format(**ports)
        except KeyError:
            pass
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"   # 不缓冲，进度行才能实时出来

    # 流式打印：ux 那套要跑好几分钟，憋到最后一次性输出会让人以为卡死
    # （2026-11-17 第一版就是这样，跑了 5 分钟屏幕上什么都没有）。
    t0 = time.time()
    proc = subprocess.Popen([py, str(E2E / suite["script"])], cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    lines: list[str] = []
    timed_out = False
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="")
        lines.append(line.rstrip("\n"))
        if time.time() - t0 > timeout:
            timed_out = True
            proc.kill()
            break
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    secs = time.time() - t0

    if timed_out:
        return {"key": key, "status": "FAIL", "tail": "超时（%ds）" % timeout, "secs": secs}

    summary = [ln for ln in lines if ln.startswith("合计:") or ln.startswith("失败项:")
               or ln.startswith("通过 ")]
    tail = "；".join(s.strip() for s in summary) or (lines[-1].strip() if lines else "")
    status = "PASS" if proc.returncode == 0 else "FAIL"
    return {"key": key, "status": status, "tail": tail[:400], "secs": secs}


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    # 许可协议总闸（2026-11-18）：e2e 跑在 tests/e2e/fixture_project 上，那个根从没
    # 被人签过字；不豁免就会满屏 403（而且看起来像“界面全坏了”）。
    # 放在这里而不是各套件里：服务与套件子进程都继承本进程环境，一处生效。
    # 真拦的判据不在 e2e，见 tests/http/test_license_http.py。
    os.environ["NF_LICENSE_ACK"] = "1"
    ap = argparse.ArgumentParser(description="e2e 一键验收（起服务→跑→收尾）")
    ap.add_argument("--only", default="", help="逗号分隔的套件名（默认全部）")
    ap.add_argument("--list", action="store_true", help="列出套件")
    ap.add_argument("--timeout", type=int, default=1800, help="单套件超时秒数（默认 1800）")
    ap.add_argument("--keep-services", action="store_true", help="跑完不杀服务（自己收尾）")
    a = ap.parse_args(argv)

    if a.list:
        print("可用套件：")
        for k, s in SUITES.items():
            print("  %-12s %s%s" % (k, s["desc"],
                                    "  [需四个服务]" if s["stack"] else ""))
        return 0

    keys = [k.strip() for k in a.only.split(",") if k.strip()] or list(SUITES)
    unknown = [k for k in keys if k not in SUITES]
    if unknown:
        print("[run_all] 不认识的套件：%s（--list 看清单）" % "、".join(unknown))
        return 2

    py = sys.executable
    print("=" * 70)
    print("e2e 一键验收 · 解释器 %s" % py)
    print("=" * 70)

    ports = {k: pick_port(v) for k, v in DEFAULT_PORTS.items()}
    need_stack = any(SUITES[k]["stack"] for k in keys)
    stack = None
    if need_stack:
        stack = Stack(py, ports)
        print("[run_all] 起服务：静态 %(site)d / mock %(mock)d / 冷启动 %(cold)d / "
              "真后端 %(real)d（夹具根 tests/e2e/fixture_project）" % ports)
        err = stack.start_all()
        if err:
            print("[run_all] 服务起不来 —— %s" % err)
            stack.stop_all()
            return 1
        print("[run_all] 四个服务就绪。")
    else:
        # 只跑自带服务的套件时，也把端口挑好注入（它们内部会自己起）
        print("[run_all] 本组套件自带服务，端口：%s" % ", ".join(
            "%s=%d" % (k, v) for k, v in ports.items() if k in
            ("site", "metrics_api", "discuss_api", "stop_api", "para_api", "rewrite_api",
             "short_api_a", "short_api_b")))

    results = []
    try:
        for k in keys:
            print("\n" + "-" * 70)
            print("[run_all] 跑 %s —— %s" % (k, SUITES[k]["desc"]))
            print("-" * 70)
            r = run_suite(k, SUITES[k], py, stack, ports, a.timeout)
            results.append(r)
            print("[run_all] %s → %s（%.1fs）" % (k, r["status"], r["secs"]))
    finally:
        if stack is not None and not a.keep_services:
            stack.stop_all()
            print("\n[run_all] 已收尾：只杀了自己起的 4 个服务进程。")

    print("\n" + "=" * 70)
    print("汇总")
    for r in results:
        print("  %-12s %-5s %6.1fs  %s" % (r["key"], r["status"], r["secs"], r["tail"]))
    bad = [r for r in results if r["status"] == "FAIL"]
    skipped = [r for r in results if r["status"] == "SKIP"]
    print("  —— 通过 %d / 失败 %d / 未执行 %d" % (
        len(results) - len(bad) - len(skipped), len(bad), len(skipped)))
    print("=" * 70)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
