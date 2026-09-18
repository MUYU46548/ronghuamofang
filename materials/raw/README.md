# materials/raw/

此目录默认空。结构化素材卡放在此处供流水线消费。

## 格式

每张卡片是一个独立的 Markdown 文件，YAML frontmatter 声明类型：

```markdown
---
type: 角色卡      # 角色卡 | 场景卡 | 概念卡 | 组织卡 | 物品卡 | 事件卡
name: 露汐
---

# 露汐

（正文内容：外貌、性格、能力、关系等）
```

## 快速上手

1. 到 [Examples](../examples/) 查看完整示例工程（含一本书的全套素材、设定、大纲）
2. 复制 `examples/leaving-your-song/materials/` 到你自己的 `materials/raw/` 试跑流水线
3. 想从零开始？直接新建 `.md` 文件，填上 frontmatter 即可

## 导入/导出

- 导入：直接复制 `.md` 文件到此目录
- 导出：运行 `python scripts/stage1_consolidate.py` 查看素材归并与冲突

> 提示：`materials/original_scraps/` 用于存放随手写的碎片（自由命名、不进 Git），
> `materials/raw/` 用于存放结构化的、流水线消费的卡片。
