# NovelForge 打包方案 — v0.2.0

> 撰写时间：2026-09-16
> 依据：产物实测 + SiTian/方寸-desktop 对比 + skill `electron-python-app-packaging`

---

## 一、现状体检（v0.1.0 = 空壳，已确诊）

### 1.1 产物清单

```
console/dist/win-unpacked/
├── 绒花墨坊.exe              169 MB      ← Electron 主程序
├── ffmpeg.dll / libEGL.dll …  ~80 MB      ← 多媒体 / GPU 动态库
├── locales/                   ~10 MB      ← 国际化
└── resources/
    ├── app.asar               16 MB       ← 壳代码
    ├── app-update.yml
    └── elevate.exe
```

**`resources/` 里只有壳，没有运行时、没有业务负载。** 用户装完双击 → `ROOT/.venv/Scripts/pythonw.exe` 不存在 → `existsSync` 守卫静默 return → UI 显示「离线」。

### 1.2 当前启动链路（源码态）

```
启动绒花墨坊.bat
  └─> cd console/
  └─> electron.exe . --disable-gpu
        └─> console/main/index.js
              ├─> ROOT = path.resolve(__dirname, "..", "..")
              │        开发态 = 项目根
              │        打包态 = resources/../..  → 不存在 .venv
              ├─> PY  = ROOT/.venv/Scripts/pythonw.exe
              ├─> spawn(PY, [scripts/nf_api.py, --port, 8765])
              └─> waitForApi() → fetch /health
```

### 1.3 后端脚本假设代码目录即数据根

```python
# scripts/nf_api.py:118
ROOT = Path(__file__).resolve().parents[1]   # scripts/ → 项目根
os.chdir(ROOT)                               # 工作目录改到项目根
```

装到 `Program Files` 后，用户数据（data/、logs/、output/）也写在安装目录里，而 Program Files 非管理员不可写 → 崩溃。

### 1.4 各组件体积（开发态）

| 组件 | 体积 | 说明 |
|------|------|------|
| Electron 壳（已打包） | ~77 MB 安装包 | v0.1.0 产物 |
| .venv | 13 MB | python-docx / PyYAML / lxml 等 |
| scripts/ + scripts/utils/ | 2 MB | 含 orchestrator 全链 |
| prompts/ | ~100 KB | 19 个 LLM 模板 |
| templates/ | ~30 KB | Word 模板 |
| config/ | ~10 KB | system / project / templates_rosa |
| **运行时 + 负载总增量** | **~15 MB** | 壳已很大，增量可接受 |

### 1.5 方寸-desktop 对比

- 方寸-desktop 有 `release/方寸 Setup 0.1.0.exe` → `electron-builder` 工具链没问题
- 但它纯 Electron，**没有本地 Python 服务的包袱** → 不能直接抄
- SiTian 同理，纯前端 + 文件读取

---

## 二、推荐路线：A3 官方 embeddable Python + 双轨路径

**为什么选 A3：**
- 不依赖 PyInstaller 的 `datas/hiddenimports/pathex` 猜谜
- 与开发期代码路径一致（就是 Python + pip 装依赖）
- 壳已 70-80MB，运行时只多 ~30MB，**不要为省 20-30 MB 折腾冻结方案**

| 路线 | 可行性 | 失败机制 |
|------|--------|----------|
| **A3 官方 embeddable Python**（推荐） | ✓ | 需处理 `python311._pth` |
| A2 PyInstaller | △ | lxml / python-docx 等带扩展模块的包容易漏 hiddenimports |
| A1 复制整个 `.venv` | ✗ | Windows venv 不可重定位：`pyvenv.cfg` 指回 base、`Scripts/*.exe` shim 内嵌绝对路径 |
| B 用户自备 Python | ✗ | 用户机器 Python 版本/依赖发散即崩 |

---

## 三、执行计划

### P0：让产物能跑（预估 4-6 小时）

#### 3.1 写运行时准备脚本 `console/scripts/prepare-runtime.js`

```js
// 1. 下载 python-3.11-embed-amd64.zip（固定版本）
// 2. 解压到 <console>/runtime/python
// 3. get-pip.py 装 pip
// 4. runtime/python/python.exe -m pip install -r ../../requirements.txt
// 5. 修 python311._pth：加入 Lib\site-packages
// 6. 冒烟：runtime/python/python.exe ../../scripts/nf_api.py --port 8799 --allow-fake → /health 200
```

#### 3.2 改 `console/package.json` build.extraResources

```json
"extraResources": [
  {"from": "runtime", "to": "runtime"},
  {"from": "../scripts", "to": "payload/scripts"},
  {"from": "../prompts", "to": "payload/prompts"},
  {"from": "../config", "to": "payload/config"},
  {"from": "../templates", "to": "payload/templates"}
]
```

#### 3.3 改主进程路径双轨（`console/main/index.js`）

```js
const ROOT = app.isPackaged
  ? path.join(process.resourcesPath, "payload")
  : path.resolve(__dirname, "..", "..");
const PY = app.isPackaged
  ? path.join(process.resourcesPath, "runtime", "python", "python.exe")
  : path.join(ROOT, ".venv", "Scripts", "pythonw.exe");
```

spawn 时注入数据根环境变量：

```js
apiProc = spawn(PY, [path.join(ROOT, "scripts", "nf_api.py"), "--port", String(API_PORT)], {
  cwd: ROOT,
  env: { ...process.env, NF_ROOT: workspaceDir },  // ← 关键
  windowsHide: true,
});
```

#### 3.4 改后端支持 NF_ROOT（`scripts/nf_api.py`）

```python
ROOT = Path(os.environ.get("NF_ROOT") or Path(__file__).resolve().parents[1])
# 去掉 os.chdir(ROOT)，改用绝对路径拼接
```

#### 3.5 dist 前 fail-fast 断言

```js
// 加在 electron-builder 之前
const fs = require("fs");
const r = path.join(__dirname, "runtime");
if (!fs.existsSync(r)) { console.error("❌ runtime/ 缺失，中止打包"); process.exit(1); }
```

### P1：数据与程序分离（预估 3-4 小时）

#### 3.6 工作区目录策略

```
安装目录（只读）                     可写数据目录
<Program Files>/绒花墨坊/            %APPDATA%\绒花墨坊\workspace\
├── resources/                      ├── scripts/  (可编辑)
│   ├── app.asar                    ├── prompts/  (可编辑)
│   ├── runtime/  (Python + deps)   ├── config/
│   └── payload/  (种子，只读)      ├── data/
│       ├── scripts/                ├── logs/
│       ├── prompts/                ├── output/
│       ├── config/                 └── materials/
│       └── templates/
└── 绒花墨坊.exe
```

#### 3.7 首启种子逻辑

主进程 `createWindow()` 之前：
1. 检测 `%APPDATA%\绒花墨坊\workspace/` 是否已存在
2. 不存在 → 把 `resources/payload/*` 拷进 workspace（种子）
3. 已存在 → 跳过（用户已修改过，升级不覆盖）

#### 3.8 便携版判断

```js
const isPortable = !!process.env.PORTABLE_EXEC_DIR;
const userDataDir = isPortable
  ? path.join(path.dirname(app.getPath("exe")), "workspace")
  : path.join(app.getPath("appData"), "绒花墨坊", "workspace");
```

### P2：验收与发布（预估 2-3 小时）

#### 3.9 验收清单

| # | 项 | 预期 |
|---|-----|------|
| 1 | 干净环境（无 Python 无 Node）装 → 双击 → 全链跑通 | 通过 |
| 2 | `--allow-fake` 走全链（每个按钮功能正常） | 通过 |
| 3 | 装到 `Program Files` 也能正常写 data/logs/output | 通过 |
| 4 | 卸载 → `%APPDATA%\绒花墨坊\workspace\` 数据仍在 | 通过 |
| 5 | 重装 → 数据能接着用 | 通过 |
| 6 | 全程无黑窗 / 控制台闪烁 | 通过 |
| 7 | `git status` 干净（产物全在 .gitignore） | 通过 |
| 8 | 安装包体积 ~110 MB（壳 77 + 运行时 ~30） | 通过 |

#### 3.10 发布

- 版本号单一来源：`console/package.json`
- `latest.yml` url 必须与安装包文件名一致（否则自动更新 404）
- 发布前 `taskkill /IM 绒花墨坊.exe` 清文件锁
- Release Notes 写明「v0.1.0 空壳作废，需手动换包」

---

## 四、反模式（历史教训）

| 反模式 | 后果 | 本方案对策 |
|--------|------|------------|
| 多条打包线路并存 | 没有单一事实源 | 只有 `prepare-runtime.js` + `electron-builder` 一条 |
| `datas=[]` / 漏 hiddenimports | 资源没带 → 空壳 | fail-fast 断言 |
| 后端 `os.chdir(ROOT)` | 安装目录不可写 | `NF_ROOT` env 覆盖 |
| 拿「打包成功」当交付 | 用户装了跑不起来 | 干净环境验收 |
| 自动更新救不了坏包 | 坏包没进程去检查更新 | Release Notes 写明手动换包 |

---

## 五、关键问题（请暮雨确认）

1. **embeddable Python 版本**：当前 .venv 是 Python 3.11，embeddable 也用 3.11。是否保持一致？
2. **书名冲突**：`config/project.yaml` 的书名由用户在 GUI 里填写（新建项目向导）。安装包是通用壳还是按书名定制？建议做**通用壳**，首启时让用户选工作目录或新建项目。
3. **升级包大小**：壳 77 MB + 运行时 ~30 MB = ~110 MB 安装包。是否接受？（后续 delta 更新只传 blockmap 差量 ~几 MB）
