# 绒花墨坊（Ronghua Mofang）— 全自动长篇小说生成系统

> 可进化 AI 小说创作工作站。输入混沌素材，输出结构化长篇小说（Markdown → Word），全程自主执行，用户仅保留审批权。
>
> **对外品牌名：绒花墨坊**（`ronghuamofang`）；NovelForge 为早期内部代号。

## 核心特性

- **七阶段工作流**：素材梳理 → 整体大纲 → 逐章大纲 → 逐章写作 → 逻辑检查 → 基础润色 → Markdown 转 Word
- **目标驱动自主执行**：启动后自我调度直至完成；用户可随时暂停、打回、重跑
- **一致性机制**：设定集唯一事实源 + 滚动摘要链（rolling.md）+ 四路逻辑检查（P1）
- **断点续跑**：每章立即落盘 + progress.json + SQLite，中断后自动续跑不重复消耗
- **审批门**：阶段 2（整体大纲）完成后强制暂停等人工确认，其余默认全自动
- **成本透明**：cost_log 逐次记账 + 预算上限熔断（超限自动暂停）
- **模板与代码分离**：提示词存于 `prompts/`，可热更新，无需改代码

## 目录结构

```
NovelForge/
├── config/            # system.yaml（模型/预算/并发）+ project.yaml（书名/类型/章数）
├── materials/raw/     # 用户放置素材（.txt/.md/.docx）
├── prompts/           # 7 阶段提示词模板 + reference/（实战咒语存档）
├── data/              # 运行时数据：setting/outline/chapters/summaries/merged/state
├── history/           # 版本快照（完整运行自动备份）
├── logs/              # runs.db（SQLite 运行/章节/成本记录）
├── output/            # 最终交付 *.docx
└── scripts/           # orchestrator.py 主调度 + stage1-7 执行器 + utils/
```

## 快速开始

1. **填配置**：编辑 `config/project.yaml`（书名、类型、目标字数、章节数）
2. **放素材**：原始素材放入 `materials/raw/`（支持 .txt/.md/.docx；PDF 需手动转文本）
3. **启动**：`python scripts/orchestrator.py`

启动后自动执行；阶段 2 完成后暂停等待审批，流程见下。

## 常用命令

| 操作 | 命令 |
|------|------|
| 全流程启动（断点续跑） | `python scripts/orchestrator.py` |
| 从阶段 N 开始 | `python scripts/orchestrator.py --from N` |
| 只跑阶段 N | `python scripts/orchestrator.py --stage N` |
| 阶段审批（如确认大纲） | `python scripts/approve.py --stage 2` |
| 撤销审批 | `python scripts/approve.py --stage 2 --revoke` |
| 素材预扫描（仅归一化+去重） | `python scripts/stage1_consolidate.py` |
| 单章校验 | `python scripts/utils/verify_chapter.py <章文件或目录>` |
| 成本查询 | SQLite: `SELECT stage, SUM(cost_yuan) FROM cost_log GROUP BY stage;` |

## 审批与打回

- **审批门**：阶段 2（整体大纲）完成后 orchestrator 暂停（exit=3）。审阅 `data/outline/global.md` 后：
  - 满意 → `python scripts/approve.py --stage 2` → 重新运行 orchestrator 继续
  - 不满意 → 编辑/重跑阶段 2，或直接修改 global.md 后审批
- **打回重跑**：`python scripts/orchestrator.py --from N` 从指定阶段重跑（下游产物需先清空，可用 `--from` 配合手动清理 `data/chapters/`）
- **素材更新**：新增素材后 `--from 1` 重跑（设定集重新归并；已有章节文件不会被覆盖）

## 架构概览

```
用户层（Hermes 对话） → 网关层（Hermes 主会话） → 执行层（orchestrator + 子会话 + 脚本） → 数据层（文件系统 + SQLite）
```

- 网关 = 用户对话界面：解析意图、调度 orchestrator、处理审批/异常
- 执行层 = orchestrator.py（确定性调度）+ hermes chat -q 子会话（LLM 创作）+ stage 脚本（校验/转换/记账）
- 通信 = 文件系统约定目录（plan/state/gates/runs.db），无 HTTP/消息队列

## 测试状态

| 层级 | 结果 |
|------|------|
| P0-1 配置验证 | 39 PASS（YAML/结构/gitignore 行为） |
| P0-2 utils 验证 | 27 PASS（编码/进度/成本/校验/摘要链） |
| P0-3 全链路 FakeClient | 23 PASS + 15 PASS 冒烟（审批门/断点/降级/记账） |
| P0-4 模板化回归 | 17 PASS + 12 PASS 复核（模板加载/任务文本/3 章迷你链路） |

真实 LLM 阶梯测试：待执行（阶段 1 → 2 → 3+4 三章 → 全量）。

## 版本记录

- v0.1.0（P0 骨架完成）：目录/配置/utils/执行器/模板/审批 CLI；未跑真实 LLM 任务
