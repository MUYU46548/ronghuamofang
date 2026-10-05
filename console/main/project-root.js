// 项目根显式解析（2026-10-04 修复单 Step 4c 根修 —— 主进程侧，零渲染层变更）
//
// 为什么要有：主进程此前对 nf_api 的 ROOT 是「隐式默认」—— getWorkspaceDir()
// 按打包态算出 %APPDATA% 工作区后**无条件**传 --root，开发机跑安装版 GUI 时
// GUI 读的项目与 orchestrator 所在源码树**静默分裂**（双 progress.json、
// 审批门不可见、GUI 批准落空 —— 2026-10-04 试写事故的 P0 病根）。
//
// 现在项目根必须**显式**决定，优先级与「记住」语义：
//   1. --project-root <path> 启动参数（本次运行生效，并写入配置文件记住）
//   2. NF_ROOT 环境变量（本次运行覆盖，**不**写入配置 —— 保留为覆盖手段）
//   3. 配置文件（此前记住的项目根；%APPDATA%\绒花墨坊\project-root.json）
//   4. 默认工作区（打包态 %APPDATA% 工作区 / 源码态仓库根）
//
// **无静默回退**：每次解析都打印 root + 来源（source=cli|env|config|default）；
// 显式配置失效（目录不存在 / 缺 config/system.yaml）时告警后才落下一优先级。
// 纯函数 + 注入 fs —— 不依赖 Electron，可被 node 直接单测。
const path = require("path");
const fs = require("fs");

function configFileFor(appDataDir) {
  /** 项目根配置文件位置（与默认工作区同目录族，两种态下都稳定）。 */
  return path.join(appDataDir, "绒花墨坊", "project-root.json");
}

function looksLikeProject(p, fsImpl) {
  /** 项目根的最低合法性：存在且带 config/system.yaml（缺它 GUI 也起不来）。 */
  try {
    return !!p && fsImpl.existsSync(path.join(p, "config", "system.yaml"));
  } catch (e) {
    return false;
  }
}

function resolveProjectRoot(opts) {
  /**
   * 解析项目根。返回 { root, source }，source ∈ cli|env|config|default。
   * 显式配置（cli/env/config）无效时告警并落下一优先级；默认值兜底必达。
   */
  const {
    cliRoot = "",       // --project-root 启动参数值
    envRoot = "",       // NF_ROOT 环境变量值
    configFile = "",    // 记忆配置文件绝对路径
    defaultDir = "",    // 默认工作区（旧 getWorkspaceDir 逻辑的产出）
    fsImpl = fs,
    log = () => {},
    warn = () => {},
  } = opts || {};

  const tryCandidate = (raw, source) => {
    if (!raw) return null;
    const abs = path.resolve(String(raw));
    if (!looksLikeProject(abs, fsImpl)) {
      warn("[console] projectRoot(" + source + ") 无效（缺 config/system.yaml）: "
           + abs + " → 继续下一优先级");
      return null;
    }
    return { root: abs, source: source };
  };

  let hit = tryCandidate(cliRoot, "cli") || tryCandidate(envRoot, "env");
  if (!hit && configFile) {
    let saved = "";
    try {
      const j = JSON.parse(fsImpl.readFileSync(configFile, "utf8"));
      saved = (j && j.projectRoot) || "";
    } catch (e) {
      // 无文件 / 坏 JSON = 「尚未记住」，属正常缺省，不是显式配置失效
      saved = "";
    }
    hit = tryCandidate(saved, "config");
  }
  if (!hit) {
    if (!defaultDir) throw new Error("resolveProjectRoot: defaultDir 必填");
    hit = { root: path.resolve(defaultDir), source: "default" };
  }
  log("[console] projectRoot: " + hit.root + " (source=" + hit.source + ")");
  return hit;
}

function persistRoot(configFile, root, fsImpl) {
  /** 把 --project-root 指定的根写进配置文件（「启动参数记住项目根」）。 */
  const f = fsImpl || fs;
  try {
    f.mkdirSync(path.dirname(configFile), { recursive: true });
    f.writeFileSync(configFile, JSON.stringify({
      projectRoot: root,
      updatedAt: new Date().toISOString(),
    }, null, 2));
    return true;
  } catch (e) {
    return false;
  }
}

module.exports = { configFileFor, looksLikeProject, resolveProjectRoot, persistRoot };
