# -*- coding: utf-8 -*-
"""运行控制域（2026-11-17 拆壳）：人叫停 / 等本章完 / 接管 / 释放 / 插一句 / 流式暂停。

为什么单独成域：这七个端点全是**人来干预正在跑的流水线**的通道
（P5 施工权、F04、清单第 4 条），与"写数据"走的是两套语义 ——
`lock_guard` 对 `/stop`、`/stream/*` 永不拦：那是人叫停的通道本身，拦了就把人锁在门外。
放在一起，这条纪律才看得见；散在 1600 行 elif 里，下一个改的人必然漏。

协议同其它域：`handle_*(h, body)` 返回 `(status, payload)`，响应统一由 nf_api 发出。
全局状态（`STREAMERS` / `STOP_EVENTS`）一律 `api.X` **属性访问** ——
`from nf_api import STREAMERS` 是值拷贝，测试/安装态会拿到另一份 dict，
届时"停止信号"发进虚空（纪律见 tests/unit/test_api_domains.py）。
"""
import threading

import nf_api as api
from utils import agent_guard, lock_guard


def _lock_path():
    """运行锁路径 —— **调用时取值**（`--root` 可能在 import 之后才生效）。"""
    return api._run_lock_path()


def handle_intervene(h, body):
    """运行中插一句（2026-11-02，清单第 4 条）。

    只对**下一章**生效，不改大纲、不重写已写的章，用完即删。
    没在跑时拒收 —— "先存着以后用"的指令到真跑时早过时了，
    那时候该改的是大纲，不是排队等一句话。
    """
    text = str(body.get("instruction") or "").strip()
    if not text:
        return 400, {"ok": False, "error": "instruction 不能为空"}
    _st = lock_guard.lock_status(str(_lock_path()))
    if not (_st.get("held") and _st.get("alive")
            and _st.get("owner") == "pipeline"):
        return 400, {"ok": False, "error":
                     "现在没有流水线在跑。这种指令只对\"下一章\"有意义，"
                     "存着等下次跑会过时 —— 直接改章大纲或提示词更快。"}
    from utils import intervene as _iv_mod
    _data = _iv_mod.write(text, target_chapter=None, root=api.ROOT)
    return 200, {"ok": True, **_data,
                 "message": "已登记：下一章写作时生效，用完即删"}


def handle_finish_chapter(h, body):
    """“写完这章就停”（2026-11-02，F04）：与 /stop 是两个动作。

    /stop 立刻掐断（半截存残稿）；这个等本章入稿、标完成后再停。
    """
    from utils import pause_flag
    on = bool(body.get("on", True))
    if on:
        _d = pause_flag.request(root=api.ROOT)
        return 200, {"ok": True, "on": True, **_d,
                     "message": "已登记：这一章写完就停（那一章是完整的）"}
    pause_flag.clear(root=api.ROOT)
    return 200, {"ok": True, "on": False, "message": "已取消"}


def handle_lock_takeover(h, body):
    """人的绝对优先权（P5 · L-2）：接管只由界面发起（双因子）。

    不动别人的进程 —— 流水线在下一章前的检查点自己看到 owner=human 再停
    （见 lock_guard.check_pipeline_should_stop）。
    """
    if not agent_guard.is_trusted_gui(getattr(h, "headers", None) or {}):
        return 403, {"ok": False,
                     "error": "接管施工权只能由界面发起；"
                              "外部 Agent 请等人释放后再写"}
    from utils.run_lock import RunLock
    ok, msg = RunLock(str(_lock_path())).takeover("human", by="界面")
    st = lock_guard.lock_status(str(_lock_path()))
    return (200 if ok else 409), {"ok": ok, "message": msg, **st}


def handle_lock_release(h, body):
    """L-4：人显式释放之后 AI 才能继续。force：锁在谁手里都清掉。"""
    if not agent_guard.is_trusted_gui(getattr(h, "headers", None) or {}):
        return 403, {"ok": False, "error": "释放施工权只能由界面发起"}
    from utils.run_lock import RunLock
    RunLock(str(_lock_path())).release(force=True)
    st = lock_guard.lock_status(str(_lock_path()))
    return 200, {"ok": True, "message": "已释放施工权", **st}


def handle_stop(h, body):
    """中断一个 job：流式置 streamer.stop；非流式置 STOP_EVENTS。"""
    job_id = str(body.get("job_id") or "")
    if not job_id:
        return 400, {"error": "job_id 必填"}
    # 先尝试流式停止
    with api.STREAMERS_LOCK:
        streamer = api.STREAMERS.get(job_id)
    if streamer:
        streamer["stop"].set()
        return 200, {"ok": True, "message": "已发送停止信号: " + job_id}
    # 非流式：设置全局停止标志
    with api.STOP_LOCK:
        api.STOP_EVENTS[job_id] = threading.Event()
        api.STOP_EVENTS[job_id].set()
    return 200, {"ok": True,
                 "message": "已设置停止标志: " + job_id + "（当前阶段完成后停止）"}


def handle_stream_pause(h, body):
    """暂停**前端显示**（不是暂停计费）。"""
    job_id = str(body.get("job_id") or "")
    if not job_id:
        return 400, {"error": "job_id 必填"}
    with api.STREAMERS_LOCK:
        streamer = api.STREAMERS.get(job_id)
    if not streamer:
        return 404, {"error": "stream not found: " + job_id}
    if "pause" not in streamer:
        streamer["pause"] = threading.Event()
    streamer["pause"].set()
    # 2026-11-02：措辞改准。这个 Event 只在包装器的 _push 里被检查，
    # 模型调用不会停 —— 旧文案“已暂停流式输出”会让人以为不再计费。
    return 200, {"ok": True, "model_running": True,
                 "message": "已暂停前端显示（模型仍在生成并计费）: " + job_id}


def handle_stream_resume(h, body):
    job_id = str(body.get("job_id") or "")
    if not job_id:
        return 400, {"error": "job_id 必填"}
    with api.STREAMERS_LOCK:
        streamer = api.STREAMERS.get(job_id)
    if streamer and "pause" in streamer:
        streamer["pause"].clear()
        return 200, {"ok": True, "message": "已恢复流式输出: " + job_id}
    return 404, {"error": "stream not found: " + job_id}
