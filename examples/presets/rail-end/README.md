# 出厂预设 · 铁轨尽头

**验什么**：低设定密度（只有 6 张卡、无超自然设定）时，stage3 细纲会不会空洞、
三条伏笔（怀表 / 时刻表 / 被撕的值班记录）能不能在 6 章内各自回收。

**规模**：6 章 × 5000-7000 字，现代悬疑。

## 跑法

```bash
python scripts/preset_apply.py --apply rail-end    # 铺素材 + 写 project.yaml（自动归档当前）
python scripts/orchestrator.py                     # 跑全流程
python scripts/nfctl.py check                      # 收尾闸门
```

## 判据

- 自动：`python tests/unit/test_presets.py`（素材自洽、专名有落点、跨宇宙无词重叠）
- 人工：6 章读完能不能说出"怀表是谁埋的、为什么"；说不出来 = 伏笔链断了
- 反向：`expect.forbidden` 里的词一个都不许出现（出现即注入串味）

## 注意

本套是**能力验证用例**，不是给读者看的作品。素材刻意留白（第三名死者身份不写），
用来测模型会不会自己编一个"完美答案"——编了就是越界改写设定。
