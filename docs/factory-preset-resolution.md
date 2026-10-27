# 出厂预设的源定位与归档回退（2026-10-25 复盘）

本文记两件事：**预设源为什么不能只认环境变量**，以及**归档失败时的一个删数据缺口**。
两件都是"只在装机态/失败态才暴露"的缺陷，源码态与正常路径测不出来。

## 一、预设源定位：环境变量只覆盖一条启动路径

`NF_SAMPLE_ROOT` 由 Electron 主进程在 spawn nf_api 时注入
（`console/main/index.js`: `process.resourcesPath/payload/examples`）。
这条注入**只对那一条启动路径有效**。同一份代码在下面这些进程里跑时完全没有它：

- 终端里手工 `python scripts/preset_apply.py …`（排障 / 外部 Agent / 用户）；
- MCP 垫片自启动代拉的 nf_api（`nf_mcp_stdio_bridge._spawn_nf_api`）；
- 计划任务、快捷方式、任何第三方壳。

实测形态（同一台装机环境同时存在两种结果）：

| 入口 | 结果 |
|---|---|
| 活的 nf_api（Electron 起的），`GET /factory/presets` | 正常返回 4 套预设 |
| 终端里同一函数 `preset_apply.find_presets_dir` | 打印「找不到 examples/presets/」 |

结论：**把整条预设能力押在一个环境变量上，等于让同一个功能一半可用一半不行。**
装机态下 `root/examples` 与 `DEFAULT_ROOT/examples` 双落空（工作区里没有 `examples/`），
env 一断就全断 —— 而这个断点不会出现在任何日志里，只表现成一句"找不到目录"。

### 修法：候选表里加一条不依赖运行环境的位置判据

`utils/app_paths.payload_example_dirs()` 从 `sys.executable` 的祖先链上反推：

```
<安装目录>/resources/runtime/python/python.exe      ← sys.executable
<安装目录>/resources/payload/examples/              ← 目标（往上第 3 层）
```

`payload/examples` 与 `resources/payload/examples` 两种拼法都试（起点不同差一层，
不自作聪明猜）。源码态（`<repo>/.venv/Scripts/python.exe`、工作区的 `scripts/` 副本）
祖先链上没有 `payload/`，所以该函数在源码态**恒空**，抢不走 `root/examples` 的优先级 ——
这条由 `tests/unit/test_preset_resolve.py` 第 [8] 节钉住。

挂载点三处（同一条链的三个消费者，断一处就只修一半）：

- `preset_apply.find_presets_dir`（铺设 / 列预设）
- `nf_api_domains.factory.resolve_sample_root`（出厂盘点 / 清理）
- `utils.factory_reset._sample_dir`（一键还原的出厂样例源）

诊断口径：`GET /factory/project` 的 `source` 字段直接给出**解析结果**，
不用猜是哪个候选命中（`describe()` 已是只读）。

## 二、归档失败的回退缺口：留 dest，不删 dest

`switch_book.archive()` 的成功路径是把 `config/`、`materials/` 搬进书档。
失败路径原本是：

```python
try:
    _restore_root_items(dest)      # 把 config/ materials/ 并回工作区
except Exception:
    pass                           # ← 回退自己的失败被吞掉
if tmp_dest and dest.exists():
    shutil.rmtree(dest)            # ← 把还装着用户素材的 dest 删了
```

即：**回退失败 + 同名旧归档存在**时，`materials/` 已被搬进 `dest`，`_restore_root_items`
半路抛错，随后 `rmtree(dest)` 把素材连同书档一起删掉，而返回消息仍是
「归档失败（已尽力回退）」—— 一句话把用户素材的消失说成了"尽力了"。

现改为：回退失败就**保留** `dest` 不删，并明确报出它的路径与两个错误原因
（归档错误 + 回退错误）。「归档失败」不该以「素材消失」收场。

## 三、仍未定性的一环（勿当成已修）

2026-10-25 10:57:58 的一次「一键还原」之后，工作区处于
`materials/raw` 0 张卡 + `data/` 已清的中间态。已确认的事实：

- 还原动手前的快照含 8 章正文、大纲、设定集、`progress.json`（**数据都在，可回退**）；
- 还原时的 6 张素材卡被它自己的 `data/state/preset_backup/<时间戳>/` 完好备份
  （已按此逐字节校验后补回工作区）；
- `config/project.yaml` 的 mtime 是 10:57:58 = 还原**确实走到了配置写回那一步**，
  所以它不是因为"找不到预设目录"失败的（那句话来自另一次终端复现）。

**因此本次没有把"环境变量没注入"当作病根** —— 该假设已被 `GET /factory/presets`
的活体结果否证。第二节的回退加固是对"同类会删用户数据"的防守，**不是**对这次事故的定性；
10:58:10 那次空快照（`preset_apply` / `before_switch_book.archive` 各一份、均空）
究竟死于哪一步，仍待现场日志或下一次复现来钉死。
