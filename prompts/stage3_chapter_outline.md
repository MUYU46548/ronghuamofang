---
stage: 3
name: stage3_chapter_outline
model: hy3
max_tokens: 10000
temperature: 0.6
---
# 阶段 3 任务：生成逐章大纲（第 {{first}}-{{last}} 章）

你是资深长篇小说编辑。请严格按指示执行。

## 输入文件（用 read_file 读取）
- 整体大纲: {{path_global_outline}}
- 设定集: {{path_setting}}
- 项目配置: {{path_project}}

## 任务
为以下章节生成详细大纲，每章写入独立文件：
{{listing}}

## 每章大纲格式（outline/chapters/NN.md）
## 第N章 章节名
- 核心事件：本章发生的关键事件（2-4 条，含因果）
- 涉及角色：出场角色及作用（列 id 或名字）
- 功能：本章在全书中的功能（铺垫/推进/转折/高潮/收束/过渡）
- 衔接：承接上一章的什么结尾，为下一章埋下什么钩子

## 要求
- 逐章顺序推进，与前章衔接、为后章铺垫，不得跳跃或冲突
- 事件必须符合设定集 locked 硬约束
- 不写正文，只写大纲；每章大纲 300-600 字
