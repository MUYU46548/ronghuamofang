# 出厂预设（能力验证用例）

这里是绒花墨坊随包出厂的**能力验证用例**：每套一套纯文本素材 + 一份 `preset.yaml` + 一份 README。
它们不是文档，是**可复现的验收起点**——从同一已知起点出发，跑完一轮就知道：
这一版流水线能跑到哪、断在哪、改动有没有打破既有能力。

总占用不到 100 KB。

## 清单

| id | 题材 | 章数 | 验什么 | 进 GUI |
|---|---|---|---|---|
| `rail-end` | 悬疑 | 6 | 低设定密度下剧情能否靠人物推进；三条伏笔能否各自回收 | 是 |
| `tidal-archive` | 科幻 | 8 | 多 POV 不串味；locked 物理规则跨章不违逆 | 是 |
| `salt-town` | 现实 | 10 | 跨 20 年时间线；年龄关系可推演；物证链不断 | 是 |
| `seventh-letter` | 悬疑 | 5 | 书信体：每章"信 + 第三人称"两种语体可区分 | 是 |
| `mill-dirty` | 奇幻 | 4 | 脏输入：无名后缀、日期文件名、素材矛盾 → 必须报冲突 | **否**（隐藏用例） |

## 用法

```bash
python scripts/preset_apply.py --list
python scripts/preset_apply.py --apply rail-end --dry-run     # 只看计划
python scripts/preset_apply.py --apply rail-end               # 空工作区直接铺
python scripts/preset_apply.py --apply rail-end --replace     # 有数据：先快照→归档→重铺
python scripts/preset_apply.py --apply mill-dirty --hidden-ok --replace
```

铺完直接 `python scripts/orchestrator.py`。

## 纪律

- **不要"修好"`mill-dirty`**：它故意做脏（缺后缀、年龄矛盾），修好了就失去测试价值。
- 每套的 `verify.must_mention` 是本套的专名清单，素材里必须都有落点；
  `verify.forbidden` 是本套禁词。两者由 `tests/unit/test_presets.py` 守着（261 条断言）。
- 改素材或大纲后必须跑一次：`python tests/unit/test_presets.py`
  历史上出过"素材整套换掉、大纲没换、门禁全绿"的事故，这条锁就是为它设的。
- 这些都是**虚构内容**，仅作测试素材，不是作品。
