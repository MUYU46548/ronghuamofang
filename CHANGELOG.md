# 更新日志（CHANGELOG）

本项目此前无版本记录（2026-09-19 审查发现「已有 v0.2.0、NSIS 发布等版本行为却无
CHANGELOG」）。本文件从工程审查修复起正式启用。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [Unreleased] — 2026-09-19 工程审查修复

依据 `deliverables/engineering-assurance/comprehensive-audit-novelforge-2026-09-19.md`
（8 项严重 / 12 项高危 / 14 项中 / 8 项低）执行止血与保障体系建设。

### 🔴 修复：默认配置下必崩的活缺陷

- **S1** `orchestrator.py` 重试路径 `datetime` 未导入 → 必然 `NameError`
  - `auto_retry` 默认 `true`，任一阶段失败即触发；且崩溃点无异常保护，
    `db.finish_run()` 永不执行 → `runs` 表残留 `status='running'` 脏行。
  - 修复：补 `from datetime import datetime`；重试循环整体包 `try/except`；
    编排层引入 `_finalize()` 统一收尾 + `finally` 幂等补偿。
- **S2** `nf_api.py` 流式分支引用未定义的 `only_stage` → GUI 跑流水线必崩
  - 先回 `202 {job_id, stream:true}` 再在后台线程抛错 → GUI 显示「正在流式输出」
    却永远等不到 token（典型静默失败）。
  - 修复：**消除流式/非流式平行分支** —— 抽出 `_rc_to_result()` 共用退出码翻译 +
    `act_run_stage()` / `act_run_stage_streamed()` 两个工厂。
- **S2+（本轮新发现，审查报告未覆盖）** `nf_api.py` 的 `/kb/search` 与 `/kb/build`
  引用未导入的 `get_vault_path` → 端点一旦被调用必 `NameError`
  - 长期存活原因：全部 HTTP 契约测试**无任何 kb 用例**（零覆盖）。
  - 修复：补齐导入 + vault 未配置时给可行动 400；新增 kb 端点 HTTP 契约用例。

### 🔴 修复：安全与配置

- **S3** `config/system.yaml` / `config/obsidian_templates.yaml` 硬编码用户本机
  绝对路径（含已废弃命名 `ROSA` 的漏网残留），并散落在 `obsidian_bridge` /
  `kb_index` / `obsidian_postprocess` / `voice_to_docx` 的默认值中。
  - 修复：改为占位符 + 项目内默认沙盒（`data/state/obsidian_sandbox`）；
    `get_vault_path()` 未配置返回 `None`（**不再返回 `Path("")`** ——
    后者的 `str()` 是 `"."`，会让「是否已配置」的判断永远为真）。
  - 顺带移除 14 个测试脚本中硬编码的本机仓库路径，统一改为 `__file__` 解析。
- **S7** `console/main/index.js` 把 `.env` 放进「外部打开」白名单 → 明文密钥可被
  外部程序直接打开。
  - 修复：移出白名单；新增 `reveal-in-folder` IPC（只定位不打开），
    GUI「打开 .env」改为在文件管理器中显示。
- **S8** 模型准入「**约束数据存在、回路从未接通**」：白名单数据（`available_models` /
  `fetched_models.json` 的 `_manual`）确实存在且被多处读写，但**全部消费点都是
  展示或缓存维护**，不存在任何判定回路；`/models/switch` 连格式校验都没有。
  - 修复：新增 `scripts/utils/model_registry.py` 作为纪律的唯一实现点，
    两级校验（① 格式合法性 ② 白名单归属）+ `strict=false` 逃生门（回报未校验）；
    `/models/switch`、`/models/add` 均接入。

### 🔴 修复：工程保障体系（比单缺陷更根本）

- **B1** `.github/` 是完全空目录 —— **本项目从未有过 CI**。
  且 `.gitignore` 的 `*.yml` 会静默吞掉未来任何 workflow 文件
  （`git check-ignore -v` exit=0，推不上仓库且不报错）。
  - 修复：`.gitignore` 加 `!.github/workflows/*.yml` 白名单；
    新增 `.github/workflows/ci.yml`（pyflakes 门禁 + 语法检查 + 单元测试）。
- **B2** `Temp/` 被 gitignore，而它是**事实上的测试目录**（67 py + 129 截图）
  → fresh clone 后测试全丢，`AGENTS.md` 引用的 20+ 测试路径全部失效。
  - 修复：新建 `tests/{unit,http,e2e}/` 并迁入 31 个测试脚本，纳入 Git；
    `AGENTS.md` 引用同步校正。
- **B3** 零 Linter；以 `py_compile` 当质量门禁（纯语法检查，对「名字写错」完全无效）。
  - 修复：新增 `scripts/quality_gate.py`（pyflakes 封装，**未定义名零容忍**）。
    本轮 4 个活缺陷全部由它命中。

### 🟠 其他改进

- `db.py` 新增 `finish_run_if_running()`：编排级幂等补偿，把崩溃残留的
  `status='running'` 收敛为 `crashed`；读侧「绕过 running 过滤」的容错**保留**
  作为深度防御第二层。
- `/models/switch` 校验失败时回报 `suggestions` 与 `registered_count`，
  便于用户纠错。

### ✅ 测试

- 新增 4 个回归用例（共 **87 断言**）：
  - `tests/unit/test_orchestrator_retry.py`（13）— S1 回归
  - `tests/unit/test_stream_stage.py`（18）— S2 回归
  - `tests/unit/test_config_and_models.py`（29）— S3/S8 回归
  - `tests/http/test_kb_and_models_api_http.py`（27）— kb + 模型端点契约
- 每个回归用例均经**反向验证**（临时还原缺陷 → 用例必须失败 → 恢复），
  确认不是「永远为绿」的空用例。
- 既有测试套件全绿：65 条新端点 HTTP 断言、27 条 GUI↔API 契约断言、
  44 条碎片聚类断言等。

---

## 第二轮：失败路径覆盖 + 两个新活缺陷（2026-09-19 晚）

第一轮把「主路径」修干净了，但报告 H5 指出的核心问题仍未解决：
**全仓 22 个 `result["exit_code"] != 0` 分支一次都没被测试走到过**
（`FakeClient` 恒 `exit_code=0`）。这一轮是正面回应 ——
**主动把系统打坏，看它是否按承诺的方式坏。**

### 🔴 新增缺陷 S9：默认全流程最后一站必崩

**发现方式**：写 F3（预算熔断）用例时，用例本身报 `KeyError: '8'`。
这个异常与预算毫无关系 —— 顺着查下去才发现是真缺陷。

| 项 | 内容 |
|---|---|
| 现象 | 默认全流程（`from_stage=1`、无 `--stage`）跑到阶段 7 完成后，进入阶段 8 时抛 `KeyError: '8'` |
| 精确位置 | `orchestrator.py:213` 的断点判断 `progress.stage_status(8)` |
| 根因 | `progress_manager.STAGE_KEYS` 只声明 `("1".."7")`，而 orchestrator 遍历 `range(from_stage, 9)` 会取到阶段 8；`STAGES` 表里也确实有第 8 项（Markdown 分卷导出） |
| 后果 | 异常穿透 `run()`，由 `finally` 兜底把 `runs` 标为 `crashed`（退出码既不是 0/1/2，也不是 3/4）→ **正常跑完的书被记成崩溃** |
| 为什么一直没暴露 | 全流程因各式原因多在早期阶段暂停，从未有人跑到阶段 8；且测试全用 `only_stage`，绕开了这条路径 |

**修法（三层）**：
1. `STAGE_KEYS` 补齐到 `"8"`（对齐编排遍历范围）；
2. 新增 `_ensure_stage()` 纵深防御 —— 阶段键缺失时**自动登记**而非 KeyError，
   把「配置失同步」从「全流程崩溃」降级为「多一个键」；
   16 处直接下标 `self.data["stages"][str(stage)]` 全部改走此方法；
3. `reject.py` 的下游重置范围 `range(stage+1, 8)` → `range(stage+1, 9)`，
   否则打回后阶段 8 残留 `done`，断点逻辑会误判「已完成」而跳过。

### 🔴 新增缺陷 S10：预算熔断的「最后一站漏洞」

| 项 | 内容 |
|---|---|
| 现象 | 最后一个阶段（阶段 8）成功且已超预算时，退出码为 0、`runs.status='done'`、GUI 显示成功 |
| 精确位置 | `orchestrator.py` 阶段循环末尾：`state, spent = cost.status(run_id)` 只 `print` 一句，**从不据此停机** |
| 根因 | 死代码 —— 计算了 `state` 却没有任何 `if state == "pause"` 分支消费它 |
| 后果 | 熔断只在「下一阶段启动前 / 重试前」生效；**没有下一阶段时，超预算被静默吞掉**，最终宣告"全部完成" |
| 性质 | 典型的**静默失败** —— 用户看到成功，账已超支 |

**修法**：阶段末尾的检查与阶段起始处的熔断保持同一语义（置 `budget.paused`、`_finalize("paused", 2)`）。

### ✅ 失败路径测试基建

- **新增 `scripts/utils/failing_client.py`**：可控失败的假客户端（生产代码零改动）。
  - `FailingClient(fail_on=..., fail_times=..., raise_exc=...)` —— 支持「全失败 / 指定阶段失败 / 前 N 次失败 / 抛异常而非返回非零码」四种策略；
  - `ExpensiveClient` —— 用于「成功但烧钱」的熔断场景（与「失败」正交）；
  - 设计要点：`exit_code != 0` 时**不写任何产物**（真实子会话崩了正是不写），否则测的就不是失败路径。
- **新增 `SEED_VERSION` 升级机制**（`console/main/index.js`）：
  此前 `seedWorkspace()` 在 `.seeded` 存在时直接 `return` ——
  **「只在首启播种」实际变成了「永远不再更新」**。桌面端用户升级 App 后，
  workspace 里跑的还是旧版 `scripts/`、`prompts/`，而 `config/` 是新的
  → 新配置撞旧代码，失败且无法从 UI 诊断。
  现在按版本号刷新代码目录（`force` 覆盖），**不动用户 `data/`**，
  `config/` 只补缺失文件、绝不覆盖用户填的书名/路径。

### ✅ 测试

- **新增 `tests/unit/test_failure_paths.py`（69 断言）**，覆盖 F1~F8 + S9/S10：
  - F1 重试计数真实递增（首次 + 2 轮 = 3 次调用，`retry_count=2`）
  - F2 异常不外泄（`run()` 返回而非抛；`runs` 无 `running` 脏行）
  - F3 / F3b / F3c 三处熔断窗口分别验证（阶段末尾 / 重试循环内 / 末阶段）
  - F4 停止请求在阶段启动前即生效（零额外 LLM 调用）
  - F5 stage4 逐章失败精确隔离（第 1 章失败不阻断第 2 章；重跑后失败清单清空）
  - F6 静态门禁 + **反向验证**（注入未定义名必须被检出，证明确实不是空门禁）
  - F7 升级刷新（代码/提示词刷新、用户 config 不覆盖、用户 data 不丢、幂等不回滚）
  - F8 打回清下游 + 审批 CLI 全链
  - S9 / S10 回归
- **反向验证**：S9、S10 两处修复均经「还原缺陷 → 用例必须失败 → 恢复 → 必须通过」验证：
  - 还原 S9 → `KeyError: '8'`、`STAGE_KEYS 覆盖 7 >= 8` 断言失败
  - 还原 S10 → 退出码 `0`、`runs=[(1,'done')]`、`budget.paused=False`（**正是静默宣告成功**）
- 回归确认：全部既有单元套件 + 8 个 HTTP 套件（合计 263 断言）全绿；
  GUI 构建 `322 modules transformed` 成功。

### ⚠️ 尚未处理

- `snapshot.py` 仍无 `--restore` 入口（恢复路径未演练）。
- `rotate_days` 仍是死配置（无 `RotatingFileHandler`）。
- `nf_api.py` 2,785 行 God Object 未拆分（P2，须保持 65 断言全绿）。
- 全仓仍有 46 条 pyflakes 历史告警（未使用导入 / f-string 无占位符），
  当前策略是「只计数不阻塞」，避免一次性冻结门禁。
- `tests/unit/test_thinking_compat.py` 的 2 条**真实探针**断言当前失败：
  探针假定「不关思考时 content 必为空」，但 TokenHub 上的模型现已在默认
  模式下把正文正常放入 `content`（该行为变化本身是好事）。
  离线部分 28 断言全绿；这两条属于**探针假设过期**，与代码缺陷无关
  （`llm_client.py` 本轮未被改动）。建议改为「记录事实」而非硬断言。

> ↑ 以上 4 项已在**第三轮**全部处理完毕，见下。

---

## 第三轮：清掉「尚未处理」全部 4 项 + 3 个新缺陷（2026-09-19 深夜）

第二轮列了 4 项待办，这一轮**全部做完**，并在过程中揪出 3 个此前无人知道
的真缺陷（其中一个属于「静默失败」中最阴的一类）。

### ✅ 待办 1：`test_thinking_compat.py` 探针硬断言过期 → 改为事实记录

原断言假定「不关思考时 `content` 必为空」，但 TokenHub 上的模型已在默认模式
把正文正常放进 `content`。这不是代码坏了，是**探针对现实的假设过期了**。
改为「记录实测事实」：探针照跑、结果照打，但只在**确认是缺陷**时断言失败。
现 **34/34 全绿**。

### ✅ 待办 2：`snapshot.py --restore` 补齐（恢复路径首次可演练）

此前快照**只写不读** —— 510 个文件的备份躺在 `history/`，却没有任何恢复入口，
「可回退」是纸面承诺。新增 `--restore`，安全设计按危险程度递减：

| 层 | 机制 |
|---|---|
| 1 | **默认 dry-run** —— 不带 `--yes` 只列「将覆盖什么、将保留什么」 |
| 2 | **恢复前自动打 `pre_restore` 快照** —— 恢复错了还能折返 |
| 3 | **删除需显式开启** —— 快照外的文件默认**保留**，`--delete-extra` 才删 |
| 4 | **范围限定** —— 只恢复 `SNAPSHOT_ITEMS`，绝不整棵 `data/` 还原 |
| 5 | **路径护栏** —— `history_dir` 必须含 `history` 段；ID 精确/唯一前缀匹配，禁穿越 |

### ✅ 待办 3：`rotate_days` 死配置 → 接通日志轮转

`config/system.yaml` 里 `logging.rotate_days: 30` 此前**没有任何代码读它**。
后果：Electron 以 append 模式无界追加 `%LOCALAPPDATA%/Temp/nf_api_child.log`，
长跑把日志撑到几百 MB 且永不清理。

新增 `scripts/utils/run_log.py`（`get_log_path` / `rotate_if_needed` /
`append_line` / `tail` / `log_status`），在 `nf_api.py` 启动前调用一次。
设计取舍：**按天轮转不按大小**（配置项语义就是时间；按大小会让「读哪个文件」
不可预测）；**不改 Electron 的 spawn 方式**（那要重启控制台才生效，而 Python 侧
每次起服务都会走到）；**轮转失败绝不阻断启动**（日志坏了不连累主流程）。

### ✅ 待办 4：`nf_api.py` God Object 拆分（P2）

**2,872 → 2,374 行（-17%）**，91 个端点分支（43 GET + 48 POST）**全部**改为
薄转发，实现搬进 `scripts/nf_api_domains/`：

| 模块 | 端点 | 内容 |
|---|---|---|
| `contract.py` | — | 接缝协议：`(status, payload)` 返回约定 + `STREAM_RESPONSES` 哨兵 |
| `misc.py` | 8 | 成本/环境/进度/质量/日志/关于（试点域） |
| `models.py` | 6 | 模型列表与切换（两级校验，S8 的发生地） |
| `materials.py` | 6 | 素材/碎片/设定**读**侧 |
| `outline.py` | 6 | 结构化大纲/版本/diff/草稿/分章 |
| `project.py` | 6 | 健康检查/状态/多书/结构树/配置 |
| `runtime.py` | 9 | job/审稿报告/提示词/校对/预估/节奏/章节历史 |
| `post_misc.py` | 1 | Markdown 导出 |

**拆分纪律（三条，全部由测试守护）**：
1. **谁发响应永远只有一个答案** —— 域模块只 `return (status, payload)`，
   绝不碰 `h._send`；`nf_api.py` 用 `_dom()` 统一展开。
2. **反向依赖必须走属性访问** —— 域模块写 `import nf_api as api` + `api.ROOT`，
   **禁止** `from nf_api import ROOT`（后者把 `ROOT` 拷成死值，
   使 `--root` 与测试的临时项目根失效）。
3. **分发器留在 `nf_api.py`** —— 契约测试以 `Handler.do_GET/do_POST` 为路由表锚点。

配套把 `tests/e2e/test_gui_api_contract.py` 从「正则切源码块」改为 **AST 解析**
（27 → 35 断言），这是拆分的**前置解锁条件**：原实现把 `do_GET`/`do_POST`
钉死在 `nf_api.py` 里，正则一改就误报。

### 🔴 新缺陷 S11：`__main__` 双份 `nf_api` 实例 → `/jobs/{id}` 永远 404

**发现方式**：拆完 `runtime.py` 后跑 HTTP 回归，`test_auto_rewrite_api_http`
从 17/0 掉到 14/3，失败项全是 `{'state': 'timeout'}`。

| 项 | 内容 |
|---|---|
| 现象 | `python scripts/nf_api.py` 启动时，`GET /jobs/{id}` 恒返回 404；前端轮询后台任务全部超时 |
| 根因 | 以脚本方式启动时本文件模块名是 `__main__`；域模块里 `import nf_api as api` 会**再加载一份**，进程内同时存在两个 `nf_api`，`JOBS` / `CURRENT` / `ROOT` 是彼此独立的对象 |
| 为什么静默 | 从**磁盘**读文件的端点（`/state`、`/costs/…`）全正常 —— 它们不依赖进程内状态；只有 `/jobs/{id}` 这类查内存字典的会坏 |
| 为什么单测抓不到 | 直接 `import nf_api` 的单测只有**一份**模块，全绿。这个缺陷**只在真机 HTTP 下暴露** |
| 性质 | 「静默失败」—— 前端显示「正在运行」却永远等不到结果，与 S2 同类 |

**修法**：在 `nf_api.py` 导入区加标准别名守卫：

```python
if __name__ == "__main__":
    sys.modules.setdefault("nf_api", sys.modules["__main__"])
```

并新增**四层判据**（`tests/unit/test_api_domains.py` 第 6 节）：静态检查守卫存在
+ 子进程里按源码守卫实跑 + 断言 `m is api` + 断言 `m.JOBS is api.JOBS`。
反向验证：移除守卫 → 该节 4 条 FAIL **且** `test_auto_rewrite_api_http` 3 条 FAIL。

### 🔴 新缺陷 S12：`snapshot --restore` 的 extras 检测漏掉子目录

写 `--restore` 回归用例时，用例断言「子目录里新增的文件应被识别为 extras」失败。

| 项 | 内容 |
|---|---|
| 现象 | 快照后新增的 `data/chapters/raw/99.md` 不出现在恢复计划里，`--delete-extra` 对它完全失效 |
| 根因 | `_collect_plan` 只扫「目录型 item」的**下一层**：`covered_parents` 由 `Path(i).parent` 得来，于是 `data/outline`、`data/chapters` 进了列表，但它们的子目录（`data/outline/chapters`、`data/chapters/raw`）没进 |
| 不对称点 | 恢复动作是 `shutil.copytree`（**整棵**替换），而 extras 检测只扫一层 —— 两者口径不一致 |
| 后果 | 用户看不到「恢复会失去哪些新章节」，`--delete-extra` 形同虚设 |

**修法**：extras 改为按**目录型 item 的整棵子树递归**比对（`_scan` 递归 +
遇到「工作区多整个目录」时逐个列出文件，而不是给一个笼统目录名）。
反向验证：退回单层扫描 → 用例 4 条 FAIL。

### 🔴 新缺陷 S13：`run_log.load_logging_cfg` 遇到结构错配置会抛

新写的 `test_run_log.py` 里「坏配置不得抛异常」用例抓到：
`logging:` 被写成标量（如 `logging: info`）时，`section.get` 抛
`AttributeError`。调用方是**启动路径**，配置写歪不该让服务起不来。

**修法**：非 dict 时回退 —— 若是字符串则**当作 level 值**（比整个丢掉更友好），
否则用默认值。

### ✅ 测试

本轮新增 **3 个回归套件（91 断言）**，全部经反向验证：

| 套件 | 断言 | 守护对象 |
|---|---|---|
| `tests/unit/test_run_log.py` | 37 | 日志轮转（超期归档/清理白名单/级别过滤/解包顺序/dry-run/容错） |
| `tests/unit/test_snapshot_restore.py` | 30 | `--restore` 真实演练（dry-run 零改动/pre_restore 折返点/extras 递归/范围限定/路径护栏） |
| `tests/unit/test_api_domains.py` | 24（原 18） | 拆分纪律 + `__main__` 别名守卫 |

另**修复 1 个被重构打破的旧用例**：`test_config_and_models.py` 的第 D 节
按分支边界切 `nf_api.py` 源码文本再 grep，实现搬家后误报红。
改为「先定位实现所在文件（顺转发目标找到域模块），再在文件内断言」——
**契约不变，位置可变**。修完 29/29，并反向验证仍能捕获真实回归。

**全量回归（本轮结束状态）**：

```
契约测试                 35/35
域模块护栏               24/24
HTTP 套件 × 8           263/263
failure_paths            69/69
stream_stage             18/18
stop_and_mingjian        38/38
config_and_models        29/29
run_log / snapshot       37 + 30
pyflakes 门禁            BLOCK=0 ✅
```

### 📌 已知技术债（本轮有意保留，未处理）

- **`/review/report`、`/proofread/report`、`/book/pacing` 用相对路径**
  （`Path("data/...")`，依赖进程 CWD），而同族其它端点用 `api.ROOT / ...`。
  拆分时**刻意保留原行为** —— 改成 `ROOT` 会让 `--root` 场景的语义变化，
  那属于行为变更，不该混在重构里做。已在 `nf_api_domains/runtime.py`
  的模块 docstring 里记录，建议后续单独处理。
- 全仓 pyflakes 历史告警 52 条（较第二轮 +6，来自新域模块的反向属性访问模式），
  策略仍是「只计数不阻塞」。

> ↑ 第一条已在**第四轮**修掉（有了「确实会读错项目」的实证后，它不再是取舍问题）。

---

## 第四轮：实证驱动的两项清理（2026-09-20）

第三轮结束时留了两条技术债，其中一条当时判为「行为变更，须单独处理」。
本轮先做**实证**再决定 —— 结论是：它不是取舍问题，是真缺陷。

### 🔴 S14：相对路径 IO 在 `--root` 场景静默读错项目

**发现方式**：不再靠推理，直接构造场景实测：

```
A = 书本A（有 review_report.json，marker=FROM_A）
B = 书本B（--root 指向它）
cwd = A，--root = B
```

| | 修前 | 修后 |
|---|---|---|
| `GET /review/report` | `200 {"marker": "FROM_A"}` ❌ | `200 {"marker": "FROM_B"}` ✅ |
| `GET /proofread/report` | 读到 A 的文件 | `404 暂无校对报告`（正确跟随 B）✅ |
| `GET /book/pacing` | 读到 A 的拆书产物 | `404 尚无拆书结果` ✅ |

**为什么至今没爆**：Electron 主进程是 `spawn(cmd, args, {cwd: ws})`
**且**同时传 `--root ws` —— CWD 与 ROOT 恰好相等。
但 `--root` 这个参数的存在本身就说明**设计允许两者不同**，
所以这是**潜伏缺陷**：一旦真的用它指向别的书档，读到的是 CWD 的书，
且不报错、不警告。

**修法**：17 处 `Path("data/...")` / `api.Path("logs/...")` 全部改为
`ROOT / ...` / `api.ROOT / ...`。影响文件：

| 文件 | 处数 | 端点族 |
|---|---|---|
| `scripts/nf_api.py` | 9 | `/costs` `/costs/summary` `/refine/*` `/review/run` `/batch_refine/run` |
| `nf_api_domains/misc.py` | 4 | `/costs/summary` `/batch_refine/progress` `/chapters/quality` `/chapters/verify` |
| `nf_api_domains/runtime.py` | 4 | `/review/report` `/review/decisions` `/proofread/report` `/book/pacing` |

**回归风险为零**：Electron 场景 CWD==ROOT，测试场景 CWD==临时根，
两种情况下 `ROOT / ...` 与 `Path("...")` 解析到**同一个路径**。
8 个 HTTP 套件（263 断言）全绿佐证。

**护栏**：`tests/unit/test_api_domains.py` 新增第 7 节，用 AST 找
`Path("<字面量>")` 里以 `data/` `logs/` `output/` `config/` `prompts/`
`materials/` `templates/` 开头的调用。反向验证：把 `runtime.py` 退回相对路径
→ 断言 FAIL 且精确定位 `runtime.py:62`。

### 🔴 S15：`run_log.log_status` 的 `age_days` 会出现 `-1 天`

**发现方式**：核对「文档声明断言数 vs 实测」的脚本里，
`test_run_log.py` 某次跑出 36/1 而不是 37/0 —— 复跑 3 次又全绿，
说明是**偶发**，顺藤摸到了根因。

| 项 | 内容 |
|---|---|
| 现象 | 刚写的日志，`log_status()["age_days"]` 偶尔返回 `-1`；`rotate_if_needed` 的 reason 里也出现 `age=-1 天` |
| 根因 | `timedelta.days` 对**负值向下取整**。而 mtime 可能比 `now()` 晚哪怕 1 微秒（文件系统时间戳精度高于两次 `now()` 之间的间隔），于是差值成了负的微秒级 timedelta |
| 后果 | 展示层出现「-1 天」这个不可能读数，会让人以为时间算错了 |

**修法**：抽出 `_age_days(mtime)`，内部 `max(0, ...)` 收敛；
`rotate_if_needed` 的 reason 与 `log_status` 都改走它。
**同时**把测试里那条 `in (0, 1)` 的断言收紧为 `== 0`
（当初写 `in (0,1)` 是为了「防跨午夜」，结果**掩盖了这个真缺陷**），
并新增一条「把 mtime 推到未来，age_days 仍须为 0」的回归用例。

反向验证：去掉 `max(0, ...)` → `age_days` 变 `-1` → 断言 FAIL。

### 📝 文档订正

- `AGENTS.md` 里 `test_auto_rewrite.py` 声明 39 断言，实测 **43**
  （该数字在第二轮回填后没再更新）。已订正。
- 同表补齐本轮变动的两项：`test_run_log.py` 37 → 38、
  `test_api_domains.py` 24 → 25。
- `AGENTS.md` 架构章节新增「路径 IO 必须经 `ROOT`」纪律与 S14 的反例。

### ✅ 全量回归（第四轮结束状态）

```
契约测试                 35/35
域模块护栏               25/25      （+1：无相对路径 IO）
HTTP 套件 × 8           263/263
failure_paths            69/69
stream_stage             18/18
stop_and_mingjian        38/38
config_and_models        29/29
run_log                  38/38      （+1：负值收敛回归）
snapshot_restore         30/30
pyflakes 门禁            BLOCK=0 ✅
```

### 📌 仍未处理

- **pyflakes 历史告警 52 条**（未使用导入 / f-string 无占位符）。
  策略仍是「只计数不阻塞」—— 一次性清零会冻结门禁，收益低于成本。
- **`tests/e2e/e2e_ux_verify.py`（声明 64 断言）未纳入常规回归**：
  它依赖 Playwright + headless Chromium + 已构建的 renderer，
  跑一次成本高。本轮改动仅涉及 API 层后端逻辑，未触碰前端，
  故未运行。**建议后续把它固定进「发版前必跑」清单**。


