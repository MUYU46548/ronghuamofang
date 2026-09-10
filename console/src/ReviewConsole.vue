<script setup>
import { ref, computed, onMounted } from "vue";

const API = "http://127.0.0.1:8765";

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  return { status: r.status, data };
}

// ---------- 状态 ----------
const report = ref(null);
const loading = ref(false);
const saving = ref(false);
const running = ref(false);
const batchProgress = ref(null); // { current, total, index, status, message }
let progressTimer = null;
const toast = ref("");
const decisions = ref({}); // { "chapter_finding_id": { action, feedback } }
const expandedChapters = ref(new Set());

let toastTimer = null;
function say(msg) {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 4500);
}

// ---------- 计算属性 ----------
const chapters = computed(() => report.value?.chapters || []);
const totalFindings = computed(() => {
  return chapters.value.reduce((sum, c) => sum + (c.findings?.length || 0), 0);
});
const acceptedCount = computed(() => {
  return Object.values(decisions.value).filter(d => d.action === "accept").length;
});
const ignoredCount = computed(() => {
  return Object.values(decisions.value).filter(d => d.action === "ignore").length;
});
const pendingCount = computed(() => {
  return totalFindings.value - acceptedCount.value - ignoredCount.value;
});
const hasDecision = computed(() => acceptedCount.value > 0);

// ---------- 方法 ----------
function toggleChapter(n) {
  const s = expandedChapters.value;
  if (s.has(n)) s.delete(n);
  else s.add(n);
}

function setDecision(chapterNo, findingId, action, feedback = "") {
  const uniqueKey = `${chapterNo}_${findingId}`;
  if (action === "ignore") {
    decisions.value[uniqueKey] = { action: "ignore", feedback: "" };
  } else {
    decisions.value[uniqueKey] = { action: "accept", feedback };
  }
}

function acceptAll() {
  for (const c of chapters.value) {
    for (const f of (c.findings || [])) {
      decisions.value[`${c.n}_${f.id}`] = { action: "accept", feedback: "" };
    }
  }
  say("已接受全部 " + totalFindings.value + " 条");
}

function ignoreAll() {
  for (const c of chapters.value) {
    for (const f of (c.findings || [])) {
      decisions.value[`${c.n}_${f.id}`] = { action: "ignore", feedback: "" };
    }
  }
  say("已忽略全部");
}

function resetDecisions() {
  decisions.value = {};
  say("已重置");
}

async function runReview() {
  loading.value = true;
  try {
    const r = await api("/review/run", "POST", { stream: false });
    if (r.status === 202) {
      say("审查任务已提交: " + r.data.job_id);
      // 轮询 job 状态
      await pollJob(r.data.job_id);
    } else {
      say("提交失败: " + (r.data.error || r.status));
    }
  } catch (e) {
    say("错误: " + e.message);
  } finally {
    loading.value = false;
  }
}

async function pollJob(jobId) {
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const r = await api("/jobs/" + jobId);
    if (r.data.state !== "running") {
      if (r.data.state === "ok") {
        say("审查完成");
        await loadReport();
      } else {
        say("审查失败: " + (r.data.result || "未知错误"));
      }
      return;
    }
  }
}

async function loadReport() {
  const r = await api("/review/report");
  if (r.status === 200 && r.data.chapters) {
    report.value = r.data;
    // 初始化决策 - 用 "章节号_findingID" 作为全局唯一 key
    for (const c of r.data.chapters) {
      for (const f of (c.findings || [])) {
        const uniqueKey = `${c.n}_${f.id}`;
        if (!decisions.value[uniqueKey]) {
          decisions.value[uniqueKey] = { action: "pending", feedback: "" };
        }
      }
    }
  } else {
    say("无可用审查报告");
  }
}

async function saveDecisions() {
  saving.value = true;
  try {
    const payload = {
      decisions: Object.entries(decisions.value)
        .filter(([_, d]) => d.action !== "pending")
        .map(([uniqueKey, d]) => {
          // "chapter_finding_id" → { finding_id, chapter }
          const underscoreIdx = uniqueKey.indexOf("_");
          const chapter = parseInt(uniqueKey.slice(0, underscoreIdx), 10);
          const findingId = uniqueKey.slice(underscoreIdx + 1);
          return {
            finding_id: findingId,
            chapter,
            action: d.action,
            feedback: d.feedback || "",
          };
        }),
    };
    const r = await api("/review/decisions", "POST", payload);
    if (r.status === 200) {
      say("决策已保存，可运行批量精修");
    } else {
      say("保存失败: " + (r.data.error || r.status));
    }
  } catch (e) {
    say("错误: " + e.message);
  } finally {
    saving.value = false;
  }
}

async function runBatchRefine() {
  running.value = true;
  batchProgress.value = null;
  try {
    const r = await api("/batch_refine/run", "POST", { decisions_from_file: true });
    if (r.status === 202) {
      say("批量精修已提交: " + r.data.job_id);
      pollBatchProgress();
      await pollJob(r.data.job_id);
    } else {
      say("提交失败: " + (r.data.error || r.status));
    }
  } catch (e) {
    say("错误: " + e.message);
  } finally {
    running.value = false;
  }
}

async function pollBatchProgress() {
  progressTimer = setInterval(async () => {
    const r = await api("/batch_refine/progress");
    if (r.status === 200) {
      batchProgress.value = r.data;
      if (r.data.status === "done" || r.data.status === "failed" || r.data.status === "idle") {
        clearInterval(progressTimer);
        progressTimer = null;
      }
    }
  }, 2000);
}

// ---------- 工具 ----------
function sevClass(severity) {
  return { error: "sev-error", warn: "sev-warn", info: "sev-info" }[severity] || "sev-info";
}
function sevIcon(severity) {
  return { error: "🔴", warn: "🟡", info: "🔵" }[severity] || "⚪";
}
function typeLabel(type) {
  return {
    outline_gap: "大纲缺漏",
    character_inconsistency: "角色不一致",
    transition_issue: "衔接问题",
    style_violation: "文风违规",
    other: "其他",
  }[type] || type;
}

onMounted(() => {
  loadReport();
});
</script>

<template>
  <div class="review-console">
    <!-- 头部 -->
    <div class="review-head">
      <h3>审稿 → 修稿闭环</h3>
      <div class="head-actions">
        <button class="mini" :disabled="loading" @click="runReview">
          {{ loading ? "审查中…" : "运行审查" }}
        </button>
        <button class="mini" @click="loadReport">刷新报告</button>
      </div>
    </div>

    <!-- 无报告提示 -->
    <div v-if="!report" class="empty">
      暂无审查报告 —— 点击"运行审查"开始分析章节
    </div>

    <!-- 报告内容 -->
    <div v-else>
      <!-- 确定性检查汇总 -->
      <section class="card">
        <h4>确定性检查汇总</h4>
        <table class="det-table">
          <thead>
            <tr><th>章</th><th>标题</th><th>字数</th><th>quality</th><th>文风</th></tr>
          </thead>
          <tbody>
            <tr v-for="(det, n) in report.deterministic" :key="n">
              <td>{{ n }}</td>
              <td class="title-cell">{{ det.title || "?" }}</td>
              <td :class="{ 'cell-bad': !det.word_ok }">{{ det.word_count }} {{ det.word_ok ? "✓" : "✗" }}</td>
              <td>{{ det.quality ?? "-" }}</td>
              <td>{{ det.style_issues?.length ? det.style_issues.length + " 项" : "✓" }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- 操作栏 -->
      <section class="card">
        <div class="decision-bar">
          <div class="decision-stats">
            <span class="stat-accept">接受 {{ acceptedCount }}</span>
            <span class="stat-ignore">忽略 {{ ignoredCount }}</span>
            <span class="stat-pending">待定 {{ pendingCount }}</span>
          </div>
          <div class="decision-actions">
            <button class="mini" @click="acceptAll">全接受</button>
            <button class="mini" @click="ignoreAll">全忽略</button>
            <button class="mini" @click="resetDecisions">重置</button>
          </div>
        </div>
      </section>

      <!-- 章节审查发现 -->
      <section v-for="c in chapters" :key="c.n" class="card chapter-card">
        <div class="chapter-head" @click="toggleChapter(c.n)">
          <span class="stage-no" :class="{ lit: c.findings?.length }">{{ c.n }}</span>
          <span class="chapter-title">第 {{ c.n }} 章</span>
          <span class="finding-count">{{ c.findings?.length || 0 }} 条</span>
          <span class="spacer"></span>
          <span class="expand-icon">{{ expandedChapters.has(c.n) ? "▼" : "▶" }}</span>
        </div>

        <div v-if="expandedChapters.has(c.n)" class="chapter-body">
          <div v-if="!c.findings?.length" class="no-issues">无问题 ✓</div>
          <div v-for="f in c.findings" :key="f.id" class="finding-row" :class="sevClass(f.severity)">
            <div class="finding-header">
              <span class="sev-icon">{{ sevIcon(f.severity) }}</span>
              <span class="finding-type">{{ typeLabel(f.type) }}</span>
              <span class="finding-id">{{ f.id }}</span>
            </div>
            <div class="finding-detail">{{ f.detail }}</div>
            <div v-if="f.suggested_action" class="finding-suggestion">
              建议：{{ f.suggested_action }}
            </div>
            <div class="finding-actions">
              <button
                class="mini"
                :class="{ primary: decisions[`${c.n}_${f.id}`]?.action === 'accept' }"
                @click="setDecision(c.n, f.id, 'accept')"
              >接受</button>
              <button
                class="mini"
                :class="{ danger: decisions[`${c.n}_${f.id}`]?.action === 'ignore' }"
                @click="setDecision(c.n, f.id, 'ignore')"
              >忽略</button>
              <input
                v-if="decisions[`${c.n}_${f.id}`]?.action === 'accept'"
                v-model="decisions[`${c.n}_${f.id}`].feedback"
                class="feedback-input"
                placeholder="补充意见（可选）"
              />
            </div>
          </div>
        </div>
      </section>

      <!-- 实时进度条 -->
      <section v-if="batchProgress && batchProgress.status === 'running'" class="card progress-card">
        <div class="progress-info">
          <span class="progress-msg">{{ batchProgress.message }}</span>
          <span class="progress-pct">{{ Math.round((batchProgress.index / batchProgress.total) * 100) }}%</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: (batchProgress.index / batchProgress.total * 100) + '%' }"></div>
        </div>
      </section>

      <!-- 底部操作 -->
      <section class="card bottom-actions">
        <button class="mini primary" :disabled="saving || !hasDecision" @click="saveDecisions">
          {{ saving ? "保存中…" : "保存决策" }}
        </button>
        <button
          class="mini primary"
          :disabled="running || !hasDecision"
          @click="runBatchRefine"
        >
          {{ running ? "精修中…" : "运行批量精修" }}
        </button>
        <span class="hint">决策保存到 review_report.decisions.json</span>
      </section>
    </div>

    <!-- Toast -->
    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.review-console {
  position: relative;
}
.review-head {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.review-head h3 {
  margin: 0;
  flex: 1;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.mini {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card);
  cursor: pointer;
  font-size: 13px;
  color: var(--ink);
}
.mini:hover { background: var(--accent-soft); color: #fff; }
.mini.primary { background: var(--accent); color: #fff; }
.mini.danger { background: var(--bad); color: #fff; }
.mini:disabled { opacity: 0.5; cursor: not-allowed; }

.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  margin-bottom: 12px;
}
.card h4 {
  margin: 0 0 10px;
  font-size: 14px;
}
.det-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.det-table th, .det-table td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.det-table th {
  color: var(--muted);
  font-weight: 500;
}
.title-cell {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cell-bad {
  color: var(--bad);
}

.decision-bar {
  display: flex;
  align-items: center;
  gap: 16px;
}
.decision-stats {
  display: flex;
  gap: 12px;
  font-size: 13px;
}
.stat-accept { color: var(--ok); }
.stat-ignore { color: var(--muted); }
.stat-pending { color: var(--warn); }
.decision-actions {
  display: flex;
  gap: 6px;
}

.chapter-card {
  padding: 0;
  overflow: hidden;
}
.chapter-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
}
.chapter-head:hover {
  background: rgba(155, 143, 196, 0.06);
}
.stage-no {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--accent-soft);
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 12px;
}
.stage-no.lit {
  background: var(--accent);
}
.chapter-title {
  font-weight: 500;
}
.finding-count {
  font-size: 12px;
  color: var(--muted);
}
.expand-icon {
  color: var(--muted);
  font-size: 10px;
}
.spacer { flex: 1; }

.chapter-body {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}
.no-issues {
  color: var(--ok);
  font-size: 13px;
  padding: 8px 0;
}

.finding-row {
  padding: 10px 12px;
  border-radius: 8px;
  margin-bottom: 8px;
  border-left: 3px solid transparent;
}
.finding-row.sev-error {
  border-left-color: var(--bad);
  background: rgba(196, 127, 127, 0.06);
}
.finding-row.sev-warn {
  border-left-color: var(--warn);
  background: rgba(201, 162, 106, 0.06);
}
.finding-row.sev-info {
  border-left-color: var(--accent);
  background: rgba(155, 143, 196, 0.06);
}

.finding-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.sev-icon {
  font-size: 12px;
}
.finding-type {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink);
}
.finding-id {
  font-size: 11px;
  color: var(--muted);
  margin-left: auto;
}
.finding-detail {
  font-size: 13px;
  line-height: 1.6;
  margin-bottom: 4px;
}
.finding-suggestion {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}
.finding-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.feedback-input {
  flex: 1;
  min-width: 150px;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  background: var(--bg);
  color: var(--ink);
}

.bottom-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.hint {
  font-size: 12px;
  color: var(--muted);
}

/* 批量精修进度条 */
.progress-card {
  padding: 12px 16px;
}
.progress-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
}
.progress-msg {
  color: var(--ink);
}
.progress-pct {
  color: var(--accent);
  font-weight: 600;
}
.progress-bar {
  height: 6px;
  background: var(--border);
  border-radius: 3px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.toast {
  position: fixed;
  bottom: 20px;
  right: 20px;
  background: var(--ink);
  color: #fff;
  padding: 10px 16px;
  border-radius: 10px;
  font-size: 13px;
  z-index: 100;
  animation: fadeIn 0.2s;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
</style>
