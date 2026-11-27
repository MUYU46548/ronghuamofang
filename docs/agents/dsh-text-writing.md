# DSH 纯文本写作（`dsh.text_writing`）

> 2026-11-27 立。一句话：阶段 4 的正文改由**一次 completion** 产出，墨坊 Python 落盘；
> 模型不拿任何工具（连文件工具都禁）。这是把“写一章”从通用 agent 任务改回写作任务的
> 结构改造，不是又一个提示词补丁。

## 为什么改

旧路径把 stage4 的任务书交给 DSH 的通用 agent 工具面（fs / shell / search / jobs…）。
实测一个段群：68 步、7.4 万输出 token、正文只占 15%；其余花在逛目录、读
`prompts/` 与 `scripts/`、用 pwsh 数字数、反复 edit 过检。墨坊在模型返回后本来就会做
确定性校验，那一遍属于重复劳动。

四家同类产品的共同结构（详见 `docs/竞品台账.md` 的“架构对照”）：

1. 上下文确定性组装（Story Bible / Codex / truth files / `novel_context`）；
2. 正文一次生成（整章或整场景一次 completion）；
3. 工具窄而专用（ainovel-cli 的 Writer 只有 6 个写小说工具，没有 pwsh / 通用搜索）；
4. 审校 / 修订是另一条流水线，不混在第一次生成里。

墨坊的对齐点就是第 2 条：`DshClient.run_text_task_stream` 等价于 ainovel-cli 的
`draft_chapter`、Sudowrite 的 Draft、Novelcrafter 的 scene beat。

## 怎么走

- 配置：`config/system.yaml` 的 `dsh.text_writing`（默认 `true`）。
  `false` 回到旧的 agent 循环 + 工具写盘，保留作回退。
- 入口：`DshClient.run_text_task_stream(task_file, on_piece, stop_flag, ...)`。
  stage4 的 `_run_task_safe` 检测到 `client.text_writing` 为真就优先走它；
  FakeClient / 其他引擎没有这个属性，行为不变。
- 工具：`--patch` 覆盖层禁掉 `tool-pwsh` / `tool-bash` / `tool-fs` /
  `tool-fs-search` / `tool-jobs` / `tool-skill` / `tool-agent-team` / `tool-web`。
  连 `tool-fs` 都禁，模型只剩“在回复里输出正文”一条路。
- 输出路径：从任务书的 `将正文写入文件: <path>.md` / `将本群正文写入文件: …` 取；
  取不到就报错，不猜目录。相对路径必须能从任务书反推项目根，否则也报错。
- 落盘：回复 → `strip_protocol_markers` → 去代码围栏 → 写 `output_path`。
  任务书正文里那行“写入文件”在提示词里被替换成“正文由墨坊落盘”，避免模型
  在回复里写“已写入…”或试图改文件。

## 失败语义（与 `run_task` 同构）

- `final.reason.kind == "max-tokens"` → `finish_reason="length"`、`exit_code=2`、
  **不落盘**（截断一律当失败）。
- 回复为空 → `exit_code=1`、**不落盘**（模型可能把正文写进文件而不是回复）。
- 用户停止 → `stopped=True`、**不落盘**；半截正文由 stage4 的缓冲负责。
- 传输断连 → 按 `transport_retries` 重发；重发不回放片段，避免界面叠两遍。

## 判据

- `tests/unit/test_dsh_client.py` 的 `test_text_writing`：
  一次 completion 落盘、`tool-fs` 被禁、补丁不碰模型配置、截断不落盘、空回复不落盘。
- `tests/unit/test_stage4_text_writing.py`：
  有 `text_writing` 就只走纯文本，**不许**因为客户端没实现 `run_task_stream`
  悄悄回退到 agent 循环。
- `tests/unit/test_dsh_client.py` 原有 107 条写作边界 / 协议 / 超时用例保持通过。

## 不做什么

- 不碰 `reasoningEffort` / 模型名：那是 DSH profile 的配置。墨坊只发任务书。
- 不为 stage1–3 开纯文本：它们的产物是结构化 JSON，仍走带 `tool-fs` 的 agent 路径
  （但已内联任务书与输入，禁 shell/搜索）。
- 不做“失败自动回退 agent”：那会把一次失败变成两次付费调用，且掩盖真问题。
