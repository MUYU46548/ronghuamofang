# -*- coding: utf-8 -*-
"""施工权裁决（单一真源）—— AI 与人的写入口拦截。

⚠️ 别与 `utils/write_guard.py` 混：那个查的是"agent 把文件写到仓库外边"，
本模块查的是"**谁有资格写**"。两件事，两个模块。

## 要治的病（2026-11-02，不可用清单第 10 条）

用户原话的意思是：**两个 agent 同时用必炸。** 事实：`RunLock` 全项目只在
`orchestrator.py:311` 一处调用，只拦"起第二条流水线"；而段落重写、保存正文、
改设定、审批、打回、plan 落盘**全部裸写**。`CURRENT["id"]` 是进程内变量，
连"流水线跑着时有人改章"都拦不住。

## 语义（作者拍板 P5 · L-1~L-5，这里是唯一实现）

| 锁在谁手里 | 人的写操作 | 外部 Agent 的写操作 |
|---|---|---|
| 没人（无锁 / 持有进程已死） | 放行 | 放行 |
| AI（`owner=pipeline`） | **409 + 可接管** | **409** |
| 人（`owner=human`） | 放行 | **409** |

- L-1 不是"拦第二条流水线"，是**拦所有写入口**；
- L-2 人可随时强夺：界面拿到 409 里的 `can_takeover` → `POST /lock/takeover`；
- L-4 人显式释放（`POST /lock/release`）之后 AI 才能继续；
- L-5 界面必须自己说出"锁在谁手里"（`GET /lock`）。

## 为什么"人的写操作"也拦

看着反直觉，但这是 L-1 的字面要求，也是唯一不会误伤的解法：如果人一写就
静默夺锁，那么"点了一下保存"就等于掐掉正在跑的四十分钟。改成**先说清楚再动手**：
界面弹一句"流水线在跑（第 N 章），接管会让它停下来"，人点确认才转移。
`/stop`、`/stream/*`、`/lock/*` 永不拦 —— 那是人来叫停和接管的通道本身。

## 为什么判定不用额外状态机

锁文件是**事实**（谁在跑、谁说了算），只读它一次；写 409 时不做任何副作用
——「拒绝」必须可重试。
"""
from utils.run_lock import read_holder

#: 永不拦截的路径（前缀匹配）。这些是"人来叫停/接管"的通道本身，
#: 拦了就等于把人锁在门外 —— 比不装锁更糟。
EXEMPT_PREFIXES = (
    "/stop",            # 中断
    "/stream/pause",    # 暂停显示
    "/stream/resume",   # 恢复显示
    "/lock/takeover",   # 接管
    "/lock/release",    # 释放
    # 运行中插一句（2026-11-02，清单第 4 条）：**它存在的意义就是 AI 持锁时人也能
    # 改方向**。若被施工权拦掉，这条功能等于没做（真机实测过一次：AI 持锁 → 409）。
    # 它写的是一个独立 json（下一章才读），不和流水线正在写的章抢同一个文件；
    # 写权限另有 agent_guard 把着（外部 Agent 不得插话）。
    "/intervene",
)

OWNER_TEXT = {"pipeline": "AI 流水线", "human": "你"}


def _exempt(path):
    p = (path or "").split("?")[0]
    return any(p == e or p.startswith(e + "/") or p.startswith(e)
               for e in EXEMPT_PREFIXES)


def check_write(lock_path, path, trusted_gui):
    """写操作能不能放行。返回 None = 放行；返回 dict = 409 负载。

    ``trusted_gui`` 必须由调用方用**双因子**判（来源头 + 主进程签发 token），
    不能自己看请求头 —— 那等于让任何本机进程自称"人"。
    """
    if _exempt(path):
        return None
    holder = read_holder(lock_path)
    if not holder:
        return None
    if not holder.get("alive"):
        # 持有进程已死 = 陈旧锁。放行（下一次 acquire / 人点释放会真的清掉它），
        # 这里再把写操作拦下来就成了"一次崩溃锁死全书"。
        return None
    owner = holder.get("owner") or "pipeline"
    if owner == "human" and trusted_gui:
        return None
    who = OWNER_TEXT.get(owner, owner)
    msg = ("%s正持有这本书的施工权（pid=%s，开始于 %s）。"
           % (who, holder.get("pid"), holder.get("started_at") or "?"))
    if owner == "pipeline":
        msg += "流水线在跑，此刻改稿会与它互相覆盖。"
        if trusted_gui:
            msg += "要改就先接管：接管会让流水线在下一章前停下来。"
        else:
            msg += "外部 Agent 不得抢跑；等人释放后再试。"
    else:
        msg += "只有人能写；外部 Agent 请等人释放。"
    return {
        "ok": False,
        "code": "LOCKED",
        "error": msg,
        "holder": {"owner": owner, "pid": holder.get("pid"),
                   "started_at": holder.get("started_at"),
                   "cmd": (holder.get("cmd") or "")[:120]},
        "can_takeover": bool(trusted_gui),
        "takeover_hint": ("POST /lock/takeover 会把施工权交给你，"
                          "流水线在下一章前停手；已写的章不丢。") if trusted_gui else "",
    }


def check_pipeline_should_stop(lock_path):
    """流水线侧的检查点（每章开始前调用）。返回停下原因，或 None 继续。

    这是 L-2 的另一半：**只把锁判给谁还不够，得让流水线真的停手**。
    不然"接管"只是改了一个文件，模型还在写。
    """
    holder = read_holder(lock_path)
    if holder and holder.get("alive") and (holder.get("owner") or "") == "human":
        return ("作者已接管施工权（%s）—— 流水线在此停手，"
                "已完成的章保留；要继续请在界面释放施工权后重跑。"
                % (holder.get("started_at") or "刚才"))
    return None


def lock_status(lock_path):
    """给界面用：锁在谁手里（L-5）。没有锁 → ``{"held": False}``。"""
    holder = read_holder(lock_path)
    if not holder:
        return {"held": False, "owner": None, "text": "没人持有（可以随便改）"}
    owner = holder.get("owner") or "pipeline"
    alive = bool(holder.get("alive"))
    who = OWNER_TEXT.get(owner, owner)
    if not alive:
        text = "锁文件残留（进程已退出）—— 可以接管或直接释放"
    elif owner == "human":
        text = "在你手里（AI 已停手）"
    else:
        text = "%s在跑（pid=%s）" % (who, holder.get("pid"))
    return {"held": True, "owner": owner, "alive": alive,
            "pid": holder.get("pid"), "started_at": holder.get("started_at"),
            "cmd": (holder.get("cmd") or "")[:200], "text": text}
