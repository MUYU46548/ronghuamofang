<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";

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
const costs = ref([]);
const online = ref(false);
const toast = ref("");
const lastJob = ref(null);      // { state, result }
let timer = null;
let toastTimer = null;

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
  const r = await api("/costs");
  if (r.status === 200) costs.value = r.data.entries || [];
}

function switchTab(t) {
  tab.value = t;
  if (t === "cost" && costs.value.length === 0) refreshCosts();
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
function openDoc(title, content) {
  if (!content) return say("该产物尚未生成");
  viewDoc.value = { title, content };
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

onMounted(() => {
  refresh();
  timer = setInterval(refresh, 2500);
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
      <button :class="{ active: tab === 'inbox' }" @click="switchTab('inbox')">
        收件箱<span v-if="pendingGates.length" class="badge">{{ pendingGates.length }}</span>
      </button>
      <button :class="{ active: tab === 'cost' }" @click="switchTab('cost')">成本</button>
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
      </div>
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
        <h3>成本流水（最近 100 笔）</h3>
        <button class="mini" @click="refreshCosts">刷新</button>
      </div>
      <div v-if="state" class="meta" style="margin-bottom: 10px;">
        累计 {{ fmtYuan(state.cost.spent_yuan) }} / 预算 {{ fmtYuan(state.cost.limit_yuan) }}
        （估算笔数 {{ state.cost.estimated_entries }}/{{ state.cost.calls }}）
      </div>
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
      <h3>引擎与模型（config/system.yaml）</h3>
      <div class="meta">engine: {{ models.engine }}</div>
      <div v-for="(m, role) in models.model" :key="role" class="art-row">
        <span class="pill st-done">{{ role }}</span>
        <span class="art-path">{{ m.provider }} / {{ m.id }}</span>
      </div>
      <div class="meta" style="margin-top: 12px;">
        改模型/服务商：编辑 config/system.yaml 后重启控制台；密钥在项目 .env（不入 git）。
        模型白名单纪律：未经确认不指定付费模型。
      </div>
      <div class="meta">项目目录: {{ state ? state.project_dir : "-" }}</div>
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
