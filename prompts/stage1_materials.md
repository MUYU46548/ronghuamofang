---
stage: 1
name: stage1_materials
max_tokens: 6000
temperature: 0.3
---
# 阶段 1 任务：归并生成设定集

你是世界观架构师。请严格按指示执行。

## 输入文件（用 read_file 读取）
- 素材清单: {{path_manifest}}
- 待归并素材（**逐个**读取；已定稿的素材**不在**此列 —— 其定稿内容见下方 canon 快照）:
{{pending_inputs}}
{{canon_ref}}
{{extra_inputs}}

{{status_context}}

## 任务
阅读全部素材，完成归并工作并生成设定集：
1. 去重：删除重复表述
2. 合并矛盾：素材矛盾时取更详细/更新的描述，并注明取舍依据；
   **若两者信息量相当、无法用「更详细/更新」裁决，就取更保守的那份写入，
   并在该条目的 `merge_note` 里写明分歧点** —— 系统会另外列出需人工裁决的
   分歧清单（`data/setting/merge_audit.json`），**不需要你判断谁对**，也不要
   为了"看起来定了"而编造依据。宁可留分歧，不可假裁决。
3. 提取角色：姓名/身份/性格/关系/经历
4. 提取世界观：地点/势力/魔法或科技体系/物品
5. 提取情节碎片：可作为故事素材的事件片段
6. 若上方提供了**碎片提炼结果**（原始碎片 → 结构化信息点），一并纳入归并：
   - 它的 `points` 是可用信息点，按第 1-5 条同样处理；
   - 它的 `open_questions` 是作者**尚未定下**的事，归并时保留为待定，
     不得当成既成事实写进设定集，也不要替作者做决定；
   - 它的 `conflicts` 是碎片间的说法冲突，按第 2 条处理并注明依据。

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
