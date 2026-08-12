---
stage: 7
name: stage7_convert
---
# 阶段 7：Markdown → Word

本阶段为确定性脚本阶段（scripts/stage7_convert.py），无需 LLM 子会话。

职责：
- 合并 chapters/refined/（缺省回退 checked/raw）→ merged/book.md
- 行解析转换：标题（#/##/###）→ Heading，列表 → List，其余 → 段落
- 复杂元素降级策略（v2 4.6）：表格/图片/代码块 → 普通段落文本
- 输出 output/{书名}_完整版.docx

P1/P2 增强：markdown AST 完整保留（表格/图片/代码块按节点写入）。
