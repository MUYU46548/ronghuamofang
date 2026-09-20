# 绒花墨坊（NovelForge）全面工程审查与长期演进报告

**日期**：2026-09-19
**工作流**：工作流 1（综合代码审查）+ 工作流 3（事故响应复盘）+ 工作流 5（技术债评估）三合一
**参与成员**：Cody（代码审查师）/ Archi（系统架构师）/ Rex（SRE 工程师）/ Tessa（测试专家）/ Docu（技术文档师）
**审查范围**：`scripts/` 37 个顶层 py + `scripts/utils/` 22 个模块（约 18,055 行）、`console/` Electron+Vue 18 个组件、`config/`、`prompts/`、`Temp/` 67 个自检脚本、全部文档与 Git 历史

---

## 📌 TL;DR（执行摘要）

- **整体结论**：**架构分层是健康的**（引擎抽象、提示词分离、白名单落盘、Electron 安全基线都做对了），**但工程保障体系基本缺位**——CI 从未建立、测试不在版本控制内、零 Linter、无独立 code review。这导致**缺陷只在打包/实跑时暴露**。
- **发现 3 个「默认配置下必崩」的活缺陷**（非理论风险，见 §2 验证矩阵）：

  | 缺陷 | 位置 | 触发条件 |
  |------|------|---------|
  | `datetime` 未导入 → `NameError` | `orchestrator.py:282` | **`auto_retry` 默认 `true`，任一阶段失败即触发** |
  | `only_stage` 未定义 → `NameError` | `nf_api.py:2017` | GUI 勾选「流式输出」跑流水线即触发 |
  | 全项目无路径下 `obsidian.vault_path` 硬编码 `E:/图书馆/ROSA` | `config/system.yaml:110/113` | 任何其他用户开箱即失败 |

- **严重度分布**：🔴严重 **8** 项 / 🟠高 **12** 项 / 🟡中 **14** 项 / 🟢低 **8** 项
- **阻塞 / 非阻塞**：**🔴 阻塞发布**。第 1 条 `auto_retry` 崩溃路径是**默认开启**的，一旦阶段失败，「自动重试」这个兜底机制本身会崩，并把 `runs.db` 留在脏状态。
- **最大隐性风险**：不是"覆盖率低"，而是**「声称覆盖 > 实际覆盖」**——项目用 `py_compile`（纯语法检查）当质量门禁，对 `only_stage`/`datetime` 这类**名字写错**完全无效。这比没测试更危险，因为它制造了虚假的安全感。
- **务实判断**：修复 §2 三个活缺陷约需 **1 小时**；建立最小 CI 门禁（pyflakes + 跑测试）约 **半天**。这两件事的投入产出比远高于任何架构重构。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 整体评级 | 🔴 **不通过（阻塞发布）** — 存在默认配置下的崩溃路径 |
| 架构健康度 | 🟢 良好（分层清晰，关键抽象到位） |
| 工程保障体系 | 🔴 缺失（CI 空目录 / 测试不入 Git / 零 Linter / 无独立 review） |
| 测试成熟度 | 🟡 C+，但**分项「测试可信度 = D」** |
| 文档成熟度 | 🟡 C（核心文档质量高但引用失效、内部纪律冲突） |
| 阻塞项数量 | **3**（缺陷）+ **4**（体系性前置） |
| 关键行动项 | 12 条（P0 四条 / P1 四条 / P2 四条） |
| 建议下一步 | 先修 3 个活缺陷 → 建最小质量门禁 → 再谈架构演进 |

---

## 一、审查范围与方法

| 维度 | 方法 | 覆盖 |
|------|------|------|
| 代码 | 逐文件精读 + AST 语义检查 + 交叉复验 | `scripts/` 核心 12 模块、`console/main`+`preload`、抽样 4 个 stage |
| 结构 | git 历史挖掘（`git log --all`）、目录/配置实测 | 全部 commit、`.gitignore`、`.github/`、`config/*.yaml` |
| 事故 | commit 考古 + `开发日志.md`（59KB 分段读） | 近 20 个 commit 中的 fix 类 |
| 测试 | 逐脚本盘点 + 覆盖矩阵交叉对照 | `Temp/` 67 个 py + 5 个 js，`scripts/` 2 个自测 |
| 数据 | SQLite 直查 + 文件系统实测 | `logs/runs.db`、`history/`、`data/` |
| 复验 | 主理人对成员每条关键结论独立复跑 | 3 个活缺陷全部复验坐实 |

**关键实测数据**（非文档声称值）：

- `scripts/` 代码量：**18,055 行**（37 顶层 py + 22 utils）
- 最大单文件：`nf_api.py` **2,785 行**、`utils/llm_client.py` **864 行**、`console/main/index.js` 448 行
- 测试资产：`Temp/` 下 **67 个 py + 5 个 js + 129 张 png**，`scripts/` 下 **2 个**自测
- 累计运行成本：**¥6.25**（31 次调用；stage4 占 ¥4.05 / 17 次）；预算上限 ¥300
- `history/` 快照：完整；`runs` 表 33 行；`.github/`：**完全空目录**

---

## 二、🔴 三个「默认配置下必崩」的活缺陷（已三方独立复验）

> 这是本次审查**最高价值**的产出。三条都不是理论推演——主理人亲自复跑了代码。

### S1 · `orchestrator.py:282` — `datetime` 未导入，重试路径必然 `NameError`

**代码事实**（主理人复验）：

```python
# scripts/orchestrator.py 第 22-26 行的全部 import
import argparse
import os
import sys
from pathlib import Path
import yaml
from utils.file_io import read_text
# ← 无 import datetime（grep 计数 = 0）

# 第 282 行，位于 auto_retry 重试循环内部：
retry_started_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
```

**为什么必崩**：`config/system.yaml` 中 `gates.auto_retry: true`（**默认开启**）。任一阶段失败 → 进入重试循环 → 第 282 行首次执行即 `NameError: name 'datetime' is not defined`。

**为什么静默且严重**：
1. 该行处于 `while` 循环体，**无 try/except 包裹** → `NameError` 穿透 `run()`
2. `db.finish_run(run_id, ...)` 因此**永不执行** → `runs` 表残留 `status='running'` 脏行
3. 「自动重试」本是**失败兜底机制**，结果它自己是最脆的一环——**兜底崩了**
4. 因为测试是 fake 驱动（`FakeClient` 恒返回 `exit_code=0`），失败路径**从未被执行过**，所以一直没被发现

**修复**：`orchestrator.py` 顶部加 `from datetime import datetime`（1 行）。
**同时建议**：给重试循环体加 `try/except`，确保任何异常都能走到 `db.finish_run()`。

### S2 · `nf_api.py:2017` — `only_stage` 未定义，流式跑阶段必然 `NameError`

**代码事实**（主理人复验 + Rex AST 级验证，两方独立坐实）：

```python
# 第 1985 行：局部变量名是 only
only = int(rest)
from_stage = int(body.get("from_stage") or only)
if body.get("only_stage") is False:
    only = None
...
# 第 2017 行（在 _fn_stream 后台闭包内）：引用了不存在的名字 only_stage
rc = orch_run(from_stage=from_stage, only_stage=only_stage, client=client)
# 第 2036 行（非流式分支）却正确使用了 only  ← 两份平行代码，改一处漏一处
```

**触发条件**：GUI 勾选「流式输出」调用 `POST /stage/{n}/run`。`App.vue:607` 与 `App.vue:745` **两个入口均发 `stream:true`**——即 GUI 的默认运行路径。

**为什么这个缺陷比 S1 更阴险**（Rex 的关键补充）：

1. **执行时序错位**：`nf_api.py:2034` **先**向客户端发出 `202 {job_id, stream:true}`，**之后**才在后台 worker 线程执行 `_fn_stream` → `NameError`
   → **GUI 会显示「任务已启动、正在流式输出」，然后永远等不到任何 token，也不报错**
2. **叠加资源泄漏**：`STREAMERS` 条目建立后**永不消费、永不释放**（内存驻留 10000 容量队列），且**无任何日志**
3. **失败无信号**：用户看到的是「卡住了」，而非「出错了」——**典型的静默失败**

**为什么零覆盖**：全部 HTTP 自检脚本**无任何** `stream=true` 用例 → 该分支静态（契约测试不做未定义名检测）+ 动态（无用例）双零覆盖。

**修复（两步，缺一不可）**：
- **止血**：`only_stage=only`（1 行）
- **根治**：**消除流式/非流式平行分支**——合并为单一 `_run_stage_core(cfg, only, from_stage, stream=False)`。平行代码是「改一处漏一处」的结构性温床，打补丁只能治标。
- 补 `stream=true` 契约用例（断言 job 正常收尾 + `STREAMERS` 被清理）

### S3 · `config/system.yaml` — 硬编码用户本机绝对路径 + 废弃命名残留

```yaml
obsidian:
  vault_path: "E:/图书馆/ROSA"                    # 用户本机私有路径，已进 Git
  sandbox_dir: "E:/图书馆/ROSA/Obsidian_AI_Sandbox/10_Inbox"
```

**三重问题**：
1. **泄露用户本机路径**到公开仓库（`config/` 按纪律进 Git）
2. **`ROSA` 是已废弃命名**——`git log` 有 3 个连续 commit 专门做 ROSA→Obsidian 重命名（`fee46f2`→`fbfbff2`→`1d613b0`），**此处是漏网残留**
3. **对任何其他用户开箱即失败**：vault 路径不存在 → Obsidian 联动全部功能不可用

**修复**：改为空字符串或相对路径占位，首次使用时由 GUI 引导用户填写并写入本地未跟踪配置。

---

## 三、🔴 体系性阻塞项（比单个缺陷更根本）

### B1 · CI 从未建立，且 `.gitignore` 会让未来的 CI 静默失效

**实测**：`.github/` 是**完全的 空目录**（`git ls-files .github` 返回空）。**这个项目从未有过任何 CI**。

**更隐蔽的坑**——主理人实测 `git check-ignore -v`：

```
.gitignore:53:*.yml    .github/workflows/ci.yml
exit=0   ← exit 0 意味着「该路径确实被忽略」
```

`.gitignore` 第 53 行的 `*.yml` 全局规则**会把所有 GitHub workflow 文件吃掉**。也就是说：**即使现在去写 CI 配置，文件也会被静默忽略、推不上仓库、CI 永远不跑**，而且不报错。这是一个「做了却无效」的陷阱。

**修复**：`.gitignore` 加 `!.github/workflows/*.yml` 白名单（或把 `*.yml` 收紧为 `console/release/*.yml`）。

### B2 · 全部测试资产不在版本控制内 —— 本次审查的**根因**

`Temp/` 被 `.gitignore` 整体忽略，而它是项目**事实上的测试目录**（67 个 py 自检脚本 + 5 个 js + 129 张验收截图）。

后果链：
- fresh clone → **测试全部消失** → 无法验证任何改动
- 没有测试可跑 → CI 即便建立也无内容可跑 → **B1 与 B2 互为因果**
- `AGENTS.md` 把 `Temp/test_*.py` 当正式资产引用（20+ 处）→ 文档描述了一个**不存在的仓库状态**
- `.gitignore` 里 `Temp/` 被标注为「临时与附件」——**目录语义与实际职责严重错位**

**修复**：新建 `tests/` 目录纳入 Git，把 `Temp/test_*.py` 迁入（保留 `Temp/` 仅存截图等临时产物）。

### B3 · 零代码质量工具链 + `py_compile` 是无效门禁

**实测**：项目根目录**无** `pyproject.toml` / `setup.cfg` / `.flake8` / `ruff.toml` / `.pre-commit-config.yaml` / `mypy.ini` —— **零 Linter、零 Formatter、零类型检查**。

**致命细节**：项目以 `py_compile` 作为质量门禁，但 **`py_compile` 是纯语法检查**。本次发现的 `only_stage`、`datetime` 两个缺陷，**语法完全合法**——`py_compile` 报不出任何错。

> **我们这轮发现的 3 个真实缺陷，最基础的 `pyflakes` 能拦 2 个。现在一个都没拦住。**

### B4 · `.gitignore` 的 `Temp/` 忽略 + 危险的 `nf_api_selftest.py`

`scripts/nf_api_selftest.py` 会**清空 `data/` 与 `logs/`** 后跑全链（`history/` 快照保留）。这与 `AGENTS.md` 硬性约束「破坏性操作前必须先 `snapshot.py` + 征求用户确认」**直接冲突**——文档内部存在纪律矛盾（详见 §7）。它靠 `make_snapshot("selftest_wipe")` 自保，但这个保护**本身没有测试**。

---

## 四、🔍 完整审查发现（按严重度排序，去重合并）

### 🔴 严重（7）

| # | 类别 | 位置 | 问题 | 影响 | 建议修复 | 来源 |
|---|------|------|------|------|---------|------|
| S1 | 正确性 | `orchestrator.py:282` | `datetime` 未导入 | **默认配置下必崩**，兜底机制失效 + runs.db 脏行 | 加 import + 包 try/except | Cody/Rex/Tessa/主理人 |
| S2 | 正确性 | `nf_api.py:2017` | `only_stage` 未定义 | 流式跑阶段 100% 崩 | 改 `only_stage=only` | Cody/Tessa/主理人 |
| S3 | 安全/配置 | `config/system.yaml:110,113` | 硬编码私路径 + ROSA 残留 | 泄露路径、他机不可用、命名债 | 改占位符 + 引导填写 | Cody/Docu/主理人 |
| S4 | 工程保障 | `.github/`（空） | **从未有 CI** | 无任何自动化门禁 | 建最小 CI | Rex/Tessa |
| S5 | 工程保障 | `.gitignore:53` | `*.yml` 会静默吞掉 workflow | 未来 CI 做了也无效 | 加 `!.github/workflows/*.yml` | 主理人 |
| S6 | 工程保障 | `.gitignore` (`Temp/`) | 67 个测试脚本不入 Git | fresh clone 后测试全丢 | 迁入 `tests/` 并跟踪 | Tessa/Docu |
| S7 | 安全 | `console/main/index.js:210` | `.env` 在「打开外部编辑器」白名单内 | 明文密钥可用外部程序直接打开 | 移出白名单或只读预览 | Cody |
| S8 | 安全/纪律 | `nf_api.py:2708-2735`（`/models/switch`）<br>`nf_api.py:2683`（`/models/add`） | **模型准入「约束数据存在、回路从未接通」**：白名单数据（`providers.*.available_models ∪ _manual`）确实存在，且 `fetched_models.json` 作为载体被 **4 处路径常量**引用（L1418/1422/1463/2692）、**6 处代码读写**（L1414/1449/1458/1476/2697/2703）——但全部消费点均为「展示或缓存维护」，**不存在任何读取-校验回路**；`/models/switch` 更不读取任一白名单数据源 | `AGENTS.md:159`「模型白名单纪律」硬约束**在代码中从未实现**。**问题不是"忘了校验"，而是"精心维护了一份从不生效的约束数据"**——任意格式合法的模型名可落盘并生效 | 修复方向不是"补个 `if`"，而是**接通回路**（让已存在的载体首次参与判定）：写入层收敛时两级校验 ① 格式合法性 ② 白名单归属；**且须先补齐 `/models/switch` 缺失的第一级**，否则把"弱入口"变成"统一弱基线" | Archi+Cody 穷举复核（11 处全景）+ 主理人复验 |

### 🟠 高（12）

| # | 类别 | 位置 | 问题 | 建议修复 | 来源 |
|---|------|------|------|---------|------|
| H1 | 可观测性 | `config/system.yaml:108` | `logging.rotate_days: 30` 是**死配置**（全仓无 `RotatingFileHandler`） | 接 `RotatingFileHandler` 或删除该键 | Rex/Tessa |
| H2 | 可观测性 | 全局 | 无结构化错误日志 / 无诊断包导出 | 加 `logs/session.log` 结构化 + 「导出诊断包」 | Rex |
| H3 | 恢复能力 | `snapshot.py` | 只有 `--list`，**无 restore 入口** | 补 `snapshot.py --restore <id>`（先 dry-run） | Rex/主理人 |
| H4 | 测试可信度 | 全局 | 「声称覆盖 > 实际覆盖」；`py_compile` 无效 | 引入 `pyflakes`/`ruff` | Tessa |
| H5 | 测试 | `Temp/*` + `utils/fake_client.py` | 失败路径 **100% 零覆盖**（FakeClient 恒 `exit_code=0`） | 新增 `FailingClient` + F1~F7 用例 | Tessa/Rex |
| H6 | 代码质量 | 全局 | 零 Linter/Formatter/类型检查 | 引入 `ruff` + 渐进式类型标注 | Cody |
| H7 | 架构 | `nf_api.py`（2,785 行） | 单文件 God Object，路由+业务+SQL+IPC 混杂 | 分阶段拆路由/服务/领域层 | Archi |
| H8 | 并发 | `nf_api.py` + `orchestrator` | 长时间任务与 HTTP 线程耦合，全局状态风险 | 明确任务隔离与状态所有权 | Archi |
| H9 | 数据一致性 | `progress.json` + `runs.db` | 双轨状态无事务，崩溃留半写状态（S1 已实例化） | 定义单一状态真源 + 原子写 | Archi |
| H10 | 发布 | `console/` | 无代码签名；升级可能破坏用户数据 | 签名 + 升级前自动快照 + 回滚 | Rex |
| H11 | 成本运维 | `orchestrator` 重试链 | `auto_retry`(2) × `auto_rewrite`(1) 叠加可能超预期费用 | 加费用速率告警 + 熔断前预警 | Rex |
| H12 | 文档 | `AGENTS.md` | 引用 20+ 个 `Temp/*.py` 全部失效；内部纪律冲突 | 全量校正 + 拆分为 Runbook | Docu |

### 🟡 中（14）· 摘要

- 命令速查表与实际 CLI `--help` 存在漂移风险（无自动校验）
- `README.md` 与 `AGENTS.md` 内容重复，双份维护漂移
- 数据契约（`review_report.json` / `proofread_report.json`）靠 dict 传递，无常量定义与 schema 校验
- `git status` 有 **20 个已删除未提交**的 `examples/leaving-your-song/materials/*` 文件 + 未跟踪 `examples/sample-book/`
- 无 `CHANGELOG.md`（但已有 v0.2.0、NSIS 发布等版本行为）
- 无 `CONTRIBUTING.md`；无 `docs/architecture.md`；无 `docs/data-contracts.md`
- `开发日志.md` 59KB 被 gitignore，其中有长期价值的工程决策未沉淀
- Emoji 与中文混排的日志输出未统一；异常信息部分直接 `str(e)` 上屏
- 无「恢复演练」脚本——快照恢复路径从未被验证
- 5 秒级 `time.sleep` 阻塞式退避在 GUI 场景无进度反馈
- `console/` 无前端单元测试（全部依赖 E2E）
- 无 API 版本协商机制（`/health` 无 schema 版本）
- `materials/original_scraps/` 用户私人数据**无独立备份机制**
- prompts 模板无变更影响分析（改模板不知会影响哪些阶段）

### 🟢 低（8）· 摘要

- 部分脚本 `__main__` 缺 `if __name__` 保护的检查
- `scripts/utils/` 有 22 个模块但缺 `__all__` 声明
- `docs/` 只有 2 张图 + 1 md，未形成体系
- `THIRD-PARTY.md` 未含前端依赖（`console/package.json` 的 devDeps）
- 无 `.editorconfig`；无 `.gitattributes`（Windows 换行风险）
- 测试截图 129 张无清理策略
- `snapshot.py` 保留 10 份的硬编码常量未配置化
- 部分中文注释与英文标识符混用无约定

---

## 五、🏗️ 架构评估（Archi）

### 当前架构画像

```
┌─ 表现层 ────────────────────────────────────────────┐
│ Electron main (448行, contextIsolation✓ nodeIntegration✗) │
│   └─ preload (27行, contextBridge 白名单 IPC — 做得好)     │
│       └─ Vue 3 (18 组件: App/NewProjectWizard/Review...)  │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP (127.0.0.1:8765)
┌─ API 层 ─────────────▼──────────────────────────────┐
│ nf_api.py (2785行 ⚠️ God Object)                     │
│   路由 + 业务逻辑 + SQL + 任务调度 + SSE  ← 全混一起      │
│   ⚠️ 同进程 import orchestrator 函数（非子进程）         │
└──────────────────────┬──────────────────────────────┘
┌─ 编排层 ─────────────▼──────────────────────────────┐
│ orchestrator.py (329行, 退出码 0/1/2/3/4)             │
│   ⚠️ 重试循环无异常保护 → S1 缺陷                      │
└──────────────────────┬──────────────────────────────┘
┌─ 阶段层 ─────────────▼──────────────────────────────┐
│ stage1..stage8 (每个可独立 CLI 调用 ✓ 设计良好)          │
└──────────────────────┬──────────────────────────────┘
┌─ 横切层 ─────────────▼──────────────────────────────┐
│ utils/ (22 模块)                                      │
│   llm_client(864行, 引擎抽象✓ thinking兼容✓)            │
│   cost_tracker / progress_manager / validator ...     │
└──────────────────────┬──────────────────────────────┘
┌─ 数据层 ─────────────▼──────────────────────────────┐
│ data/*.json|md (主) + logs/runs.db (辅) ⚠️ 双轨无事务   │
│ history/ 快照(承担版本职责) + data/*/history/ 模块备份   │
│ materials/ (raw / original_scraps) + prompts/          │
└─────────────────────────────────────────────────────┘
```

### 六个专项评级

| # | 专项 | 现状 | 评级 |
|---|------|------|------|
| a | `nf_api.py` 2785 行单文件 | 路由/业务/SQL/调度/SSE 全混 | 🔴 |
| b | 文件系统 + SQLite 双轨状态 | 无事务、无单一真源、崩溃留脏 | 🔴 |
| c | 三层备份机制 | `history/` + `data/*/history/` + `_backup/` 职责边界模糊，**恢复路径不可验证** | 🟡 |
| d | 同进程函数级复用 | GUI 长任务阻塞 HTTP 线程；全局状态共享 | 🟠 |
| e | 数据契约单一真源 | `review_report`/`proofread_report` schema 对齐但**未显式定义校验** | 🟡 |
| f | 可扩展性（→多用户/云端） | **最大阻塞点**：状态强绑定本地文件系统 | 🟠 |

### 关键决策记录（ADR）

**ADR-1：是否拆分 `nf_api.py`**
- **背景**：2,785 行单文件，路由与业务混杂
- **决策**：**分阶段拆**——先抽 `api/routes.py`（纯路由表）+ `api/services.py`（业务编排）+ `api/repo.py`（SQL），保留 `nf_api.py` 作为薄入口
- **备选**：① 不拆（继续加）② 换 FastAPI 重写
- **后果**：现有 65 个 HTTP 断言**必须全绿**才允许合并；不换框架（避免重写风险）

**ADR-2：状态真源与写入层约束**

**背景**：系统实际存在 **4 组状态重复**（经 Archi 逐条代码复核，非原认知的 1 组）：

| 编号 | 多轨 | 代码证据 |
|------|------|---------|
| D1 | `runs.status` 写入侧 vs 读侧 | `orchestrator.py:316-317` finally 只 `db.close()` **无补偿**；`nf_api.py:615-616` 注释自陈「不按 status='running' 过滤——崩溃残留旧 running 行会盖掉真实结果」 |
| D2 | `config/system.yaml` 两条写入路径 | `nf_api.py:2708-2732` `safe_dump` 整体重写 vs `project_config.py:140-221` 定向改写 |
| D3 | progress.json / runs.db / 章节文件 | `stage4_writing.py:231` 写 progress + `:238` 写 DB，**两写间无事务** |
| D4 | JOBS / STREAMERS / STOP_EVENTS 内存三态 | `nf_api.py:136-144,176` |

**裁决一（D2，最重要）**：`config/*.yaml` 定性为**用户资产**，不是程序状态。
`config/system.yaml` 含 **11 处纯人读注释**，其中 L93-96 是**程序永不消费的行为契约**（「退避系数：3s → 6s → 12s…」）、L62 熔断恢复语义、L88-89 stage 5.5 开关语义。故 `/models/switch` 的 `safe_dump` **从"格式问题"升级为"违反架构原则"**。抽 `utils/yaml_patch.py` 供 system.yaml/project.yaml 共用定向改写——它是这条原则的载体。

**裁决二（D1）**：加编排级**幂等补偿**，**不引入真事务**。
`orchestrator.py` 的 `finally` 加 `db.finish_run_if_running(run_id, "crashed")`。读侧 `ORDER BY id DESC` 的"绕过"**保留**——它是有效的深度防御第二层。

**裁决三（D3）**：**下调严重度**——是"报表分叉"，不是"数据损坏"。
依据 `stage4_writing.py:172-174`：断点续跑**不信任 progress 就跳过**，而是**逐章回验文件完整性**（`is_chapter_complete`）。所以「progress 说完成、DB 无记录」最坏只导致**报表少统计一章，不会丢稿**。
**真源排序**：断点真源 = **文件产物 + progress.json 缓存**（现状正确）；成本真源 = **runs.db**。理由：`runs.db` 记 `(run_id, stage, chapter)`，**跨 run 无法表达"第 7 章写完了"**，结构上不适合当断点真源。
**不追求强一致**（三者是不同用途视图，强一致是伪需求），改为加 `GET /state/consistency` 对账端点，**显式报告分叉章号**。

**裁决四（D4）**：并入 ADR-4 的 `JobRegistry` 统一处理（内存三 dict 无生命周期正是其第三个实证理由），降为 P2。

**ADR-2 一句话定稿**：**状态真源唯一化不适用于全部场景**——正确做法是按用途分层（产物 > 断点缓存 > 报表）并对分叉显式化；但 `config/*.yaml` **必须收敛为单一写入路径**（用户资产原则）。

**ADR-3：测试资产是否进 Git**
- **背景**：67 个测试脚本在 `Temp/`（被 gitignore）
- **决策**：**必须进**——新建 `tests/` 目录，迁入并设 CI 执行；`Temp/` 只留截图等临时产物
- **后果**：测试成为可信资产；需要处理测试之间对 `Temp/` 路径的硬编码引用

**ADR-4：是否引入类型检查**
- **背景**：零类型标注，S2 类"变量名写错"缺陷无法静态发现
- **决策**：**渐进式**——第一步只上 `ruff`（含 `F821 undefined-name`，能拦 S1/S2）；`mypy` 仅对 `utils/` 新代码强制
- **后果**：低成本立即获得收益，避免一次性标注 18,000 行

### 明确不做的重构（避免过度设计）

| 不做 | 理由 |
|------|------|
| 换 FastAPI / 上 Web 框架 | 本地单人应用，`http.server` 够用，重写风险 >> 收益 |
| 引入消息队列 / 分布式任务 | 单机单人，SQLite + 线程池足够 |
| 全面重写为微服务 | 18,000 行的收益边际为零，破坏性极大 |
| 把 `data/*.json` 换成数据库 | 文件可读性/可 diff 是**刻意的设计选择**（用户能直接看大纲），不该丢 |
| 全量类型标注 | 成本极高，按 ADR-4 渐进即可 |

### 长期演进路线

**阶段一（1-2 天）· 止血：让系统不崩**
- 目标：消除 3 个活缺陷 + 建立最小质量门禁
- 动作：修 S1/S2/S3 → `.gitignore` 加 `!.github/workflows/*.yml` → 上 `ruff`（含 F821）→ 建最小 CI
- **退出标准**：`ruff check` 零 error；CI 在 push 时真实运行；三缺陷各有回归用例

**阶段二（1-2 周）· 建立保障：让问题能被发现**
- 目标：测试可信、失败路径有覆盖、运维可恢复
- 动作：迁移测试入 `tests/` → 新增 `FailingClient` + F1~F7 失败路径用例 → 补 `snapshot --restore` → 落实日志轮转 → 统一状态真源（ADR-2）
- **退出标准**：失败路径覆盖率 > 60%；快照恢复经过一次真实演练；`rotate_days` 生效或删除

**阶段三（1-2 月）· 架构优化：为长期演进铺路**
- 目标：拆 God Object、解耦 API 层、契约显式化
- 动作：按 ADR-1 拆 `nf_api.py`（保持 65 断言全绿）→ 数据契约抽常量+schema 校验 → 文档体系重构 → Obsidian 联动配置外置
- **退出标准**：`nf_api.py` < 800 行；契约有显式定义；文档引用零失效

---

## 六、🚨 事故响应复盘（Rex）

### 事故清单

| # | 事故 | 证据 | SEV | 根因（5 Why 收敛） | 残余风险 |
|---|------|------|-----|-------------------|---------|
| A1 | **思考模式空白产出**：glm-5.x/kimi 正文全进 `reasoning_content`，`content` 为空 | `AGENTS.md:12-14`；`2026-09-14` 修复 | **SEV-2** | 供应商侧默认行为变更未纳入兼容矩阵；**产出为"空"不报错**（静默失败） | 新模型不在 `disable_thinking_models` 名单；探测本身失败时的兜底正文质量未验证 |
| A2 | **ROSA→Obsidian 全面重命名**：3 个连续 commit | `fee46f2`→`fbfbff2`→`1d613b0` | SEV-3 | 专有名词硬编码进代码；概念未集中到配置常量 | **`config/system.yaml:110/113` 仍有 ROSA 残留**（S3 已坐实） |
| A3 | 打包启动失败 / NSIS 弹窗侵略性 | `acce7a4`、`2af6f43` | SEV-3 | 打包产物从未被自动验证 | 无打包后冒烟测试 |
| A4 | CSS 缺失闭合括号 `3c30e43` | commit | SEV-4 | 无 CSS 语法检查 | 同类问题可复发 |
| A5 | `QualityTrend` 首次加载不触发 `165ecb1` | commit | SEV-4 | 无前端单测 | 同类问题可复发 |
| A6 | 自动更新 404 报错过长 `e186d24` | commit | SEV-4 | 错误信息无长度约束/无降级 | 用户体验受损 |
| A7 | **S1 `datetime` 崩溃**（本轮新发现） | `orchestrator.py:282` | **SEV-2** | 失败路径零测试 + `py_compile` 无效门禁 + 重试循环无异常保护 | **当前仍活着** |

### 深度复盘 A1（最高 SEV）：为什么会「静默产出空白」

**5 Why**：
1. 为什么流水线产出空白？→ 模型把正文写进了 `reasoning_content`，`content` 为空
2. 为什么没早发现？→ 空白不是异常，流程"成功"走完了，只是产物是空的
3. 为什么空白不算失败？→ 校验环节**校验的是格式与长度规则，而非"内容是否有意义"**——空字符串可能通过了长度下限之外的其他检查
4. 为什么供应商行为变了没预警？→ 无兼容性矩阵、无模型行为基线测试、无"新模型接入前的探针"
5. 为什么缺少这些？→ **项目没有 CI 与模型兼容性测试的概念**——测试都是"跑通就算过"

**修复有效性**：三层防线（名单注入 / 探测重试 / `reasoning_content` 兜底）设计**是合理的**，方向正确。
**未闭环项**：① 三层防线**自身没有测试**（`test_thinking_compat.py` 在 `Temp/` 里，不入 Git）；② 缺少"产出内容非空且非退化"的**业务级断言**——这是比技术防线更根本的一层。

**行动项**：在 stage4/stage6 的校验里增加「正文非空 + 非重复退化」的硬断言，让空白产出**变成显式失败**而不是静默成功。

### 可运维性五维评估

| 维度 | 现状 | 评级 | 关键改进 |
|------|------|------|---------|
| 可观测性 | `logging.level: info`；`rotate_days` 是**死配置**；无结构化日志 | 🔴 | 落地 `RotatingFileHandler`；加「导出诊断包」按钮 |
| 故障恢复 | 有 `progress.json` 断点续跑 + 12 处快照调用点；**但 `snapshot.py` 无 restore 入口**；恢复路径从未演练 | 🟠 | 补 restore CLI + 加恢复演练脚本 |
| 部署升级 | Electron NSIS + 内嵌 Python + 自动更新；**无代码签名**；升级可能覆盖用户 workspace | 🟠 | 签名；升级前自动快照；失败回滚 |
| 备份数据安全 | `materials/original_scraps/` 是私人数据且不进 Git，**无独立备份**；快照纪律在代码中确有执行（`approve.py:28`、`reject.py:65`、`orchestrator.py:209` 等 12 处 ✓） | 🟠 | 碎片独立备份；碎片导入导出功能 |
| 成本运维 | `cost_tracker` + `runs.db`（累计 ¥6.25 / 预算 ¥300）；`estimate_tokens` 预估；熔断 `manual` 模式 | 🟢 | 增加费用速率告警（如单阶段 > ¥20 提示） |

**值得肯定**：快照纪律**不是纸面规定**——主理人 grep 到 12 处真实调用点（`approve.py:28`、`batch_refine.py:355`、`reject.py:65`、`orchestrator.py:209`、`nf_api.py:805/873` 等）。`snapshot.py:43` 还有「安全拦截」（校验路径含 `history` 段）。**这体现了良好的工程自觉**。

### Runbook 缺口（现有 6 个场景之外的缺失）

1. **崩溃后诊断**：`runs.db` 有 `status='running'` 脏行怎么办？（S1 会制造这种情况）
2. **快照恢复**：怎么从 `history/{ts}_标签/` 恢复？粒度是整项目还是单模块？
3. **数据迁移**：换机器 / 重装系统，`data/` + `materials/` 怎么完整搬迁？（原计划由"导入/导出"承担，尚未实现）
4. **模型切换验证**：换 provider/模型后的**验收清单**（费用核对 + 空产出自检 + 质量抽检）
5. **费用异常处置**：某阶段突然烧掉 ¥50 怎么定位与止血
6. **半损坏状态修复**：`progress.json` 与 `data/` 实际产物不一致时怎么对齐

### SLO 建议（本地桌面应用应定义的指标）

| 指标 | 建议目标 | 为什么值得定义 |
|------|---------|---------------|
| 流水线阶段成功率 | > 95%（失败=必然要人介入） | 直接反映稳定性，A7/S1 会被立刻暴露 |
| **空白产出率** | **0**（硬指标） | A1 类事故的唯一有效拦截面 |
| 单章平均成本 | 有基线，偏离 > 50% 告警 | 防费用失控 |
| 阶段平均耗时 | 有基线，超时 > 3x 提示 | 发现 LLM 侧异常 |
| `runs` 表脏行数 | 恒为 0 | S1 类崩溃的可观测代理指标 |

---

## 七、🧪 测试策略与可信度（Tessa）

### 覆盖矩阵要点

**做得好的**：项目测试**并非一片空白**。`Temp/` 下 17 个自检脚本覆盖了 HTTP 契约（`test_new_endpoints_api_http.py` 65 断言）、E2E 真机验收（`e2e_ux_verify.py` 64 断言）、GUI↔API 契约静态核对（27 断言）。**「临时项目根/临时工作目录」隔离策略是一流实践**——真实 `data/` 零污染。

**结构性缺口（按风险排序）**：

| 模块 | 行数 | 业务重要性 | 覆盖 | 缺失风险 |
|------|------|-----------|------|---------|
| `utils/llm_client.py` | **864** | 🔴 核心中的核心（密钥+重试+thinking+落盘协议） | ❌ 仅 `llm_client_selftest.py` | 最高 |
| `utils/cost_tracker.py` | - | 🔴 算错就多花钱 | ⚠️ 间接 | 高 |
| `utils/progress_manager.py` | - | 🔴 状态坏了断点续跑失效 | ❌ | 高 |
| `utils/project_config.py` | - | 🟠 YAML 定向改写，坏了毁用户配置 | ⚠️ 间接 | 高 |
| `orchestrator.py` | 329 | 🔴 编排核心 + 退出码语义 | ❌ **含失败路径** | **最高**（S1 已证） |
| `approve.py` / `reject.py` | - | 🔴 审批门，坏了误删产物 | ❌ | 高 |
| `utils/file_io.py` | - | 🟠 原子写入 | ❌ | 中 |

### 🔴 Failure-Path 覆盖专项（本项目最大结构性盲区）

**核心结论：现有测试 100% 是 happy-path，失败语义面几乎零覆盖。**

**根因**（一个系统性机制）：

> `utils/fake_client.py` 的 `FakeClient.run_task` **硬编码 `"exit_code": 0`** → 所有以 fake 模式驱动的集成/HTTP 测试里**阶段永不失败** → 一切 failure path（重试、熔断、停止、异常恢复、脏状态）**永不执行**。

| 失败面 | 生产位置 | 覆盖证据 | 状态 |
|--------|---------|---------|------|
| 阶段失败自动重试 | `orchestrator.py:277-288` | `grep retry Temp/test_*.py` → **零命中** | ❌ **且已损坏（S1）** |
| 重试-预算熔断 | `orchestrator.py:288-299` | 同上 | ❌ |
| 重试-用户停止 | `orchestrator.py:288-299` | 同上 | ❌ |
| 流式跑阶段 | `nf_api.py:2017` | 全测试**无任何** `stream=true` 用例 | ❌ **且已损坏（S2）** |
| 日志轮转 | `system.yaml:108` | 全仓无 `RotatingFileHandler` | ❌ 死配置 |
| Electron 升级种子刷新 | `console/main/index.js:101-120` | `grep seedWorkspace Temp/` → 零命中 | ❌ 零覆盖 |

### 🔴 「声称覆盖 > 实际覆盖」——这是信任问题

比覆盖率低**更危险**，因为它制造虚假安全感：

- 项目用 **`py_compile` 当质量门禁**，但它是**纯语法检查**。`only_stage`、`datetime` 都是**语法合法**的，门禁对"名字写错"**完全无效**
- `AGENTS.md:80` 把契约测试描述成"名遮蔽 AST 检查"，但它**结构上不含未定义名检测**
- **本轮发现的 3 个真实缺陷，最基础的 `pyflakes` 能拦 2 个——现在一个都没拦住**

### Top 8 补测建议（按 风险÷成本 排序）

| # | 目标 | 类型 | 要验证的行为 | 为什么优先 |
|---|------|------|-------------|-----------|
| F2 | `orchestrator` 重试异常不外泄 | 集成 | `db.finish_run()` 被调用（runs 行 status≠running）；`run()` 返回 exit=1 而非抛 | **S1 回归** |
| F5 | 流式跑阶段 | HTTP | `{"stream":true}` 不崩，返回 202+job_id，SSE 有片 | **S2 回归** |
| F1 | 阶段失败→重试 | 集成 | progress 出现 `retry_count` 递增 | 兜底机制本身 |
| F3 | 重试-预算熔断 | 集成 | 超预算→停止，exit 语义正确 | 费用安全 |
| F4 | 重试-用户停止 | 集成 | stop→exit=4 | 用户控制权 |
| F6 | 未定义名静态检测 | 静态 | `ruff check --select F821` 零 error | **一次拦掉整类缺陷** |
| F7 | `seedWorkspace` 升级 | 集成 | 旧 `.seeded` workspace 升级后 scripts/prompts 被刷新 | 升级安全 |
| F8 | `approve`/`reject` 审批门 | 集成 | 打回后下游产物被清、状态被重置 | 防误删 |

**低阻力实现建议**：不必改造 `FakeClient`。新增 **`FailingClient`**（同签名，`run_task` 可配返回 `exit_code≠0` 或抛异常）或 `unittest.mock.patch` 掉 `orchestrator.STAGES[n].run_stage`，即可在**不动生产代码、不联网**下稳定复现。**F1/F2 约 2 小时可落地。**

### 成熟度评级

**总分 C+**，分项：结构 **B**（隔离策略好）/ 覆盖面 **C**（生产模块缺口大）/ 可信度 **D**（声称 > 实际）/ CI 集成 **F**（无）。

### 是否引入 pytest？

**建议：引入，但不强制重写。** 现有手写断言**能跑、有隔离、有真实价值**，不必推倒。pytest 的价值在于：① 统一发现（`pytest --collect-only` 能看到全部用例）；② fixture 复用（替代各脚本重复的"临时项目根"样板代码）；③ CI 集成标准。**迁移方式**：新用例用 pytest，旧脚本先整体纳入 CI 执行，逐步迁移。

---

## 八、📚 文档体系评估（Docu）

### 引用失效清单（实测）

`AGENTS.md` 命令速查表引用了 **20+ 个 `Temp/*.py` 路径**，而 `Temp/` 被 `.gitignore` 整体忽略 → **对任何 fresh clone 的读者/Maintainer，这些资产不存在**。同时 `AGENTS.md:80` 声称 `test_gui_api_contract.py` 做"死分支与丢失 elif 守卫"，实际结构不含未定义名检测 → **能力描述超出实现**。

### 准确性问题（文档说 X / 代码实际 Y）

| 文档陈述 | 代码实际 | 证据 |
|---------|---------|------|
| `Temp/*.py` 是正式自检资产（20+ 处） | 被 gitignore，不在仓库 | `.gitignore` `Temp/` |
| 契约测试做"名遮蔽 AST 检查" | 不含未定义名检测 | `Temp/test_gui_api_contract.py` |
| `rotate_days: 30` 控制日志轮转 | 无 `RotatingFileHandler`，死配置 | `grep -rn rotate_days scripts/` 零命中 |
| 「破坏性操作前必须先 snapshot + 确认」 | `nf_api_selftest.py` 会清空 `data/`（靠自快照，非用户确认） | `nf_api_selftest.py:90` |
| `prompts/`、`config/`、`scripts/` 进 Git（隐含"已提交"） | `nf_api.py`、`project.yaml`、`App.vue`、`NewProjectWizard.vue` 有未提交修改 | `git status` |
| 概念已统一为 Obsidian/vault | `config/system.yaml:110/113` 仍有 `ROSA` | 实测 grep |

### 完整性问题（新 Maintainer 会在哪卡住）

1. **无安装/环境准备文档**：`README` 未说怎么建 `.venv`、怎么配 `.env` 的 `TOKENHUB_API_KEY`、Python 版本要求（`requirements.txt` 注释说 3.11）
2. **无首次运行路径**：AGENTS 面向"用户只说运行 NovelForge"，但没写首次怎么启动 GUI（`启动绒花墨坊.bat` 未在文档中说明）
3. **无测试运行指南**：知道有测试，但不知道按什么顺序跑、跑哪些、哪些会毁数据
4. **无 `CHANGELOG.md`**：已有 v0.2.0、NSIS 发布等版本行为却无版本记录

### 结构问题与重复维护风险

`AGENTS.md` 22.5KB 单文件混杂**五种职责**：AI 操作手册 / 命令速查（40+ 条）/ 关键路径索引 / 运维 Runbook / 硬性约束。且命令速查表与 `README` 存在重复描述 → **双份维护、必然漂移**（已实测多例漂移）。

### 文档债清单

| 位置 | 问题 | 影响对象 | 成本 |
|------|------|---------|------|
| `AGENTS.md` 命令速查 | 20+ 失效引用 | 所有读者 | S（改路径） |
| `AGENTS.md` 整体 | 需拆为 AI 手册 + Runbook | Maintainer | M |
| `config/system.yaml` | ROSA 残留 + 私路径 | 所有新用户 | S |
| 缺 `CHANGELOG.md` | 无版本记录 | 用户/自己 | S |
| 缺 `docs/architecture.md` | 架构无沉淀 | 长期维护 | M |
| 缺 `docs/data-contracts.md` | 契约散落代码 | 改动风险 | M |
| 缺 `docs/troubleshooting.md` | 故障无手册 | 用户 | M |
| `开发日志.md` 59KB | 草稿，有决策价值的内容未沉淀 | 长期 | M |

### 文档体系建议

```
README.md              ← 对外：是什么、5 分钟跑起来、依赖与配置
docs/architecture.md   ← 架构分层 + 数据流 + 关键设计决策（含 ADR）
docs/data-contracts.md ← review_report / proofread_report / progress.json / APPEARANCES 契约
docs/troubleshooting.md← 常见故障（含 6 个缺失 Runbook 场景）
CHANGELOG.md           ← 版本记录（建议从 0.1.0 回溯补齐）
AGENTS.md              ← 保留为「AI 操作手册」，只留命令速查 + 硬性约束，拆出运维场景
```

**自动化建议**：
- 命令速查表**从各脚本 argparse 自动生成**（`--help` 解析），根治漂移
- 关键路径清单加**存在性校验**（CI 里跑一遍，失效即 fail）
- `开发日志.md` 中的**技术决策**沉淀为 `docs/adr/`，流水账部分可丢

### 成熟度评级：**C**（核心文档（AGENTS.md）信息密度高、意图清晰，是加分项；但引用失效、纪律冲突、无载体分层）

---

## 九、✅ 正面亮点（这份清单值得保留）

工程审查不该只说问题。以下做法**确实做对了**，应在重构中保护：

1. **引擎抽象干净**：`make_client(cfg, role)` 统一出口，`config/system.yaml` 切换 provider，**零调用点硬编码引擎/模型名**——这是很多成熟项目都做不到的纪律。
2. **提示词与代码分离**：`prompts/stage[1-7]_*.md` 独立，GUI 可编辑 + 自动备份 + 白名单校验。**调优无需改代码**。
3. **Electron 安全基线正确**：`contextIsolation: true` + `nodeIntegration: false` + `contextBridge` **白名单 IPC**（27 行 preload）——没有被"图省事"侵蚀。
4. **模型产物落盘白名单**：只有 `data/**` 与 `logs/runs.db`，期望外路径直接拒绝。**对 LLM 输出的不信任是正确姿态**。
5. **快照纪律是真的**：12 处真实调用点 + `snapshot.py:43` 的路径安全拦截。**不是纸面规定**。
6. **测试隔离策略一流**：`Temp/` 大量脚本采用"临时项目根/临时工作目录"，真实 `data/` 零污染。
7. **思考模式三层防线**：设计有层次（名单→探测→兜底），方向正确。
8. **用户数据保护意识**：`original_scraps/` 从不进 Git、碎片改写前备份、`materials/` 与 `data/` 职责分离。
9. **成本可见性**：`cost_tracker` + `runs.db` + `estimate_tokens` + 预算熔断 + `price_wizard` 计价向导。
10. **文档信息密度高**：`AGENTS.md` 的"关键路径"章节实际上是一份**数据契约清单**，价值很高。

---

## 📋 行动清单（按优先级排序）

| # | 行动 | 负责角色 | 紧急度 | 预期投入 |
|---|------|---------|--------|---------|
| **0** | **把 `py_compile` 门禁升级为 `pyflakes`**——**本轮 3 个缺陷中一次拦下 2 个**（`only_stage` + `datetime`），改动量≈零，仅加 dev 依赖、不动运行时零依赖 | Tessa | **P0（最高性价比）** | 30 分钟 |
| 1 | `orchestrator.py` 加 `from datetime import datetime` + 重试循环包 `try/except` 确保 `db.finish_run()` 必执行 | Cody | **P0** | 15 分钟 |
| 2 | `nf_api.py:2017` 改 `only_stage=only`；**并把流式/非流式合并为单一 `_run_stage_core`**（根治平行分支） | Cody | **P0** | 1 小时 |
| 3 | `config/system.yaml` 的 `vault_path`/`sandbox_dir` 去私路径、去 ROSA 残留，改占位符+首启引导 | Cody+Docu | **P0** | 20 分钟 |
| 4 | `.gitignore` 加 `!.github/workflows/*.yml`，并把 `console/main/index.js` 的 `.env` 移出外部打开白名单 | 主理人+Cody | **P0** | 15 分钟 |
| 5 | **建立模型准入回路**：写入层收敛，两级校验（格式合法性 + 白名单归属）；**须先补齐 `/models/switch` 缺失的格式级校验**，避免"统一弱基线" | Cody | **P0** | 1 小时 |
| 5 | 新建 `tests/` 并迁入 `Temp/test_*.py`（纳入 Git） | Tessa | **P1** | 1 小时 |
| 6 | 引入 `ruff`（`--select F821,E9`）+ 最小 CI（push 触发：ruff + 跑测试） | Tessa | **P1** | 半天 |
| 7 | 新增 `FailingClient` + F1/F2/F5 失败路径用例（S1/S2 回归） | Tessa | **P1** | 2-4 小时 |
| 8 | `snapshot.py` 补 `--restore <id>`（先 dry-run）+ 做一次真实恢复演练 | Rex | **P1** | 半天 |
| 9 | 落地日志轮转（`RotatingFileHandler`）或删除 `rotate_days` 死配置 | Rex | **P2** | 2 小时 |
| 10 | stage4/stage6 加「正文非空+非退化」硬断言（让空白产出显式失败） | Cody | **P2** | 半天 |
| 11 | 补 6 个缺失 Runbook 场景 + `docs/troubleshooting.md` | Docu+Rex | **P2** | 1 天 |
| 12 | 按 ADR-1 拆 `nf_api.py`（保持 65 断言全绿）+ ADR-2 状态真源统一 | Archi+Cody | **P2** | 1-2 周 |

---

## ⚠️ 待完善 / 已知局限

- **本次审查为静态分析 + 交叉复验，未执行任何会写 `data/` 的测试脚本**。三个活缺陷是通过**代码复验**坐实的（源码路径 + 默认配置 + 无异常保护），而非通过运行时复现——但结论强度足够（`auto_retry: true` 是默认配置，`datetime` 确实未导入）。**建议修复前先做一次最小运行时复现**。
- **未做依赖 CVE 漏洞扫描**：项目 Python 依赖仅 4 个包（`python-docx`/`lxml`/`typing_extensions`/`PyYAML`），前端 `console/node_modules` 有大量传递依赖，**本次未做 `pip-audit` / `npm audit`**，属审查盲区。
- **未实测 E2E 与 GUI**：`e2e_ux_verify.py`（64 断言）未运行（需起多个服务），前端真实表现未亲眼验证。
- **未做性能压测**：LLM 调用为外部瓶颈，本次关注点在正确性与工程保障，性能数据（如 stage4 单章耗时分布）未采集。
- **`console/node_modules`（10244 文件）未审查**，其依赖链风险未评估。
- 严重度评级基于**单人本地应用**这一形态校准：一个"其他用户开箱即失败"的配置问题（S3）在此形态下拔高为严重，因为它直接阻断分发。

---

## 📚 数据来源 & 成员产出索引

| 成员 | 产出 | 关键贡献 |
|------|------|---------|
| **Cody**（代码审查师） | 全栈代码审查（安全/性能/正确性/可维护性） | 发现 S1/S2 两个活缺陷、`.env` 白名单问题、Electron 安全基线核实 |
| **Archi**（系统架构师） | 架构评估 + 4 条 ADR + 三阶段演进路线 | God Object 拆分边界、状态双轨无事务、明确"不做"清单 |
| **Rex**（SRE 工程师） | 7 起事故清单 + 2 起深度 5Why 复盘 + 五维可运维性 + SLO | A1 空白产出根因、`rotate_days` 死配置、Runbook 6 个缺口 |
| **Tessa**（测试专家） | 覆盖矩阵 + Failure-Path 专项 + Top 8 补测 | 「声称覆盖 > 实际覆盖」定性、FakeClient 恒 exit=0 根因、`FailingClient` 方案 |
| **Docu**（技术文档师） | 文档实测盘点 + 引用失效清单 + 体系建议 | 20+ 失效引用、6 处文档-代码不符、文档分层结构 |
| **主理人复验** | 独立复跑 S1/S2/S3、`.gitignore:53` 的 `*.yml` 陷阱、`.github/` 空目录、快照 12 处调用点、累计成本 ¥6.25 | 三方交叉验证，推翻/确认成员结论 |

> 本报告由工程保障团队 AI 协作生成，关键决策请由人类工程负责人复核。
