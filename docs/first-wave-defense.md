# 第一波防御工程 · 设计取舍与踩坑记录（2026-10-22）

对应施工单 01（四件：生成了没人拦 / 断了没人救 / 错了没人管 / 加强模式照原设计落）。
验收判据全部可离线验证：`test_hard_validate.py` 27 · `test_net_recovery.py` 20 ·
`test_enhanced_mode.py` 33 · `test_export_chain.py` 41，全量回归 140/140。

> 本文件是 AGENTS.md 对齐稿的载体：AGENTS.md 为受保护文件，本轮写入未获批准，
> 内容先落 docs/，待用户拍板后再并入手册。

## 任务一：字数与标点硬校验 + 打回重生成

### 标点校验器（`utils/punct_check.py`）

- **与 text_norm 的分工**：text_norm 修**修得掉的**（半角→全角、直角引号、重复标点
  折叠，挂 7 处落盘后）；punct_check 拦**修不掉的**（不成对《》（）‘’、未过归一化的残留）。
- **硬拦两类**：成对标号不成对（只列跨段引用极罕见的符号：‘’《》〈〉（）〔〕【】「」『』）、
  连续重复标点（同字符 ≥2；…—· 刻意排除 —— ……/—— 本来就是成对字符）。
- **双引号“”刻意不硬拦**：中文排版规范允许引语跨段（每段段首用前引号、末段才收尾），
  全文计数不配对是**合法形态**。硬拦的误伤代价是白烧一整章重写 → 降级为 warning。
- **保护区与 text_norm 一致**：`<!-- -->` 注释块与 ``` 围栏整块跳过（不剥会全数误报）。

### 判据挂载点的选择

- **进 `check_chapter`、不进 `is_chapter_complete`**：后者是断点续跑的"已完成"闸 ——
  标点校验上线前写的存量书稿成对配对不规范是常态，算进 incomplete 会让断点续跑把
  **存量书稿整批判未完成并全部重写**（白烧 + 新版往往更差）。新生效、不溯及既往。

### 打回重生成循环（stage4）

- 上限 `gates.hard_validate_regen_rounds`（默认 2；0 = 关闭打回，退回"不过即失败"）。
- 每轮策略：字数差一点（≥ 下限 60%）→ 定向扩写（读原稿补量，实证比整章重写有效）；
  其余（差太多 / 退化 / 标点）→ 整章重生成，**并把上一轮问题清单写进任务反馈**。
- 仍不过 → 停"待人工"（failed 章，不混入合格品），留痕指路 `data/state/validation/`。
- 每轮调用都记账 + 查熔断（一章多轮调用同样可能烧穿限额）。
- **夹具坑**：行内列表 `target_words: [5000, 9000]` 与旧块序列两种形态都要匹配，
  只认块序列会让夹具静默用上仓库真值（本单首轮就这么假红的）。

### 留痕（`utils/validation_log.py`）

`data/state/validation/NN.json`（单章完整现场）+ `summary.json`（处置一览）。
disposition：`passed`（含打回后通过，attempts 里能看到打了几轮）/ `regenerated`
（= 待人工）。IO 失败只 WARN 不阻断写作 —— 留痕坏了不该拖垮产线。

## 任务二：报错自愈与断点续跑

### 盘点结论

HTTP 层重试**已有**：`llm_client._post_chat` 指数退避 `2**attempt`、
`providers.*.retries` 可配（默认 5）、5xx/断连走重试、400/401/403/404 直接 raise
（死模型不吃链，2026-10-09 glm-5 教训）。阶段级 auto_retry、章级断点续跑、
崩溃窗口恢复（d178b07）也都已��。

### 真正缺的：异常兜底

断网时 `_post_chat` 重试耗尽后 raise → 异常穿透 `run_stage` → 整个 run 被
orchestrator 的 finally 收敛成 `crashed`，而不是"挂起当前章、保存进度、等恢复"。
现 `stage4._run_task_safe` 统一转 `exit_code=1` 失败结果，走既有
「记失败章 → 连败止损」路径。

- **失败原因点名"网络/传输异常"**：只报"子会话退出码非零"会把排障方向带偏到
  提示词/产物上，而真因是网络。
- **只兜客户端调用这一步**：任务构造（write_task / build_chapter_task）的异常仍穿透 ——
  那是代码/输入缺陷，吞掉只会让每章都悄悄失败。

### 验收形态

- 断网：client 抛 URLError → 不崩、记账、已完成章保留；恢复轮零重复。
- 强杀：真实子进程 `os._exit(1)`（无 finally/atexit）杀在「第 2 章写盘后、标完成前」
  → 重启后第 1 章（已记账）跳过、第 2 章（崩溃窗口孤儿）零 LLM 补账、第 3 章正常写。

## 任务三：加强模式（`chapter.enhanced_mode`，默认关）

### 拆群生成（`utils/enhanced_chapter.py` + `prompts/stage4_group.md`）

- `plan_groups`：目标中值 ÷ `enhanced_group_words`（默认 1500）→ 2~8 群，
  **各群配额之和 == 章节目标字数**（施工单原话；余数摊前面的群）。
- 段群任务模板沿用前缀缓存纪律（不变内容在 `---` 前，群号/配额/前文在后）；
  配额写成 `字数 X-Y 字` 区间形态（FakeClient 按该正则取目标字数，勿改裸数字）。
- 装机端模板缺失时**显式失败**（与 stage4_expand 的内置兜底不同：段群是新功能，
  劣质兜底文本会烧真模型钱）。

### 段群级断点续跑

每群立即落盘 `data/chapters/_parts/NN/gK.md`；重启时合格群（≥ 配额 50%）跳过重调。
**整章打回时必须 `clear_parts(n)`** —— 否则 part_ok 会把不过关的旧群当断点复用。

### 组装纪律

标题只留第一处、quality/summary 注释收集去重：真模型/FakeClient 都可能每群带标题，
不剥会拼出 K 个标题直接撞 verify_chapter 的标题判据（实测 FakeClient 正是每群全带）。

### 记账与熔断

段群逐群 charge（`_group_run` 闭包）；聚合结果带 `already_charged=True`，
`_finalize` 据此跳过重复 charge（否则一章记两遍账）。熔断也在群间查。

### 开关

- `GET/POST /config/enhanced_mode`：**严格布尔** —— `"true"` 字符串 / `1` 一律拒绝
  （开关字段静默把字符串当真值 = 用户以为关了其实开着，烧钱翻倍）。不设 GUI-only
  （与 target_words 同口径：创作决策）。
- GUI 设置页两步确认（`confirmArm` 武装 → 再点生效），**不用 window.confirm**
  （GUI 纪律：Electron 原生对话框不可靠）。
- 关闭时与改动前逐字节一致（test_enhanced_mode case_off_zero_impact 钉住）。

## 任务四：导出链模板化（校验不过不出文件）

### 三层校验（`utils/export_check.py`）

| 层 | 时机 | 硬拦 | 只警告 |
|---|---|---|---|
| `check_merged_md` | 导出前 | 占位符残留 / 协议标记残留 / **绝对路径泄漏** / 无章标题 / 章号重复 / 空章 / 章数不足 | 复杂元素（导出时降级） |
| `check_template` | 导出前 | 模板缺失/不可解析 / 缺 [请输入文本] 锚点 / 缺 Normal·Heading 1 | 缺 [作者] / [目录占位符] |
| `check_docx_output` | 导出后 | 四占位符残留 / 章标题数不足（不过 → **删文件**） | — |

### 关键取舍

- **模板缺失从静默降级改为报错**：旧代码 `Path(tmpl) if tmpl and Path(tmpl).exists()
  else None` —— 配置指向的模板丢了就静默回退硬编码样式，用户以为用了模板其实没有
  （与 KB 注入静默降级是同一类坑）。现为导出前错误。
- **失败消息必须点名具体问题**：只报"N 项未通过"等于让人去翻 stdout ——
  orchestrator 日志 / GUI 只拿到这条消息（本单测试当场抓出）。
- **占位模板已就位**：templates/小说格式模板V1.3.dotx；用户自有模板到手后改
  `book.word_template` 即插即用，不改代码（挂账兑现）。

## 全量回归

`nfctl.py test` 140/140（基线 136 + 新增 4 套验收）。唯一动过的既有用例：
`test_session_continuation` K7 的源码级断言（run_task 外包了 `_run_task_safe`，
session_id 经 **kw 透传 —— 判据认两种形态）。
