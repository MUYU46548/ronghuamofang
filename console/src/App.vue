<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { diffParagraphs } from "./diff.js";
import ReviewConsole from "./ReviewConsole.vue";
import OutlineView from "./OutlineView.vue";
import ScrapsPanel from "./ScrapsPanel.vue";
import RoleGraph from "./RoleGraph.vue";
import ChapterBlueprint from "./ChapterBlueprint.vue";

const API = "http://127.0.0.1:8765";

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
let timer = null;
let toastTimer = null;
let updaterHandler = null;

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
    if (s.status === 200) { state.value = s.data; online.value = true; }
    if (m.status === 200) models.value = m.data;
    if (j && j.status === 200) lastJob.value = j.data;
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
  if (t === "cost" && costs.value.length === 0) refreshCosts();
  if (t === "project") loadProjects();
  if (t === "outline" && outlineRef.value) outlineRef.value.load(false);
  if (t === "settings") {
    if (!providers.value) refreshModels();
    if (!promptFiles.value.length) loadPromptList();
    if (!styleNotesLoaded.value) loadStyleNotes();
  }
  if (t === "materials") loadMaterials();
  if (t === "story") loadSetting();
  if (t === "outline_chapters") loadOutlineChapters();
  if (t === "export") refreshExportState();
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
  settingEditor.value = { open: true, key, idx, item: { ...item } };
}

function closeSettingEditor() {
  const { key, idx, item } = settingEditor.value;
  if (key && idx >= 0 && item && settingData.value?.[key]) {
    settingData.value[key][idx] = { ...item };
  }
  settingEditor.value = { open: false, key: '', idx: -1, item: null };
}

function addSettingItem(key) {
  if (!settingData.value) return;
  if (!settingData.value[key]) settingData.value[key] = [];
  settingData.value[key].push({ name: "新条目", description: "" });
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

async function loadProjects() {
  const r = await api("/project/list");
  if (r.status === 200) {
    projects.value = r.data;
    say("项目列表已刷新");
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

async function runStage(n) {
  const r = await api("/stage/" + n + "/run", "POST", {});
  if (r.status === 202) say("已提交 阶段" + n + "（job " + r.data.job_id + "），进度见顶栏");
  else say("提交失败: " + (r.data.error || r.status));
  refresh();
}

async function runPipelineFull() {
  if (!window.confirm(
      "确定要全自动运行流水线？\\n\\n系统将从第一个未完成的阶段开始依次运行。\\n遇到审批门（大纲/润色完成时会暂停等待确认。")) return;
  const r = await api("/stage/1/run", "POST", { from_stage: 1 });
  if (r.status === 202) say("已提交全流程运行，进度见顶栏");
  else say("提交失败: " + (r.data.error || r.status));
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

async function runPipelineStreamFull() {
  if (!window.confirm(
      "确定要流式全自动运行流水线？\\n\\n系统将从第一个未完成的阶段开始依次运行，实时输出阶段日志。\\n遇到审批门会自动暂停，可随时中断。")) return;
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
let streamSource = null;

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
    // 从第一个服务商的 available_models 填充下拉选项
    const firstProv = Object.values(r.data.providers)[0];
    if (firstProv && firstProv.available_models) {
      modelOptions.value = firstProv.available_models;
    }
    say("已刷新");
  } else {
    say("刷新失败");
  }
}

async function openEnvFile() {
  const r = await api("/env/open");
  if (r.status === 200 && r.data.exists) {
    const path = r.data.env_path;
    if (window.mofangAPI && window.mofangAPI.openArtifact) {
      window.mofangAPI.openArtifact(".env");
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
  const order = ["pending", "failed", "rejected", "running", "done"];
  const doneCount = state.value.stages.filter((s) => s.status === "done").length;
  return Math.round((doneCount / 7) * 100);
});

function statusBadge(s) {
  return { pending: "待办", running: "进行中", done: "完成", failed: "失败", rejected: "已打回" }[s] || s;
}
function fmtYuan(v) { return "¥" + Number(v || 0).toFixed(4); }
function fmtNum(v) { return Number(v || 0).toLocaleString("zh-CN"); }
function fmtTime(iso) { return (iso || "").replace("T", " "); }

/* ---------- 章节浏览 ---------- */
const chapters = ref([]);      // [{n, files: {raw, checked, refined}}]
const chaptersLoaded = ref(false);
async function loadChapters() {
  // 章数从 config/project.yaml 读（白名单允许 config/*.yaml）
  let total = 3;
  const cfg = await window.mofangAPI.readPreview("config/project.yaml");
  if (cfg.ok) {
    const m = cfg.content.match(/^\s*chapters:\s*(\d+)/m);
    if (m) total = parseInt(m[1], 10);
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
        updateStatus.value = "更新错误: " + data.message;
      }
    };
    window.mofangAPI.onUpdater(updaterHandler);
  }
  // 首次检查是否需要显示初始化向导
  checkInitWizard();
  // 检查素材目录是否为空
  checkMaterialsEmpty();
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
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div class="bg-blobs" aria-hidden="true">
    <div class="blob blob-a"></div>
    <div class="blob blob-b"></div>
  </div>

  <header class="topbar">
    <div class="brand">
      <span class="brand-mark">绒</span>
      <div>
        <div class="brand-name">绒花墨坊</div>
        <div class="brand-sub">{{ state ? state.book || "（未命名书）" : "未连接" }}</div>
      </div>
    </div>
    <nav class="tabs">
      <button :class="{ active: tab === 'pipeline' }" @click="switchTab('pipeline')">流水线</button>
      <button :class="{ active: tab === 'chapters' }" @click="switchTab('chapters'); !chaptersLoaded && loadChapters()">章节</button>
      <button :class="{ active: tab === 'materials' }" @click="switchTab('materials'); loadMaterials()">素材</button>
      <button :class="{ active: tab === 'story' }" @click="switchTab('story'); loadSetting()">设定</button>
      <button :class="{ active: tab === 'outline' }" @click="switchTab('outline')">大纲</button>
      <button :class="{ active: tab === 'outline_chapters' }" @click="switchTab('outline_chapters'); loadOutlineChapters()">分章</button>
      <button :class="{ active: tab === 'review' }" @click="switchTab('review')">审稿</button>
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
      <span v-else-if="streamStatus === 'ok'" class="pill st-done">完成</span>
      <span v-else-if="streamStatus === 'stopped'" class="pill st-rej">已中断</span>
      <span v-else class="pill st-rej">{{ streamStatus }}</span>
      <button v-if="streamConnected" class="mini danger" @click="stopStream">中断</button>
      <button class="mini" @click="streamOpen = false; stopStream()">关闭</button>
    </div>
    <pre class="stream-body">{{ streamText || "等待输出…" }}</pre>
  </div>

  <main class="content">
    <!-- 从收件箱跳转而来 → 一步返回 -->
    <div v-if="cameFromInbox && tab !== 'inbox'" class="backbar">
      <button class="mini" @click="backToInbox">← 返回收件箱</button>
      <span class="meta">从收件箱跳转而来；审批门（{{ pendingGates.length }} 项待处理）还在等着你</span>
    </div>

    <!-- 流水线 -->
    <section v-if="tab === 'pipeline' && state" class="grid-2">
      <div class="card">
        <h3>七阶段流水线</h3>
        <div class="progress"><div class="progress-in" :style="{ width: progressPct + '%' }"></div></div>
        <div class="run-all-bar">
          <button class="mini primary" :disabled="isRunning" @click="runPipelineFull" title="从第一个未完成的阶段开始，依次运行到完成">
            ▶ 全自动运行
          </button>
          <button class="mini" :disabled="isRunning" @click="runPipelineStreamFull" title="全自动运行（流式输出，可随时中断）">
            ▶ 流式全自动
          </button>
          <button class="mini" :disabled="isRunning" @click="runPublish" title="生成最终 Word 全书 + 摘要">
            📄 生成 Word 成品
          </button>
          <span class="meta">从第一个未完成阶段依次跑完，遇审批门自动暂停</span>
        </div>
        <div v-for="s in stageList" :key="s.stage" class="stage-row">
          <div class="stage-info">
            <span class="stage-no" :class="{ lit: s.status === 'done', run: s.status === 'running' }">{{ s.stage }}</span>
            <span class="stage-name">{{ s.name }}</span>
            <span class="pill" :class="'st-' + s.status">{{ statusBadge(s.status) }}</span>
            <span v-if="s.status === 'done' && !s.approved" class="pill st-gate">待审批</span>
            <span v-if="s.rejected" class="pill st-rej" :title="s.rejected">打回: {{ s.rejected.slice(0, 12) }}</span>
          </div>
          <div class="stage-actions">
            <button class="mini" :disabled="isRunning" @click="runStage(s.stage)">运行</button>
            <button class="mini" :disabled="isRunning" @click="runStageStream(s.stage)" title="实时流式输出，可随时中断">流式运行</button>
            <button v-if="s.status === 'done' && !s.approved" class="mini primary" @click="approve(s.stage)">确认</button>
            <button v-else-if="s.approved" class="mini" @click="approve(s.stage, true)">撤销</button>
            <button v-if="s.stage >= 2 && s.status !== 'pending'" class="mini danger" :disabled="isRunning" @click="openReject(s.stage)">打回</button>
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
      </div>
      <div v-if="!chaptersLoaded" class="empty">点击"重新探测"加载章节产物</div>
      <div v-for="c in chapters" :key="c.n" class="chap-row">
        <span class="stage-no lit">{{ c.n }}</span>
        <span class="chap-name">第 {{ c.n }} 章</span>
        <span class="spacer"></span>
        <button class="mini" :class="{ ghost: !c.files.raw }" @click="openDoc('第' + c.n + '章 原稿', c.files.raw)">原稿</button>
        <button class="mini" :class="{ ghost: !c.files.checked }" @click="openDoc('第' + c.n + '章 检查稿', c.files.checked)">检查稿</button>
        <button class="mini" :class="{ ghost: !c.files.refined }" @click="openDoc('第' + c.n + '章 润色稿', c.files.refined)">润色稿</button>
        <button class="mini" :class="{ ghost: !(c.files.raw && c.files.refined) }" @click="openDiff('第' + c.n + '章 润色对比', c.files.raw, c.files.refined)">对比</button>
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
        <button :class="{ on: settingTab === 'plot_fragments' }" @click="openSettingTab('plot_fragments')">
          情节 ({{ countItems('plot_fragments') }})
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

    <!-- Story Bible 编辑抽屉 -->
    <div v-if="settingEditor.open" class="drawer-mask" @click.self="settingEditor.open = false">
      <div class="drawer" style="width: min(600px, 90vw);">
        <div class="drawer-head">
          <b>{{ settingEditor.item?.name || '编辑条目' }}</b>
          <span class="spacer"></span>
          <button class="mini" @click="closeSettingEditor()">关闭</button>
        </div>
        <div style="padding: 16px;">
          <label>名称</label>
          <input v-model="settingEditor.item.name" class="text-input" style="width: 100%; margin-bottom: 12px;" placeholder="条目名称" @input="settingDirty = true" />
          <label>描述</label>
          <textarea v-model="settingEditor.item.description" class="prompt-text" style="width: 100%; min-height: 300px;" placeholder="详细描述..." @input="settingDirty = true"></textarea>
          <div class="meta" style="margin-top: 8px;">
            {{ (settingEditor.item.description || '').length }} 字符
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
      <h3>审批收件箱</h3>
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
        <button class="mini" @click="refreshModels">刷新</button>
      </div>
      <div class="meta">engine: {{ models.engine }}</div>
      <div v-for="(m, role) in models.model" :key="role" class="art-row">
        <span class="pill st-done">{{ role }}</span>
        <span class="art-path">{{ m.provider }} / {{ m.id }}</span>
        <span class="spacer"></span>
        <select v-model="m.id" @change="switchModel(role, m.id)" class="model-select">
          <option v-for="opt in modelOptions" :key="opt" :value="opt">{{ opt }}</option>
        </select>
      </div>
      <div class="meta" style="margin-top: 12px;">
        改模型：下拉切换后立即写入 config/system.yaml，下次运行阶段时生效。
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

      <!-- 自动更新 -->
      <h4 style="margin-top: 16px;">自动更新</h4>
      <div class="provider-row">
        <span class="pill st-done">electron-updater</span>
        <span class="art-path">{{ updateStatus }}</span>
        <span class="spacer"></span>
        <button class="mini" @click="checkForUpdates" :disabled="checkingUpdate">
          {{ checkingUpdate ? "检查中…" : "检查更新" }}
        </button>
        <button v-if="updateReady" class="mini primary" @click="quitAndInstall">重启安装</button>
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

    <!-- 项目（书籍切换） -->
    <section v-if="tab === 'project'" class="card">
      <div class="card-head">
        <h3>项目切换</h3>
        <button class="mini" @click="loadProjects">刷新</button>
      </div>
      <div class="meta" style="margin-bottom: 12px;">
        当前项目：<b>{{ projects.current || "（未初始化）" }}</b>
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
      <div class="meta" style="margin-top: 14px;">
        <b>新建项目：</b>在 config/project.yaml 中修改 book.name，然后归档当前项目并初始化新工作区。
      </div>
    </section>

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

  <!-- 初始化向导（C1） -->
  <div v-if="showInitWizard" class="drawer-mask">
    <div class="dialog" style="width: min(560px, 92vw);">
      <h3>项目初始化向导</h3>
      <div class="meta" style="margin-bottom: 12px;">
        首次使用绒花墨坊，请先配置书籍基本信息。所有设置后续可在「设置」页修改。
      </div>
      <div class="art-row" style="margin: 8px 0;">
        <span class="art-label">书名 *</span>
        <input v-model="initBookName" class="text-input" placeholder="如：留下你的歌" />
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
