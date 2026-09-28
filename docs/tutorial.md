# 从 0 到第一章：绒花墨坊快速上手

本教程带你从零开始，跑通绒花墨坊的第一章。全程约 15 分钟（含安装）。

## 前置要求

- Windows 10/11
- Python 3.11+（[python.org](https://www.python.org/downloads/)）
- Node.js 20+（[nodejs.org](https://nodejs.org/)）
- 一个 OpenAI 兼容 API Key（支持 TokenHub / DeepSeek / 硅基流动等）

## 第一步：安装

### 方式 A：安装包（推荐）

1. 到 [Releases](https://github.com/MUYU46548/ronghuamofang/releases) 下载
2. 双击安装，启动「绒花墨坊」
3. 首次启动按向导填书名/类型/章数

### 方式 B：源码运行

```bash
git clone https://github.com/MUYU46548/ronghuamofang.git
cd ronghuamofang
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
copy .env.example .env        # 填入你的 API Key
cd console && npm install && npm run build && cd ..
```

## 第二步：配置 API Key

1. 打开 `.env` 文件（项目根目录）
2. 填入你的 API Key：

```
TOKENHUB_API_KEY=你的密钥
```

3. 保存后重启绒花墨坊

## 第三步：放素材

把设定卡放到 `materials/raw/` 目录。每张卡是一个 `.md` 文件，格式如下：

```markdown
# 角色：角色名

> 来源：你的设定库路径

## 基本信息

- 正式名称：xxx
- 种族：xxx
- 性别：xxx

## 外貌与生活

- 外貌描述...

## 人际关系

- 角色A：关系说明
```

**快速体验**：复制示例书素材：

```bash
copy examples\sample-book\materials\* materials\raw\
```

## 第四步：跑流水线

1. 打开绒花墨坊控制台
2. 点「流水线」页签
3. 点「一键工作流 → 执行下一步」
4. 系统自动执行：素材归并 → 整体大纲 → 逐章大纲 → 逐章写作 → 逻辑检查 → 润色

**审批门**：阶段 2（大纲）和阶段 6（润色）完成后会暂停，等你确认。在「收件箱」查看产物，满意后点「通过」。

## 第五步：看成品

1. 点「审稿」页签 → 逐条查看 LLM 发现的逻辑问题
2. 点「校对」页签 → 确定性检查标点/错字/格式
3. 点「导出」页签 → 生成 Word 成品

成品在 `output/示例书目_完整版.docx`。

## 常见问题

**Q：控制台显示「离线」？**
看 `%LOCALAPPDATA%\Temp\nf_api_child.log`，或在命令面板 → 「查看运行日志」。

**Q：想换一本书继续写？**
「项目」页签 →「＋ 新建项目」（自动归档当前书），或「恢复」已归档项目。

**Q：改稿怕丢？**
每次精修前自动备份到 `data/chapters/history/`，可在「章节 → 历史」一键回退。

**Q：成本超了怎么办？**
系统自动熔断。调高 `config/system.yaml` 的 `budget.limit_yuan` 后重跑。

## 下一步

- 完整命令表见 `AGENTS.md`
- 历次变更见 `CHANGELOG.md`
- 目录结构说明见 `README.md`
