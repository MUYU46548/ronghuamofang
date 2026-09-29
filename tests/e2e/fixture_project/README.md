# e2e 夹具项目（Hermetic fixture for 真机视觉验收）

**用途**：给 `tests/e2e/e2e_ux_verify.py` 的「真后端」那一段提供一个**状态固定**的项目根，
让断言不依赖你本机正在写的那本书。启动方式：

```bash
.venv/Scripts/python.exe scripts/nf_api.py --port 8799 --allow-fake --root tests/e2e/fixture_project
```

**为什么需要它**：该段的断言写的是「阶段 1-4 已完成 → 下一步 = 运行阶段 5」。
若指向真实项目，而本机那本书已经跑完（或停在别的阶段），断言必然失败 ——
2026-09-29 实测就是这条：本书 1-7 全 done、阶段 6 待审批，于是「下一步」是阶段 6 而非 5。

**夹具状态**：阶段 1-4 全部 `done` 且 `approved: true`；阶段 5/6/7 `pending`。
按 `App.vue` 的 nextAction 规则（先找「done 但未批准」的审批门，再找第一个非 done 的阶段）：

    gate = 无（1-4 都已批准，5-7 都没 done）
    pend = 阶段 5   →  下一步 = 运行阶段 5·逻辑检查

**注意**：本夹具只固定 `data/state/progress.json`（`/state` 已改为经 `ROOT` 解析）。
`/estimate`、`/chapters/quality` 等端点里仍有一批 **CWD 相对路径**（见验收报告 §11），
所以它们读到的仍是**主项目**的数据 —— 对本段的断言（只要求有真实数字）不构成影响，
但这是「一律经 ROOT」纪律尚未清完的存量，别把它当成夹具设计上的疏忽。
