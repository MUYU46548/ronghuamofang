# -*- coding: utf-8 -*-
"""nf_api 域模块的**统一返回协议**（P2 拆分的接缝定义）。

## 为什么需要一层协议

`nf_api.py` 曾是一个 2,872 行的 God Object：`do_GET` 662 行、`do_POST` 796 行，
两条巨型 elif 链各含 40+ 个分支。拆分时最容易出事的就是「搬运过程中改了语义」——
响应码写错、`return` 漏掉导致二次发送、`self` 传丢。

所以拆分的接缝**必须是一份可被测试的契约**，而不是「把代码剪出来粘贴过去」：

    # 旧：分支体直接调 self._send / self._query / self._body
    elif p == "/logs/tail":
        lines = int((self._query().get("lines") or ["200"])[0])
        ...
        self._send(200, {...})

    # 新：分支只剩一行薄转发，实现搬到域模块的纯函数
    elif p == "/logs/tail":
        self._send(*h_logs_tail(self))

## 契约

每个 handler 函数签名统一为：

    def handle_xxx(h) -> (status: int, payload: Any)

- `h` 是 `BaseHTTPRequestHandler` 实例（即 `Handler`）。域模块**只**通过
  `h._query()` / `h._body()` / `h.command` / `h.headers` 读请求，
  通过**返回值**表达响应 —— 彻底不碰 `h._send`，于是「谁发响应」永远只有一个答案。
- 唯一例外：SSE 流式端点 `/stream/{job_id}` 需要持续写 socket 并自行管理连接生命周期，
  无法用「返回一个 payload」表达。它用 `STREAM_RESPONSES` 标记显式豁免。

## 为什么不索性把 do_GET/do_POST 也搬走

`tests/e2e/test_gui_api_contract.py` 以 `nf_api.Handler.do_GET/do_POST` 为路由表来源
（AST 解析），这是「前端在调、后端有没有」这类事故的唯一护栏。把分发器留在
`nf_api.py`，域模块只承载实现 —— 契约测试的锚点不动，实现怎么搬都安全。

## 拆分后的不变式（由契约测试守护）

1. `do_GET` / `do_POST` 仍是 `nf_api.Handler` 的方法；
2. 每个分支仍是**一条 if/elif 链**（链被裸 `if` 打断 → 后续端点全部 404）；
3. 分支体只做「取参 → 调域函数 → `self._send`」，不再内联业务逻辑。
"""
from typing import Any, Callable, NamedTuple, Tuple

# 返回类型别名：handler 的返回值
Result = Tuple[int, Any]

# 需要自行接管响应（SSE）的哨兵：handler 返回 (200, STREAM_RESPONSES) 时，
# nf_api 不再调用 _send，由 handler 自己完成写响应。
STREAM_RESPONSES = object()


class Route(NamedTuple):
    """一条路由：HTTP 方法 + 路径判定 + 实现函数。

    仅用于**文档与自检**（域模块声明自己负责哪些端点），
    真正的分发仍在 nf_api.Handler.do_GET/do_POST 的 elif 链里 ——
    因为契约测试需要在那里读到路由表。
    """
    method: str
    path: str
    handler: Callable[..., Result]
    kind: str = "exact"     # exact | prefix | template
