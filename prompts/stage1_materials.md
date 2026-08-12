---
stage: 1
name: stage1_materials
model: deepseek-v4-flash
max_tokens: 6000
temperature: 0.3
---
# 阶段 1 任务：归并生成设定集

你是世界观架构师。请严格按指示执行。

## 输入文件（用 read_file 读取）
- 素材清单: {{path_manifest}}
- 归一化素材目录: {{path_normalized}}/ 下的 *.md（按清单逐个读取）

## 任务
阅读全部素材，完成归并工作并生成设定集：
1. 去重：删除重复表述
2. 合并矛盾：素材矛盾时取更详细/更新的描述，并注明取舍依据
3. 提取角色：姓名/身份/性格/关系/经历
4. 提取世界观：地点/势力/魔法或科技体系/物品
5. 提取情节碎片：可作为故事素材的事件片段

将设定集写入文件: {{path_setting}}

## 输出格式（setting.json，JSON 文件）
{
  "characters": [{"id": "...", "name": "...", "role": "...", "traits": [...], "relations": [...]}],
  "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
  "plot_fragments": [{"id": "f001", "source": "素材名", "summary": "...", "status": "unused"}],
  "timeline": [{"event": "...", "year": null, "locked": false}],
  "_meta": {"version": 1, "generated_from": [...], "vault_readonly": true}
}

## 要求
- 四个顶层键必须全部存在（characters/world/plot_fragments/timeline）
- 每条目保留 source 溯源字段
- locked=true 表示硬约束（后续写作不可违逆），仅对明确的设定库条目使用
