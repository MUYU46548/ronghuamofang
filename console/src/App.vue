<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from "vue";
import { diffParagraphs } from "./diff.js";
import ReviewConsole from "./ReviewConsole.vue";
import SandboxQueue from "./SandboxQueue.vue";
import OutlineView from "./OutlineView.vue";
import ScrapsPanel from "./ScrapsPanel.vue";
import RoleGraph from "./RoleGraph.vue";
import ChapterBlueprint from "./ChapterBlueprint.vue";
import ProofreadPanel from "./ProofreadPanel.vue";
import StylePanel from "./StylePanel.vue";
import CommandPalette from "./CommandPalette.vue";
import AboutDialog from "./AboutDialog.vue";
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

// 后端地址：默认本机 8765。自动化 UI 验收脚本可用 window.__NF_API_BASE__ 把它指向
// 临时端口（避免干扰用户正在运行的控制台实例）。
const API = (window.__NF_API_BASE__ || "http://127.0.0.1:8765").replace(/\/+$/, "");

// 素材页签内的子视图：结构化卡片（materials/raw） / 原始碎片（materials/original_scraps）
const materialSub = ref("cards");

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  return { status: r.status, data };
}

const tab = ref("pipeline");
const state = ref(null);
const models = ref(null);
const modelOptions = ref([]); // 动态从 /models/available 加载
const costs = ref([]);
const costSummary = ref(null);
const costView = ref("cost");   // "cost" | "usage"
const providers = ref(null);    // provider status (no keys exposed)
const online = ref(false);
const toast = ref("");
const lastJob = ref(null);      // { state, result }
const updateStatus = ref("等待检查");
const updateReady = ref(false);
const updateProgress = ref(0);
const checkingUpdate = ref(false);
const updaterInitialized = ref(false);
const updaterDev = ref(true);
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

async function refreshCosts() {
  const [list, summary] = await Promise.all([
    api("/costs"),
    api("/costs/summary"),
  ]);
  if (list.status === 200) costs.value = list.data.entries || [];
  if (summary.status === 200) costSummary.value = summary.data;
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
  { id: "pink", name: "樱粉", color: "#d48ca0" },
  { id: "blue", name: "天蓝", color: "#7dadde" },
  { id: "green", name: "翠绿", color: "#8ab89a" },
  { id: "orange", name: "暖橙", color: "#e09950" },
  { id: "gray", name: "灰白", color: "#9a9aa4" },
];

function setTheme(t) {
  theme.value = t;
  localStorage.setItem("mofang_theme", t);
  document.documentElement.className = `theme-${t}`;
}

function setFontSize(px) {
  fontSize.value = px;
  localStorage.setItem("mofang_font_size", px.toString());
  document.documentElement.style.setProperty("--app-font-size", px + "px");
}

// 初始化外观
setTheme(theme.value);
setFontSize(fontSize.value);

/* ---------- 退出确认 ---------- */
const hasUnfinishedJob = computed(() => {
  if (!state.value) return false;
  // 当前有运行中的 job，或存在未完成/待审批的 stage
  if (state.value.current_job) return true;
  const stages = state.value.stages || [];
  return stages.some((s) => s.status === "running" || (s.status === "done" && !s.approved));
});

function setupExitGuard() {
  window.addEventListener("beforeunload", (e) => {
    if (hasUnfinishedJob.value) {
      e.preventDefault();
      e.returnValue = "当前有正在运行的流水线或待审批阶段，退出将中止当前任务。确定退出？";
      return e.returnValue;
    }
  });
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
  const r = await api("/stage/1/run", "POST", { from_stage: 1 });
  if (r.status === 202) say("已提交全流程运行，进度见顶栏");
  else say("提交失败: " + (r.data.error || r.status));
  refresh();
}

async function runPipelineStreamFull() {
  await confirmRunWithEstimate("流式全自动运行流水线", null, doRunPipelineStreamFull);
}

async function doRunPipelineStreamFull() {
  const r = await api("/stage/1/run", "POST", { from_stage: 1, stream: true });
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
    const firstProv = Object.values(r.data.providers)[0];
    if (firstProv && firstProv.available_models) {
      modelOptions.value = firstProv.available_models;
    }
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
      const tokenhub = r.data.providers?.tokenhub;
      if (tokenhub?.models?.length) {
        modelOptions.value = tokenhub.models;
        modelSource.value = "fetched";
        say(`已同步 ${tokenhub.count} 个模型`);
        // 更新服务商选项
        if (providers.value?.tokenhub) {
          providerOptions.value = [{
            id: "tokenhub",
            name: "tokenhub ✓",
            has_key: true,
          }];
        }
      } else {
        const err = tokenhub?.error || "未知错误";
        say(`同步失败: ${err}（检查 .env 中的 API Key）`);
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
    say(`已切换 ${role} → ${newProvider}`);
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

async function addManualModel() {
  const name = newModelName.value.trim();
  if (!name) return;
  if (!modelOptions.value.includes(name)) {
    modelOptions.value = [...modelOptions.value, name];
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

async function openEnvFile() {
  const r = await api("/env/open");
  if (r.status === 200 && r.data.exists) {
    const path = r.data.env_path;
    // .env 含明文密钥：**不再交给外部编辑器打开**（2026-09-19 审查 S7）。
    // 只展示路径 + 提供「在文件夹中显示」，由用户在受控环境内自行编辑。
    if (window.mofangAPI && window.mofangAPI.revealInFolder) {
      window.mofangAPI.revealInFolder(".env");
      say("已在文件夹中定位 .env —— 请用你自己的编辑器打开并填入 Key（界面不显示明文）");
    } else {
      say("请手动打开: " + path);
    }
  } else {
    say(".env 文件不存在");
  }
}

async function switchModel(role, modelId) {
  const r = await api("/models/switch", "POST", { role, model: modelId });
  if (r.status === 200) say("已切换 " + role + " → " + modelId + "（下次运行生效）");
  else say("切换失败: " + (r.data.error || ""));
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
  promptDirty.value = false;
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
  const doneCount = state.value.stages.filter((s) => s.status === "done").length;
  return Math.round((doneCount / 7) * 100);
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
      if (r.ok) info.push({ path: p, lines: r.content.split("\n").length });
      else info.push({ path: p, lines: 0 });
    }
    gateArtifacts.value[g.stage] = info;
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
  // 弹层打开时不吃其它按键（交给弹层自己处理）
  if (paletteOpen.value || helpOpen.value) return;
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
const chapterQuality = ref({});  // {1: 7.5, 2: null, ...}
const chapterThreshold = ref(6);
const gateNotify = ref(gateNotifyEnabled());
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
    updateStatus.value = "updater 未初始化";
    return;
  }
  // 先检查 updater 状态
  const status = await window.mofangAPI.updaterStatus();
  if (status?.dev) {
    updateStatus.value = "开发模式（仅打包版本支持自动更新）";
    return;
  }
  checkingUpdate.value = true;
  updateStatus.value = "正在检查...";
  const r = await window.mofangAPI.updaterCheck();
  if (!r.ok) updateStatus.value = "检查失败: " + (r.error || "");
  checkingUpdate.value = false;
}

async function quitAndInstall() {
  if (window.mofangAPI?.updaterQuitAndInstall) {
    await window.mofangAPI.updaterQuitAndInstall();
  }
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
      if (updaterDev.value) updateStatus.value = "开发模式（仅打包版本支持自动更新）";
    });
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
      } else if (data.type === "error") {
        // 404 = 暂无发布，不显示冗长错误信息
        const msg = data.message || "";
        if (msg.includes("404") || msg.includes("no published")) {
          updateStatus.value = "暂无更新（当前已是最新版本）";
        } else {
          updateStatus.value = "更新检查失败";
        }
      }
    };
    updaterCleanup = window.mofangAPI.onUpdater(updaterHandler);
  }
  // 首次检查是否需要显示初始化向导
  checkInitWizard();
  // 检查素材目录是否为空
  checkMaterialsEmpty();
  // 首启引导状态检测（监听 isColdStart 变化，state 加载完成后自动触发）
  watch(isColdStart, (v) => { if (v) checkColdStartStatus(); });
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
  </header>

  <!-- 运行进度条（全局） -->
  <div v-if="isRunning" class="runbar">
    <span class="run-dot"></span>
    <span>任务执行中：{{ state.current_job }}（{{ lastJob?.result || "运行中…" }}）</span>
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
    <div v-if="tab === 'pipeline' && state && isColdStart" class="coldstart">
      <div class="cs-title">从这个开始（四步跑通第一本书）</div>
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
      <ReviewConsole />
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
    </section>

    <!-- 设置 -->
    <section v-if="tab === 'settings' && models" class="card">
      <div class="card-head">
        <h3>引擎与模型（config/system.yaml）</h3>
        <button class="mini" @click="refreshModels" title="从 config/system.yaml 重载">刷新</button>
      </div>
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
        <select v-model="m.id" @change="switchModel(role, m.id)" class="model-select">
          <option v-for="opt in filteredModelOptions" :key="opt" :value="opt">{{ opt }}</option>
        </select>
        <select v-model="m.provider" @change="switchProvider(role, m.provider)" class="provider-select">
          <option v-for="p in providerOptions" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="art-row" style="margin-top: 8px;">
        <span class="pill st-done">搜索模型</span>
        <input v-model="modelSearch" placeholder="输入关键词过滤..." class="model-input" />
        <button class="mini" @click="modelSearch = ''" v-if="modelSearch">×</button>
        <span class="meta" style="margin-left: auto;">{{ filteredModelOptions.length }} / {{ modelOptions.length }}</span>
      </div>
      <div class="art-row" style="margin-top: 8px;">
        <span class="pill st-done">手动添加</span>
        <input v-model="newModelName" placeholder="输入模型名（如 kimi-k2.5）" class="model-input" />
        <button class="mini" @click="addManualModel">+ 添加</button>
      </div>
      <div class="meta" style="margin-top: 12px;">
        改模型：下拉切换后立即写入 config/system.yaml，下次运行阶段时生效。
        <br>architect=设定/世界观 | outliner=大纲 | writer=写作 | checker=检查 | reviewer=审核 | polisher=润色
      </div>

      <!-- 服务商与密钥状态 -->
      <h4 style="margin-top: 16px;">服务商与 API Key</h4>
      <div v-if="providers" v-for="(p, pid) in providers" :key="pid" class="provider-row">
        <span class="pill st-done">{{ pid }}</span>
        <span class="art-path">{{ p.base_url }}</span>
        <span class="spacer"></span>
        <span v-if="p.has_key" class="key-status ok">✓ 已配置 ({{ p.key_mask }})</span>
        <span v-else class="key-status bad">✗ 缺失 ({{ p.api_key_env }})</span>
      </div>
      <div class="meta" style="margin-top: 8px;">
        API Key 通过项目 .env 文件配置，不在此处明文显示。
      </div>
      <button class="mini" @click="openEnvFile" style="margin-top: 8px;">打开 .env 文件</button>

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
          <div class="meta">
            共 {{ promptContent.length }} 字符
            <template v-if="promptBackups.length">
              · 最近备份：{{ promptBackups.slice(-3).join("、") }}
            </template>
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
                :title="updaterDev ? '开发模式（仅打包版本支持自动更新）' : '检查更新'">
          {{ checkingUpdate ? "检查中…" : "检查更新" }}
        </button>
        <button v-if="updateReady" class="mini primary" @click="quitAndInstall">重启安装</button>
      </div>
      <div v-if="updaterDev" class="meta" style="margin-top: 4px; opacity: 0.7;">
        开发模式下自动更新已禁用。手动下载：<a href="https://github.com/MUYU46548/ronghuamofang/releases" target="_blank" style="color: var(--accent);">GitHub Releases</a>
      </div>
      <div v-if="updateProgress > 0 && updateProgress < 100" class="meta" style="margin-top: 4px;">
        下载进度: {{ updateProgress.toFixed(1) }}%
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
        暂无素材 —— 点击"添加素材"从其他地方复制文件到 raw/，或"新建素材"直接创建。
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
      <textarea v-model="refineFeedback" rows="4" placeholder="例如：第3章侧重露汐；合的部分收太快，加一场过渡"></textarea>
      <div class="dialog-actions">
        <button class="mini" @click="refineOpen = false">取消</button>
        <button class="mini primary" @click="submitRefine">提交精修</button>
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
               @goto="(t) => { aboutOpen = false; switchTab(t); }" />

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

  <!-- ③ 跳过阶段确认（比 window.confirm 更明确：勾选护栏 + 事后回报缺失产物） -->
  <div v-if="skipDlg.open" class="drawer-mask" @click.self="skipDlg.open = false">
    <div class="dialog" style="width: min(560px, 92vw);">
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
