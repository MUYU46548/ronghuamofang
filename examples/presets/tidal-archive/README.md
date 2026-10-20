# 出厂预设 · 潮汐档案

**验什么**：多 POV（4 个视角）切换不串味、locked 物理规则跨 8 章不违逆。

**规模**：8 章 × 5000-7000 字，科幻。

## 跑法

```bash
python scripts/preset_apply.py --apply tidal-archive
python scripts/orchestrator.py
python scripts/nfctl.py check
```

## 判据

- **视角纪律**：白釉不得用"我"（全书只允许一次例外）；沈澜段落不得出现阿澈的方位口语；
  任何章节不得出现全知视角（同时知道两个人的内心）
- **locked 规则**：全书不得出现"年/月/日"式日期（只能用潮/潜/刻）；溺声症四条规则不可改
- **信息不对称**：每章只能推进当前视角知道的那部分，不得提前揭底
- 自动：`python tests/unit/test_presets.py`

## 注意

这套最容易翻车的地方是**模型自动补全全知信息**——它会让每个人物都知道剧情真相，
读起来顺，但多 POV 就废了。发现"所有人都知道一切"即判不合格。
