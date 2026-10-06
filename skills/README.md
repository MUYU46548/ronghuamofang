# skills/ —— 接线卡真源（仓库即备份）

本目录是**绒花墨坊接线卡的唯一真源**。Hermes 侧
`%LOCALAPPDATA%\hermes\skills\` 里的同名卡是**部署副本**（装卡覆盖，Hermes 侧只读，
不在 Hermes 侧手改 —— 见技能库 `skill-management-policy`）。

## 布局

```
skills/
└── worldbuilding/            ← 与 Hermes 技能库的分类目录同名
    └── ronghuamofang/        ← 操作接线卡（只管「用」，不管「改」）
        ├── SKILL.md
        └── references/
            └── api.md        ← 端点清单（快照，滞后以 nf_api.py docstring 为准）
```

开发类卡（`novelforge-gui` / `ai-novel-pipeline`）真源仍在 Hermes 技能库，
不在本仓库。

## 装卡（部署副本同步）

改完 `SKILL.md` 后执行（覆盖是部署动作，不是事故）：

```bash
# 仓库 → Hermes 技能库
cp -r skills/worldbuilding/ronghuamofang "$LOCALAPPDATA/hermes/skills/worldbuilding/"
```

## 纪律

1. **改卡只改本仓库**，然后装卡；不直接编辑 `%LOCALAPPDATA%` 下的副本。
2. 卡里**不维护会过期的清单快照**（命令/端点以现场文档为真源：`AGENTS.md`、
   `scripts/nf_api.py` docstring、`scripts/*.py --help`）。
3. 卡内容变更需要同步 `docs/mcp-connect.md` 等文档时，一并改，不留两处口径。
