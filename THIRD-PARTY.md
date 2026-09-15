# 第三方组件与许可（THIRD-PARTY NOTICES）

绒花墨坊（Ronghua Mofang / NovelForge）本体以 **MIT** 许可发布（见 `LICENSE`）。
程序在运行期使用/分发下列第三方组件，各自保留其原始许可与版权声明。

## 运行期依赖（Python）

| 组件 | 版本 | 许可 | 用途 | 项目主页 |
|------|------|------|------|----------|
| python-docx | 1.2.0 | MIT | 生成 Word 成品（阶段 7） | https://github.com/python-openxml/python-docx |
| lxml | 6.1.2 | BSD-3-Clause | python-docx 的底层 XML 引擎 | https://lxml.de/ |
| PyYAML | 6.0.2 | MIT | 读取 `config/*.yaml` | https://pyyaml.org/ |
| typing_extensions | 4.16.0 | PSF-2.0 | python-docx 运行期依赖 | https://github.com/python/typing_extensions |

完整锁定版本见 `requirements.txt`。

## 桌面控制台（Node / Electron）

| 组件 | 版本 | 许可 | 用途 |
|------|------|------|------|
| Electron | 28.3.3 | MIT | 桌面外壳（Chromium + Node） |
| Vue | 3.5.40 | MIT | 控制台前端框架 |
| Vite | 5.4.21 | MIT | 前端构建 |
| electron-builder | 26.x | MIT | 打包（NSIS / portable） |
| electron-updater | 6.x | MIT | 自动更新 |
| d3-force / d3-drag / d3-scale / d3-selection / d3-zoom | 3.x / 4.x | ISC | 关系图力导向布局 |

完整锁定版本见 `console/package.json`。

## 不使用的内容

- 本项目**不分发**任何模型权重或推理框架：所有 LLM 调用都通过用户自己配置的
  OpenAI 兼容 API 完成（默认见 `config/system.yaml`），密钥只存在于用户本机 `.env`。
- 运行时工作区（`data/`、`history/`、`output/`、`materials/`）中的内容属于用户本人创作，
  不在开源许可覆盖范围内。

## 免责声明

本软件按「原样」提供，不附带任何明示或暗示的担保。使用本软件生成的文本内容（小说、设定等）
由使用者自行负责；调用第三方模型 API 产生的费用与合规责任亦由使用者承担。
