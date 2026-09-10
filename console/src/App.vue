<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { diffParagraphs } from "./diff.js";
import ReviewConsole from "./ReviewConsole.vue";

const API = "http://127.0.0.1:8765";

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
  if (t === "cost" && costs.value.length === 0) refreshCosts();
  if (t === "project") loadProjects();
  if (t === "settings") {
    if (!providers.value) refreshModels();
    if (!promptFiles.value.length) loadPromptList();
    if (!styleNotesLoaded.value) loadStyleNotes();
  }
}

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
});
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
        <div class="brand-sub">{{ state ? state.book || "（未命名书）" : "连接中…" }}</div>
      </div>
    </div>
    <nav class="tabs">
      <button :class="{ active: tab === 'pipeline' }" @click="switchTab('pipeline')">流水线</button>
      <button :class="{ active: tab === 'chapters' }" @click="switchTab('chapters'); !chaptersLoaded && loadChapters()">章节</button>
      <button :class="{ active: tab === 'review' }" @click="switchTab('review')">审稿</button>
      <button :class="{ active: tab === 'inbox' }" @click="switchTab('inbox')">
        收件箱<span v-if="pendingGates.length" class="badge">{{ pendingGates.length }}</span>
      </button>
      <button :class="{ active: tab === 'project' }" @click="switchTab('project')">项目</button>
      <button :class="{ active: tab === 'settings' }" @click="switchTab('settings')">设置</button>
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
            <span v-if="s.rejected" class="pill st-rej" :title="s.rejected">打回: {{ s.rejected.slice(0, 12) }}</span>
          </div>
          <div class="stage-actions">
            <button class="mini" :disabled="isRunning" @click="runStage(s.stage)">运行</button>
            <button class="mini" :disabled="isRunning" @click="runStageStream(s.stage)" title="实时流式输出，可随时中断">流式运行</button>
            <button v-if="s.status === 'done' && !s.approved" class="mini primary" @click="approve(s.stage)">确认</button>
            <button v-else-if="s.approved" class="mini" @click="approve(s.stage, true)">撤销</button>
            <button v-if="s.stage >= 2" class="mini danger" :disabled="isRunning" @click="openReject(s.stage)">打回</button>
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

    <!-- 审稿 -->
    <section v-if="tab === 'review'">
      <ReviewConsole />
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
          <button class="mini" v-if="s.stage === 2" @click="refineOpen = true">精修意见</button>
          <button class="mini danger" @click="openReject(s.stage)">打回</button>
        </div>
        <div class="gate-files">
          <button class="mini" v-if="s.stage === 2" @click="preview('data/outline/global.md')">预览整体大纲</button>
          <button class="mini" v-if="s.stage === 2" @click="preview('data/outline/review_report.md')">体检报告</button>
          <button class="mini" v-if="s.stage === 6" @click="switchTab('chapters'); loadChapters()">去章节页看润色稿</button>
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
  </main>

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
</template>
