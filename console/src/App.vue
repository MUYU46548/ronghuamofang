<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import { diffParagraphs } from "./diff.js";
import ReviewConsole from "./ReviewConsole.vue";
import SandboxQueue from "./SandboxQueue.vue";
import ParagraphRefinePanel from "./ParagraphRefinePanel.vue";
import OutlineView from "./OutlineView.vue";
import ScrapsPanel from "./ScrapsPanel.vue";
import RoleGraph from "./RoleGraph.vue";
import ChapterBlueprint from "./ChapterBlueprint.vue";
import ProofreadPanel from "./ProofreadPanel.vue";
import StylePanel from "./StylePanel.vue";
import CommandPalette from "./CommandPalette.vue";
import AboutDialog from "./AboutDialog.vue";
import DisclaimerDialog from "./DisclaimerDialog.vue";
import { hasAckedDisclaimer, ackedDisclaimerAt } from "./disclaimer.js";
import NewProjectWizard from "./NewProjectWizard.vue";
import QualityTrend from "./QualityTrend.vue";

// 审批门通知偏好
const GATE_NOTIFY_KEY = "mofang_gate_notify";
function gateNotifyEnabled() {
  return localStorage.getItem(GATE_NOTIFY_KEY) === "1";
}
function setGateNotify(on) {
  localStorage.setItem(GATE_NOTIFY_KEY, on ? "1" : "0");
}

import { API } from "./apiBase.js";

// 素材页签内的子视图：结构化卡片（materials/raw） / 原始碎片（materials/original_scraps）
const materialSub = ref("cards");

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json", "X-Mofang-Source": "gui" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  return { status: r.status, data };
}

const tab = ref("pipeline");
const state = ref(null);
const models = ref(null);
const modelOptions = ref([]); // 动态从 /models/available 加载（**全量**，供搜索框计数）
// 模型下拉**按角色所在的 provider 过滤**。此前只取 providers 里的「第一个」的
// available_models，于是**新增一个供应商后，它的模型根本不进下拉** ——
// 换模型时表现为「明明加了 provider 却选不到模型」（2026-10-01 修）。
const modelsByProvider = ref({});   // {providerId: [模型名]}
const manualModels = ref([]);       // 手动添加的模型（全局可见，任何 provider 的下拉都列出）
const costs = ref([]);
const costSummary = ref(null);
const costView = ref("cost");   // "cost" | "usage" | "rates"
const rates = ref({});           // 定价表（合并视图）
const newRateName = ref("");     // 新增模型名称输入
const importOpen = ref(false);   // 定价批量导入面板展开
const importText = ref("");      // 粘贴的定价原文
const importReport = ref(null);  // 解析预览结果（含 plan/errors/warnings）
const importBusy = ref(false);
const providers = ref(null);    // provider status (no keys exposed)
const online = ref(false);
const toast = ref("");
const lastJob = ref(null);      // { state, result }
const updateStatus = ref("等待检查");
const updateReady = ref(false);
const updateProgress = ref(0);
const checkingUpdate = ref(false);
const updaterInitialized = ref(false);
const updaterDev = ref(false);
const modelSource = ref("config"); // "config" | "fetched"
const fetchingModels = ref(false);
const budgetPaused = ref(false);
const circuitBreakerShow = ref(false);
const settingEditOpen = ref(false);
const settingEditDisclaimer = ref(false);
let timer = null;
let toastTimer = null;
let updaterHandler = null;
let updaterCleanup = null;

function say(msg) {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 4500);
}

async function refresh() {
  try {
    const [s, m, j] = await Promise.all([
      api("/state"), api("/models"),
      state.value && state.value.current_job ? api("/jobs/" + state.value.current_job) : Promise.resolve(null),
    ]);
    if (s.status === 200) {
      state.value = s.data; online.value = true; budgetPaused.value = !!s.data?.budget?.paused;
      agentMode.value = !!s.data?.agent_mode;  // 同步 Agent 模式开关状态
      noteJobTransition(s.data);
    }
    if (m.status === 200) models.value = m.data;
    if (j && j.status === 200) lastJob.value = j.data;
    // 成本页可见时同步刷新「当前运行」实时花费（与 state 同频 2.5s）
    if (tab.value === "cost") await refreshStreamingCost();
  } catch (e) {
    online.value = false;
  }
}

async function globalRefresh() {
  // 全局刷新：同时刷新所有页签数据
  await Promise.all([
    refresh(),
    refreshCosts(),
    refreshModels(),
    refreshSandboxBadge(),
    refreshAppearances(),
    refreshAgentRuns(),
  ]);
  say('已全局刷新');
}

async function refreshCosts() {
  const [list, summary] = await Promise.all([
    api("/costs"),
    api("/costs/summary"),
  ]);
  if (list.status === 200) costs.value = list.data.entries || [];
  if (summary.status === 200) costSummary.value = summary.data;
}

const agentRuns = ref([]);
async function refreshAgentRuns() {
  if (!agentMode.value) { agentRuns.value = []; return; }
  const r = await api("/agent/runs");
  if (r.status === 200) agentRuns.value = r.data.runs || [];
}

const remedyResult = ref(null);
const remedyOpen = ref(false);
async function runRemedy() {
  const r = await api("/remedy");
  if (r.status === 200) {
    remedyResult.value = r.data;
    remedyOpen.value = true;
  } else {
    say("补救检测失败: " + (r.data?.error || r.status));
  }
}

/* ---------- 定价编辑器 ---------- */
async function loadRates() {
  const r = await api("/costs/rates");
  if (r.status === 200 && r.data.rates) {
    rates.value = r.data.rates;
  }
}

async function saveRates() {
  // 构造要保存的自定义定价（仅包含有 in/out 的条目）
  const toSave = {};
  for (const [name, rate] of Object.entries(rates.value)) {
    if (rate.in != null && rate.out != null && name) {
      toSave[name] = {
        in: Number(rate.in) || 0,
        out: Number(rate.out) || 0,
        cache_read: Number(rate.cache_read) || 0,
      };
    }
  }
  const r = await api("/costs/rates", "POST", { rates: toSave });
  if (r.status === 200) {
    say("定价已保存（" + r.data.saved + " 条）");
    loadRates();
  } else {
    say("保存失败: " + (r.data.error || r.status));
  }
}

function addRate() {
  const name = newRateName.value.trim();
  if (!name) return;
  rates.value[name] = { name, in: 1.0, out: 4.0, cache_read: 0, source: "custom" };
  newRateName.value = "";
}

function removeRate(name) {
  delete rates.value[name];
}

/* ---------- 定价批量导入（两段式：预览 → 确认） ---------- */
function actionLabel(a) {
  return { new: "新增", override: "覆盖源码价", update: "更新自定义" }[a] || a;
}
function fmtRate(r) {
  if (!r) return "—";
  const f = (x) => (x == null ? "—" : x);
  return "in " + f(r.in) + " / out " + f(r.out) + " / cache " + f(r.cache_read);
}
function toggleImport() {
  importOpen.value = !importOpen.value;
  if (importOpen.value) importReport.value = null;
}
async function previewImport() {
  const t = (importText.value || "").trim();
  if (!t) { say("请先粘贴定价内容"); return; }
  importBusy.value = true;
  const r = await api("/costs/rates/import", "POST", { text: t, confirm: false });
  importBusy.value = false;
  importReport.value = r.data || null;
  if (r.status !== 200) say("解析失败: " + ((r.data && r.data.error) || r.status));
}
async function confirmImport() {
  const rep = importReport.value;
  if (!rep || !rep.ok) { say("请先解析预览，且确认无解析错误"); return; }
  const t = (importText.value || "").trim();
  importBusy.value = true;
  const r = await api("/costs/rates/import", "POST", { text: t, confirm: true });
  importBusy.value = false;
  importReport.value = r.data || null;
  if (r.status === 200) {
    say("已导入 " + r.data.saved + " 条定价（自定义共 " + r.data.custom_total + " 条）");
    importText.value = "";
    loadRates();
  } else {
    say("导入失败: " + ((r.data && r.data.error) || r.status));
  }
}

function switchToRefine(chapterNo) {
  pendingRefineChapter.value = chapterNo;
  switchTab('refine');
}

function switchTab(t) {
  tab.value = t;
  if (t === "inbox") cameFromInbox.value = false;   // 回到收件箱即清掉"来路"标记
  if (t === "cost") {
    if (costs.value.length === 0) refreshCosts();
    refreshStreamingCost();
  }
  if (t === "project") loadProjects();
  if (t === "outline" && outlineRef.value) outlineRef.value.load(false);
  if (t === "sandbox") {
    // 每次进来自查一次：产物可能在别处刚写完（徽标可能已过时）
    refreshSandboxBadge();
    if (sandboxRef.value) sandboxRef.value.load(false);
  }
  if (t === "settings") {
    if (!providers.value) refreshModels();
    if (!promptFiles.value.length) loadPromptList();
    if (!styleNotesLoaded.value) loadStyleNotes();
    loadTokenLimit();          // 止烧阈值：每次进设置页都重读（可能在别处改过）
    loadAgentConnect();        // 接入其他 Agent：路径/MCP 状态每次重探（垫片可能刚代拉）
    loadKickoff();             // 启动提示词：项目根可能变，每次重读替换占位符
    loadDebugInfo();           // 诊断与调试：健康/路径/日志位置每次重探
  }
  if (t === "materials") loadMaterials();
  if (t === "chapters" && !chaptersLoaded.value) loadChapters();
  if (t === "story") loadSetting();
  if (t === "outline_chapters") loadOutlineChapters();
  if (t === "export") refreshExportState();
}

/* ---------- 外观配置 ---------- */
const theme = ref(localStorage.getItem("mofang_theme") || "purple");
const fontSize = ref(parseInt(localStorage.getItem("mofang_font_size") || "14"));

const THEMES = [
  { id: "purple", name: "雾灰紫", color: "#9b8fc4" },
  { id: "dark", name: "暗夜紫", color: "#7c83d4" },
  { id: "night-blue", name: "暗夜蓝", color: "#5a8fd4" },
  { id: "night-green", name: "暗夜绿", color: "#5cb88a" },
  { id: "night-warm", name: "暗夜暖", color: "#b8a08a" },
  { id: "pink", name: "樱粉", color: "#d48ca0" },
  { id: "blue", name: "天蓝", color: "#7dadde" },
  { id: "green", name: "翠绿", color: "#8ab89a" },
  { id: "orange", name: "暖橙", color: "#e09950" },
  { id: "gray", name: "灰白", color: "#9a9aa4" },
];

// 暗色主题清单：CSS 里所有 `.is-dark ...` 的公共覆盖都靠它生效。
// 新增暗色主题时**必须**同时加到这里，否则新主题会像当年的 night-* 一样裸奔
// （顶栏白半透明、输入框写死白底 → 部分界面看不清）。
const DARK_THEMES = ["dark", "night-blue", "night-green", "night-warm"];

function setTheme(t) {
  theme.value = t;
  localStorage.setItem("mofang_theme", t);
  document.documentElement.className =
    `theme-${t}` + (DARK_THEMES.includes(t) ? " is-dark" : "");
}

function setFontSize(px) {
  fontSize.value = px;
  localStorage.setItem("mofang_font_size", px.toString());
  document.documentElement.style.setProperty("--app-font-size", px + "px");
}

// 初始化外观
setTheme(theme.value);
setFontSize(fontSize.value);

/* ---------- 退出确认（2026-10-03 重做：不再用 window.beforeunload） ----------
 *
 * 旧实现把确认挂在 `window.beforeunload` 上，触发条件包含「有 stage 是 done 但未审批」
 * —— 那是审批门的**长期正常状态**（可持续数天）。于是每次关窗都弹 Chromium **原生**确认框：
 * 它无法自绘、可能跑到窗口后面、或被 Esc/取消吃掉 → 用户看到的就是「点了 X 没反应、
 * 关不掉、原因不明」，还会连带卡住「重启安装」。
 *
 * 现在按项目既有 GUI 纪律（破坏性操作走**应用内**确认框，不用原生对话框）改成：
 *   ① 渲染进程只 `setQuitBusy(hasUnfinishedJob)` 上报状态；
 *   ② 主进程在关窗时按需 `app:close-requested` 问过来；
 *   ③ 这里弹应用内对话框（复用「跳过阶段」那套抽屉 + 勾选护栏）；
 *   ④ 主进程有 5s 看门狗：本进程若挂了/没应答，它会强制退出，**绝不把用户关在应用里**。
 */
const hasUnfinishedJob = computed(() => {
  if (!state.value) return false;
  // 当前有运行中的 job，或存在未完成/待审批的 stage
  if (state.value.current_job) return true;
  const stages = state.value.stages || [];
  return stages.some((s) => s.status === "running" || (s.status === "done" && !s.approved));
});
const quitDlg = ref({ open: false, agreed: false });

function setupExitGuard() {
  // ① 上报 busy（状态一变就推给主进程，主进程据此决定关窗要不要拦）
  watch(hasUnfinishedJob, (v) => {
    try { window.mofangAPI?.setQuitBusy?.(!!v); } catch (e) { /* 非 Electron 环境忽略 */ }
  }, { immediate: true });
  // ② 主进程问「确认退出吗」→ 弹应用内对话框（忙碌时才问；不忙主进程会直接放行）
  try {
    window.mofangAPI?.onCloseRequested?.(() => {
      if (!hasUnfinishedJob.value) { window.mofangAPI.confirmQuit(); return; }
      quitDlg.value = { open: true, agreed: false };
    });
  } catch (e) { /* 非 Electron 环境忽略 */ }
}

function cancelQuit() {
  quitDlg.value = { open: false, agreed: false };
  try { window.mofangAPI?.cancelQuit?.(); } catch (e) { /* ignore */ }
}

function confirmQuit() {
  if (!quitDlg.value.agreed) return;
  try { window.mofangAPI?.confirmQuit?.(); } catch (e) { /* ignore */ }
}

/* ---------- 导出（P3 多平台发布） ---------- */
const exportCfg = ref({ format: "both", volSize: 5, includeFrontmatter: true });
const exporting = ref(false);
const exportMsg = ref("");
const exportHistory = ref([]);

async function runExport() {
  if (exporting.value) return;
  if (!window.confirm(`确定导出？\n\n格式: ${exportCfg.value.format}\n每卷 ${exportCfg.value.volSize} 章`)) return;
  exporting.value = true;
  exportMsg.value = "";
  try {
    const r = await api("/export/markdown", "POST", {
      per_vol: exportCfg.value.volSize,
      book_name: null,
    });
    if (r.status === 200 && r.data.ok) {
      exportMsg.value = r.data.message;
      exportHistory.value.unshift({
        time: new Date().toLocaleString("zh-CN"),
        volumes: "",
        chapters: "",
        format: exportCfg.value.format,
        path: r.data.path,
      });
      say("导出完成");
    } else {
      exportMsg.value = "导出失败: " + (r.data.error || r.data.message);
    }
  } catch (e) {
    exportMsg.value = "导出错误: " + (e.message || e);
  } finally {
    exporting.value = false;
  }
}

async function refreshExportState() {}

function openExportDir(path) {
  if (window.mofangAPI?.openArtifact) {
    window.mofangAPI.openArtifact(path);
  } else {
    say("请在 Electron 中使用此功能");
  }
}

/* ---------- 章节大纲（B2） ---------- */
const outlineChapters = ref([]);
const outlineChapterContent = ref(null);
const outlineChapterN = ref(null);

async function loadOutlineChapters() {
  const r = await api("/outline/chapters/list");
  if (r.status === 200) outlineChapters.value = r.data.chapters || [];
}

async function loadOutlineChapter(n) {
  const r = await api(`/outline/chapters/get?n=${n}`);
  if (r.status === 200 && r.data.ok) {
    outlineChapterN.value = r.data.n;
    outlineChapterContent.value = r.data.content;
  } else {
    say("加载失败: " + (r.data.error || r.status));
  }
}

/* ---------- Story Bible（B1） ---------- */
const settingTab = ref("characters"); // characters | world | plot | timeline
const settingData = ref(null);
const settingLoaded = ref(false);
const settingDirty = ref(false);

async function loadSetting() {
  if (settingLoaded.value) return;
  const r = await api("/setting/current");
  if (r.status === 200 && r.data.ok) {
    settingData.value = r.data.setting;
    settingLoaded.value = true;
  } else {
    say("加载失败: " + (r.data.error || r.status));
  }
}

const settingEditor = ref({ open: false, key: '', idx: -1, item: null });

function openSettingEditor(key, idx) {
  const item = settingData.value?.[key]?.[idx];
  if (!item) return;
  const copy = { ...item };
  // 初始化标签输入框（数组 → 逗号分隔字符串）
  if (key === 'characters' || key === 'world') {
    const tags = copy.tags;
    copy._tagsInput = Array.isArray(tags) ? tags.join(',') : (tags || '');
  }
  settingEditor.value = { open: true, key, idx, item: copy };
}

function closeSettingEditor() {
  const { key, idx, item } = settingEditor.value;
  if (key && idx >= 0 && item && settingData.value?.[key]) {
    // 保存前将 _tagsInput 转回数组
    if (key === 'characters' || key === 'world') {
      const tagsStr = (item._tagsInput || '').trim();
      item.tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(Boolean) : [];
      delete item._tagsInput;
    }
    settingData.value[key][idx] = { ...item };
  }
  settingEditor.value = { open: false, key: '', idx: -1, item: null };
}

function addSettingItem(key) {
  if (!settingData.value) return;
  if (key === 'concepts') {
    if (!settingData.value.world) settingData.value.world = { concepts: [], all: [], locations: [], factions: [] };
    if (!settingData.value.world.concepts) settingData.value.world.concepts = [];
    settingData.value.world.concepts.push({ name: "新概念", type: "", tags: [], snippet: "", _tagsInput: "" });
    settingDirty.value = true;
    return;
  }
  if (!settingData.value[key]) settingData.value[key] = [];
  if (key === 'characters') {
    settingData.value[key].push({ name: "新角色", type: "", tags: [], snippet: "", locked: false, _tagsInput: "" });
  } else if (key === 'world') {
    settingData.value[key].push({ name: "新条目", type: "", tags: [], snippet: "", _tagsInput: "" });
  } else {
    settingData.value[key].push({ name: "新条目", description: "" });
  }
  settingDirty.value = true;
}

function removeSettingItem(key, idx) {
  if (!settingData.value?.[key]) return;
  if (!window.confirm(`确定删除 ${settingData.value[key][idx]?.name || "该条目"}？`)) return;
  settingData.value[key].splice(idx, 1);
  settingDirty.value = true;
}

async function saveSetting() {
  if (!settingData.value) return;
  if (!window.confirm("确定保存 Story Bible？\n\n修改将写入 data/setting/setting.json（自动备份）。")) return;
  const r = await api("/setting/save", "POST", { setting: settingData.value });
  if (r.status === 200 && r.data.ok) {
    settingDirty.value = false;
    say("已保存");
  } else {
    say("保存失败: " + (r.data.error || r.status));
  }
}

function settingItems(key) {
  if (!settingData.value) return [];
  if (key === 'concepts') {
    return settingData.value.world?.concepts || [];
  }
  if (key === 'world') {
    return settingData.value.world?.all || [];
  }
  return settingData.value[key] || [];
}

function countItems(key) {
  return settingItems(key).length;
}

/* ---------- 角色关系图（方向2） ---------- */
// appearances 缺失不阻塞：降级为均一节点大小（RoleGraph 内部处理 null）
const appearances = ref(null);
const aliasMap = ref({});
const graphLoading = ref(false);
const graphHint = ref("");

async function loadAppearances() {
  graphLoading.value = true;
  try {
    const r = await api("/setting/appearances");
    if (r.status === 200 && r.data.ok) {
      appearances.value = r.data;
      aliasMap.value = r.data.alias || {};
      graphHint.value = "";
    } else {
      appearances.value = null;
      aliasMap.value = {};
      graphHint.value = r.data?.hint || ("出场数据加载失败 " + r.status);
    }
  } catch (e) {
    appearances.value = null;
    graphHint.value = "出场数据加载失败：" + (e.message || e);
  } finally {
    graphLoading.value = false;
  }
}

async function openSettingTab(k) {
  settingTab.value = k;
  if (k === "relation" && !appearances.value) await loadAppearances();
}

async function refreshAppearances() {
  const r = await api("/appearances/refresh", "POST", {});
  if (r.status !== 202 || !r.data.job_id) {
    say("重算失败: " + (r.data?.error || r.status));
    return;
  }
  const jid = r.data.job_id;
  say("正在重算出场统计…");
  for (let i = 0; i < 60; i++) {
    await new Promise((res) => setTimeout(res, 500));
    const j = await api("/jobs/" + jid);
    if (j.status === 200 && (j.data.state === "ok" || j.data.state === "failed")) {
      if (j.data.state === "ok") {
        say("出场统计已更新");
        await loadAppearances();
      } else {
        say("重算失败: " + (j.data.result || ""));
      }
      return;
    }
  }
  say("重算超时，请稍后手动刷新");
}

/* ---------- 大纲页签（结构化视图，任务1） ---------- */
/* 从收件箱跳转后要能一步回来：记一个"来路"标记，在内容区顶部显示返回按钮。
   仅当用户确实是从收件箱点进来的才显示，正常浏览其他页签不会被打扰。 */
const cameFromInbox = ref(false);
function gotoFromInbox(t) {
  cameFromInbox.value = true;
  switchTab(t);
}
function backToInbox() {
  cameFromInbox.value = false;
  switchTab("inbox");
}

const outlineRef = ref(null);
const outlineSummary = ref(null);
function onStructureLoaded(s) {
  outlineSummary.value = s;
}

/* ---------- 沙盒审核队列（「审核」页签） ----------
   徽标数 = 待审份数。刻意**独立于页签是否打开**去轮询（节流 60s）：
   沙盒产物是后台产物写入时自动登记的，用户在别的页签干活时也可能新增，
   徽标不刷新就成了「有活等你但你看不见」。
   只在首页加载时拉一次 + 每次切到该页签刷新，避免无谓请求。 */
const sandboxRef = ref(null);
const sandboxPending = ref(0);
async function loadSandboxQueue() {
  if (sandboxRef.value) { sandboxRef.value.load(false); return; }
  await refreshSandboxBadge();
}
/** 审核动作后组件广播 stats → 顶部徽标即时同步（不用再打一次接口）。 */
function onSandboxStats(st) {
  sandboxPending.value = (st && st.pending) || 0;
}

async function refreshSandboxBadge() {
  const r = await api("/sandbox/queue");
  if (r.status !== 200) return;
  sandboxPending.value = (r.data.stats && r.data.stats.pending) || 0;
}
function openSandboxTab() {
  switchTab("sandbox");
  if (sandboxRef.value) sandboxRef.value.load(false);
}

function openOutlineTab() {
  cameFromInbox.value = true;
  switchTab("outline");
  if (outlineRef.value) outlineRef.value.load(false);
}

async function openMultiDraftDlg() {
  const count = parseInt(window.prompt("生成几份方案？（2-5，推荐 3）", "3"), 10);
  if (isNaN(count) || count < 2 || count > 5) return say("取消");
  if (!window.confirm(`确定生成 ${count} 份大纲方案？\n\n生成后可在收件箱对比拼合。`)) return;
  const r = await api("/stage/2/run-multi", "POST", { count });
  if (r.status === 202) {
    say(`已提交多方案生成（${count} 份，job ${r.data.job_id}）`);
    setTimeout(() => openDraftsDlg(count), 100);
  } else {
    say("提交失败: " + (r.data.error || r.status));
  }
  refresh();
}

const drafts = ref([]);
const draftsLoaded = ref(false);
const draftsDlgOpen = ref(false);
async function openDraftsDlg(count) {
  // 轮询 job 直到完成，然后加载 drafts
  const check = async () => {
    const s = await api("/state");
    if (!s.data.current_job) return true;
    await new Promise(r => setTimeout(r, 2000));
    return false;
  };
  // 简单方式：等 8s 再加载
  await new Promise(r => setTimeout(r, 8000));
  const r = await api("/outline/drafts");
  if (r.status === 200) {
    drafts.value = r.data.drafts || [];
    draftsLoaded.value = true;
    draftsDlgOpen.value = true;
  } else {
    say("加载方案失败: " + (r.data.error || r.status));
  }
}

async function openRefineDiffDlg() {
  await loadChapters();
  diffMode.value = true;
  switchTab("chapters");
}

/* ---------- diff 模式（润色逐章对比） ---------- */
const diffMode = ref(false);

/* ---------- 用户风格笔记（config/project.yaml → book.style_notes） ---------- */
const styleNotes = ref("");
const styleNotesDirty = ref(false);
const styleNotesLoaded = ref(false);

async function loadStyleNotes() {
  if (!window.mofangAPI?.styleNotesGet) {
    say("当前为浏览器预览模式，风格笔记需通过 Electron 启动");
    return;
  }
  const r = await window.mofangAPI.styleNotesGet();
  if (!r.ok) {
    say("风格笔记读取失败: " + (r.error || ""));
    return;
  }
  styleNotes.value = r.content || "";
  styleNotesDirty.value = false;
  styleNotesLoaded.value = true;
}

async function saveStyleNotes() {
  if (!window.mofangAPI?.styleNotesSave) return;
  if (!window.confirm(
      "确定保存风格笔记？\n\n"
      + "· 将写入 config/project.yaml 的 book.style_notes\n"
      + "· 旧版自动备份到 config/history/\n"
      + "· 下次运行写作（阶段4）/润色（阶段6）时生效\n"
      + "· 留空则完全不注入，不影响现有逻辑")) return;
  const r = await window.mofangAPI.styleNotesSave(styleNotes.value);
  if (!r.ok) {
    say("风格笔记保存失败: " + (r.error || ""));
    return;
  }
  styleNotesDirty.value = false;
  say("风格笔记" + (r.message || "已保存"));
}

async function loadProjects(quiet = false) {
  const r = await api("/project/list");
  if (r.status === 200) {
    projects.value = r.data;
    if (!quiet) say("项目列表已刷新");
  } else {
    say("加载失败: " + (r.data.error || r.status));
  }
}

async function archiveCurrent() {
  if (!projects.value.current) {
    say("当前无项目可归档");
    return;
  }
  const finalName = archiveName.value.trim() || projects.value.current;
  if (!window.confirm(`确定归档当前项目为「${finalName}」？\n\n归档后当前工作区数据将移至 data/books/${finalName}/，工作区清空。`)) return;
  const r = await api("/project/archive", "POST", { name: finalName, yes: true });
  if (r.status === 200 && r.data.ok) {
    say("已归档: " + finalName);
    archiveName.value = "";
    loadProjects();
    refresh();
  } else {
    say("归档失败: " + (r.data.message || r.data.error || r.status));
  }
}

async function restoreProject(name) {
  if (!window.confirm(`确定恢复项目「${name}」？\n\n当前工作区数据将先自动归档，然后替换为 ${name} 的数据。`)) return;
  const r = await api("/project/restore", "POST", { name, yes: true });
  if (r.status === 200 && r.data.ok) {
    say("已恢复: " + name);
    loadProjects();
    refresh();
  } else {
    say("恢复失败: " + (r.data.message || r.data.error || r.status));
  }
}

/* ---------- 生成前 token / 费用预估确认 ---------- */
const estDialog = ref({ open: false, loading: false, data: null, title: "", confirm: null });
const estSkipSession = ref(false);   // 「本次会话不再提示」

function fmtTok(v) { return Number(v || 0).toLocaleString("zh-CN"); }

/** 打开预估确认框；stage 为 null 表示全流程。 */
async function confirmRunWithEstimate(title, stage, action) {
  if (estSkipSession.value) { await action(); return; }
  estDialog.value = { open: true, loading: true, data: null, title, confirm: action };
  try {
    const r = await api("/estimate" + (stage ? ("?stage=" + stage) : ""));
    if (r.status !== 200) throw new Error(r.data.error || ("HTTP " + r.status));
    estDialog.value.data = r.data;
  } catch (e) {
    estDialog.value.open = false;
    say("预估失败（仍可运行）：" + (e.message || e));
    if (window.confirm("预估失败，是否仍要运行？\n\n" + title)) await action();
    return;
  } finally {
    estDialog.value.loading = false;
  }
}

async function confirmEstimateRun() {
  const fn = estDialog.value.confirm;
  const skip = estSkipSession.value;
  estDialog.value = { open: false, loading: false, data: null, title: "", confirm: null };
  if (skip) say("本次会话已关闭运行前预估提示");
  if (fn) await fn();
}

function cancelEstimate() {
  estDialog.value = { open: false, loading: false, data: null, title: "", confirm: null };
}

async function runStage(n) {
  await confirmRunWithEstimate("运行阶段 " + n + "（" + (STAGE_NAMES[n] || "") + "）",
                               n, () => doRunStage(n));
}

async function doRunStage(n) {
  const r = await api("/stage/" + n + "/run", "POST", {});
  if (r.status === 202) say("已提交 阶段" + n + "（job " + r.data.job_id + "），进度见顶栏");
  else say("提交失败: " + (r.data.error || r.status));
  refresh();
}

async function runPipelineFull() {
  await confirmRunWithEstimate("全自动运行流水线（从第一个未完成阶段跑到审批门）",
                               null, doRunPipelineFull);
}

async function doRunPipelineFull() {
  // ⚠️ 必须显式传 `only_stage: false`：后端把 URL 里的阶段号默认当成"只跑这一阶段"
  // （`only = int(rest)`），只有显式声明 false 才走 `from_stage` 起跑全程。
  // 漏传的后果是：点「全自动」实际只跑了阶段 1，界面却提示"已提交全流程运行"。
  const r = await api("/stage/1/run", "POST", { from_stage: 1, only_stage: false });
  if (r.status === 202) say("已提交全流程运行，进度见顶栏");
  else say("提交失败: " + (r.data.error || r.status));
  refresh();
}

async function runPipelineStreamFull() {
  await confirmRunWithEstimate("流式全自动运行流水线", null, doRunPipelineStreamFull);
}

async function doRunPipelineStreamFull() {
  // 同 doRunPipelineFull：不传 only_stage:false 就只会跑阶段 1
  const r = await api("/stage/1/run", "POST",
                      { from_stage: 1, stream: true, only_stage: false });
  if (r.status === 202) {
    say("已提交流式全流程运行");
    if (streamSource) streamSource.close();
    streamText.value = "";
    streamJob.value = r.data.job_id;
    streamModel.value = "";
    streamCost.value = 0;
    streamStatus.value = "running";
    streamConnected.value = true;
    streamOpen.value = true;
    const es = new EventSource(API + "/stream/" + r.data.job_id);
    streamSource = es;
    es.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "token") {
        streamText.value += msg.text;
      } else if (msg.type === "done") {
        streamStatus.value = msg.status || "ok";
        streamModel.value = msg.model || "";
        streamCost.value = msg.cost_yuan || 0;
        es.close();
        streamSource = null;
        streamConnected.value = false;
        refresh();
      }
    };
    es.onerror = () => {
      es.close();
      streamSource = null;
      streamConnected.value = false;
      if (streamStatus.value === "running") streamStatus.value = "failed";
      refresh();
    };
  } else {
    say("提交失败: " + (r.data.error || r.status));
  }
  refresh();
}

async function runPublish() {
  if (!state.value) return say("状态未加载");
  const incomplete = state.value.stages.filter(s => s.status !== "done");
  if (incomplete.length > 0) {
    const stages = incomplete.map(s => s.stage).join(", ");
    return say(`阶段 ${stages} 尚未完成，无法生成成品`);
  }
  if (!window.confirm("确定要生成最终 Word 全书？\n\n将生成到 output/ 目录。")) return;
  const r = await api("/stage/7/run", "POST", {});
  if (r.status === 202) say("已提交生成 Word 成品（job " + r.data.job_id + "）");
  else say("提交失败: " + (r.data.error || r.status));
  refresh();
}

async function approve(stage, revoke = false) {
  const r = await api("/approve", "POST", { stage, revoke });
  say(r.status === 200 ? (revoke ? "已撤销审批（阶段" + stage + "）" : "已确认阶段" + stage)
                       : "操作失败: " + (r.data.message || r.data.error || ""));
  refresh();
}

const rejectOpen = ref(false);
const rejectStage = ref(null);
const rejectReason = ref("");
function openReject(stage) {
  rejectStage.value = stage;
  rejectReason.value = "";
  rejectOpen.value = true;
}
async function submitReject() {
  const r = await api("/reject", "POST", { stage: rejectStage.value, reason: rejectReason.value, dry_run: false });
  rejectOpen.value = false;
  say(r.status === 200 ? "已打回阶段" + rejectStage.value : "打回失败: " + (r.data.error || ""));
  refresh();
}

const refineOpen = ref(false);
const refineFeedback = ref("");
async function submitRefine() {
  const r = await api("/refine/outline", "POST", { feedback: refineFeedback.value });
  refineOpen.value = false;
  if (r.status === 202) say("精修任务已提交，跑完可在流水线页签看到状态");
  else say("提交失败: " + (r.data.error || ""));
  refresh();
}

/* ---------- 实时流式输出 ---------- */
const streamText = ref("");
const streamJob = ref(null);
const streamConnected = ref(false);
const streamModel = ref("");
const streamCost = ref(0);
const streamStatus = ref(""); // "running" | "ok" | "failed" | "stopped"
const streamOpen = ref(false);
const streamPromptOverride = ref("");
let streamSource = null;

async function pauseStream() {
  if (!streamJob.value) return;
  const r = await api("/stream/pause", "POST", { job_id: streamJob.value });
  if (r.status === 200 && r.data.ok) {
    streamStatus.value = "paused";
    say("已暂停输出，可修改提示词后点恢复");
  } else {
    say("暂停失败: " + (r.data?.error || r.status));
  }
}

async function resumeStream() {
  if (!streamJob.value) return;
  const r = await api("/stream/resume", "POST", { job_id: streamJob.value });
  if (r.status === 200 && r.data.ok) {
    streamStatus.value = "running";
    if (streamPromptOverride.value.trim()) {
      say("已恢复，新提示词将在后续输出中生效");
    } else {
      say("已恢复输出");
    }
  } else {
    say("恢复失败: " + (r.data?.error || r.status));
  }
}

/* ---------- 错误反馈（A4） ---------- */
const errorLog = ref([]); // [{time, message, detail}]
const showErrorLog = ref(false);

function logError(message, detail = "") {
  errorLog.value.unshift({
    time: new Date().toLocaleTimeString("zh-CN"),
    message,
    detail: detail.slice(0, 500),
  });
  if (errorLog.value.length > 50) errorLog.value.pop();
  say(message);
}

async function runStageStream(n) {
  const r = await api("/stage/" + n + "/run", "POST", { stream: true });
  if (r.status !== 202) {
    say("提交失败: " + (r.data.error || r.status));
    return;
  }
  // 断开旧流
  if (streamSource) streamSource.close();
  streamText.value = "";
  streamJob.value = r.data.job_id;
  streamModel.value = "";
  streamCost.value = 0;
  streamStatus.value = "running";
  streamConnected.value = true;
  streamOpen.value = true;

  const es = new EventSource(API + "/stream/" + r.data.job_id);
  streamSource = es;
  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "token") {
      streamText.value += msg.text;
    } else if (msg.type === "done") {
      streamStatus.value = msg.status || "ok";
      streamModel.value = msg.model || "";
      streamCost.value = msg.cost_yuan || 0;
      es.close();
      streamSource = null;
      streamConnected.value = false;
      refresh();
    }
  };
  es.onerror = () => {
    es.close();
    streamSource = null;
    streamConnected.value = false;
    if (streamStatus.value === "running") streamStatus.value = "failed";
    refresh();
  };
}

function stopStream() {
  if (!streamJob.value) return;
  api("/stop", "POST", { job_id: streamJob.value });
  streamStatus.value = "stopped";
  if (streamSource) {
    streamSource.close();
    streamSource = null;
  }
  streamConnected.value = false;
  refresh();
}

async function stopJob() {
  const jobId = state.value?.current_job;
  if (!jobId) return;
  const r = await api("/stop", "POST", { job_id: jobId });
  if (r.status === 200) {
    say("已发送停止请求，当前阶段完成后停止");
  } else {
    say(`停止失败: ${r.data?.error || r.status}`);
  }
}

/* ---------- 其他 ---------- */
const projects = ref({ current: "", archived: [] });
const archiveName = ref("");

/* ---------- 初始化向导（C1） ---------- */
const showInitWizard = ref(false);
const initWizardStep = ref(1);
const initBookName = ref("");
const initBookType = ref("奇幻");
const initChapters = ref(10);
const initStyleNotes = ref("");

async function checkInitWizard() {
  // 首次加载时检查是否需要显示初始化向导
  const cfg = await api("/config/project");
  if (cfg.status === 200 && cfg.data.ok) {
    const book = cfg.data.config?.book || {};
    if (!book.name || book.name === "示例书名（待填写）" || !book.chapters) {
      showInitWizard.value = true;
      initBookName.value = book.name === "示例书名（待填写）" ? "" : (book.name || "");
      initBookType.value = book.type || "奇幻";
      initChapters.value = book.chapters || 10;
      initStyleNotes.value = book.style_notes || "";
    }
  }
}

async function submitInitWizard() {
  if (!initBookName.value.trim()) return say("请填写书名");
  if (initChapters.value < 1 || initChapters.value > 999) return say("章节数须在 1-999 之间");
  if (!window.confirm(
      `确认初始化项目？\n\n书名：${initBookName.value}\n类型：${initBookType.value}\n章节数：${initChapters.value}\n\n将写入 config/project.yaml。`)) return;
  const r = await api("/project/init", "POST", {
    name: initBookName.value,
    type: initBookType.value,
    chapters: initChapters.value,
    style_notes: initStyleNotes.value,
  });
  if (r.status === 200 && r.data.ok) {
    showInitWizard.value = false;
    say("项目初始化完成");
    refresh();
  } else {
    say("初始化失败: " + (r.data.error || r.data.message || r.status));
  }
}

/* ---------- 素材管理 ---------- */
const materials = ref([]);      // [{name, size, modified, kind, ext, md5}]
const materialsLoaded = ref(false);
const materialEdit = ref(null);  // {name, content} 正在编辑的素材
const materialEditDirty = ref(false);
const materialDir = ref("");
const newMaterialName = ref("");
const newMaterialContent = ref("");
const newMaterialOpen = ref(false);

async function loadMaterials() {
  const r = await api("/materials/list");
  if (r.status === 200) {
    materials.value = r.data.items || [];
    materialDir.value = r.data.dir || "";
    materialsLoaded.value = true;
  } else {
    say("加载失败: " + (r.data.error || r.status));
  }
}

async function addMaterial() {
  if (!window.mofangAPI?.openFileDialog) {
    say("文件选择需通过 Electron 启动");
    return;
  }
  const r = await window.mofangAPI.openFileDialog({
    title: "添加素材到 materials/raw/",
    properties: ["openFile", "multiSelections"],
  });
  if (!r.ok) return say(r.error || "选择文件失败");
  for (const path of r.paths || []) {
    const a = await api("/materials/add", "POST", { src_path: path });
    if (a.status === 200 && a.data.ok) say("已添加: " + a.data.name);
    else say("添加失败: " + (a.data.error || a.status));
  }
  loadMaterials();
}

async function createMaterial() {
  const name = newMaterialName.value.trim();
  if (!name) return;
  if (!name.endsWith(".md") && !name.endsWith(".txt")) {
    say("新建素材须以 .md 或 .txt 结尾");
    return;
  }
  const a = await api("/materials/new", "POST", { name, content: newMaterialContent.value });
  if (a.status === 200 && a.data.ok) {
    say("已创建: " + a.data.name);
    newMaterialName.value = "";
    newMaterialContent.value = "";
    newMaterialOpen.value = false;
    loadMaterials();
  } else {
    say("创建失败: " + (a.data.error || a.status));
  }
}

async function openMaterialEdit(name) {
  const r = await api("/materials/read/" + encodeURIComponent(name));
  if (r.status === 200 && r.data.ok) {
    materialEdit.value = { name, content: r.data.content };
    materialEditDirty.value = false;
  } else {
    say("读取失败: " + (r.data.error || r.status));
  }
}

async function saveMaterialEdit() {
  if (!materialEdit.value) return;
  if (!window.confirm(
      "确定保存「" + materialEdit.value.name + "」？\\n\\n修改立即生效。")) return;
  const a = await api("/materials/save", "POST", {
    name: materialEdit.value.name,
    content: materialEdit.value.content,
  });
  if (a.status === 200 && a.data.ok) {
    say("已保存");
    materialEditDirty.value = false;
    loadMaterials(); // refresh sizes
  } else {
    say("保存失败: " + (a.data.error || a.status));
  }
}

async function deleteMaterial(name) {
  if (!window.confirm(
      "确定删除「" + name + "」？\\n\\n仅删除 raw/ 副本，不影响设定集。history/ 快照保留。")) return;
  const a = await api("/materials/delete", "POST", { name });
  if (a.status === 200 && a.data.ok) {
    say("已删除: " + a.data.name);
    if (materialEdit.value?.name === name) materialEdit.value = null;
    loadMaterials();
  } else {
    say("删除失败: " + (a.data.error || a.status));
  }
}

async function triggerReMerge() {
  if (!window.confirm(
      "确定触发素材重归并？\\n\\n将重新扫描 materials/raw/，更新 manifest 和设定集（阶段1）。")) return;
  const a = await api("/materials/remerge", "POST", {});
  if (a.status === 202) {
    say("已触发重归并（job " + a.data.job_id + "）");
  } else {
    say("触发失败: " + (a.data.error || a.status));
  }
}

function materialKindLabel(m) {
  return { text: "文本", image: "图片", other: "其他" }[m.kind] || m.kind;
}

function materialIcon(m) {
  return m.kind === "image" ? "🖼" : m.kind === "other" ? "📦" : "📄";
}

function fmtBytes(b) {
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB";
  return (b / 1024 / 1024).toFixed(2) + " MB";
}

async function refreshModels() {
  const r = await api("/models/available");
  if (r.status === 200) {
    providers.value = r.data.providers;
    // 逐 provider 收模型列表（不再「只取第一个 provider」—— 那是换供应商后
    // 下拉里看不到新模型的根因）
    const byProv = {};
    for (const [pid, p] of Object.entries(r.data.providers || {})) {
      byProv[pid] = Array.isArray(p.available_models) ? p.available_models.slice() : [];
    }
    modelsByProvider.value = byProv;
    modelOptions.value = Object.values(byProv).flat();
    // 构建服务商选项列表
    providerOptions.value = Object.entries(r.data.providers).map(([pid, p]) => ({
      id: pid,
      name: pid + (p.has_key ? " ✓" : " ✗"),
      has_key: p.has_key,
    }));
    say("已刷新");
  } else {
    say("刷新失败");
  }
}

async function refreshFetchedModels() {
  fetchingModels.value = true;
  try {
    const r = await api("/models/fetched");
    if (r.status === 200 && r.data.providers) {
      // 逐 provider 汇总。⚠️ 旧实现**硬编码 tokenhub**，并且顺手把 providerOptions
      // 重置成「只剩 tokenhub 一项」—— 切到别的供应商后下拉里就再也选不回来了。
      // 现在：谁成功就更新谁，**绝不重建 providerOptions**（那只该由 refreshModels 负责）。
      const merged = { ...modelsByProvider.value };
      const parts = [];
      let okCount = 0;
      for (const [pid, info] of Object.entries(r.data.providers)) {
        if (info && Array.isArray(info.models) && info.models.length) {
          merged[pid] = info.models.slice();
          parts.push(`${pid} ${info.count ?? info.models.length} 个`);
          okCount += 1;
        } else if (info && info.error) {
          parts.push(`${pid} ✗ ${String(info.error).slice(0, 48)}`);
        }
      }
      if (okCount) {
        modelsByProvider.value = merged;
        modelOptions.value = Object.values(merged).flat();
        modelSource.value = "fetched";
        say(`同步完成：${parts.join(" / ")}`);
      } else {
        say(`同步失败：${parts.join(" / ") || "无可用供应商"}（检查 .env 中的 API Key）`);
      }
    } else if (r.status === 500) {
      say(`同步失败: 服务端错误（${r.data?.error || "查看 nf_api 日志"}）`);
    } else {
      say(`同步失败 (HTTP ${r.status})`);
    }
  } catch (e) {
    say(`同步失败: ${e.message}（检查 nf_api 是否在线）`);
  } finally {
    fetchingModels.value = false;
  }
}

async function switchProvider(role, newProvider) {
  if (!newProvider) return;
  // 更新 models 中该 role 的 provider
  const cfg = models.value;
  if (cfg?.model?.[role]) {
    cfg.model[role].provider = newProvider;
  }
  // 调用后端 API
  const r = await api("/config/provider", "POST", { role, provider: newProvider });
  if (r.status === 200) {
    // 换了供应商、模型名未必跟着换。若当前模型不在新供应商的清单里，
    // 运行时会对该模型名直接 404 —— 而本项目对 400/404 是 **raise、不走 fallback**
    // （见 llm_client._post_chat），等于整个角色哑火。所以这里必须提醒。
    const curModel = cfg?.model?.[role]?.id || "";
    const avail = modelsByProvider.value[newProvider] || [];
    let msg = `已切换 ${role} → ${newProvider}`;
    if (curModel && avail.length && !avail.includes(curModel)) {
      msg += `，但模型「${curModel}」不在其清单内 —— 请同时把模型改为 ${avail.slice(0, 3).join(" / ")} 之一`;
    }
    if (r.data?.warning) msg += `；${r.data.warning}`;
    say(msg);
  } else {
    say(`切换失败: ${r.data?.error || r.status}`);
  }
}

const newModelName = ref("");
const modelSearch = ref("");
const providerOptions = ref([]);

const filteredModelOptions = computed(() => {
  const all = modelOptions.value || [];
  const q = modelSearch.value.trim().toLowerCase();
  if (!q) return all;
  return all.filter((m) => m.toLowerCase().includes(q));
});

// 某角色所在供应商的模型清单（含手动添加项与当前值）。
// 当前值必须始终在列表里：否则下拉会显示为空白，看起来像「模型配置丢了」。
function providerModelOptions(pid, current) {
  const base = (modelsByProvider.value[pid] || []).concat(manualModels.value);
  const all = Array.from(new Set(base.filter(Boolean)));
  if (current && !all.includes(current)) all.unshift(current);
  const q = modelSearch.value.trim().toLowerCase();
  return q ? all.filter((m) => m.toLowerCase().includes(q)) : all;
}

async function addManualModel() {
  const name = newModelName.value.trim();
  if (!name) return;
  if (!modelOptions.value.includes(name)) {
    modelOptions.value = [...modelOptions.value, name];
  }
  // 手动添加 = 用户显式准入（后端 /models/add 会写 fetched_models.json 的 _manual，
  // 该模型随即通过白名单校验）。这里同步进 manualModels，让**每个**供应商的
  // 下拉都能看到它。
  if (!manualModels.value.includes(name)) {
    manualModels.value = [...manualModels.value, name];
  }
  newModelName.value = "";
  say(`已手动添加: ${name}`);
  // 持久化到缓存
  try {
    await api("/models/add", "POST", { name });
  } catch (e) {
    console.warn("[App] 持久化手动模型失败:", e);
  }
}

// ---------- API Key 写入（GUI 内置；不再借助外部编辑器）----------
// 为什么不是「打开 .env」：主进程按 2026-09-19 审查 S7 把 .env 排除在外部打开
// 白名单外（程序不该代为用外部编辑器打开明文密钥），但当时只砍了出口、没给入口 ——
// 界面留着那个按钮，点下去必然「路径不在白名单」，用户卡在"怎么填 Key"。
// 现在改由后端写盘（键名白名单 + 值校验 + 写前备份），前端只传键与值。
const envEditing = ref("");   // 正在编辑哪个 provider 的 Key（空 = 都不在编辑）
const envValue = ref("");     // 新 Key 明文（仅存在于内存，不回显已存值）
const envConfirmOpen = ref(false); // 「在文件夹中显示 .env」前的知情确认（明文密钥风险，2026-10-01）

function startEnvEdit(pid) {
  envEditing.value = pid;
  envValue.value = "";
}

function cancelEnvEdit() {
  envEditing.value = "";
  envValue.value = "";
}

async function saveEnvKey(pid, clear = false) {
  const p = (providers.value || {})[pid] || {};
  const keyName = p.api_key_env || "";
  if (!keyName) {
    say(`服务商 ${pid} 未配置 api_key_env，无法写入`);
    return;
  }
  if (!clear && !envValue.value.trim()) {
    say("请先粘贴 API Key");
    return;
  }
  const r = await api("/env/set", "POST", { key: keyName, value: clear ? "" : envValue.value.trim() });
  if (r.status === 200) {
    say(clear ? `已清空 ${keyName}` : `已写入 ${keyName}（${r.data.mask || "已设置"}），立即生效`);
    cancelEnvEdit();
    await refreshModels();
  } else {
    say("写入失败: " + (r.data?.error || r.status));
  }
}

async function revealEnvInFolder() {
  // 先确保 .env 存在（缺失时后端会按当前 providers 生成模板）
  const r = await api("/env/open");
  const envPath = (r.status === 200 && r.data && r.data.env_path) || "";
  if (!window.mofangAPI || !window.mofangAPI.revealInFolder) {
    say(envPath ? "当前为浏览器预览，无法定位；.env 实际位置：" + envPath
                : "当前为浏览器预览，无法定位 .env");
    return;
  }
  const res = await window.mofangAPI.revealInFolder(".env");
  // 必须看返回值：此前不管成功与否都提示「已在文件夹中定位」——
  // 定位失败被吞掉，用户只看到「点了没反应」，还以为是自己电脑的问题。
  if (res && res.ok) {
    say("已在文件夹中定位 .env");
  } else {
    say("定位失败：" + ((res && res.error) || "未知错误")
        + (envPath ? "；.env 实际位置：" + envPath : ""));
  }
}

async function switchModel(role, modelId) {
  const r = await api("/models/switch", "POST", { role, model: modelId });
  if (r.status === 200) {
    say("已切换 " + role + " → " + modelId + "（下次运行生效）");
  } else {
    // 白名单拒绝时后端会带 suggestions；只显示 "切换失败" 会让人不知道下一步做什么。
    const d = r.data || {};
    const sug = Array.isArray(d.suggestions) && d.suggestions.length
      ? "；相近候选：" + d.suggestions.join("、") : "";
    say("切换失败: " + (d.error || r.status) + sug);
  }
  refresh();
}

/* ---------- 熔断恢复 ---------- */
const restartFromStage = computed(() => {
  if (!state.value) return 1;
  const stages = state.value.stages || [];
  for (const s of stages) {
    if (s.status !== "done") return s.stage;
  }
  return 1;
});

async function confirmRestart() {
  circuitBreakerShow.value = false;
  const stage = restartFromStage.value;
  if (!window.confirm(`确认从阶段 ${stage} 重新运行？\n\n已完成的章节不会重写。`)) return;
  const r = await api(`/stage/${stage}/run`, "POST", { from_stage: stage });
  if (r.status === 202) {
    say(`已从阶段 ${stage} 重新开始`);
  } else {
    say(`启动失败: ${r.data?.error || r.status}`);
  }
}

/* ---------- 中途修改设定 ---------- */
const originalSetting = ref(null);

async function openSettingEdit() {
  const r = await api("/setting/current");
  if (r.status === 200) {
    originalSetting.value = r.data.setting || {};
    settingEditDisclaimer.value = true;
  } else {
    say("加载设定失败");
  }
}

function cancelSettingEdit() {
  settingEditDisclaimer.value = false;
  originalSetting.value = null;
}

async function confirmSettingEdit() {
  settingEditDisclaimer.value = false;
  settingEditOpen.value = true;
  const r = await api("/setting/current");
  if (r.status === 200) {
    originalSetting.value = r.data.setting || {};
  }
}

async function saveSettingEdit() {
  const r = await api("/setting/save", "POST", { setting: originalSetting.value });
  if (r.status === 200) {
    settingEditOpen.value = false;
    say("设定已保存，将在下一章节生效");
    originalSetting.value = null;
  } else {
    say(`保存失败: ${r.data?.error || r.status}`);
  }
}

/* ---------- 提示词模板编辑器（prompts/stage[1-7]_*.md） ---------- */
const promptFiles = ref([]);       // [{name, stage, size, modified, backups}]
const promptName = ref("");        // 当前编辑的文件名
const promptContent = ref("");
const promptModified = ref("");
const promptBackups = ref([]);
const promptBackupSel = ref("");   // 选中的历史备份（待回滚）
const promptDirty = ref(false);
const promptLoading = ref(false);

async function loadPromptList() {
  if (!window.mofangAPI?.promptsList) {
    say("当前为浏览器预览模式，提示词编辑器需通过 Electron 启动");
    return;
  }
  const r = await window.mofangAPI.promptsList();
  if (!r.ok) {
    say("模板列表加载失败: " + (r.error || ""));
    return;
  }
  promptFiles.value = r.items || [];
  if (!promptName.value && promptFiles.value.length) {
    openPrompt(promptFiles.value[0].name);
  }
}

async function openPrompt(name) {
  if (promptDirty.value &&
      !window.confirm("当前模板有未保存的修改，切换后修改将丢失。确定继续？")) return;
  promptName.value = name;
  promptLoading.value = true;
  const r = await window.mofangAPI.promptsGet(name);
  promptLoading.value = false;
  if (!r.ok) {
    say("读取失败: " + (r.error || ""));
    return;
  }
  promptContent.value = r.content || "";
  promptModified.value = r.modified || "";
  promptBackups.value = r.backups || [];
  promptBackupSel.value = "";
  promptDirty.value = false;
}

// 从 prompts/history/ 回滚。走 HTTP 端点而不是本地覆盖文件 ——
// 回滚动作本身也要留痕（旧版仍进备份链），否则出问题时连对照物都没有。
async function restorePrompt(backup) {
  if (!promptName.value || !backup) return;
  if (!window.confirm(
      "用备份回滚 " + promptName.value + "？\n\n"
      + "· 备份文件：" + backup + "\n"
      + "· 当前版本会先存入 prompts/history/（所以回滚本身也可撤销）\n"
      + "· 回滚后编辑器会重新载入")) return;
  const r = await api("/prompts/restore", "POST",
                      { name: promptName.value, backup });
  if (r.status === 200) {
    say(r.data?.message || "已回滚");
    await openPrompt(promptName.value);
    loadPromptList();
  } else {
    say("回滚失败: " + (r.data?.error || r.status));
  }
}

async function savePrompt() {
  if (!promptName.value) return;
  if (!window.confirm(
      "确定保存 " + promptName.value + "？\n\n"
      + "· 旧版会自动备份到 prompts/history/\n"
      + "· 修改将在下次运行对应阶段时生效\n"
      + "· 若改坏可到 prompts/history/ 取回旧版")) return;
  const r = await window.mofangAPI.promptsSave(promptName.value, promptContent.value);
  if (!r.ok) {
    say("保存失败: " + (r.error || ""));
    return;
  }
  promptDirty.value = false;
  say("已保存 " + promptName.value + (r.backup ? "（备份: " + r.backup + "）" : ""));
  loadPromptList();
}

const STAGE_NAMES = { 1: "素材→设定集", 2: "整体大纲", 3: "逐章大纲", 4: "逐章写作", 5: "逻辑检查", 6: "润色", 7: "Word 成品" };
const stageList = computed(() => {
  if (!state.value) return [];
  return state.value.stages.map((s) => ({ ...s, name: STAGE_NAMES[s.stage] || "" }));
});
const pendingGates = computed(() => {
  if (!state.value) return [];
  return state.value.stages.filter((s) => s.status === "done" && !s.approved);
});
const isRunning = computed(() => !!state.value?.current_job);
const progressPct = computed(() => {
  if (!state.value) return 0;
  const stages = state.value.stages || [];
  if (!stages.length) return 0;
  // 阶段内进度：当前运行阶段按章节数细分
  let total = 0;
  let done = 0;
  for (const s of stages) {
    if (s.status === "done") {
      total += 1;
      done += 1;
    } else if (s.status === "running") {
      total += 1;
      // 阶段内进度：当前阶段完成的章节数 / 总章节数
      const chapters = s.chapters_done || 0;
      const totalChapters = s.total_chapters || 1;
      done += Math.min(chapters / Math.max(totalChapters, 1), 0.99);
    } else {
      total += 1;
    }
  }
  return total > 0 ? Math.round((done / total) * 100) : 0;
});

// 预计剩余成本（基于已完成阶段的平均成本）
const estimatedRemainingCost = computed(() => {
  if (!state.value || !costSummary.value) return null;
  const stages = state.value.stages || [];
  const doneStages = stages.filter(s => s.status === "done");
  if (!doneStages.length) return null;
  const avgCostPerStage = costSummary.value.total_yuan / doneStages.length;
  const remaining = stages.filter(s => s.status !== "done").length;
  return (avgCostPerStage * remaining).toFixed(2);
});
const allDone = computed(() => {
  if (!state.value) return false;
  const stages = state.value.stages || [];
  return stages.length > 0 && stages.every((s) => s.status === "done");
});
const currentStageText = computed(() => {
  if (!state.value) return "—";
  const stages = state.value.stages || [];
  const running = stages.find((s) => s.status === "running");
  if (running) return "阶段 " + running.stage + "·" + (STAGE_NAMES[running.stage] || "") + "（运行中）";
  const pending = stages.find((s) => s.status === "pending");
  if (pending) return "阶段 " + pending.stage + "·" + (STAGE_NAMES[pending.stage] || "") + "（待运行）";
  const failed = stages.find((s) => s.status === "failed" || s.status === "rejected");
  if (failed) return "阶段 " + failed.stage + "·" + (STAGE_NAMES[failed.stage] || "") + "（需处理）";
  if (stages.every((s) => s.status === "done")) return "全部完成";
  return "—";
});
const doneStages = computed(() => {
  if (!state.value) return 0;
  return state.value.stages.filter((s) => s.status === "done").length;
});
const totalStages = computed(() => {
  if (!state.value) return 7;
  return state.value.stages.length || 7;
});
const progressPercent = computed(() => progressPct.value);

function statusBadge(s) {
  return { pending: "待办", running: "进行中", done: "完成", failed: "失败", rejected: "已打回", retrying: "自动重试中" }[s] || s;
}
function fmtYuan(v) { return "¥" + Number(v || 0).toFixed(4); }
function fmtNum(v) { return Number(v || 0).toLocaleString("zh-CN"); }
function fmtTime(iso) { return (iso || "").replace("T", " "); }

/* ============================================================
   UX 增强（2026-09-15）
   ① 一键工作流：选阶段 → 估成本 → 一键跑 → 跑完自动提示下一步
   ② 键盘快捷键 + 命令面板（Ctrl+K）
   ③ 失败/卡住阶段的错误恢复一级入口（重试 / 跳过 / 回退 / 日志）
   ============================================================ */

/* ---------- ① 一键工作流 ---------- */
// 页签注册表：数字键直接跳页签（13 个页签就是这套「工作阶段」导航）
const TAB_ORDER = [
  { t: "pipeline", name: "流水线", key: "1" },
  { t: "chapters", name: "章节", key: "2" },
  { t: "materials", name: "素材", key: "3" },
  { t: "story", name: "设定", key: "4" },
  { t: "outline", name: "大纲", key: "5" },
  { t: "outline_chapters", name: "分章", key: "6" },
  { t: "review", name: "审稿", key: "7" },
  { t: "sandbox", name: "审核", key: "" },
  { t: "proofread", name: "校对", key: "8" },
  { t: "style", name: "文风", key: "9" },
  { t: "inbox", name: "收件箱", key: "0" },
  { t: "cost", name: "成本", key: "" },
  { t: "project", name: "项目", key: "" },
  { t: "settings", name: "设置", key: "" },
  { t: "export", name: "导出", key: "" },
];

const quickStage = ref(null);      // 快速运行面板选中的阶段
const quickEstLoading = ref(false);
const quickEstData = ref(null);
const quickEstError = ref("");

// 下一步（单数）：卡片上的主按钮 / Space 键
const nextAction = computed(() => {
  if (!state.value) return null;
  const stages = state.value.stages || [];
  const gate = stages.find((s) => s.status === "done" && !s.approved);
  if (gate) {
    return { kind: "approve", stage: gate.stage,
             label: "确认阶段 " + gate.stage + "·" + (STAGE_NAMES[gate.stage] || ""),
             hint: "审批门：审阅产物后放行" };
  }
  const pend = stages.find((s) => s.status !== "done");
  if (pend) {
    return { kind: "run", stage: pend.stage,
             label: "运行阶段 " + pend.stage + "·" + (STAGE_NAMES[pend.stage] || ""),
             hint: pend.status === "failed" ? "上次运行失败，可重试" : "从断点续跑" };
  }
  if (stages.length && stages.every((s) => s.status === "done")) {
    return { kind: "publish", label: "生成 Word 成品", hint: "七阶段已完成" };
  }
  return null;
});

// 候选下一步（复数）：跑完弹出的面板 + 命令面板用
const nextActions = computed(() => {
  const acts = [];
  if (!state.value) return acts;
  const stages = state.value.stages || [];
  const gate = stages.find((s) => s.status === "done" && !s.approved);
  const pend = stages.find((s) => s.status !== "done");
  if (gate) {
    acts.push({ id: "approve:" + gate.stage, kind: "approve", stage: gate.stage,
                label: "确认阶段 " + gate.stage + "·" + (STAGE_NAMES[gate.stage] || "") + "（放行）",
                desc: "审批门 —— 可先去收件箱预览产物" });
  }
  if (pend) {
    acts.push({ id: "run:" + pend.stage, kind: "run", stage: pend.stage,
                label: "运行阶段 " + pend.stage + "·" + (STAGE_NAMES[pend.stage] || ""),
                desc: "从该阶段续跑（先出费用预估）" });
  }
  if (stages[3] && stages[3].status === "done") {
    acts.push({ id: "tab:review", kind: "tab", tab: "review",
                label: "去审稿（章节审查 → 批量精修）",
                desc: "逐条接受/忽略 AI 发现，再一键精修" });
  }
  if (stages[5] && (stages[5].status === "done" || stages[5].status === "running")) {
    acts.push({ id: "tab:proofread", kind: "tab", tab: "proofread",
                label: "去校对（标点/错字/章节节奏）",
                desc: "零 token 确定性体检，交付 Word 前必跑" });
  }
  if (stages.length && stages.every((s) => s.status === "done")) {
    acts.push({ id: "publish", kind: "publish",
                label: "生成 Word 成品", desc: "输出 output/{书名}_完整版.docx" });
  }
  acts.push({ id: "tab:chapters", kind: "tab", tab: "chapters",
              label: "浏览章节产物", desc: "raw / checked / refined 逐章对比" });
  return acts;
});

watch(quickStage, (n) => { if (n) loadQuickEstimate(); });

async function loadQuickEstimate() {
  const n = quickStage.value;
  quickEstData.value = null;
  quickEstError.value = "";
  if (!n) return;
  quickEstLoading.value = true;
  const r = await api("/estimate?stage=" + n);
  quickEstLoading.value = false;
  if (r.status === 200) quickEstData.value = r.data;
  else quickEstError.value = r.data?.error || ("HTTP " + r.status);
}

async function execNextAction(a) {
  if (!a) return say("没有可执行的下一步");
  if (a.kind === "run") return runStage(a.stage);
  if (a.kind === "approve") return approve(a.stage);
  if (a.kind === "publish") return runPublish();
  if (a.kind === "tab") return switchTab(a.tab);
  return say("未知的下一步类型: " + a.kind);
}

async function runNextAction() {
  await execNextAction(nextAction.value);
}

async function runQuickStage() {
  const n = quickStage.value;
  if (!n) return say("请先选择要运行的阶段");
  await runStage(n);
}

// 跑完自动提示下一步（②"完成后自动跳转"）：检测 job 从「有」到「无」的跳变
const nextPopup = ref(false);
const nextPopupDismissed = ref(localStorage.getItem("mofang_flow_popup") === "0");
const gateJustHit = ref(false); // 刚刚撞门（从非审批门状态跳到审批门）
let wasRunning = false;
let hadGate = false;
const gateArtifacts = ref({}); // {stage: [{path, lines}]}

// 加载审批门的产物概况（行数/是否为空）
async function loadGateArtifacts() {
  for (const g of pendingGates.value) {
    const paths = g.stage === 2
      ? ["data/outline/global.md", "data/outline/review_report.md"]
      : ["data/outline/polish_report.md"];
    const info = [];
    for (const p of paths) {
      const r = await window.mofangAPI.readPreview(p);
      if (r.ok) {
        // 检查最近一条审计日志，判断产物是否由 Agent 生成
        const agentGenerated = await checkAgentOrigin(p);
        info.push({ path: p, lines: r.content.split("\n").length, agent: agentGenerated });
      }
      else info.push({ path: p, lines: 0, agent: false });
    }
    gateArtifacts.value[g.stage] = info;
  }
}

// 检查产物是否由 Agent 生成（读取审计日志）
async function checkAgentOrigin(artifactPath) {
  try {
    const r = await api("/state");
    if (r.status !== 200) return false;
    // 如果 agent_mode=true，则标记为 Agent 生成
    return !!r.data?.agent_mode;
  } catch (e) {
    return false;
  }
}

watch(pendingGates, loadGateArtifacts, { immediate: true });

// 撞门通知：当审批门出现时弹系统通知
function notifyGate(stage) {
  if (!gateNotify.value) return;
  const label = STAGE_NAMES[stage] || "未知阶段";
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification("绒花墨坊 — 阶段 " + stage + " 可审批", {
        body: label + " 已完成，点击进入收件箱审阅放行。",
        tag: "gate-" + stage,
      });
    }
  } catch (e) { /* ignore */ }
}
async function requestNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    await Notification.requestPermission();
  }
}

let lastFinishedKind = "";

function noteJobTransition(snap) {
  const nowRunning = !!snap?.current_job;
  // 撞门检测：上一拍没有审批门，这一拍出现了 → 通知+自动跳转
  const stages = snap?.stages || [];
  const newGate = stages.find((s) => s.status === "done" && !s.approved);
  if (!hadGate && newGate) {
    notifyGate(newGate.stage);
    gateJustHit.value = true;
    if (gateNotify.value) switchTab("inbox");
  }
  hadGate = !!newGate;
  if (wasRunning && !nowRunning) {
    lastFinishedKind = (lastJob.value?.kind || snap?.last_job?.kind || "") + "";
    // 跑完自动把「快速运行」的选择器推进到下一个未完成阶段
    const a = nextAction.value;
    if (a && a.kind === "run") quickStage.value = a.stage;
    if (!nextPopupDismissed.value) nextPopup.value = true;
  }
  wasRunning = nowRunning;
}

const stage4Done = computed(() => state.value?.stages?.[3]?.status === "done");

// 状态首次加载后，把快速运行面板对齐到「下一个未完成阶段」
watch(state, () => {
  if (quickStage.value === null && nextAction.value?.kind === "run") {
    quickStage.value = nextAction.value.stage;
  }
});

// stage 4 完成后自动拉取章节质量数据（QualityTrend 首次加载触发）
watch(stage4Done, (done) => {
  if (done && Object.keys(chapterQuality.value).length === 0) {
    loadChapterQuality();
  }
});

function dismissNextPopupForever() {
  nextPopupDismissed.value = true;
  localStorage.setItem("mofang_flow_popup", "0");
  nextPopup.value = false;
  say("已关闭自动弹出（可用命令面板重新开启）");
}

function reenableNextPopup() {
  nextPopupDismissed.value = false;
  localStorage.setItem("mofang_flow_popup", "1");
  say("已开启「跑完自动提示下一步」");
}

/* ---------- ① 手动快照 ---------- */
async function doSnapshot() {
  const r = await api("/snapshot", "POST", { label: "手动快照" });
  if (r.status === 202 && r.data.job_id) say("快照任务已提交（job " + r.data.job_id + "）");
  else say("快照失败: " + (r.data?.error || r.status));
  refresh();
}

/* ---------- ③ 错误恢复一级入口 ---------- */
// 跳过阶段：不用 window.confirm（Electron 原生对话框不可靠），走应用内确认框 + 勾选护栏
const skipDlg = ref({ open: false, stage: null, reason: "", agreed: false, busy: false, warn: "" });
function openSkipDlg(stage) {
  skipDlg.value = { open: true, stage, reason: "", agreed: false, busy: false, warn: "" };
}
async function submitSkipStage() {
  const d = skipDlg.value;
  if (!d.agreed) return say("请先勾选确认项");
  d.busy = true;
  const r = await api("/stage/skip", "POST",
                      { stage: d.stage, confirm: true, reason: d.reason || "GUI 人工跳过" });
  d.busy = false;
  if (r.status === 200 && r.data.ok) {
    say(r.data.message);
    if (r.data.missing?.length) d.warn = "缺失产物：" + r.data.missing.join("、");
    skipDlg.value = { open: false, stage: null, reason: "", agreed: false, busy: false, warn: "" };
    refresh();
  } else {
    say("跳过失败: " + (r.data?.error || r.data?.message || r.status));
  }
}

async function retryStage(stage) {
  say("重试阶段 " + stage + " ……");
  await runStage(stage);
}

// 运行日志（失败排障）：读 nf_api/orchestrator 的标准输出尾部
const logDlg = ref({ open: false, loading: false, lines: [], path: "", hint: "", total: 0 });
async function openRunLog() {
  logDlg.value = { open: true, loading: true, lines: [], path: "", hint: "", total: 0 };
  const r = await api("/logs/tail?lines=400");
  if (r.status !== 200) {
    logDlg.value = { open: true, loading: false, lines: [], path: "",
                     hint: "读取日志失败: " + (r.data?.error || r.status), total: 0 };
    return;
  }
  logDlg.value = {
    open: true, loading: false,
    lines: r.data.lines || [], path: r.data.path || "",
    hint: r.data.hint || "", total: r.data.total_lines || 0,
  };
}

/* ---------- ② 键盘快捷键 + 命令面板 ---------- */
const paletteOpen = ref(false);
const helpOpen = ref(false);

/* ---------- 关于 / 新建项目（发布级入口） ---------- */
const aboutOpen = ref(false);

// 免责声明弹窗：first-run=首启强制（未同意不可关）；view=随时查看（关于/命令面板/设置页）
const disclaimerOpen = ref(false);
const disclaimerMode = ref("view");
const disclaimerAckedAt = ref("");
function openDisclaimer(mode = "view") {
  disclaimerMode.value = mode;
  disclaimerAckedAt.value = ackedDisclaimerAt();
  disclaimerOpen.value = true;
}
const newProjectOpen = ref(false);

function openAbout() { aboutOpen.value = true; }

/** 点左上角书名 → 项目页签（顺手把项目列表刷出来） */
function gotoProjectTab() {
  switchTab("project");
  say("→ 项目（新建 / 归档 / 恢复）");
}

function openNewProject() {
  newProjectOpen.value = true;
}

async function onProjectCreated(msg) {
  newProjectOpen.value = false;
  await refresh();
  await loadProjects(true);          // 静默刷新，别把创建成功的 toast 顶掉
  say("新项目已创建：" + msg);
  switchTab("pipeline");
}

/** 工作区全新（没有跑过流水线）→ 冷启动引导 */
const isColdStart = computed(() => {
  if (!state.value) return false;
  const stages = state.value.stages || [];
  const allPending = stages.length > 0 && stages.every((s) => s.status === "pending");
  return allPending && !state.value.has_progress;
});

// 引导显示控制：冷启动时自动显示，用户可关闭；非冷启动时可通过按钮重看
const showGuide = ref(false);
const GUIDE_KEY = "mofang_guide_dismissed";
function dismissGuide() {
  showGuide.value = false;
  localStorage.setItem(GUIDE_KEY, "1");
}
function reopenGuide() {
  showGuide.value = true;
  localStorage.removeItem(GUIDE_KEY);
}

/* ---------- 首启引导状态检测 ---------- */
const coldStartStatus = ref({
  materials: { count: 0, checked: false },
  apiKey: { has_key: false, checked: false },
  bookName: { name: "", checked: false },
});

async function checkColdStartStatus() {
  if (!isColdStart.value) return;
  // 素材数
  try {
    const m = await api("/materials/list");
    if (m.status === 200) {
      coldStartStatus.value.materials = { count: m.data.count || 0, checked: true };
    }
  } catch (e) { /* ignore */ }
  // API Key
  try {
    const p = await api("/models/available");
    if (p.status === 200) {
      const tokenhub = p.data.providers?.tokenhub;
      coldStartStatus.value.apiKey = { has_key: !!tokenhub?.has_key, checked: true };
    }
  } catch (e) { /* ignore */ }
  // 书名
  try {
    const c = await api("/config/project");
    if (c.status === 200 && c.data.ok) {
      coldStartStatus.value.bookName = { name: c.data.config?.book?.name || "", checked: true };
    }
  } catch (e) { /* ignore */ }
}

const coldStartReady = computed(() => {
  const s = coldStartStatus.value;
  return s.materials.count > 0 && s.apiKey.has_key && s.bookName.name && s.bookName.name !== "示例书名（待填写）";
});

const SHORTCUTS = [
  { k: "Ctrl + K", d: "命令面板（搜索一切操作）" },
  { k: "Space", d: "执行下一步 / 停止当前任务" },
  { k: "R", d: "刷新状态与成本" },
  { k: "1 – 9, 0", d: "切换页签（流水线/章节/素材/设定/大纲/分章/审稿/校对/文风/收件箱）" },
  { k: "?", d: "显示本快捷键表" },
  { k: "Esc", d: "关闭当前弹层" },
  { k: "↑ ↓ Enter", d: "命令面板内选择与执行" },
];

function isTypingTarget(el) {
  if (!el) return false;
  const tag = (el.tagName || "").toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
}

function onGlobalKey(e) {
  const k = e.key;
  // Ctrl/Cmd+K：输入框内也要能用
  if ((e.ctrlKey || e.metaKey) && (k === "k" || k === "K")) {
    e.preventDefault();
    paletteOpen.value = !paletteOpen.value;
    return;
  }
  if (k === "Escape") {
    if (paletteOpen.value) { paletteOpen.value = false; return; }
    if (helpOpen.value) { helpOpen.value = false; return; }
    return;
  }
  // 弹层打开时不吃其它按键（交给弹层自己处理）；免责声明首启强制模式同样锁键
  if (paletteOpen.value || helpOpen.value) return;
  if (disclaimerOpen.value && disclaimerMode.value === "first-run") return;
  if (isTypingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (k === "?") { e.preventDefault(); helpOpen.value = true; return; }
  const tag = (e.target?.tagName || "").toLowerCase();
  const onControl = tag === "button" || tag === "a";
  if (k === " " || k === "Spacebar") {
    if (onControl) return;         // 焦点在按钮上时让按钮自己响应
    e.preventDefault();
    if (isRunning.value) stopJob(); else runNextAction();
    return;
  }
  if (k === "r" || k === "R") { e.preventDefault(); refresh(); say("已刷新状态"); return; }
  const hit = TAB_ORDER.find((t) => t.key && t.key === k);
  if (hit) { e.preventDefault(); switchTab(hit.t); say("→ " + hit.name + "（" + hit.key + "）"); }
}

const commands = computed(() => {
  const cs = [];
  const a = nextAction.value;
  if (a) {
    cs.push({ id: "next", group: "工作流", label: "执行下一步 · " + a.label,
              desc: a.hint, hint: "Space", keywords: "next 下一步" });
  }
  cs.push({ id: "flow.all", group: "工作流", label: "全自动运行（跑到审批门）",
            desc: "从第一个未完成阶段依次跑完，带运行前费用预估", keywords: "auto all 全自动" });
  cs.push({ id: "flow.stream", group: "工作流", label: "流式全自动运行",
            desc: "实时逐 token 输出，可暂停/中断", keywords: "stream 流式" });
  cs.push({ id: "publish", group: "工作流", label: "生成 Word 成品",
            desc: "output/{书名}_完整版.docx", keywords: "word docx 导出 成品" });
  cs.push({ id: "newproject", group: "工作流", label: "新建项目（一键创建）",
            desc: "归档当前项目 → 建空工作区 → 写 project.yaml", keywords: "new project 新建 项目 向导" });
  cs.push({ id: "snapshot", group: "工作流", label: "手动快照",
            desc: "history/ 留存一份当前状态，可回退", keywords: "snapshot 备份" });

  for (let n = 1; n <= 7; n++) {
    cs.push({ id: "stage.run:" + n, group: "运行阶段",
              label: "运行阶段 " + n + " · " + (STAGE_NAMES[n] || ""),
              desc: (state.value?.stages?.[n - 1]?.status === "done" ? "该阶段已完成，重跑会覆盖产物" : "带运行前费用预估"),
              keywords: "stage 阶段 " + n });
  }
  for (let n = 1; n <= 7; n++) {
    cs.push({ id: "stage.stream:" + n, group: "运行阶段",
              label: "流式运行阶段 " + n + " · " + (STAGE_NAMES[n] || ""),
              desc: "实时输出，可暂停改提示词", keywords: "stage stream 流式 " + n });
  }
  if (state.value) {
    for (const s of state.value.stages || []) {
      if (s.status === "done" && !s.approved) {
        cs.push({ id: "approve:" + s.stage, group: "审批", label: "确认放行阶段 " + s.stage,
                  desc: "审批门", keywords: "approve 审批 " + s.stage });
      }
      if (s.status === "failed" || s.status === "rejected") {
        cs.push({ id: "stage.run:" + s.stage, group: "错误恢复",
                  label: "重试阶段 " + s.stage + " · " + (STAGE_NAMES[s.stage] || ""),
                  desc: s.status === "failed" ? "上次失败" : "已被打回", keywords: "retry 重试 " + s.stage });
        cs.push({ id: "skip:" + s.stage, group: "错误恢复",
                  label: "跳过阶段 " + s.stage + "（标记完成继续跑）",
                  desc: "不生成产物，仅放行后续阶段", keywords: "skip 跳过 " + s.stage });
        cs.push({ id: "reject:" + s.stage, group: "错误恢复",
                  label: "回退 / 打回阶段 " + s.stage,
                  desc: "清理该阶段及下游产物（history/ 可回退）", keywords: "reject 打回 回退 " + s.stage });
      }
    }
  }
  for (const t of TAB_ORDER) {
    cs.push({ id: "tab:" + t.t, group: "跳转页签",
              label: "切换到 " + t.name + " 页签", hint: t.key || "",
              keywords: "tab go 页签 " + t.name });
  }
  cs.push({ id: "logs", group: "诊断", label: "查看运行日志", desc: "orchestrator / nf_api 输出尾部", keywords: "log 日志 报错" });
  cs.push({ id: "about", group: "诊断", label: "关于绒花墨坊", desc: "版本 / 运行环境 / 数据位置 / 许可证", keywords: "about 关于 版本 许可" });
  cs.push({ id: "disclaimer", group: "诊断", label: "查看免责声明", desc: "AI 订阅合规 / 内容权属 / 风险自担 · 首次使用需确认", keywords: "disclaimer 免责 声明 条款 合规" });
  cs.push({ id: "refresh", group: "诊断", label: "刷新状态", hint: "R", keywords: "refresh 刷新" });
  cs.push({ id: "help", group: "诊断", label: "快捷键说明", hint: "?", keywords: "help 快捷键" });
  cs.push({ id: nextPopupDismissed.value ? "popup.on" : "popup.off", group: "诊断",
            label: nextPopupDismissed.value ? "开启「跑完自动提示下一步」" : "关闭「跑完自动提示下一步」",
            keywords: "popup 提示 自动" });
  for (const th of THEMES) {
    cs.push({ id: "theme:" + th.id, group: "外观", label: "切换到主题：" + th.name,
              keywords: "theme 主题 " + th.name });
  }
  for (const a2 of artifacts.value) {
    cs.push({ id: "open:" + a2.path, group: "打开产物", label: "打开 " + a2.label,
              desc: a2.path, keywords: "open 打开 " + a2.label });
  }
  return cs;
});

async function runCommand(id) {
  paletteOpen.value = false;
  if (!id) return;
  if (id === "next") return runNextAction();
  if (id === "newproject") return openNewProject();
  if (id === "about") return openAbout();
  if (id === "disclaimer") return openDisclaimer("view");
  if (id === "flow.all") return runPipelineFull();
  if (id === "flow.stream") return runPipelineStreamFull();
  if (id === "publish") return runPublish();
  if (id === "snapshot") return doSnapshot();
  if (id === "refresh") { await refresh(); return say("已刷新状态"); }
  if (id === "logs") return openRunLog();
  if (id === "help") { helpOpen.value = true; return; }
  if (id === "popup.on") return reenableNextPopup();
  if (id === "popup.off") return dismissNextPopupForever();
  if (id.startsWith("stage.run:")) return runStage(Number(id.split(":")[1]));
  if (id.startsWith("stage.stream:")) return runStageStream(Number(id.split(":")[1]));
  if (id.startsWith("approve:")) return approve(Number(id.split(":")[1]));
  if (id.startsWith("skip:")) return openSkipDlg(Number(id.split(":")[1]));
  if (id.startsWith("reject:")) return openReject(Number(id.split(":")[1]));
  if (id.startsWith("theme:")) return setTheme(id.slice(6));
  if (id.startsWith("open:")) return openArtifact(id.slice(5));
  if (id.startsWith("tab:")) {
    const t = id.slice(4);
    switchTab(t);
    const hit = TAB_ORDER.find((x) => x.t === t);
    return say(hit ? ("→ " + hit.name) : "已切换");
  }
  say("未知命令: " + id);
}

/* ---------- 章节浏览 ---------- */
const chapters = ref([]);      // [{n, files: {raw, checked, refined}}]
const chaptersLoaded = ref(false);
const pendingRefineChapter = ref(null);
const chapterQuality = ref({});  // {1: 7.5, 2: null, ...}
const chapterThreshold = ref(6);
const gateNotify = ref(gateNotifyEnabled());
const agentMode = ref(false);

/* ---------- 止烧阈值（budget.token_limit）----------
 * 为什么放在设置页：hermes 下金额阈值恒不生效（订阅流量记账恒 0），
 * 止烧全靠这几个 token 上限；阈值要按实测调，看不见就只能猜。
 * 校验在后端（utils/cost_tracker.validate_token_limit）—— 前端只回显错误，
 * 不自己写一份判据（否则两边必然漂移）。 */
const tokenLimit = ref({
  loading: true, busy: false, ok: true, msg: "",
  bounds: {}, preset: {},
  form: { enabled: true, per_request_max_tokens: 50000, per_request_pause_hermes: false,
          max_total_tokens: 10000000, warn_ratio: 0.7 },
});
async function loadTokenLimit() {
  tokenLimit.value.loading = true;
  try {
    const r = await api('/config/token_limit', 'GET');
    if (r.status === 200 && r.data.ok) {
      tokenLimit.value.bounds = r.data.bounds || {};
      tokenLimit.value.preset = r.data.preset || {};
      tokenLimit.value.form = Object.assign({}, tokenLimit.value.preset, r.data.current || {});
      tokenLimit.value.msg = "";
      tokenLimit.value.ok = true;
    } else {
      tokenLimit.value.msg = '读取失败: ' + (r.data.error || r.status);
      tokenLimit.value.ok = false;
    }
  } catch (e) {
    tokenLimit.value.msg = '读取失败: ' + (e.message || e);
    tokenLimit.value.ok = false;
  } finally {
    tokenLimit.value.loading = false;
  }
}
async function saveTokenLimit() {
  tokenLimit.value.busy = true;
  try {
    const r = await api('/config/token_limit', 'POST', tokenLimit.value.form);
    tokenLimit.value.ok = !!(r.status === 200 && r.data.ok);
    tokenLimit.value.msg = tokenLimit.value.ok
      ? (r.data.message || '已保存')
      : ('保存失败: ' + (r.data.error || r.status));
    if (tokenLimit.value.ok && r.data.current) tokenLimit.value.form = Object.assign({}, r.data.current);
  } catch (e) {
    tokenLimit.value.ok = false;
    tokenLimit.value.msg = '保存失败: ' + (e.message || e);
  } finally {
    tokenLimit.value.busy = false;
  }
}
async function resetTokenLimit() {
  tokenLimit.value.busy = true;
  try {
    const r = await api('/config/token_limit', 'POST', tokenLimit.value.preset);
    tokenLimit.value.ok = !!(r.status === 200 && r.data.ok);
    tokenLimit.value.msg = tokenLimit.value.ok
      ? ('已恢复默认预设（单请求 ' + tokenLimit.value.preset.per_request_max_tokens
         + ' / 累计 ' + tokenLimit.value.preset.max_total_tokens + '）')
      : ('恢复失败: ' + (r.data.error || r.status));
    if (tokenLimit.value.ok && r.data.current) tokenLimit.value.form = Object.assign({}, r.data.current);
  } catch (e) {
    tokenLimit.value.ok = false;
    tokenLimit.value.msg = '恢复失败: ' + (e.message || e);
  } finally {
    tokenLimit.value.busy = false;
  }
}

async function toggleAgentMode() {
  const newState = !agentMode.value;
  // 二次确认：开启 Agent 模式前明确提示
  if (newState) {
    if (!window.confirm('确认开启 Agent 模式？\n\n开启后，外部 Agent（如 Hermes）将能通过 HTTP API 调用绒花墨坊进行大纲草拟、设定起草等操作。\n\n注意：审批/打回/归档/恢复/项目创建等操作仍只允许在 GUI 内手动执行。')) {
      return;
    }
  } else {
    if (!window.confirm('确认关闭 Agent 模式？\n\n外部 Agent 将无法再通过 API 调用绒花墨坊。')) {
      return;
    }
  }
  const r = await api('/config/agent_mode', 'POST', { agent_mode: newState });
  if (r.status === 200 && r.data.ok) {
    agentMode.value = newState;
    say(r.data.message || (newState ? 'Agent 模式已开启' : 'Agent 模式已关闭'));
  } else {
    say('切换失败: ' + (r.data.error || r.status));
  }
}

/* ---------- 接入其他 Agent（2026-10-06 对齐方寸「接入页」） ---------- */
// 配置段在主进程按安装态/源码态生成真实路径；这里只展示、复制、打开目标文件。
const agentConnect = ref(null);
const acTab = ref('hermes');
const acKickoff = ref('');
async function loadAgentConnect() {
  if (!window.mofangAPI || !window.mofangAPI.agentConnectInfo) return;
  try {
    const r = await window.mofangAPI.agentConnectInfo();
    if (r && r.ok) agentConnect.value = r;
  } catch (e) { /* IPC 不可用（浏览器验收态）→ 隐藏该块 */ }
}
async function loadKickoff() {
  try {
    const r = await window.mofangAPI.readPreview('prompts/agent_kickoff.md');
    if (r && r.ok) {
      const root = (state.value && state.value.project_dir) || '';
      acKickoff.value = String(r.content || '')
        .replace(/\{\{PROJECT_ROOT\}\}/g, root);
    } else if (r && r.error && r.error.indexOf('文件不存在') === -1) {
      say('启动提示词读取失败: ' + r.error);
    }
  } catch (e) { /* 同上 */ }
}
function copyText(text, okMsg) {
  const fallback = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      say(okMsg);
    } catch (e) { say('复制失败: ' + (e && e.message)); }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => say(okMsg)).catch(fallback);
  } else fallback();
}
function copyAcSnippet() {
  if (!agentConnect.value) return;
  const s = acTab.value === 'hermes'
    ? agentConnect.value.yamlSnippet : agentConnect.value.jsonSnippet;
  copyText(s, 'MCP 配置段已复制');
}
function copyKickoff() { copyText(acKickoff.value, '启动提示词已复制'); }
async function openAgentCfg(target) {
  const r = await window.mofangAPI.agentOpenConfig(target);
  if (r && r.ok) say(r.message || ('已打开: ' + r.path));
  else say('打开失败: ' + ((r && r.error) || '未知错误'));
}

/* ---------- 诊断与调试（2026-10-06 对齐方寸「🩺 诊断」） ---------- */
const dbg = ref(null);
async function loadDebugInfo() {
  if (!window.mofangAPI || !window.mofangAPI.debugInfo) return;
  try {
    const r = await window.mofangAPI.debugInfo();
    if (r && r.ok) dbg.value = r;
  } catch (e) { /* IPC 不可用（浏览器验收态）→ 隐藏值 */ }
}
async function toggleDevtools() {
  const r = await window.mofangAPI.debugToggleDevtools();
  if (r && r.ok) say(r.open ? 'DevTools 已打开' : 'DevTools 已关闭');
  else say('DevTools 切换失败: ' + ((r && r.error) || '未知错误'));
  loadDebugInfo();
}
async function openBackendLog() {
  const r = await window.mofangAPI.debugOpenLog();
  if (r && r.ok) say('已打开日志: ' + r.path);
  else say((r && r.error) || '打开日志失败');
}
async function restartBackend() {
  say('正在重启后端…');
  const r = await window.mofangAPI.debugRestartApi();
  if (r && r.ok) { say('后端已重启，/health 正常'); refresh(); }
  else say('后端重启后未就绪 —— 用「查看运行日志」排查');
  loadDebugInfo();
}
function copyDiagnostics() {
  const d = dbg.value || {};
  copyText(JSON.stringify({
    version: d.version, packaged: d.packaged, platform: d.platform,
    electron: d.electron, apiBase: d.apiBase, apiHealthy: d.apiHealthy,
    apiChildPid: d.apiChildPid, codeRoot: d.codeRoot,
    projectRoot: d.projectRoot, projectRootSource: d.projectRootSource,
    logPath: d.logPath, engine: models.value ? models.value.engine : null,
    agentMode: agentMode.value, online: online.value,
    book: state.value ? state.value.book : null,
    copiedAt: new Date().toISOString(),
  }, null, 2), '诊断信息已复制');
}
async function loadChapters() {
  // 章数从 config/project.yaml 读（白名单允许 config/*.yaml）
  let total = 3;
  const cfg = await window.mofangAPI.readPreview("config/project.yaml");
  if (cfg.ok) {
    const m = cfg.content.match(/^\s*chapters:\s*(\d+)/m);
    if (m) total = parseInt(m[1], 10);
    // 读取质量阈值
    const tm = cfg.content.match(/quality_threshold:\s*([\d.]+)/);
    if (tm) chapterThreshold.value = parseFloat(tm[1]);
  }
  const list = [];
  for (let n = 1; n <= total; n++) {
    const nn = String(n).padStart(2, "0");
    const files = {};
    for (const [k, rel] of Object.entries({
      raw: "data/chapters/raw/" + nn + ".md",
      checked: "data/chapters/checked/" + nn + ".md",
      refined: "data/chapters/refined/" + nn + ".md",
    })) {
      const r = await window.mofangAPI.readPreview(rel);
      files[k] = r.ok ? r.content : null;
    }
    list.push({ n, files });
  }
  chapters.value = list;
  chaptersLoaded.value = true;
  // 加载质量评分
  loadChapterQuality();
}
async function loadChapterQuality() {
  try {
    const r = await api("/chapters/quality");
    if (r.status === 200 && r.data.ok) {
      const q = {};
      for (const c of r.data.chapters) {
        q[c.n] = c.quality;
      }
      chapterQuality.value = q;
    }
  } catch (e) { /* ignore */ }
}
function qualityClass(score) {
  if (score == null) return "st-pending";
  if (score >= 7) return "st-done";
  if (score >= 4) return "st-warn";
  return "st-failed";
}
const viewDoc = ref(null);     // { title, content }
const diffView = ref(null);     // { title, raw, refined }
const diffComputed = ref([]);   // [{type, raw, refined}]
function openDoc(title, content) {
  if (!content) return say("该产物尚未生成");
  viewDoc.value = { title, content };
}
function openDiff(title, raw, refined) {
  if (!raw || !refined) return say("需要原稿和润色稿都已生成");
  diffView.value = { title, raw, refined };
  diffComputed.value = diffParagraphs(raw, refined);
  console.log("[openDiff] diffComputed:", diffComputed.value.length, "items");
}

/* ---------- 章节历史版本（回退到上一版） ---------- */
const histOpen = ref(false);
const histN = ref(null);
const histLoading = ref(false);
const histBusy = ref(false);
const histVersions = ref([]);
const histCurrent = ref("");

async function openHistory(n) {
  histN.value = n;
  histOpen.value = true;
  histLoading.value = true;
  histVersions.value = [];
  const r = await api("/chapters/history?n=" + n);
  histLoading.value = false;
  if (r.status === 200 && r.data.ok) {
    histVersions.value = r.data.versions || [];
    histCurrent.value = r.data.current || "";
  } else {
    say("读取版本历史失败: " + (r.data?.error || r.status));
  }
}

async function restoreChapterVersion(v) {
  if (histBusy.value) return;
  const n = histN.value;
  if (!window.confirm(
      "确定把第 " + n + " 章回退到 v" + v.version + " ？\n\n"
      + "· 版本：v" + v.version + "（" + v.words + " 字，quality=" + (v.quality || "-") + "，"
      + v.mtime + "）\n"
      + "· 当前稿会先另存一版，回退本身也可回退\n"
      + "· 覆盖目标：" + (histCurrent.value || "（当前章文件）"))) return;
  histBusy.value = true;
  const r = await api("/chapters/restore", "POST", { n, version: v.version });
  histBusy.value = false;
  if (r.status === 200 && r.data.ok) {
    histOpen.value = false;
    say(r.data.message);
    await loadChapters();
  } else {
    say("回退失败: " + (r.data?.message || r.data?.error || r.status));
  }
}

/* ---------- 实时花费（成本页「当前运行」） ---------- */
const streamingCost = ref(null);

async function refreshStreamingCost() {
  const r = await api("/costs/streaming");
  if (r.status === 200) streamingCost.value = r.data;
}

const costBarPct = computed(() => {
  const p = Number(streamingCost.value?.pct || 0);
  return Math.max(0, Math.min(100, p));
});
const costBarClass = computed(() => {
  const p = costBarPct.value;
  if (p >= 90) return "danger";
  if (p >= (Number(streamingCost.value?.warn_ratio || 0.7) * 100)) return "warn";
  return "ok";
});

function fmtDur(s) {
  s = Math.max(0, Math.floor(Number(s || 0)));
  if (s < 60) return s + " 秒";
  if (s < 3600) return Math.floor(s / 60) + " 分 " + (s % 60) + " 秒";
  return Math.floor(s / 3600) + " 时 " + Math.floor((s % 3600) / 60) + " 分";
}

const previewText = ref("");
const previewPath = ref("");
async function preview(rel) {
  const r = await window.mofangAPI.readPreview(rel);
  if (!r.ok) return say(r.error);
  previewText.value = r.content;
  previewPath.value = rel;
}
async function openArtifact(rel) {
  const r = await window.mofangAPI.openArtifact(rel);
  if (!r.ok) say(r.error);
}

const artifacts = computed(() => [
  { label: "设定集", path: "data/setting/setting.json" },
  { label: "素材体检", path: "data/setting/material_review.md" },
  { label: "整体大纲", path: "data/outline/global.md" },
  { label: "素材清单", path: "data/setting/materials_manifest.json" },
  { label: "Word 成品", path: "output/" + (state.value?.book || "") + "_完整版.docx" },
]);

async function checkForUpdates() {
  if (!window.mofangAPI?.updaterCheck) {
    updateStatus.value = "当前为浏览器预览，不支持自动更新";
    return;
  }
  try {
    const status = await window.mofangAPI.updaterStatus();
    if (status?.dev) {
      updateStatus.value = "开发模式不支持自动更新（仅安装版可用）";
      return;
    }
    if (status && status.initialized === false) {
      updateStatus.value = "本机未启用自动更新，请用「下载新版本」";
      return;
    }
    checkingUpdate.value = true;
    updateStatus.value = "正在检查…";
    const r = await window.mofangAPI.updaterCheck();
    if (r?.ok) {
      const v = r.version || "", cur = r.currentVersion || "";
      // 有新版本时主进程会另推 update-available / download-progress 事件覆盖此行
      updateStatus.value = (v && cur && v !== cur)
        ? `发现新版本 ${v}，正在下载…`
        : `已是最新版本（v${cur || "?"}）`;
    } else if (r?.noUpdate) {
      updateStatus.value = r.error || "暂无更新（当前已是最新版本）";
    } else {
      updateStatus.value = "检查失败：" + ((r && r.error) || "未知错误");
    }
  } catch (e) {
    // IPC 自身抛错（例如返回值不可序列化）也必须让按钮恢复可用 ——
    // 否则它会永远停在「检查中…」并保持 disabled，看起来就像功能消失了。
    updateStatus.value = "检查失败：" + String((e && e.message) || e);
  } finally {
    checkingUpdate.value = false;
  }
}

async function quitAndInstall() {
  if (window.mofangAPI?.updaterQuitAndInstall) {
    await window.mofangAPI.updaterQuitAndInstall();
  }
}

// 「下载新版本」的手动兜底入口：自动更新在便携版 / 开发模式下不可用，
// 但用户永远需要一条能拿到新版本的路径（此前只在 dev 模式下显示，等于藏起来了）。
const RELEASES_URL = "https://github.com/MUYU46548/ronghuamofang/releases";
function openReleases() {
  if (window.mofangAPI?.openExternal) window.mofangAPI.openExternal(RELEASES_URL);
  else window.open(RELEASES_URL, "_blank");
}


onMounted(() => {
  refresh();
  timer = setInterval(refresh, 2500);
  // 首屏拉一次待审数：沙盒产物是后台写入时自动登记的，不主动刷就看不见徽标
  refreshSandboxBadge();
  // 检查 updater 状态
  if (window.mofangAPI?.updaterStatus) {
    window.mofangAPI.updaterStatus().then((s) => {
      updaterDev.value = !!s?.dev;
      updaterInitialized.value = !!s?.initialized;
      if (updaterDev.value) {
        updateStatus.value = "开发模式（仅安装版支持自动更新）";
      } else if (!updaterInitialized.value) {
        // 便携版 / updater 初始化失败：别让用户以为「点了没反应」
        updateStatus.value = "本机未启用自动更新，请用「下载新版本」";
      } else {
        updateStatus.value = `当前 v${s?.version || "?"} · 可检查更新`;
      }
    }).catch(() => { updaterDev.value = false; });
  }
  // 退出确认
  setupExitGuard();
  // 键盘快捷键（UX-2）：Space/R/数字/?/Ctrl+K
  window.addEventListener("keydown", onGlobalKey);
  // 监听主进程推送的更新事件
  if (window.mofangAPI?.onUpdater) {
    updaterHandler = (data) => {
      if (data.type === "update-available") {
        updateStatus.value = `发现新版本 ${data.version}`;
      } else if (data.type === "download-progress") {
        updateProgress.value = data.percent;
        updateStatus.value = `下载中 ${data.percent.toFixed(1)}%`;
      } else if (data.type === "update-downloaded") {
        updateReady.value = true;
        updateStatus.value = `新版本已下载，可重启安装`;
      } else if (data.type === "no-update") {
        // 404 = 暂无发布，主进程已过滤掉冗长的 HTTP 响应体
        updateStatus.value = data.message || "暂无更新（当前已是最新版本）";
      } else if (data.type === "error") {
        updateStatus.value = "更新检查失败";
      }
    };
    updaterCleanup = window.mofangAPI.onUpdater(updaterHandler);
  }
  // 免责声明首启强制确认（先于向导/引导/updater——合规确认优先于一切 UX）
  if (!hasAckedDisclaimer()) {
    openDisclaimer("first-run");
  }
  // 首次检查是否需要显示初始化向导
  checkInitWizard();
  // 检查素材目录是否为空
  checkMaterialsEmpty();
  // 首启引导状态检测（监听 isColdStart 变化，state 加载完成后自动触发）
  watch(isColdStart, (v) => {
    if (v) {
      showGuide.value = !localStorage.getItem(GUIDE_KEY);
      checkColdStartStatus();
    } else {
      showGuide.value = false;
    }
  });
});

async function checkMaterialsEmpty() {
  // 提醒用户放置素材（仅首次）
  if (!state.value) return;
  const cfg = await api("/config/project");
  if (cfg.status !== 200 || !cfg.data.ok) return;
  const book = cfg.data.config?.book || {};
  if (book.name && book.name !== "示例书名（待填写）" && !book._materials_checked) {
    const r = await api("/materials/list");
    if (r.status === 200 && r.data.count === 0) {
      say("提示：materials/raw/ 暂无素材，放置素材后可运行流水线");
      // 标记已提醒，避免重复
      book._materials_checked = true;
    }
  }
}
onUnmounted(() => {
  clearInterval(timer);
  window.removeEventListener("keydown", onGlobalKey);
  if (typeof updaterCleanup === 'function') updaterCleanup();
});
</script>

<template>
  <div class="bg-blobs" aria-hidden="true">
    <div class="blob blob-a"></div>
    <div class="blob blob-b"></div>
  </div>

  <header class="topbar">
    <div class="brand">
      <!-- LOGO + 应用名 → 关于（版本/环境/数据位置/许可）；书名 → 项目页签 -->
      <button class="brand-btn" @click="openAbout" title="关于绒花墨坊：版本 / 运行环境 / 数据位置 / 开源许可">
        <span class="brand-mark">绒</span>
        <span class="brand-name">绒花墨坊</span>
      </button>
      <button class="brand-sub-btn" @click="gotoProjectTab"
              title="当前项目 —— 点击进入项目管理（新建 / 归档 / 恢复）">
        {{ state ? state.book || "（未命名书）" : "未连接" }}
      </button>
    </div>
    <nav class="tabs">
      <button :class="{ active: tab === 'pipeline' }" @click="switchTab('pipeline')">流水线</button>
      <button :class="{ active: tab === 'chapters' }" @click="switchTab('chapters'); !chaptersLoaded && loadChapters()">章节</button>
      <button :class="{ active: tab === 'materials' }" @click="switchTab('materials'); loadMaterials()">素材</button>
      <button :class="{ active: tab === 'story' }" @click="switchTab('story'); loadSetting()">设定</button>
      <button :class="{ active: tab === 'outline' }" @click="switchTab('outline')">大纲</button>
      <button :class="{ active: tab === 'outline_chapters' }" @click="switchTab('outline_chapters'); loadOutlineChapters()">分章</button>
      <button :class="{ active: tab === 'review' }" @click="switchTab('review')">审稿</button>
      <button :class="{ active: tab === 'refine' }" @click="switchTab('refine')">精修</button>
      <button :class="{ active: tab === 'sandbox' }" @click="switchTab('sandbox'); loadSandboxQueue()">
        审核<span v-if="sandboxPending" class="badge">{{ sandboxPending }}</span>
      </button>
      <button :class="{ active: tab === 'proofread' }" @click="switchTab('proofread')">校对</button>
      <button :class="{ active: tab === 'style' }" @click="switchTab('style')">文风</button>
      <button :class="{ active: tab === 'cost' }" @click="switchTab('cost')">成本</button>
      <button :class="{ active: tab === 'inbox' }" @click="switchTab('inbox')">
        收件箱<span v-if="pendingGates.length" class="badge">{{ pendingGates.length }}</span>
      </button>
      <button :class="{ active: tab === 'project' }" @click="switchTab('project')">项目</button>
      <button :class="{ active: tab === 'settings' }" @click="switchTab('settings')">设置</button>
      <button :class="{ active: tab === 'export' }" @click="switchTab('export')">导出</button>
    </nav>
    <div class="conn" :class="{ on: online }">{{ online ? "已连接" : "离线" }}</div>
    <button class="mini global-refresh-btn" @click="globalRefresh" title="全局刷新（所有页签数据）">↻</button>
    <button class="mini" @click="runRemedy" title="一键补救：检测问题并给出修复建议">🩹 补救</button>
    <button class="mini" @click="openRunLog" title="运行日志（nf_api / 流水线输出尾部）—— 排障第一入口">📋 日志</button>
    <div v-if="agentMode" class="agent-mode-badge" title="Agent 模式已开启 —— 外部 Agent 可通过 HTTP API 调用">⚡ Agent</div>
  </header>

  <!-- 全局"下一步"提示条 -->
  <div v-if="nextAction && !isRunning" class="next-action-bar" @click="runNextAction">
    <span class="na-icon">▶</span>
    <span class="na-label">{{ nextAction.label }}</span>
    <span class="na-hint">{{ nextAction.hint }}</span>
    <span class="spacer"></span>
    <button class="mini primary" @click.stop="runNextAction">执行</button>
  </div>

  <!-- 一键补救结果弹窗 -->
  <div v-if="remedyOpen" class="drawer-mask" @click.self="remedyOpen = false">
    <div class="dialog" style="width: min(560px, 94vw);">
      <h3 style="margin:0 0 10px;">🩹 一键补救建议</h3>
      <div v-if="remedyResult && remedyResult.issues && remedyResult.issues.length === 0" class="meta">
        ✓ 未检测到问题，书档状态良好。
      </div>
      <div v-else>
        <div v-for="(r, i) in (remedyResult?.remedies || [])" :key="i"
             style="padding: 8px 0; border-bottom: 1px solid var(--border);">
          <div style="display:flex; align-items:center; gap:8px;">
            <span :class="['pill', r.blocking ? 'st-failed' : 'st-warn']">{{ r.blocking ? '阻塞' : '提醒' }}</span>
            <b style="font-size:13px;">{{ r.issue }}</b>
          </div>
          <div class="meta" style="margin-top:4px;">{{ r.action }}</div>
          <code style="font-size:11px; color: var(--accent);">{{ r.command }}</code>
        </div>
      </div>
      <div class="dialog-actions">
        <span class="spacer"></span>
        <button class="mini" @click="remedyOpen = false">关闭</button>
      </div>
    </div>
  </div>

  <!-- 运行进度条（全局） -->
  <div v-if="isRunning" class="runbar">
    <span class="run-dot"></span>
    <span>任务执行中：{{ state.current_job }}（{{ lastJob?.result || "运行中…" }}）</span>
    <span v-if="estimatedRemainingCost" class="run-cost">预计剩余 ¥{{ estimatedRemainingCost }}</span>
    <span class="spacer"></span>
    <button class="mini danger" @click="stopJob">停止</button>
    <span class="run-pct">{{ progressPct }}%</span>
  </div>

  <!-- 实时流式输出面板 -->
  <div v-if="streamOpen" class="stream-panel">
    <div class="stream-head">
      <span class="run-dot" v-if="streamConnected"></span>
      <b>实时输出</b>
      <span v-if="streamJob" class="stream-job-id">{{ streamJob }}</span>
      <span class="spacer"></span>
      <span v-if="streamModel" class="pill st-done">{{ streamModel }}</span>
      <span v-if="streamCost > 0" class="stream-cost">¥{{ streamCost.toFixed(4) }}</span>
      <span v-if="streamStatus === 'running'" class="pill st-running">生成中</span>
      <span v-else-if="streamStatus === 'paused'" class="pill" style="background: var(--warn)">⏸ 已暂停</span>
      <span v-else-if="streamStatus === 'ok'" class="pill st-done">完成</span>
      <span v-else-if="streamStatus === 'stopped'" class="pill st-rej">已中断</span>
      <span v-else class="pill st-rej">{{ streamStatus }}</span>
      <button v-if="streamStatus === 'running'" class="mini" @click="pauseStream" title="暂停输出">⏸ 暂停</button>
      <button v-if="streamStatus === 'paused'" class="mini primary" @click="resumeStream" title="恢复输出">▶ 恢复</button>
      <button v-if="streamConnected" class="mini danger" @click="stopStream">中断</button>
      <button class="mini" @click="streamOpen = false; stopStream()">关闭</button>
    </div>
    <!-- 暂停时显示修改提示词输入框 -->
    <div v-if="streamStatus === 'paused'" style="padding: 12px; border-top: 1px solid var(--border);">
      <label style="display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px;">修改提示词（可选，恢复后注入）</label>
      <textarea v-model="streamPromptOverride" class="prompt-text" style="width: 100%; min-height: 80px;" placeholder="输入修改后的提示词（追加到任务末尾）..."></textarea>
    </div>
    <pre class="stream-body">{{ streamText || "等待输出…" }}</pre>
  </div>

  <main class="content">
    <!-- 预算暂停横幅 -->
    <div v-if="state?.budget?.paused" class="budget-banner">
      <span>⚠️ 预算已超限（已用 {{ fmtYuan(state.cost.spent_yuan) }} / 限额 {{ fmtYuan(state.cost.limit_yuan) }}）</span>
      <button class="mini primary" @click="circuitBreakerShow = true">恢复运行</button>
      <button class="mini" @click="switchTab('cost')">查看详情</button>
    </div>

    <!-- 从收件箱跳转而来 → 一步返回 -->
    <div v-if="cameFromInbox && tab !== 'inbox'" class="backbar">
      <button class="mini" @click="backToInbox">← 返回收件箱</button>
      <span class="meta">从收件箱跳转而来；审批门（{{ pendingGates.length }} 项待处理）还在等着你</span>
    </div>


    <!-- 冷启动引导：工作区全新（无产物）时给出三步上手路径 -->
    <div v-if="tab === 'pipeline' && state && showGuide" class="coldstart">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <div class="cs-title">从这个开始（四步跑通第一本书）</div>
        <button class="mini" @click="dismissGuide" title="关闭引导">✕</button>
      </div>
      <div class="cs-steps">
        <div class="cs-step">
          <span class="cs-no">1</span>
          <div>
            <b>放素材</b>
            <div class="meta">materials/raw/ 放设定卡；随手写的碎片丢 materials/original_scraps/</div>
          </div>
          <span v-if="coldStartStatus.materials.checked && coldStartStatus.materials.count > 0" class="pill st-done">✓ {{ coldStartStatus.materials.count }} 张卡</span>
          <span v-else-if="coldStartStatus.materials.checked" class="pill st-warn">暂无素材</span>
          <button class="mini" @click="switchTab('materials')">去素材</button>
        </div>
        <div class="cs-step">
          <span class="cs-no">2</span>
          <div>
            <b>建项目</b>
            <div class="meta">书名 / 类型 / 章数一键创建（自动归档旧项目）</div>
          </div>
          <span v-if="coldStartStatus.bookName.checked && coldStartStatus.bookName.name && coldStartStatus.bookName.name !== '示例书名（待填写）'" class="pill st-done">✓ {{ coldStartStatus.bookName.name }}</span>
          <span v-else-if="coldStartStatus.bookName.checked" class="pill st-warn">未命名</span>
          <button class="mini primary" @click="openNewProject">新建项目</button>
        </div>
        <div class="cs-step">
          <span class="cs-no">3</span>
          <div>
            <b>跑流水线</b>
            <div class="meta">下面「一键工作流 → 执行下一步」；审批门在「收件箱」</div>
          </div>
          <span v-if="coldStartStatus.apiKey.checked && coldStartStatus.apiKey.has_key" class="pill st-done">✓ API 已配置</span>
          <span v-else-if="coldStartStatus.apiKey.checked" class="pill st-warn">API 未配置</span>
          <button class="mini" @click="paletteOpen = true">Ctrl+K 命令面板</button>
        </div>
        <div class="cs-step">
          <span class="cs-no">4</span>
          <div>
            <b>验收交付</b>
            <div class="meta">审稿逐条决策 → 校对体检 → 导出 Word 成品</div>
          </div>
          <button class="mini" @click="openAbout">看完整说明</button>
        </div>
      </div>
      <!-- 状态全绿时显示「开始运行」按钮 -->
      <div v-if="coldStartReady" class="cs-ready">
        <span class="meta">三项准备就绪：素材 {{ coldStartStatus.materials.count }} 张 · API 已配置 · 项目「{{ coldStartStatus.bookName.name }}」</span>
        <button class="mini primary" @click="runNextAction">开始运行流水线</button>
      </div>
      <!-- 状态未全绿时显示提示 -->
      <div v-else class="cs-warn">
        <span class="meta">请先完成上方步骤（素材、项目、API Key）后再运行流水线。</span>
      </div>
    </div>

    <!-- 重看引导按钮（非冷启动或引导已关闭时显示） -->
    <div v-if="tab === 'pipeline' && state && !showGuide" style="margin-bottom: 8px;">
      <button class="mini" @click="reopenGuide" title="重新查看上手引导">📖 重看引导</button>
    </div>

    <!-- Agent 模式开关（从「设置」提到首页）：高频 + 影响权限语义，
         藏在设置页里等于没有 —— 用户会忘了自己到底开着还是关着。 -->
    <div v-if="tab === 'pipeline'" class="agent-bar" :class="{ on: agentMode }">
      <span class="agent-bar-dot"></span>
      <span class="agent-bar-label">Agent 模式</span>
      <span class="agent-bar-desc">
        <template v-if="agentMode">已开启 —— 外部 Agent（Hermes 等）可通过 HTTP API 调用本机；审批 / 打回 / 归档 / 建项目仍只在桌面端手动执行</template>
        <template v-else>已关闭 —— 外部 Agent 无法调用本机 API</template>
      </span>
      <span class="spacer"></span>
      <button class="mini" :class="{ primary: agentMode }" @click="toggleAgentMode">
        {{ agentMode ? '关闭' : '开启' }}
      </button>
    </div>

    <!-- 流水线状态卡片：当前状态 + 下一步 + 实时进度 -->
    <section v-if="tab === 'pipeline' && state" class="card pipeline-status">
      <div class="card-head">
        <h3>当前状态</h3>
        <span v-if="isRunning" class="pill st-running">运行中</span>
        <span v-else-if="pendingGates.length" class="pill st-gate">待审批 {{ pendingGates.length }}</span>
        <span v-else-if="allDone" class="pill st-done">全部完成</span>
        <span v-else class="pill st-pending">准备就绪</span>
      </div>
      <div class="ps-grid">
        <div class="ps-item">
          <span class="ps-label">当前阶段</span>
          <span class="ps-value">{{ currentStageText }}</span>
        </div>
        <div class="ps-item">
          <span class="ps-label">下一步</span>
          <span class="ps-value">{{ nextAction ? nextAction.label : '（已完成）' }}</span>
        </div>
        <div class="ps-item">
          <span class="ps-label">预计费用</span>
          <span class="ps-value">{{ nextAction ? nextAction.hint : '—' }}</span>
        </div>
        <div class="ps-item">
          <span class="ps-label">累计花费</span>
          <span class="ps-value">¥{{ state.cost.spent_yuan.toFixed(2) }} / ¥{{ state.cost.limit_yuan }}</span>
        </div>
        <!-- token 级熔断（hermes 下金额恒 0，真正的止烧闸门是 token）：不显示就等于让用户盲调阈值 -->
        <div class="ps-item" v-if="state.budget && state.budget.token_limit">
          <span class="ps-label">token 闸门</span>
          <span class="ps-value" :class="{ 'bad-val': (state.budget.token_pct || 0) >= 70 }">
            <template v-if="!state.budget.token_limit.enabled">未启用</template>
            <template v-else>
              {{ fmtTok(state.budget.tokens_used) }} / {{ fmtTok(state.budget.token_limit.max_total_tokens) }}
              <span class="meta" v-if="state.budget.token_pct !== null">（{{ state.budget.token_pct }}%）</span>
            </template>
          </span>
        </div>
      </div>
      <div v-if="isRunning" class="ps-progress">
        <div class="ps-progress-bar" :style="{ width: progressPercent + '%' }"></div>
        <span class="ps-progress-text">{{ progressPercent }}% 完成（{{ doneStages }}/{{ totalStages }} 阶段）</span>
      </div>
    </section>

    <!-- 一键工作流（UX-1）：唯一的运行入口 —— 步骤条 + 下一步 + 选阶段预估 + 全自动 -->
    <section v-if="tab === 'pipeline' && state" class="card flow-strip">
      <div class="card-head">
        <h3>一键工作流</h3>
        <span v-if="isRunning" class="pill st-running">运行中</span>
        <span class="spacer"></span>
        <button class="mini" @click="paletteOpen = true" title="Ctrl+K 搜索所有操作">⌘K 命令面板</button>
        <button class="mini" @click="helpOpen = true" title="快捷键说明">? 快捷键</button>
      </div>

      <div class="flow-steps">
        <template v-for="(s, i) in stageList" :key="s.stage">
          <button class="flow-step"
                  :class="{ done: s.status === 'done', cur: nextAction && nextAction.stage === s.stage, bad: s.status === 'failed' || s.status === 'rejected', skip: s.skipped }"
                  :title="(s.rejected ? '打回原因：' + s.rejected : STAGE_NAMES[s.stage]) + '（点击可设为快速运行目标）'"
                  @click="quickStage = s.stage">
            <span class="fs-no">{{ s.status === 'done' ? '✓' : s.stage }}</span>
            <span class="fs-name">{{ STAGE_NAMES[s.stage] }}</span>
          </button>
          <span v-if="i < stageList.length - 1" class="fs-arrow">›</span>
        </template>
      </div>

      <div class="flow-next">
        <span class="meta">下一步</span>
        <b>{{ nextAction ? nextAction.label : '（七阶段已完成，可生成成品或导出）' }}</b>
        <span class="meta">{{ nextAction ? nextAction.hint : '' }}</span>
        <span class="spacer"></span>
        <button class="mini primary" :disabled="isRunning || !nextAction" @click="runNextAction">
          ⏎ 执行下一步
        </button>
        <button class="mini" :disabled="isRunning" @click="runPipelineFull" title="从第一个未完成阶段跑到审批门">全自动</button>
        <button class="mini" :disabled="isRunning" @click="runPipelineStreamFull" title="全自动 + 实时流式输出（可暂停/中断）">流式全自动</button>
        <button class="mini" :disabled="isRunning" @click="runPublish" title="生成最终 Word 全书">📄 Word 成品</button>
        <button class="mini" @click="openSettingEdit" title="暂停流水线并编辑设定集（不影响已完成章节）">✏️ 中途改设定</button>
      </div>

      <div class="flow-quick">
        <span class="meta">快速运行</span>
        <select v-model="quickStage" class="model-select">
          <option :value="null">选择阶段…</option>
          <option v-for="s in stageList" :key="s.stage" :value="s.stage">
            {{ s.stage }} · {{ STAGE_NAMES[s.stage] }}（{{ statusBadge(s.status) }}）
          </option>
        </select>
        <span class="flow-est" :class="{ warn: quickEstData?.budget?.exceeds }">
          <template v-if="!quickStage">—</template>
          <template v-else-if="quickEstLoading">预估中…</template>
          <template v-else-if="quickEstData">
            预计 {{ fmtTok(quickEstData.totals.tokens_in) }} in / {{ fmtTok(quickEstData.totals.tokens_out) }} out
            · {{ fmtYuan(quickEstData.totals.cost_yuan) }}
            <span v-if="quickEstData.budget?.exceeds">⚠ 跑完将超预算</span>
          </template>
          <template v-else-if="quickEstError">预估不可用（仍可运行）：{{ quickEstError }}</template>
          <template v-else>—</template>
        </span>
        <button class="mini" :disabled="isRunning || !quickStage" @click="loadQuickEstimate">重估</button>
        <button class="mini primary" :disabled="isRunning || !quickStage" @click="runQuickStage">▶ 运行所选阶段</button>
      </div>
    </section>

    <!-- 流水线 -->
    <section v-if="tab === 'pipeline' && state" class="grid-2">
      <div class="card">
        <h3>七阶段流水线</h3>
        <div class="progress"><div class="progress-in" :style="{ width: progressPct + '%' }"></div></div>
        <div v-for="s in stageList" :key="s.stage" class="stage-row">
          <div class="stage-info">
            <span class="stage-no" :class="{ lit: s.status === 'done', run: s.status === 'running' }">{{ s.stage }}</span>
            <span class="stage-name">{{ s.name }}</span>
            <span class="pill" :class="'st-' + s.status">{{ statusBadge(s.status) }}</span>
            <span v-if="s.status === 'done' && !s.approved" class="pill st-gate">待审批</span>
            <span v-if="s.skipped" class="pill st-skip" title="人工跳过，未生成产物">已跳过</span>
            <span v-if="s.rejected" class="pill st-rej" :title="s.rejected">打回: {{ s.rejected.slice(0, 12) }}</span>
            <span v-if="s.needs_rewrite && s.needs_rewrite.length" class="pill st-rej"
                  :title="'未达质量线的章节：' + s.needs_rewrite.join('、')">
              待重写 {{ s.needs_rewrite.length }} 章
            </span>
          </div>
          <div class="stage-actions">
            <button class="mini" :disabled="isRunning" @click="runStage(s.stage)">运行</button>
            <button class="mini" :disabled="isRunning" @click="runStageStream(s.stage)" title="实时流式输出，可随时中断">流式运行</button>
            <button v-if="s.status === 'done' && !s.approved" class="mini primary" @click="approve(s.stage)">确认</button>
            <button v-else-if="s.approved" class="mini" @click="approve(s.stage, true)">撤销</button>
            <button v-if="s.stage >= 2 && s.status !== 'pending'" class="mini danger" :disabled="isRunning" @click="openReject(s.stage)">打回</button>
          </div>
          <!-- 错误恢复一级入口（UX-3）：失败/被打回/跳过的恢复动作直接摆在卡片上 -->
          <div v-if="s.status === 'failed' || s.status === 'rejected' || s.skipped || (s.needs_rewrite && s.needs_rewrite.length)"
               class="stage-recovery">
            <span v-if="s.status === 'failed'" class="rec-msg">
              ⚠ 阶段 {{ s.stage }} 执行失败<span v-if="lastJob?.result">（最近一次：{{ lastJob.result }}）</span>
            </span>
            <span v-else-if="s.status === 'rejected'" class="rec-msg">↩ 已被打回：{{ s.rejected }}</span>
            <span v-if="s.skipped" class="rec-msg">⏭ 已人工跳过，未生成产物</span>
            <span v-if="s.needs_rewrite && s.needs_rewrite.length" class="rec-msg">
              ✍ 第 {{ s.needs_rewrite.join('、') }} 章未达质量线
            </span>
            <span class="spacer"></span>
            <button class="mini primary" :disabled="isRunning" @click="retryStage(s.stage)">重试</button>
            <button class="mini" :disabled="isRunning" @click="openSkipDlg(s.stage)">跳过此阶段</button>
            <button v-if="s.stage >= 2" class="mini" :disabled="isRunning" @click="openReject(s.stage)">回退（清下游）</button>
            <button class="mini" @click="openRunLog">查看日志</button>
            <button v-if="s.needs_rewrite && s.needs_rewrite.length" class="mini" @click="switchTab('review')">去批量精修</button>
          </div>
        </div>
      </div>

      <div class="card">
        <h3>产物速览</h3>
        <div v-for="a in artifacts" :key="a.path" class="art-row">
          <span class="art-label">{{ a.label }}</span>
          <span class="art-path">{{ a.path }}</span>
          <span class="spacer"></span>
          <button class="mini" @click="preview(a.path)">预览</button>
          <button class="mini" @click="openArtifact(a.path)">打开</button>
        </div>
        <div class="meta">
          <div>预算: {{ fmtYuan(state.cost.spent_yuan) }} / {{ fmtYuan(state.cost.limit_yuan) }}</div>
          <div>记账笔数: {{ state.cost.calls }}（估算 {{ state.cost.estimated_entries }}）</div>
        </div>
      </div>
    </section>

    <!-- 章节 -->
    <section v-if="tab === 'chapters'" class="card">
      <div class="card-head">
        <h3>章节产物（raw → checked → refined）</h3>
        <button class="mini" @click="loadChapters">重新探测</button>
        <button class="mini" @click="loadChapterQuality" title="刷新质量评分">质量</button>
      </div>
      <!-- P2: 质量趋势图 -->
      <QualityTrend :chapters="Object.keys(chapterQuality).map(n => ({n: +n, quality: chapterQuality[n]}))"
                    :threshold="chapterThreshold" class="qt-wrap"
                    @gotoReview="switchTab('review')" @gotoRefine="switchTab('review')" />
      <div v-if="!chaptersLoaded" class="empty">点击"重新探测"加载章节产物</div>
      <div v-for="c in chapters" :key="c.n" class="chap-row">
        <span class="stage-no lit">{{ c.n }}</span>
        <span class="chap-name">第 {{ c.n }} 章</span>
        <span v-if="chapterQuality[c.n] != null" class="pill" :class="qualityClass(chapterQuality[c.n])">
          {{ chapterQuality[c.n] }}/10
        </span>
        <span v-else-if="c.files.raw" class="pill st-pending">未评分</span>
        <span class="spacer"></span>
        <button class="mini" :class="{ ghost: !c.files.raw }" @click="openDoc('第' + c.n + '章 原稿', c.files.raw)">原稿</button>
        <button class="mini" :class="{ ghost: !c.files.checked }" @click="openDoc('第' + c.n + '章 检查稿', c.files.checked)">检查稿</button>
        <button class="mini" :class="{ ghost: !c.files.refined }" @click="openDoc('第' + c.n + '章 润色稿', c.files.refined)">润色稿</button>
        <button class="mini" :class="{ ghost: !(c.files.raw && c.files.refined) }" @click="openDiff('第' + c.n + '章 润色对比', c.files.raw, c.files.refined)">对比</button>
        <button class="mini" @click="openHistory(c.n)">历史</button>
      </div>
    </section>

    <!-- 大纲（结构化视图 / 节点精修 / 版本对比 / 多方案） -->
    <section v-if="tab === 'outline'">
      <OutlineView ref="outlineRef" :api="api" :is-busy="isRunning" :book-name="state ? state.book : ''"
                   @say="say" @structure-loaded="onStructureLoaded" />
    </section>

    <!-- 审稿 -->
    <section v-if="tab === 'review'">
      <ReviewConsole @goto-refine="switchToRefine" />
    </section>

    <!-- 精修（段落级定点重写） -->
    <section v-if="tab === 'refine'">
      <ParagraphRefinePanel />
    </section>

    <!-- 审核（沙盒产物审核队列：通过/驳回只改状态，不写文件、不碰 vault） -->
    <section v-if="tab === 'sandbox'">
      <SandboxQueue ref="sandboxRef" :api="api"
                    @say="say" @stats="onSandboxStats" />
    </section>

    <!-- 校对（stage 5.5） -->
    <section v-if="tab === 'proofread'">
      <ProofreadPanel @say="say" />
    </section>

    <!-- 文风分析 + 章节节奏 -->
    <section v-if="tab === 'style'">
      <StylePanel @say="say" />
    </section>

    <!-- Story Bible（B1） -->
    <section v-if="tab === 'story' && settingData" class="card">
      <div class="card-head">
        <h3>Story Bible（data/setting/setting.json）</h3>
        <span v-if="settingDirty" class="pill st-gate">已修改</span>
        <span class="spacer"></span>
        <button class="mini" @click="loadSetting">刷新</button>
        <button class="mini primary" :disabled="!settingDirty" @click="saveSetting">保存</button>
      </div>

      <div class="tabs-sub">
        <button :class="{ on: settingTab === 'characters' }" @click="openSettingTab('characters')">
          角色 ({{ countItems('characters') }})
        </button>
        <button :class="{ on: settingTab === 'world' }" @click="openSettingTab('world')">
          世界观 ({{ countItems('world') }})
        </button>
        <button :class="{ on: settingTab === 'concepts' }" @click="openSettingTab('concepts')">
          概念 ({{ countItems('concepts') }})
        </button>
        <button :class="{ on: settingTab === 'timeline' }" @click="openSettingTab('timeline')">
          时间线 ({{ countItems('timeline') }})
        </button>
        <button :class="{ on: settingTab === 'relation' }" @click="openSettingTab('relation')">
          关系图
        </button>
      </div>

      <div v-if="settingTab === 'relation'" style="margin-top: 12px;">
        <div v-if="graphHint" class="meta" style="margin-bottom: 8px;">{{ graphHint }}</div>
        <RoleGraph
          :setting="settingData"
          :appearances="appearances"
          :alias="aliasMap"
          @refresh="refreshAppearances"
        />
      </div>

      <div v-else style="margin-top: 12px;">
        <div class="card-head">
          <h4>{{ { characters: '角色', world: '世界观', plot_fragments: '情节碎片', timeline: '时间线' }[settingTab] }}</h4>
          <span class="spacer"></span>
          <button class="mini" @click="addSettingItem(settingTab)">+ 新增条目</button>
        </div>

        <div v-if="!settingItems(settingTab).length" class="empty">暂无条目</div>
        <div v-for="(item, idx) in settingItems(settingTab)" :key="idx" class="setting-row" @click="openSettingEditor(settingTab, idx)">
          <div class="setting-row-name">{{ item.name || '未命名' }}</div>
          <div class="setting-row-summary">{{ item.description?.slice(0, 60) || '（无描述）' }}</div>
          <span class="spacer"></span>
          <button class="mini danger" @click.stop="removeSettingItem(settingTab, idx)">删除</button>
        </div>
      </div>
    </section>

    <div v-if="settingEditor.open" class="drawer-mask" @click.self="settingEditor.open = false">
      <div class="drawer" style="width: min(640px, 90vw);">
        <div class="drawer-head">
          <b>{{ settingEditor.item?.name || '编辑条目' }}</b>
          <span class="spacer"></span>
          <button class="mini" @click="closeSettingEditor()">关闭</button>
        </div>
        <div style="padding: 16px; max-height: 70vh; overflow-y: auto;">
          <label>名称 *</label>
          <input v-model="settingEditor.item.name" class="text-input" style="width: 100%; margin-bottom: 12px;" placeholder="条目名称" @input="settingDirty = true" />

          <template v-if="settingEditor.key === 'characters'">
            <label>类型</label>
            <input v-model="settingEditor.item.type" class="text-input" style="width: 100%; margin-bottom: 12px;" placeholder="如：主角 / 反派 / 配角" @input="settingDirty = true" />
            <label>标签（逗号分隔）</label>
            <input v-model="settingEditor.item._tagsInput" class="text-input" style="width: 100%; margin-bottom: 12px;" placeholder="如：人类,火属性,主角方" @input="settingDirty = true" />
            <label style="display: flex; gap: 6px; align-items: center; margin-bottom: 12px;">
              <input type="checkbox" v-model="settingEditor.item.locked" @change="settingDirty = true" />
              <span>locked（不可违逆）</span>
            </label>
            <label>简介</label>
            <textarea v-model="settingEditor.item.snippet" class="prompt-text" style="width: 100%; min-height: 150px;" placeholder="角色简介..." @input="settingDirty = true"></textarea>
          </template>

          <template v-else-if="settingEditor.key === 'world'">
            <label>类型</label>
            <input v-model="settingEditor.item.type" class="text-input" style="width: 100%; margin-bottom: 12px;" placeholder="如：地点 / 势力 / 概念 / 物品" @input="settingDirty = true" />
            <label>标签（逗号分隔）</label>
            <input v-model="settingEditor.item._tagsInput" class="text-input" style="width: 100%; margin-bottom: 12px;" placeholder="如：人类,科技,都市" @input="settingDirty = true" />
            <label>简介</label>
            <textarea v-model="settingEditor.item.snippet" class="prompt-text" style="width: 100%; min-height: 150px;" placeholder="世界观条目简介..." @input="settingDirty = true"></textarea>
          </template>

          <template v-else>
            <label>描述</label>
            <textarea v-model="settingEditor.item.description" class="prompt-text" style="width: 100%; min-height: 200px;" placeholder="详细描述..." @input="settingDirty = true"></textarea>
          </template>

          <div class="meta" style="margin-top: 8px;">
            {{ ((settingEditor.item.description || settingEditor.item.snippet || '')).length }} 字符
          </div>
        </div>
      </div>
    </div>

    <!-- 章节大纲（B2） -->
    <!-- 分章大纲（章节蓝图编辑器 P2.2） -->
    <section v-if="tab === 'outline_chapters'" class="card">
      <ChapterBlueprint @toast="say" />
    </section>

    <!-- 收件箱 -->
    <section v-if="tab === 'inbox' && state" class="card">
      <div class="card-head">
        <h3>审批收件箱</h3>
        <span v-if="gateJustHit" class="pill st-gate">刚到达</span>
        <button class="mini" @click="requestNotifyPermission" title="允许浏览器发送审批门通知">🔔 通知权限</button>
        <span class="meta">{{ gateNotify ? '已开启' : '已关闭' }}（点击切换）</span>
      </div>
      <div v-if="!pendingGates.length" class="empty">暂无待审项 —— 审批门阶段（2 大纲 / 6 润色）完成并等待确认时会出现在这里</div>
      <div v-for="s in pendingGates" :key="s.stage" class="gate-card">
        <div class="gate-head">
          <span class="stage-no">{{ s.stage }}</span>
          <b>{{ STAGE_NAMES[s.stage] }}</b>
          <span class="pill st-done">完成</span>
          <span class="spacer"></span>
          <button class="mini primary" @click="approve(s.stage)">确认放行</button>
          <button class="mini" v-if="s.stage === 2" @click="openOutlineTab">查看结构化大纲</button>
          <button class="mini" v-if="s.stage === 2" @click="openMultiDraftDlg">多方案对比</button>
          <button class="mini" v-if="s.stage === 2" @click="refineOpen = true">精修意见</button>
          <button class="mini" v-if="s.stage === 6" @click="gotoFromInbox('chapters'); loadChapters()">章节润色稿</button>
          <button class="mini" v-if="s.stage === 6" @click="openRefineDiffDlg">逐章对比</button>
          <button class="mini danger" @click="openReject(s.stage)">打回</button>
        </div>
        <div class="gate-files">
          <button class="mini" v-if="s.stage === 2" @click="preview('data/outline/global.md')">预览原文</button>
          <button class="mini" v-if="s.stage === 2" @click="preview('data/outline/review_report.md')">体检报告</button>
          <button class="mini" v-if="s.stage === 6" @click="preview('data/outline/polish_report.md')">润色体检报告</button>
        </div>
        <!-- 产物摘要 -->
        <div v-if="gateArtifacts[s.stage]" class="gate-artifacts">
          <span class="meta">产物概况：</span>
          <span v-for="a in gateArtifacts[s.stage]" :key="a.path" class="gate-artifact-pill">
            {{ a.path.split('/').slice(-1)[0] }} ({{ a.lines }} 行)
            <span v-if="a.agent" class="agent-tag" title="此产物由外部 Agent 生成">⚡Agent</span>
          </span>
        </div>
        <!-- Stage 2: 大纲体检评分 -->
        <div v-if="s.stage === 2 && outlineSummary" class="gate-score">
          <span class="score-label">大纲评分:</span>
          <span class="score-val" :class="outlineSummary.score >= 70 ? 'ok' : outlineSummary.score >= 40 ? 'warn' : 'bad'">
            {{ outlineSummary.score }}/100
          </span>
          <span v-if="outlineSummary.issues?.length" class="score-issues">
            · {{ outlineSummary.issues.length }} 项待改进
          </span>
        </div>
      </div>
    </section>

    <!-- 成本 -->
    <section v-if="tab === 'cost'" class="card">
      <div class="card-head">
        <h3>成本中心</h3>
        <div class="view-toggle">
          <button :class="{ on: costView === 'cost' }" @click="costView = 'cost'">费用</button>
          <button :class="{ on: costView === 'usage' }" @click="costView = 'usage'">用量</button>
          <button :class="{ on: costView === 'rates' }" @click="costView = 'rates'; loadRates()">定价</button>
        </div>
        <button class="mini" @click="refreshCosts">刷新</button>
      </div>

      <!-- 当前运行：实时花费 / 已用 token / 预算进度（每 2.5s 随 state 刷新） -->
      <div v-if="streamingCost" class="run-cost">
        <div class="run-cost-head">
          <span class="run-dot" v-if="streamingCost.running"></span>
          <b>{{ streamingCost.running ? "当前运行" : "最近一次运行" }}</b>
          <span v-if="streamingCost.kind" class="pill st-running">{{ streamingCost.kind }}</span>
          <span v-if="streamingCost.run_id" class="meta">
            run #{{ streamingCost.run_id }} · {{ streamingCost.run_status || "-" }}
          </span>
          <span class="spacer"></span>
          <span class="meta" v-if="streamingCost.running">已运行 {{ fmtDur(streamingCost.elapsed_s) }}</span>
          <span class="meta" v-else>当前无任务在跑</span>
        </div>
        <div class="stat-row" style="margin-top: 8px;">
          <div class="cost-stat">
            <div class="stat-label">本次花费</div>
            <div class="stat-val">{{ fmtYuan(streamingCost.spent_yuan) }}</div>
            <div class="stat-sub">限额 {{ fmtYuan(streamingCost.limit_yuan) }}</div>
          </div>
          <div class="cost-stat">
            <div class="stat-label">本次 token（入 / 出）</div>
            <div class="stat-val sm">{{ fmtNum(streamingCost.tokens_in) }} / {{ fmtNum(streamingCost.tokens_out) }}</div>
            <div class="stat-sub">cache_read {{ fmtNum(streamingCost.cache_read) }}</div>
          </div>
          <div class="cost-stat">
            <div class="stat-label">预算剩余</div>
            <div class="stat-val">{{ fmtYuan(streamingCost.budget_left) }}</div>
            <div class="stat-sub">
              {{ streamingCost.calls }} 次调用<template v-if="streamingCost.estimated_calls">（含估算 {{ streamingCost.estimated_calls }}）</template>
            </div>
          </div>
        </div>
        <div class="cost-progress">
          <div class="cost-progress-bar" :class="costBarClass" :style="{ width: costBarPct + '%' }"></div>
        </div>
        <div class="meta">
          已用 {{ costBarPct.toFixed(2) }}% 预算 · 预警线 {{ (Number(streamingCost.warn_ratio || 0.7) * 100).toFixed(0) }}%
          <span v-if="streamingCost.yield_yuan_per_min"> · 平均 {{ fmtYuan(streamingCost.yield_yuan_per_min) }}/分钟</span>
        </div>
      </div>

      <div v-if="costSummary" class="cost-cards">
        <div class="cost-stat">
          <div class="stat-label">累计费用</div>
          <div class="stat-val">{{ fmtYuan(costSummary.totals.cost_yuan) }}</div>
          <div class="stat-sub">预算 {{ fmtYuan(state?.cost.limit_yuan || 0) }}</div>
        </div>
        <div class="cost-stat">
          <div class="stat-label">输入 token</div>
          <div class="stat-val">{{ fmtNum(costSummary.totals.tokens_in) }}</div>
          <div class="stat-sub">含 cache_read {{ fmtNum(costSummary.totals.cache_read) }}</div>
        </div>
        <div class="cost-stat">
          <div class="stat-label">输出 token</div>
          <div class="stat-val">{{ fmtNum(costSummary.totals.tokens_out) }}</div>
          <div class="stat-sub">{{ costSummary.totals.calls }} 次调用</div>
        </div>
      </div>

      <!-- 按阶段 -->
      <div v-if="costSummary && costSummary.by_stage.length" style="margin-top: 14px;">
        <h4>按阶段</h4>
        <table class="cost-table">
          <thead>
            <tr><th>阶段</th><th>调用</th><th>入</th><th>cache</th><th>出</th><th v-if="costView==='cost'">费用</th><th v-else>估算</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in costSummary.by_stage" :key="s.stage">
              <td>{{ s.stage }} {{ s.name }}</td>
              <td>{{ s.calls }}</td>
              <td>{{ fmtNum(s.tokens_in) }}</td>
              <td>{{ fmtNum(s.cache_read) }}</td>
              <td>{{ fmtNum(s.tokens_out) }}</td>
              <td v-if="costView==='cost'">{{ fmtYuan(s.cost_yuan) }}</td>
              <td v-else>{{ s.estimated }}/{{ s.calls }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 按模型 -->
      <div v-if="costSummary && costSummary.by_model.length" style="margin-top: 14px;">
        <h4>按模型</h4>
        <table class="cost-table">
          <thead>
            <tr><th>模型</th><th>调用</th><th>入</th><th>cache</th><th>出</th><th v-if="costView==='cost'">费用</th><th v-else>估算</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in costSummary.by_model" :key="m.model">
              <td>{{ m.model }}</td>
              <td>{{ m.calls }}</td>
              <td>{{ fmtNum(m.tokens_in) }}</td>
              <td>{{ fmtNum(m.cache_read) }}</td>
              <td>{{ fmtNum(m.tokens_out) }}</td>
              <td v-if="costView==='cost'">{{ fmtYuan(m.cost_yuan) }}</td>
              <td v-else>{{ m.estimated }}/{{ m.calls }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h4 style="margin-top: 14px;">流水明细（最近 100 笔）</h4>
      <table v-if="costs.length" class="cost-table">
        <thead>
          <tr><th>时间</th><th>阶段</th><th>模型</th><th>入</th><th>出</th><th>费用</th><th>来源</th></tr>
        </thead>
        <tbody>
          <tr v-for="c in costs" :key="c.id">
            <td>{{ fmtTime(c.called_at) }}</td>
            <td>{{ c.stage }}</td>
            <td>{{ c.model }}</td>
            <td>{{ c.tokens_in }}</td>
            <td>{{ c.tokens_out }}</td>
            <td>{{ fmtYuan(c.cost_yuan) }}</td>
            <td>{{ c.estimated ? "估算" : "实测" }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">暂无记账</div>

      <!-- 定价编辑器 -->
      <div v-if="costView === 'rates'" style="margin-top: 16px;">
        <h4>模型定价表（元/百万 token）</h4>
        <div class="meta" style="margin-bottom: 8px;">
          自定义定价覆盖源码刊例价。留空则使用默认值。保存后立即生效。
        </div>
        <table class="cost-table">
          <thead>
            <tr><th>模型</th><th>输入</th><th>输出</th><th>缓存</th><th>来源</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="(rate, name) in rates" :key="name">
              <td><input v-model="rate.name" class="rate-input" /></td>
              <td><input v-model.number="rate.in" class="rate-input" type="number" step="0.01" /></td>
              <td><input v-model.number="rate.out" class="rate-input" type="number" step="0.01" /></td>
              <td><input v-model.number="rate.cache_read" class="rate-input" type="number" step="0.01" /></td>
              <td><span class="pill" :class="rate.source === 'custom' ? 'st-running' : 'st-done'">{{ rate.source === 'custom' ? '自定义' : '默认' }}</span></td>
              <td><button class="mini" @click="removeRate(name)">删除</button></td>
            </tr>
          </tbody>
        </table>
        <div style="display: flex; gap: 8px; margin-top: 8px; align-items: center;">
          <input v-model="newRateName" placeholder="模型名称（如 deepseek-v4-pro）" class="rate-input" style="max-width: 240px;" />
          <button class="mini" @click="addRate">+ 添加</button>
          <button class="mini primary" @click="saveRates">保存定价</button>
          <button class="mini" @click="loadRates">重置</button>
          <button class="mini" @click="toggleImport">📋 批量导入</button>
        </div>

        <!-- 批量导入：粘贴整张价格表 → 解析预览 → 确认（只覆盖同名条目） -->
        <div v-if="importOpen" class="rate-import">
          <div class="meta" style="margin-bottom: 6px;">
            粘贴整张价格表：每行「模型 输入价 输出价 [缓存价]」（分隔符任意），
            也支持 JSON 或直接从 <code>RATES</code> 复制的字面量。
            <b>先解析预览，确认无误再导入</b> —— 只覆盖同名条目，不会删除其他定价。
          </div>
          <textarea v-model="importText" class="rate-import-box" rows="6"
                    placeholder="kimi-k2.6  1.0  4.2  0.2&#10;deepseek-v4-pro  12  24  1"></textarea>
          <div style="display: flex; gap: 8px; margin-top: 6px; align-items: center;">
            <button class="mini" @click="previewImport" :disabled="importBusy">解析预览</button>
            <button class="mini primary" @click="confirmImport"
                    :disabled="importBusy || !importReport || !importReport.ok">确认导入</button>
            <span class="meta" v-if="importReport">
              格式 {{ importReport.format }} · 解析出 {{ importReport.count }} 条
            </span>
          </div>

          <div v-if="importReport">
            <div v-if="importReport.errors && importReport.errors.length" class="rate-import-err">
              <b>无法解析（{{ importReport.errors.length }} 项）——修正后才能导入：</b>
              <div v-for="(e, i) in importReport.errors" :key="'ie' + i">· {{ e }}</div>
            </div>
            <div v-if="importReport.warnings && importReport.warnings.length" class="rate-import-warn">
              <b>注意（{{ importReport.warnings.length }} 项）：</b>
              <div v-for="(w, i) in importReport.warnings" :key="'iw' + i">· {{ w }}</div>
            </div>
            <table class="cost-table" v-if="importReport.plan && importReport.plan.length">
              <thead>
                <tr><th>模型</th><th>动作</th><th>现有</th><th>导入后</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in importReport.plan" :key="'ir' + row.model">
                  <td>{{ row.model }}</td>
                  <td>
                    <span class="pill" :class="row.action === 'new' ? 'st-done' : 'st-running'">
                      {{ actionLabel(row.action) }}
                    </span>
                  </td>
                  <td class="meta">{{ fmtRate(row.from) }}</td>
                  <td>{{ fmtRate(row.to) }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="importReport.applied" class="meta" style="margin-top: 6px;">
              ✅ 已导入 {{ importReport.saved }} 条<span v-if="importReport.backup">（原文件已备份到 {{ importReport.backup }}）</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 设置 -->
    <section v-if="tab === 'settings' && models" class="card">
      <div class="card-head">
        <h3>引擎与模型（config/system.yaml）</h3>
        <button class="mini" @click="refreshModels" title="从 config/system.yaml 重载">刷新</button>
      </div>
      <!-- 模型使用免责声明（2026-10-04 用户指定文案，原样照录，静态展示，勿改写） -->
      <div class="model-disclaimer">若您的AI订阅仅可在编程工具（如 OpenClaw、OpenCode 等）中使用，请务必开启绒花墨坊的Agent模式，使用外部Agent调用绒花墨坊。否则您可能因以API调用的形式用于自动化脚本、自定义应用程序后端等被AI提供商封禁。因此造成的一切损失与绒花墨坊无关。若您继续使用绒花墨坊，则代表您已知悉并同意以上内容。</div>
      <div class="meta">
        engine: {{ models.engine }} · 下拉来源：{{ modelSource === 'fetched' ? '服务商动态拉取' : 'config 手写列表' }}
        <button class="mini" style="margin-left: 8px;" @click="refreshFetchedModels" :disabled="fetchingModels">
          {{ fetchingModels ? '同步中…' : '↻ 同步服务商模型' }}
        </button>
      </div>
      <div v-for="(m, role) in models.model" :key="role" class="art-row">
        <span class="pill st-done">{{ role }}</span>
        <span class="art-path">{{ m.provider }} / {{ m.id }}</span>
        <span class="spacer"></span>
        <select :value="m.id" @change="switchModel(role, $event.target.value)" class="model-select">
          <option v-for="opt in providerModelOptions(m.provider, m.id)" :key="opt" :value="opt">{{ opt }}</option>
        </select>
        <select v-model="m.provider" @change="switchProvider(role, m.provider)" class="provider-select">
          <option v-for="p in providerOptions" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="art-row" style="margin-top: 8px;">
        <span class="pill st-done">搜索模型</span>
        <input v-model="modelSearch" placeholder="输入关键词过滤..." class="model-input" />
        <button class="mini" @click="modelSearch = ''" v-if="modelSearch">×</button>
        <span class="meta" style="margin-left: auto;">全部 {{ modelOptions.length }} 个（下拉按该角色的供应商过滤）</span>
      </div>
      <div class="art-row" style="margin-top: 8px;">
        <span class="pill st-done">手动添加</span>
        <input v-model="newModelName" placeholder="输入模型名（如 kimi-k2.5）" class="model-input" />
        <button class="mini" @click="addManualModel">+ 添加</button>
      </div>
      <div class="meta" style="margin-top: 12px;">
        改模型：下拉切换后立即写入 config/system.yaml，下次运行阶段时生效。
        <br>architect=设定/世界观 | outliner=大纲 | writer=写作 | checker=检查 | reviewer=审核 | polisher=润色
        <br>⚠️ **换供应商后请顺手确认模型**：模型名不通用（换了 provider 却留着旧模型名 → 运行时直接 404）。
        下拉里的候选项来自该供应商的 <code>available_models</code>；清单里没有的模型可先「手动添加」。
      </div>

      <!-- 服务商与密钥状态 -->
      <h4 style="margin-top: 16px;">服务商与 API Key</h4>
      <div v-if="providers" v-for="(p, pid) in providers" :key="pid" class="provider-row">
        <span class="pill st-done">{{ pid }}</span>
        <span class="art-path">{{ p.base_url }}</span>
        <span class="spacer"></span>
        <span v-if="p.has_key" class="key-status ok">✓ 已配置 ({{ p.key_mask }})</span>
        <span v-else class="key-status bad">✗ 缺失 ({{ p.api_key_env }})</span>
        <button class="mini" v-if="!p.has_key" @click="startEnvEdit(pid)">填入 Key</button>
        <template v-else>
          <button class="mini" @click="startEnvEdit(pid)">替换</button>
          <button class="mini" @click="saveEnvKey(pid, true)" title="删除 .env 里这一行">清除</button>
        </template>
      </div>
      <div v-if="envEditing" class="art-row" style="margin-top: 8px;">
        <span class="pill st-done">写入 {{ (providers && providers[envEditing] && providers[envEditing].api_key_env) || '' }}</span>
        <input type="password" v-model="envValue" placeholder="粘贴 API Key（不回显已存值）"
               class="model-input" style="max-width: 320px;"
               @keyup.enter="saveEnvKey(envEditing)" />
        <button class="mini primary" @click="saveEnvKey(envEditing)">保存</button>
        <button class="mini" @click="cancelEnvEdit">取消</button>
      </div>
      <div class="meta" style="margin-top: 8px;">
        Key 写入项目 <code>.env</code>（不进 git、界面内从不回显明文）；保存后<b>立即生效、无需重启</b>；已存值只显示前后各几位。
      </div>
      <div style="display: flex; gap: 8px; margin-top: 8px; align-items: center;">
        <button class="mini" @click="envConfirmOpen = true" title="在资源管理器中定位 .env（只定位，不代为打开；点击后需确认）">在文件夹中显示</button>
      </div>
      <div class="meta" style="margin-top: 4px;">
        <b>配置 Key 只需点上面的「填入 Key」</b>，不必手动编辑文件。.env 含明文密钥，
        故刻意不提供「用外部编辑器一键打开」（编辑器插件 / AI 工具可能读取）；
        确需手动编辑时，用「在文件夹中显示」定位后自行打开。
      </div>
      <div class="art-row" style="margin-top: 10px;">
        <span class="pill st-done">免责声明</span>
        <span class="meta">AI 订阅合规 · 内容权属 · 风险自担 · 首次使用需确认</span>
        <span class="spacer"></span>
        <button class="mini" @click="openDisclaimer('view')">查看完整声明</button>
      </div>

      <!-- P1: 审批门通知 -->
      <h4 style="margin-top: 16px;">审批门通知</h4>
      <div class="gate-notify-row">
        <div>
          <div class="label">浏览器通知 + 自动跳转收件箱</div>
          <div class="meta">审批门到达（阶段 2/6 完成未确认）时，自动切到收件箱页签并弹浏览器通知（需授权）。可关闭。</div>
        </div>
        <button class="mini" :class="{ primary: gateNotify }" @click="gateNotify = !gateNotify; setGateNotify(gateNotify)">
          {{ gateNotify ? '已开启' : '已关闭' }}
        </button>
      </div>

      <!-- 止烧阈值（token 级熔断）：hermes 下金额阈值无效，止烧全靠这几个值 -->
      <h4 style="margin-top: 16px;">预算与止烧（token 级熔断）</h4>
      <div class="meta" style="line-height: 1.8; margin-bottom: 8px;">
        engine: hermes 走订阅流量，<b>金额阈值恒不生效</b>（记账恒 0）—— 止烧靠这里的 token 上限。
        越界值会被拒绝（设成 0/负数等于让闸门消失）。改动对<b>下一次运行</b>生效。
      </div>
      <div v-if="tokenLimit.loading" class="meta">读取中…</div>
      <template v-else>
        <div class="tl-grid">
          <label class="tl-field">
            <span class="label">启用 token 级熔断</span>
            <input type="checkbox" v-model="tokenLimit.form.enabled" />
          </label>
          <label class="tl-field">
            <span class="label">单请求输出上限（token）</span>
            <input type="number" v-model="tokenLimit.form.per_request_max_tokens" />
            <span class="meta">区间 {{ tokenLimit.bounds.per_request_max_tokens?.join(' ~ ') }}</span>
          </label>
          <label class="tl-field">
            <span class="label">单轮累计上限（token）</span>
            <input type="number" v-model="tokenLimit.form.max_total_tokens" />
            <span class="meta">区间 {{ tokenLimit.bounds.max_total_tokens?.join(' ~ ') }}</span>
          </label>
          <label class="tl-field">
            <span class="label">预警比例</span>
            <input type="number" step="0.05" v-model="tokenLimit.form.warn_ratio" />
            <span class="meta">区间 {{ tokenLimit.bounds.warn_ratio?.join(' ~ ') }}</span>
          </label>
          <label class="tl-field" style="grid-column: 1 / -1;">
            <span class="label">hermes 单次子会话超限也熔断</span>
            <span style="display: flex; gap: 8px; align-items: center;">
              <input type="checkbox" v-model="tokenLimit.form.per_request_pause_hermes" />
              <span class="meta">默认关：实测一次 stage1 子会话输出就有 1.8 万 token，
                单次判据更容易误停（误停比不停更贵）。</span>
            </span>
          </label>
        </div>
        <div style="display: flex; gap: 8px; align-items: center; margin-top: 10px;">
          <button class="mini primary" :disabled="tokenLimit.busy" @click="saveTokenLimit">
            {{ tokenLimit.busy ? '保存中…' : '保存阈值' }}
          </button>
          <button class="mini" :disabled="tokenLimit.busy" @click="resetTokenLimit">恢复默认预设</button>
          <span class="meta">预设：单请求 {{ tokenLimit.preset.per_request_max_tokens }} ·
            累计 {{ tokenLimit.preset.max_total_tokens }}（{{ tokenLimit.preset.enabled ? '启用' : '停用' }}）</span>
        </div>
        <div v-if="tokenLimit.msg" class="meta tl-msg"
             :style="{ color: tokenLimit.ok ? 'var(--ok)' : 'var(--bad)', marginTop: '6px' }">
          {{ tokenLimit.msg }}
        </div>
      </template>

      <!-- P1: Agent 模式开关 -->
      <h4 style="margin-top: 16px;">Agent 模式（外部 Agent 控制）</h4>
      <div class="gate-notify-row">
        <div>
          <div class="label">允许外部 Agent 通过 HTTP API 调用</div>
          <div class="meta">开启后，Hermes 等外部 Agent 可调用绒花墨坊 API 进行大纲草拟、设定起草等操作。<br>⚠️ 禁止性操作（审批/打回/归档/恢复/项目创建）仍只允许在 GUI 内手动执行。<br>Agent 生成的大纲/产物自动进入待审批状态，需在桌面端确认。</div>
        </div>
        <button class="mini" :class="{ primary: agentMode }" @click="toggleAgentMode">
          {{ agentMode ? '已开启' : '已关闭' }}
        </button>
      </div>

      <!-- Agent 派发状态（P1-9: GUI 观测面） -->
      <div v-if="agentMode" style="margin-top: 8px;">
        <div class="label">最近派发任务</div>
        <div v-if="agentRuns.length === 0" class="meta">暂无派发记录</div>
        <div v-else>
          <div v-for="run in agentRuns.slice(0, 5)" :key="run.run_id"
               style="display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--border);">
            <span :class="['pill', run.status === 'done' ? 'st-ok' : run.status === 'error' ? 'st-failed' : 'st-running']">
              {{ run.status }}
            </span>
            <span class="meta" style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
              {{ run.title || run.run_id }}
            </span>
            <span class="meta">{{ run.created_at?.slice(11, 19) || '' }}</span>
          </div>
        </div>
      </div>

      <!-- 接入其他 Agent（2026-10-06 对齐方寸「接入页」）：MCP 配置段 + kickoff -->
      <h4 style="margin-top: 16px;">接入其他 Agent（MCP 配置生成）</h4>
      <div class="meta" style="margin-bottom: 8px;">
        标准 MCP 客户端（Hermes / Claude Code / Cline / Cursor）配置一次即可接入；
        后端未启动时垫片会<b>自动代拉</b> nf_api（逃生门：环境变量
        <code>NF_BRIDGE_NO_AUTOSTART=1</code>）。审批/打回等红线操作<b>不经过 MCP</b>，
        外部 Agent 拿不到 —— 这是设计，不是故障。
        <template v-if="agentConnect">
          <br>MCP 8766：
          <b :style="{ color: agentConnect.mcpListening ? 'var(--ok)' : 'var(--warn)' }">
            {{ agentConnect.mcpListening ? '监听中' : '未监听（客户端连接时自动拉起）' }}
          </b>
          · 运行形态：{{ agentConnect.packaged ? '安装版' : '源码版' }} v{{ agentConnect.version }}
        </template>
      </div>
      <div v-if="agentConnect" class="ac-block">
        <div class="ac-tabs">
          <button class="mini" :class="{ primary: acTab === 'hermes' }"
                  @click="acTab = 'hermes'">Hermes（config.yaml · YAML）</button>
          <button class="mini" :class="{ primary: acTab === 'json' }"
                  @click="acTab = 'json'">Claude Code / Cline（JSON）</button>
        </div>
        <pre class="ac-code">{{ acTab === 'hermes' ? agentConnect.yamlSnippet : agentConnect.jsonSnippet }}</pre>
        <div class="ac-actions">
          <button class="mini primary" @click="copyAcSnippet">复制配置段</button>
          <button v-if="acTab === 'hermes'" class="mini" @click="openAgentCfg('hermes')">
            打开 Hermes 配置文件
          </button>
          <template v-else>
            <button class="mini" @click="openAgentCfg('claude')">打开 Claude 配置</button>
            <button class="mini" @click="openAgentCfg('cline')">打开 Cline 配置</button>
          </template>
        </div>
        <div class="meta" style="margin-top: 4px; word-break: break-all;">
          配置文件：<code>{{ acTab === 'hermes' ? agentConnect.hermesConfig
            : (acTab === 'claude' ? agentConnect.claudeConfig : agentConnect.clineConfig) }}</code><br>
          配置段里的路径按当前运行形态生成（安装版 = 安装目录资源，源码版 = 本仓库 .venv）；
          粘进对应客户端的 MCP 配置后重启客户端生效。
        </div>

        <div class="ac-kickoff">
          <div class="label" style="margin-top: 12px;">
            启动提示词（kickoff）—— 复制给外部 Agent 作开场白
          </div>
          <div class="meta">
            单一事实源 <code>prompts/agent_kickoff.md</code>；复制时自动把
            <code>&#123;&#123;PROJECT_ROOT&#125;&#125;</code> 占位符替换为项目根。
            防「AI 从头重建本项目 / 擅自绕过审批守卫」的第一道闸。
          </div>
          <textarea class="prompt-text" style="min-height: 150px; margin-top: 6px;"
                    :value="acKickoff" readonly spellcheck="false"
                    placeholder="读取中…（prompts/agent_kickoff.md）"></textarea>
          <div class="ac-actions" style="margin-top: 6px;">
            <button class="mini primary" :disabled="!acKickoff"
                    @click="copyKickoff">复制启动提示词</button>
            <button class="mini" @click="loadKickoff">重新读取</button>
          </div>
        </div>
      </div>

      <!-- 用户风格笔记 -->
      <h4 style="margin-top: 16px;">用户风格笔记（book.style_notes）</h4>
      <div class="meta" style="margin-bottom: 8px;">
        手动补充的风格要求，与范文自动分析结论叠加注入写作（阶段4）与润色（阶段6）；
        冲突时以本节为准。留空则完全不注入。
      </div>
      <textarea class="prompt-text" style="min-height: 110px;" v-model="styleNotes"
                @input="styleNotesDirty = true" spellcheck="false"
                placeholder="例如：多用短句，少用形容词；对话带点方言味，避免书面腔…"></textarea>
      <div class="provider-row" style="margin-top: 6px;">
        <span class="meta">共 {{ styleNotes.length }} 字符（上限 4000）</span>
        <span v-if="styleNotesDirty" class="pill st-gate">已修改</span>
        <span class="spacer"></span>
        <button class="mini" @click="loadStyleNotes">重新读取</button>
        <button class="mini primary" :disabled="!styleNotesDirty" @click="saveStyleNotes">保存笔记</button>
      </div>

      <!-- 提示词模板编辑器 -->
      <h4 style="margin-top: 16px;">提示词模板（prompts/stage[1-7]_*.md）</h4>
      <div class="meta" style="margin-bottom: 8px;">
        直接编辑阶段提示词；保存前自动备份旧版到 prompts/history/，下次运行对应阶段生效。
      </div>
      <div class="prompt-editor">
        <div class="prompt-list">
          <div v-for="p in promptFiles" :key="p.name"
               class="prompt-item" :class="{ on: p.name === promptName }"
               :title="p.name + ' · ' + p.modified"
               @click="openPrompt(p.name)">
            <span class="pill st-done">S{{ p.stage }}</span>
            <span class="prompt-name">{{ p.name }}</span>
            <span class="spacer"></span>
            <span class="prompt-size">{{ p.size }}B</span>
          </div>
          <div v-if="!promptFiles.length" class="empty">未发现模板文件</div>
        </div>
        <div class="prompt-main">
          <div class="prompt-bar">
            <b>{{ promptName || "未选择模板" }}</b>
            <span v-if="promptDirty" class="pill st-gate">已修改</span>
            <span v-if="promptLoading" class="pill st-running">读取中</span>
            <span v-if="promptModified" class="prompt-size">最后修改 {{ promptModified }}</span>
            <span class="spacer"></span>
            <button class="mini" :disabled="!promptName || promptLoading" @click="loadPromptList">刷新列表</button>
            <button class="mini" :disabled="!promptName || promptLoading" @click="openPrompt(promptName)">放弃修改</button>
            <button class="mini primary" :disabled="!promptName || !promptDirty" @click="savePrompt">保存</button>
          </div>
          <textarea class="prompt-text" v-model="promptContent" @input="promptDirty = true"
                    :disabled="!promptName" spellcheck="false"
                    placeholder="从左侧列表选择一个模板文件…"></textarea>
          <div class="meta" style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
            <span>共 {{ promptContent.length }} 字符</span>
            <template v-if="promptBackups.length">
              <span>· 历史备份 {{ promptBackups.length }} 份（最多留 50 份）</span>
              <select v-model="promptBackupSel" class="model-select" style="max-width: 260px;">
                <option value="">选择要回滚的版本…</option>
                <option v-for="b in promptBackups.slice().reverse()" :key="b" :value="b">{{ b }}</option>
              </select>
              <button class="mini" :disabled="!promptBackupSel"
                      @click="restorePrompt(promptBackupSel)">回滚到此版本</button>
            </template>
            <span v-else>· 暂无历史备份（首次保存后才会有）</span>
          </div>
        </div>
      </div>

      <!-- 外观配置 -->
      <h4 style="margin-top: 16px;">外观配置</h4>
      <div class="meta" style="margin-bottom: 8px;">主题与字号即时生效，自动保存。</div>

      <div class="bp-field">
        <label>主题颜色</label>
        <div class="theme-swatches">
          <button
            v-for="t in THEMES"
            :key="t.id"
            class="theme-swatch"
            :class="{ on: theme === t.id }"
            :style="{ background: t.color }"
            :title="t.name"
            @click="setTheme(t.id)"
          >
            <span v-if="theme === t.id" class="theme-check">✓</span>
          </button>
        </div>
      </div>

      <div class="bp-field">
        <label>字号（{{ fontSize }}px）</label>
        <div class="font-size-control">
          <button class="mini" @click="setFontSize(Math.max(10, fontSize - 1))">A-</button>
          <input
            type="range"
            min="10"
            max="20"
            :value="fontSize"
            @input="setFontSize(parseInt($event.target.value))"
            class="font-slider"
          />
          <button class="mini" @click="setFontSize(Math.min(20, fontSize + 1))">A+</button>
        </div>
      </div>

      <!-- 自动更新 -->
      <h4 style="margin-top: 16px;">自动更新</h4>
      <div class="provider-row">
        <span class="pill st-done">electron-updater</span>
        <span class="art-path">{{ updateStatus }}</span>
        <span class="spacer"></span>
        <button class="mini" @click="checkForUpdates" :disabled="checkingUpdate"
                :title="updaterDev ? '开发模式不支持自动更新（仅安装版可用）' : '向 GitHub 查询是否有新版本'">
          {{ checkingUpdate ? "检查中…" : "检查更新" }}
        </button>
        <button class="mini" @click="openReleases" title="在浏览器打开 GitHub Releases 手动下载">下载新版本</button>
        <button v-if="updateReady" class="mini primary" @click="quitAndInstall">重启安装</button>
      </div>
      <div v-if="updaterDev || !updaterInitialized" class="meta" style="margin-top: 4px; opacity: 0.75;">
        本机不支持自动更新（{{ updaterDev ? "开发模式" : "便携版 / updater 未初始化" }}）；
        请用「下载新版本」手动更新 —— 数据都在本机工作区，覆盖安装不会丢。
      </div>
      <div v-else-if="!updateReady" class="meta" style="margin-top: 4px; opacity: 0.75;">
        自动更新走 GitHub Releases：检查到新版本会自动下载，完成后这里出现「重启安装」。
      </div>
      <div v-if="updateProgress > 0 && updateProgress < 100" class="meta" style="margin-top: 4px;">
        下载进度: {{ updateProgress.toFixed(1) }}%
      </div>

      <!-- 诊断与调试（2026-10-06 对齐方寸「🩺 诊断」—— 像样的调试入口） -->
      <h4 style="margin-top: 16px;">诊断与调试</h4>
      <div class="meta" style="line-height: 1.8; margin-bottom: 8px;">
        运行形态：{{ dbg ? (dbg.packaged ? '安装版' : '源码版') + ' v' + dbg.version : '读取中…' }}
        · 后端：<b :style="{ color: dbg && dbg.apiHealthy ? 'var(--ok)' : 'var(--bad)' }">
          {{ dbg ? (dbg.apiHealthy ? '在线' : '离线') : '…' }}</b>
        <template v-if="dbg">
          · 子进程 PID {{ dbg.apiChildPid || '无（端口可能由外部进程服务）' }}
          · 项目根来源 {{ dbg.projectRootSource }}
        </template>
        <br>代码根：<code>{{ dbg ? dbg.codeRoot : '-' }}</code>
        <br>数据根：<code>{{ dbg ? dbg.projectRoot : '-' }}</code>
        <br>日志：<code>{{ dbg ? dbg.logPath : '-' }}</code>
      </div>
      <div class="ac-actions" style="margin-top: 0;">
        <button class="mini" @click="openRunLog">查看运行日志（400 行）</button>
        <button class="mini" @click="openBackendLog">打开日志文件</button>
        <button class="mini" @click="loadDebugInfo">刷新诊断</button>
        <button class="mini" @click="copyDiagnostics">复制诊断信息</button>
        <button class="mini" @click="toggleDevtools" title="快捷键 Ctrl+Shift+I 或 F12">
          DevTools 开关
        </button>
        <button class="mini" :disabled="isRunning" @click="restartBackend"
                :title="isRunning ? '流水线运行中，重启会打断当前任务' : '杀掉并重启 nf_api 子进程（约 3-15 秒）'">
          重启后端 API
        </button>
      </div>
      <div class="meta" style="margin-top: 4px; opacity: 0.75;">
        排障顺序：先「查看运行日志」→ 拿「复制诊断信息」贴给协助者 →
        仍不行再「重启后端 API」。DevTools 看渲染层（前端报错、网络请求）。
      </div>

      <div class="meta" style="margin-top: 12px;">项目目录: {{ state ? state.project_dir : "-" }}</div>
    </section>

    <!-- 导出面板（P3 多平台发布） -->
    <section v-if="tab === 'export'" class="card">
      <div class="card-head">
        <h3>多平台导出</h3>
        <span class="meta">Markdown 分卷 + Word 成品</span>
      </div>

      <div class="export-grid">
        <!-- 导出配置 -->
        <div class="export-panel">
          <h4>导出配置</h4>

          <div class="bp-field">
            <label>导出格式</label>
            <select v-model="exportCfg.format" class="bp-select">
              <option value="both">Markdown + Word（双格式）</option>
              <option value="md">仅 Markdown</option>
              <option value="docx">仅 Word</option>
            </select>
          </div>

          <div v-if="exportCfg.format !== 'docx'" class="bp-field">
            <label>每卷章节数（仅 Markdown 分卷）</label>
            <input v-model.number="exportCfg.volSize" type="number" min="1" max="30" class="bp-input" />
          </div>

          <div class="bp-field">
            <label>
              <input type="checkbox" v-model="exportCfg.includeFrontmatter" />
              包含 frontmatter 元数据
            </label>
          </div>

          <button class="mini primary" :disabled="exporting" @click="runExport">
            {{ exporting ? '导出中...' : '开始导出' }}
          </button>

          <div v-if="exportMsg" class="meta" style="margin-top: 8px;">{{ exportMsg }}</div>
        </div>

        <!-- 最近导出 -->
        <div class="export-panel">
          <h4>最近导出</h4>
          <div v-if="!exportHistory.length" class="bp-empty">暂无导出记录</div>
          <div v-for="h in exportHistory" :key="h.time" class="export-history-item">
            <span class="export-time">{{ h.time }}</span>
            <span class="export-meta">{{ h.volumes }} 卷 / {{ h.chapters }} 章 · {{ h.format }}</span>
            <button class="mini" @click="openExportDir(h.path)">打开目录</button>
          </div>
        </div>
      </div>
    </section>

    <!-- 素材管理（A1 P0） -->
    <section v-if="tab === 'materials'" class="card">
      <div class="card-head">
        <h3>素材管理（materials/raw/）</h3>
        <span class="meta">{{ materials.length }} 个素材</span>
        <span class="spacer"></span>
        <button class="mini" @click="addMaterial">添加素材</button>
        <button class="mini" @click="newMaterialOpen = !newMaterialOpen">新建素材</button>
        <button class="mini primary" @click="triggerReMerge">触发重归并</button>
        <button class="mini" @click="loadMaterials">刷新</button>
      </div>

      <div class="tabs-sub">
        <button :class="{ on: materialSub === 'cards' }" @click="materialSub = 'cards'">结构化卡片</button>
        <button :class="{ on: materialSub === 'scraps' }" @click="materialSub = 'scraps'">
          原始碎片
        </button>
      </div>

      <div v-if="materialSub === 'cards'">
        <div class="meta" style="margin-bottom: 12px;">
          目录: {{ materialDir }}
        </div>

      <!-- 新建素材表单 -->
      <div v-if="newMaterialOpen" style="margin-bottom: 16px; padding: 12px; border: 1px solid var(--border); border-radius: 8px;">
        <h4>新建素材</h4>
        <div class="art-row" style="margin: 8px 0;">
          <span class="art-label">文件名</span>
          <input v-model="newMaterialName" class="text-input" placeholder="如：新角色_角色卡.md" />
        </div>
        <textarea class="prompt-text" style="min-height: 120px;" v-model="newMaterialContent"
                  spellcheck="false" placeholder="素材正文（Markdown / 纯文本）"></textarea>
        <div class="provider-row" style="margin-top: 6px;">
          <span class="meta">须以 .md 或 .txt 结尾</span>
          <span class="spacer"></span>
          <button class="mini" @click="newMaterialOpen = false">取消</button>
          <button class="mini primary" @click="createMaterial">创建</button>
        </div>
      </div>

      <!-- 素材列表 -->
      <div v-if="!materials.length" class="empty">
        <div style="margin-bottom: 12px;">暂无素材 —— 点击"添加素材"从其他地方复制文件到 raw/，或"新建素材"直接创建。</div>
        <div style="background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px;">
          <div style="font-weight: 600; margin-bottom: 8px;">示例素材卡格式</div>
          <pre style="margin: 0; font-size: 12px; line-height: 1.6; color: var(--muted-foreground); overflow-x: auto;"># 角色：角色名

> 来源：你的设定库路径

## 基本信息

- 正式名称：xxx
- 种族：xxx
- 性别：xxx

## 外貌与生活

- 外貌描述...

## 人际关系

- 角色A：关系说明</pre>
        </div>
        <div class="meta">快速体验：复制 examples/sample-book/materials/* 到 materials/raw/</div>
      </div>
      <table v-if="materials.length" class="cost-table">
        <thead>
          <tr><th>名称</th><th>类型</th><th>大小</th><th>修改时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in materials" :key="m.name">
            <td><span style="margin-right: 6px;">{{ materialIcon(m) }}</span>{{ m.name }}</td>
            <td>{{ materialKindLabel(m) }}{{ m.ext }}</td>
            <td>{{ fmtBytes(m.size) }}</td>
            <td>{{ m.modified }}</td>
            <td>
              <button class="mini" v-if="m.kind === 'text'" @click="openMaterialEdit(m.name)">编辑</button>
              <button class="mini" @click="openArtifact('materials/raw/' + m.name)">打开</button>
              <button class="mini danger" @click="deleteMaterial(m.name)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      </div>

      <!-- 原始碎片（自由命名 / 私人数据 / 不进 Git）→ 勾选信息点 → 一键生成素材卡 -->
      <ScrapsPanel v-else />
    </section>
    <!-- 项目（书籍切换） -->
    <section v-if="tab === 'project'" class="card">
      <div class="card-head">
        <h3>项目管理</h3>
        <button class="mini" @click="loadProjects">刷新</button>
        <span class="spacer"></span>
        <button class="mini primary" @click="openNewProject" title="一键新建：归档当前 → 建空工作区 → 写 project.yaml">
          ＋ 新建项目
        </button>
      </div>
      <div class="meta" style="margin-bottom: 12px;">
        当前项目：<b>{{ projects.current || "（未初始化）" }}</b>
        <span v-if="state?.has_work" class="pill st-gate">工作区有数据</span>
        <span v-else class="pill">工作区为空</span>
      </div>
      <div v-if="projects.current" class="art-row">
        <span class="art-label">归档当前项目</span>
        <input v-model="archiveName" class="text-input" placeholder="留空使用当前书名" />
        <span class="spacer"></span>
        <button class="mini" @click="archiveCurrent">归档</button>
      </div>
      <div v-if="projects.archived.length" style="margin-top: 14px;">
        <h4>已归档项目</h4>
        <div v-for="b in projects.archived" :key="b.name" class="art-row">
          <span class="pill st-done">{{ b.display_name }}</span>
          <span class="art-path">{{ b.items }} 项 / {{ b.size_kb }} KB · {{ b.archived_at }}</span>
          <span class="spacer"></span>
          <button class="mini primary" @click="restoreProject(b.name)">恢复</button>
        </div>
      </div>
      <div v-else class="empty">暂无归档项目</div>
      <div class="meta" style="margin-top: 14px; line-height: 1.9;">
        <b>新建项目</b>：点右上角「＋ 新建项目」，填书名/类型/章数即可 ——
        系统会自动快照、归档当前项目、建好空工作区并写好 <code>config/project.yaml</code>。<br>
        也可以手工改 <code>config/project.yaml</code> 的 <code>book.name</code>（高级用法，不推荐）。
      </div>
    </section>
  </main>

  <!-- 素材编辑抽屉 -->

  <div v-if="materialEdit" class="drawer-mask" @click.self="materialEdit = null">
    <div class="drawer" style="width: min(1000px, 92vw);">
      <div class="drawer-head">
        <b>{{ materialEdit.name }}</b>
        <span v-if="materialEditDirty" class="pill st-gate" style="margin-left: 8px;">已修改</span>
        <span class="spacer"></span>
        <button class="mini" @click="loadMaterials">刷新列表</button>
        <button class="mini" @click="materialEdit = null">关闭</button>
      </div>
      <textarea class="prompt-text" style="min-height: 60vh; font-family: var(--mono);"
                v-model="materialEdit.content"
                @input="materialEditDirty = true"
                spellcheck="false"></textarea>
      <div class="provider-row" style="margin-top: 6px;">
        <span class="meta">{{ materialEdit.content.length }} 字符</span>
        <span class="spacer"></span>
        <button class="mini" @click="openMaterialEdit(materialEdit.name)">放弃修改</button>
        <button class="mini primary" :disabled="!materialEditDirty" @click="saveMaterialEdit">保存</button>
      </div>
    </div>
  </div>

  <!-- 文档阅读器 -->
  <div v-if="viewDoc || previewPath" class="drawer-mask" @click.self="viewDoc = null; previewPath = ''">
    <div class="drawer">
      <div class="drawer-head">
        <b>{{ viewDoc ? viewDoc.title : previewPath }}</b>
        <span class="spacer"></span>
        <button v-if="previewPath" class="mini" @click="openArtifact(previewPath)">打开所在目录</button>
        <button class="mini" @click="viewDoc = null; previewPath = ''">关闭</button>
      </div>
      <pre class="preview-body">{{ viewDoc ? viewDoc.content : previewText }}</pre>
    </div>
  </div>

  <!-- 润色对比（段落 diff） -->
  <div v-if="diffView" class="drawer-mask" @click.self="diffView = null">
    <div class="drawer" style="width: min(1280px, 96vw);">
      <div class="drawer-head">
        <b>{{ diffView.title }}</b>
        <span class="spacer"></span>
        <button class="mini" @click="diffView = null">关闭</button>
      </div>
      <div class="diff-view">
        <div class="diff-col">
          <div class="diff-head">原稿</div>
          <div class="diff-body">
            <template v-if="diffComputed.length > 0">
              <div v-for="(p, i) in diffComputed" :key="i" 
                   :class="['diff-para', p.type === 'del' ? 'para-del' : p.type === 'add' ? 'para-empty' : '']">
                <span v-if="p.type === 'del' || p.type === 'same'">{{ p.content }}</span>
                <span v-else class="para-empty-mark">-</span>
              </div>
            </template>
            <div v-else class="diff-para">{{ diffView.raw }}</div>
          </div>
        </div>
        <div class="diff-col">
          <div class="diff-head">润色稿</div>
          <div class="diff-body">
            <template v-if="diffComputed.length > 0">
              <div v-for="(p, i) in diffComputed" :key="i" 
                   :class="['diff-para', p.type === 'add' ? 'para-add' : p.type === 'del' ? 'para-empty' : '']">
                <span v-if="p.type === 'add' || p.type === 'same'">{{ p.content }}</span>
                <span v-else class="para-empty-mark">-</span>
              </div>
            </template>
            <div v-else class="diff-para">{{ diffView.refined }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 运行前 token / 费用预估确认 -->
  <div v-if="estDialog.open" class="drawer-mask">
    <div class="dialog" style="width: min(760px, 94vw);">
      <h3>运行前预估</h3>
      <div class="meta" style="margin-bottom: 10px;">
        {{ estDialog.title }} —— 以下是本次预计消耗，确认后才真正开跑。
      </div>

      <div v-if="estDialog.loading" class="empty">正在估算…</div>

      <template v-else-if="estDialog.data">
        <div class="est-totals">
          <div class="cost-stat">
            <div class="stat-label">预计调用</div>
            <div class="stat-val">{{ estDialog.data.totals.calls }}</div>
            <div class="stat-sub">次 LLM 请求</div>
          </div>
          <div class="cost-stat">
            <div class="stat-label">输入 token</div>
            <div class="stat-val">{{ fmtTok(estDialog.data.totals.tokens_in) }}</div>
            <div class="stat-sub">口径：{{ { history: '历史实测均值', heuristic: '字符折算', mixed: '实测 + 折算' }[estDialog.data.basis_source] || estDialog.data.basis_source }}</div>
          </div>
          <div class="cost-stat">
            <div class="stat-label">输出 token</div>
            <div class="stat-val">{{ fmtTok(estDialog.data.totals.tokens_out) }}</div>
            <div class="stat-sub">预计生成量</div>
          </div>
          <div class="cost-stat">
            <div class="stat-label">预计费用</div>
            <div class="stat-val" :class="{ 'bad-val': estDialog.data.budget.exceeds }">
              {{ fmtYuan(estDialog.data.totals.cost_yuan) }}
            </div>
            <div class="stat-sub" :class="{ 'bad-val': estDialog.data.budget.exceeds }">
              跑完 {{ fmtYuan(estDialog.data.budget.projected_yuan) }}
              / 上限 {{ fmtYuan(estDialog.data.budget.limit_yuan) }}
              （{{ estDialog.data.budget.projected_pct }}%{{ estDialog.data.budget.exceeds ? " ⚠ 超预算" : "" }}）
            </div>
          </div>
        </div>

        <div class="est-bar">
          <div class="est-bar-in" :style="{ width: Math.min(100, estDialog.data.budget.projected_pct) + '%' }"
               :class="{ over: estDialog.data.budget.exceeds }"></div>
        </div>

        <table class="cost-table" style="margin-top: 12px;">
          <thead>
            <tr><th>阶段</th><th>调用</th><th>输入</th><th>输出</th><th>费用</th><th>依据</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in estDialog.data.stages" :key="s.stage">
              <td>{{ s.stage }} {{ s.name }}</td>
              <td>{{ s.calls }}</td>
              <td>{{ fmtTok(s.tokens_in) }}</td>
              <td>{{ fmtTok(s.tokens_out) }}</td>
              <td>{{ fmtYuan(s.cost_yuan) }}<span v-if="!s.rate_known" title="单价未知"> *</span></td>
              <td class="est-basis">{{ (s.basis || [])[0] || "-" }}</td>
            </tr>
          </tbody>
        </table>

        <div v-if="estDialog.data.unknown_rate_models.length" class="est-warn">
          ⚠ 以下模型没有刊例价（带 *），费用按 default 角色兜底，偏差可能很大：
          {{ estDialog.data.unknown_rate_models.join("、") }}。
          请先运行 <code>python scripts/price_wizard.py</code> 补录后再下单。
        </div>

        <div class="meta" style="margin-top: 8px;">
          {{ estDialog.data.disclaimer }}
        </div>

        <label class="est-skip">
          <input type="checkbox" v-model="estSkipSession" />
          本次会话不再提示（仍然可在「成本」页随时查看实际用量）
        </label>
      </template>

      <div class="dialog-actions">
        <button class="mini" @click="cancelEstimate">取消</button>
        <button class="mini primary" :disabled="estDialog.loading" @click="confirmEstimateRun">
          确认运行
        </button>
      </div>
    </div>
  </div>

  <!-- 打回表单 -->
  <div v-if="rejectOpen" class="drawer-mask" @click.self="rejectOpen = false">
    <div class="dialog">
      <h3>打回阶段 {{ rejectStage }}</h3>
      <div class="meta">将清理该阶段及下游产物（history/ 快照保留可回退），并撤销其审批。</div>
      <textarea v-model="rejectReason" rows="3" placeholder="打回原因（记录到 progress.json）"></textarea>
      <div class="dialog-actions">
        <button class="mini" @click="rejectOpen = false">取消</button>
        <button class="mini danger" @click="submitReject">确认打回</button>
      </div>
    </div>
  </div>

  <!-- 精修表单 -->
  <div v-if="refineOpen" class="drawer-mask" @click.self="refineOpen = false">
    <div class="dialog">
      <h3>大纲精修（增量修订）</h3>
      <div class="meta">不重跑 stage2；自动备份旧版到 data/outline/history/。</div>
      <textarea v-model="refineFeedback" rows="4" placeholder="例如：第3章侧重主角；合的部分收太快，加一场过渡"></textarea>
      <div class="dialog-actions">
        <button class="mini" @click="refineOpen = false">取消</button>
        <button class="mini primary" @click="submitRefine">提交精修</button>
      </div>
    </div>
  </div>

  <!-- .env 明文密钥知情确认：定位 .env 前强制过一道（2026-10-01 用户要求） -->
  <div v-if="envConfirmOpen" class="drawer-mask" @click.self="envConfirmOpen = false">
    <div class="dialog" style="width: min(520px, 92vw);">
      <h3>即将在文件夹中显示 .env</h3>
      <div class="meta">
        <code>.env</code> 以<b>明文</b>保存各家 API Key。继续操作只会在资源管理器中定位该文件
        （<b>不代为打开</b>），随后你自行打开时请注意：<br>
        · 不要截图，也不要把内容粘贴到聊天、工单等第三方场景；<br>
        · 编辑器插件 / AI 工具可能读取该文件，改完请尽快关闭；<br>
        · 日常配置 Key 建议优先用上方「填入 Key」输入框（后端直写，不经外部编辑器）。
      </div>
      <div class="dialog-actions">
        <button class="mini" @click="envConfirmOpen = false">取消</button>
        <button class="mini danger" @click="envConfirmOpen = false; revealEnvInFolder()">我已了解风险，并愿意继续</button>
      </div>
    </div>
  </div>

  <div v-if="toast" class="toast">{{ toast }}</div>

  <!-- ② 命令面板 -->
  <CommandPalette :open="paletteOpen" :commands="commands"
                  @close="paletteOpen = false" @pick="runCommand" />

  <!-- 关于（左上皮牌点击打开）：版本 / 环境 / 数据位置 / 快速上手 / 许可 -->
  <AboutDialog :open="aboutOpen" :api="api" :book="state ? state.book : ''"
               @close="aboutOpen = false"
               @goto="(t) => { aboutOpen = false; switchTab(t); }"
               @disclaimer="aboutOpen = false; openDisclaimer('view')" />

  <!-- 免责声明：首启强制确认（未勾选不可关）/ 随时查看（关于 · 命令面板 · 设置页） -->
  <DisclaimerDialog :open="disclaimerOpen" :mode="disclaimerMode" :acked-at="disclaimerAckedAt"
                    @close="disclaimerOpen = false"
                    @ack="disclaimerOpen = false; say('已确认免责声明，祝创作顺利')" />

  <!-- 新建项目向导（项目页签 / 命令面板入口） -->
  <NewProjectWizard :open="newProjectOpen" :api="api"
                    :current="projects.current || (state ? state.book : '')"
                    :has-work="!!state?.has_work"
                    :defaults="{ chapters: 12 }"
                    @close="newProjectOpen = false"
                    @created="onProjectCreated" />

  <!-- ② 快捷键说明 -->
  <div v-if="helpOpen" class="drawer-mask" @click.self="helpOpen = false">
    <div class="dialog" style="width: min(520px, 92vw);">
      <h3>键盘快捷键</h3>
      <table class="cost-table" style="margin-top: 8px;">
        <tbody>
          <tr v-for="s in SHORTCUTS" :key="s.k">
            <td style="width: 130px;"><kbd>{{ s.k }}</kbd></td>
            <td>{{ s.d }}</td>
          </tr>
        </tbody>
      </table>
      <div class="meta" style="margin-top: 10px;">
        数字键只在输入框外生效；快捷键与命令面板（Ctrl+K）是同一套操作的两条路径。
      </div>
      <div class="dialog-actions">
        <button class="mini" @click="!nextPopupDismissed ? dismissNextPopupForever() : reenableNextPopup()">
          {{ nextPopupDismissed ? '开启「跑完自动提示下一步」' : '关闭「跑完自动提示下一步」' }}
        </button>
        <span class="spacer"></span>
        <button class="mini primary" @click="helpOpen = false">知道了</button>
      </div>
    </div>
  </div>

  <!-- ① 跑完自动提示下一步 -->
  <div v-if="nextPopup" class="next-popup">
    <div class="np-head">
      <b>任务结束{{ lastFinishedKind ? '（' + lastFinishedKind + '）' : '' }}</b>
      <span v-if="lastJob?.result" class="pill st-done">{{ lastJob.result }}</span>
      <span class="spacer"></span>
      <button class="mini" @click="nextPopup = false" title="稍后再说">✕</button>
    </div>
    <div class="np-body">
      <div v-for="a in nextActions" :key="a.id" class="np-row">
        <div class="np-text">
          <div class="np-label">{{ a.label }}</div>
          <div class="meta">{{ a.desc }}</div>
        </div>
        <button class="mini" :class="{ primary: a.kind === 'run' || a.kind === 'publish' }"
                @click="nextPopup = false; execNextAction(a)">执行</button>
      </div>
    </div>
    <div class="np-foot">
      <button class="mini" @click="dismissNextPopupForever">不再自动弹出</button>
      <span class="spacer"></span>
      <button class="mini" @click="nextPopup = false">稍后</button>
    </div>
  </div>

  <!-- ③ 退出确认（**应用内**，替代 window.beforeunload 的 Chromium 原生框） -->
  <div v-if="quitDlg.open" class="drawer-mask" @click.self="cancelQuit">
    <div class="dialog" style="width: min(520px, 92vw);">
      <h3>退出绒花墨坊？</h3>
      <div class="meta" style="line-height: 1.8; margin-bottom: 10px;">
        当前有正在运行的流水线，或存在<b>待审批阶段</b>。<br>
        · 退出会中止正在运行的任务（已完成阶段的产物保留，重跑从断点续上）<br>
        · 待审批阶段不受影响，下次打开仍停在审批门
      </div>
      <label style="display: flex; gap: 8px; align-items: flex-start; margin-bottom: 10px;">
        <input type="checkbox" v-model="quitDlg.agreed" />
        <span>我已了解，确认退出</span>
      </label>
      <div class="dialog-actions">
        <button class="mini" @click="cancelQuit">取消</button>
        <button class="mini danger" :disabled="!quitDlg.agreed" @click="confirmQuit">确认退出</button>
      </div>
    </div>
  </div>

  <!-- ④ 跳过阶段确认（比 window.confirm 更明确：勾选护栏 + 事后回报缺失产物） -->
  <div v-if="skipDlg.open" class="drawer-mask" @click.self="skipDlg.open = false">    <div class="dialog" style="width: min(560px, 92vw);">
      <h3>跳过阶段 {{ skipDlg.stage }} · {{ STAGE_NAMES[skipDlg.stage] }}</h3>
      <div class="meta" style="line-height: 1.8; margin-bottom: 10px;">
        跳过只会把该阶段标记为「已完成 + 已审批」，<b>不会生成任何产物</b>：<br>
        · 该阶段应有的产物若缺失，后续阶段可能直接失败<br>
        · progress.json 里会记下 skipped 标记，之后仍可用「打回」回到原状态
      </div>
      <label style="display: flex; gap: 8px; align-items: flex-start; margin-bottom: 10px;">
        <input type="checkbox" v-model="skipDlg.agreed" />
        <span>我确认跳过该阶段，并接受后续阶段可能因产物缺失而失败</span>
      </label>
      <label class="meta">跳过原因（记录到 progress.json，可留空）</label>
      <textarea v-model="skipDlg.reason" rows="2" placeholder="例如：素材不足，先放行后续阶段"></textarea>
      <div v-if="skipDlg.warn" class="meta" style="color: var(--bad); margin-top: 8px;">{{ skipDlg.warn }}</div>
      <div class="dialog-actions">
        <button class="mini" @click="skipDlg.open = false">取消</button>
        <button class="mini danger" :disabled="!skipDlg.agreed || skipDlg.busy" @click="submitSkipStage">
          {{ skipDlg.busy ? '处理中…' : '确认跳过' }}
        </button>
      </div>
    </div>
  </div>

  <!-- ③ 运行日志（失败排障） -->
  <div v-if="logDlg.open" class="drawer-mask" @click.self="logDlg.open = false">
    <div class="dialog" style="width: min(900px, 94vw); max-height: min(84vh, 700px); display: flex; flex-direction: column;">
      <div style="display:flex; align-items:center; margin-bottom:10px; gap:8px;">
        <h3 style="margin:0;">运行日志</h3>
        <span class="meta">{{ logDlg.path }}</span>
        <span v-if="logDlg.total" class="meta">（共 {{ logDlg.total }} 行，显示末尾 {{ logDlg.lines.length }} 行）</span>
        <span class="spacer"></span>
        <button class="mini" @click="openRunLog">刷新</button>
        <button class="mini" @click="logDlg.open = false">关闭</button>
      </div>
      <div v-if="logDlg.loading" class="empty">读取中…</div>
      <div v-else-if="!logDlg.lines.length" class="empty">{{ logDlg.hint || '（日志为空）' }}</div>
      <pre v-else class="log-body">{{ logDlg.lines.join('\n') }}</pre>
    </div>
  </div>

  <!-- 熔断恢复对话框 -->
  <div v-if="circuitBreakerShow" class="drawer-mask">
    <div class="dialog" style="width: min(520px, 92vw);">
      <h3>⚠️ 预算超限，流水线已暂停</h3>
      <div class="meta" style="margin-bottom: 10px;">
        已用 {{ fmtYuan(state.cost.spent_yuan) }} / 限额 {{ fmtYuan(state.cost.limit_yuan) }}
      </div>
      <div class="meta" style="margin-bottom: 16px; line-height: 1.6;">
        将从阶段 {{ restartFromStage }} 继续运行（该阶段之后的内容尚未生成）。<br>
        <strong>已完成的章节不会被重写</strong>，已完成阶段保持现状。
      </div>
      <div class="dialog-actions">
        <span class="spacer"></span>
        <button class="mini" @click="circuitBreakerShow = false">取消</button>
        <button class="mini primary" @click="confirmRestart">确认从阶段 {{ restartFromStage }} 重跑</button>
      </div>
    </div>
  </div>

  <!-- 中途修改设定免责声明 -->
  <div v-if="settingEditDisclaimer" class="drawer-mask">
    <div class="dialog" style="width: min(520px, 92vw);">
      <h3>⚠️ 中途修改设定</h3>
      <div class="meta" style="margin-bottom: 16px; line-height: 1.8;">
        <p><strong>免责声明：</strong></p>
        <ul style="padding-left: 20px; margin: 8px 0;">
          <li>修改设定只会影响<strong>后续章节</strong>的生成</li>
          <li>已完成的章节不会被重写</li>
          <li>滚动摘要会在下一章节自动包含新设定</li>
          <li>大纲不会自动重跑（除非您手动打回）</li>
        </ul>
        <p>继续？</p>
      </div>
      <div class="dialog-actions">
        <span class="spacer"></span>
        <button class="mini" @click="cancelSettingEdit">取消</button>
        <button class="mini primary" @click="confirmSettingEdit">确认继续</button>
      </div>
    </div>
  </div>

  <!-- 设定编辑面板 -->
  <div v-if="settingEditOpen" class="drawer-mask">
    <div class="dialog" style="width: min(760px, 94vw); height: min(80vh, 600px); display: flex; flex-direction: column;">
      <div style="display:flex; align-items:center; margin-bottom:12px;">
        <h3 style="margin:0;">编辑设定集</h3>
        <span class="spacer"></span>
        <span class="meta">修改后将在下一章节生效</span>
      </div>
      <textarea
        :value="JSON.stringify(originalSetting, null, 2)"
        @input="originalSetting = JSON.parse($event.target.value)"
        style="flex: 1; font-family: monospace; font-size: 12px; resize: none; border: 1px solid var(--border); border-radius: 8px; padding: 12px;"
        spellcheck="false"
      ></textarea>
      <div class="dialog-actions" style="margin-top: 12px;">
        <span class="spacer"></span>
        <button class="mini" @click="settingEditOpen = false">取消</button>
        <button class="mini primary" @click="saveSettingEdit">保存</button>
      </div>
    </div>
  </div>

  <!-- 章节历史版本（回退） -->
  <div v-if="histOpen" class="drawer-mask">
    <div class="dialog" style="width: min(680px, 94vw); max-height: min(80vh, 620px); display: flex; flex-direction: column;">
      <div style="display:flex; align-items:center; margin-bottom:10px;">
        <h3 style="margin:0;">第 {{ histN }} 章 · 版本历史</h3>
        <span class="spacer"></span>
        <button class="mini" @click="histOpen = false">关闭</button>
      </div>
      <div class="meta" style="margin-bottom:10px;">
        备份目录 data/chapters/history/ch{{ String(histN).padStart(2, "0") }}_vM.md ·
        当前稿：{{ histCurrent || "（无）" }}
      </div>
      <div v-if="histLoading" class="empty">读取中…</div>
      <div v-else-if="!histVersions.length" class="empty">
        暂无历史版本 —— 每次用「精修」改稿前会自动备份一版
      </div>
      <div v-else style="overflow:auto;">
        <table class="cost-table">
          <thead>
            <tr><th>版本</th><th>字数</th><th>quality</th><th>备份时间</th><th>文件</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="v in histVersions" :key="v.version">
              <td>v{{ v.version }}</td>
              <td>{{ v.words }}</td>
              <td>{{ v.quality || "-" }}</td>
              <td>{{ v.mtime }}</td>
              <td class="title-cell">{{ v.file }}</td>
              <td>
                <button class="mini primary" :disabled="histBusy"
                        @click="restoreChapterVersion(v)">回退到此版</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="dialog-actions" style="margin-top:12px;">
        <span class="meta">回退前当前稿会再存一版，不会丢稿</span>
        <span class="spacer"></span>
        <button class="mini" @click="openHistory(histN)">刷新</button>
      </div>
    </div>
  </div>

  <!-- 初始化向导（C1） -->
  <div v-if="showInitWizard" class="drawer-mask">
    <div class="dialog" style="width: min(560px, 92vw);">
      <h3>项目初始化向导</h3>
      <div class="meta" style="margin-bottom: 12px;">
        首次使用绒花墨坊，请先配置书籍基本信息。所有设置后续可在「设置」页修改。
      </div>
      <div class="art-row" style="margin: 8px 0;">
        <span class="art-label">书名 *</span>
        <input v-model="initBookName" class="text-input" placeholder="如：示例书名" />
      </div>
      <div class="art-row" style="margin: 8px 0;">
        <span class="art-label">类型</span>
        <select v-model="initBookType" class="model-select">
          <option>奇幻</option>
          <option>科幻</option>
          <option>都市</option>
          <option>悬疑</option>
          <option>历史</option>
          <option>言情</option>
          <option>其他</option>
        </select>
      </div>
      <div class="art-row" style="margin: 8px 0;">
        <span class="art-label">章节数 *</span>
        <input v-model.number="initChapters" type="number" min="1" max="999" class="text-input" style="width: 100px;" />
      </div>
      <div class="art-row" style="margin: 8px 0;">
        <span class="art-label">风格笔记</span>
        <textarea v-model="initStyleNotes" rows="3" class="prompt-text" placeholder="可选：补充写作风格要求（可在设置页修改）"></textarea>
      </div>
      <div class="dialog-actions">
        <span class="spacer"></span>
        <button class="mini primary" @click="submitInitWizard">确认初始化</button>
      </div>
    </div>
  </div>
</template>
