# -*- coding: utf-8 -*-
"""nf_api 域模块包（P2：God Object 拆分的落点）。

`scripts/nf_api.py` 曾是 2,872 行的单体：HTTP 分发、业务动作、文件读写、
外部进程调用全部混在一个文件里。本包按**端点域**承接其中的实现部分。

## 分层

    nf_api.py                 ← 只留：常量、工具、Handler（薄转发）、main()
      └── nf_api_domains/     ← 各域的实现函数（纯函数，不碰 socket）
            contract.py       ← 统一返回协议（handler 只能 return，不能 _send）
            misc.py           ← 运行环境 / 日志 / 关于 / 章节校验等杂项
            ...

## 与 nf_api 的依赖方向

域模块**反向依赖** nf_api 的模块级名字（`ROOT` / `ALLOW_FAKE` / `load_all`
/ `start_job` / `act_*` 等），做法是 `import nf_api` 后经 `nf_api.xxx` 访问。

为什么不让 nf_api 把名字 inject 进来？
  · 项目已有 `scripts/` 在 `sys.path` 上（nf_api 自己就 `sys.path.insert`），
    子包 `import nf_api` 天然可用；
  · 更关键 —— 测试会用临时项目根重设 `nf_api.ROOT`（`--root`），
    若域模块在 import 期就把 ROOT 拷成局部常量，测试与安装态都会指向错目录。
    `nf_api.ROOT` 这种**属性访问**保证拿到的一定是当下值。

## 纪律

1. **绝不**在域模块里 `from nf_api import ROOT`（会拷成死值）；
2. **绝不**在域模块里写 `import json` 之类会遮蔽的名字 —— 域模块扁平、
   一个函数一屏，且没有历史包袱，新代码不允许再引入这类陷阱；
3. 每个 handler 的返回值必须是 `(status, payload)`，**不碰** `h._send`；
4. 新增端点时，分发（elif）留在 `nf_api.py`，实现放本包。
"""
