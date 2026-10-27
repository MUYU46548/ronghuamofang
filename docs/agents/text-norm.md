# 中文标点归一化（2026-10-18，确定性、零 token）

> 原 `AGENTS.md` 的一节，2026-10-27 拆分至此（原文件 76,326 字节，超过 65,536 字节的
> 指令加载预算会被系统**静默截断**，被截掉的恰是靠后的《能力边界》《硬性约束》）。
> **纪律效力不变**：动这块代码/数据前按需读本文。

**为什么必须是代码不是提示词**：提示词里早就写着「所有标点使用全角中文标点」，产物照旧
（实测《雾港核心》refined 稿 76 个半角 `"`、67 对 `「」`，成品 docx 里 `“` 出现 0 次）。
形态错误只能靠确定性替换治理——可单测、可幂等、可事后验收。

**唯一实现** `scripts/utils/text_norm.py`（`normalize_chinese_punctuation` /
`normalize_chapter_file`）。判据：半角 `" `按**行内开合交替**转 `“”`（偶数个严格交替、
奇数个保留最后一个为孤儿不硬造配对）；`「」`→`“”`、`『』`→`‘’`；单 `—`/`--`→`——`；
`...`/`。。。`/单 `…`→`……`；汉字间半角 `,;:!?`→全角；括号内含汉字→全角。

三条护栏（每条都是实测踩出来的）：
- **幂等** —— 挂载点有 7 处，重复跑是常态；`f(f(x)) == f(x)` 由用例断言。
- **保守** —— 英文语境一律不动（`--flag`、`3-5`、`don't`）；破折号只在汉字/中文标点
  语境下转换。孤儿引号保持原样 —— 宁可留脏，不可把正文改错。
- **保护区** —— `<!-- -->` 注释块与 ``` 代码围栏**整块跳过**（注释里全是 ASCII，
  归一化它会把 `<!-- quality: 8/10 -->` 改坏 → 下游 `QUALITY_RE` 再也匹配不上）。

挂载点（都在 LLM 落盘后、下游校验前）：stage4 / stage5 / stage6 / `refine_chapter` /
`refine_paragraph` 各自落盘后，外加 stage7 `merge_book()` 合并时**兜底**——
前面各阶段只覆盖新产出的章，**修复前写下的历史脏章**只有合并这一步才洗得到，
所以成品 docx 永不带脏标点。失败只打印不阻断主链路（标点是清洁工作，它坏了不该拖垮写作）。

**误开 `gates.auto_rewrite` = 双倍烧**（审稿前先跑一轮重写）。本机默认 `false`；
orchestrator 启动横幅会打印生效值（`gates: auto_rewrite=… auto_refine=… review_after_stage4=…`），别靠翻 YAML 猜。

**审稿 JSON 断裂**：解析统一走 `utils.validator.parse_llm_json`（含括号配对补全）；
仍失败 → 按 `gates.review_retries`（默认 1）重试 → 仍败则**原文隔离** `data/state/review_raw/` +
可行动报错 + 审稿门 fail-closed（不放行 stage5/6）。调用本身失败（退出码非零）**不重试**（同一上限只会再截断一次）。

**截断类失败不自动重试**（2026-10-03）：`finish_reason == "length"` 时除了隔离残缺稿，
还会写机器可读标记 `data/state/truncated/last.json`（判据 = `utils/truncation`，
写 `mark()` / 读 `after(阶段开始时刻)`）。orchestrator 的 `auto_retry`（默认开、默认 2 轮）
**先过 `should_auto_retry()`**：截断 → 一票否决并打印「同一上限只会再截断一次，白烧一倍输入 +
该调 `budget.token_limit.per_request_max_tokens`」。自检 `tests/unit/test_truncation_no_retry.py`（28 断言）。

**输入段格式**：任务「输入文件」段每条路径必须是**列表行**（`- 名称: <路径>`）。
格式不符（含目录引用指向不存在的目录）→ `InputSectionFormatError`（行号 + 原文 + 正确写法），
**不再静默返空** —— stage5 空稿事故的同类根因。

**Agent 模式守卫（CLI 侧，2026-10-15 方案A；2026-10-16 扩族 P2）**：`gates.agent_mode=true` 时，
`approve.py` / `reject.py` / `segment_cli.py`（勾段与撤销）对 CLI 审批**无条件拒绝**，
且 `plan_cli.py`（select/write/drop/revive 四态拍板）、`switch_book.py`（--archive/--restore/--init）、
`import_docx.py`（非 --preview 落盘）三个拍板/破坏性写入家族同日补闸 —— HTTP 侧早在这张禁用名单里、
CLI 侧此前**零守卫**（Agent 一条命令即可拍板/搬空工作区/整本盖稿）。propose（可提案不可自选）、
只读命令、`--preview`、`--list` 不受限。判据唯一实现
`utils/agent_guard.cli_approval_gate()`（恒拒；策略原因 + 原证据进 `agent_audit.jsonl` 台账）。
用户拍板原话「都用桌面端了，没人会用 CLI」，**审批唯一入口 = 桌面端 GUI 双因子**。
原正路「真实交互终端」（双端 `isatty` 快筛 + 祖先进程链取证）废止但机制保留：`cli_human_evidence`
仍是唯一证据实现（**正判 ≠ 放行**），链须落在用户终端锚点、不得经自动化宿主、取证失败或链截断
= fail-closed 拒（2026-10-15 红队修复：深度上限截断同样打 `?`）；`--human` / `MOFANG_SOURCE=gui`
只当声明留痕，不参与放行（V1/V2）。拒绝文案零通道名，只说「停手并报告用户」（V5，拒文不教绕行）。
只读命令（`segment_cli list/status` 等）不受方案A限制（Agent 可读）。⚠️ 这不是沙箱，别对外宣称更强。

**控制台输出纪律**：`print` 里**不要**写非 GBK 字符（`¥`/`⚠️`/`✅`/emoji）——
stdout 被管道捕获时 Python 用 cp936，一行日志就能 `UnicodeEncodeError` 打死长跑
（实测：hermes 横幅把 orchestrator 启动搞崩、`quality_gate` 通过却退 1 假红）。
入口已统一调 `utils.console.ensure_utf8_stdout()` 兜底，但新代码请仍按此纪律写。

**project.yaml**：读写都必须走**带重复键检测**的 loader（`utils.project_config`）。
裸 `yaml.safe_load` 对重复键静默取后值 → 换书可能把数据归档到**错误书名**下。
`switch_book.py` 配置坏时**拒绝执行**（含 `--list`），改完再换书。
⚠️ 它的 `PROJECT_ROOT` 取自**脚本位置**（不是 CWD）：从别的目录敲
`python <某路径>/scripts/switch_book.py --archive`，动的是**脚本所在的那个仓库**
（本轮就因此归档过一次真实工作区，已恢复）。现它会在任何写操作前打印
「将操作的项目根」，CWD 不同还会显式提示 —— 别忽略那两行。

**配置类 YAML 一律经 `utils/config_io`（2026-10-03 收口）**：`load_config_yaml` 严格
（重复键/语法错 → `ConfigError` 带**行号 + 危害 + 体检入口**）、`load_pipeline_config`
（system ← local 深合并 + project，三份全严格）、`set_section_scalar(s)`
（**定向改写**保留注释 + 备份 + 写后按值回读；嵌套段路径如 `("budget","token_limit")`）。
**结构性护栏**在 `tests/unit/test_config_io.py` 第 5 节：`scripts/**` 出现裸 `yaml.safe_load(`
即 FAIL，豁免名单（模板 frontmatter / nfctl 只读诊断 / 设定集数据文件）**每条都要写理由**。

**收尾质量闸门（`quality_checklist.py`）**：**产物缺失 = 失败**（`all_pass=False` + 指出
`data/chapters/{refined,raw}/NN.md` 两个候选路径 + 说明「跑过但没落盘」的含义）。
确属还没写到那一章时用 `--allow-missing`，届时摘要必须写「**未经验收**」—— 跳过 ≠ 通过。
`book.chapters = 0` 这类空集合也直接报错（vacuous truth 是门禁最阴的假绿）。
